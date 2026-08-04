from datetime import datetime
import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import database
import models
import auth
from api.operations_routes import cancel_job, retry_job
from services import backups
from services.admin_imports import (
    ADMIN_EXPORT_FORMAT,
    import_admin_export,
    parse_admin_export,
)
from services import system_logs
from services.project_access import require_project_access
from services.prompt_templates import get_prompt_addendum
from services.usage import enforce_user_quota, get_user_usage
from services.versions import create_project_version, restore_project_version


async def seed_project(session):
    owner = models.User(id=1, username="owner", hashed_password="unused")
    viewer = models.User(id=2, username="viewer", hashed_password="unused")
    editor = models.User(id=3, username="editor", hashed_password="unused")
    session.add_all([owner, viewer, editor])
    await session.flush()
    project = models.Project(
        id=1,
        owner_id=owner.id,
        title="连续剧",
        logline="主角追查失踪案",
        project_type="tv",
        status=models.ProcessingStatus.PENDING,
        global_context={"ending": "找到真相"},
    )
    session.add(project)
    await session.flush()
    session.add_all(
        [
            models.ProjectMember(
                project_id=project.id,
                user_id=viewer.id,
                role="viewer",
                created_at=datetime.now().isoformat(),
            ),
            models.ProjectMember(
                project_id=project.id,
                user_id=editor.id,
                role="editor",
                created_at=datetime.now().isoformat(),
            ),
            models.Scene(
                project_id=project.id,
                scene_index=1,
                outline="主角发现线索",
                content="旧正文",
                summary="拿到钥匙",
                status=models.ProcessingStatus.COMPLETED,
            ),
        ]
    )
    await session.commit()
    return owner, viewer, editor, project


@pytest.mark.asyncio
async def test_project_roles_and_version_restore():
    async with database.SessionLocal() as session:
        owner, viewer, editor, project = await seed_project(session)
        _, viewer_role = await require_project_access(session, project.id, viewer.id)
        _, editor_role = await require_project_access(
            session, project.id, editor.id, minimum_role="editor"
        )
        assert viewer_role == "viewer"
        assert editor_role == "editor"
        with pytest.raises(HTTPException) as denied:
            await require_project_access(
                session, project.id, viewer.id, minimum_role="editor"
            )
        assert denied.value.status_code == 404

        version = await create_project_version(session, project.id, owner.id, "初稿")
        await session.commit()
        scene = (await session.scalars(select(models.Scene))).one()
        scene.content = "被修改的正文"
        await session.commit()
        project_with_scenes, _ = await require_project_access(
            session, project.id, owner.id, load_scenes=True
        )
        await restore_project_version(session, project_with_scenes, version)
        await session.commit()
        restored_scene = (await session.scalars(select(models.Scene))).one()
        assert restored_scene.content == "旧正文"


@pytest.mark.asyncio
async def test_quota_blocks_generation_at_limit():
    async with database.SessionLocal() as session:
        owner, *_ = await seed_project(session)
        owner.daily_token_limit = 100
        session.add(
            models.AIInteractionLog(
                user_id=owner.id,
                project_id=1,
                action="test",
                prompt="p",
                response="r",
                tokens=100,
                status="success",
                timestamp=datetime.now().isoformat(),
            )
        )
        await session.commit()
        usage = await get_user_usage(session, owner.id)
        assert usage["daily_tokens"] == 100
        with pytest.raises(HTTPException) as limited:
            await enforce_user_quota(session, owner.id)
        assert limited.value.status_code == 429


