import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

async def fix():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./lumina_v2.db")
    
    # Just in case the user hasn't set it in env correctly (e.g. running outside of bat context)
    if not db_url:
        db_url = "sqlite+aiosqlite:///./lumina_v2.db"
        
    print(f"Fixing DB at {db_url}...")
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            print("Fixing projects 'status'...")
            # We blindly update 'pending' -> 'PENDING'
            await conn.execute(text("UPDATE projects SET status='PENDING' WHERE status='pending'"))
            await conn.execute(text("UPDATE projects SET status='GENERATING' WHERE status='generating'"))
            await conn.execute(text("UPDATE projects SET status='COMPLETED' WHERE status='completed'"))
            await conn.execute(text("UPDATE projects SET status='FAILED' WHERE status='failed'"))
            
            print("Fixing scenes 'status'...")
            # Scenes likely used ORM, so might be OK, but checking anyway
            await conn.execute(text("UPDATE scenes SET status='PENDING' WHERE status='pending'"))
            await conn.execute(text("UPDATE scenes SET status='GENERATING' WHERE status='generating'"))
            await conn.execute(text("UPDATE scenes SET status='COMPLETED' WHERE status='completed'"))
            await conn.execute(text("UPDATE scenes SET status='FAILED' WHERE status='failed'"))
            
            print("Updates committed.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(fix())