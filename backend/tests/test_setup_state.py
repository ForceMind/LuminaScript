"""Real isolated SQLite sessions exercise the setup CAS and stale-AI boundary."""
import asyncio
from copy import deepcopy
import json
import sqlite3

from alembic import command
from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import select

import database
import main
import migrate
import models
import schemas
from api.operations_routes import VersionRestore, restore_version
from api import operations_routes as operations
from repositories.projects import claim_generation, increment_tokens
from services.setup_state import (
    context_revision, valid_setup_cache, write_setup, write_setup_cache,
)
from services.versions import create_project_version, restore_project_version
from test_setup_modes import complete_movie_draft, seed_project


@pytest.fixture(autouse=True)
def forbid_real_ai(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("This test must not call a real AI provider")

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(main.llm, "raw_generation", forbidden)
    monkeypatch.setattr(main, "log_ai_action", no_audit)


@pytest.mark.asyncio
async def test_two_sessions_past_precheck_only_one_quick_confirm_wins(monkeypatch):
    async with database.SessionLocal() as session:
        await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})

    checked = 0
    both_checked = asyncio.Event()
    original_check = main.assert_setup_writable

    async def check_then_barrier(*args):
        nonlocal checked
        await original_check(*args)
        checked += 1
        if checked == 2:
            both_checked.set()
        await asyncio.wait_for(both_checked.wait(), timeout=5)

    monkeypatch.setattr(main, "assert_setup_writable", check_then_barrier)

    async def confirm(tone):
        async with database.SessionLocal() as session:
            user = await session.get(models.User, 1)
            values = complete_movie_draft()
            values["tone"] = tone
            try:
                result = await main.submit_quick_setup_review(
                    1, main.QuickSetupReviewRequest(values=values, context_revision="setup-v2:0:0"),
                    db=session, current_user=user,
                )
                return result["status"], tone
            except HTTPException as exc:
                return exc.status_code, tone

    results = await asyncio.gather(confirm("甲会话确认"), confirm("乙会话确认"))
    assert sorted(str(item[0]) for item in results) == ["409", "confirmed"]
    winning_tone = next(tone for status, tone in results if status == "confirmed")
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.global_context["tone"] == winning_tone
        assert project.global_context["final_confirm"] == "confirmed"
        assert context_revision(project) == "setup-v2:1:1"


@pytest.mark.asyncio
async def test_stale_session_cannot_write_before_cas_or_autoflush():
    async with database.SessionLocal() as session:
        await seed_project(session)
    async with database.SessionLocal() as first, database.SessionLocal() as second:
        stale = await first.get(models.Project, 1)
        latest = await second.get(models.Project, 1)
        await write_setup(second, latest, context_revision(latest), {"global_context": {"tone": "新设定"}})
        await second.commit()
        with pytest.raises(HTTPException) as error:
            await write_setup(first, stale, "setup-v2:0:0", {"global_context": {"tone": "过期覆盖"}})
        assert error.value.status_code == 409
    async with database.SessionLocal() as session:
        assert (await session.get(models.Project, 1)).global_context == {"tone": "新设定"}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "1234567890abcdef12345678", "setup-v1:0:0", "setup-v2:00:0", "setup-v2:99999999999999999999999999:0"])
async def test_legacy_or_missing_tokens_are_conflicts(token):
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        with pytest.raises(HTTPException) as error:
            await main.submit_interaction(
                1, main.InteractionRequest(answer="guided", context_key="setup_mode", context_revision=token),
                db=session, current_user=user,
            )
        assert error.value.status_code == 409
        await session.refresh(project)
        assert context_revision(project) == "setup-v2:0:0"
        assert project.global_context == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["generating", "queued", "running"])
