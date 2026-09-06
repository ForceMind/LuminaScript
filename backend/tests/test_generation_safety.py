import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
import sqlite3

import database
import auth
from bootstrap_security import ensure_secret_key
from core.config import BASE_DIR, Settings
import main
import migrate
import models
import schemas
import upgrade_admin
from api.admin_routes import update_user_role
from services import login_limiter


async def seed_user(session, user_id: int = 1) -> models.User:
    user = models.User(
        id=user_id,
        username=f"user-{user_id}",
        hashed_password="unused",
    )
    session.add(user)
    await session.commit()
    return user


async def no_op_log(*args, **kwargs):
    return None


def confirmed_movie_context(*, scene_count: str = "1") -> dict[str, str]:
    return {
        "final_confirm": "confirmed",
        "project_type": "movie",
        "movie_duration": "120",
        "scene_count_target": scene_count,
        "tone": "冷峻悬疑",
        "time_period": "近未来沿海都市",
        "story_expansion": "记者发现城市档案每日改写，他联合医生追查集体失忆实验并公开真相。",
        "character_details": "失忆记者执着追查真相，神经科医生掌握旧档案并协助他保存证据。",
        "plot_details": "医生曾参与实验，高潮中记者必须在个人记忆与公开证据之间选择。",
        "title": "失忆之城",
        "theme": "真实与社会秩序的代价",
        "visual_style": "低饱和霓虹与潮湿街道",
        "user_notes": "保持推理线索公平",
        "synopsis_brief": "brief",
        "synopsis_detailed": "detailed",
    }


@pytest.mark.asyncio
async def test_outline_failure_marks_project_failed(monkeypatch):
    async def fail_outline(*args, **kwargs):
        raise RuntimeError("synthetic outline failure")

    monkeypatch.setattr(main.llm, "generate_scene_batch", fail_outline)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="failure",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        await session.commit()

    await main.run_incremental_outline_generation(1, "style", 1, 1)

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scenes_result = await session.execute(
            select(models.Scene).where(models.Scene.project_id == 1)
        )
        assert project.status == models.ProcessingStatus.FAILED
        assert list(scenes_result.scalars().all()) == []


@pytest.mark.asyncio
async def test_content_failure_marks_project_and_scene_failed(monkeypatch):
    async def fail_content(*args, **kwargs):
        raise RuntimeError("synthetic content failure")

    monkeypatch.setattr(main.llm, "write_scene_content", fail_content)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="failure",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        await session.commit()
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="scene",
                status=models.ProcessingStatus.PENDING,
            )
        )
        await session.commit()

    await main.run_generation_loop(1)

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene_result = await session.execute(
            select(models.Scene).where(models.Scene.project_id == 1)
        )
        scene = scene_result.scalars().one()
        assert project.status == models.ProcessingStatus.FAILED
        assert scene.status == models.ProcessingStatus.FAILED


@pytest.mark.asyncio
async def test_late_scene_restart_is_rewritten_once(monkeypatch):
    calls = 0

    async def write_content(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "序幕。故事开始，主角第一次见到搭档。", 5
        return "内景·仓库·夜\n主角握紧上一场拿到的钥匙，继续逼近密室。", 7

    monkeypatch.setattr(main.llm, "write_scene_content", write_content)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="long story",
                logline="持续追查真相",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        await session.flush()
        for index in range(1, 51):
            completed = index < 50
            session.add(
                models.Scene(
                    project_id=1,
                    scene_index=index,
                    outline=f"推进线索 {index}",
                    content=(f"第{index}场结尾，主角保留关键物品。" if completed else None),
                    summary=(f"线索推进到第{index}步" if completed else None),
                    status=(
                        models.ProcessingStatus.COMPLETED
                        if completed
                        else models.ProcessingStatus.PENDING
                    ),
                )
            )
        await session.commit()

    await main.run_generation_loop(1)

    async with database.SessionLocal() as session:
        scene = await session.scalar(
            select(models.Scene)
            .where(models.Scene.project_id == 1)
            .where(models.Scene.scene_index == 50)
        )
        project = await session.get(models.Project, 1)
        assert calls == 2
        assert "上一场拿到的钥匙" in scene.content
        assert scene.status == models.ProcessingStatus.COMPLETED
        assert project.status == models.ProcessingStatus.COMPLETED
        assert project.total_tokens == 12


@pytest.mark.asyncio
async def test_cancel_during_llm_call_discards_returned_content(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_content(*args, **kwargs):
        started.set()
        await release.wait()
        return "这段内容不应在取消后保存", 9

    monkeypatch.setattr(main.llm, "write_scene_content", delayed_content)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="cancel",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        await session.flush()
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="scene",
                status=models.ProcessingStatus.PENDING,
            )
        )
        await session.commit()

    generation_task = asyncio.create_task(main.run_generation_loop(1))
    await asyncio.wait_for(started.wait(), timeout=2)
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        project.status = models.ProcessingStatus.FAILED
        scene.status = models.ProcessingStatus.PENDING
        await session.commit()
    release.set()
    await asyncio.wait_for(generation_task, timeout=2)

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        assert project.status == models.ProcessingStatus.FAILED
        assert scene.status == models.ProcessingStatus.PENDING
        assert scene.content is None
        # Cancellation discards the draft, not already incurred model usage.
        assert project.total_tokens == 9


