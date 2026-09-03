from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import auth
import models
from api.dependencies import require_admin
from database import get_db
from repositories.projects import claim_generation
from services.backups import (
    BackupSettings,
    backup_path,
    create_backup,
    load_backup_settings,
    restore_projects_as_copies,
    save_backup_settings,
)
from services.job_queue import enqueue_job
from services.generation_state import clear_generation_error
from services.project_access import project_role, require_project_access
from services.prompt_templates import ALLOWED_TEMPLATE_STAGES
from services.system_logs import read_system_log
from services.usage import enforce_user_quota, get_user_usage
from services.setup_state import assert_setup_writable, revision_meta
from services.versions import (
    create_project_version,
    diff_version_snapshots,
    restore_project_version,
    serialize_project_snapshot,
    utc_iso,
)


router = APIRouter(tags=["operations"])
admin_router = APIRouter(prefix="/admin/ops", tags=["admin-operations"])


class VersionCreate(BaseModel):
    label: str = Field(default="手动快照", min_length=1, max_length=200)


class VersionRestore(BaseModel):
    confirm: bool
    context_revision: str | None = Field(default=None, max_length=128)


class MemberCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    role: Literal["viewer", "editor"] = "viewer"


class MemberUpdate(BaseModel):
    role: Literal["viewer", "editor"]


class QuotaUpdate(BaseModel):
    daily_token_limit: int = Field(default=0, ge=0, le=2_000_000_000)
    monthly_token_limit: int = Field(default=0, ge=0, le=2_000_000_000)


class PromptTemplatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    stage: Literal["outline", "content", "review", "interaction", "prompt"]
    project_type: Literal["all", "movie", "tv", "short", "short_video"] = "all"
    content: str = Field(min_length=1, max_length=50000)
    enabled: bool = True


class BackupRestoreRequest(BaseModel):
    confirm: bool


def enum_value(value):
    return str(getattr(value, "value", value))


def serialize_job(job: models.GenerationJob, project: models.Project | None = None) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "project_title": project.title if project else "",
        "kind": job.kind,
        "status": enum_value(job.status),
        "attempts": int(job.attempts or 0),
        "max_attempts": int(job.max_attempts or 0),
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "cancel_requested": bool(job.cancel_requested),
    }


def serialize_version(version: models.ProjectVersion, include_snapshot: bool = False) -> dict:
    payload = {
        "id": version.id,
        "project_id": version.project_id,
        "created_by": version.created_by,
        "label": version.label,
        "created_at": version.created_at,
        "scene_count": len((version.snapshot or {}).get("scenes") or []),
    }
    if include_snapshot:
        payload["snapshot"] = version.snapshot
    return payload


