"""Single write boundary for setup state and versioned analysis caches.

Callers build detached values, never mutate a session-bound Project before CAS.
The conditional UPDATE serializes with generation claims and other setup writers.
The caller owns commit so version restoration can replace scenes atomically.
"""
from copy import deepcopy
import re
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models


CACHE_SCHEMA = 2
SETUP_ATTRIBUTES = (
    "title", "logline", "project_type", "genre", "global_context",
    "global_summary", "next_step_cache", "setup_revision",
    "setup_cache_revision", "status", "total_tokens",
)
SETUP_WRITE_FIELDS = {
    "title", "logline", "project_type", "genre", "global_context",
    "global_summary", "status",
}


def context_revision(project: models.Project) -> str:
    return f"setup-v2:{int(project.setup_revision or 0)}:{int(project.setup_cache_revision or 0)}"


def revision_meta(project: models.Project) -> dict[str, Any]:
    return {
        "context_revision": context_revision(project),
        "setup_revision": int(project.setup_revision or 0),
        "setup_cache_revision": int(project.setup_cache_revision or 0),
    }


def parse_revision(token: str | None) -> tuple[int, int]:
    match = re.fullmatch(r"setup-v2:(0|[1-9]\d*):(0|[1-9]\d*)", token or "")
    if not match:
        raise HTTPException(status_code=409, detail="设定版本已失效，请刷新后重试。")
    revision = int(match[1]), int(match[2])
    if any(value > 2**63 - 1 for value in revision):
        raise HTTPException(status_code=409, detail="设定版本已失效，请刷新后重试。")
    return revision


def detached_setup(project: models.Project) -> SimpleNamespace:
    return SimpleNamespace(
        id=project.id,
        **{name: deepcopy(getattr(project, name)) for name in SETUP_ATTRIBUTES},
    )


def active_job_condition():
    return exists(
        select(models.GenerationJob.id)
        .where(models.GenerationJob.project_id == models.Project.id)
        .where(models.GenerationJob.status.in_([models.JobStatus.QUEUED, models.JobStatus.RUNNING]))
    )


def writable_conditions(project_id: int, token: str | None):
    setup, cache = parse_revision(token)
    return (
        models.Project.id == project_id,
        models.Project.setup_revision == setup,
        models.Project.setup_cache_revision == cache,
        models.Project.status != models.ProcessingStatus.GENERATING,
        ~active_job_condition(),
    )


async def assert_setup_writable(db: AsyncSession, project: models.Project, token: str | None) -> None:
    with db.no_autoflush:
        row = await db.scalar(select(models.Project.id).where(*writable_conditions(project.id, token)))
    if row is None:
        raise HTTPException(status_code=409, detail="项目设定已更新或正在生成，请刷新后重试。")
    # A request can reuse a session with an older identity-map object. Build all
    # proposed values from the observed row; the final CAS still checks token.
    await db.refresh(project, attribute_names=list(SETUP_ATTRIBUTES))


async def _conditional_update(
    db: AsyncSession, project: models.Project, token: str | None, values: dict,
    *, extra_conditions: tuple = (),
) -> None:
    with db.no_autoflush:
        result = await db.execute(
            update(models.Project)
            .where(*writable_conditions(project.id, token))
            .where(*extra_conditions)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
    if int(result.rowcount or 0) != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="项目设定已更新或正在生成，请刷新后重试。")
    await db.refresh(project, attribute_names=list(SETUP_ATTRIBUTES))


async def write_scene_prompt_cache(
    db: AsyncSession, project: models.Project, token: str,
    *, scene_id: int, scene_index: int, scene_outline: str | None,
    scene_content: str, prompt: str,
) -> None:
    """Merge a derived prompt only after the input snapshot still matches.

    The conditional no-op UPDATE takes a short database write lock *after* AI
    returns. Reading and merging under that lock preserves both newer setup
    fields and concurrent derived caches, without changing setup revisions.
    The caller must commit immediately after this helper (never await AI).
    """
    same_scene = exists(
        select(models.Scene.id).where(
            models.Scene.id == scene_id,
            models.Scene.project_id == models.Project.id,
            models.Scene.scene_index == scene_index,
            models.Scene.status == models.ProcessingStatus.COMPLETED,
            models.Scene.outline == scene_outline,
            models.Scene.content == scene_content,
        )
    )
    await _conditional_update(
        db, project, token, {"setup_revision": models.Project.setup_revision},
        extra_conditions=(same_scene,),
    )
    context = deepcopy(project.global_context) if isinstance(project.global_context, dict) else {}
    existing = context.get("_scene_ai_prompts")
    prompts = dict(existing) if isinstance(existing, dict) else {}
    prompts[str(scene_index)] = prompt
    context["_scene_ai_prompts"] = prompts
    with db.no_autoflush:
        await db.execute(
            update(models.Project)
            .where(models.Project.id == project.id)
            .values(global_context=context)
            .execution_options(synchronize_session=False)
        )
    await db.refresh(project, attribute_names=["global_context"])


async def write_setup(db: AsyncSession, project: models.Project, token: str | None, values: dict[str, Any]) -> None:
    if set(values) - SETUP_WRITE_FIELDS:
        raise ValueError("Unsupported setup write fields")
    await _conditional_update(db, project, token, {
        **deepcopy(values),
        "setup_revision": models.Project.setup_revision + 1,
        "setup_cache_revision": models.Project.setup_cache_revision + 1,
        "next_step_cache": None,
    })


async def write_setup_cache(
    db: AsyncSession, project: models.Project, token: str,
    payload: dict[str, Any], *, mode: str, stage: str,
) -> dict[str, Any]:
    setup, cache = parse_revision(token)
    cached = deepcopy(payload)
    cached["_setup_cache"] = {
        "schema": CACHE_SCHEMA, "mode": mode, "stage": stage,
        "setup_revision": setup, "setup_cache_revision": cache + 1,
    }
    cached.setdefault("payload", {})["context_revision"] = f"setup-v2:{setup}:{cache + 1}"
    await _conditional_update(db, project, token, {
        "next_step_cache": cached,
        "setup_cache_revision": models.Project.setup_cache_revision + 1,
    })
    return cached


def valid_setup_cache(project: models.Project, *, mode: str, stage: str) -> bool:
    cached = project.next_step_cache
    if not isinstance(cached, dict) or cached.get("type") != "interaction_required":
        return False
    if cached.get("_setup_cache") != {
        "schema": CACHE_SCHEMA, "mode": mode, "stage": stage,
        "setup_revision": int(project.setup_revision or 0),
        "setup_cache_revision": int(project.setup_cache_revision or 0),
    }:
        return False
    payload = cached.get("payload")
    if not isinstance(payload, dict) or payload.get("field") != stage:
        return False
    if payload.get("context_revision") != context_revision(project):
        return False
    if not isinstance(payload.get("question"), str) or not payload["question"].strip():
        return False
    if stage == "quick_review":
        sections = payload.get("sections")
        return isinstance(sections, list) and bool(sections) and all(
            isinstance(item, dict) and isinstance(item.get("key"), str)
            and isinstance(item.get("value"), str)
            and isinstance(item.get("label"), str)
            and isinstance(item.get("question"), str)
            and isinstance(item.get("editable"), bool)
            and item.get("source") in ("confirmed", "ai")
            for item in sections
        )
    options = payload.get("options")
    return isinstance(options, list) and len(options) >= 3 and all(
        isinstance(item, dict) and isinstance(item.get("label"), str)
        and isinstance(item.get("value"), str) for item in options
    )
