from datetime import datetime
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import database
import models
import schemas
from api.dependencies import require_admin
from database import get_db


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def serialize_login_log(log: models.LoginLog, username: str) -> dict[str, Any]:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_name": str(username or ""),
        "ip_address": str(log.ip_address or ""),
        "user_agent": str(log.user_agent or ""),
        "location": str(log.location or ""),
        "status": str(log.status or ""),
        "timestamp": str(log.timestamp or ""),
    }


def serialize_ai_log(
    log: models.AIInteractionLog,
    username: str,
) -> dict[str, Any]:
    def safe_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        try:
            return str(value)
        except Exception:
            return default

    def safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_name": safe_text(username),
        "project_id": log.project_id,
        "action": safe_text(log.action),
        "prompt": safe_text(log.prompt),
        "response": safe_text(log.response),
        "tokens": safe_int(log.tokens),
        "status": safe_text(log.status, "success") or "success",
        "step_key": safe_text(log.step_key),
        "error_type": safe_text(log.error_type),
        "error_message": safe_text(log.error_message),
        "attempt": safe_int(log.attempt, 1),
        "timestamp": safe_text(log.timestamp),
    }


def serialize_scene(scene: models.Scene) -> dict[str, Any]:
    return {
        "id": scene.id,
        "scene_index": scene.scene_index,
        "outline": scene.outline,
        "content": scene.content,
        "summary": scene.summary,
        "status": str(scene.status),
    }


def serialize_project(
    project: models.Project,
    owner_lookup: dict[int, str],
) -> dict[str, Any]:
    return {
        "id": project.id,
        "owner_id": project.owner_id,
        "owner_username": owner_lookup.get(project.owner_id, ""),
        "title": project.title,
        "logline": project.logline,
        "project_type": project.project_type,
        "genre": project.genre,
        "status": str(project.status),
        "total_tokens": project.total_tokens,
        "global_context": project.global_context or {},
        "global_summary": project.global_summary,
        "scene_count": len(project.scenes or []),
        "scenes": [
            serialize_scene(scene)
            for scene in sorted(
                project.scenes or [],
                key=lambda item: item.scene_index,
            )
        ],
    }


def iter_export_database_paths() -> list[Path]:
    candidates: list[Path] = []
    sqlite_prefix = "sqlite+aiosqlite:///"
    if database.DATABASE_URL.startswith(sqlite_prefix):
        candidates.append(Path(database.DATABASE_URL[len(sqlite_prefix):]))

    backend_dir = Path(__file__).resolve().parents[1]
    project_dir = backend_dir.parent
    candidates.extend(
        [
            backend_dir / "lumina_v2.db",
            backend_dir / "lumina.db",
            project_dir / "lumina_v2.db",
            project_dir / "lumina.db",
        ]
    )

    unique_paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = resolved.as_posix()
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        unique_paths.append(resolved)
    return unique_paths


def schema_query_error(exc: OperationalError) -> HTTPException:
    logger.exception("管理员日志查询遇到数据库结构错误: %s", exc)
    return HTTPException(
        status_code=500,
        detail="数据库结构异常，请先执行迁移命令后重试。",
    )


