"""Pure working-draft state, immutable baseline selection and value deltas."""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from services import setup_fields
from services.setup_state import valid_setup_cache

DRAFT_SCHEMA = 1
REGENERATE_KEY = "_quick_setup_regenerate_requested"


def inspect_draft(project) -> tuple[dict | None, bool]:
    raw = project.quick_setup_draft
    if raw is None:
        return None, False
    if not isinstance(raw, dict):
        return {"values": {}, "baseline_values": {}}, True
    draft = deepcopy(raw)
    stale = (type(draft.get("schema")) is not int or draft.get("schema") != DRAFT_SCHEMA
             or type(draft.get("base_setup_revision")) is not int
             or draft.get("base_setup_revision") != int(project.setup_revision or 0))
    try:
        setup_fields.validate_safety(draft.get("values"))
        setup_fields.validate_safety(draft.get("baseline_values"))
    except ValueError:
        stale = True
    for key in ("edited_fields", "ai_adjusted_fields"):
        items = draft.get(key)
        if not isinstance(items, list) or any(not isinstance(item, str) or item not in setup_fields.SETUP_FIELDS for item in items):
            stale = True
    if not isinstance(draft.get("saved_at"), str):
        stale = True
    return draft, stale


def require_current_draft(project) -> None:
    _, stale = inspect_draft(project)
    if stale:
        raise HTTPException(status_code=409, detail="保存的工作稿已过期，只能查看、复制或明确丢弃；请勿覆盖当前正式设定。")


def baseline_for(project, supplied: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    draft, stale = inspect_draft(project)
    if draft is not None and not stale:
        return deepcopy(draft["baseline_values"]), "saved"
    mode = (project.global_context or {}).get("_setup_mode", "") if isinstance(project.global_context, dict) else ""
    if valid_setup_cache(project, mode=mode, stage="quick_review"):
        payload = project.next_step_cache["payload"]
        baseline = payload.get("baseline_values")
        if baseline is None:
            baseline = {item["key"]: item["value"] for item in payload["sections"]}
        try:
            return setup_fields.validate_safety(baseline), "cache"
        except ValueError:
            pass
    if supplied:
        return setup_fields.validate_safety(supplied), "request"
    context = project.global_context if isinstance(project.global_context, dict) else {}
    baseline = {key: str(value) for key, value in context.items() if key in setup_fields.SETUP_FIELDS}
    if project.project_type in setup_fields.PROJECT_TYPES:
        baseline["project_type"] = project.project_type
    return setup_fields.validate_safety(baseline), "formal"


def value_changes(values: dict[str, str], baseline: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        key: {"before": baseline.get(key, ""), "after": values.get(key, "")}
        for key in sorted(set(values) | set(baseline))
        if values.get(key, "") != baseline.get(key, "")
    }


def build_working_draft(project, values: dict[str, str], baseline: dict[str, str], edited_fields: list[str], ai_adjusted_fields: list[str], *, mode_change: bool = False) -> dict[str, Any]:
    values = setup_fields.validate_safety(values)
    baseline = setup_fields.validate_safety(baseline)
    changed = set(value_changes(values, baseline))
    ai_fields = changed.intersection(ai_adjusted_fields) - set(edited_fields)
    return {
        "schema": DRAFT_SCHEMA, "values": deepcopy(values), "baseline_values": deepcopy(baseline),
        "edited_fields": sorted(changed - ai_fields), "ai_adjusted_fields": sorted(ai_fields),
        "base_setup_revision": int(project.setup_revision or 0) + int(mode_change),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def draft_after_mode_change(project) -> dict | None:
    draft, stale = inspect_draft(project)
    if draft is not None and not stale:
        draft["base_setup_revision"] = int(project.setup_revision or 0) + 1
        return draft
    return deepcopy(project.quick_setup_draft)
