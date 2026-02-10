import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./lumina_v2.db")
    logger.info(f"Checking DB URL: {db_url}")
    
    engine = create_async_engine(db_url)
    
    try:
        async with engine.connect() as conn:
            # Check for tables
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table';"))
            tables = result.scalars().all()
            logger.info(f"Tables found: {tables}")
            
            if 'users' not in tables:
                logger.error("Users table missing!")
            else:
                logger.info("Users table exists.")
                # Check columns
                cols = await conn.execute(text("PRAGMA table_info(users)"))
                logger.info(f"User columns: {cols.fetchall()}")

            if 'projects' not in tables:
                logger.error("Projects table missing!")
            else:
                 cols = await conn.execute(text("PRAGMA table_info(projects)"))
                 logger.info(f"Project columns: {cols.fetchall()}")

    except Exception as e:
        logger.error(f"Connection failed: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