def serialize_template(template: models.PromptTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "stage": template.stage,
        "project_type": template.project_type,
        "content": template.content,
        "enabled": bool(template.enabled),
        "created_by": template.created_by,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


@router.get("/usage/me")
async def my_usage(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    usage = await get_user_usage(db, current_user.id)
    return {
        **usage,
        "daily_limit": int(current_user.daily_token_limit or 0),
        "monthly_limit": int(current_user.monthly_token_limit or 0),
    }


@admin_router.get("/usage")
async def admin_usage(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    users = (await db.execute(select(models.User).order_by(models.User.id))).scalars().all()
    items = []
    for user in users:
        usage = await get_user_usage(db, user.id)
        items.append(
            {
                "user_id": user.id,
                "username": user.username,
                **usage,
                "daily_limit": int(user.daily_token_limit or 0),
                "monthly_limit": int(user.monthly_token_limit or 0),
            }
        )
    return {"items": items}


@admin_router.patch("/users/{user_id}/quota")
async def update_user_quota(
    user_id: int,
    payload: QuotaUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    user = await db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.daily_token_limit = payload.daily_token_limit
    user.monthly_token_limit = payload.monthly_token_limit
    await db.commit()
    return {"status": "updated"}


@router.get("/jobs")
async def list_jobs(
    project_id: Optional[int] = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    query = (
        select(models.GenerationJob, models.Project)
        .join(models.Project, models.GenerationJob.project_id == models.Project.id)
        .order_by(models.GenerationJob.id.desc())
        .limit(100)
    )
    if project_id:
        query = query.where(models.GenerationJob.project_id == project_id)
    rows = (await db.execute(query)).all()
    items = []
    for job, project in rows:
        if await project_role(db, project, current_user.id):
            items.append(serialize_job(job, project))
    return {"items": items}


@admin_router.get("/jobs")
async def list_all_jobs(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    query = (
        select(models.GenerationJob, models.Project)
        .join(models.Project, models.GenerationJob.project_id == models.Project.id)
        .order_by(models.GenerationJob.id.desc())
        .limit(200)
    )
    if status:
        try:
            selected_status = models.JobStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="无效的任务状态") from exc
        query = query.where(models.GenerationJob.status == selected_status)
    rows = (await db.execute(query)).all()
    return {"items": [serialize_job(job, project) for job, project in rows]}


@admin_router.get("/system-logs")
async def get_system_logs(
    source: Literal["backend", "worker", "frontend"] = "worker",
    lines: int = Query(default=300, ge=20, le=2_000),
    keyword: str = Query(default="", max_length=200),
    _admin: models.User = Depends(require_admin),
):
    return await asyncio.to_thread(
        read_system_log,
        source,
        lines=lines,
        keyword=keyword,
    )


@admin_router.post("/jobs/{job_id}/cancel")
async def admin_cancel_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    job = await db.get(models.GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in {models.JobStatus.QUEUED, models.JobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="该任务已经结束")
    project = await db.get(models.Project, job.project_id)
    job.cancel_requested = True
    job.status = models.JobStatus.CANCELED
    job.lock_token = None
    job.locked_at = None
    job.updated_at = utc_iso()
    if project:
        project.status = models.ProcessingStatus.FAILED
    await db.execute(
        update(models.Scene)
        .where(models.Scene.project_id == job.project_id)
        .where(models.Scene.status == models.ProcessingStatus.GENERATING)
        .values(status=models.ProcessingStatus.PENDING)
    )
    await db.commit()
    return {"status": "canceled"}


@admin_router.post("/jobs/{job_id}/retry")
async def admin_retry_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    old_job = await db.get(models.GenerationJob, job_id)
    if not old_job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if old_job.status not in {models.JobStatus.FAILED, models.JobStatus.CANCELED}:
        raise HTTPException(status_code=409, detail="只有失败或已取消任务可以重试")
    project = await db.get(models.Project, old_job.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    await enforce_user_quota(db, project.owner_id)
    if not await claim_generation(db, project.id, project.owner_id):
        await db.rollback()
        raise HTTPException(status_code=409, detail="该项目已有生成任务正在运行")
    await db.refresh(project)
    if old_job.kind == "outline_generation":
        await db.execute(delete(models.Scene).where(models.Scene.project_id == project.id))
    else:
        await db.execute(
            update(models.Scene)
            .where(models.Scene.project_id == project.id)
            .where(models.Scene.status.in_([models.ProcessingStatus.FAILED, models.ProcessingStatus.GENERATING]))
            .values(status=models.ProcessingStatus.PENDING, content=None, summary=None)
        )
    project.status = models.ProcessingStatus.GENERATING
    clear_generation_error(project)
    new_job = await enqueue_job(
        db,
        project_id=project.id,
        kind=old_job.kind,
        payload=dict(old_job.payload or {}),
        max_attempts=old_job.max_attempts,
    )
    await db.commit()
    return {"status": "queued", "job_id": new_job.id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    job = await db.get(models.GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    project, _ = await require_project_access(
        db, job.project_id, current_user.id, minimum_role="editor"
    )
    if job.status not in {models.JobStatus.QUEUED, models.JobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="该任务已经结束")
    job.cancel_requested = True
    job.status = models.JobStatus.CANCELED
    job.lock_token = None
    job.locked_at = None
    job.updated_at = utc_iso()
    project.status = models.ProcessingStatus.FAILED
    await db.execute(
        update(models.Scene)
        .where(models.Scene.project_id == project.id)
        .where(models.Scene.status == models.ProcessingStatus.GENERATING)
        .values(status=models.ProcessingStatus.PENDING)
    )
    await db.commit()
    return {"status": "canceled"}


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    old_job = await db.get(models.GenerationJob, job_id)
    if not old_job:
        raise HTTPException(status_code=404, detail="任务不存在")
    project, _ = await require_project_access(
        db, old_job.project_id, current_user.id, minimum_role="editor"
    )
    if old_job.status not in {models.JobStatus.FAILED, models.JobStatus.CANCELED}:
        raise HTTPException(status_code=409, detail="只有失败或已取消任务可以重试")
    await enforce_user_quota(db, project.owner_id)
    if not await claim_generation(db, project.id, current_user.id):
        await db.rollback()
        raise HTTPException(status_code=409, detail="该项目已有生成任务正在运行")
    await db.refresh(project)
    if old_job.kind == "outline_generation":
        await db.execute(delete(models.Scene).where(models.Scene.project_id == project.id))
    else:
        await db.execute(
            update(models.Scene)
            .where(models.Scene.project_id == project.id)
            .where(models.Scene.status.in_([models.ProcessingStatus.FAILED, models.ProcessingStatus.GENERATING]))
            .values(status=models.ProcessingStatus.PENDING, content=None, summary=None)
        )
    project.status = models.ProcessingStatus.GENERATING
    clear_generation_error(project)
    new_job = await enqueue_job(
        db,
        project_id=project.id,
        kind=old_job.kind,
        payload=dict(old_job.payload or {}),
        max_attempts=old_job.max_attempts,
    )
    await db.commit()
    return {"status": "queued", "job_id": new_job.id}


@router.post("/projects/{project_id}/versions")
async def create_version(
    project_id: int,
    payload: VersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    await require_project_access(db, project_id, current_user.id, minimum_role="editor")
    version = await create_project_version(db, project_id, current_user.id, payload.label)
    await db.commit()
    return serialize_version(version)


@router.get("/projects/{project_id}/versions")
async def list_versions(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    await require_project_access(db, project_id, current_user.id)
    versions = (
        await db.execute(
            select(models.ProjectVersion)
            .where(models.ProjectVersion.project_id == project_id)
            .order_by(models.ProjectVersion.id.desc())
        )
    ).scalars().all()
    return {"items": [serialize_version(item) for item in versions]}


@router.get("/projects/{project_id}/versions/{version_id}/diff")
async def version_diff(
    project_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    project, _ = await require_project_access(
        db, project_id, current_user.id, load_scenes=True
    )
    version = await db.get(models.ProjectVersion, version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"diff": diff_version_snapshots(version.snapshot, serialize_project_snapshot(project))}


@router.post("/projects/{project_id}/versions/{version_id}/restore")
async def restore_version(
    project_id: int,
    version_id: int,
    payload: VersionRestore,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复操作")
    project, _ = await require_project_access(
        db, project_id, current_user.id, minimum_role="editor", load_scenes=True
    )
    version = await db.get(models.ProjectVersion, version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="版本不存在")
    await assert_setup_writable(db, project, payload.context_revision)
    await create_project_version(db, project_id, current_user.id, "恢复前自动快照")
    await restore_project_version(db, project, version, payload.context_revision)
    await db.commit()
    return {"status": "restored", **revision_meta(project)}


@router.get("/projects/{project_id}/members")
async def list_members(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    project, _ = await require_project_access(db, project_id, current_user.id)
    rows = (
        await db.execute(
            select(models.ProjectMember, models.User.username)
            .join(models.User, models.ProjectMember.user_id == models.User.id)
            .where(models.ProjectMember.project_id == project_id)
            .order_by(models.ProjectMember.id)
        )
    ).all()
    return {
        "owner_id": project.owner_id,
        "items": [
            {"id": member.id, "user_id": member.user_id, "username": username, "role": member.role}
            for member, username in rows
        ],
    }


@router.post("/projects/{project_id}/members")
async def add_member(
    project_id: int,
    payload: MemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    project, role = await require_project_access(db, project_id, current_user.id)
    if role != "owner":
        raise HTTPException(status_code=403, detail="只有项目所有者可以管理成员")
    user = (await db.execute(select(models.User).where(models.User.username == payload.username))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == project.owner_id:
        raise HTTPException(status_code=400, detail="项目所有者无需重复添加")
    existing = await db.scalar(
        select(models.ProjectMember)
        .where(models.ProjectMember.project_id == project_id)
        .where(models.ProjectMember.user_id == user.id)
    )
    if existing:
        existing.role = payload.role
    else:
        db.add(
            models.ProjectMember(
                project_id=project_id,
                user_id=user.id,
                role=payload.role,
                created_at=utc_iso(),
            )
        )
    await db.commit()
    return {"status": "saved"}


@router.patch("/projects/{project_id}/members/{member_id}")
async def update_member(
    project_id: int,
    member_id: int,
    payload: MemberUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    _, role = await require_project_access(db, project_id, current_user.id)
    if role != "owner":
        raise HTTPException(status_code=403, detail="只有项目所有者可以管理成员")
    member = await db.get(models.ProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise HTTPException(status_code=404, detail="成员不存在")
    member.role = payload.role
    await db.commit()
    return {"status": "updated"}


@router.delete("/projects/{project_id}/members/{member_id}")
async def remove_member(
    project_id: int,
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    _, role = await require_project_access(db, project_id, current_user.id)
    if role != "owner":
        raise HTTPException(status_code=403, detail="只有项目所有者可以管理成员")
    member = await db.get(models.ProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise HTTPException(status_code=404, detail="成员不存在")
    await db.delete(member)
    await db.commit()
    return {"status": "removed"}


@router.get("/prompt-templates")
async def available_prompt_templates(
    stage: Optional[str] = None,
    project_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    if stage and stage not in ALLOWED_TEMPLATE_STAGES:
        raise HTTPException(status_code=422, detail="无效的模板阶段")
    query = select(models.PromptTemplate).where(models.PromptTemplate.enabled.is_(True))
    if stage:
        query = query.where(models.PromptTemplate.stage == stage)
    if project_type:
        query = query.where(models.PromptTemplate.project_type.in_([project_type, "all"]))
    templates = (await db.execute(query.order_by(models.PromptTemplate.id.desc()))).scalars().all()
    return {"items": [serialize_template(item) for item in templates]}


@admin_router.get("/prompt-templates")
async def list_prompt_templates(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    templates = (
        await db.execute(select(models.PromptTemplate).order_by(models.PromptTemplate.id.desc()))
    ).scalars().all()
    return {"items": [serialize_template(item) for item in templates]}


@admin_router.post("/prompt-templates")
async def create_prompt_template(
    payload: PromptTemplatePayload,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    now = utc_iso()
    template = models.PromptTemplate(
        **payload.model_dump(), created_by=admin.id, created_at=now, updated_at=now
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return serialize_template(template)


@admin_router.put("/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: int,
    payload: PromptTemplatePayload,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    template = await db.get(models.PromptTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    for key, value in payload.model_dump().items():
        setattr(template, key, value)
    template.updated_at = utc_iso()
    await db.commit()
    return serialize_template(template)


@admin_router.delete("/prompt-templates/{template_id}")
async def delete_prompt_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    template = await db.get(models.PromptTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    await db.delete(template)
    await db.commit()
    return {"status": "deleted"}


@admin_router.get("/backup-settings")
async def get_backup_settings(
    _admin: models.User = Depends(require_admin),
):
    return load_backup_settings().model_dump()


@admin_router.put("/backup-settings")
async def update_backup_settings(
    payload: BackupSettings,
    _admin: models.User = Depends(require_admin),
):
    return save_backup_settings(payload).model_dump()


@admin_router.post("/backups")
async def create_manual_backup(
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    record = await create_backup(
        db,
        actor_id=admin.id,
        actor_name=admin.username,
        backup_type="manual",
    )
    return {"id": record.id, "filename": record.filename, "size_bytes": record.size_bytes}


@admin_router.get("/backups")
async def list_backups(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    records = (
        await db.execute(select(models.BackupRecord).order_by(models.BackupRecord.id.desc()))
    ).scalars().all()
    return {
        "items": [
            {
                "id": item.id,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "status": item.status,
                "backup_type": item.backup_type,
                "created_at": item.created_at,
                "notes": item.notes,
            }
            for item in records
        ]
    }


@admin_router.get("/backups/{backup_id}/download")
async def download_backup(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    record = await db.get(models.BackupRecord, backup_id)
    if not record:
        raise HTTPException(status_code=404, detail="备份不存在")
    try:
        path = backup_path(record)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="备份文件已丢失")
    return FileResponse(path, filename=record.filename, media_type="application/octet-stream")


@admin_router.post("/backups/{backup_id}/restore")
async def restore_backup(
    backup_id: int,
    payload: BackupRestoreRequest,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复操作")
    record = await db.get(models.BackupRecord, backup_id)
    if not record:
        raise HTTPException(status_code=404, detail="备份不存在")
    count = await restore_projects_as_copies(db, record, admin.id)
    return {"status": "restored_as_copies", "project_count": count}


@admin_router.get("/alerts")
async def operation_alerts(
    db: AsyncSession = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    counts = {}
    for status in models.JobStatus:
        counts[status.value] = int(
            await db.scalar(
                select(func.count())
                .select_from(models.GenerationJob)
                .where(models.GenerationJob.status == status)
            )
            or 0
        )
    failures = (
        await db.execute(
            select(models.GenerationJob, models.Project)
            .join(models.Project, models.GenerationJob.project_id == models.Project.id)
            .where(models.GenerationJob.status == models.JobStatus.FAILED)
            .order_by(models.GenerationJob.id.desc())
            .limit(10)
        )
    ).all()
    return {
        "counts": counts,
        "recent_failures": [serialize_job(job, project) for job, project in failures],
    }