@pytest.mark.parametrize("operation", ["mode", "reset", "rewind", "answer", "quick", "guided", "patch", "restore"])
async def test_all_setup_write_routes_reject_generation(busy, operation):
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast", "tone": "保留"})
        session.add(models.Scene(project_id=1, scene_index=1, outline="原有大纲", content="原有正文"))
        await session.commit()
        version = await create_project_version(session, 1, user.id, "恢复目标")
        await session.commit()
        if busy == "generating":
            project.status = models.ProcessingStatus.GENERATING
        else:
            session.add(models.GenerationJob(project_id=1, kind="outline_generation", status=models.JobStatus(busy)))
        await session.commit()
        token = context_revision(project)
        before = deepcopy(project.global_context)
        with pytest.raises(HTTPException) as error:
            if operation in {"mode", "reset", "rewind", "answer"}:
                key, answer = {
                    "mode": ("setup_mode", "guided"), "reset": ("final_confirm", "reset"),
                    "rewind": ("final_confirm", "edit:story_expansion"), "answer": ("tone", "新答案"),
                }[operation]
                await main.submit_interaction(1, main.InteractionRequest(
                    context_key=key, answer=answer, context_revision=token,
                ), db=session, current_user=user)
            elif operation in {"quick", "guided"}:
                await main.submit_quick_setup_review(1, main.QuickSetupReviewRequest(
                    action="guided" if operation == "guided" else "confirm",
                    values=complete_movie_draft(), context_revision=token,
                ), db=session, current_user=user)
            elif operation == "patch":
                await main.update_project(1, schemas.ProjectUpdate(project_type="tv", context_revision=token), db=session, current_user=user)
            else:
                await restore_version(1, version.id, VersionRestore(confirm=True, context_revision=token), db=session, current_user=user)
        assert error.value.status_code == 409
        await session.refresh(project)
        assert project.global_context == before
        assert context_revision(project) == token
        assert (await session.scalar(select(models.Scene.content))) == "原有正文"
        assert len((await session.scalars(select(models.ProjectVersion))).all()) == 1


@pytest.mark.asyncio
async def test_analysis_return_after_mode_switch_does_not_pollute_cache(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_draft(**kwargs):
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return complete_movie_draft(), 7

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", delayed_draft)
    async with database.SessionLocal() as session:
        await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})

    async def analyze():
        async with database.SessionLocal() as session:
            user = await session.get(models.User, 1)
            with pytest.raises(HTTPException) as error:
                await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
            assert error.value.status_code == 409

    task = asyncio.create_task(analyze())
    await asyncio.wait_for(started.wait(), timeout=5)
    async with database.SessionLocal() as session:
        user = await session.get(models.User, 1)
        await main.submit_interaction(1, main.InteractionRequest(
            answer="guided", context_key="setup_mode", context_revision="setup-v2:0:0",
        ), db=session, current_user=user)
    release.set()
    await task
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.global_context == {main.SETUP_MODE_KEY: "guided"}
        assert project.next_step_cache is None
        assert context_revision(project) == "setup-v2:1:1"
        assert project.total_tokens == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("ai_path", ["prefill", "guided", "revise", "synopsis"])
