import sys
print("RUNNING UPDATED TEST SCRIPT VERSION 2")
import asyncio
import os
from unittest.mock import MagicMock

# Add current dir to path to find modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# Monkeypatch LLM service to avoid real API calls
import services.llm as llm_service

async def mock_generate_scene_batch(logline, style_guide, start_idx, end_idx, previous_context="", total_target=0):
    print(f"  [Mock LLM] Generating batch {start_idx}-{end_idx}. Context len: {len(previous_context)}")
    # Return list of dicts + usage
    scenes = []
    for i in range(start_idx, end_idx + 1):
        scenes.append({
            "index": i, 
            "outline": f"Mock Scene {i} Outline Content for testing.", 
            "characters": ["Hero"], 
            "location": "Test Location"
        })
    return scenes, 100

llm_service.generate_scene_batch = mock_generate_scene_batch 
# Also mock write_scene_content for the content generation part
async def mock_write_content(logline, style_guide, current_scene_outline, previous_context=""):
    return f"EXT. TEST LOCATION - DAY\n\nHERO walks in.\n\nHERO\n(to self)\nI am testing scene {current_scene_outline[:10]}...\n", 50

llm_service.write_scene_content = mock_write_content
 

import main 
from models import Project, Scene, User 
from database import SessionLocal, init_db, engine, Base
from sqlalchemy import select

async def test_incremental_gen():
    with open("backend/verify.txt", "w", encoding="utf-8") as f:
        f.write("Starting Test...\n")
        print("Starting Test...")
    
        # Reset DB tables for clean test
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        project_id = 999 
        
        async with SessionLocal() as db:
            # Create Dummy User 
            # Check if user exists first
            result = await db.execute(select(User).where(User.id == 1))
            u = result.scalar_one_or_none()
            if not u:
                db.add(User(id=1, username="test", hashed_password="pw"))
                await db.commit()

            # Clean old project
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
               await db.delete(p)
               await db.commit()
               
            new_project = Project(
                id=project_id,
                title="Test Project 120 Scenes",
                logline="Test Robot Logline",
                project_type="movie",
                owner_id=1,
                status="generating" # Use string directly if enum issues
            )
            db.add(new_project)
            await db.commit()
        
        f.write(f"Project Created: {project_id}\n")
        print(f"Project Created: {project_id}")
        
        # Run the function with mocked LLM
        print("Invoking run_incremental_outline_generation...")
        f.write("Invoking run_incremental_outline_generation...\n")
        try:
            # Generate 3 scenes
            await main.run_incremental_outline_generation(project_id, "Sci-Fi", 3, 1) 
        except Exception as e:
            f.write(f"Error executing loop: {e}\n")
            print(f"Error executing loop: {e}")
            import traceback
            traceback.print_exc(file=f)
            
        # Check results
        async with SessionLocal() as db:
            result = await db.execute(select(Scene).where(Scene.project_id == project_id).order_by(Scene.scene_index))
            scenes = result.scalars().all()
            msg = f"\nGenerations Complete. Total Scenes in DB: {len(scenes)}"
            f.write(msg + "\n")
            print(msg)
            for s in scenes:
                line = f"- Scene {s.scene_index}: [{s.status}] {s.outline[:30]}..."
                f.write(line + "\n")
                print(line)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_incremental_gen())
