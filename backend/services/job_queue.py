from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models


OUTLINE_JOB = "outline_generation"
CONTENT_JOB = "content_generation"
SUPPORTED_JOB_KINDS = {OUTLINE_JOB, CONTENT_JOB}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    return value.isoformat()


async def enqueue_job(
    db: AsyncSession,
    *,
    project_id: int,
    kind: str,
    payload: dict[str, Any],
    max_attempts: int = 3,
) -> models.GenerationJob:
    if kind not in SUPPORTED_JOB_KINDS:
        raise ValueError(f"Unsupported generation job kind: {kind}")

    now = to_iso(utc_now())
    job = models.GenerationJob(
        project_id=project_id,
        kind=kind,
        payload=dict(payload or {}),
        status=models.JobStatus.QUEUED,
        attempts=0,
        max_attempts=max(1, min(int(max_attempts), 10)),
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    return job


async def claim_next_job(
    db: AsyncSession,
) -> Optional[models.GenerationJob]:
    now = to_iso(utc_now())
    lock_token = uuid4().hex
    candidate_result = await db.execute(
        select(models.GenerationJob.id)
        .where(models.GenerationJob.status == models.JobStatus.QUEUED)
        .where(models.GenerationJob.attempts < models.GenerationJob.max_attempts)
        .where(models.GenerationJob.available_at <= now)
        .order_by(
            models.GenerationJob.available_at.asc(),
            models.GenerationJob.id.asc(),
        )
        .limit(1)
    )
    job_id = candidate_result.scalar_one_or_none()
    if job_id is None:
        await db.rollback()
        return None

    claim_result = await db.execute(
        update(models.GenerationJob)
        .where(models.GenerationJob.id == job_id)
        .where(models.GenerationJob.status == models.JobStatus.QUEUED)
        .values(
            status=models.JobStatus.RUNNING,
            attempts=models.GenerationJob.attempts + 1,
            locked_at=now,
            lock_token=lock_token,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if int(claim_result.rowcount or 0) != 1:
        await db.rollback()
        return None

    await db.commit()
    return await db.get(models.GenerationJob, job_id)


async def prepare_job_attempt(
    db: AsyncSession,
    job: models.GenerationJob,
) -> None:
    if int(job.attempts or 0) <= 1:
        return

    project = await db.get(models.Project, job.project_id)
    if not project:
        raise RuntimeError(f"Project {job.project_id} no longer exists")

    project.status = models.ProcessingStatus.GENERATING
    if job.kind == OUTLINE_JOB:
        await db.execute(
            delete(models.Scene).where(
                models.Scene.project_id == job.project_id
            )
        )
    elif job.kind == CONTENT_JOB:
        await db.execute(
            update(models.Scene)
            .where(models.Scene.project_id == job.project_id)
            .where(
                models.Scene.status.in_(
                    [
                        models.ProcessingStatus.FAILED,
                        models.ProcessingStatus.GENERATING,
                    ]
                )
            )
            .values(
                status=models.ProcessingStatus.PENDING,
                content=None,
                summary=None,
            )
            .execution_options(synchronize_session=False)
        )
    await db.commit()


async def complete_job(
    db: AsyncSession,
    job_id: int,
    lock_token: str,
) -> bool:
    now = to_iso(utc_now())
    result = await db.execute(
        update(models.GenerationJob)
        .where(models.GenerationJob.id == job_id)
        .where(models.GenerationJob.status == models.JobStatus.RUNNING)
        .where(models.GenerationJob.lock_token == lock_token)
        .values(
            status=models.JobStatus.COMPLETED,
            locked_at=None,
            lock_token=None,
            last_error=None,
            updated_at=now,
        )
    )
    await db.commit()
    return int(result.rowcount or 0) == 1


async def heartbeat_job(
    db: AsyncSession,
    job_id: int,
    lock_token: str,
) -> bool:
    now = to_iso(utc_now())
    result = await db.execute(
        update(models.GenerationJob)
        .where(models.GenerationJob.id == job_id)
        .where(models.GenerationJob.status == models.JobStatus.RUNNING)
        .where(models.GenerationJob.lock_token == lock_token)
        .values(locked_at=now, updated_at=now)
    )
    await db.commit()
    return int(result.rowcount or 0) == 1


async def fail_job(
    db: AsyncSession,
    job_id: int,
    lock_token: str,
    error: Exception,
) -> models.JobStatus:
    job = await db.get(models.GenerationJob, job_id)
    if not job:
        return models.JobStatus.FAILED
    if (
        job.status != models.JobStatus.RUNNING
        or job.lock_token != lock_token
    ):
        return job.status

    now_value = utc_now()
    should_retry = int(job.attempts or 0) < int(job.max_attempts or 1)
    project = await db.get(models.Project, job.project_id)
    if should_retry:
        delay_seconds = min(300, 5 * (2 ** max(0, int(job.attempts or 1) - 1)))
        job.status = models.JobStatus.QUEUED
        job.available_at = to_iso(now_value + timedelta(seconds=delay_seconds))
        if project:
            project.status = models.ProcessingStatus.GENERATING
    else:
        job.status = models.JobStatus.FAILED
        if project:
            project.status = models.ProcessingStatus.FAILED
    job.locked_at = None
    job.lock_token = None
    job.last_error = f"{type(error).__name__}: {error}"[:5000]
    job.updated_at = to_iso(now_value)
    await db.commit()
    return job.status


async def recover_stale_jobs(
    db: AsyncSession,
    *,
    lease_seconds: int,
) -> tuple[int, int]:
    stale_before = to_iso(
        utc_now() - timedelta(seconds=max(30, int(lease_seconds)))
    )
    result = await db.execute(
        select(models.GenerationJob)
        .where(models.GenerationJob.status == models.JobStatus.RUNNING)
        .where(models.GenerationJob.locked_at <= stale_before)
    )
    stale_jobs = result.scalars().all()
    requeued = 0
    failed = 0
    now = to_iso(utc_now())

    for job in stale_jobs:
        job.locked_at = None
        job.lock_token = None
        job.updated_at = now
        job.last_error = "Worker lease expired before completion."
        if int(job.attempts or 0) < int(job.max_attempts or 1):
            job.status = models.JobStatus.QUEUED
            job.available_at = now
            project = await db.get(models.Project, job.project_id)
            if project:
                project.status = models.ProcessingStatus.GENERATING
            requeued += 1
        else:
            job.status = models.JobStatus.FAILED
            project = await db.get(models.Project, job.project_id)
            if project:
                project.status = models.ProcessingStatus.FAILED
            failed += 1

    await db.commit()
    return requeued, failed