async def test_other_ai_paths_do_not_apply_after_concurrent_setup_write(monkeypatch, ai_path):
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        if ai_path == "prefill":
            return {"tone": "旧输入预填", "theme": "不应写入"}, 5
        if ai_path == "synopsis":
            return {"brief": "旧梗概", "detailed": "旧详细梗概"}, 5
        return {"question": "旧问题", "options": [
            {"label": str(i), "value": f"旧基调方案{i}"} for i in range(3)
        ]}, 5

    target = {
        "prefill": "extract_setup_from_long_input", "synopsis": "generate_story_synopsis",
    }.get(ai_path, "generate_interaction_options")
    monkeypatch.setattr(main.llm, target, delayed)
    async with database.SessionLocal() as session:
        _, project = await seed_project(session, project_type="movie", context={
            main.SETUP_MODE_KEY: "ai_fast" if ai_path == "revise" else "guided",
            "movie_duration": "90", "scene_count_target": "60",
        })
        if ai_path == "prefill":
            project.logline = "这是一个足够长的故事创意，需要自动预填其他故事设定。" * 8
            await session.commit()
        elif ai_path == "synopsis":
            project.global_context = {**complete_movie_draft(), main.SETUP_MODE_KEY: "guided"}
            project.title = complete_movie_draft()["title"]
            await session.commit()

    async def request_ai():
        async with database.SessionLocal() as session:
            user = await session.get(models.User, 1)
            with pytest.raises(HTTPException) as error:
                if ai_path == "revise":
                    await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                        operation="regenerate_field", target_field="tone", values=complete_movie_draft(),
                        context_revision="setup-v2:0:0",
                    ), db=session, current_user=user)
                else:
                    await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
            assert error.value.status_code == 409

    task = asyncio.create_task(request_ai())
    await asyncio.wait_for(started.wait(), timeout=5)
    async with database.SessionLocal() as session:
        user = await session.get(models.User, 1)
        await main.submit_interaction(1, main.InteractionRequest(
            answer="另一个会话的最终基调", context_key="tone", context_revision="setup-v2:0:0",
        ), db=session, current_user=user)
    release.set()
    await task
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.global_context["tone"] == "另一个会话的最终基调"
        if ai_path != "synopsis":
            assert "theme" not in project.global_context
        assert "synopsis_brief" not in project.global_context
        assert "synopsis_detailed" not in project.global_context
        assert main.AUTO_PREFILL_FLAG not in project.global_context
        assert project.next_step_cache is None
        assert context_revision(project) == "setup-v2:1:1"
        assert project.total_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["legacy", "schema", "mode", "setup_revision", "setup_cache_revision", "stage", "payload", "missing_field", "duplicate_field"])
async def test_mismatched_cache_is_rebuilt_not_relabelled(monkeypatch, damage):
    calls = 0

    async def draft(**kwargs):
        nonlocal calls
        calls += 1
        return complete_movie_draft(), 3

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", draft)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})
        old = {"type": "interaction_required", "payload": {
            "field": "quick_review", "question": "旧草案", "sections": main.build_quick_review_sections(project, complete_movie_draft()),
        }}
        await write_setup_cache(session, project, context_revision(project), old, mode="ai_fast", stage="quick_review")
        await session.commit()
        cache = deepcopy(project.next_step_cache)
        if damage == "legacy":
            cache.pop("_setup_cache")
        elif damage == "payload":
            cache["payload"]["sections"] = "malformed"
        elif damage == "missing_field":
            cache["payload"]["sections"].pop()
        elif damage == "duplicate_field":
            cache["payload"]["sections"].append(cache["payload"]["sections"][0])
        else:
            cache["_setup_cache"][damage] = "stale"
        project.next_step_cache = cache  # deliberate fixture corruption, not an application write
        await session.commit()
        response = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert calls == 1
        assert response["payload"]["question"] != "旧草案"
        assert response["context_revision"] == "setup-v2:0:2"
        assert response["payload"]["context_revision"] == response["context_revision"]
        assert "_setup_cache" not in response
        assert valid_setup_cache(project, mode="ai_fast", stage="quick_review")
        again = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert calls == 1
        assert again["context_revision"] == response["context_revision"]


@pytest.mark.asyncio
async def test_tokens_do_not_advance_revision_and_get_does_not_normalize():
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={"character_details": "经典叙事风格", "title": "《保留原文》"})
        project.title = "《保留原文》"
        await session.commit()
        token = context_revision(project)
        await increment_tokens(session, project, 9)
        await session.commit()
        listed = await main.list_projects(db=session, current_user=user)
        detail = await main.get_project(1, db=session, current_user=user)
        assert context_revision(project) == token
        assert project.total_tokens == 9
        assert not session.dirty
        assert listed[0].title == detail.title == "《保留原文》"
        assert detail.global_context["character_details"] == "经典叙事风格"
        assert schemas.ProjectResponse.model_validate(detail).context_revision == token


