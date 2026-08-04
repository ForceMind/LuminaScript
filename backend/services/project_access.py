from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import models


ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


async def project_role(
    db: AsyncSession,
    project: models.Project,
    user_id: int,
) -> str | None:
    if int(project.owner_id or 0) == int(user_id):
        return "owner"
    result = await db.execute(
        select(models.ProjectMember.role)
        .where(models.ProjectMember.project_id == project.id)
        .where(models.ProjectMember.user_id == user_id)
        .limit(1)
    )
    role = result.scalar_one_or_none()
    return str(role) if role in ROLE_RANK else None


async def require_project_access(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    *,
    minimum_role: str = "viewer",
    load_scenes: bool = False,
) -> tuple[models.Project, str]:
    query = select(models.Project).where(models.Project.id == project_id)
    if load_scenes:
        from sqlalchemy.orm import selectinload

        query = query.options(selectinload(models.Project.scenes))
    project = (await db.execute(query)).scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    role = await project_role(db, project, user_id)
    if not role or ROLE_RANK[role] < ROLE_RANK[minimum_role]:
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")
    return project, role


def accessible_project_condition(user_id: int):
    membership = (
        select(models.ProjectMember.id)
        .where(models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == user_id)
        .exists()
    )
    return or_(models.Project.owner_id == user_id, membership)