@pytest.mark.asyncio
async def test_prompt_templates_are_scoped_by_stage_and_project_type():
    async with database.SessionLocal() as session:
        owner, *_ = await seed_project(session)
        now = datetime.now().isoformat()
        session.add_all(
            [
                models.PromptTemplate(
                    name="通用正文",
                    stage="content",
                    project_type="all",
                    content="保持动作清晰",
                    enabled=True,
                    created_by=owner.id,
                    created_at=now,
                    updated_at=now,
                ),
                models.PromptTemplate(
                    name="剧集正文",
                    stage="content",
                    project_type="tv",
                    content="每集保留悬念",
                    enabled=True,
                    created_by=owner.id,
                    created_at=now,
                    updated_at=now,
                ),
                models.PromptTemplate(
                    name="电影大纲",
                    stage="outline",
                    project_type="movie",
                    content="三幕式",
                    enabled=True,
                    created_by=owner.id,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()
        addendum = await get_prompt_addendum(
            session, stage="content", project_type="tv"
        )
        assert "保持动作清晰" in addendum
        assert "每集保留悬念" in addendum
        assert "三幕式" not in addendum


@pytest.mark.asyncio
async def test_owner_can_cancel_and_retry_failed_job():
    async with database.SessionLocal() as session:
        owner, *_ = await seed_project(session)
        job = models.GenerationJob(
            project_id=1,
            kind="content_generation",
            payload={},
            status=models.JobStatus.RUNNING,
            attempts=1,
            max_attempts=3,
            available_at=datetime.now().isoformat(),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        response = await cancel_job(job.id, db=session, current_user=owner)
        assert response["status"] == "canceled"
        await session.refresh(job)
        assert job.status == models.JobStatus.CANCELED
        response = await retry_job(job.id, db=session, current_user=owner)
        assert response["status"] == "queued"
        new_job = await session.get(models.GenerationJob, response["job_id"])
        assert new_job.status == models.JobStatus.QUEUED


@pytest.mark.asyncio
async def test_encrypted_backup_download_payload_and_safe_copy_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backups, "BACKUP_CONFIG_PATH", tmp_path / "backup-settings.json")
    backups.save_backup_settings(backups.BackupSettings(encrypt=True, retention_count=2))

    async with database.SessionLocal() as session:
        owner, *_ = await seed_project(session)
        record = await backups.create_backup(
            session,
            actor_id=owner.id,
            actor_name=owner.username,
            backup_type="manual",
        )
        encrypted = backups.backup_path(record).read_bytes()
        assert not encrypted.startswith(b"PK")
        decrypted = backups.decrypt_backup(encrypted)
        with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
            names = set(archive.namelist())
            assert {
                "manifest.json",
                "projects.json",
                "project_members.json",
                "project_versions.json",
                "prompt_templates.json",
                "generation_jobs.json",
                "login_logs.json",
                "ai_logs.json",
            }.issubset(names)

        restored = await backups.restore_projects_as_copies(session, record, owner.id)
        assert restored == 1
        projects = (await session.scalars(select(models.Project).order_by(models.Project.id))).all()
        assert len(projects) == 2
        assert projects[-1].title.endswith("（恢复副本）")


def test_system_log_tail_filter_and_secret_redaction(tmp_path, monkeypatch):
    worker_log = tmp_path / "worker.log"
    worker_log.write_text(
        "\n".join(
            [f"normal line {index}" for index in range(25)]
            + ["generation failed api_key=sk-secretvalue123 project 5"]
        ),
        encoding="utf-8",
    )
    runtime_file = tmp_path / ".lumina_runtime"
    runtime_file.write_text(f"WORKER_LOG={worker_log}\n", encoding="utf-8")
    monkeypatch.setattr(system_logs, "RUNTIME_FILES", (runtime_file,))

    result = system_logs.read_system_log("worker", lines=20, keyword="project 5")

    assert result["available"] is True
    assert result["line_count"] == 1
    assert "project 5" in result["content"]
    assert "sk-secretvalue123" not in result["content"]
    assert "api_key=***" in result["content"]


@pytest.mark.asyncio
async def test_admin_export_import_safely_merges_users_projects_and_logs():
    password_hash = auth.get_password_hash("Archived-password-123")
    manifest = {"format": ADMIN_EXPORT_FORMAT, "exported_by": "old-admin"}
    users = [
        {
            "id": 41,
            "username": "archived-user",
            "is_admin": False,
            "hashed_password": password_hash,
        }
    ]
    projects = [
        {
            "id": 88,
            "owner_id": 41,
            "owner_username": "archived-user",
            "title": "归档项目",
            "logline": "一个被重新导入的故事",
            "project_type": "movie",
            "status": "ProcessingStatus.COMPLETED",
            "scenes": [
                {
                    "scene_index": 1,
                    "outline": "开场",
                    "content": "画面亮起。",
                    "status": "ProcessingStatus.COMPLETED",
                }
            ],
        }
    ]
    login_logs = [
        {
            "user_id": 41,
            "user_name": "archived-user",
            "ip_address": "127.0.0.1",
            "status": "success",
            "timestamp": "2026-08-04T00:00:00",
        }
    ]
    ai_logs = [
        {
            "user_id": 41,
            "project_id": 88,
            "action": "write_scene_1",
            "prompt": "prompt",
            "response": "response",
            "tokens": 12,
            "status": "success",
            "timestamp": "2026-08-04T00:00:01",
        }
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in {
            "manifest.json": manifest,
            "users.json": users,
            "projects.json": projects,
            "login_logs.json": login_logs,
            "ai_logs.json": ai_logs,
        }.items():
            archive.writestr(name, json.dumps(content, ensure_ascii=False))

    payload = parse_admin_export(buffer.getvalue())
    async with database.SessionLocal() as session:
        admin = models.User(
            id=1,
            username="current-admin",
            hashed_password="unused",
            is_admin=1,
        )
        session.add(admin)
        await session.commit()

        result = await import_admin_export(session, payload, importing_admin=admin)

        imported_user = await session.scalar(
            select(models.User).where(models.User.username == "archived-user")
        )
        imported_project = await session.scalar(
            select(models.Project).where(models.Project.owner_id == imported_user.id)
        )
        imported_ai_log = await session.scalar(select(models.AIInteractionLog))
        assert result["created_users"] == 1
        assert result["created_projects"] == 1
        assert result["created_scenes"] == 1
        assert result["temporary_passwords"] == []
        assert imported_user.hashed_password == password_hash
        assert imported_project.title == "归档项目（后台导入）"
        assert imported_project.status == models.ProcessingStatus.COMPLETED
        assert imported_ai_log.project_id == imported_project.id