@pytest.mark.asyncio
async def test_restore_clears_cache_and_increments_not_rewinds_revision():
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={"tone": "旧设定"})
        version = await create_project_version(session, 1, user.id, "旧版本")
        await session.commit()
        await write_setup(session, project, context_revision(project), {"global_context": {"tone": "新设定"}})
        await session.commit()
        await increment_tokens(session, project, 11)
        await session.commit()
        await write_setup_cache(session, project, context_revision(project), {"payload": {}}, mode="guided", stage="tone")
        await session.commit()
        assert context_revision(project) == "setup-v2:1:2"
        await restore_project_version(session, project, version, context_revision(project))
        await session.commit()
        assert project.global_context == {"tone": "旧设定"}
        assert context_revision(project) == "setup-v2:2:3"
        assert project.next_step_cache is None
        assert project.total_tokens == 11


@pytest.mark.asyncio
async def test_generation_claim_blocks_stale_setup_cas():
    async with database.SessionLocal() as session:
        await seed_project(session)
    async with database.SessionLocal() as setup_session, database.SessionLocal() as generation_session:
        project = await setup_session.get(models.Project, 1)
        assert await claim_generation(generation_session, 1, 1)
        await generation_session.commit()
        with pytest.raises(HTTPException) as error:
            await write_setup(setup_session, project, "setup-v2:0:0", {"global_context": {"tone": "冲突"}})
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_ai_revision_started_before_generation_is_discarded_and_usage_charged(monkeypatch):
    async def start_generation_then_respond(*args, **kwargs):
        async with database.SessionLocal() as other:
            assert await claim_generation(other, 1, 1)
            await other.commit()
        return {"question": "过期问题", "options": [
            {"label": str(i), "value": f"候选{i}"} for i in range(3)
        ]}, 5

    monkeypatch.setattr(main.llm, "generate_interaction_options", start_generation_then_respond)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                operation="regenerate_field", target_field="tone", values=complete_movie_draft(),
                context_revision="setup-v2:0:0",
            ), db=session, current_user=user)
        assert error.value.status_code == 409
        await session.refresh(project)
        assert project.status == models.ProcessingStatus.GENERATING
        assert project.global_context == {main.SETUP_MODE_KEY: "ai_fast"}
        assert project.total_tokens == 5
        assert context_revision(project) == "setup-v2:0:0"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_input", ["setup", "generating", "scene"])
async def test_scene_prompt_cannot_overwrite_changes_from_another_session(monkeypatch, changed_input):
    async def change_inputs_then_respond(**kwargs):
        async with database.SessionLocal() as other:
            if changed_input == "setup":
                other_user = await other.get(models.User, 1)
                await main.submit_interaction(1, main.InteractionRequest(
                    answer="另一会话已确认的新设定", context_key="tone", context_revision="setup-v2:0:0",
                ), db=other, current_user=other_user)
            elif changed_input == "generating":
                assert await claim_generation(other, 1, 1)
                await other.commit()
            else:
                current_scene = await other.scalar(select(models.Scene).where(models.Scene.project_id == 1))
                current_scene.content = "期间重新完成的场次内容"
                await other.commit()
        return "不应覆盖设定的过期提示词", 7

    monkeypatch.setattr(main.llm, "rewrite_scene_to_ai_prompt", change_inputs_then_respond)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={"tone": "原设定"})
        session.add(models.Scene(
            project_id=1, scene_index=1, outline="原大纲", content="原场次内容",
            status=models.ProcessingStatus.COMPLETED,
        ))
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await main.rewrite_scene_to_prompt(1, 1, db=session, current_user=user)
        assert error.value.status_code == 409
        await session.refresh(project)
        assert project.global_context == {
            "tone": "另一会话已确认的新设定" if changed_input == "setup" else "原设定",
        }
        assert project.total_tokens == 7
        assert context_revision(project) == ("setup-v2:1:1" if changed_input == "setup" else "setup-v2:0:0")
        if changed_input == "scene":
            assert (await session.scalar(select(models.Scene.content))) == "期间重新完成的场次内容"


