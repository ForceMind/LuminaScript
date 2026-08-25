from datetime import datetime
import io
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Optional
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import database
import models
import schemas
from api.dependencies import require_admin
from database import get_db
from services.admin_imports import (
    ADMIN_EXPORT_FORMAT,
    MAX_ADMIN_IMPORT_BYTES,
    import_admin_export,
    parse_admin_export,
)
from services.llm_config import (
    LLMProfileStore,
    LLMRuntimeConfig,
    connection_error_details,
    get_llm_profile_store,
    get_runtime_llm_config,
    list_llm_models,
    public_llm_config,
    safe_connection_error,
    save_runtime_llm_config,
    save_llm_profile_store,
    test_llm_concurrency,
    test_llm_connection,
)


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
    sqlite_prefix = "sqlite+aiosqlite:///"
    if database.DATABASE_URL.startswith(sqlite_prefix):
        configured_path = Path(
            database.DATABASE_URL[len(sqlite_prefix):].split("?", 1)[0]
        ).resolve()
        if configured_path.exists():
            return [configured_path]

    backend_dir = Path(__file__).resolve().parents[1]
    project_dir = backend_dir.parent
    for candidate in (
        backend_dir / "lumina_v2.db",
        backend_dir / "lumina.db",
        project_dir / "lumina_v2.db",
        project_dir / "lumina.db",
    ):
        resolved = candidate.resolve()
        if resolved.exists():
            return [resolved]
    return []


def create_sqlite_snapshot(database_path: Path) -> bytes:
    temporary = tempfile.NamedTemporaryFile(
        prefix="luminascript_export_",
        suffix=".db",
        delete=False,
    )
    snapshot_path = Path(temporary.name)
    temporary.close()
    source = None
    destination = None
    try:
        source = sqlite3.connect(database_path)
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


def schema_query_error(exc: OperationalError) -> HTTPException:
    logger.exception("管理员日志查询遇到数据库结构错误: %s", exc)
    return HTTPException(
        status_code=500,
        detail="数据库结构异常，请先执行迁移命令后重试。",
    )


def build_llm_config_update(
    payload: schemas.AIConfigUpdate,
    current: LLMRuntimeConfig | None = None,
) -> LLMRuntimeConfig:
    current = current or get_runtime_llm_config()
    submitted_key = (payload.api_key or "").strip()
    if payload.clear_api_key:
        api_key = None
    elif submitted_key:
        api_key = submitted_key
    else:
        api_key = current.api_key

    return LLMRuntimeConfig(
        api_key=api_key,
        base_url=payload.base_url,
        model_id=payload.model_id,
        timeout_seconds=payload.timeout_seconds,
        max_concurrency=payload.max_concurrency,
        api_protocol=payload.api_protocol,
        stream_response=payload.stream_response,
        updated_at=current.updated_at,
        updated_by=current.updated_by,
        source=current.source,
        profile_id=current.profile_id,
        profile_name=current.profile_name,
        enabled=current.enabled,
        priority=current.priority,
    )


