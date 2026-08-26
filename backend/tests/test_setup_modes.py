from fastapi import BackgroundTasks, HTTPException
import pytest

import main
import models
from services import llm


def test_ai_revision_request_rejects_unknown_and_strips_optional_fields():
    with pytest.raises(ValueError):
        main.QuickSetupAIReviseRequest(
            operation="review_edits", values={"unknown": "x"},
            context_revision="12345678", edited_fields=["tone"]
        )
    request = main.QuickSetupAIReviseRequest(
        operation="review_edits", values={"tone": "x"},
        context_revision="12345678", edited_fields=["tone"],
        instruction="  improve  ", target_field=" tone "
    )
    assert request.instruction == "improve"
    assert request.target_field == "tone"

    with pytest.raises(ValueError):
        main.QuickSetupAIReviseRequest(
            operation="review_edits",
            values={"movie_duration": "120"},
            context_revision="12345678",
            edited_fields=["movie_duration"],
        )


@pytest.mark.asyncio
async def test_revision_llm_rejects_out_of_scope_json(monkeypatch):
    async def fake_raw(*args, **kwargs):
        return '{"fields":{"tone":"新","title":"越界"},"summary":"x"}', 3

    monkeypatch.setattr(llm, "raw_generation", fake_raw)
    with pytest.raises(llm.InteractionGenerationError) as error:
        await llm.revise_quick_setup_fields(
            logline="创意", values={"tone": "旧"}, allowed_fields=["tone"]
        )
    assert error.value.error_type == "revision_scope_violation"


@pytest.mark.asyncio
async def test_revision_llm_parses_scoped_candidate(monkeypatch):
    async def fake_raw(messages, **kwargs):
        assert "记者追查真相" in messages[1]["content"]
        assert "单项重新生成" in messages[1]["content"]
        return '{"fields":{"tone":"更紧张的都市悬疑"},"summary":"已改变基调"}', 9

    monkeypatch.setattr(llm, "raw_generation", fake_raw)
    fields, summary, usage = await llm.revise_quick_setup_fields(
        logline="记者追查真相",
        values={"tone": "克制悬疑"},
        allowed_fields=["tone"],
        operation="regenerate_field",
    )

    assert fields == {"tone": "更紧张的都市悬疑"}
    assert summary == "已改变基调"
    assert usage == 9


async def seed_project(
    session,
    *,
    project_type: str = "pending",
    context: dict | None = None,
) -> tuple[models.User, models.Project]:
    user = models.User(
        id=1,
        username="setup-user",
        hashed_password="unused",
        is_admin=0,
    )
    project = models.Project(
        id=1,
        title="创意草稿",
        logline="一名失忆记者追查一座城市反复消失的真相。",
        project_type=project_type,
        owner_id=1,
        global_context=context or {},
    )
    session.add_all([user, project])
    await session.commit()
    return user, project


def complete_movie_draft() -> dict[str, str]:
    return {
        "project_type": "movie",
        "movie_duration": "120",
        "scene_count_target": "80",
        "tone": "冷峻悬疑与克制的人性温度",
        "time_period": "近未来的沿海都市",
        "story_expansion": (
            "第一幕记者发现城市档案每天都会改写；第二幕他与一名保留旧记忆的医生结盟，"
            "逐步逼近集体失忆实验；第三幕两人公开真相，并在城市重置前保住关键证据。"
        ),
        "character_details": (
            "主角：失忆记者，执着但不再相信自己的记忆。\n"
            "对手：实验负责人，以社会稳定为理由篡改记忆。\n"
            "关键配角：神经科医生，掌握旧档案并承担背叛嫌疑。"
        ),
        "plot_details": "关键转折是医生曾参与实验；高潮中主角必须选择个人记忆或公开证据。",
        "title": "失忆之城",
        "theme": "真实、记忆与社会秩序之间的代价",
        "visual_style": "低饱和霓虹、潮湿街道与不断重复的城市构图",
        "user_notes": "保持推理线索公平，不使用梦境作为最终解释",
    }


