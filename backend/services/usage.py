from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models


def period_starts() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.replace(tzinfo=None).isoformat(), month.replace(tzinfo=None).isoformat()


async def get_user_usage(db: AsyncSession, user_id: int) -> dict[str, int]:
    day_start, month_start = period_starts()
    daily = await db.scalar(
        select(func.coalesce(func.sum(models.AIInteractionLog.tokens), 0))
        .where(models.AIInteractionLog.user_id == user_id)
        .where(models.AIInteractionLog.timestamp >= day_start)
    )
    monthly = await db.scalar(
        select(func.coalesce(func.sum(models.AIInteractionLog.tokens), 0))
        .where(models.AIInteractionLog.user_id == user_id)
        .where(models.AIInteractionLog.timestamp >= month_start)
    )
    return {"daily_tokens": int(daily or 0), "monthly_tokens": int(monthly or 0)}


async def enforce_user_quota(db: AsyncSession, user_id: int) -> dict[str, int]:
    user = await db.get(models.User, user_id)
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
