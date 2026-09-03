from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Optional
from uuid import uuid4
import zipfile

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import database
import models
from core.config import BASE_DIR, settings


logger = logging.getLogger(__name__)
BACKUP_ROOT = BASE_DIR.parent / "backups" / "server"
BACKUP_CONFIG_PATH = BASE_DIR / ".backup_runtime.json"


class BackupSettings(BaseModel):
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=720)
    retention_count: int = Field(default=14, ge=1, le=365)
    encrypt: bool = True
    mirror_directory: Optional[str] = Field(default=None, max_length=2048)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_backup_settings() -> BackupSettings:
    if not BACKUP_CONFIG_PATH.exists():
        return BackupSettings()
    try:
        return BackupSettings.model_validate_json(
            BACKUP_CONFIG_PATH.read_text(encoding="utf-8")
        )
    except Exception as exc:
        logger.error("无法读取备份设置，使用默认值: %s", exc)
        return BackupSettings()


def save_backup_settings(config: BackupSettings) -> BackupSettings:
    temporary = BACKUP_CONFIG_PATH.with_name(
        f".{BACKUP_CONFIG_PATH.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, BACKUP_CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return config


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.require_secure_secret_key().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_backup(payload: bytes) -> bytes:
    return _fernet().encrypt(payload)


def decrypt_backup(payload: bytes) -> bytes:
    return _fernet().decrypt(payload)


def create_sqlite_snapshot_bytes(path: Path) -> bytes:
    handle = tempfile.NamedTemporaryFile(prefix="lumina_snapshot_", suffix=".db", delete=False)
    snapshot_path = Path(handle.name)
    handle.close()
    source = destination = None
    try:
        source = sqlite3.connect(path)
        destination = sqlite3.connect(snapshot_path)
        source.backup(destination)
        destination.commit()
        destination.close()
        destination = None
        return snapshot_path.read_bytes()
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        snapshot_path.unlink(missing_ok=True)


def active_sqlite_path() -> Path | None:
    prefix = "sqlite+aiosqlite:///"
    if not database.DATABASE_URL.startswith(prefix):
        return None
    candidate = Path(database.DATABASE_URL[len(prefix):].split("?", 1)[0]).resolve()
    return candidate if candidate.exists() else None


async def build_backup_archive(db: AsyncSession, actor_name: str) -> bytes:
    users = (await db.execute(select(models.User).order_by(models.User.id))).scalars().all()
    projects = (
        await db.execute(
            select(models.Project)
            .options(selectinload(models.Project.scenes))
            .order_by(models.Project.id)
        )
    ).scalars().all()
    members = (await db.execute(select(models.ProjectMember).order_by(models.ProjectMember.id))).scalars().all()
    versions = (await db.execute(select(models.ProjectVersion).order_by(models.ProjectVersion.id))).scalars().all()
    templates = (await db.execute(select(models.PromptTemplate).order_by(models.PromptTemplate.id))).scalars().all()
    jobs = (await db.execute(select(models.GenerationJob).order_by(models.GenerationJob.id))).scalars().all()
    login_logs = (await db.execute(select(models.LoginLog).order_by(models.LoginLog.id))).scalars().all()
    ai_logs = (await db.execute(select(models.AIInteractionLog).order_by(models.AIInteractionLog.id))).scalars().all()
    user_names = {user.id: user.username for user in users}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "format": "luminascript-backup-v2",
            "created_at": utc_iso(),
            "created_by": actor_name,
            "counts": {
                "users": len(users),
                "projects": len(projects),
                "members": len(members),
                "versions": len(versions),
                "prompt_templates": len(templates),
                "jobs": len(jobs),
                "login_logs": len(login_logs),
                "ai_logs": len(ai_logs),
            },
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(
            "users.json",
            json.dumps(
                [
                    {
                        "id": u.id,
                        "username": u.username,
                        "is_admin": bool(u.is_admin),
                        "daily_token_limit": int(u.daily_token_limit or 0),
                        "monthly_token_limit": int(u.monthly_token_limit or 0),
                    }
                    for u in users
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "projects.json",
            json.dumps(
                [
                    {
                        "id": p.id,
                        "owner_id": p.owner_id,
                        "owner_username": user_names.get(p.owner_id, ""),
                        "title": p.title,
                        "logline": p.logline,
                        "project_type": p.project_type,
                        "genre": p.genre,
                        "global_context": p.global_context or {},
                        "global_summary": p.global_summary,
                        "setup_revision": int(p.setup_revision or 0),
                        "setup_cache_revision": int(p.setup_cache_revision or 0),
                        "quick_setup_draft": p.quick_setup_draft,
                        "total_tokens": int(p.total_tokens or 0),
                        "scenes": [
                            {
                                "scene_index": s.scene_index,
                                "outline": s.outline,
                                "content": s.content,
                                "summary": s.summary,
                                "status": str(getattr(s.status, "value", s.status)),
                            }
                            for s in sorted(p.scenes or [], key=lambda item: item.scene_index)
                        ],
                    }
                    for p in projects
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "project_members.json",
            json.dumps(
                [
                    {
                        "project_id": item.project_id,
                        "user_id": item.user_id,
                        "role": item.role,
                        "created_at": item.created_at,
                    }
                    for item in members
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "project_versions.json",
            json.dumps(
                [
                    {
                        "project_id": item.project_id,
                        "created_by": item.created_by,
                        "label": item.label,
                        "snapshot": item.snapshot,
                        "created_at": item.created_at,
                    }
                    for item in versions
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "prompt_templates.json",
            json.dumps(
                [
                    {
                        "name": item.name,
                        "stage": item.stage,
                        "project_type": item.project_type,
                        "content": item.content,
                        "enabled": bool(item.enabled),
                        "created_by": item.created_by,
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                    }
                    for item in templates
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "generation_jobs.json",
            json.dumps(
                [
                    {
                        "project_id": item.project_id,
                        "kind": item.kind,
                        "payload": item.payload or {},
                        "status": str(getattr(item.status, "value", item.status)),
                        "attempts": int(item.attempts or 0),
                        "max_attempts": int(item.max_attempts or 0),
                        "last_error": item.last_error,
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                    }
                    for item in jobs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "login_logs.json",
            json.dumps(
                [
                    {
                        "user_id": item.user_id,
                        "ip_address": item.ip_address,
                        "user_agent": item.user_agent,
                        "location": item.location,
                        "status": item.status,
                        "timestamp": item.timestamp,
                    }
                    for item in login_logs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "ai_logs.json",
            json.dumps(
                [
                    {
                        "user_id": item.user_id,
                        "billed_user_id": item.billed_user_id,
                        "billed_username": user_names.get(item.billed_user_id),
                        "project_id": item.project_id,
                        "action": item.action,
                        "prompt": item.prompt,
                        "response": item.response,
                        "tokens": int(item.tokens or 0),
                        "status": item.status,
                        "step_key": item.step_key,
                        "error_type": item.error_type,
                        "error_message": item.error_message,
                        "attempt": int(item.attempt or 1),
                        "timestamp": item.timestamp,
                    }
                    for item in ai_logs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        sqlite_path = active_sqlite_path()
        if sqlite_path:
            archive.writestr(
                f"database/{sqlite_path.name}",
                create_sqlite_snapshot_bytes(sqlite_path),
            )
    return buffer.getvalue()


async def create_backup(
    db: AsyncSession,
    *,
    actor_id: int | None,
    actor_name: str,
    backup_type: str,
) -> models.BackupRecord:
    config = load_backup_settings()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    archive = await build_backup_archive(db, actor_name)
    suffix = ".zip"
    if config.encrypt:
        archive = encrypt_backup(archive)
        suffix += ".enc"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"luminascript_{backup_type}_{stamp}_{uuid4().hex[:6]}{suffix}"
    path = (BACKUP_ROOT / filename).resolve()
    path.write_bytes(archive)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    if config.mirror_directory:
        mirror = Path(config.mirror_directory).expanduser().resolve()
        mirror.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, mirror / filename)

    record = models.BackupRecord(
        filename=filename,
        size_bytes=len(archive),
        status="completed",
        backup_type=backup_type,
        created_by=actor_id,
        created_at=utc_iso(),
        notes="encrypted" if config.encrypt else "plain zip",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    await apply_retention(db, config.retention_count, config.mirror_directory)
    return record


async def apply_retention(
    db: AsyncSession,
    retention_count: int,
    mirror_directory: str | None = None,
) -> None:
    records = (
        await db.execute(
            select(models.BackupRecord).order_by(models.BackupRecord.id.desc())
        )
    ).scalars().all()
    for record in records[max(1, int(retention_count)):]:
        path = (BACKUP_ROOT / record.filename).resolve()
        if path.parent == BACKUP_ROOT.resolve():
            path.unlink(missing_ok=True)
        if mirror_directory:
            mirror_root = Path(mirror_directory).expanduser().resolve()
            mirror_path = (mirror_root / record.filename).resolve()
            if mirror_path.parent == mirror_root:
                mirror_path.unlink(missing_ok=True)
        await db.delete(record)
    await db.commit()


def backup_path(record: models.BackupRecord) -> Path:
    path = (BACKUP_ROOT / record.filename).resolve()
    if path.parent != BACKUP_ROOT.resolve() or not path.exists():
        raise FileNotFoundError(record.filename)
    return path


async def restore_projects_as_copies(
    db: AsyncSession,
    record: models.BackupRecord,
    fallback_owner_id: int,
) -> int:
    payload = backup_path(record).read_bytes()
    if record.filename.endswith(".enc"):
        payload = decrypt_backup(payload)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        projects = json.loads(archive.read("projects.json"))
        source_users = json.loads(archive.read("users.json")) if "users.json" in archive.namelist() else []
    existing_users = (await db.scalars(select(models.User))).all()
    users_by_name = {user.username: user.id for user in existing_users}
    source_user_map = {int(user["id"]): users_by_name.get(user.get("username")) for user in source_users if user.get("id")}
    restored = 0
    for item in projects:
        owner_id = (source_user_map.get(int(item.get("owner_id") or 0))
                    or users_by_name.get(item.get("owner_username")) or fallback_owner_id)
        project = models.Project(
            owner_id=owner_id,
            title=f"{item.get('title') or '恢复项目'}（恢复副本）",
            logline=item.get("logline") or "恢复的项目",
            project_type=item.get("project_type") or "movie",
            genre=item.get("genre"),
            global_context=item.get("global_context") or {},
            global_summary=item.get("global_summary"),
            setup_revision=int(item.get("setup_revision") or 0),
            setup_cache_revision=int(item.get("setup_cache_revision") or 0),
            quick_setup_draft=item.get("quick_setup_draft"),
            total_tokens=int(item.get("total_tokens") or 0),
            status=models.ProcessingStatus.PENDING,
        )
        db.add(project)
        await db.flush()
        for scene_item in item.get("scenes") or []:
            status_value = scene_item.get("status") or "pending"
            try:
                scene_status = models.ProcessingStatus(status_value)
            except ValueError:
                scene_status = models.ProcessingStatus.PENDING
            db.add(
                models.Scene(
                    project_id=project.id,
                    scene_index=int(scene_item.get("scene_index") or 1),
                    outline=scene_item.get("outline") or "恢复场次",
                    content=scene_item.get("content"),
                    summary=scene_item.get("summary"),
                    status=scene_status,
                )
            )
        restored += 1
    await db.commit()
    return restored


async def backup_scheduler_loop() -> None:
    while True:
        try:
            config = load_backup_settings()
            if config.enabled:
                async with database.SessionLocal() as db:
                    latest = (
                        await db.execute(
                            select(models.BackupRecord)
                            .where(models.BackupRecord.backup_type == "scheduled")
                            .order_by(models.BackupRecord.id.desc())
                            .limit(1)
                        )
                    ).scalars().first()
                    due = latest is None
                    if latest:
                        try:
                            last_time = datetime.fromisoformat(latest.created_at)
                            if last_time.tzinfo is None:
                                last_time = last_time.replace(tzinfo=timezone.utc)
                            elapsed = datetime.now(timezone.utc) - last_time
                            due = elapsed.total_seconds() >= config.interval_hours * 3600
                        except (TypeError, ValueError):
                            due = True
                    if due:
                        await create_backup(
                            db,
                            actor_id=None,
                            actor_name="system-scheduler",
                            backup_type="scheduled",
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("定时备份失败: %s", exc)
        await asyncio.sleep(60)