async def no_op_async(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_ai_revision_regenerates_one_field_without_applying(monkeypatch):
    from database import SessionLocal

    audits = []

    async def fake_options(
        step_key,
        base_question,
        context_str,
        template_instructions="",
    ):
        assert step_key == "tone"
        assert "基调" in base_question
        assert "current_draft" in context_str
        assert template_instructions == ""
        return {
            "question": "请选择一种新基调",
            "options": [
                {"label": "方案一", "value": "高密度都市悬疑"},
                {"label": "方案二", "value": "黑色幽默科幻"},
                {"label": "方案三", "value": "克制冷峻惊悚"},
                {"label": "方案四", "value": "温暖的社会寓言"},
            ],
        }, 11

    async def capture_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(main, "enforce_user_quota", no_op_async)
    monkeypatch.setattr(main, "log_ai_action", capture_audit)
    monkeypatch.setattr(main.llm, "generate_interaction_options", fake_options)

    async with SessionLocal() as session:
        user, project = await seed_project(
            session,
            context={main.SETUP_MODE_KEY: main.SETUP_MODE_AI_FAST},
        )
        original_context = dict(project.global_context)
        response = await main.revise_quick_setup_with_ai(
            1,
            main.QuickSetupAIReviseRequest(
                operation="regenerate_field",
                values=complete_movie_draft(),
                target_field="tone",
                context_revision=main.build_setup_context_revision(project),
            ),
            db=session,
            current_user=user,
        )
        await session.refresh(project)

    assert response["status"] == "options"
    assert response["target_field"] == "tone"
    assert len(response["options"]) == 3
    assert [item["label"] for item in response["options"]] == [
        "方案一",
        "方案二",
        "方案三",
    ]
    assert response["tokens_used"] == 11
    assert response["total_tokens"] == 11
    assert project.project_type == "pending"
    assert project.title == "创意草稿"
    assert project.global_context == original_context
    assert audits[0]["action"] == "regenerate_quick_setup_field"
    assert audits[0]["status"] == "success"


@pytest.mark.asyncio
async def test_ai_revision_edited_only_rejects_model_scope_violation(monkeypatch):
    from database import SessionLocal

    audits = []

    async def fake_revise(**kwargs):
        assert kwargs["allowed_fields"] == ["theme"]
        return {
            "theme": "记忆如何塑造真实",
            "plot_details": "不应被允许的越界修改",
        }, "越界", 4

    async def capture_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(main, "enforce_user_quota", no_op_async)
    monkeypatch.setattr(main, "log_ai_action", capture_audit)
    monkeypatch.setattr(main.llm, "revise_quick_setup_fields", fake_revise)

    async with SessionLocal() as session:
        user, project = await seed_project(
            session,
            context={main.SETUP_MODE_KEY: main.SETUP_MODE_AI_FAST},
        )
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(
                1,
                main.QuickSetupAIReviseRequest(
                    operation="review_edits",
                    scope="edited_only",
                    values=complete_movie_draft(),
                    edited_fields=["theme"],
                    context_revision=main.build_setup_context_revision(project),
                ),
                db=session,
                current_user=user,
            )
        await session.refresh(project)

    assert error.value.status_code == 503
    assert project.global_context == {main.SETUP_MODE_KEY: main.SETUP_MODE_AI_FAST}
    assert project.total_tokens == 4
    assert audits[0]["status"] == "failed"
    assert audits[0]["error_type"] == "invalid_ai_candidate"


@pytest.mark.asyncio
async def test_ai_revision_related_scope_locks_control_fields(monkeypatch):
    from database import SessionLocal

    async def fake_revise(**kwargs):
        allowed = set(kwargs["allowed_fields"])
        assert allowed
        assert not allowed.intersection(main.QUICK_CONTROL_FIELDS)
        assert {"theme", "plot_details"}.issubset(allowed)
        return {
            "theme": "真实与稳定之间的道德代价",
            "plot_details": "医生曾主动参与实验；高潮中她公开自己的责任。",
        }, "联动校准了主题与关键转折", 7

    monkeypatch.setattr(main, "enforce_user_quota", no_op_async)
    monkeypatch.setattr(main, "log_ai_action", no_op_async)
    monkeypatch.setattr(main.llm, "revise_quick_setup_fields", fake_revise)

    async with SessionLocal() as session:
        user, project = await seed_project(
            session,
            context={main.SETUP_MODE_KEY: main.SETUP_MODE_AI_FAST},
        )
        response = await main.revise_quick_setup_with_ai(
            1,
            main.QuickSetupAIReviseRequest(
                operation="review_edits",
                scope="related",
                values=complete_movie_draft(),
                edited_fields=["theme"],
                context_revision=main.build_setup_context_revision(project),
            ),
            db=session,
            current_user=user,
        )

    assert set(response["changed_fields"]) == {"theme", "plot_details"}
    assert not set(response["changed_fields"]).intersection(main.QUICK_CONTROL_FIELDS)


@pytest.mark.asyncio
async def test_ai_revision_rejects_stale_context_before_calling_ai(monkeypatch):
    from database import SessionLocal

    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("stale requests must stop before quota or AI calls")

    monkeypatch.setattr(main, "enforce_user_quota", fail_if_called)
    monkeypatch.setattr(main.llm, "revise_quick_setup_fields", fail_if_called)
    monkeypatch.setattr(main.llm, "generate_interaction_options", fail_if_called)

    async with SessionLocal() as session:
        user, _project = await seed_project(session)
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(
                1,
                main.QuickSetupAIReviseRequest(
                    operation="regenerate_field",
                    values=complete_movie_draft(),
                    target_field="tone",
                    context_revision="stale-revision",
                ),
                db=session,
                current_user=user,
            )

    assert error.value.status_code == 409
    assert called is False


@pytest.mark.asyncio
async def test_ai_revision_discards_candidate_if_context_changes_during_call(monkeypatch):
    from database import SessionLocal

    audits = []

    async def fake_options(*_args, **_kwargs):
        return {
            "question": "过期的选项",
            "options": [
                {"label": "一", "value": "过期基调一"},
                {"label": "二", "value": "过期基调二"},
                {"label": "三", "value": "过期基调三"},
            ],
        }, 5

    async def capture_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(main, "enforce_user_quota", no_op_async)
    monkeypatch.setattr(main, "log_ai_action", capture_audit)
    monkeypatch.setattr(main.llm, "generate_interaction_options", fake_options)

    async with SessionLocal() as session:
        user, project = await seed_project(session)
        initial_revision = main.build_setup_context_revision(project)
        revision_calls = 0

        def changing_revision(_project):
            nonlocal revision_calls
            revision_calls += 1
            return initial_revision if revision_calls == 1 else "changed-during-ai-call"

        monkeypatch.setattr(main, "build_setup_context_revision", changing_revision)
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(
                1,
                main.QuickSetupAIReviseRequest(
                    operation="regenerate_field",
                    values=complete_movie_draft(),
                    target_field="tone",
                    context_revision=initial_revision,
                ),
                db=session,
                current_user=user,
            )
        await session.refresh(project)

    assert error.value.status_code == 409
    assert project.total_tokens == 5
    assert project.project_type == "pending"
    assert project.global_context == {}
    assert audits[0]["status"] == "stale"
    assert audits[0]["error_type"] == "stale_context"


@pytest.mark.asyncio
async def test_ai_revision_rejects_invalid_numeric_candidate_and_charges_usage(monkeypatch):
    from database import SessionLocal

    audits = []

    async def fake_options(step_key, *_args, **_kwargs):
        assert step_key == "movie_duration"
        return {
            "question": "选择时长",
            "options": [
                {"label": "过长一", "value": "9999"},
                {"label": "过长二", "value": "8888"},
                {"label": "过长三", "value": "7777"},
            ],
        }, 6

    async def capture_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(main, "enforce_user_quota", no_op_async)
    monkeypatch.setattr(main, "log_ai_action", capture_audit)
    monkeypatch.setattr(main.llm, "generate_interaction_options", fake_options)

    async with SessionLocal() as session:
        user, project = await seed_project(session)
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(
                1,
                main.QuickSetupAIReviseRequest(
                    operation="regenerate_field",
                    values=complete_movie_draft(),
                    target_field="movie_duration",
                    context_revision=main.build_setup_context_revision(project),
                ),
                db=session,
                current_user=user,
            )
        await session.refresh(project)

    assert error.value.status_code == 503
    assert project.project_type == "pending"
    assert project.global_context == {}
    assert project.total_tokens == 6
    assert audits[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_fresh_project_offers_setup_mode_choice():
    from database import SessionLocal

    async with SessionLocal() as session:
        user, _project = await seed_project(session)
        response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )

    assert response["type"] == "interaction_required"
    assert response["payload"]["field"] == "setup_mode"
    assert [item["value"] for item in response["payload"]["options"]] == [
        "ai_fast",
        "guided",
    ]


@pytest.mark.asyncio
async def test_guided_mode_continues_existing_question_flow():
    from database import SessionLocal

    async with SessionLocal() as session:
        user, _project = await seed_project(session)
        await main.submit_interaction(
            1,
            main.InteractionRequest(answer="guided", context_key="setup_mode"),
            db=session,
            current_user=user,
        )
        response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )

    assert response["payload"]["field"] == "project_type"