@router.get("/users", response_model=list[schemas.UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    result = await db.execute(select(models.User).order_by(models.User.id.asc()))
    return result.scalars().all()


@router.patch("/users/{user_id}/role", response_model=schemas.UserResponse)
async def update_user_role(
    user_id: int,
    role: schemas.AdminRoleUpdate,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    target = await db.get(models.User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    requested_value = 1 if role.is_admin else 0
    if target.id == admin.id and requested_value == 0:
        raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
    if int(target.is_admin or 0) == requested_value:
        return target

    if requested_value == 0:
        result = await db.execute(
            select(func.count())
            .select_from(models.User)
            .where(models.User.is_admin == 1)
        )
        if int(result.scalar() or 0) <= 1:
            raise HTTPException(
                status_code=400,
                detail="系统必须至少保留一名管理员",
            )

    target.is_admin = requested_value
    await db.commit()
    await db.refresh(target)
    logger.warning(
        "Administrator %s changed user %s role to is_admin=%s",
        admin.id,
        target.id,
        requested_value,
    )
    return target


@router.get("/logs/login", response_model=schemas.PaginatedLoginLogs)
async def list_login_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    offset = (page - 1) * page_size
    total_result = await db.execute(
        select(func.count()).select_from(models.LoginLog)
    )
    result = await db.execute(
        select(models.LoginLog, models.User.username)
        .join(models.User, models.LoginLog.user_id == models.User.id)
        .order_by(models.LoginLog.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    )
    return {
        "total": int(total_result.scalar() or 0),
        "items": [
            serialize_login_log(log, username or "")
            for log, username in result
        ],
    }


@router.get("/logs/ai", response_model=schemas.PaginatedAILogs)
async def list_ai_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user_id: Optional[int] = Query(default=None, ge=1),
    action: Optional[str] = Query(default=None, max_length=100),
    log_status: Optional[str] = Query(default=None, max_length=50),
    keyword: Optional[str] = Query(default=None, max_length=200),
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    offset = (page - 1) * page_size
    count_query = select(func.count()).select_from(models.AIInteractionLog)
    data_query = (
        select(models.AIInteractionLog, models.User.username)
        .join(models.User, models.AIInteractionLog.user_id == models.User.id)
    )

    if user_id is not None:
        count_query = count_query.where(
            models.AIInteractionLog.user_id == user_id
        )
        data_query = data_query.where(
            models.AIInteractionLog.user_id == user_id
        )
    if action and (action_text := action.strip()):
        count_query = count_query.where(
            models.AIInteractionLog.action == action_text
        )
        data_query = data_query.where(
            models.AIInteractionLog.action == action_text
        )
    if log_status and (status_text := log_status.strip()):
        count_query = count_query.where(
            models.AIInteractionLog.status == status_text
        )
        data_query = data_query.where(
            models.AIInteractionLog.status == status_text
        )
    if keyword and (keyword_text := keyword.strip()):
        pattern = f"%{keyword_text}%"
        condition = (
            models.AIInteractionLog.prompt.like(pattern)
            | models.AIInteractionLog.response.like(pattern)
            | models.AIInteractionLog.error_message.like(pattern)
            | models.AIInteractionLog.action.like(pattern)
        )
        count_query = count_query.where(condition)
        data_query = data_query.where(condition)

    try:
        total_result = await db.execute(count_query)
        result = await db.execute(
            data_query
            .order_by(
                models.AIInteractionLog.timestamp.desc(),
                models.AIInteractionLog.id.desc(),
            )
            .offset(offset)
            .limit(page_size)
        )
    except OperationalError as exc:
        raise schema_query_error(exc)

    return {
        "total": int(total_result.scalar() or 0),
        "items": [
            serialize_ai_log(log, username or "")
            for log, username in result
        ],
    }


@router.get("/logs/ai/users")
async def list_ai_log_users(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    result = await db.execute(
        select(
            models.User.id,
            models.User.username,
            func.count(models.AIInteractionLog.id).label("log_count"),
            func.max(models.AIInteractionLog.timestamp).label("last_log_at"),
        )
        .join(
            models.AIInteractionLog,
            models.AIInteractionLog.user_id == models.User.id,
        )
        .group_by(models.User.id, models.User.username)
        .order_by(
            func.count(models.AIInteractionLog.id).desc(),
            models.User.id.asc(),
        )
    )
    return {
        "items": [
            {
                "user_id": int(user_id),
                "username": str(username or ""),
                "log_count": int(log_count or 0),
                "last_log_at": str(last_log_at or ""),
            }
            for user_id, username, log_count, last_log_at in result
        ]
    }


@router.get(
    "/logs/ai/{log_id}",
    response_model=schemas.AIInteractionLogResponse,
)
async def get_ai_log_detail(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    try:
        result = await db.execute(
            select(models.AIInteractionLog, models.User.username)
            .join(models.User, models.AIInteractionLog.user_id == models.User.id)
            .where(models.AIInteractionLog.id == log_id)
            .limit(1)
        )
    except OperationalError as exc:
        raise schema_query_error(exc)

    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="AI日志不存在")
    log, username = row
    return serialize_ai_log(log, username or "")


@router.get("/export/all")
async def export_all_data(
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    users = (
        await db.execute(select(models.User).order_by(models.User.id.asc()))
    ).scalars().all()
    owner_lookup = {user.id: user.username for user in users}
    projects = (
        await db.execute(
            select(models.Project)
            .options(selectinload(models.Project.scenes))
            .order_by(models.Project.id.asc())
        )
    ).scalars().all()
    login_logs = (
        await db.execute(
            select(models.LoginLog).order_by(
                models.LoginLog.timestamp.desc(),
                models.LoginLog.id.desc(),
            )
        )
    ).scalars().all()
    ai_logs = (
        await db.execute(
            select(models.AIInteractionLog).order_by(
                models.AIInteractionLog.timestamp.desc(),
                models.AIInteractionLog.id.desc(),
            )
        )
    ).scalars().all()

    export_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"luminascript_admin_export_{export_time}.zip"
    database_paths = iter_export_database_paths()
    manifest = {
        "exported_at": datetime.now().isoformat(),
        "exported_by": admin.username,
        "counts": {
            "users": len(users),
            "projects": len(projects),
            "login_logs": len(login_logs),
            "ai_logs": len(ai_logs),
        },
        "database_files": [path.name for path in database_paths],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "users.json",
            json.dumps(
                [
                    {
                        "id": user.id,
                        "username": user.username,
                        "is_admin": bool(user.is_admin),
                    }
                    for user in users
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "projects.json",
            json.dumps(
                [
                    serialize_project(project, owner_lookup)
                    for project in projects
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "login_logs.json",
            json.dumps(
                [
                    serialize_login_log(
                        log,
                        owner_lookup.get(log.user_id, ""),
                    )
                    for log in login_logs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "ai_logs.json",
            json.dumps(
                [
                    serialize_ai_log(
                        log,
                        owner_lookup.get(log.user_id, ""),
                    )
                    for log in ai_logs
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        for database_path in database_paths:
            archive.write(
                database_path,
                arcname=f"database/{database_path.name}",
            )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(archive_name)}"
            )
        },
    )
