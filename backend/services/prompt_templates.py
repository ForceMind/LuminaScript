from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models


ALLOWED_TEMPLATE_STAGES = {"outline", "content", "review", "interaction", "prompt"}


async def get_prompt_addendum(
    db: AsyncSession,
    *,
    stage: str,
    project_type: str,
) -> str:
    result = await db.execute(
        select(models.PromptTemplate)
        .where(models.PromptTemplate.enabled.is_(True))
        .where(models.PromptTemplate.stage == stage)
        .where(models.PromptTemplate.project_type.in_([project_type, "all"]))
        .order_by(
            (models.PromptTemplate.project_type == project_type).desc(),
            models.PromptTemplate.id.desc(),
        )
    )
    templates = result.scalars().all()
    if not templates:
        return ""
    sections = [
        f"【{template.name}】\n{str(template.content or '').strip()}"
        for template in templates
        if str(template.content or "").strip()
    ]
    return "\n\n".join(sections)[:12000]
