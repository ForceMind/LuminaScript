"""Working draft persistence, independent revisions and baseline-aware scopes."""
import asyncio
from copy import deepcopy
from datetime import datetime
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
from services import llm
from test_setup_fields import draft
from test_setup_modes import seed_project


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("This test must not call a real AI provider")

    async def no_log(*args, **kwargs):
        return None

    monkeypatch.setattr(llm, "raw_generation", forbidden)
    monkeypatch.setattr(main, "log_ai_action", no_log)


async def seed(session, *, context=None, project_type="pending"):
    return await seed_project(session, project_type=project_type, context=context or {main.SETUP_MODE_KEY: "ai_fast"})


async def act(session, user, project, action="save", **kwargs):
    return await main.submit_quick_setup_review(1, main.QuickSetupReviewRequest(
        action=action, context_revision=project.context_revision, **kwargs,
    ), db=session, current_user=user)


@pytest.mark.asyncio
async def test_save_keeps_raw_work_and_does_not_confirm_then_refresh_restores_without_ai(monkeypatch):
    baseline = draft()
    values = {**baseline, "title": "  标题：未确认：归途—甲  ", "movie_duration": "91.5", "user_notes": ""}
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        original = (project.title, project.project_type, deepcopy(project.global_context))
        result = await act(session, user, project, values=values, baseline_values=baseline,
                           edited_fields=["title", "theme"], ai_adjusted_fields=["movie_duration"])
        saved = result["quick_setup_draft"]
        assert result["status"] == "saved"
        assert saved["values"] == values and saved["baseline_values"] == baseline
        assert saved["edited_fields"] == ["title", "user_notes"]
        assert saved["ai_adjusted_fields"] == ["movie_duration"]
        assert saved["base_setup_revision"] == 0
        assert datetime.fromisoformat(saved["saved_at"]).tzinfo is not None
        assert project.context_revision == "setup-v2:0:1"
        assert (project.title, project.project_type, project.global_context) == original
        assert project.next_step_cache is None

    async def no_quota_for_read(*args, **kwargs):
        raise AssertionError("Saved-draft read must not require another AI quota check")

    monkeypatch.setattr(main, "enforce_user_quota", no_quota_for_read)
    async with database.SessionLocal() as session:
        user = await session.get(models.User, 1)
        response = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        payload = response["payload"]
        assert payload["values"] == values and payload["baseline_values"] == baseline
        assert payload["draft_status"] == "saved" and payload["read_only"] is False
        assert payload["context_revision"] == response["context_revision"] == "setup-v2:0:1"
        detail = await main.get_project(1, db=session, current_user=user)
        serialized = schemas.ProjectResponse.model_validate(detail)
        assert serialized.has_quick_setup_draft is True
        assert serialized.quick_setup_draft_stale is False
        assert serialized.quick_setup_draft["values"] == values


@pytest.mark.asyncio
async def test_cache_baseline_is_authoritative_and_survives_repeated_saves(monkeypatch):
    calls = []

    async def generated(**kwargs):
        calls.append(True)
        return draft(), 0

    monkeypatch.setattr(llm, "generate_quick_setup_draft", generated)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        initial = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        baseline = initial["payload"]["baseline_values"]
        assert baseline == initial["payload"]["values"] == draft()
        assert initial["payload"]["draft_status"] == "generated"
        values = {**baseline, "theme": "改后的主题"}
        await act(session, user, project, values=values, baseline_values=values, edited_fields=["theme"])
        assert project.quick_setup_draft["baseline_values"] == baseline
        assert project.quick_setup_draft["edited_fields"] == ["theme"]
        values["title"] = "新的工作题目"
        values["theme"] = baseline["theme"]  # actual revert removes dirty history
        await act(session, user, project, values=values, baseline_values=values, edited_fields=["theme", "title"])
        assert project.quick_setup_draft["baseline_values"] == baseline
        assert project.quick_setup_draft["edited_fields"] == ["title"]
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_save_guided_and_mode_only_round_trip_relocate_valid_draft():
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        values = {**draft(), "theme": "已保存的新主题"}
        result = await act(session, user, project, "save_guided", values=values, baseline_values=draft(), edited_fields=["theme"])
        assert result["status"] == "saved_guided"
        assert project.global_context == {main.SETUP_MODE_KEY: "guided"}
        assert project.project_type == "pending" and "final_confirm" not in project.global_context
        assert project.context_revision == "setup-v2:1:1"
        assert project.quick_setup_draft["base_setup_revision"] == 1
        guided = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert guided["payload"]["field"] == "project_type"
        assert guided["saved_draft_available"] is True
        for mode in ("ai_fast", "guided", "ai_fast"):
            await main.submit_interaction(1, main.InteractionRequest(
                answer=mode, context_key="setup_mode", context_revision=project.context_revision,
            ), db=session, current_user=user)
            assert project.quick_setup_draft["base_setup_revision"] == project.setup_revision
            assert project.quick_setup_draft_stale is False
        restored = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert restored["payload"]["values"] == values
        assert restored["payload"]["baseline_values"] == draft()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "save_guided", "confirm", "ai_revise"])
