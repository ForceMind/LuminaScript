from datetime import datetime, timezone
import logging
from typing import Optional

from user_agents import parse

import models
from sqlalchemy import update
from database import SessionLocal


logger = logging.getLogger(__name__)


async def log_login(
    user_id: int,
    ip: str,
    status: str,
    user_agent_str: str = "",
) -> None:
    try:
        user_agent = parse(user_agent_str)
        device_info = (
            f"{user_agent.os.family} {user_agent.os.version_string} / "
            f"{user_agent.browser.family} {user_agent.browser.version_string}"
        )
        if user_agent.is_mobile:
            device_info += " (Mobile)"
        if user_agent.is_tablet:
            device_info += " (Tablet)"
        if user_agent.is_pc:
            device_info += " (PC)"
    except Exception as exc:
        logger.error("Error parsing user agent: %s", exc)
        device_info = user_agent_str[:50]

    async with SessionLocal() as db:
        db.add(
            models.LoginLog(
                user_id=user_id,
                ip_address=ip,
                user_agent=device_info,
                status=status,
                timestamp=datetime.now().isoformat(),
            )
        )
        await db.commit()


async def log_ai_action(
    user_id: int,
    project_id: Optional[int],
    action: str,
    prompt: str,
    response: str,
    tokens: int,
    *,
    billed_user_id: Optional[int] = None,
    status: str = "success",
    step_key: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    attempt: int = 1,
) -> int:
    prompt_text = "" if prompt is None else str(prompt)
    response_text = "" if response is None else str(response)

    async with SessionLocal() as db:
        # A call can finish after its project was deleted. Preserve operator and
        # captured billing attribution without an invalid project foreign key.
        if project_id is not None and await db.get(models.Project, project_id) is None:
            project_id = None
        entry = models.AIInteractionLog(
            user_id=user_id,
            billed_user_id=billed_user_id if billed_user_id is not None else user_id,
            project_id=project_id,
            action=action,
            prompt=prompt_text,
            response=response_text,
            tokens=tokens,
            status=(status or "success")[:50],
            step_key=(step_key or "")[:100] or None,
            error_type=(error_type or "")[:100] or None,
            error_message=(error_message or "")[:5000] or None,
            attempt=max(1, int(attempt or 1)),
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )
        db.add(entry)
        await db.commit()
        return entry.id


async def update_ai_action_status(log_id: int, *, status: str, error_type: str, error_message: str) -> None:
    """Finalize an already-accounted call without creating a second usage entry."""
    async with SessionLocal() as db:
        result = await db.execute(update(models.AIInteractionLog).where(models.AIInteractionLog.id == log_id).values(
            status=status[:50], error_type=error_type[:100], error_message=error_message[:5000],
        ))
        if int(result.rowcount or 0) != 1:
            raise RuntimeError("AI audit entry no longer exists")
        await db.commit()
