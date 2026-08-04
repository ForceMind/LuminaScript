from typing import Optional

import models


GENERATION_ERROR_KEY = "_last_generation_error"


def record_generation_error(
    project: models.Project,
    exc: BaseException,
    *,
    stage: str,
) -> None:
    context = dict(project.global_context) if isinstance(project.global_context, dict) else {}
    context[GENERATION_ERROR_KEY] = {
        "type": exc.__class__.__name__,
        "message": (str(exc).strip() or exc.__class__.__name__)[:2_000],
        "stage": stage[:100],
    }
    project.global_context = context


def clear_generation_error(project: models.Project) -> None:
    context = dict(project.global_context) if isinstance(project.global_context, dict) else {}
    if GENERATION_ERROR_KEY in context:
        context.pop(GENERATION_ERROR_KEY, None)
        project.global_context = context


def get_generation_error(project: models.Project) -> str:
    context = project.global_context if isinstance(project.global_context, dict) else {}
    error = context.get(GENERATION_ERROR_KEY)
    if not isinstance(error, dict):
        return ""
    error_type = str(error.get("type") or "GenerationError").strip()
    message = str(error.get("message") or "").strip()
    stage = str(error.get("stage") or "").strip()
    detail = f"{error_type}: {message}" if message else error_type
    return f"{stage} - {detail}" if stage else detail


def invalidate_scene_prompt_cache(
    project: models.Project,
    scene_index: Optional[int] = None,
) -> bool:
    context = (
        dict(project.global_context)
        if isinstance(project.global_context, dict)
        else {}
    )
    prompt_cache = context.get("_scene_ai_prompts")
    if not isinstance(prompt_cache, dict):
        return False

    updated_cache = dict(prompt_cache)
    if scene_index is None:
        if not updated_cache:
            return False
        updated_cache.clear()
    else:
        removed = updated_cache.pop(str(scene_index), None)
        if removed is None:
            return False

    if updated_cache:
        context["_scene_ai_prompts"] = updated_cache
    else:
        context.pop("_scene_ai_prompts", None)
    project.global_context = context
    return True