async def test_real_setup_change_retains_stale_readonly_draft_and_blocks_application(action):
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft())
        before_draft = deepcopy(project.quick_setup_draft)
        await main.submit_interaction(1, main.InteractionRequest(
            answer="另一会话的正式新基调", context_key="tone", context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert project.quick_setup_draft == before_draft
        assert project.quick_setup_draft_stale is True
        current_revision = project.context_revision
        response = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert response["payload"]["draft_status"] == "stale"
        assert response["payload"]["read_only"] is True
        assert all(not item["editable"] for item in response["payload"]["sections"])
        assert response["payload"]["values"] == before_draft["values"]
        assert response["context_revision"] == current_revision
        with pytest.raises(HTTPException) as error:
            if action == "ai_revise":
                await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                    operation="regenerate_field", target_field="theme", values=draft(), context_revision=current_revision,
                ), db=session, current_user=user)
            else:
                await act(session, user, project, action, values=draft(), baseline_values=draft())
        assert error.value.status_code == 409
        assert project.context_revision == current_revision
        assert project.quick_setup_draft == before_draft
        await act(session, user, project, "discard")
        assert project.quick_setup_draft is None
        assert project.global_context["tone"] == "另一会话的正式新基调"


@pytest.mark.asyncio
async def test_explicit_guided_discards_and_confirm_consumes_saved_work():
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft())
        await act(session, user, project, "guided")
        assert project.quick_setup_draft is None
        assert project.global_context == {main.SETUP_MODE_KEY: "guided"}
        values = {**draft(), "title": "标题：确认后的故事"}
        await act(session, user, project, values=values, baseline_values=draft(), edited_fields=["title"])
        await act(session, user, project, "confirm", values=values, edited_fields=["title"])
        assert project.quick_setup_draft is None
        assert project.title == "确认后的故事"
        assert project.project_type == "movie"
        assert project.global_context["final_confirm"] == "confirmed"


@pytest.mark.asyncio
async def test_regenerate_never_restores_discarded_work_and_keeps_formal_constraints(monkeypatch):
    calls = []

    async def generated(**kwargs):
        calls.append(kwargs)
        assert kwargs["current_context"]["tone"] == "正式确认的基调"
        assert "尚未确认的工作稿基调" not in json.dumps(kwargs, ensure_ascii=False)
        return draft(), 0

    monkeypatch.setattr(llm, "generate_quick_setup_draft", generated)
    async with database.SessionLocal() as session:
        context = {**draft(), "tone": "正式确认的基调", "final_confirm": "confirmed", main.SETUP_MODE_KEY: "ai_fast"}
        user, project = await seed(session, context=context, project_type="movie")
        project.title = draft()["title"]
        await session.commit()
        await act(session, user, project, values={**draft(), "tone": "尚未确认的工作稿基调"}, baseline_values=draft())
        await act(session, user, project, "regenerate")
        assert project.quick_setup_draft is None and project.next_step_cache is None
        assert project.global_context["final_confirm"] == "confirmed"
        response = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert len(calls) == 1
        assert response["payload"]["values"]["tone"] == "正式确认的基调"
        assert response["payload"]["draft_status"] == "generated"


