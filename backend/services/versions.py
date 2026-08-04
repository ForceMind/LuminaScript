from __future__ import annotations

from datetime import datetime, timezone
import difflib
import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_project_snapshot(project: models.Project) -> dict[str, Any]:
    return {
        "title": project.title,
        "logline": project.logline,
        "project_type": project.project_type,
        "genre": project.genre,
        "status": str(getattr(project.status, "value", project.status)),
        "total_tokens": int(project.total_tokens or 0),
        "global_context": project.global_context or {},
        "global_summary": project.global_summary,
        "scenes": [
            {
                "scene_index": scene.scene_index,
                "outline": scene.outline,
                "content": scene.content,
                "summary": scene.summary,
                "status": str(getattr(scene.status, "value", scene.status)),
            }
            for scene in sorted(project.scenes or [], key=lambda item: item.scene_index)
        ],
    }


async def create_project_version(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    label: str,
) -> models.ProjectVersion:
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(selectinload(models.Project.scenes))
    )
    project = result.scalars().first()
    if not project:
        raise ValueError("Project not found")
    version = models.ProjectVersion(
        project_id=project_id,
        created_by=user_id,
        label=(label or "手动快照")[:200],
        snapshot=serialize_project_snapshot(project),
        created_at=utc_iso(),
    )
    db.add(version)
    await db.flush()
    return version


async def restore_project_version(
    db: AsyncSession,
    project: models.Project,
    version: models.ProjectVersion,
) -> None:
    snapshot = dict(version.snapshot or {})
    project.title = snapshot.get("title") or project.title
    project.logline = snapshot.get("logline") or project.logline
    project.project_type = snapshot.get("project_type") or project.project_type
    project.genre = snapshot.get("genre")
    project.global_context = snapshot.get("global_context") or {}
    project.global_summary = snapshot.get("global_summary")
    project.total_tokens = int(snapshot.get("total_tokens") or 0)
    project.status = models.ProcessingStatus.PENDING
    await db.execute(delete(models.Scene).where(models.Scene.project_id == project.id))
    for item in snapshot.get("scenes") or []:
        status_value = item.get("status") or "pending"
        try:
            status = models.ProcessingStatus(status_value)
        except ValueError:
            status = models.ProcessingStatus.PENDING
        db.add(
            models.Scene(
                project_id=project.id,
                scene_index=int(item.get("scene_index") or 1),
                outline=str(item.get("outline") or ""),
                content=item.get("content"),
                summary=item.get("summary"),
                status=status,
            )
        )


def diff_version_snapshots(
    older: dict[str, Any],
    newer: dict[str, Any],
) -> str:
    before = json.dumps(older or {}, ensure_ascii=False, indent=2).splitlines()
    after = json.dumps(newer or {}, ensure_ascii=False, indent=2).splitlines()
    return "\n".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="旧版本",
            tofile="新版本",
            lineterm="",
        )
    )[:200000]
