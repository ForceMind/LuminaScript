"""Actor/billing separation, per-call quota, legacy packages and migration safety."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import sqlite3
import zipfile

from alembic import command
from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from tenacity import wait_none

import database
import main
import migrate
import models
import worker
from api import admin_routes, operations_routes
from services import audit, backups, llm, usage
from services.admin_imports import import_admin_export, parse_admin_export
from services.llm_config import LLMRuntimeConfig
from services.setup_drafts import inspect_draft
from services.setup_state import write_setup_cache
from test_setup_modes import complete_movie_draft


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real AI connections are forbidden in billing tests")
    monkeypatch.setattr(llm, "_get_client", forbidden)
    monkeypatch.setattr(admin_routes, "iter_export_database_paths", lambda: [])
    monkeypatch.setattr(backups, "active_sqlite_path", lambda: None)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def seed(db, *, context=None, project_type="pending"):
    owner = models.User(id=1, username="bill-owner", hashed_password="unused", daily_token_limit=100)
    editor = models.User(id=2, username="bill-editor", hashed_password="unused", daily_token_limit=1)
    admin = models.User(id=3, username="bill-admin", hashed_password="unused", is_admin=1)
    db.add_all([owner, editor, admin])
    await db.flush()
    project = models.Project(id=1, owner_id=1, title="草稿", logline="寻找归途的故事", project_type=project_type,
                             global_context=context if context is not None else {"_setup_mode": "ai_fast"})
    db.add(project)
    await db.flush()
    db.add(models.ProjectMember(project_id=1, user_id=2, role="editor", created_at=now()))
    await db.commit()
    return owner, editor, admin, project


def working_draft(revision=0):
    return {"schema": 1, "values": complete_movie_draft(), "baseline_values": complete_movie_draft(),
            "edited_fields": [], "ai_adjusted_fields": [], "base_setup_revision": revision, "saved_at": now()}


async def records(db):
    return list((await db.scalars(select(models.AIInteractionLog).order_by(models.AIInteractionLog.id))).all())


@pytest.mark.asyncio
async def test_actor_billing_legacy_usage_and_relationships():
    async with database.SessionLocal() as db:
        owner, editor, _, _ = await seed(db)
        db.add(models.AIInteractionLog(user_id=2, tokens=3, timestamp=now()))
        await db.commit()
        await audit.log_ai_action(2, 1, "new", "p", "r", 8, billed_user_id=1)
        assert (await usage.get_user_usage(db, 1))["daily_tokens"] == 8
        assert (await usage.get_user_usage(db, 2))["daily_tokens"] == 3
        rows = (await db.scalars(select(models.AIInteractionLog).options(
            selectinload(models.AIInteractionLog.user), selectinload(models.AIInteractionLog.billed_user)))).all()
        assert rows[0].billed_user_id is None
        assert rows[1].user.username == editor.username and rows[1].billed_user.username == owner.username
        loaded = await db.scalar(select(models.User).where(models.User.id == 2).options(selectinload(models.User.ai_logs)))
        assert len(loaded.ai_logs) == 2
        assert rows[1].timestamp[:10] == now()[:10]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["partial", "bad_json"])
async def test_editor_options_bill_owner_each_attempt_and_keep_raw(monkeypatch, first):
    raw_first = "not-json" if first == "bad_json" else json.dumps({"question": "主题", "options": [{"label": "甲", "value": "勇气"}]})
    raw_second = json.dumps({"question": "主题", "options": [{"label": str(i), "value": value} for i, value in enumerate(["信任", "责任", "成长"])]})
    replies = iter([(raw_first, 5), (raw_second, 7)])
    async def fake(*args, **kwargs):
        return next(replies)
    monkeypatch.setattr(llm, "raw_generation", fake)
    async with database.SessionLocal() as db:
        _, editor, _, project = await seed(db)
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field="theme", values=complete_movie_draft(),
            context_revision=project.context_revision), db=db, current_user=editor)
        rows = await records(db)
        assert result["tokens_used"] == project.total_tokens == 12
        assert [row.tokens for row in rows] == [5, 7]
        assert [row.response for row in rows] == [raw_first, raw_second]
        assert all(row.user_id == 2 and row.billed_user_id == 1 for row in rows)
        assert (await usage.get_user_usage(db, 2))["daily_tokens"] == 0
        assert project.setup_revision == 0


@pytest.mark.asyncio
async def test_refill_checks_newly_used_owner_quota(monkeypatch):
    calls = []
    async def partial(*args, **kwargs):
        calls.append(1)
        return {"question": "主题", "options": [{"label": "甲", "value": "勇气"}]}, 5
    monkeypatch.setattr(llm, "generate_interaction_options", partial)
    async with database.SessionLocal() as db:
        owner, editor, _, project = await seed(db)
        owner.daily_token_limit = 5
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                operation="regenerate_field", target_field="theme", values=complete_movie_draft(),
                context_revision=project.context_revision), db=db, current_user=editor)
        assert error.value.status_code == 429 and len(calls) == 1
        assert len(await records(db)) == 1 and project.total_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["static", "guided_static", "cache", "saved", "stale", "complete", "confirm"])
async def test_exhausted_quota_does_not_block_non_ai_analysis(monkeypatch, mode):
    async with database.SessionLocal() as db:
        owner, editor, _, project = await seed(db, context={})
        owner.daily_token_limit = 1
        db.add(models.AIInteractionLog(user_id=2, billed_user_id=1, tokens=1, timestamp=now()))
        if mode in {"saved", "stale"}:
            project.global_context = {"_setup_mode": "ai_fast"}
            project.quick_setup_draft = working_draft(4 if mode == "stale" else 0)
        elif mode in {"complete", "confirm"}:
            project.project_type = "movie"
            project.global_context = {**complete_movie_draft(), "_setup_mode": "guided"}
            if mode == "complete":
                project.global_context = {**project.global_context, "final_confirm": "confirmed"}
        elif mode == "guided_static":
            project.global_context = {"_setup_mode": "guided"}
        elif mode == "cache":
            project.project_type = "movie"
            project.global_context = {"_setup_mode": "guided", "movie_duration": "120", "scene_count_target": "80"}
            await db.commit()
            steps = main.get_relevant_setup_steps("movie")
            normalized = main.build_normalized_context(project)
            stage = next(step["key"] for step in steps if step["key"] not in normalized)
            await write_setup_cache(db, project, project.context_revision,
                {"type": "interaction_required", "payload": {"field": stage, "question": "cached?", "options": [{"label": str(i), "value": str(i)} for i in range(3)]}},
                mode="guided", stage=stage)
        await db.commit()
        result = await main.analyze_logline(1, BackgroundTasks(), db=db, current_user=editor)
        assert result["type"] == ("completed" if mode == "complete" else "interaction_required")
        assert len(await records(db)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_personal_review_bills_actor_and_keeps_known_failure_usage(monkeypatch, invalid):
    original = "broken-json" if invalid else json.dumps({"flagged": True, "categories": ["价值观风险"], "reason": "说明", "suggested_rewrite": "积极表达"})
    async def raw(*args, **kwargs):
        return original, 7
    monkeypatch.setattr(llm, "raw_generation", raw)
    async with database.SessionLocal() as db:
        _, editor, _, _ = await seed(db)
        if invalid:
            with pytest.raises(HTTPException) as error:
                await main.review_content(main.ContentReviewRequest(text="反思仇恨的故事"), db=db, current_user=editor)
            assert error.value.status_code == 503
        else:
            result = await main.review_content(main.ContentReviewRequest(text="反思仇恨的故事"), db=db, current_user=editor)
            assert result["suggested_rewrite"] == "积极表达" and result["tokens_used"] == 7
        row, = await records(db)
        assert (row.user_id, row.billed_user_id, row.project_id, row.tokens) == (2, 2, None, 7)
        assert row.response == original and row.status == ("failed" if invalid else "success")
        assert (await usage.get_user_usage(db, 1))["daily_tokens"] == 0
        with pytest.raises(HTTPException) as limited:
            await main.review_content(main.ContentReviewRequest(text="反思仇恨"), db=db, current_user=editor)
        assert limited.value.status_code == 429
        safe = await main.review_content(main.ContentReviewRequest(text="一个温暖故事"), db=db, current_user=editor)
        assert safe["tokens_used"] == 0 and len(await records(db)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["outline", "content", "prompt", "synopsis"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_project_ai_paths_bill_owner_and_fail_before_apply(monkeypatch, kind, invalid):
    async def fake(*args, **kwargs):
        if kind == "outline":
            return llm.AIResultList([] if invalid else [{"outline": "沿线寻找钥匙"}], "raw-outline"), 9
        if kind == "synopsis":
            if invalid:
                raise llm.InteractionGenerationError("invalid", raw_content="raw-synopsis", usage=9)
            return llm.AIResultDict({"brief": "梗概", "detailed": "详细梗概"}, "raw-synopsis"), 9
        return llm.AIText(" " if invalid else "沿线寻找钥匙", "raw-text"), 9
    monkeypatch.setattr(llm, {"outline": "generate_scene_batch", "content": "write_scene_content", "prompt": "rewrite_scene_to_ai_prompt", "synopsis": "generate_story_synopsis"}[kind], fake)
    async with database.SessionLocal() as db:
        _, editor, _, project = await seed(db, project_type="movie")
        project.status = models.ProcessingStatus.GENERATING if kind in {"outline", "content"} else models.ProcessingStatus.COMPLETED
        if kind in {"content", "prompt"}:
            db.add(models.Scene(project_id=1, scene_index=1, outline="线索", content="旧正文" if kind == "prompt" else None,
                                status=models.ProcessingStatus.COMPLETED if kind == "prompt" else models.ProcessingStatus.PENDING))
        await db.commit()
        if kind == "prompt":
            if invalid:
                with pytest.raises(HTTPException):
                    await main.rewrite_scene_to_prompt(1, 1, db=db, current_user=editor)
            else:
                await main.rewrite_scene_to_prompt(1, 1, db=db, current_user=editor)
        elif kind == "synopsis":
            if invalid:
                with pytest.raises(Exception):
                    await main.ensure_story_synopsis(project, {}, db, actor_id=2)
            else:
                await main.ensure_story_synopsis(project, {}, db, actor_id=2)
        elif kind == "outline":
            # The outline-to-content handoff is covered separately; no second AI here.
            async def no_content(*args, **kwargs):
                return None
            monkeypatch.setattr(main, "run_generation_loop", no_content)
            await main.run_incremental_outline_generation(1, "style", 1, 2)
        else:
            await main.run_generation_loop(1, user_id=2)
        await db.refresh(project)
        row, = await records(db)
        assert (row.user_id, row.billed_user_id, row.tokens) == (2, 1, 9)
        assert row.status == ("failed" if invalid else "success")
        assert row.response.startswith("raw-") and project.total_tokens == 9
        assert project.setup_revision == 0


@pytest.mark.asyncio
async def test_provider_failover_and_retry_recheck_quota_without_further_call(monkeypatch):
    runtime = LLMRuntimeConfig(api_key="test", profile_name="test", max_concurrency=1, base_url="https://example.test/v1", model_id="test", timeout_seconds=10)
    monkeypatch.setattr(llm, "_get_client", lambda config: object())
    monkeypatch.setattr(llm.raw_generation.retry, "wait", wait_none())
    async with database.SessionLocal() as db:
        await seed(db)
    for profiles in ([runtime], [runtime, runtime]):
        async with database.SessionLocal() as db:
            await db.execute(delete(models.AIInteractionLog))
            await db.commit()
        calls = []
        async def failed(*args, **kwargs):
            calls.append(1)
            await audit.log_ai_action(2, 1, "concurrent", "p", "r", 100, billed_user_id=1)
            raise RuntimeError("synthetic transport failure")
        monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task: profiles)
        monkeypatch.setattr(llm, "_create_completion", failed)
        with pytest.raises(HTTPException) as error:
            await usage.invoke_with_quota(1, lambda: llm.raw_generation([{"role": "user", "content": "test"}]))
        assert error.value.status_code == 429 and len(calls) == 1


@pytest.mark.asyncio
async def test_quota_scope_isolated_and_latest_admin_limits_checked(monkeypatch):
    runtime = LLMRuntimeConfig(api_key="test", max_concurrency=4, base_url="https://example.test/v1", model_id="test", timeout_seconds=10)
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: object())
    async def raw(*args, **kwargs):
        return "ok", 1
    monkeypatch.setattr(llm, "_create_completion", raw)
    async with database.SessionLocal() as db:
        owner, editor, _, _ = await seed(db)
        await audit.log_ai_action(1, None, "history", "", "", 50, billed_user_id=1)
        async with database.SessionLocal() as other:
            changed = await other.get(models.User, 1)
            changed.daily_token_limit = 50
            await other.commit()
        with pytest.raises(HTTPException):
            await usage.enforce_user_quota(db, owner.id)
    async def call(user_id):
        try:
            return await usage.invoke_with_quota(user_id, lambda: llm.raw_generation([]))
        except HTTPException as exc:
            return exc.status_code
    assert await asyncio.gather(call(1), call(2)) == [429, ("ok", 1)]
    # Scope reset means standalone admin diagnostic calls remain supported.
    assert await llm.raw_generation([]) == ("ok", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("kind", ["content_generation", "outline_generation"])
async def test_worker_uses_saved_actor_or_legacy_owner(monkeypatch, legacy, kind):
    async with database.SessionLocal() as db:
        await seed(db)
    seen = []
    async def called(*args, **kwargs):
        seen.append(kwargs["user_id"])
        async with database.SessionLocal() as db:
            project = await db.get(models.Project, 1)
            project.status = models.ProcessingStatus.COMPLETED
            await db.commit()
    monkeypatch.setattr(main, "run_generation_loop", called)
    monkeypatch.setattr(main, "run_incremental_outline_generation", called)
    await worker.execute_job(models.GenerationJob(project_id=1, kind=kind, payload={} if legacy else {"user_id": 2}))
    assert seen == [1 if legacy else 2]


@pytest.mark.asyncio
async def test_admin_export_import_preserves_billing_remapping_and_drafts():
    async with database.SessionLocal() as db:
        owner, editor, admin, project = await seed(db)
        project.setup_revision = 8
        project.setup_cache_revision = 12
        project.quick_setup_draft = working_draft(8)
        db.add(models.AIInteractionLog(user_id=2, billed_user_id=None, tokens=3, timestamp=now()))
        db.add(models.LoginLog(user_id=2, status="success", timestamp=now()))
        await db.commit()
        await audit.log_ai_action(2, 1, "new", "p", "r", 5, billed_user_id=1)
        response = await admin_routes.export_all_data(db=db, admin=admin)
        payload = parse_admin_export(b"".join([part async for part in response.body_iterator]))
        new_record = next(row for row in payload["ai_logs"] if row["billed_user_id"])
        assert new_record["billed_username"] == "bill-owner"
        assert payload["projects"][0]["quick_setup_draft"] == project.quick_setup_draft
        # Source ids intentionally differ from destination ids for both users.
        for row in payload["users"]:
            row["id"] += 40
        payload["projects"][0]["owner_id"] += 40
        for row in payload["ai_logs"]:
            row["user_id"] += 40
            if row["billed_user_id"] is not None:
                row["billed_user_id"] += 40
        await import_admin_export(db, payload, importing_admin=admin)
        new_project = await db.scalar(select(models.Project).order_by(models.Project.id.desc()).limit(1))
        assert new_project.owner_id == 1
        assert (new_project.setup_revision, new_project.setup_cache_revision) == (8, 12)
        assert new_project.quick_setup_draft == project.quick_setup_draft and inspect_draft(new_project)[1] is False
        rows = await records(db)
        assert [(r.user_id, r.billed_user_id) for r in rows[-2:]] == [(2, 1), (2, None)]
        detail = await admin_routes.get_ai_log_detail(rows[-2].id, db=db, _admin=admin)
        assert detail["user_name"] == "bill-editor" and detail["billed_username"] == "bill-owner"
        legacy_detail = await admin_routes.get_ai_log_detail(rows[-1].id, db=db, _admin=admin)
        assert legacy_detail["billed_user_id"] is None and legacy_detail["billed_username"] is None
        # Source stale draft stays stale, not silently rebased.
        payload["projects"][0]["quick_setup_draft"]["base_setup_revision"] = 7
        await import_admin_export(db, payload, importing_admin=admin)
        stale_project = await db.scalar(select(models.Project).order_by(models.Project.id.desc()).limit(1))
        assert inspect_draft(stale_project)[1] is True


@pytest.mark.asyncio
async def test_unmapped_billing_rejects_import_atomically_and_old_package_remains_legacy():
    async with database.SessionLocal() as db:
        _, _, admin, _ = await seed(db)
        payload = {"users": [], "projects": [], "login_logs": [], "ai_logs": [{"user_id": 3, "billed_user_id": 999, "tokens": 5}]}
        with pytest.raises(ValueError, match="计费用户无法映射"):
            await import_admin_export(db, payload, importing_admin=admin)
        assert await records(db) == []
        admin = await db.get(models.User, 3)
        del payload["ai_logs"][0]["billed_user_id"]
        await import_admin_export(db, payload, importing_admin=admin)
        row, = await records(db)
        assert row.user_id == 3 and row.billed_user_id is None


@pytest.mark.asyncio
async def test_encrypted_backup_contains_attribution_and_restores_draft_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(backups, "BACKUP_CONFIG_PATH", tmp_path / "config.json")
    async with database.SessionLocal() as db:
        owner, _, _, project = await seed(db)
        project.setup_revision, project.setup_cache_revision = 4, 6
        project.quick_setup_draft = working_draft(3)  # already stale
        await db.commit()
        await audit.log_ai_action(2, 1, "backup", "p", "r", 4, billed_user_id=1)
        record = await backups.create_backup(db, actor_id=1, actor_name=owner.username, backup_type="manual")
        archive = backups.backup_path(record).read_bytes()
        if record.filename.endswith(".enc"):
            archive = backups.decrypt_backup(archive)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            rows = json.loads(zipped.read("ai_logs.json"))
            assert rows[0]["user_id"] == 2 and rows[0]["billed_user_id"] == 1
            assert rows[0]["billed_username"] == "bill-owner"
        await backups.restore_projects_as_copies(db, record, fallback_owner_id=3)
        restored = await db.scalar(select(models.Project).order_by(models.Project.id.desc()).limit(1))
        assert restored.owner_id == 1
        assert (restored.setup_revision, restored.setup_cache_revision) == (4, 6)
        assert restored.quick_setup_draft == project.quick_setup_draft and inspect_draft(restored)[1] is True
        # Restore copies never imports usage again (avoids double billing).
        assert len(await records(db)) == 1


def test_0006_migration_keeps_legacy_logs_null_and_detects_unversioned_schema(tmp_path, monkeypatch):
    path = tmp_path / "billing-old.db"
    monkeypatch.setattr(migrate.settings, "database_url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(migrate.alembic_config(), migrate.DRAFT_REVISION)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(id,username,hashed_password) VALUES(1,'old','unused')")
        db.execute("INSERT INTO ai_logs(id,user_id,tokens,timestamp) VALUES(1,1,19,'2026-01-01T01:00:00')")
        db.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.DRAFT_REVISION
    monkeypatch.setattr("upgrade_admin.upgrade_schema", lambda **kwargs: None)
    migrate.run_migrations()
    migrate.run_migrations()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT user_id,billed_user_id,tokens,timestamp FROM ai_logs").fetchone() == (1, None, 19, "2026-01-01T01:00:00")
        db.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.HEAD_REVISION


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"unrelated": 1}, {"flagged": "true", "categories": [], "reason": "r", "suggested_rewrite": "ok"}, {"flagged": True, "categories": [1], "reason": "r", "suggested_rewrite": "ok"}])
async def test_review_rejects_semantic_shape_with_known_raw_usage(monkeypatch, payload):
    original = json.dumps(payload)
    async def raw(*args, **kwargs):
        return original, 9
    monkeypatch.setattr(llm, "raw_generation", raw)
    with pytest.raises(llm.InteractionGenerationError) as error:
        await llm.review_user_input("色情故事")
    assert error.value.usage == 9 and error.value.raw_content == original


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"unrelated": 1}, {"brief": "", "detailed": ""}, {"brief": "文本", "detailed": ["bad"]}])
async def test_synopsis_rejects_missing_or_empty_text_with_usage(monkeypatch, payload):
    original = json.dumps(payload)
    async def raw(*args, **kwargs):
        return original, 9
    monkeypatch.setattr(llm, "raw_generation", raw)
    with pytest.raises(llm.InteractionGenerationError) as error:
        await llm.generate_story_synopsis("一个故事")
    assert error.value.usage == 9 and error.value.raw_content == original


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["helper", "provider"])
async def test_optional_synopsis_late_quota_denial_remains_readable(monkeypatch, boundary):
    async with database.SessionLocal() as db:
        _, editor, _, project = await seed(db, project_type="movie", context={**complete_movie_draft(), "_setup_mode": "guided"})
        original_quota = main.enforce_user_quota
        calls = []
        async def quota(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise HTTPException(status_code=429, detail="exhausted during enrichment")
            return await original_quota(*args, **kwargs)
        async def provider_quota(*args, **kwargs):
            raise HTTPException(status_code=429, detail="exhausted before provider")
        if boundary == "helper":
            monkeypatch.setattr(main, "enforce_user_quota", quota)
        else:
            monkeypatch.setattr(llm, "generate_story_synopsis", provider_quota)
        result = await main.analyze_logline(1, BackgroundTasks(), db=db, current_user=editor)
        assert result["payload"]["field"] == "final_confirm"
        assert await records(db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("second_invalid", [False, True])
async def test_continuity_calls_record_partial_then_actual_outcome(monkeypatch, second_invalid):
    calls = []
    async def write(*args, **kwargs):
        calls.append(1)
        content = "故事开始，主角第一次见到搭档。" if len(calls) == 1 or second_invalid else "接续上场的追踪，主角把钥匙交给搭档。"
        return llm.AIText(content, f"raw-{len(calls)}"), 5
    monkeypatch.setattr(llm, "write_scene_content", write)
    async with database.SessionLocal() as db:
        _, _, _, project = await seed(db, project_type="movie")
        project.status = models.ProcessingStatus.GENERATING
        for i in range(1, 11):
            db.add(models.Scene(project_id=1, scene_index=i, outline=f"继续寻找线索{i}", content="旧内容" if i < 10 else None,
                                status=models.ProcessingStatus.COMPLETED if i < 10 else models.ProcessingStatus.PENDING))
        await db.commit()
    await main.run_generation_loop(1, user_id=2)
    async with database.SessionLocal() as db:
        rows = await records(db)
        assert len(calls) == 2 and [row.status for row in rows] == ["partial", "failed" if second_invalid else "success"]
        assert [row.tokens for row in rows] == [5, 5] and [row.response for row in rows] == ["raw-1", "raw-2"]
        project = await db.get(models.Project, 1)
        scene = await db.scalar(select(models.Scene).where(models.Scene.scene_index == 10))
        assert project.total_tokens == 10
        assert (scene.content is None) == second_invalid


@pytest.mark.asyncio
async def test_prompt_cas_late_conflict_updates_same_log_not_double_billing(monkeypatch):
    async def rewrite(*args, **kwargs):
        return llm.AIText("新提示词", "raw-prompt"), 6
    async def stale_cas(*args, **kwargs):
        raise HTTPException(status_code=409, detail="synthetic last-moment CAS conflict")
    monkeypatch.setattr(llm, "rewrite_scene_to_ai_prompt", rewrite)
    monkeypatch.setattr(main, "write_scene_prompt_cache", stale_cas)
    async with database.SessionLocal() as db:
        _, editor, _, project = await seed(db, project_type="movie")
        project.status = models.ProcessingStatus.COMPLETED
        db.add(models.Scene(project_id=1, scene_index=1, outline="大纲", content="原文", status=models.ProcessingStatus.COMPLETED))
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await main.rewrite_scene_to_prompt(1, 1, db=db, current_user=editor)
        assert error.value.status_code == 409
        row, = await records(db)
        assert (row.status, row.tokens, row.user_id, row.billed_user_id) == ("stale", 6, 2, 1)
        assert row.response == "raw-prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "content", "review"])
async def test_audit_failure_does_not_apply_or_report_success(monkeypatch, kind):
    async def fake(*args, **kwargs):
        return "AI新结果", 6
    async def review(*args, **kwargs):
        return llm.AIResultDict({"flagged": True, "categories": [], "reason": "r", "suggested_rewrite": "new"}, "raw", usage=6)
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit unavailable")
    monkeypatch.setattr(main, "log_ai_action", fail_audit)
    monkeypatch.setattr(llm, "rewrite_scene_to_ai_prompt", fake)
    monkeypatch.setattr(llm, "write_scene_content", fake)
    monkeypatch.setattr(llm, "review_user_input", review)
    async with database.SessionLocal() as db:
        _, editor, _, project = await seed(db, project_type="movie")
        project.status = models.ProcessingStatus.GENERATING if kind == "content" else models.ProcessingStatus.COMPLETED
        db.add(models.Scene(project_id=1, scene_index=1, outline="大纲", content="原文" if kind == "prompt" else None,
                            status=models.ProcessingStatus.COMPLETED if kind == "prompt" else models.ProcessingStatus.PENDING))
        await db.commit()
        if kind == "content":
            await main.run_generation_loop(1, user_id=2)
        else:
            with pytest.raises(HTTPException) as error:
                if kind == "prompt":
                    await main.rewrite_scene_to_prompt(1, 1, db=db, current_user=editor)
                else:
                    await main.review_content(main.ContentReviewRequest(text="反思仇恨"), db=db, current_user=editor)
            assert error.value.status_code == 503
        await db.refresh(project)
        assert project.total_tokens == (0 if kind == "review" else 6)
        assert "_scene_ai_prompts" not in project.global_context
        scene = await db.scalar(select(models.Scene).execution_options(populate_existing=True))
        assert scene.content == ("原文" if kind == "prompt" else None)


@pytest.mark.asyncio
async def test_deleted_project_retains_captured_billing_not_actor_fallback(monkeypatch):
    async def deleted(*args, **kwargs):
        async with database.SessionLocal() as other:
            await other.execute(delete(models.ProjectMember))
            await other.execute(delete(models.Project).where(models.Project.id == 1))
            await other.commit()
        return llm.AIResultDict({"brief": "新梗概", "detailed": "新详细梗概"}, "original"), 8
    monkeypatch.setattr(llm, "generate_story_synopsis", deleted)
    async with database.SessionLocal() as db:
        _, _, _, project = await seed(db)
        with pytest.raises(HTTPException) as error:
            await main.ensure_story_synopsis(project, {}, db, actor_id=2)
        assert error.value.status_code == 409
        row, = await records(db)
        assert (row.user_id, row.billed_user_id, row.project_id, row.tokens, row.status) == (2, 1, None, 8, "stale")
