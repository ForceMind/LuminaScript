from fastapi import BackgroundTasks, HTTPException
import pytest

import main
import models
from services import llm


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