@pytest.mark.asyncio
async def test_legacy_project_without_mode_continues_as_guided():
    from database import SessionLocal

    async with SessionLocal() as session:
        user, project = await seed_project(
            session,
            project_type="movie",
            context={"tone": "已经存在的旧项目基调"},
        )
        response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )
        await session.refresh(project)

    assert response["payload"]["field"] == "movie_duration"
    assert project.global_context[main.SETUP_MODE_KEY] == "guided"


@pytest.mark.asyncio
async def test_quick_mode_generates_reviews_and_atomically_confirms(monkeypatch):
    from database import SessionLocal

    async def fake_generate_quick_setup_draft(**kwargs):
        return complete_movie_draft(), 17

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", fake_generate_quick_setup_draft)

    async with SessionLocal() as session:
        user, _project = await seed_project(session)
        await main.submit_interaction(
            1,
            main.InteractionRequest(answer="ai_fast", context_key="setup_mode"),
            db=session,
            current_user=user,
        )
        draft_response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )
        payload = draft_response["payload"]
        values = {item["key"]: item["value"] for item in payload["sections"]}

        confirm_response = await main.submit_quick_setup_review(
            1,
            main.QuickSetupReviewRequest(
                action="confirm",
                values=values,
                edited_fields=["theme"],
                context_revision=payload["context_revision"],
            ),
            db=session,
            current_user=user,
        )
        completed = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )
        project = await session.get(models.Project, 1)

    assert payload["field"] == "quick_review"
    assert values["project_type"] == "movie"
    assert confirm_response["status"] == "confirmed"
    assert completed["type"] == "completed"
    assert project.project_type == "movie"
    assert project.title == "失忆之城"
    assert project.global_context["final_confirm"] == "confirmed"
    assert project.global_context[main.QUICK_EDITED_FIELDS_KEY] == ["theme"]
    assert project.total_tokens == 17


