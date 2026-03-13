from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import OperationalError
from typing import List, Dict, Any, Optional
from pydantic import BaseModel 
import json
import math
import re
import zipfile
from pathlib import Path

from database import init_db, get_db
import models
import schemas
import auth
from services import llm  # Import LLM Service
import logging
import sys
from datetime import datetime
from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import StreamingResponse
import io
from urllib.parse import quote

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

# Load environment variables from .env file if it exists
load_dotenv()
from database import init_db, get_db, SessionLocal
import database # needed for SessionLocal access in some scopes if not imported directly

# Configure Logging
logging.basicConfig(
    level=logging.INFO, # Changed to INFO to avoid too much noise but capture essential flows
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("lumina_backend")

PROJECT_TYPE_LABELS = {
    "movie": "电影剧本",
    "tv": "剧集剧本",
    "short": "短剧剧本",
    "short_video": "短视频",
    "pending": "待确定"
}

SUMMARY_LABELS = {
    "title": "故事题目",
    "project_type": "剧本类型",
    "logline": "核心概念",
    "synopsis_brief": "故事梗概",
    "movie_duration": "电影时长",
    "scene_count_target": "目标场次",
    "episode_count": "集数",
    "episode_duration": "单集时长",
    "video_duration_seconds": "总时长",
    "tone": "基调",
    "time_period": "时代背景",
    "story_expansion": "剧情大纲",
    "character_details": "人物设定",
    "plot_details": "关键设定",
    "theme": "主题",
    "visual_style": "视觉风格",
    "user_notes": "补充说明"
}

SUMMARY_ORDER = [
    "title",
    "project_type",
    "logline",
    "synopsis_brief",
    "movie_duration",
    "scene_count_target",
    "episode_count",
    "episode_duration",
    "video_duration_seconds",
    "tone",
    "time_period",
    "story_expansion",
    "character_details",
    "plot_details",
    "theme",
    "visual_style",
    "user_notes"
]

SETUP_FLOW_STEPS = [
    {"key": "project_type", "question": "您想创作哪种类型的剧本？", "default_options": [
        {"label": "🎥 电影剧本", "value": "movie"},
        {"label": "📺 剧集剧本", "value": "tv"},
        {"label": "📱 短剧剧本", "value": "short"},
        {"label": "🎬 短视频", "value": "short_video"}
    ]},
    {"key": "movie_duration", "question": "电影预计时长是多少分钟？", "movie_only": True},
    {"key": "scene_count_target", "question": "您希望生成多少场戏？（电影通常 40-100 场，越多越细）", "movie_only": True},
    {"key": "episode_count", "question": "您计划创作多少集？", "tv_short_only": True},
    {"key": "episode_duration", "question": "每一集的大致时长是？", "tv_short_only": True},
    {"key": "video_duration_seconds", "question": "短视频总时长是多少秒？系统会自动按每 15 秒拆分。", "short_video_only": True},
    {"key": "tone", "question": "这部作品的基调是什么？"},
    {"key": "time_period", "question": "故事发生在什么时代背景？"},
    {"key": "story_expansion", "question": "我们需要基于目前构思扩展出完整的剧情大纲，您有什么特别想法吗？"},
    {"key": "character_details", "question": "主要角色的性格、外貌、关系或背景有什么特别设定？"},
    {"key": "plot_details", "question": "有哪些一定要发生的关键情节、转折或高潮？"},
    {"key": "title", "question": "现在请为这个故事确定一个题目，最好直接给出书名号里的名字。"},
    {"key": "theme", "question": "您想通过这个故事探讨什么主题？"},
    {"key": "visual_style", "question": "视觉风格偏向于什么？"},
    {"key": "user_notes", "question": "还有什么补充内容，或者特别要求吗？"},
    {"key": "final_confirm", "question": "以上是剧本的完整设定，请确认是否可以开始生成分场大纲？", "is_confirmation": True}
]

FINAL_CONFIRM_EDIT_TARGETS = [
    ("story_expansion", "返回修改剧情大纲"),
    ("character_details", "返回修改人物设定"),
    ("plot_details", "返回修改关键设定"),
    ("title", "返回修改故事题目"),
]

FINAL_CONFIRM_ALLOWED_VALUES = {"confirmed", "reset"} | {f"edit:{key}" for key, _ in FINAL_CONFIRM_EDIT_TARGETS}
AUTO_PREFILL_MIN_LENGTH = 120
AUTO_PREFILL_FLAG = "_auto_prefill_attempted"
AUTO_PREFILL_FIELDS = [
    "project_type",
    "movie_duration",
    "scene_count_target",
    "episode_count",
    "episode_duration",
    "video_duration_seconds",
    "tone",
    "time_period",
    "title",
    "story_expansion",
    "character_details",
    "plot_details",
    "theme",
    "visual_style",
    "user_notes",
]


def get_relevant_setup_steps(project_type: str) -> List[Dict[str, Any]]:
    p_type = project_type or "movie"
    relevant_steps = []
    for step in SETUP_FLOW_STEPS:
        if step.get("movie_only") and p_type != "movie":
            continue
        if step.get("tv_short_only") and p_type not in {"tv", "short"}:
            continue
        if step.get("short_video_only") and p_type != "short_video":
            continue
        relevant_steps.append(step)
    return relevant_steps

TITLE_PATTERNS = [
    re.compile(r"《\s*([^《》\n]{1,60}?)\s*》"),
    re.compile(r"〈\s*([^〈〉\n]{1,60}?)\s*〉"),
    re.compile(r"「\s*([^「」\n]{1,60}?)\s*」"),
    re.compile(r"『\s*([^『』\n]{1,60}?)\s*』"),
]
TITLE_BREAK_PATTERN = re.compile(r"[，。！？：；,.!?;:\n]|--+|——|—|-")


def extract_story_title(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""

    for pattern in TITLE_PATTERNS:
        marked_title = pattern.search(text)
        if marked_title:
            return marked_title.group(1).strip()

    short_title = TITLE_BREAK_PATTERN.split(text, maxsplit=1)[0].strip(" \t\r\n\"'“”‘’《》")
    if short_title and len(short_title) <= 30:
        return short_title

    return ""


def sanitize_title_options(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized_options: List[Dict[str, Any]] = []
    seen_titles: set[str] = set()

    for option in options or []:
        if not isinstance(option, dict):
            continue

        raw_value = str(option.get("value", "") or "").strip()
        raw_label = str(option.get("label", "") or "").strip()
        clean_title = extract_story_title(raw_value) or extract_story_title(raw_label)
        if not clean_title:
            clean_title = raw_value if raw_value and len(raw_value) <= 30 else raw_label

        clean_title = clean_title.strip()
        if not clean_title or clean_title in seen_titles:
            continue

        seen_titles.add(clean_title)
        sanitized_options.append({
            "label": clean_title,
            "value": clean_title
        })

    return sanitized_options


def normalize_project_title(project: models.Project) -> bool:
    current_title = str(project.title or "").strip()
    context_title = ""
    if isinstance(project.global_context, dict):
        context_title = str(project.global_context.get("title", "") or "").strip()

    raw_candidate = context_title or current_title
    if not raw_candidate:
        return False

    clean_title = extract_story_title(raw_candidate)
    if not clean_title or clean_title == current_title:
        return False

    project.title = clean_title
    if isinstance(project.global_context, dict):
        updated_context = dict(project.global_context)
        updated_context["title"] = clean_title
        project.global_context = updated_context
    return True


def is_valid_character_details(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False

    if text in {"经典叙事风格", "带有反转的剧情", "大胆的实验性风格"}:
        return False

    if any(keyword in text for keyword in ("叙事风格", "实验风格", "镜头语言")) and not any(
        keyword in text for keyword in ("主角", "角色", "配角", "反派", "人物", "身份", "关系", "秘密")
    ):
        return False

    if len(text) < 12 and not any(keyword in text for keyword in ("主角", "角色", "配角", "人物")):
        return False

    return True


def normalize_project_context(project: models.Project) -> bool:
    if not isinstance(project.global_context, dict):
        return False

    updated_context = dict(project.global_context)
    changed = False

    if "character_details" in updated_context and not is_valid_character_details(updated_context.get("character_details")):
        updated_context.pop("character_details", None)
        changed = True

    if changed:
        project.global_context = updated_context

    return changed


def build_normalized_context(project: models.Project) -> Dict[str, Any]:
    raw_context = dict(project.global_context) if isinstance(project.global_context, dict) else {}
    context = {
        key: value
        for key, value in raw_context.items()
        if not str(key).startswith("_")
    }
    if project.project_type and project.project_type != "pending":
        context["project_type"] = project.project_type
    return context


def get_internal_project_context(project: models.Project) -> Dict[str, Any]:
    return dict(project.global_context) if isinstance(project.global_context, dict) else {}


def has_setup_value(project: models.Project, context: Dict[str, Any], key: str) -> bool:
    if key == "project_type":
        value = project.project_type if project.project_type and project.project_type != "pending" else context.get("project_type")
    elif key == "title":
        value = context.get("title") or project.title
    else:
        value = context.get(key)

    if isinstance(value, str):
        value = value.strip()

    return value not in (None, "", "pending")


def normalize_extracted_setup_value(key: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if key == "project_type":
        normalized = text.lower()
        return normalized if normalized in {"movie", "tv", "short", "short_video"} else ""

    if key == "title":
        return extract_story_title(text)

    if key in {"movie_duration", "scene_count_target", "episode_count", "video_duration_seconds"}:
        match = re.search(r"\d+", text)
        return match.group(0) if match else ""

    if key == "episode_duration":
        match = re.search(r"\d+", text)
        return f"{match.group(0)}mins" if match else ""

    if key == "character_details":
        return text if is_valid_character_details(text) else ""

    if key == "story_expansion":
        return text if len(text) >= 24 else ""

    if key == "plot_details":
        return text if len(text) >= 12 else ""

    if key in {"tone", "time_period", "theme", "visual_style", "user_notes"}:
        return text if len(text) >= 2 else ""

    return text


def should_auto_prefill_from_logline(project: models.Project, context: Dict[str, Any]) -> bool:
    if not isinstance(context, dict):
        return False
    raw_context = get_internal_project_context(project)
    if raw_context.get(AUTO_PREFILL_FLAG):
        return False

    clean_logline = re.sub(r"\s+", "", str(project.logline or ""))
    return len(clean_logline) >= AUTO_PREFILL_MIN_LENGTH


def apply_auto_prefill(project: models.Project, extracted_payload: Dict[str, Any] | None) -> tuple[List[str], bool]:
    current_context = dict(project.global_context) if isinstance(project.global_context, dict) else {}
    extracted_payload = extracted_payload if isinstance(extracted_payload, dict) else {}
    changed = False
    filled_fields: List[str] = []

    if not current_context.get(AUTO_PREFILL_FLAG):
        current_context[AUTO_PREFILL_FLAG] = True
        changed = True

    for key in AUTO_PREFILL_FIELDS:
        if has_setup_value(project, current_context, key):
            continue

        if key == "title":
            raw_value = extracted_payload.get(key) or project.logline or ""
        else:
            raw_value = extracted_payload.get(key)

        normalized_value = normalize_extracted_setup_value(key, raw_value)
        if not normalized_value:
            continue

        if key == "project_type":
            project.project_type = normalized_value

        if key == "title":
            project.title = normalized_value

        current_context[key] = normalized_value
        filled_fields.append(key)
        changed = True

    if changed:
        project.global_context = current_context

    return filled_fields, changed


def should_invalidate_cached_question(cache_payload: Any, current_context: Dict[str, Any] | None = None) -> bool:
    if not isinstance(cache_payload, dict):
        return False

    payload = cache_payload.get("payload")
    if not isinstance(payload, dict):
        return False

    field = payload.get("field")
    options = payload.get("options")
    current_context = current_context or {}

    if field == "project_type":
        current_value = current_context.get("project_type")
        if isinstance(current_value, str):
            current_value = current_value.strip()
        if current_value not in (None, "", "pending"):
            return True

    if field == "retry_current_step":
        return True

    if field and field not in {"final_confirm", "project_type"}:
        current_value = current_context.get(field)
        if isinstance(current_value, str):
            current_value = current_value.strip()
        if current_value not in (None, ""):
            return True

    if field == "final_confirm":
        if not isinstance(options, list):
            return True
        if not any(isinstance(option, dict) and str(option.get("value", "")).startswith("edit:") for option in options):
            return True

    if field == "title":
        if any(key not in current_context for key in ("story_expansion", "character_details", "plot_details")):
            return True

    if field not in {"character_details", "story_expansion", "plot_details"}:
        return False
    if not isinstance(options, list) or len(options) < 3:
        return True

    generic_values = {"经典叙事风格", "带有反转的剧情", "大胆的实验性风格"}

    if field == "character_details":
        for option in options:
            if not isinstance(option, dict):
                return True
            option_text = f"{option.get('label', '')}\n{option.get('value', '')}"
            if any(value in option_text for value in generic_values):
                return True
            if not any(keyword in option_text for keyword in ("主角", "角色", "配角", "反派", "人物", "关系", "秘密")):
                return True

    if field == "story_expansion":
        for option in options:
            if not isinstance(option, dict):
                return True
            option_text = f"{option.get('label', '')}\n{option.get('value', '')}"
            if any(value in option_text for value in generic_values):
                return True
            if not any(keyword in option_text for keyword in ("第一幕", "第二幕", "第三幕", "开端", "高潮")):
                return True

    if field == "plot_details":
        for option in options:
            if not isinstance(option, dict):
                return True
            option_text = f"{option.get('label', '')}\n{option.get('value', '')}"
            if any(value in option_text for value in generic_values):
                return True
            if not any(keyword in option_text for keyword in ("关键", "转折", "冲突", "危机", "真相", "高潮")):
                return True

    return False


def rewind_project_setup(project: models.Project, target_key: str) -> Dict[str, Any]:
    current_context = dict(project.global_context) if isinstance(project.global_context, dict) else {}
    project_type = project.project_type if project.project_type and project.project_type != "pending" else current_context.get("project_type", "movie")
    relevant_steps = get_relevant_setup_steps(project_type)

    clear_from_here = False
    for step in relevant_steps:
        key = step["key"]
        if key == target_key:
            clear_from_here = True
        if not clear_from_here or key == "project_type":
            continue

        current_context.pop(key, None)
        if key == "title":
            project.title = ""

    for derived_key in (
        "synopsis_brief",
        "synopsis_detailed",
        "brief_synopsis",
        "detailed_synopsis",
        "story_brief",
        "story_detailed",
        "final_confirm",
    ):
        current_context.pop(derived_key, None)

    project.global_context = current_context
    project.next_step_cache = None
    return current_context


def format_summary_value(key: str, value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        text = str(value).strip()

    if not text:
        return ""

    if key == "project_type":
        return PROJECT_TYPE_LABELS.get(text, text)

    if key in {"movie_duration", "episode_duration"}:
        if "分钟" in text:
            return text
        duration_match = re.search(r"\d+", text)
        if duration_match:
            return f"{duration_match.group(0)} 分钟"

    if key == "video_duration_seconds":
        if "秒" in text:
            return text
        duration_match = re.search(r"\d+", text)
        if duration_match:
            return f"{duration_match.group(0)} 秒"

    if key == "scene_count_target" and re.fullmatch(r"\d+", text):
        return f"{text} 场"

    if key == "episode_count" and re.fullmatch(r"\d+", text):
        return f"{text} 集"

    return text


def build_context_summary(project: models.Project, context: Dict[str, Any]) -> str:
    summary_context = dict(context or {})
    if project.logline:
        summary_context.setdefault("logline", project.logline)

    lines: List[str] = []
    for key in SUMMARY_ORDER:
        if key not in summary_context:
            continue

        label = SUMMARY_LABELS.get(key)
        if not label:
            continue

        display_value = format_summary_value(key, summary_context.get(key))
        if not display_value:
            continue

        if "\n" in display_value:
            lines.append(f"- {label}：")
            lines.append(display_value)
        else:
            lines.append(f"- {label}：{display_value}")

    return "\n".join(lines)


async def ensure_story_synopsis(project: models.Project, context: Dict[str, Any]) -> Dict[str, Any]:
    enriched_context = dict(context or {})
    has_brief = bool(str(enriched_context.get("synopsis_brief", "") or "").strip())
    has_detailed = bool(str(enriched_context.get("synopsis_detailed", "") or "").strip())
    if has_brief and has_detailed:
        return enriched_context

    try:
        synopsis = await llm.generate_story_synopsis(
            logline=project.logline or "",
            context=enriched_context,
            project_type=project.project_type or "movie"
        )
    except Exception as exc:
        logger.warning(f"Failed to generate story synopsis for project {project.id}: {exc}")
        return enriched_context

    brief = str(synopsis.get("brief", "") or "").strip()
    detailed = str(synopsis.get("detailed", "") or "").strip()

    if brief:
        enriched_context["synopsis_brief"] = brief
    if detailed:
        enriched_context["synopsis_detailed"] = detailed

    if enriched_context != (project.global_context or {}):
        project.global_context = enriched_context

    return enriched_context

# Initialize App
app = FastAPI(title="LuminaScript API", version="0.1.0")

if __name__ == "__main__":
    import uvicorn
    # Allow running this file directly for debugging
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")

import asyncio

@app.on_event("startup")
async def on_startup():
    logger.info("服务器正在启动...")
    await init_db()
    
    # Run Schema Upgrade Logic directly on startup to ensure DB is current
    try:
        logger.info("Running database schema upgrade check...")
        import upgrade_admin
        # Run in threadpool to avoid blocking event loop too much
        # But for startup, blocking is acceptable or use run_in_executor
        await asyncio.to_thread(upgrade_admin.upgrade_schema)
        logger.info("Database schema upgrade check complete.")
    except Exception as e:
        logger.error(f"Failed to run schema upgrade: {e}")
        
    logger.info("数据库初始化完成，服务准备就绪。")

@app.get("/")
async def root():
    logger.info("收到根路径请求")
    return {"message": "欢迎使用妙笔流光 (LuminaScript) API"}

# --- Admin & Logging Helpers ---

async def check_admin(current_user: models.User = Depends(auth.get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user

async def log_login(user_id: int, ip: str, status: str, user_agent_str: str = ''):
    try:
        from user_agents import parse
        import json
        ua = parse(user_agent_str)
        device_info = f"{ua.os.family} {ua.os.version_string} / {ua.browser.family} {ua.browser.version_string}"
        if ua.is_mobile: device_info += " (Mobile)"
        if ua.is_tablet: device_info += " (Tablet)"
        if ua.is_pc: device_info += " (PC)"
    except Exception as e:
        logger.error(f"Error parsing UA: {e}")
        device_info = user_agent_str[:50] # Fallback

    async with SessionLocal() as db:
        log = models.LoginLog(
             user_id=user_id, 
             ip_address=ip, 
             user_agent=device_info,
             status=status, 
             timestamp=datetime.now().isoformat()
        )
        db.add(log)
        await db.commit()

async def log_ai_action(
    user_id: int,
    project_id: Optional[int],
    action: str,
    prompt: str,
    response: str,
    tokens: int,
    *,
    status: str = "success",
    step_key: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    attempt: int = 1,
):
    async with SessionLocal() as db:
        log = models.AIInteractionLog(
            user_id=user_id,
            project_id=project_id,
            action=action,
            prompt=prompt[:5000],  # Truncate if too long to save generic DB space
            response=response[:5000],
            tokens=tokens,
            status=(status or "success")[:50],
            step_key=(step_key or "")[:100] or None,
            error_type=(error_type or "")[:100] or None,
            error_message=(error_message or "")[:5000] or None,
            attempt=max(1, int(attempt or 1)),
            timestamp=datetime.now().isoformat()
        )
        db.add(log)
        await db.commit()

# --- Admin Routes ---

@app.get("/admin/users", response_model=List[schemas.UserResponse])
async def admin_list_users(
    db: AsyncSession = Depends(get_db), 
    admin: models.User = Depends(check_admin)
):
    result = await db.execute(select(models.User))
    return result.scalars().all()

@app.get("/admin/logs/login", response_model=schemas.PaginatedLoginLogs)
async def admin_list_login_logs(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(check_admin)
):
    # Calculate offset
    offset = (page - 1) * page_size
    
    # 1. Get Total Count
    count_query = select(func.count()).select_from(models.LoginLog)
    total_result = await db.execute(count_query)
    total = int(total_result.scalar() or 0)

    # 2. Get Items
    result = await db.execute(
        select(models.LoginLog, models.User.username)
        .join(models.User, models.LoginLog.user_id == models.User.id)
        .order_by(models.LoginLog.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    )
    
    logs = []
    for log, username in result:
        logs.append(_serialize_admin_login_log(log, username or ""))
        
    return {"total": total, "items": logs}

@app.get("/admin/logs/ai", response_model=schemas.PaginatedAILogs)
async def admin_list_ai_logs(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(check_admin)
):
    async def _fetch_ai_logs_page() -> Dict[str, Any]:
        offset = (page - 1) * page_size

        count_query = select(func.count()).select_from(models.AIInteractionLog)
        total_result = await db.execute(count_query)
        total = int(total_result.scalar() or 0)

        result = await db.execute(
            select(models.AIInteractionLog, models.User.username)
            .join(models.User, models.AIInteractionLog.user_id == models.User.id)
            .order_by(models.AIInteractionLog.timestamp.desc())
            .offset(offset)
            .limit(page_size)
        )
        logs = []
        for log, username in result:
            logs.append(_serialize_admin_ai_log(log, username or ""))

        return {"total": total, "items": logs}

    try:
        return await _fetch_ai_logs_page()
    except OperationalError as exc:
        logger.error(f"AI 日志查询失败，尝试自动修复表结构: {exc}")
        try:
            import upgrade_admin
            await asyncio.to_thread(upgrade_admin.upgrade_schema)
            return await _fetch_ai_logs_page()
        except Exception as retry_exc:
            logger.error(f"AI 日志自动修复后仍失败: {retry_exc}")
            raise HTTPException(status_code=500, detail="AI日志表结构异常，请执行更新脚本后重试。")


def _serialize_admin_scene(scene: models.Scene) -> Dict[str, Any]:
    return {
        "id": scene.id,
        "scene_index": scene.scene_index,
        "outline": scene.outline,
        "content": scene.content,
        "summary": scene.summary,
        "status": str(scene.status),
    }


def _serialize_admin_project(project: models.Project, owner_lookup: Dict[int, str]) -> Dict[str, Any]:
    return {
        "id": project.id,
        "owner_id": project.owner_id,
        "owner_username": owner_lookup.get(project.owner_id, ""),
        "title": project.title,
        "logline": project.logline,
        "project_type": project.project_type,
        "genre": project.genre,
        "status": str(project.status),
        "total_tokens": project.total_tokens,
        "global_context": project.global_context or {},
        "global_summary": project.global_summary,
        "scene_count": len(project.scenes or []),
        "scenes": [
            _serialize_admin_scene(scene)
            for scene in sorted(project.scenes or [], key=lambda item: item.scene_index)
        ],
    }


def _serialize_admin_login_log(log: models.LoginLog, username: str) -> Dict[str, Any]:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_name": str(username or ""),
        "ip_address": str(log.ip_address or ""),
        "user_agent": str(log.user_agent or ""),
        "location": str(log.location or ""),
        "status": str(log.status or ""),
        "timestamp": str(log.timestamp or ""),
    }


def _serialize_admin_ai_log(log: models.AIInteractionLog, username: str) -> Dict[str, Any]:
    def _safe_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        try:
            return str(value)
        except Exception:
            return default

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_name": _safe_text(username),
        "project_id": log.project_id,
        "action": _safe_text(log.action),
        "prompt": _safe_text(log.prompt),
        "response": _safe_text(log.response),
        "tokens": _safe_int(log.tokens, 0),
        "status": _safe_text(log.status, "success") or "success",
        "step_key": _safe_text(log.step_key),
        "error_type": _safe_text(log.error_type),
        "error_message": _safe_text(log.error_message),
        "attempt": _safe_int(log.attempt, 1),
        "timestamp": _safe_text(log.timestamp),
    }


def _iter_export_database_paths() -> List[Path]:
    candidates: List[Path] = []
    sqlite_prefix = "sqlite+aiosqlite:///"
    if database.DATABASE_URL.startswith(sqlite_prefix):
        candidates.append(Path(database.DATABASE_URL[len(sqlite_prefix):]))

    backend_dir = Path(__file__).resolve().parent
    project_dir = backend_dir.parent
    candidates.extend([
        backend_dir / "lumina_v2.db",
        backend_dir / "lumina.db",
        project_dir / "lumina_v2.db",
        project_dir / "lumina.db",
    ])

    unique_paths: List[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        key = candidate.as_posix()
        if key in seen or not candidate.exists():
            continue
        seen.add(key)
        unique_paths.append(candidate)
    return unique_paths


@app.get("/admin/export/all")
async def admin_export_all_data(
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(check_admin)
):
    users_result = await db.execute(select(models.User).order_by(models.User.id.asc()))
    users = users_result.scalars().all()
    owner_lookup = {user.id: user.username for user in users}

    projects_result = await db.execute(
        select(models.Project)
        .options(selectinload(models.Project.scenes))
        .order_by(models.Project.id.asc())
    )
    projects = projects_result.scalars().all()

    login_logs_result = await db.execute(
        select(models.LoginLog)
        .order_by(models.LoginLog.timestamp.desc(), models.LoginLog.id.desc())
    )
    login_logs = login_logs_result.scalars().all()

    ai_logs_result = await db.execute(
        select(models.AIInteractionLog)
        .order_by(models.AIInteractionLog.timestamp.desc(), models.AIInteractionLog.id.desc())
    )
    ai_logs = ai_logs_result.scalars().all()

    export_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"luminascript_admin_export_{export_time}.zip"

    manifest = {
        "exported_at": datetime.now().isoformat(),
        "exported_by": admin.username,
        "counts": {
            "users": len(users),
            "projects": len(projects),
            "login_logs": len(login_logs),
            "ai_logs": len(ai_logs),
        },
        "database_files": [path.name for path in _iter_export_database_paths()],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(
            "users.json",
            json.dumps(
                [
                    {
                        "id": user.id,
                        "username": user.username,
                        "is_admin": bool(user.is_admin),
                    }
                    for user in users
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "projects.json",
            json.dumps(
                [_serialize_admin_project(project, owner_lookup) for project in projects],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "login_logs.json",
            json.dumps(
                [_serialize_admin_login_log(log, owner_lookup.get(log.user_id, "")) for log in login_logs],
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "ai_logs.json",
            json.dumps(
                [_serialize_admin_ai_log(log, owner_lookup.get(log.user_id, "")) for log in ai_logs],
                ensure_ascii=False,
                indent=2,
            ),
        )

        for db_path in _iter_export_database_paths():
            archive.write(db_path, arcname=f"database/{db_path.name}")

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(archive_name)}"}
    )

# --- Auth Routes ---

@app.post("/token", response_model=schemas.Token)
async def login_for_access_token(
    request: Request,
    background_tasks: BackgroundTasks,
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: AsyncSession = Depends(get_db)
):
    logger.info(f"收到登录请求: 用户名={form_data.username}")
    # 1. Fetch user
    result = await db.execute(select(models.User).where(models.User.username == form_data.username))
    user = result.scalars().first()
    
    # 获取真实IP (X-Forwarded-For 优先)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host
    
    # User Agent
    user_agent = request.headers.get("user-agent", "")
    
    # 2. Verify
    if not user:
        logger.warning(f"登录失败: 用户 {form_data.username} 不存在")
        # Log failed attempt (No user_id, use 0 or distinct log)
        # For simplicity, we skip logging unknown users or we need to change model to allow nullable user_id
    elif not auth.verify_password(form_data.password, user.hashed_password):
        logger.warning(f"登录失败: 用户 {form_data.username} 密码错误")
        background_tasks.add_task(log_login, user_id=user.id, ip=ip, status="failed", user_agent_str=user_agent)
        
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"用户 {form_data.username} 登录成功")
    background_tasks.add_task(log_login, user_id=user.id, ip=ip, status="success", user_agent_str=user_agent)
    
    # 3. Create Token
    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/register", response_model=schemas.UserResponse)
async def register(user: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    # Check existing
    result = await db.execute(select(models.User).where(models.User.username == user.username))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Create
    hashed_pw = auth.get_password_hash(user.password)
    new_user = models.User(username=user.username, hashed_password=hashed_pw)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@app.get("/users/me", response_model=schemas.UserResponse)
async def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user

# --- Project Management ---

@app.post("/projects/", response_model=schemas.ProjectResponse)
async def create_project(
    project: schemas.ProjectCreate, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    logger.info(f"用户 {current_user.username} 正在创建新项目，Logline: {project.logline[:50]}...")
    # 1. First step: Create the project record based on logline
    # Real implementation would call LLM here to analyze logline first, 
    # but for now we just save it.
    new_project = models.Project(
        title=project.title,
        logline=project.logline,
        project_type=project.project_type,
        owner_id=current_user.id
    )
    db.add(new_project)
    await db.commit()
    await db.refresh(new_project)
    
    logger.info(f"项目创建成功 ID: {new_project.id}")

    # Reload to ensure relationships (scenes) are loaded for Pydantic
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == new_project.id)
        .options(selectinload(models.Project.scenes))
    )
    return result.scalars().first()

@app.get("/projects/", response_model=List[schemas.ProjectListResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    result = await db.execute(
        select(models.Project)
        .where(models.Project.owner_id == current_user.id)
        .order_by(models.Project.id.desc())
    )
    projects = result.scalars().all()
    title_updated = False
    for project in projects:
        title_updated = normalize_project_title(project) or title_updated
        title_updated = normalize_project_context(project) or title_updated

    if title_updated:
        await db.commit()

    return projects


@app.get("/projects/{project_id}", response_model=schemas.ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .where(models.Project.owner_id == current_user.id)
        .options(selectinload(models.Project.scenes))
    )
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    normalized = False
    if normalize_project_title(project):
        normalized = True
    if normalize_project_context(project):
        normalized = True
    if normalized:
        await db.commit()

    return project


@app.delete("/projects/{project_id}")
async def delete_project(
    project_id: int, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    project = await db.get(models.Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Mark as failed/deleted to stop background tasks
    project.status = models.ProcessingStatus.FAILED 
    await db.delete(project)
    await db.commit()
    return {"status": "success"}

@app.patch("/projects/{project_id}", response_model=schemas.ProjectResponse)
async def update_project(
    project_id: int,
    project_update: schemas.ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Use select with options to eager load scenes to avoid MissingGreenlet error in response validation
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(selectinload(models.Project.scenes))
    )
    project = result.scalars().first()

    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project_update.project_type:
        project.project_type = project_update.project_type
    
    await db.commit()
    await db.refresh(project)
    return project

class InteractionRequest(BaseModel):
    answer: str
    context_key: str

class ContentReviewRequest(BaseModel):
    text: str

@app.post("/content/review")
async def review_content(
    payload: ContentReviewRequest,
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Review free user text and return whether it should be rewritten,
    plus an AI-generated safe rewrite suggestion.
    """
    try:
        result = await llm.review_user_input(payload.text)
        return result
    except Exception as e:
        logger.error(f"Content review failed: {e}")
        raise HTTPException(status_code=503, detail="Content review service unavailable")

@app.post("/projects/{project_id}/interact")
async def submit_interaction(
    project_id: int,
    interaction: InteractionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    logger.info(f"收到项目 {project_id} 的交互回答: Key={interaction.context_key}, Answer={interaction.answer}")
    
    result = await db.execute(
        select(models.Project).where(models.Project.id == project_id)
    )
    project = result.scalars().first()
    
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Update context
    # Note: sqlalchemy JSON field needs reassignment to trigger update
    current_context = dict(project.global_context) if project.global_context else {}
    previous_title = project.title
    
    # Special Handling: Reset
    answer_text = (interaction.answer or "").strip()
    if interaction.context_key == 'final_confirm' and answer_text == 'reset':
        logger.info(f"项目 {project_id} 收到重置请求，清空上下文重新开始设定流程")
        project.global_context = {}
        project.next_step_cache = None
        project.project_type = "pending"
        await db.commit()
        return {"status": "reset", "context": {}}

    if interaction.context_key == 'final_confirm' and answer_text.startswith('edit:'):
        target_key = answer_text.split(':', 1)[1].strip()
        rewind_project_setup(project, target_key)
        await db.commit()
        return {
            "status": "rewind",
            "context": project.global_context,
            "title": project.title or previous_title or "",
        }

    if interaction.context_key == 'final_confirm' and answer_text not in FINAL_CONFIRM_ALLOWED_VALUES:
        raise HTTPException(status_code=400, detail="请直接点击下方按钮选择确认操作，再重新发起。")

    # Ensure project_type is synced if that was the key (legacy support)
    if interaction.context_key == 'project_type':
        project.project_type = answer_text
    
    # Handle Title Update specifically
    if interaction.context_key == 'title':
        logger.info(f"Checking title update. Proposed Title: '{answer_text}'")
        clean_title = extract_story_title(answer_text)
        if clean_title:
            current_context[interaction.context_key] = clean_title
            project.title = clean_title
            logger.info(f"Project Title Updated to: {project.title}")
        else:
             current_context.pop(interaction.context_key, None)
             logger.warning(f"Ignored suspicious title update: {answer_text}")
    else:
        current_context[interaction.context_key] = answer_text

    project.global_context = current_context

    # Clear the cache because state has changed
    project.next_step_cache = None

    await db.commit()
    logger.info(f"项目 {project_id} 上下文已更新，缓存已清除")
    return {
        "status": "updated",
        "context": project.global_context,
        "title": project.title or previous_title or "",
    }


@app.post("/projects/{project_id}/analyze")
async def analyze_logline(
    project_id: int, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Phase 1: Deep Analysis & Setup.
    Iteratively helps the user build the 'Project Bible' by asking questions.
    """
    project = await db.get(models.Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    normalized = False
    if normalize_project_title(project):
        normalized = True
    if normalize_project_context(project):
        normalized = True
    if normalized:
        await db.commit()

    normalized_context = build_normalized_context(project)

    if should_auto_prefill_from_logline(project, normalized_context):
        filled_fields: List[str] = []
        prefill_changed = False
        prefill_usage = 0
        extracted_setup: Dict[str, Any] = {}

        try:
            extracted_setup, prefill_usage = await llm.extract_setup_from_long_input(project.logline or "")
            filled_fields, prefill_changed = apply_auto_prefill(project, extracted_setup)
        except Exception as exc:
            logger.warning(f"Failed to auto-prefill setup from long logline for project {project_id}: {exc}")
            filled_fields, prefill_changed = apply_auto_prefill(project, {})

        if prefill_usage:
            project.total_tokens += prefill_usage
            background_tasks.add_task(
                log_ai_action,
                user_id=current_user.id,
                project_id=project_id,
                action="auto_prefill_setup",
                prompt=project.logline or "",
                response=json.dumps(extracted_setup, ensure_ascii=False),
                tokens=prefill_usage
            )

        if filled_fields:
            logger.info(f"项目 {project_id} 已从长输入自动补全字段: {', '.join(filled_fields)}")
            project.next_step_cache = None

        if prefill_changed or prefill_usage:
            await db.commit()

        normalized_context = build_normalized_context(project)

    # Check Cache First (For resuming sessions)
    if project.next_step_cache:
        if should_invalidate_cached_question(project.next_step_cache, normalized_context):
            project.next_step_cache = None
            await db.commit()
        else:
            logger.info(f"项目 {project_id} 命中缓存，直接返回之前的提问。")
            return project.next_step_cache

    logger.info(f"正在分析项目 {project_id} 的进度状况...")

    context = dict(project.global_context) if isinstance(project.global_context, dict) else {}

    # 1. Check which steps are missing
    # Important: 'project_type' is stored in column, others in global_context
    normalized_context = build_normalized_context(project)
    
    # Calculate Total Steps (Dynamic based on Type)
    p_type = normalized_context.get("project_type", "movie")
    relevant_steps = get_relevant_setup_steps(p_type)

    next_step = None
    next_step_index = 0
    total_steps = len(relevant_steps)

    for i, step in enumerate(relevant_steps):
        if step["key"] not in normalized_context:
            next_step = step
            next_step_index = i + 1
            break
            
    # 2. If all steps completed -> Proceed to Outline Generation
    if not next_step:
        logger.info(f"项目 {project_id} 所有基础设定步骤已完成，准备生成大纲。")
        return {"type": "completed", "message": "基础设定已完成！准备生成大纲..."}

    logger.info(f"项目 {project_id} 下一步骤: {next_step['key']} ({next_step_index}/{total_steps})")
    
    # helper to inject progress info
    def add_progress(payload):
        payload["progress"] = {"current": next_step_index, "total": total_steps}
        return payload

    # 3. Handle specific logic for the next step
    # 3.1 Hardcoded options for Type
    if next_step["key"] == "project_type":
        return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "project_type",
                "question": next_step["question"],
                "options": next_step["default_options"]
            })
        }
    
    # 3.2 Hardcoded options for Episode Count / Movie Duration / Scene Count
    if next_step["key"] == "movie_duration":
         return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "movie_duration",
                "question": next_step["question"],
                "options": [
                    {"label": "90分钟 (标准电影)", "value": "90"},
                    {"label": "120分钟 (长篇商业片)", "value": "120"},
                    {"label": "150分钟以上 (史诗篇幅)", "value": "150"},
                    {"label": "60分钟 (中片/电视电影)", "value": "60"}
                ]
            })
        }

    if next_step["key"] == "scene_count_target":
         return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "scene_count_target",
                "question": next_step["question"],
                "options": [
                    {"label": "40场 (简约大纲)", "value": "40"},
                    {"label": "60场 (标准大纲)", "value": "60"},
                    {"label": "100场 (精细大纲)", "value": "100"},
                    {"label": "120场以上 (极度详尽)", "value": "120"}
                ]
            })
        }

    if next_step["key"] == "episode_count":
         return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "episode_count",
                "question": next_step["question"],
                "options": [
                    {"label": "8集 (迷你剧)", "value": "8"},
                    {"label": "12集 (标准季)", "value": "12"},
                    {"label": "20集 (国产剧标准)", "value": "20"},
                    {"label": "24集", "value": "24"},
                    {"label": "40集以上", "value": "40"}
                ]
            })
        }
    
    if next_step["key"] == "episode_duration":
         return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "episode_duration",
                "question": next_step["question"],
                "options": [
                    {"label": "1-2分钟 (竖屏短剧)", "value": "2mins"},
                    {"label": "5-10分钟 (迷你剧)", "value": "10mins"},
                    {"label": "20分钟 (情景喜剧/动画)", "value": "20mins"},
                    {"label": "45分钟 (标准剧集)", "value": "45mins"},
                    {"label": "60分钟 (美剧/电影感)", "value": "60mins"}
                ]
            })
        }
    
    if next_step.get("is_confirmation"):
        normalized_context = await ensure_story_synopsis(project, normalized_context)
        if project.project_type and project.project_type != "pending":
            normalized_context["project_type"] = project.project_type

        summary_text = build_context_summary(project, normalized_context)
        await db.commit()
        return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "final_confirm",
                "question": next_step["question"],
                "context_summary": summary_text,
                "options": [
                    {"label": "✅ 确定并开始生成", "value": "confirmed"},
                    *[
                        {"label": label, "value": f"edit:{target_key}"}
                        for target_key, label in FINAL_CONFIRM_EDIT_TARGETS
                    ],
                    {"label": "🔄 重新设定 (清空当前设定重头开始)", "value": "reset"}
                ]
            })
        }

    if next_step["key"] == "video_duration_seconds":
         return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "video_duration_seconds",
                "question": next_step["question"],
                "options": [
                    {"label": "15秒（1条提示词）", "value": "15"},
                    {"label": "30秒（2条提示词）", "value": "30"},
                    {"label": "45秒（3条提示词）", "value": "45"},
                    {"label": "60秒（4条提示词）", "value": "60"},
                    {"label": "90秒（6条提示词）", "value": "90"},
                    {"label": "120秒（8条提示词）", "value": "120"}
                ]
            })
        }
    
    # 3.4 Check Prompt Richness (Optimization)
    # If the user's initial logline is very long (> 100 chars) and detailed,
    # we tell the LLM to verify if we even need to ask this question.
    # Note: Currently we just proceed to ask to be comprehensive.
    
    # 3.4 For other steps, use LLM to generate context-aware options
    # We pass the logline + current context to LLM
    prompt_context = f"Logline: {project.logline}\nCurrent Settings: {json.dumps(normalized_context, ensure_ascii=False)}"
    
    logger.info(f"正在调用 LLM 为步骤 {next_step['key']} 生成选项...")
    
    # 3.2 For other steps, use LLM to generate context-aware options
    attempt = 0
    question_data = None
    usage = 0
    while True:
        attempt += 1
        try:
            question_data, usage = await llm.generate_interaction_options(
                step_key=next_step["key"],
                base_question=next_step["question"],
                context_str=prompt_context
            )
            background_tasks.add_task(
                log_ai_action,
                user_id=current_user.id,
                project_id=project_id,
                action=f"analyze_step_{next_step['key']}",
                prompt=prompt_context,
                response=str(question_data),
                tokens=usage,
                status="success",
                step_key=next_step["key"],
                attempt=attempt,
            )
            break
        except Exception as e:
            raw_content = str(getattr(e, "raw_content", "") or "")
            error_type = str(getattr(e, "error_type", type(e).__name__) or type(e).__name__)
            error_message = str(e)
            wait_seconds = min(30, 2 * attempt)

            logger.error(
                f"LLM 交互生成失败: step={next_step['key']} attempt={attempt} "
                f"error_type={error_type} error={error_message}"
            )

            await log_ai_action(
                user_id=current_user.id,
                project_id=project_id,
                action=f"analyze_step_{next_step['key']}",
                prompt=prompt_context,
                response=raw_content,
                tokens=0,
                status="failed",
                step_key=next_step["key"],
                error_type=error_type,
                error_message=error_message,
                attempt=attempt,
            )

            logger.warning(
                f"项目 {project_id} 的步骤 {next_step['key']} 第 {attempt} 次生成失败，"
                f"{wait_seconds} 秒后自动重试。"
            )
            await asyncio.sleep(wait_seconds)
    
    # Update Token Usage
    project.total_tokens += usage
    
    # Construction Response
    response_payload = {
        "type": "interaction_required",
        "payload": add_progress({
            "field": next_step["key"],
            "question": question_data.get("question", next_step["question"]), 
            "options": question_data.get("options", [])
        })
    }

    if next_step["key"] == "title":
        response_payload["payload"]["options"] = sanitize_title_options(
            response_payload["payload"].get("options", [])
        )
    
    # Cache the result to DB so next fetch is instant
    project.next_step_cache = response_payload
    await db.commit()

    return response_payload

