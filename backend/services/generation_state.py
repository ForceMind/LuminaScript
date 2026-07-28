from typing import Optional

import models


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