@pytest.mark.asyncio
async def test_quick_review_rejects_stale_context(monkeypatch):
    from database import SessionLocal

    async def fake_generate_quick_setup_draft(**kwargs):
        return complete_movie_draft(), 0

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", fake_generate_quick_setup_draft)

    async with SessionLocal() as session:
        user, project = await seed_project(session)
        await main.submit_interaction(
            1,
            main.InteractionRequest(answer="ai_fast", context_key="setup_mode"),
            db=session,
            current_user=user,
        )
        draft_response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )
        project.global_context = {
            **project.global_context,
            "tone": "另一个标签页修改后的基调",
        }
        await session.commit()

        with pytest.raises(HTTPException) as error:
            await main.submit_quick_setup_review(
                1,
                main.QuickSetupReviewRequest(
                    action="confirm",
                    values=complete_movie_draft(),
                    context_revision=draft_response["payload"]["context_revision"],
                ),
                db=session,
                current_user=user,
            )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_quick_generation_failure_offers_retry_or_guided(monkeypatch):
    from database import SessionLocal

    async def fail_quick_setup(**kwargs):
        raise RuntimeError("synthetic quick setup failure")

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", fail_quick_setup)

    async with SessionLocal() as session:
        user, _project = await seed_project(session)
        await main.submit_interaction(
            1,
            main.InteractionRequest(answer="ai_fast", context_key="setup_mode"),
            db=session,
            current_user=user,
        )
        response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )

    assert response["payload"]["field"] == "setup_mode"
    assert [item["value"] for item in response["payload"]["options"]] == [
        "ai_fast",
        "guided",
    ]


@pytest.mark.asyncio
async def test_quick_mode_preserves_answers_already_confirmed(monkeypatch):
    from database import SessionLocal

    generated = complete_movie_draft()
    generated["tone"] = "不应覆盖的 AI 基调"

    async def fake_generate_quick_setup_draft(**kwargs):
        return generated, 0

    monkeypatch.setattr(main.llm, "generate_quick_setup_draft", fake_generate_quick_setup_draft)

    async with SessionLocal() as session:
        user, _project = await seed_project(
            session,
            project_type="movie",
            context={
                main.SETUP_MODE_KEY: "guided",
                "movie_duration": "90",
                "scene_count_target": "60",
                "tone": "用户已经确认的温暖现实主义",
            },
        )
        await main.submit_interaction(
            1,
            main.InteractionRequest(answer="ai_fast", context_key="setup_mode"),
            db=session,
            current_user=user,
        )
        response = await main.analyze_logline(
            1,
            BackgroundTasks(),
            db=session,
            current_user=user,
        )
        values = {item["key"]: item["value"] for item in response["payload"]["sections"]}
        assert values["tone"] == "用户已经确认的温暖现实主义"
        assert values["movie_duration"] == "90"
        assert values["scene_count_target"] == "60"

        values["tone"] = "用户在快速审查中修改后的冷峻现实主义"
        await main.submit_quick_setup_review(
            1,
            main.QuickSetupReviewRequest(
                action="confirm",
                values=values,
                edited_fields=["tone"],
                context_revision=response["payload"]["context_revision"],
            ),
            db=session,
            current_user=user,
        )
        await session.refresh(_project)

    assert _project.global_context["tone"] == "用户在快速审查中修改后的冷峻现实主义"


@pytest.mark.asyncio
async def test_quick_setup_service_parses_fields(monkeypatch):
    async def fake_raw_generation(*args, **kwargs):
        return '{"fields":{"project_type":"movie","tone":"悬疑"}}', 8

    monkeypatch.setattr(llm, "raw_generation", fake_raw_generation)
    fields, usage = await llm.generate_quick_setup_draft(
        logline="故事",
        current_context={},
        field_specs=[
            {"key": "project_type", "question": "类型"},
            {"key": "tone", "question": "基调"},
        ],
    )

    assert fields == {"project_type": "movie", "tone": "悬疑"}
    assert usage == 8
