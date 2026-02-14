from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Dict, Any
from pydantic import BaseModel 
import json

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

async def log_login(user_id: int, ip: str, status: str):
    async with SessionLocal() as db:
        log = models.LoginLog(
             user_id=user_id, 
             ip_address=ip, 
             status=status, 
             timestamp=datetime.now().isoformat()
        )
        db.add(log)
        await db.commit()

async def log_ai_action(user_id: int, project_id: int, action: str, prompt: str, response: str, tokens: int):
    async with SessionLocal() as db:
        log = models.AIInteractionLog(
            user_id=user_id,
            project_id=project_id,
            action=action,
            prompt=prompt[:5000],  # Truncate if too long to save generic DB space
            response=response[:5000],
            tokens=tokens,
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
    total = total_result.scalar()

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
        log_dict = log.__dict__
        log_dict['user_name'] = username
        logs.append(log_dict)
        
    return {"total": total, "items": logs}

@app.get("/admin/logs/ai", response_model=schemas.PaginatedAILogs)
async def admin_list_ai_logs(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(check_admin)
):
    offset = (page - 1) * page_size
    
    # 1. Get Total Count
    count_query = select(func.count()).select_from(models.AIInteractionLog)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # 2. Get Items
    result = await db.execute(
        select(models.AIInteractionLog, models.User.username)
        .join(models.User, models.AIInteractionLog.user_id == models.User.id)
        .order_by(models.AIInteractionLog.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    )
    logs = []
    for log, username in result:
        log_dict = log.__dict__
        log_dict['user_name'] = username
        logs.append(log_dict)
        
    return {"total": total, "items": logs}

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
    
    # 2. Verify
    if not user:
        logger.warning(f"登录失败: 用户 {form_data.username} 不存在")
        # Log failed attempt (No user_id, use 0 or distinct log)
        # For simplicity, we skip logging unknown users or we need to change model to allow nullable user_id
    elif not auth.verify_password(form_data.password, user.hashed_password):
        logger.warning(f"登录失败: 用户 {form_data.username} 密码错误")
        background_tasks.add_task(log_login, user_id=user.id, ip=ip, status="failed")
        
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"用户 {form_data.username} 登录成功")
    background_tasks.add_task(log_login, user_id=user.id, ip=ip, status="success")
    
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

@app.get("/projects/", response_model=List[schemas.ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    result = await db.execute(
        select(models.Project)
        .where(models.Project.owner_id == current_user.id)
        .options(selectinload(models.Project.scenes))
    )
    return result.scalars().all()


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
    
    # Special Handling: Reset
    if interaction.context_key == 'final_confirm' and interaction.answer == 'reset':
        logger.info(f"项目 {project_id} 收到重置请求，清空上下文重新开始设定流程")
        project.global_context = {}
        project.next_step_cache = None
        project.project_type = "pending"
        await db.commit()
        return {"status": "reset", "context": {}}

    current_context[interaction.context_key] = interaction.answer
    project.global_context = current_context

    # Ensure project_type is synced if that was the key (legacy support)
    if interaction.context_key == 'project_type':
        project.project_type = interaction.answer
    
    # Handle Title Update specifically
    if interaction.context_key == 'title':
        logger.info(f"Checking title update. Proposed Title: '{interaction.answer}'")
        # Ensure we don't accidentally set the title to the question string if logic failed somewhere
        # Simple heuristic: If it ends with '?', it's likely a mistake.
        if interaction.answer and not interaction.answer.strip().endswith('?'):
            project.title = interaction.answer
            logger.info(f"Project Title Updated to: {project.title}")
        else:
             logger.warning(f"Ignored suspicious title update: {interaction.answer}")
        
    # Clear the cache because state has changed
    project.next_step_cache = None

    await db.commit()
    logger.info(f"项目 {project_id} 上下文已更新，缓存已清除")
    return {"status": "updated", "context": project.global_context}


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

    # Check Cache First (For resuming sessions)
    if project.next_step_cache:
        logger.info(f"项目 {project_id} 命中缓存，直接返回之前的提问。")
        return project.next_step_cache

    logger.info(f"正在分析项目 {project_id} 的进度状况...")

    context = project.global_context or {}
    
    # --- Definition of the 10-Step Setup Flow ---
    # Follow Snowflake Method concepts: Logline -> Expansion -> Characters -> Detailed Plot -> Confirmation
    REQUIRED_STEPS = [
        {"key": "project_type", "question": "您想创作哪种类型的剧本？", "default_options": [
             {"label": "🎥 电影剧本 (Movie)", "value": "movie"},
             {"label": "📺 电视剧 (TV Series)", "value": "tv"},
             {"label": "📱 现代短剧 (Short Drama)", "value": "short"}
        ]},
        # Dynamic steps based on Project Type
        {"key": "movie_duration", "question": "电影预期的时长是多少分钟？", "movie_only": True},
        {"key": "scene_count_target", "question": "您希望生成多少场戏？(电影通常40-100场，精细剧本可能更多)", "movie_only": True},
        {"key": "episode_count", "question": "您计划创作多少集？", "tv_short_only": True},
        {"key": "episode_duration", "question": "每一集的大致时长是？", "tv_short_only": True},
        
        {"key": "tone", "question": "这部作品的基调是什么？"},
        {"key": "time_period", "question": "故事发生在什么时代背景？"},
        {"key": "title", "question": "不管是暂定还是正式，给这个故事起个名字吧？"},
        
        # Snowflake Step 2 & 4: Expansion
        {"key": "story_expansion", "question": "我们需要基于目前的构思扩展出一个完整的三幕式大纲，您有什么特别的想法吗？"},
        
        # Snowflake Step 3 & 5: Character focus
        {"key": "character_details", "question": "主要角色的性格、外貌或背景有什么特别设定？"},
        
        # Detailed plot
        {"key": "plot_details", "question": "有哪些一定要发生的关键情节或转折？"},
        
        {"key": "theme", "question": "您想通过这个故事探讨什么主题？"},
        {"key": "visual_style", "question": "视觉风格偏向于什么？"},
        {"key": "user_notes", "question": "还有什么补充的内容，或者特别的要求吗？"},
        
        # Final confirmation
        {"key": "final_confirm", "question": "以上是剧本的完整设定，请确认是否可以开始生成分场大纲？", "is_confirmation": True}
    ]

    # 1. Check which steps are missing
    # Important: 'project_type' is stored in column, others in global_context
    normalized_context = context.copy()
    if project.project_type and project.project_type != "pending":
        normalized_context['project_type'] = project.project_type
    
    # Calculate Total Steps (Dynamic based on Type)
    relevant_steps = []
    p_type = normalized_context.get("project_type", "movie")
    for step in REQUIRED_STEPS:
         # Filter based on type
         if step.get("movie_only") and p_type != "movie": continue
         if step.get("tv_short_only") and p_type == "movie": continue
         relevant_steps.append(step)

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
        # Format a summary for confirmation
        summary_lines = []
        for k, v in normalized_context.items():
             summary_lines.append(f"- {k}: {v}")
        summary_text = "\n".join(summary_lines)
        return {
            "type": "interaction_required",
            "payload": add_progress({
                "field": "final_confirm",
                "question": next_step["question"],
                "context_summary": summary_text,
                "options": [
                    {"label": "✅ 确定并开始生成", "value": "confirmed"},
                    {"label": "🔄 我想修改一些内容", "value": "reset"}
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
    try:
        question_data, usage = await llm.generate_interaction_options(
            step_key=next_step["key"],
            base_question=next_step["question"],
            context_str=prompt_context
        )
        # Log AI action
        background_tasks.add_task(
            log_ai_action,
            user_id=current_user.id,
            project_id=project_id,
            action=f"analyze_step_{next_step['key']}",
            prompt=prompt_context,
            response=str(question_data),
            tokens=usage
        )
    except Exception as e:
        logger.error(f"LLM 交互生成失败: {e}")
        raise HTTPException(
            status_code=503, 
            detail=f"AI 服务暂时不可用，请检查 API Key 配置或稍后重试 ({str(e)})"
        )
    
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
    
    # Use selected_option if string generic, or fallback to stored context values
    style_context = selected_option
    if not style_context:
        # Construct summary from context
        c = project.global_context or {}
        style_context = f"Genre: {project.project_type}, Tone: {c.get('tone')}, Style: {c.get('visual_style')}"

    # Extract target episode count / scene count from context
    c = project.global_context or {}
    target_count = 5
    
    # Priority for Movie: scene_count_target
    if project.project_type == "movie":
        raw_count = c.get("scene_count_target")
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
                    for s_data in batch_scenes:
                        new_scene = models.Scene(
                            project_id=project.id,
                            scene_index=s_data.get("index", current_idx),
                            outline=s_data.get("outline", "Unknown"),
                            status=models.ProcessingStatus.PENDING
                        )
                        db.add(new_scene)
                    
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
