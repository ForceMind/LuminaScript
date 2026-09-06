from __future__ import annotations

from datetime import datetime, timezone
from contextvars import ContextVar

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from database import SessionLocal


_ai_billed_user: ContextVar[int | None] = ContextVar("ai_billed_user", default=None)


async def invoke_with_quota(billed_user_id: int, invoke):
    """Request/task-local scope, inherited by provider retries but never shared."""
    token = _ai_billed_user.set(billed_user_id)
    try:
        return await invoke()
    finally:
        _ai_billed_user.reset(token)


async def recheck_ai_quota() -> None:
    billed_user_id = _ai_billed_user.get()
    if billed_user_id is not None:
        # A short independent read sees other calls/admin limit updates. No session
        # or SQLite transaction is held for the duration of the network request.
        async with SessionLocal() as db:
            await enforce_user_quota(db, billed_user_id)


def period_starts() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.replace(tzinfo=None).isoformat(), month.replace(tzinfo=None).isoformat()


async def get_user_usage(db: AsyncSession, user_id: int) -> dict[str, int]:
    day_start, month_start = period_starts()
    logs = models.AIInteractionLog
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(case((logs.timestamp >= day_start, logs.tokens), else_=0)),
                0,
            ).label("daily_tokens"),
            func.coalesce(func.sum(logs.tokens), 0).label("monthly_tokens"),
        )
        .where(func.coalesce(logs.billed_user_id, logs.user_id) == user_id)
        .where(logs.timestamp >= month_start)
    )
    daily, monthly = result.one()
    return {"daily_tokens": int(daily or 0), "monthly_tokens": int(monthly or 0)}


async def get_all_user_usage(db: AsyncSession) -> dict[int, dict[str, int]]:
    """Aggregate all active billing identities in one bounded historical scan."""
    day_start, month_start = period_starts()
    logs = models.AIInteractionLog
    billing_identity = func.coalesce(logs.billed_user_id, logs.user_id)
    rows = await db.execute(
        select(
            billing_identity.label("user_id"),
            func.coalesce(
                func.sum(case((logs.timestamp >= day_start, logs.tokens), else_=0)),
                0,
            ).label("daily_tokens"),
            func.coalesce(func.sum(logs.tokens), 0).label("monthly_tokens"),
        )
        .where(logs.timestamp >= month_start)
        .group_by(billing_identity)
    )
    return {
        int(user_id): {"daily_tokens": int(daily or 0), "monthly_tokens": int(monthly or 0)}
        for user_id, daily, monthly in rows
        if user_id is not None
    }


async def enforce_user_quota(db: AsyncSession, user_id: int) -> dict[str, int]:
    user = await db.get(models.User, user_id, populate_existing=True)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    usage = await get_user_usage(db, user_id)
    daily_limit = int(user.daily_token_limit or 0)
    monthly_limit = int(user.monthly_token_limit or 0)
    if daily_limit and usage["daily_tokens"] >= daily_limit:
        raise HTTPException(status_code=429, detail="今日 AI Token 额度已用完")
    if monthly_limit and usage["monthly_tokens"] >= monthly_limit:
        raise HTTPException(status_code=429, detail="本月 AI Token 额度已用完")
    return {
        **usage,
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
    }
