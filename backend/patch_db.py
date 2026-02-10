import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

async def patch():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./lumina_v2.db")
    print(f"Patching DB at {db_url}...")
    
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            # Add columns. Use separate statements as SQLite doesn't support multiple ADD COLUMN in one statement
            try:
                await conn.execute(text("ALTER TABLE projects ADD COLUMN total_tokens INTEGER DEFAULT 0"))
                print("Added total_tokens column.")
            except Exception as e:
                print(f"Skipping total_tokens: {e}")

            try:
                await conn.execute(text("ALTER TABLE projects ADD COLUMN status VARCHAR DEFAULT 'pending'"))
                print("Added status column.")
            except Exception as e:
                print(f"Skipping status: {e}")
                
            await conn.commit()
    finally:
        await engine.dispose()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(patch())