@pytest.mark.asyncio
async def test_two_sessions_save_with_same_revision_only_one_wins(monkeypatch):
    async with database.SessionLocal() as session:
        await seed(session)
    checked = 0
    both_checked = asyncio.Event()
    original_check = main.assert_setup_writable

    async def barrier(*args):
        nonlocal checked
        await original_check(*args)
        checked += 1
        if checked == 2:
            both_checked.set()
        await asyncio.wait_for(both_checked.wait(), timeout=5)

    monkeypatch.setattr(main, "assert_setup_writable", barrier)

    async def save(title):
        async with database.SessionLocal() as session:
            user = await session.get(models.User, 1)
            try:
                result = await main.submit_quick_setup_review(1, main.QuickSetupReviewRequest(
                    action="save", values={**draft(), "title": title}, baseline_values=draft(), context_revision="setup-v2:0:0",
                ), db=session, current_user=user)
                return result["status"], title
            except HTTPException as exc:
                return exc.status_code, title

    results = await asyncio.gather(save("甲工作稿"), save("乙工作稿"))
    assert sorted(str(status) for status, _ in results) == ["409", "saved"]
    winner = next(title for status, title in results if status == "saved")
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.context_revision == "setup-v2:0:1"
        assert project.quick_setup_draft["values"]["title"] == winner
        assert project.title == "创意草稿" and project.project_type == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["generating", "queued", "running"])
@pytest.mark.parametrize("action", ["save", "save_guided", "discard", "regenerate", "guided", "confirm"])
async def test_every_working_draft_action_is_blocked_during_generation(busy, action):
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft())
        before = deepcopy(project.quick_setup_draft)
        token = project.context_revision
        if busy == "generating":
            project.status = models.ProcessingStatus.GENERATING
        else:
            session.add(models.GenerationJob(project_id=1, kind="content_generation", status=models.JobStatus(busy)))
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await act(session, user, project, action, values=draft(), baseline_values=draft())
        assert error.value.status_code == 409
        await session.refresh(project)
        assert project.quick_setup_draft == before and project.context_revision == token


@pytest.mark.asyncio
@pytest.mark.parametrize("source,target", [("manual", "theme"), ("ai", "theme"), ("ai", "movie_duration")])
async def test_related_uses_saved_baseline_and_locks_user_ai_and_scale_directions(monkeypatch, source, target):
    baseline = draft()
    values = {**baseline, target: "91.5" if target == "movie_duration" else "全新的明确方向"}
    edited = [target] if source == "manual" else []
    ai_fields = [target] if source == "ai" else []
    calls = []

    async def revise(**kwargs):
        calls.append(kwargs)
        assert kwargs["baseline_values"] == baseline
        assert kwargs["changed_fields"][target] == {"before": baseline[target], "after": values[target], "source": source}
        assert target not in kwargs["allowed_fields"]
        assert not set(kwargs["allowed_fields"]).intersection(main.QUICK_CONTROL_FIELDS)
        assert set(kwargs["locked_fields"]).issuperset(main.QUICK_CONTROL_FIELDS | {target})
        assert kwargs["values"][target] == values[target]
        return {"plot_details": "记者公开母亲留下的实验档案，使医生必须面对此前隐瞒的责任。"}, "仅调整关联情节", 3

    monkeypatch.setattr(llm, "revise_quick_setup_fields", revise)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=values, baseline_values=baseline, edited_fields=edited, ai_adjusted_fields=ai_fields)
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", scope="related", values=values,
            baseline_values=values, edited_fields=edited, ai_adjusted_fields=ai_fields,
            context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert result["changed_fields"] == ["plot_details"]
        assert len(calls) == 1
        assert project.quick_setup_draft["values"] == values
        assert project.total_tokens == 3


@pytest.mark.asyncio
async def test_related_cannot_return_locked_changed_value_even_if_reverting_to_baseline(monkeypatch):
    async def invalid_revert(**kwargs):
        return {"theme": draft()["theme"]}, "试图反向改回", 5

    monkeypatch.setattr(llm, "revise_quick_setup_fields", invalid_revert)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        values = {**draft(), "theme": "用户新方向"}
        await act(session, user, project, values=values, baseline_values=draft(), edited_fields=["theme"])
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                operation="review_edits", scope="related", values=values, edited_fields=["theme"], context_revision=project.context_revision,
            ), db=session, current_user=user)
        assert error.value.status_code == 503
        assert project.quick_setup_draft["values"]["theme"] == "用户新方向"
        assert project.total_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["edited_only", "related"])
