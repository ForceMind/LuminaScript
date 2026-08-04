from __future__ import annotations

import asyncio
import io
import json
import secrets
from typing import Any
import zipfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models


ADMIN_EXPORT_FORMAT = "luminascript-admin-export-v1"
MAX_ADMIN_IMPORT_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_USERS = 2_000
MAX_PROJECTS = 20_000
MAX_LOGS_PER_TYPE = 500_000
REQUIRED_FILES = {"manifest.json", "users.json", "projects.json"}


def _read_json_file(archive: zipfile.ZipFile, name: str, *, default: Any) -> Any:
    if name not in archive.namelist():
        return default
    info = archive.getinfo(name)
    if info.file_size > MAX_UNCOMPRESSED_BYTES:
        raise ValueError(f"{name} 过大")
    try:
        return json.loads(archive.read(info).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} 不是有效的 JSON 文件") from exc


def parse_admin_export(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ValueError("导入文件为空")
    if len(payload) > MAX_ADMIN_IMPORT_BYTES:
        raise ValueError("后台导入文件不能超过 100 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("请选择后台导出的 ZIP 文件") from exc

    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("ZIP 中包含重复文件名")
        missing = REQUIRED_FILES.difference(names)
        if missing:
            raise ValueError(f"导出包缺少文件：{', '.join(sorted(missing))}")
        total_size = sum(max(0, int(info.file_size)) for info in archive.infolist())
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP 解压后的数据超过 500 MB")

        manifest = _read_json_file(archive, "manifest.json", default={})
        users = _read_json_file(archive, "users.json", default=[])
        projects = _read_json_file(archive, "projects.json", default=[])
        login_logs = _read_json_file(archive, "login_logs.json", default=[])
        ai_logs = _read_json_file(archive, "ai_logs.json", default=[])

    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 格式无效")
    archive_format = str(manifest.get("format") or "").strip()
    if archive_format and archive_format != ADMIN_EXPORT_FORMAT:
        raise ValueError("该 ZIP 不是受支持的 LuminaScript 后台导出包")
    collections = {
        "users": (users, MAX_USERS),
        "projects": (projects, MAX_PROJECTS),
        "login_logs": (login_logs, MAX_LOGS_PER_TYPE),
        "ai_logs": (ai_logs, MAX_LOGS_PER_TYPE),
    }
    for name, (items, maximum) in collections.items():
        if not isinstance(items, list):
            raise ValueError(f"{name}.json 必须是数组")
        if len(items) > maximum:
            raise ValueError(f"{name}.json 数据量超过限制")
        if any(not isinstance(item, dict) for item in items):
            raise ValueError(f"{name}.json 包含无效记录")

    return {
        "manifest": manifest,
        "users": users,
        "projects": projects,
        "login_logs": login_logs,
        "ai_logs": ai_logs,
    }


def _integer(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = 2_000_000_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _text(value: Any, default: str = "", *, maximum: int = 2_000_000) -> str:
    if value is None:
        return default
    return str(value)[:maximum]


def _processing_status(value: Any, *, generating_fallback: models.ProcessingStatus) -> models.ProcessingStatus:
    normalized = _text(value).strip().lower().split(".")[-1]
    try:
        status = models.ProcessingStatus(normalized)
    except ValueError:
        return models.ProcessingStatus.PENDING
    if status == models.ProcessingStatus.GENERATING:
        return generating_fallback
    return status


def _validate_username(value: Any) -> str:
    username = _text(value, maximum=64).strip()
    if len(username) < 3 or any(character.isspace() or ord(character) < 32 for character in username):
        raise ValueError(f"导出包包含无效用户名：{username or '(空)'}")
    return username


def _valid_password_hash(value: Any) -> str:
    password_hash = _text(value, maximum=512).strip()
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return password_hash
    return ""


async def import_admin_export(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    importing_admin: models.User,
) -> dict[str, Any]:
    existing_users = (await db.execute(select(models.User))).scalars().all()
    users_by_username = {user.username: user for user in existing_users}
    source_user_ids: dict[int, models.User] = {}
    temporary_passwords: list[dict[str, str]] = []
    created_users = 0
    matched_users = 0

    try:
        for item in payload["users"]:
            username = _validate_username(item.get("username"))
            user = users_by_username.get(username)
            if user:
                matched_users += 1
            else:
                password_hash = _valid_password_hash(item.get("hashed_password"))
                if not password_hash:
                    temporary_password = f"Ls!{secrets.token_urlsafe(12)}"
                    password_hash = await asyncio.to_thread(
                        auth.get_password_hash,
                        temporary_password,
                    )
                    temporary_passwords.append(
                        {"username": username, "password": temporary_password}
                    )
                user = models.User(
                    username=username,
                    hashed_password=password_hash,
                    is_admin=1 if bool(item.get("is_admin")) else 0,
                    daily_token_limit=_integer(item.get("daily_token_limit")),
                    monthly_token_limit=_integer(item.get("monthly_token_limit")),
                )
                db.add(user)
                await db.flush()
                users_by_username[username] = user
                created_users += 1
            source_id = _integer(item.get("id"), minimum=1)
            if source_id:
                source_user_ids[source_id] = user

        source_project_ids: dict[int, models.Project] = {}
        created_projects = 0
        created_scenes = 0
        allowed_project_types = {"movie", "tv", "short", "short_video", "pending"}
        for item in payload["projects"]:
            owner = source_user_ids.get(_integer(item.get("owner_id"), minimum=1))
            if not owner:
                owner = users_by_username.get(_text(item.get("owner_username"), maximum=64))
            owner = owner or importing_admin

            raw_title = _text(item.get("title"), "导入项目", maximum=200).strip() or "导入项目"
            suffix = "（后台导入）"
            title = f"{raw_title[: max(1, 200 - len(suffix))]}{suffix}"
            project_type = _text(item.get("project_type"), "movie", maximum=30)
            if project_type not in allowed_project_types:
                project_type = "movie"
            scenes = item.get("scenes") or []
            if not isinstance(scenes, list) or len(scenes) > 1000:
                raise ValueError(f"项目“{raw_title}”的场次数据无效")
            indexes: set[int] = set()
            for scene_item in scenes:
                if not isinstance(scene_item, dict):
                    raise ValueError(f"项目“{raw_title}”包含无效场次")
                scene_index = _integer(scene_item.get("scene_index"), minimum=1, maximum=1000)
                if not scene_index or scene_index in indexes:
                    raise ValueError(f"项目“{raw_title}”包含无效或重复的场次序号")
                indexes.add(scene_index)

            context = item.get("global_context")
            project = models.Project(
                owner_id=owner.id,
                title=title,
                logline=_text(item.get("logline"), "后台导入的项目", maximum=20_000) or "后台导入的项目",
                project_type=project_type,
                genre=_text(item.get("genre"), maximum=100_000) or None,
                global_context=context if isinstance(context, dict) else {},
                global_summary=_text(item.get("global_summary"), maximum=500_000) or None,
                total_tokens=_integer(item.get("total_tokens")),
                status=_processing_status(
                    item.get("status"),
                    generating_fallback=models.ProcessingStatus.FAILED,
                ),
            )
            db.add(project)
            await db.flush()
            source_project_id = _integer(item.get("id"), minimum=1)
            if source_project_id:
                source_project_ids[source_project_id] = project
            for scene_item in scenes:
                db.add(
                    models.Scene(
                        project_id=project.id,
                        scene_index=_integer(scene_item.get("scene_index"), minimum=1, maximum=1000),
                        outline=_text(scene_item.get("outline"), "后台导入场次", maximum=50_000) or "后台导入场次",
                        content=_text(scene_item.get("content"), maximum=2_000_000) or None,
                        summary=_text(scene_item.get("summary"), maximum=200_000) or None,
                        status=_processing_status(
                            scene_item.get("status"),
                            generating_fallback=models.ProcessingStatus.PENDING,
                        ),
                    )
                )
                created_scenes += 1
            created_projects += 1

        await db.flush()
        created_login_logs = 0
        for item in payload["login_logs"]:
            user = source_user_ids.get(_integer(item.get("user_id"), minimum=1))
            if not user:
                user = users_by_username.get(_text(item.get("user_name"), maximum=64))
            user = user or importing_admin
            db.add(
                models.LoginLog(
                    user_id=user.id,
                    ip_address=_text(item.get("ip_address"), maximum=255),
                    user_agent=_text(item.get("user_agent"), maximum=2_000) or None,
                    location=_text(item.get("location"), maximum=500) or None,
                    status=_text(item.get("status"), "unknown", maximum=50),
                    timestamp=_text(item.get("timestamp"), maximum=100),
                )
            )
            created_login_logs += 1

        created_ai_logs = 0
        for item in payload["ai_logs"]:
            user = source_user_ids.get(_integer(item.get("user_id"), minimum=1)) or importing_admin
            source_project_id = _integer(item.get("project_id"), minimum=1)
            imported_project = source_project_ids.get(source_project_id) if source_project_id else None
            db.add(
                models.AIInteractionLog(
                    user_id=user.id,
                    project_id=imported_project.id if imported_project else None,
                    action=_text(item.get("action"), maximum=255),
                    prompt=_text(item.get("prompt")),
                    response=_text(item.get("response")),
                    tokens=_integer(item.get("tokens")),
                    status=_text(item.get("status"), "success", maximum=50) or "success",
                    step_key=_text(item.get("step_key"), maximum=255) or None,
                    error_type=_text(item.get("error_type"), maximum=255) or None,
                    error_message=_text(item.get("error_message")) or None,
                    attempt=_integer(item.get("attempt"), default=1, minimum=1, maximum=100),
                    timestamp=_text(item.get("timestamp"), maximum=100),
                )
            )
            created_ai_logs += 1

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "created_users": created_users,
        "matched_users": matched_users,
        "created_projects": created_projects,
        "created_scenes": created_scenes,
        "created_login_logs": created_login_logs,
        "created_ai_logs": created_ai_logs,
        "temporary_passwords": temporary_passwords,
    }