def ai_config_error_response(
    exc: Exception,
    *,
    api_key: Optional[str],
    operation: str,
) -> JSONResponse:
    status_code, code, message = connection_error_details(exc, api_key)
    logger.warning(
        "AI %s failed: status=%s code=%s exception=%s summary=%s",
        operation,
        status_code,
        code,
        exc.__class__.__name__,
        safe_connection_error(exc, api_key),
    )
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def ai_config_validation_error(
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def public_profile_store(store: LLMProfileStore) -> dict[str, Any]:
    return {
        "active_profile": store.active_profile,
        "routes": store.routes,
        "profiles": [public_llm_config(profile) for profile in store.profiles],
    }


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


@router.get("/ai-config", response_model=schemas.AIConfigResponse)
async def get_ai_config(
    _admin: models.User = Depends(require_admin),
):
    return public_llm_config(get_runtime_llm_config())


@router.put("/ai-config", response_model=schemas.AIConfigResponse)
async def update_ai_config(
    payload: schemas.AIConfigUpdate,
    admin: models.User = Depends(require_admin),
):
    candidate = build_llm_config_update(payload)
    saved = save_runtime_llm_config(candidate, updated_by=admin.username)
    logger.warning(
        "Administrator %s updated AI config: base_url=%s model_id=%s "
        "timeout=%s concurrency=%s key_configured=%s",
        admin.id,
        saved.base_url,
        saved.model_id,
        saved.timeout_seconds,
        saved.max_concurrency,
        bool(saved.api_key),
    )
    return public_llm_config(saved)


@router.post("/ai-config/test", response_model=schemas.AIConfigTestResponse)
async def test_ai_config(
    payload: schemas.AIConfigTestRequest,
    _admin: models.User = Depends(require_admin),
):
    current = get_runtime_llm_config()
    if payload.profile_id:
        stored_profile = next(
            (
                profile
                for profile in get_llm_profile_store().profiles
                if profile.profile_id == payload.profile_id
            ),
            None,
        )
        current = stored_profile or current.model_copy(
            update={"api_key": None, "profile_id": payload.profile_id}
        )
    candidate = build_llm_config_update(payload, current=current)
    try:
        preview = await test_llm_connection(candidate)
    except Exception as exc:
        return ai_config_error_response(
            exc,
            api_key=candidate.api_key,
            operation="connection test",
        )

    if candidate.max_concurrency == 1:
        return {
            "success": True,
            "message": "连接测试成功",
            "response_preview": preview,
            "concurrency_requested": 1,
            "concurrency_succeeded": 1,
            "concurrency_failed": 0,
            "concurrency_supported": True,
            "recommended_max_concurrency": 1,
            "error_messages": [],
        }

    try:
        concurrency_result = await test_llm_concurrency(
            candidate,
            candidate.max_concurrency,
        )
    except Exception as exc:
        safe_error = safe_connection_error(exc, candidate.api_key)
        logger.warning(
            "AI configuration concurrency test failed: %s",
            exc.__class__.__name__,
        )
        return {
            "success": False,
            "message": "单请求成功，但并发能力测试执行失败",
            "response_preview": preview,
            "concurrency_requested": candidate.max_concurrency,
            "concurrency_succeeded": 0,
            "concurrency_failed": candidate.max_concurrency,
            "concurrency_supported": False,
            "recommended_max_concurrency": 1,
            "error_messages": [safe_error],
        }
    safe_errors = [
        safe_connection_error(RuntimeError(message), candidate.api_key)
        for message in concurrency_result["errors"]
    ]
    supported = bool(concurrency_result["supported"])
    return {
        "success": supported,
        "message": (
            f"连接与 {candidate.max_concurrency} 路并发测试均成功"
            if supported
            else (
                f"单请求成功，但 {candidate.max_concurrency} 路并发仅成功 "
                f"{concurrency_result['succeeded']} 路"
            )
        ),
        "response_preview": preview or concurrency_result["response_preview"],
        "concurrency_requested": concurrency_result["requested"],
        "concurrency_succeeded": concurrency_result["succeeded"],
        "concurrency_failed": concurrency_result["failed"],
        "concurrency_supported": supported,
        "recommended_max_concurrency": concurrency_result["recommended_max_concurrency"],
        "error_messages": safe_errors,
    }


@router.post("/ai-config/models", response_model=schemas.AIModelListResponse)
async def get_ai_models(
    payload: schemas.AIModelListRequest,
    _admin: models.User = Depends(require_admin),
):
    submitted_key = (payload.api_key or "").strip()
    if not submitted_key and not payload.profile_id:
        return ai_config_validation_error(
            status_code=400,
            code="api_key_or_profile_required",
            message="api_key 和 profile_id 至少提供一个",
        )

    stored_key = None
    if not submitted_key and payload.profile_id:
        store = get_llm_profile_store()
        profile = next(
            (item for item in store.profiles if item.profile_id == payload.profile_id),
            None,
        )
        if profile is None:
            return ai_config_validation_error(
                status_code=422,
                code="profile_not_found",
                message="指定的 AI 配置档案不存在",
            )
        stored_key = profile.api_key
    api_key = submitted_key or stored_key
    if not api_key:
        return ai_config_validation_error(
            status_code=400,
            code="api_key_required",
            message="请先填写或保存 API Key",
        )

    try:
        model_ids = await list_llm_models(
            base_url=payload.base_url,
            api_key=api_key,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        return ai_config_error_response(
            exc,
            api_key=api_key,
            operation="model list request",
        )
    if not model_ids:
        return ai_config_validation_error(
            status_code=502,
            code="upstream_empty_response",
            message="模型接口返回成功，但列表为空",
        )
    return {"models": model_ids}


@router.get("/ai-profiles")
async def list_ai_profiles(
    _admin: models.User = Depends(require_admin),
):
    return public_profile_store(get_llm_profile_store())


@router.put("/ai-profiles/{profile_id}")
async def upsert_ai_profile(
    profile_id: str,
    payload: schemas.AIProfileUpdate,
    admin: models.User = Depends(require_admin),
):
    normalized_id = profile_id.strip()
    if not normalized_id or len(normalized_id) > 64 or not normalized_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="配置档案 ID 只能包含字母、数字、横线和下划线")
    store = get_llm_profile_store()
    existing = next((item for item in store.profiles if item.profile_id == normalized_id), None)
    submitted_key = (payload.api_key or "").strip()
    api_key = None if payload.clear_api_key else (submitted_key or (existing.api_key if existing else None))
    profile = LLMRuntimeConfig(
        api_key=api_key,
        base_url=payload.base_url,
        model_id=payload.model_id,
        timeout_seconds=payload.timeout_seconds,
        max_concurrency=payload.max_concurrency,
        api_protocol=payload.api_protocol,
        stream_response=payload.stream_response,
        updated_at=datetime.now().astimezone().isoformat(),
        updated_by=admin.username,
        source="admin",
        profile_id=normalized_id,
        profile_name=payload.name,
        enabled=payload.enabled,
        priority=payload.priority,
    )
    if existing:
        store.profiles[store.profiles.index(existing)] = profile
    else:
        store.profiles.append(profile)
    if len(store.profiles) == 1:
        store.active_profile = normalized_id
    save_llm_profile_store(store)
    return public_profile_store(store)


@router.delete("/ai-profiles/{profile_id}")
async def delete_ai_profile(
    profile_id: str,
    _admin: models.User = Depends(require_admin),
):
    store = get_llm_profile_store()
    remaining = [item for item in store.profiles if item.profile_id != profile_id]
    if len(remaining) == len(store.profiles):
        raise HTTPException(status_code=404, detail="AI 配置档案不存在")
    if not remaining:
        raise HTTPException(status_code=400, detail="至少保留一个 AI 配置档案")
    store.profiles = remaining
    if store.active_profile == profile_id:
        store.active_profile = remaining[0].profile_id
    store.routes = {
        task: [item for item in profile_ids if item != profile_id]
        for task, profile_ids in store.routes.items()
    }
    save_llm_profile_store(store)
    return public_profile_store(store)


@router.put("/ai-routing")
async def update_ai_routing(
    payload: schemas.AIRoutingUpdate,
    _admin: models.User = Depends(require_admin),
):
    store = get_llm_profile_store()
    profile_ids = {profile.profile_id for profile in store.profiles}
    if payload.active_profile not in profile_ids:
        raise HTTPException(status_code=422, detail="默认 AI 配置档案不存在")
    for route_profiles in payload.routes.values():
        if any(profile_id not in profile_ids for profile_id in route_profiles):
            raise HTTPException(status_code=422, detail="路由中包含不存在的 AI 配置档案")
    store.active_profile = payload.active_profile
    store.routes = payload.routes
    save_llm_profile_store(store)
    return public_profile_store(store)


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
        "format": ADMIN_EXPORT_FORMAT,
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
                        "hashed_password": user.hashed_password,
                        "daily_token_limit": int(user.daily_token_limit or 0),
                        "monthly_token_limit": int(user.monthly_token_limit or 0),
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
            archive.writestr(
                f"database/{database_path.name}",
                create_sqlite_snapshot(database_path),
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


@router.post("/import/all")
async def import_all_data(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    filename = str(file.filename or "").lower()
    if filename and not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="请选择后台导出的 ZIP 文件")
    raw = await file.read(MAX_ADMIN_IMPORT_BYTES + 1)
    try:
        payload = parse_admin_export(raw)
        result = await import_admin_export(
            db,
            payload,
            importing_admin=admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Administrator %s failed to import admin archive: %s",
            admin.id,
            exc.__class__.__name__,
        )
        raise HTTPException(status_code=500, detail="后台数据导入失败，所有更改已回滚") from exc

    logger.warning(
        "Administrator %s imported admin archive: users=%s projects=%s scenes=%s",
        admin.id,
        result["created_users"],
        result["created_projects"],
        result["created_scenes"],
    )
    return result