@pytest.mark.asyncio
async def test_generate_scenes_rejects_second_active_job():
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="project",
                logline="story",
                project_type="movie",
                owner_id=user.id,
                status=models.ProcessingStatus.PENDING,
                global_context=confirmed_movie_context(),
            )
        )
        await session.commit()

        response = await main.generate_scenes(
            project_id=1,
            selected_option="auto",
            context_revision="setup-v2:0:0",
            db=session,
            current_user=user,
        )
        assert response["status"] == "Scene generation queued"
        job = await session.get(models.GenerationJob, response["job_id"])
        assert job.status == models.JobStatus.QUEUED

    async with database.SessionLocal() as session:
        user = await session.get(models.User, 1)
        with pytest.raises(HTTPException) as error:
            await main.generate_scenes(
                project_id=1,
                selected_option="auto",
                context_revision="setup-v2:0:0",
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unconfirmed", "stale_revision"])
async def test_generate_scenes_requires_confirmed_current_setup(case):
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        context = confirmed_movie_context()
        if case == "unconfirmed":
            context.pop("final_confirm")
        session.add(
            models.Project(
                id=1,
                title="失忆之城",
                logline="story",
                project_type="movie",
                owner_id=user.id,
                status=models.ProcessingStatus.PENDING,
                global_context=context,
                setup_revision=1 if case == "stale_revision" else 0,
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as error:
            await main.generate_scenes(
                project_id=1,
                selected_option="auto",
                context_revision="setup-v2:0:0",
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 409
        assert list(await session.scalars(select(models.GenerationJob))) == []
        project = await session.get(models.Project, 1)
        assert project.status == models.ProcessingStatus.PENDING


@pytest.mark.asyncio
async def test_generate_scenes_rechecks_revision_inside_generation_claim(monkeypatch):
    real_claim = main.claim_generation

    async def change_setup_before_claim(db, project_id, actor_id):
        async with database.SessionLocal() as other:
            project = await other.get(models.Project, project_id)
            project.setup_revision += 1
            await other.commit()
        return await real_claim(db, project_id, actor_id)

    monkeypatch.setattr(main, "claim_generation", change_setup_before_claim)
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="失忆之城",
                logline="story",
                project_type="movie",
                owner_id=user.id,
                status=models.ProcessingStatus.PENDING,
                global_context=confirmed_movie_context(),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as error:
            await main.generate_scenes(
                project_id=1,
                selected_option="auto",
                context_revision="setup-v2:0:0",
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 409

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.context_revision == "setup-v2:1:0"
        assert project.status == models.ProcessingStatus.PENDING
        assert list(await session.scalars(select(models.GenerationJob))) == []


@pytest.mark.asyncio
async def test_existing_scenes_require_explicit_full_regeneration_intent():
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        project = models.Project(
            id=1,
            title="失忆之城",
            logline="story",
            project_type="movie",
            owner_id=user.id,
            status=models.ProcessingStatus.COMPLETED,
            global_context=confirmed_movie_context(),
        )
        session.add(project)
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="已有大纲",
                content="已有正文",
                status=models.ProcessingStatus.COMPLETED,
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as error:
            await main.generate_scenes(
                project_id=1,
                selected_option="自定义风格",
                context_revision=project.context_revision,
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 409
        kept = await session.scalar(select(models.Scene))
        assert kept.content == "已有正文"
        assert (await session.get(models.Project, 1)).status == models.ProcessingStatus.COMPLETED


@pytest.mark.asyncio
async def test_auto_option_keeps_existing_full_regeneration_compatibility():
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        project = models.Project(
            id=1,
            title="失忆之城",
            logline="story",
            project_type="movie",
            owner_id=user.id,
            status=models.ProcessingStatus.COMPLETED,
            global_context=confirmed_movie_context(),
        )
        session.add(project)
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="旧大纲",
                content="旧正文",
                status=models.ProcessingStatus.COMPLETED,
            )
        )
        await session.commit()

        response = await main.generate_scenes(
            project_id=1,
            selected_option="auto",
            context_revision=project.context_revision,
            db=session,
            current_user=user,
        )

        assert list(await session.scalars(select(models.Scene))) == []
        version = await session.scalar(select(models.ProjectVersion))
        assert version.snapshot["scenes"][0]["content"] == "旧正文"
        job = await session.get(models.GenerationJob, response["job_id"])
        assert job.status == models.JobStatus.QUEUED


@pytest.mark.asyncio
async def test_outline_resume_skips_durable_prefix_after_midstream_failure(monkeypatch):
    calls: list[int] = []
    fail_scene_five_once = True

    async def generate_batch(_logline, _style, start, _end, **_kwargs):
        nonlocal fail_scene_five_once
        calls.append(start)
        if start == 5 and fail_scene_five_once:
            fail_scene_five_once = False
            raise RuntimeError("synthetic late outline failure")
        return [{"outline": f"新大纲{start}"}], 1

    async def no_content(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main.llm, "generate_scene_batch", generate_batch)
    monkeypatch.setattr(main, "run_generation_loop", no_content)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="resume",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        session.add_all(
            [
                models.Scene(
                    project_id=1,
                    scene_index=index,
                    outline=f"保留大纲{index}",
                    content=f"保留正文{index}",
                    summary=f"保留摘要{index}",
                    status=models.ProcessingStatus.COMPLETED,
                )
                for index in range(1, 4)
            ]
        )
        job = models.GenerationJob(
            project_id=1,
            kind="outline_generation",
            status=models.JobStatus.RUNNING,
            attempts=2,
            max_attempts=3,
            lock_token="resume-token",
            cancel_requested=False,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    await main.run_incremental_outline_generation(
        1,
        "style",
        5,
        1,
        job_id=job_id,
        lock_token="resume-token",
    )
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        project.status = models.ProcessingStatus.GENERATING
        await session.commit()
    await main.run_incremental_outline_generation(
        1,
        "style",
        5,
        1,
        job_id=job_id,
        lock_token="resume-token",
    )

    async with database.SessionLocal() as session:
        scenes = list(
            await session.scalars(
                select(models.Scene)
                .where(models.Scene.project_id == 1)
                .order_by(models.Scene.scene_index)
            )
        )
        assert calls == [4, 5, 5]
        assert [scene.scene_index for scene in scenes] == [1, 2, 3, 4, 5]
        assert [scene.content for scene in scenes[:3]] == [
            "保留正文1",
            "保留正文2",
            "保留正文3",
        ]


@pytest.mark.asyncio
async def test_canceled_inflight_job_discards_result_and_starts_no_followup(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def delayed_content(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "取消后不应保存的正文", 9

    monkeypatch.setattr(main.llm, "write_scene_content", delayed_content)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="cancel fenced",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="scene",
                status=models.ProcessingStatus.PENDING,
            )
        )
        job = models.GenerationJob(
            project_id=1,
            kind="content_generation",
            status=models.JobStatus.RUNNING,
            lock_token="old-token",
            cancel_requested=False,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    task = asyncio.create_task(
        main.run_generation_loop(
            1,
            job_id=job_id,
            lock_token="old-token",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    async with database.SessionLocal() as session:
        job = await session.get(models.GenerationJob, job_id)
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        job.cancel_requested = True
        job.status = models.JobStatus.CANCELED
        job.lock_token = None
        project.status = models.ProcessingStatus.FAILED
        scene.status = models.ProcessingStatus.PENDING
        await session.commit()
    release.set()
    await asyncio.wait_for(task, timeout=2)

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        audit = await session.scalar(select(models.AIInteractionLog))
        assert calls == 1
        assert project.status == models.ProcessingStatus.FAILED
        assert project.total_tokens == 0
        assert scene.status == models.ProcessingStatus.PENDING
        assert scene.content is None
        assert audit.tokens == 9
        assert audit.status == "stale"
        assert audit.error_type == "stale_job_lease"


@pytest.mark.asyncio
async def test_lost_lease_and_audit_failure_reports_unrecorded_usage(monkeypatch):
    provider_calls = 0

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic audit outage")

    monkeypatch.setattr(main, "log_ai_action", fail_audit)

    async with database.SessionLocal() as session:
        await seed_user(session)
        project = models.Project(
            id=1,
            title="audit outage",
            logline="story",
            project_type="movie",
            owner_id=1,
            status=models.ProcessingStatus.GENERATING,
            global_summary="unchanged summary",
        )
        session.add(project)
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="unchanged outline",
                status=models.ProcessingStatus.PENDING,
            )
        )
        job = models.GenerationJob(
            project_id=1,
            kind="content_generation",
            status=models.JobStatus.RUNNING,
            lock_token="audit-token",
            cancel_requested=False,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

        async def provider_result_after_lease_loss():
            nonlocal provider_calls
            provider_calls += 1
            async with database.SessionLocal() as other:
                current_job = await other.get(models.GenerationJob, job_id)
                current_job.cancel_requested = True
                current_job.status = models.JobStatus.CANCELED
                current_job.lock_token = None
                await other.commit()
            return "discarded provider result", 13

        with pytest.raises(HTTPException) as error:
            await main.run_project_ai_call(
                db=session,
                project=project,
                actor_id=1,
                action="write_scene_1",
                prompt="prompt",
                invoke=provider_result_after_lease_loss,
                expected_status=models.ProcessingStatus.GENERATING,
                job_id=job_id,
                lock_token="audit-token",
            )

        assert error.value.status_code == 503
        assert "项目 Token 未写入" in error.value.detail
        assert "没有持久记录" in error.value.detail
        assert "AI 提供商账单" in error.value.detail
        assert "项目 Token 已持久化" not in error.value.detail
        assert "已保留已知项目 Token" not in error.value.detail

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        audits = list(await session.scalars(select(models.AIInteractionLog)))
        assert provider_calls == 1
        assert project.total_tokens == 0
        assert project.status == models.ProcessingStatus.GENERATING
        assert project.global_summary == "unchanged summary"
        assert scene.status == models.ProcessingStatus.PENDING
        assert scene.content is None
        assert audits == []


@pytest.mark.asyncio
async def test_reclaimed_job_rejects_old_token_and_accepts_new_token(monkeypatch):
    calls = 0

    async def content(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "新 worker 生成的正文", 3

    monkeypatch.setattr(main.llm, "write_scene_content", content)
    monkeypatch.setattr(main, "log_ai_action", no_op_log)

    async with database.SessionLocal() as session:
        await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="reclaimed",
                logline="story",
                project_type="movie",
                owner_id=1,
                status=models.ProcessingStatus.GENERATING,
            )
        )
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="scene",
                status=models.ProcessingStatus.PENDING,
            )
        )
        job = models.GenerationJob(
            project_id=1,
            kind="content_generation",
            status=models.JobStatus.RUNNING,
            lock_token="new-token",
            cancel_requested=False,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    await main.run_generation_loop(
        1,
        job_id=job_id,
        lock_token="old-token",
    )
    async with database.SessionLocal() as session:
        scene = await session.scalar(select(models.Scene))
        assert calls == 0
        assert scene.content is None
        assert scene.status == models.ProcessingStatus.PENDING

    await main.run_generation_loop(
        1,
        job_id=job_id,
        lock_token="new-token",
    )
    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        scene = await session.scalar(select(models.Scene))
        assert calls == 1
        assert project.status == models.ProcessingStatus.COMPLETED
        assert scene.content == "新 worker 生成的正文"
        assert scene.status == models.ProcessingStatus.COMPLETED


@pytest.mark.asyncio
async def test_generation_preparation_failure_releases_project_claim(monkeypatch):
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="project",
                logline="story",
                project_type="movie",
                owner_id=user.id,
                status=models.ProcessingStatus.PENDING,
                global_context=confirmed_movie_context(),
            )
        )
        await session.commit()

        def fail_delete(*args, **kwargs):
            raise RuntimeError("synthetic preparation failure")

        monkeypatch.setattr(main, "delete", fail_delete)
        with pytest.raises(HTTPException) as error:
            await main.generate_scenes(
                project_id=1,
                selected_option="auto",
                context_revision="setup-v2:0:0",
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 500

    async with database.SessionLocal() as session:
        project = await session.get(models.Project, 1)
        assert project.status == models.ProcessingStatus.FAILED


@pytest.mark.asyncio
async def test_regenerate_invalidates_prompt_cache():
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        session.add(
            models.Project(
                id=1,
                title="project",
                logline="story",
                project_type="movie",
                owner_id=user.id,
                status=models.ProcessingStatus.COMPLETED,
                global_context={"_scene_ai_prompts": {"1": "stale"}},
            )
        )
        await session.commit()
        session.add(
            models.Scene(
                project_id=1,
                scene_index=1,
                outline="scene",
                content="old",
                status=models.ProcessingStatus.COMPLETED,
            )
        )
        await session.commit()

        response = await main.regenerate_scene(
            project_id=1,
            scene_index=1,
            db=session,
            current_user=user,
        )
        await session.refresh(
            await session.get(models.Project, 1),
            attribute_names=["global_context"],
        )
        project = await session.get(models.Project, 1)
        assert "_scene_ai_prompts" not in project.global_context
        job = await session.get(models.GenerationJob, response["job_id"])
        assert job.kind == "content_generation"
        assert job.status == models.JobStatus.QUEUED


def test_request_schemas_reject_invalid_values():
    with pytest.raises(Exception):
        schemas.UserCreate(username="", password="")
    with pytest.raises(Exception):
        schemas.ProjectCreate(logline="", project_type="not-a-real-type")
    with pytest.raises(Exception):
        main.InteractionRequest(answer="x", context_key="_scene_ai_prompts")


@pytest.mark.asyncio
async def test_admin_can_promote_registered_user_but_not_demote_self():
    async with database.SessionLocal() as session:
        admin = models.User(
            id=1,
            username="admin-user",
            hashed_password="unused",
            is_admin=1,
        )
        registered_user = models.User(
            id=2,
            username="registered-user",
            hashed_password="unused",
            is_admin=0,
        )
        session.add_all([admin, registered_user])
        await session.commit()

        promoted = await update_user_role(
            user_id=registered_user.id,
            role=schemas.AdminRoleUpdate(is_admin=True),
            db=session,
            admin=admin,
        )
        assert promoted.is_admin == 1

        with pytest.raises(HTTPException) as error:
            await update_user_role(
                user_id=admin.id,
                role=schemas.AdminRoleUpdate(is_admin=False),
                db=session,
                admin=admin,
            )
        assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_login_failure_limiter_blocks_and_can_be_cleared(monkeypatch):
    key = "127.0.0.1|test-user"
    monkeypatch.setattr(login_limiter.settings, "login_attempt_max", 3)
    await login_limiter.clear_failures(key)

    for _ in range(3):
        await login_limiter.record_failure(key)

    assert await login_limiter.get_retry_after(key) > 0
    await login_limiter.clear_failures(key)
    assert await login_limiter.get_retry_after(key) == 0


@pytest.mark.asyncio
async def test_password_change_marker_revokes_existing_token():
    async with database.SessionLocal() as session:
        user = await seed_user(session)
        token = auth.create_access_token(
            {
                "sub": user.username,
                "pwd": auth.password_token_version(user.hashed_password),
            }
        )
        assert (await auth.get_current_user(token=token, db=session)).id == user.id

        user.hashed_password = "changed-hash"
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await auth.get_current_user(token=token, db=session)
        assert error.value.status_code == 401


def test_security_bootstrap_normalizes_duplicate_secret_keys(tmp_path):
    env_file = tmp_path / ".env"
    strong_secret = "x" * 48
    env_file.write_text(
        f"SECRET_KEY=weak\nLLM_API_KEY=test\nSECRET_KEY={strong_secret}\n",
        encoding="utf-8",
    )

    assert ensure_secret_key(env_file) is True
    result = env_file.read_text(encoding="utf-8")
    assert result.count("SECRET_KEY=") == 1
    assert f"SECRET_KEY={strong_secret}" in result


@pytest.mark.asyncio
async def test_application_lifespan_accepts_provisioned_admin():
    async with database.SessionLocal() as session:
        session.add(
            models.User(
                username="safe-admin",
                hashed_password=auth.get_password_hash("safe-admin-password"),
                is_admin=1,
            )
        )
        await session.commit()

    async with main.lifespan(main.app):
        async with database.SessionLocal() as session:
            admin_result = await session.execute(
                select(models.User).where(models.User.is_admin == 1)
            )
            assert admin_result.scalars().one().username == "safe-admin"


def test_fresh_database_is_created_by_alembic(tmp_path, monkeypatch):
    database_path = tmp_path / "alembic-fresh.db"
    monkeypatch.setattr(
        migrate.settings,
        "database_url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )

    migrate.run_migrations()

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert {
        "users",
        "projects",
        "scenes",
        "login_logs",
        "ai_logs",
        "generation_jobs",
        "project_members",
        "project_versions",
        "prompt_templates",
        "backup_records",
        "alembic_version",
    }.issubset(tables)
    assert revision == migrate.HEAD_REVISION


def test_legacy_upgrade_archives_and_resolves_duplicate_scenes(tmp_path):
    database_path = tmp_path / "duplicate-scenes.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE scenes (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                scene_index INTEGER,
                outline TEXT,
                content TEXT,
                summary TEXT,
                status VARCHAR
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO scenes (
                id, project_id, scene_index, outline, content, summary, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 5, 2, "较短大纲", None, "备用摘要", "PENDING"),
                (2, 5, 2, "完整大纲", "完整正文内容", None, "COMPLETED"),
                (3, 5, 3, "下一场", "下一场正文", "下一场摘要", "COMPLETED"),
            ],
        )
        groups, archived = upgrade_admin.resolve_duplicate_scenes(connection.cursor())
        connection.execute(
            """
            CREATE UNIQUE INDEX uq_scenes_project_scene_index
            ON scenes (project_id, scene_index)
            """
        )
        connection.commit()

        kept = connection.execute(
            "SELECT id, outline, content, summary, status FROM scenes "
            "WHERE project_id = 5 AND scene_index = 2"
        ).fetchall()
        archive = connection.execute(
            "SELECT source_scene_id, kept_scene_id, summary "
            "FROM scene_duplicate_archive"
        ).fetchall()

    assert groups == 1
    assert archived == 1
    assert kept == [(2, "完整大纲", "完整正文内容", "备用摘要", "COMPLETED")]
    assert archive == [(1, 2, "备用摘要")]


def test_modular_routers_preserve_public_api_paths():
    public_paths = main.app.openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in public_paths.items()
        for method in operations
    }
    assert ("POST", "/token") in routes
    assert ("POST", "/auth/register") in routes
    assert ("PATCH", "/admin/users/{user_id}/role") in routes
    assert ("POST", "/projects/{project_id}/generate_scenes") in routes


def test_database_urls_are_normalized_from_one_config_boundary():
    sqlite_settings = Settings(
        _env_file=None,
        secret_key="x" * 32,
        database_url="sqlite+aiosqlite:///./relative.db",
    )
    postgres_settings = Settings(
        _env_file=None,
        secret_key="x" * 32,
        database_url="postgresql://user:pass@db/lumina",
    )

    assert sqlite_settings.database_url == (
        f"sqlite+aiosqlite:///{(BASE_DIR / 'relative.db').as_posix()}"
    )
    assert postgres_settings.database_url.startswith("postgresql+asyncpg://")
