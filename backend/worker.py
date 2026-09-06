import asyncio
import logging

import models
from core.config import settings
from database import SessionLocal
from migrate import run_migrations
from services.job_queue import (
    CONTENT_JOB,
    OUTLINE_JOB,
    claim_next_job,
    complete_job,
    fail_job,
    heartbeat_job,
    prepare_job_attempt,
    recover_stale_jobs,
)
from services.generation_state import get_generation_error


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("lumina_worker")


async def heartbeat_loop(
    job_id: int,
    lock_token: str,
    stop_event: asyncio.Event,
) -> None:
    interval = max(10.0, settings.worker_lease_seconds / 3)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            try:
                async with SessionLocal() as db:
                    if not await heartbeat_job(db, job_id, lock_token):
                        logger.warning(
                            "Job %s no longer owns its running lease.",
                            job_id,
                        )
                        return
            except Exception:
                logger.exception("Failed to heartbeat generation job %s.", job_id)


async def execute_job(job: models.GenerationJob) -> None:
    # The generation engine is imported lazily so queue maintenance and
    # migrations do not initialize the LLM client until work exists.
    from main import run_generation_loop, run_incremental_outline_generation

    payload = job.payload if isinstance(job.payload, dict) else {}
    async with SessionLocal() as db:
        project = await db.get(models.Project, job.project_id)
        if not project:
            raise RuntimeError(f"Project {job.project_id} no longer exists")
        actor_id = int(payload.get("user_id") or project.owner_id)
    if job.kind == OUTLINE_JOB:
        await run_incremental_outline_generation(
            project_id=job.project_id,
            style_context=str(payload.get("style_context", "")),
            target_count=max(1, int(payload.get("target_count", 1))),
            user_id=actor_id,
            job_id=job.id,
            lock_token=job.lock_token,
        )
    elif job.kind == CONTENT_JOB:
        await run_generation_loop(
            job.project_id,
            user_id=actor_id,
            job_id=job.id,
            lock_token=job.lock_token,
        )
    else:
        raise RuntimeError(f"Unsupported job kind: {job.kind}")

    async with SessionLocal() as db:
        project = await db.get(models.Project, job.project_id)
        if not project:
            raise RuntimeError(f"Project {job.project_id} no longer exists")
        if project.status != models.ProcessingStatus.COMPLETED:
            generation_error = get_generation_error(project)
            raise RuntimeError(
                generation_error
                or f"Project generation ended with status {project.status}"
            )


async def process_next_job() -> bool:
    async with SessionLocal() as db:
        job = await claim_next_job(db)
    if not job:
        return False

    logger.info(
        "Claimed generation job id=%s kind=%s project=%s attempt=%s/%s",
        job.id,
        job.kind,
        job.project_id,
        job.attempts,
        job.max_attempts,
    )

    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(job.id, job.lock_token, stop_heartbeat)
    )
    try:
        async with SessionLocal() as db:
            attached_job = await db.get(models.GenerationJob, job.id)
            if not attached_job:
                raise RuntimeError(f"Job {job.id} no longer exists")
            await prepare_job_attempt(db, attached_job)

        await execute_job(job)
        async with SessionLocal() as db:
            completed = await complete_job(db, job.id, job.lock_token)
        if not completed:
            raise RuntimeError("Job lease was lost before completion")
        logger.info("Completed generation job id=%s", job.id)
    except Exception as exc:
        logger.exception("Generation job id=%s failed: %s", job.id, exc)
        async with SessionLocal() as db:
            next_status = await fail_job(
                db,
                job.id,
                job.lock_token,
                exc,
            )
        logger.warning(
            "Generation job id=%s transitioned to %s",
            job.id,
            next_status,
        )
    finally:
        stop_heartbeat.set()
        await heartbeat_task
    return True


async def worker_loop() -> None:
    await asyncio.to_thread(run_migrations)
    async with SessionLocal() as db:
        requeued, failed = await recover_stale_jobs(
            db,
            lease_seconds=settings.worker_lease_seconds,
        )
    if requeued or failed:
        logger.warning(
            "Recovered stale generation jobs: requeued=%s failed=%s",
            requeued,
            failed,
        )

    logger.info(
        "LuminaScript worker ready. poll=%ss lease=%ss",
        settings.worker_poll_seconds,
        settings.worker_lease_seconds,
    )
    while True:
        processed = await process_next_job()
        if not processed:
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped.")