async def test_reverted_values_do_not_trigger_ai_from_event_history(monkeypatch, scope):
    async def no_model(**kwargs):
        raise AssertionError("No actual change must not call the model")

    monkeypatch.setattr(llm, "revise_quick_setup_fields", no_model)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft(), edited_fields=["theme"], ai_adjusted_fields=["tone"])
        assert project.quick_setup_draft["edited_fields"] == project.quick_setup_draft["ai_adjusted_fields"] == []
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", scope=scope, values=draft(), edited_fields=["theme"], ai_adjusted_fields=["tone"], context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert result["changes"] == [] and result["tokens_used"] == 0
        assert project.total_tokens == 0


@pytest.mark.asyncio
async def test_edited_only_repairs_actual_manual_and_ai_content_not_controls(monkeypatch):
    async def revise(**kwargs):
        assert set(kwargs["allowed_fields"]) == {"tone", "theme"}
        assert "movie_duration" in kwargs["locked_fields"]
        return {"tone": "冷", "theme": "责任"}, "只调整已改内容", 2

    monkeypatch.setattr(llm, "revise_quick_setup_fields", revise)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        values = {**draft(), "tone": "暖", "theme": "爱", "movie_duration": "91.5"}
        await act(session, user, project, values=values, baseline_values=draft(), edited_fields=["theme"], ai_adjusted_fields=["tone", "movie_duration"])
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", values=values, edited_fields=["theme"], ai_adjusted_fields=["tone", "movie_duration"], context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert set(result["changed_fields"]) == {"tone", "theme"}
        assert project.quick_setup_draft["values"]["movie_duration"] == "91.5"


@pytest.mark.asyncio
async def test_invalid_changed_anchor_is_locked_and_explained_not_silently_repaired(monkeypatch):
    async def revise(**kwargs):
        assert "character_details" not in kwargs["allowed_fields"]
        assert "character_details" in kwargs["invalid_changed_fields"]
        assert kwargs["values"]["character_details"] == "短"
        return {"theme": "爱"}, "关联主题建议", 2

    monkeypatch.setattr(llm, "revise_quick_setup_fields", revise)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        values = {**draft(), "character_details": "短"}
        await act(session, user, project, values=values, baseline_values=draft(), edited_fields=["character_details"])
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", scope="related", values=values, context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert "character_details" in result["summary"] and "未自动改写" in result["summary"]
        assert project.quick_setup_draft["values"]["character_details"] == "短"


@pytest.mark.asyncio
async def test_save_during_ai_invalidates_late_candidate_without_losing_saved_work(monkeypatch):
    async def save_then_options(*args, **kwargs):
        async with database.SessionLocal() as other:
            user = await other.get(models.User, 1)
            project = await other.get(models.Project, 1)
            await act(other, user, project, values={**draft(), "theme": "另一会话保存的主题"}, baseline_values=draft())
        return {"question": "过期候选", "options": [{"label": value, "value": value} for value in ("爱", "勇气", "自由")]}, 4

    monkeypatch.setattr(llm, "generate_interaction_options", save_then_options)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                operation="regenerate_field", target_field="theme", values=draft(), context_revision=project.context_revision,
            ), db=session, current_user=user)
        assert error.value.status_code == 409
        await session.refresh(project)
        assert project.quick_setup_draft["values"]["theme"] == "另一会话保存的主题"
        assert project.total_tokens == 4


def test_unversioned_0005_is_not_mistaken_for_0006_and_migration_preserves_data(tmp_path, monkeypatch):
    path = tmp_path / "old-setup-revision.db"
    monkeypatch.setattr(migrate.settings, "database_url", f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(migrate.alembic_config(), migrate.SETUP_REVISION)
    context = json.dumps({"title": "历史标题", "theme": "旧设定"}, ensure_ascii=False)
    cache = json.dumps({"legacy": "cache"})
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users(id, username, hashed_password, is_admin) VALUES(1, 'old', 'unused', 0)")
        connection.execute("INSERT INTO projects(id, owner_id, title, logline, global_context, next_step_cache, setup_revision, setup_cache_revision) VALUES(1,1,'原题目','故事',?,?,9,12)", (context, cache))
        connection.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.SETUP_REVISION
    monkeypatch.setattr("upgrade_admin.upgrade_schema", lambda **kwargs: None)
    migrate.run_migrations()
    migrate.run_migrations()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT title,global_context,next_step_cache,setup_revision,setup_cache_revision,quick_setup_draft FROM projects").fetchone() == ("原题目", context, cache, 9, 12, None)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == migrate.HEAD_REVISION
        connection.execute("DROP TABLE alembic_version")
    assert migrate.unversioned_sqlite_revision(path) == migrate.HEAD_REVISION


@pytest.mark.asyncio
async def test_mode_switch_never_revalidates_a_draft_already_stale():
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft())
        await main.update_project(1, schemas.ProjectUpdate(project_type="movie", context_revision=project.context_revision), db=session, current_user=user)
        stale_work = deepcopy(project.quick_setup_draft)
        for mode in ("guided", "ai_fast"):
            await main.submit_interaction(1, main.InteractionRequest(answer=mode, context_key="setup_mode", context_revision=project.context_revision), db=session, current_user=user)
            assert project.quick_setup_draft == stale_work
            assert project.quick_setup_draft_stale is True


