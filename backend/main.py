from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from database import init_db, get_db
import models
import schemas

# Initialize App
app = FastAPI(title="LuminaScript API", version="0.1.0")

@app.on_event("startup")
async def on_startup():
    await init_db()

@app.get("/")
async def root():
    return {"message": "Welcome to LuminaScript API"}

# --- Project Management ---

@app.post("/projects/", response_model=schemas.ProjectResponse)
async def create_project(project: schemas.ProjectCreate, db: AsyncSession = Depends(get_db)):
    # 1. First step: Create the project record based on logline
    # Real implementation would call LLM here to analyze logline first, 
    # but for now we just save it.
    new_project = models.Project(
        title=project.title,
        logline=project.logline
    )
    db.add(new_project)
    await db.commit()
    await db.refresh(new_project)
    return new_project

@app.post("/projects/{project_id}/analyze")
async def analyze_logline(project_id: int, db: AsyncSession = Depends(get_db)):
    """
    Phase 1: Mock for LLM Analysis.
    Should return a JSON that triggers the Frontend Interaction.
    """
    project = await db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Mock response complying with "Interaction Protocol"
    return {
        "type": "interaction_required",
        "payload": {
            "question": "请确认剧本规格与风格 (Based on Logline: " + project.logline[:20] + "...)",
            "options": [
                {"label": "爽文短剧 (100集, 快节奏)", "value": "short_drama_100"},
                {"label": "电影剧本 (90分钟, 英雄旅程)", "value": "movie_standard"},
                {"label": "电视剧 (12集, 悬疑风格)", "value": "tv_series_suspense"}
            ]
        }
    }

@app.post("/projects/{project_id}/generate_scenes")
async def generate_scenes(project_id: int, selected_option: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    Phase 1.5: User selected an option, now generate outline.
    Phase 2: Add background task for generation.
    """
    # 1. Update project genre/style based on selected_option
    project = await db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.genre = selected_option
    await db.commit()

    # 2. Mock: Generate Scene Outline (In reality, LLM does this)
    # We will insert dummy scenes to test the loop later.
    dummy_outlines = [
        f"Scene {i}: 主角遭遇挑战 {i}" for i in range(1, 6)
    ]
    
    for idx, outline in enumerate(dummy_outlines):
        new_scene = models.Scene(
            project_id=project.id,
            scene_index=idx + 1,
            outline=outline,
            status=models.ProcessingStatus.PENDING
        )
        db.add(new_scene)
    
    await db.commit()
    
    # 3. Trigger Background Loop (Concept)
    background_tasks.add_task(run_generation_loop, project.id)
    
    return {"status": "Scene generation started", "project_id": project_id}

# --- Background Task (The Engine) ---

async def run_generation_loop(project_id: int):
    """
    The Core Loop: Iterates scenes and generates content with Rolling Summary.
    """
    # Note: Requires a new DB session in background task usually, 
    # but for simplicity of this scaffold we'll mock the logic flow here.
    print(f"Starting generation loop for Project {project_id}...")
    # Implementation depends on how we handle sessions in background tasks
    # Usually: async with SessionLocal() as db: ...
    
    async with database.SessionLocal() as db:
        # TODO: Implement the rolling summary logic here
        pass

import database # Import at end to avoid circular dependency issues in loop if needed