@pytest.mark.asyncio
async def test_concurrent_scene_prompt_merges_keep_both_caches_and_setup_revision(monkeypatch):
    entered = 0
    both_entered = asyncio.Event()

    async def parallel_prompt(**kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return f"场次{kwargs['scene_index']}的提示词", 3

    monkeypatch.setattr(main.llm, "rewrite_scene_to_ai_prompt", parallel_prompt)
    async with database.SessionLocal() as session:
        await seed_project(session, context={"tone": "保留设定"})
        session.add_all([
            models.Scene(project_id=1, scene_index=index, outline=f"大纲{index}", content=f"内容{index}", status=models.ProcessingStatus.COMPLETED)
            for index in (1, 2)
        ])
        await session.commit()

    async def rewrite(index):
        async with database.SessionLocal() as session:
            user = await session.get(models.User, 1)
            return await main.rewrite_scene_to_prompt(1, index, db=session, current_user=user)

    results = await asyncio.gather(rewrite(1), rewrite(2))
    assert all(result["cached"] is False for result in results)
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.global_context == {
            "tone": "保留设定",
            "_scene_ai_prompts": {"1": "场次1的提示词", "2": "场次2的提示词"},
        }
        assert project.total_tokens == 6
        assert context_revision(project) == "setup-v2:0:0"


async def seed_retry_state(session, *, kind="content_generation"):
    owner, project = await seed_project(session, context={
        "tone": "原有正式设定",
        "_last_generation_error": {"message": "旧错误"},
        "_scene_ai_prompts": {"1": "旧提示词", "2": "另一场提示词"},
    })
    admin = models.User(id=2, username="retry-admin", hashed_password="unused", is_admin=1)
    project.status = models.ProcessingStatus.FAILED
    old_job = models.GenerationJob(
        project_id=project.id, kind=kind, status=models.JobStatus.FAILED,
        payload={"scene_index": 1, "custom_existing_option": "preserve"}, max_attempts=2,
    )
    scene = models.Scene(project_id=project.id, scene_index=1, outline="保留大纲", content="失败前正文", status=models.ProcessingStatus.FAILED)
    session.add_all([admin, old_job, scene])
    await session.commit()
    return owner, admin, project, old_job


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["regenerate", "retry", "admin_retry"])
async def test_generation_entries_refresh_after_claim_and_preserve_concurrent_setup(monkeypatch, operation):
    async def update_setup_before_claim(db, project_id, actor_id):
        async with database.SessionLocal() as other:
            owner = await other.get(models.User, 1)
            await main.submit_interaction(1, main.InteractionRequest(
                answer="claim前另一会话确认的设定", context_key="tone", context_revision="setup-v2:0:0",
            ), db=other, current_user=owner)
        return await claim_generation(db, project_id, actor_id)

    monkeypatch.setattr(main if operation == "regenerate" else operations, "claim_generation", update_setup_before_claim)
    async with database.SessionLocal() as session:
        owner, admin, project, old_job = await seed_retry_state(session)
        if operation == "regenerate":
            result = await main.regenerate_scene(1, 1, db=session, current_user=owner)
        elif operation == "retry":
            result = await operations.retry_job(old_job.id, db=session, current_user=owner)
        else:
            result = await operations.admin_retry_job(old_job.id, db=session, _admin=admin)
        await session.refresh(project)
        assert project.global_context["tone"] == "claim前另一会话确认的设定"
        assert project.status == models.ProcessingStatus.GENERATING
        assert context_revision(project) == "setup-v2:1:1"
        queued = await session.get(models.GenerationJob, result["job_id"])
        assert queued.status == models.JobStatus.QUEUED
        if operation == "regenerate":
            assert project.global_context["_scene_ai_prompts"] == {"2": "另一场提示词"}
            version = await session.scalar(select(models.ProjectVersion))
            assert version.snapshot["global_context"]["tone"] == "claim前另一会话确认的设定"
        else:
            assert "_last_generation_error" not in project.global_context
            assert queued.payload == old_job.payload
            assert queued.max_attempts == old_job.max_attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("callers", ["user", "admin", "mixed"])