@app.post("/projects/{project_id}/generate_scenes")
async def generate_scenes(
    project_id: int, 
    selected_option: str = None, 
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Phase 1.5: User selected an option, now generate outline.
    Phase 2: Add background task for generation.
    """
    logger.info(f"收到生成分场大纲请求，项目ID: {project_id}")
    # 1. Update project genre/style based on selected_option
    project = await db.get(models.Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    
    c = await ensure_story_synopsis(project, project.global_context or {})
    project.global_context = c

    # Use selected_option if string generic, or fallback to stored context values
    style_context = selected_option
    if not style_context:
        # Construct summary from context
        style_context = f"Genre: {project.project_type}, Tone: {c.get('tone')}, Style: {c.get('visual_style')}"

    # Extract target episode count / scene count from context
    target_count = 5
    duration_seconds = 0
    
    # Priority for Movie: scene_count_target
    if project.project_type == "movie":
        raw_count = c.get("scene_count_target")
    elif project.project_type == "short_video":
        raw_count = c.get("video_duration_seconds")
    else:
        raw_count = c.get("episode_count")

    if raw_count:
        try:
            if isinstance(raw_count, int):
                target_count = raw_count
            elif isinstance(raw_count, str):
                import re
                # Try to find first number
                digits = re.findall(r'\d+', raw_count)
                if digits:
                    target_count = int(digits[0])
        except Exception as e:
            logger.warning(f"Error parsing count: {e}")

    if project.project_type == "short_video":
        if raw_count:
            try:
                duration_seconds = int(re.findall(r"\d+", str(raw_count))[0])
            except Exception:
                duration_seconds = 0
        if duration_seconds <= 0:
            duration_seconds = 60
        target_count = max(1, math.ceil(duration_seconds / 15))
            
    # If movie duration is set but scene count isn't, estimate
    if project.project_type == "movie" and not c.get("scene_count_target"):
        duration = c.get("movie_duration")
        if duration:
            try:
                # 1.5 scenes per minute is a high-detail script, 0.5 is low. 1.0 is standard.
                target_count = int(int(re.findall(r'\d+', str(duration))[0]) * 0.8)
            except: pass

    project.genre = style_context
    project.status = models.ProcessingStatus.GENERATING
    # Force clearing of any old scenes from a previous attempt
    await db.execute(delete(models.Scene).where(models.Scene.project_id == project_id))
    await db.commit()

    if project.project_type == "short_video":
        style_context = (
            f"{style_context}; 模式:短视频15秒分段提示词; 总时长:{duration_seconds}秒;"
            f" 需要生成{target_count}条15秒提示词"
        )

    logger.info(f"启动后台任务生成大纲... (Style: {style_context}, Count: {target_count})")
    
    # 2. Trigger Background Task for Incremental Outline Generation
    background_tasks.add_task(
        run_incremental_outline_generation, 
        project_id, 
        style_context, 
        target_count,
        current_user.id
    )
    
    return {"status": "Scene generation started", "project_id": project_id}

# --- Background Task Implementation ---
from sqlalchemy import delete

async def run_incremental_outline_generation(project_id: int, style_context: str, target_count: int, user_id: int):
    logger.info(f"[Task] Starting Incremental Outline Gen for Project {project_id}")
    
    async with database.SessionLocal() as db:
        project = await db.get(models.Project, project_id)
        if not project: return
        
        # Determine Batch Size (User requested "safe/one-by-one", so we choose 1 to be absolutely safe and responsive)
        # Using 1 allows frontend to see each scene pop up.
        batch_size = 1 
        current_idx = 1
        last_context = "Start of story."
        
        while current_idx <= target_count:
            # Re-check status in case user cancelled
            await db.refresh(project)
            if project.status == models.ProcessingStatus.FAILED:
                logger.info("[Task] Outline Gen Cancelled.")
                return 

            end_idx = min(current_idx + batch_size - 1, target_count)
            logger.info(f"[Task] Generating scenes {current_idx}-{end_idx}...")
            
            try:
                batch_scenes, usage = await llm.generate_scene_batch(
                    project.logline, 
                    style_context, 
                    current_idx, 
                    end_idx, 
                    previous_context=last_context,
                    total_target=target_count
                )
                
                project.total_tokens += usage
                
                # If success, save to DB immediately
                if batch_scenes:
                    # Logic Fix: Enforce strictly sequential indexing based on loop counter.
                    # Do not trust LLM returned 'index' property to avoid duplicates if LLM resets to 1.
                    offset = 0
                    for s_data in batch_scenes:
                        calculated_index = current_idx + offset
                        new_scene = models.Scene(
                            project_id=project.id,
                            scene_index=calculated_index, 
                            outline=s_data.get("outline", "Unknown"),
                            status=models.ProcessingStatus.PENDING
                        )
                        db.add(new_scene)
                        offset += 1
                    
                    # Update context for next batch
                    summaries = [s.get('outline', '') for s in batch_scenes]
                    last_context = "; ".join(summaries) # Keep it short
                else:
                    # Fallback for empty/failure
                    logger.error(f"[Task] Batch {current_idx} failed.")
                    new_scene = models.Scene(
                        project_id=project.id,
                        scene_index=current_idx,
                        outline="[生成失败] 请稍后尝试重写此场。",
                        status=models.ProcessingStatus.PENDING
                    )
                    db.add(new_scene)

                await db.commit()
                
            except Exception as e:
                logger.error(f"[Task] Critical error in outline batch: {e}")
            
            current_idx += batch_size
        
        # After Outline Complete -> Trigger Content Generation
        logger.info("[Task] Outline Complete. Starting Content Gen Loop...")
        # Since we are not in a request scope, we can't use BackgroundTasks object easily to chain.
        # But we can just await the next function directly since we are already in an async background loop.
        await run_generation_loop(project.id)


@app.post("/projects/{project_id}/scenes/{scene_index}/regenerate")
async def regenerate_scene(
    project_id: int, 
    scene_index: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    project = await db.get(models.Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
        
    result = await db.execute(
        select(models.Scene)
        .where(models.Scene.project_id == project_id)
        .where(models.Scene.scene_index == scene_index)
    )
    scene = result.scalars().first()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
        
    # Reset status
    scene.status = models.ProcessingStatus.PENDING
    scene.content = None # Clear old content
    if project.status == models.ProcessingStatus.COMPLETED:
        project.status = models.ProcessingStatus.GENERATING
        
    await db.commit()
    
    # Trigger loop again
    background_tasks.add_task(run_generation_loop, project.id)
    return {"status": "Regeneration scheduled"}

# --- Export (New) ---
import io
# Try imports, fallback to plain text if failed
try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except ImportError:
    canvas = None

from fastapi.responses import StreamingResponse

@app.get("/projects/{project_id}/export")
async def export_project(
    project_id: int, 
    format: str = "txt",
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Eager load scenes
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(selectinload(models.Project.scenes))
    )
    project = result.scalars().first()
    
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    filename_raw = project.title or 'Untitled_Script'
    filename_encoded = quote(filename_raw)
    
    # Prepare Content Data
    project_scenes = sorted(project.scenes, key=lambda s: s.scene_index)
    context = project.global_context or {}
    
    if format == "docx":
        if not DocxDocument:
            raise HTTPException(501, "Word export library (python-docx) not installed on server.")
        
        doc = DocxDocument()
        doc.add_heading(project.title or "Untitled", 0)
        
        doc.add_heading("Project Bible", level=1)
        doc.add_paragraph(f"Logline: {project.logline}")
        doc.add_paragraph(f"Type: {project.project_type} | Genre: {project.genre}")
        for k, v in context.items():
            if k not in ['logline', 'project_type']:
                try:
                     doc.add_paragraph(f"{str(k).capitalize()}: {str(v)}")
                except:
                     pass
                
        doc.add_page_break()
        doc.add_heading("Screenplay", level=1)
        
        for scene in project_scenes:
            doc.add_heading(f"SCENE {scene.scene_index}", level=2)
            doc.add_paragraph(f"Outline: {scene.outline}", style='Intense Quote')
            if scene.content:
                # Basic formatting for script
                doc.add_paragraph(scene.content)
            else:
                doc.add_paragraph("[Content Generating...]")
            doc.add_paragraph("") # Spacing
            
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer, 
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}.docx"}
        )

    elif format == "md":
        content = f"# {project.title or 'Untitled'}\n\n"
        content += f"**Logline:** {project.logline}\n\n"
        content += f"**Type:** {project.project_type}\n"
        content += "---\n\n## Project Settings\n"
        for k, v in context.items():
             content += f"- **{k}:** {v}\n"
        content += "\n---\n\n## Script\n\n"
        
        for scene in project_scenes:
            content += f"### SCENE {scene.scene_index}\n"
            content += f"> **Outline:** {scene.outline}\n\n"
            content += (scene.content or "[Generating...]") + "\n\n"
            content += "---\n\n"
            
        return StreamingResponse(
            io.BytesIO(content.encode('utf-8')),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}.md"}
        )
        
    else: # Default TXT
        content = f"Title: {project.title}\nLogline: {project.logline}\n\n"
        for scene in project_scenes:
            content += f"SCENE {scene.scene_index}\n{scene.content or ''}\n\n"
        return StreamingResponse(
            io.BytesIO(content.encode('utf-8')),
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}.txt"}
        )

# --- Background Task (The Engine) ---

async def run_generation_loop(project_id: int):
    """
    The Core Loop: Iterates scenes and generates content with Rolling Summary.
    """
    logger.info(f"[后台任务] 开始为项目 {project_id} 生成剧本内容...")
    
    # Create a new session for the background task
    async with database.SessionLocal() as db:
        # Load Project Info
        project = await db.get(models.Project, project_id)
        if not project: 
            logger.error(f"[后台任务] 项目 {project_id} 未找到，任务中止")
            return

        # Load scenes
        result = await db.execute(
            select(models.Scene)
            .where(models.Scene.project_id == project_id)
            .order_by(models.Scene.scene_index)
        )
        scenes = result.scalars().all()
        
        cumulative_context = ""

        for scene in scenes:
            # Re-Check Status (User might have deleted/paused)
            await db.refresh(project)
            if project.status == models.ProcessingStatus.FAILED: # Treat as stop signal
                logger.info("[后台任务] 检测到停止信号，任务中止")
                break

            if scene.status == models.ProcessingStatus.COMPLETED:
                continue # Skip already generated

            # 1. Mark as Generating
            logger.info(f"[后台任务] 正在生成第 {scene.scene_index} 场: {scene.outline[:30]}...")
            scene.status = models.ProcessingStatus.GENERATING
            await db.commit()
            
            # 2. Call LLM to Write Scene
            if project.project_type == "short_video":
                generated_content, usage = await llm.write_short_video_prompt(
                    logline=project.logline,
                    style_guide=project.genre,
                    current_scene_outline=scene.outline,
                    clip_index=scene.scene_index,
                    previous_context=cumulative_context
                )
            else:
                generated_content, usage = await llm.write_scene_content(
                    logline=project.logline,
                    style_guide=project.genre,
                    current_scene_outline=scene.outline,
                    previous_context=cumulative_context
                )
            
            project.total_tokens += usage

            # Log AI Action (Direct call since we are already in background)
            await log_ai_action(
                user_id=project.owner_id,
                project_id=project.id,
                action=f"write_scene_{scene.scene_index}",
                prompt=f"Outline: {scene.outline}, PrevContextLength: {len(cumulative_context)}",
                response=generated_content if generated_content else "Error/Empty",
                tokens=usage
            )

            # 3. Update Content
            if generated_content:
                scene.content = generated_content
                # Simple rolling context for now (first 200 chars to avoid token limit in basic version)
                cumulative_context += f"\n[Scene {scene.scene_index} Summary]: {scene.outline}" 
                logger.info(f"[后台任务] 第 {scene.scene_index} 场生成完成")
            else:
                scene.content = "(AI Generation Failed)"
                logger.error(f"[后台任务] 第 {scene.scene_index} 场生成内容为空")

            scene.status = models.ProcessingStatus.COMPLETED
            await db.commit()
        
        # Mark Project Complete
        project.status = models.ProcessingStatus.COMPLETED
        await db.commit()
        logger.info(f"[后台任务] 项目 {project_id} 所有剧本生成任务完成！")
            
    print(f"Generation loop finished for Project {project_id}")

import database # Import at end to avoid circular dependency issues in loop if needed
