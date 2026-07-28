import asyncio
from datetime import timedelta

import pytest

import database
import models
import worker
from repositories.projects import recover_interrupted
from services.job_queue import (
    OUTLINE_JOB,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    heartbeat_job,
    recover_stale_jobs,
    to_iso,
    utc_now,
)


async def seed_project(session, project_id: int = 1) -> models.Project:
    user = models.User(
        id=project_id,
        username=f"queue-user-{project_id}",
        hashed_password="unused",
    )
    project = models.Project(
        id=project_id,
        title="queued project",
        logline="story",
        project_type="movie",
        owner_id=project_id,
        status=models.ProcessingStatus.GENERATING,
    )
    session.add_all([user, project])
    await session.commit()
    return project


@pytest.mark.asyncio
async def test_only_one_worker_can_claim_the_same_job():
    async with database.SessionLocal() as session:
        await seed_project(session)
        job = await enqueue_job(
            session,
            project_id=1,
            kind=OUTLINE_JOB,
            payload={"target_count": 1},
        )
        await session.commit()
        job_id = job.id

    async def claim_once():
        async with database.SessionLocal() as session:
            claimed = await claim_next_job(session)
            return claimed.id if claimed else None

    claimed_ids = await asyncio.gather(claim_once(), claim_once())

    assert claimed_ids.count(job_id) == 1
    assert claimed_ids.count(None) == 1


@pytest.mark.asyncio
async def test_stale_worker_lease_is_requeued_without_releasing_project():
    async with database.SessionLocal() as session:
        await seed_project(session)
        stale_time = to_iso(utc_now() - timedelta(minutes=10))
        job = models.GenerationJob(
            project_id=1,
            kind=OUTLINE_JOB,
            payload={},
            status=models.JobStatus.RUNNING,
            attempts=1,
            max_attempts=3,
            available_at=stale_time,
            locked_at=stale_time,
            created_at=stale_time,
            updated_at=stale_time,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

        requeued, failed = await recover_stale_jobs(
            session,
            lease_seconds=30,
        )

        await session.refresh(job)
        project = await session.get(models.Project, 1)
        assert (requeued, failed) == (1, 0)
        assert job.id == job_id
        assert job.status == models.JobStatus.QUEUED
        assert job.locked_at is None
        assert project.status == models.ProcessingStatus.GENERATING


@pytest.mark.asyncio
async def test_expired_worker_cannot_update_a_reclaimed_job():
    async with database.SessionLocal() as session:
        await seed_project(session)
        await enqueue_job(
            session,
            project_id=1,
            kind=OUTLINE_JOB,
            payload={"target_count": 1},
        )
        await session.commit()

    async with database.SessionLocal() as session:
        first_claim = await claim_next_job(session)
        first_token = first_claim.lock_token
        first_claim.locked_at = to_iso(utc_now() - timedelta(minutes=10))
        await session.commit()
        await recover_stale_jobs(session, lease_seconds=30)

    async with database.SessionLocal() as session:
        second_claim = await claim_next_job(session)
        second_token = second_claim.lock_token
        assert second_token != first_token

    async with database.SessionLocal() as session:
        assert await heartbeat_job(
            session,
            second_claim.id,
            first_token,
        ) is False
        assert await complete_job(
            session,
            second_claim.id,
            first_token,
        ) is False
        status = await fail_job(
            session,
            second_claim.id,
            first_token,
            RuntimeError("late failure"),
        )
        assert status == models.JobStatus.RUNNING

        job = await session.get(models.GenerationJob, second_claim.id)
        assert job.status == models.JobStatus.RUNNING
        assert job.lock_token == second_token

        assert await complete_job(
            session,
            second_claim.id,
            second_token,
        ) is True


@pytest.mark.asyncio
async def test_api_recovery_preserves_projects_with_active_queue_jobs():
    async with database.SessionLocal() as session:
        await seed_project(session)
        scene = models.Scene(
            project_id=1,
            scene_index=1,
            outline="scene",
            status=models.ProcessingStatus.GENERATING,
        )
        session.add(scene)
        await enqueue_job(
            session,
            project_id=1,
            kind=OUTLINE_JOB,
            payload={"target_count": 1},
        )
        await session.commit()

        recovered = await recover_interrupted(session)
        await session.refresh(scene)
        project = await session.get(models.Project, 1)

        assert recovered == (0, 0)
        assert scene.status == models.ProcessingStatus.GENERATING
        assert project.status == models.ProcessingStatus.GENERATING


@pytest.mark.asyncio
async def test_worker_completes_claimed_job(monkeypatch):
    async with database.SessionLocal() as session:
        await seed_project(session)
        job = await enqueue_job(
            session,
            project_id=1,
            kind=OUTLINE_JOB,
            payload={"target_count": 1},
        )
        await session.commit()
        job_id = job.id

    async def complete_project(_job):
        async with database.SessionLocal() as session:
            project = await session.get(models.Project, 1)
            project.status = models.ProcessingStatus.COMPLETED
            await session.commit()

    monkeypatch.setattr(worker, "execute_job", complete_project)

    assert await worker.process_next_job() is True

    async with database.SessionLocal() as session:
        job = await session.get(models.GenerationJob, job_id)
        assert job.status == models.JobStatus.COMPLETED
        assert job.attempts == 1
        assert job.locked_at is None


@pytest.mark.asyncio
async def test_worker_failure_retries_and_keeps_project_claimed(monkeypatch):
    async with database.SessionLocal() as session:
        await seed_project(session)
        job = await enqueue_job(
            session,
            project_id=1,
            kind=OUTLINE_JOB,
            payload={"target_count": 1},
        )
        await session.commit()
        job_id = job.id

    async def fail_generation(_job):
        async with database.SessionLocal() as session:
            project = await session.get(models.Project, 1)
            project.status = models.ProcessingStatus.FAILED
            await session.commit()
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(worker, "execute_job", fail_generation)

    assert await worker.process_next_job() is True

    async with database.SessionLocal() as session:
        job = await session.get(models.GenerationJob, job_id)
        project = await session.get(models.Project, 1)
        assert job.status == models.JobStatus.QUEUED
        assert job.attempts == 1
        assert "synthetic worker failure" in job.last_error
        assert project.status == models.ProcessingStatus.GENERATING