@pytest.mark.asyncio
async def test_saved_ai_provenance_survives_omitted_request_history(monkeypatch):
    async def revise(**kwargs):
        assert kwargs["changed_fields"]["movie_duration"]["source"] == "ai"
        assert "movie_duration" in kwargs["locked_fields"]
        return {}, "无需其他关联变更", 1

    monkeypatch.setattr(llm, "revise_quick_setup_fields", revise)
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        values = {**draft(), "movie_duration": "91.5"}
        await act(session, user, project, values=values, baseline_values=draft(), ai_adjusted_fields=["movie_duration"])
        await act(session, user, project, values=values, baseline_values=values)
        assert project.quick_setup_draft["ai_adjusted_fields"] == ["movie_duration"]
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", scope="related", values=values, context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert result["changes"] == [] and result["tokens_used"] == 1


@pytest.mark.asyncio
async def test_real_related_prompt_contains_baseline_deltas_and_locked_new_direction(monkeypatch):
    baseline = draft()
    values = {**baseline, "theme": "用户明确的新方向"}

    async def raw(messages, **kwargs):
        prompt = messages[1]["content"]
        assert "不得反向改回基线" in prompt
        assert "用户明确的新方向" in prompt
        assert json.dumps({"theme": {"before": baseline["theme"], "after": values["theme"], "source": "manual"}}, ensure_ascii=False) in prompt
        assert "权威比较基线" in prompt and "锁定字段" in prompt
        return '{"fields":{},"summary":"其他关联内容已与新方向一致"}', 2

    monkeypatch.setattr(llm, "raw_generation", raw)
    result, summary, usage = await llm.revise_quick_setup_fields(
        logline="故事", values=values, allowed_fields=["plot_details"], scope="related",
        baseline_values=baseline, changed_fields={"theme": {"before": baseline["theme"], "after": values["theme"], "source": "manual"}},
        locked_fields=["theme", *main.QUICK_CONTROL_FIELDS],
    )
    assert result == {} and usage == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "save_guided"])
async def test_missing_values_never_replaces_an_existing_draft_with_empty_data(action):
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, values=draft(), baseline_values=draft())
        before = deepcopy(project.quick_setup_draft)
        token = project.context_revision
        with pytest.raises(HTTPException) as error:
            await act(session, user, project, action)
        assert error.value.status_code == 422
        assert project.quick_setup_draft == before and project.context_revision == token


@pytest.mark.asyncio
async def test_guided_continues_after_saved_work_becomes_stale_until_explicit_resume():
    async with database.SessionLocal() as session:
        user, project = await seed(session)
        await act(session, user, project, "save_guided", values=draft(), baseline_values=draft())
        saved = deepcopy(project.quick_setup_draft)
        await main.submit_interaction(1, main.InteractionRequest(
            answer="movie", context_key="project_type", context_revision=project.context_revision,
        ), db=session, current_user=user)
        response = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert response["setup_mode"] == "guided"
        assert response["payload"]["field"] == "movie_duration"
        assert response["saved_draft_available"] is True
        assert response["draft_stale"] is True
        assert project.quick_setup_draft == saved
        await main.submit_interaction(1, main.InteractionRequest(
            answer="ai_fast", context_key="setup_mode", context_revision=project.context_revision,
        ), db=session, current_user=user)
        resumed = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert resumed["payload"]["field"] == "quick_review"
        assert resumed["payload"]["draft_stale"] is True
        assert resumed["payload"]["read_only"] is True
        assert resumed["payload"]["values"] == saved["values"]
