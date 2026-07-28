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
                global_context={
                    "scene_count_target": "1",
                    "synopsis_brief": "brief",
                    "synopsis_detailed": "detailed",
                },
            )
        )
        await session.commit()

        response = await main.generate_scenes(
            project_id=1,
            selected_option="auto",
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
                db=session,
                current_user=user,
            )
        assert error.value.status_code == 409


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
                global_context={
                    "scene_count_target": "1",
                    "synopsis_brief": "brief",
                    "synopsis_detailed": "detailed",
                },
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
        "alembic_version",
    }.issubset(tables)
    assert revision == migrate.HEAD_REVISION


def test_modular_routers_preserve_public_api_paths():
    routes = {
        (method, route.path)
        for route in main.app.routes
        for method in (route.methods or set())
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