@pytest.mark.parametrize("kind", ["outline_generation", "content_generation"])
async def test_concurrent_retry_only_one_claims_before_mutating_scenes(monkeypatch, callers, kind):
    async with database.SessionLocal() as session:
        _, _, _, old_job = await seed_retry_state(session, kind=kind)
        old_job_id = old_job.id

    entered = 0
    both_entered = asyncio.Event()

    async def claim_after_both_prechecks(db, project_id, actor_id):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return await claim_generation(db, project_id, actor_id)

    monkeypatch.setattr(operations, "claim_generation", claim_after_both_prechecks)

    async def retry(as_admin):
        async with database.SessionLocal() as session:
            actor = await session.get(models.User, 2 if as_admin else 1)
            try:
                if as_admin:
                    result = await operations.admin_retry_job(old_job_id, db=session, _admin=actor)
                else:
                    result = await operations.retry_job(old_job_id, db=session, current_user=actor)
                return result["status"]
            except HTTPException as exc:
                return str(exc.status_code)

    results = await asyncio.gather(retry(callers == "admin"), retry(callers != "user"))
    assert sorted(results) == ["409", "queued"]
    async with database.SessionLocal() as session:
        jobs = (await session.scalars(select(models.GenerationJob).order_by(models.GenerationJob.id))).all()
        assert len(jobs) == 2
        assert jobs[0].status == models.JobStatus.FAILED
        assert jobs[1].status == models.JobStatus.QUEUED
        assert jobs[1].payload == jobs[0].payload
        assert jobs[1].max_attempts == jobs[0].max_attempts
        assert (await session.get(models.Project, 1)).global_context["tone"] == "原有正式设定"
        scenes = (await session.scalars(select(models.Scene))).all()
        if kind == "outline_generation":
            assert scenes == []
        else:
            assert len(scenes) == 1
            assert scenes[0].status == models.ProcessingStatus.PENDING
            assert scenes[0].content is None


@pytest.mark.asyncio
@pytest.mark.parametrize("target_in_version", [False, True])
async def test_regeneration_reloads_scene_identity_after_normal_interleaved_id_restore(monkeypatch, target_in_version):
    # Natural SQLite ordering: A1=id1, B1=id2, A2=id3. Restoring A leaves
    # B1=id2, then assigns id3 to restored A1 and (if present) id4 to A2.
    async with database.SessionLocal() as seed:
        owner, project = await seed_project(seed, context={"tone": "版本内设定"})
        second_project = models.Project(id=2, owner_id=owner.id, title="项目B", logline="另一个项目", project_type="movie")
        seed.add(second_project)
        first_scene = models.Scene(project_id=1, scene_index=1, outline="版本A1大纲", content="版本A1正文", status=models.ProcessingStatus.COMPLETED)
        seed.add(first_scene)
        await seed.flush()
        other_scene = models.Scene(project_id=2, scene_index=1, outline="项目B大纲", content="项目B正文不能改变", status=models.ProcessingStatus.COMPLETED)
        seed.add(other_scene)
        await seed.flush()
        if not target_in_version:
            version = await create_project_version(seed, 1, owner.id, "仅A1的历史版本")
            await seed.commit()
        target_scene = models.Scene(project_id=1, scene_index=2, outline="版本A2大纲", content="版本A2正文", status=models.ProcessingStatus.COMPLETED)
        seed.add(target_scene)
        await seed.flush()
        assert (first_scene.id, other_scene.id, target_scene.id) == (1, 2, 3)
        if target_in_version:
            version = await create_project_version(seed, 1, owner.id, "A1和A2的历史版本")
            await seed.commit()
        version_id = version.id
        project.global_context = {"tone": "恢复前的当前设定"}
        first_scene.content = "恢复前当前A1正文"
        target_scene.content = "恢复前当前A2正文"
        await seed.commit()

    async def restore_after_scene_read_before_claim(db, project_id, actor_id):
        # The A session has already read old A2=id3 when this hook runs.
        async with database.SessionLocal() as other:
            current_project = await other.get(models.Project, 1)
            target_version = await other.get(models.ProjectVersion, version_id)
            await restore_project_version(other, current_project, target_version, "setup-v2:0:0")
            await other.commit()
            recreated_first = await other.scalar(select(models.Scene).where(models.Scene.project_id == 1, models.Scene.scene_index == 1))
            assert recreated_first.id == 3  # exact normal id-reuse reproduction
        return await claim_generation(db, project_id, actor_id)

    monkeypatch.setattr(main, "claim_generation", restore_after_scene_read_before_claim)
    async with database.SessionLocal() as session:
        owner = await session.get(models.User, 1)
        project, _ = await main.require_project_access(session, 1, owner.id, load_scenes=True)
        assert {scene.scene_index: scene.content for scene in project.scenes} == {
            1: "恢复前当前A1正文", 2: "恢复前当前A2正文",
        }
        if target_in_version:
            result = await main.regenerate_scene(1, 2, db=session, current_user=owner)
            assert result["status"] == "Regeneration queued"
        else:
            with pytest.raises(HTTPException) as error:
                await main.regenerate_scene(1, 2, db=session, current_user=owner)
            assert error.value.status_code in {404, 409}

    async with database.SessionLocal() as check:
        scenes = (await check.scalars(select(models.Scene).where(models.Scene.project_id == 1).order_by(models.Scene.scene_index))).all()
        assert scenes[0].scene_index == 1
        assert scenes[0].content == "版本A1正文"
        assert (await check.get(models.Scene, 2)).content == "项目B正文不能改变"
        project = await check.get(models.Project, 1)
        assert project.global_context == {"tone": "版本内设定"}
        assert context_revision(project) == "setup-v2:1:1"
        jobs = (await check.scalars(select(models.GenerationJob))).all()
        versions = (await check.scalars(select(models.ProjectVersion).order_by(models.ProjectVersion.id))).all()
        if target_in_version:
            assert len(scenes) == 2 and scenes[1].scene_index == 2
            assert scenes[1].content is None
            assert len(jobs) == 1 and jobs[0].payload == {"scene_index": 2}
            assert len(versions) == 2
            snapshot_scenes = versions[1].snapshot["scenes"]
            assert [scene["scene_index"] for scene in snapshot_scenes] == [1, 2]
            assert [scene["content"] for scene in snapshot_scenes] == ["版本A1正文", "版本A2正文"]
            assert versions[1].snapshot["global_context"] == {"tone": "版本内设定"}
        else:
            assert len(scenes) == 1
            assert jobs == []
            assert len(versions) == 1  # no erroneous automatic snapshot
            assert project.status == models.ProcessingStatus.PENDING  # claim rolled back


