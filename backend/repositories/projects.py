from typing import Any

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models


async def claim_generation(
    db: AsyncSession,
    project_id: int,
    actor_id: int,
) -> bool:
    editor_membership = exists(
        select(models.ProjectMember.id)
        .where(models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == actor_id)
        .where(models.ProjectMember.role == "editor")
    )
    result = await db.execute(
        update(models.Project)
        .where(models.Project.id == project_id)
        .where(or_(models.Project.owner_id == actor_id, editor_membership))
        .where(models.Project.status != models.ProcessingStatus.GENERATING)
        .values(status=models.ProcessingStatus.GENERATING)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0) == 1


async def increment_tokens(
    db: AsyncSession,
    project: models.Project,
    usage: Any,
) -> None:
    token_delta = max(0, int(usage or 0))
    if token_delta <= 0:
        return

    await db.flush()
    await db.execute(
        update(models.Project)
        .where(models.Project.id == project.id)
        .values(
            total_tokens=func.coalesce(models.Project.total_tokens, 0) + token_delta
        )
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    await db.refresh(project, attribute_names=["total_tokens"])


async def mark_claimed_failed(db: AsyncSession, project_id: int) -> None:
    await db.rollback()
    await db.execute(
        update(models.Project)
        .where(models.Project.id == project_id)
        .where(models.Project.status == models.ProcessingStatus.GENERATING)
        .values(status=models.ProcessingStatus.FAILED)
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def recover_interrupted(db: AsyncSession) -> tuple[int, int]:
    active_scene_job = exists(
        select(models.GenerationJob.id)
        .where(models.GenerationJob.project_id == models.Scene.project_id)
        .where(
            models.GenerationJob.status.in_(
                [models.JobStatus.QUEUED, models.JobStatus.RUNNING]
            )
        )
    )
    scene_result = await db.execute(
        update(models.Scene)
        .where(models.Scene.status == models.ProcessingStatus.GENERATING)
        .where(~active_scene_job)
        .values(status=models.ProcessingStatus.FAILED)
        .execution_options(synchronize_session=False)
    )

    active_project_job = exists(
        select(models.GenerationJob.id)
        .where(models.GenerationJob.project_id == models.Project.id)
        .where(
            models.GenerationJob.status.in_(
                [models.JobStatus.QUEUED, models.JobStatus.RUNNING]
            )
        )
    )
    project_result = await db.execute(
        update(models.Project)
        .where(models.Project.status == models.ProcessingStatus.GENERATING)
        .where(~active_project_job)
        .values(status=models.ProcessingStatus.FAILED)
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    return (
        max(0, int(scene_result.rowcount or 0)),
        max(0, int(project_result.rowcount or 0)),
    )
