from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from pydantic import BaseModel 
import json

from database import init_db, get_db
import models
import schemas
import auth
from services import llm  # Import LLM Service
import logging
import sys

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

# --- Auth Routes ---

@app.post("/token", response_model=schemas.Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    logger.info(f"收到登录请求: 用户名={form_data.username}")
    # 1. Fetch user
    result = await db.execute(select(models.User).where(models.User.username == form_data.username))
    user = result.scalars().first()
    
    # 2. Verify
    if not user:
        logger.warning(f"登录失败: 用户 {form_data.username} 不存在")
    elif not auth.verify_password(form_data.password, user.hashed_password):
        logger.warning(f"登录失败: 用户 {form_data.username} 密码错误")
        
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"用户 {form_data.username} 登录成功")
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
    current_context[interaction.context_key] = interaction.answer
    project.global_context = current_context

    # Ensure project_type is synced if that was the key (legacy support)
    if interaction.context_key == 'project_type':
        project.project_type = interaction.answer
    
    # Handle Title Update specifically
    if interaction.context_key == 'title':
        project.title = interaction.answer
        
    # Clear the cache because state has changed
    project.next_step_cache = None

    await db.commit()
    logger.info(f"项目 {project_id} 上下文已更新，缓存已清除")
    return {"status": "updated", "context": project.global_context}


@app.post("/projects/{project_id}/analyze")
async def analyze_logline(
    project_id: int, 
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
    REQUIRED_STEPS = [
        {"key": "project_type", "question": "您想创作哪种类型的剧本？", "default_options": [
             {"label": "🎥 电影剧本 (Movie)", "value": "movie"},
             {"label": "📺 电视剧 (TV Series)", "value": "tv"},
             {"label": "📱 现代短剧 (Short Drama)", "value": "short"}
        ]},
        {"key": "tone", "question": "这部作品的基调是什么？"},
        {"key": "time_period", "question": "故事发生在什么时代背景？"},
        {"key": "title", "question": "不管是暂定还是正式，给这个故事起个名字吧？"},
        {"key": "protagonist_core", "question": "主角的核心特征或最大欲望是什么？"},
        {"key": "antagonist_obstacle", "question": "主角面临的最大阻碍或反派是谁？"},
        {"key": "central_conflict", "question": "故事的核心冲突或两难困境是什么？"},
        {"key": "theme", "question": "您想通过这个故事探讨什么主题？"},
        {"key": "visual_style", "question": "视觉风格偏向于什么？（如：赛博朋克、写实、黑白诺尔等）"},
        {"key": "target_audience", "question": "您预想的目标观众是谁？"}
    ]

    # 1. Check which steps are missing
    # Important: 'project_type' is stored in column, others in global_context
    normalized_context = context.copy()
    if project.project_type and project.project_type != "pending":
        normalized_context['project_type'] = project.project_type

    next_step = None
    for step in REQUIRED_STEPS:
        if step["key"] not in normalized_context:
            next_step = step
            break
            
    # 2. If all steps completed -> Proceed to Outline Generation
    if not next_step:
        logger.info(f"项目 {project_id} 所有基础设定步骤已完成，准备生成大纲。")
        # Check if Outline exists, if not, generate it
        # return {"type": "complete", "message": "Bible complete. Ready for Outline."}
        # For now, let's trigger scene generation or "outline confirmation"
        return {"type": "completed", "message": "基础设定已完成！准备生成大纲..."}

    logger.info(f"项目 {project_id} 下一步骤: {next_step['key']}")

    # 3. Handle specific logic for the next step
    # 3.1 Hardcoded options for Type
    if next_step["key"] == "project_type":
        return {
            "type": "interaction_required",
            "payload": {
                "field": "project_type",
                "question": next_step["question"],
                "options": next_step["default_options"]
            }
        }

    # 3.2 For other steps, use LLM to generate context-aware options
    # We pass the logline + current context to LLM
    prompt_context = f"Logline: {project.logline}\nCurrent Settings: {json.dumps(normalized_context, ensure_ascii=False)}"
    
    logger.info(f"正在调用 LLM 为步骤 {next_step['key']} 生成选项...")
    
    # 3.2 For other steps, use LLM to generate context-aware options
    question_data, usage = await llm.generate_interaction_options(
        step_key=next_step["key"],
        base_question=next_step["question"],
        context_str=prompt_context
    )
    
    # Update Token Usage
    project.total_tokens += usage
    
    # Construction Response
    response_payload = {
        "type": "interaction_required",
        "payload": {
            "field": next_step["key"],
            "question": question_data.get("question", next_step["question"]), 
            "options": question_data.get("options", [])
        }
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

    project.genre = style_context
    await db.commit()

    logger.info(f"正在调用 LLM 生成分场大纲... (Style: {style_context})")
    # 2. Real: Generate Scene Outline using LLM
    scenes_data, usage = await llm.generate_outline(project.logline, style_context)
    project.total_tokens += usage
    
    if not scenes_data:
        logger.error("分场大纲生成失败或为空")
        # Fallback
        scenes_data = [{"index": 1, "outline": "Start: Intro Protagonist"}]
    else:
        logger.info(f"成功生成 {len(scenes_data)} 个分场")

    # Clear existing scenes if any (cleanup for retry)
    # Note: For basic version, we just append. Advanced: delete old.
    
    for scene_item in scenes_data:
        new_scene = models.Scene(
            project_id=project.id,
            scene_index=scene_item.get("index", 1),
            outline=scene_item.get("outline", "Unknown Scene"),
            status=models.ProcessingStatus.PENDING
        )
        db.add(new_scene)
    
    project.status = models.ProcessingStatus.GENERATING
    await db.commit()
    
    # 3. Trigger Background Loop (Concept)
    logger.info(f"启动后台任务生成具体的剧本内容...")
    background_tasks.add_task(run_generation_loop, project.id)
    
    return {"status": "Scene generation started", "project_id": project_id}

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