@pytest.mark.asyncio
async def test_populate_existing_version_keeps_intentional_pending_changes():
    async with database.SessionLocal() as session:
        owner, project = await seed_project(session)
        project.genre = "待写入的已确认风格"
        version = await create_project_version(session, project.id, owner.id, "包含本事务变更")
        await session.commit()
        assert version.snapshot["genre"] == "待写入的已确认风格"
        assert project.genre == "待写入的已确认风格"


def test_operations_unversioned_database_migrates_only_new_columns(tmp_path, monkeypatch):
    path = tmp_path / "unversioned-operations.db"
    monkeypatch.setattr(migrate.settings, "database_url", f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(migrate.alembic_config(), migrate.OPERATIONS_REVISION)
    original_context = json.dumps({"title": "《历史原文》", "character_details": "经典叙事风格", "_old": True}, ensure_ascii=False)
    original_cache = json.dumps({"type": "legacy", "payload": {"field": "tone"}})
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users(id, username, hashed_password, is_admin) VALUES (1, 'legacy', 'unused', 0)")
        connection.execute("INSERT INTO projects(id, owner_id, title, logline, global_context, next_step_cache) VALUES (1, 1, ?, '历史', ?, ?)", ("《历史原文》", original_context, original_cache))
        connection.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.OPERATIONS_REVISION
    # Schema compatibility preparation is tested separately; do not invoke real admin policy.
    monkeypatch.setattr("upgrade_admin.upgrade_schema", lambda **kwargs: None)
    migrate.run_migrations()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT setup_revision, setup_cache_revision FROM projects").fetchone() == (0, 0)
        assert connection.execute("SELECT title, global_context, next_step_cache FROM projects").fetchone() == ("《历史原文》", original_context, original_cache)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == migrate.HEAD_REVISION
        connection.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.HEAD_REVISION
