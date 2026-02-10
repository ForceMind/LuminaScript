import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text, select
from dotenv import load_dotenv
import models
from database import SessionLocal

async def check():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./lumina_v2.db")
    print(f"Connecting to {db_url}...")
    
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            # Check columns
            print("Checking columns in 'projects' table:")
            result = await conn.execute(text("PRAGMA table_info(projects)"))
            columns = result.fetchall()
            for col in columns:
                print(col)
                
        # Try a query using SQLAlchemy ORM to see if it crashes
        print("\nAttempting ORM Query...")
        async with SessionLocal() as session:
            try:
                # We need to simulate the query in list_projects
                # But we don't have a user, so allow getting all projects
                stmt = select(models.Project)
                result = await session.execute(stmt)
                projects = result.scalars().all()
                print(f"Successfully fetched {len(projects)} projects.")
                for p in projects:
                    print(f"Project ID: {p.id}, Status: {p.status}, Tokens: {p.total_tokens}")
            except Exception as e:
                print(f"ORM Query Failed: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"Connection Failed: {e}")

    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())