import sqlite3
import asyncio
import sys
from pathlib import Path
from database import init_db, DATABASE_URL
import models  # Register ORM tables before init_db() runs in standalone mode.
from services.admin_provisioning import (
    AdminProvisioningRequired,
    ensure_admin_policy,
)

BASE_DIR = Path(__file__).resolve().parent


def resolve_db_file() -> str:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if DATABASE_URL.startswith(prefix):
            raw_path = DATABASE_URL[len(prefix):].split("?", 1)[0]
            if not raw_path or raw_path == ":memory:":
                return str((BASE_DIR / "lumina_v2.db").resolve())

            db_path = Path(raw_path)
            if not db_path.is_absolute():
                db_path = (BASE_DIR / db_path).resolve()
            return str(db_path)

    return str((BASE_DIR / "lumina_v2.db").resolve())


DB_FILE = resolve_db_file()


def table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    )
    return cursor.fetchone() is not None

def upgrade_legacy_schema():
    print(f"Checking database schema in {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if not table_exists(cursor, "users") or not table_exists(cursor, "projects"):
        print("Core tables missing. Initializing ORM schema first...")
        conn.close()
        asyncio.run(init_db())
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

    # 1. Add is_admin to users
    try:
        cursor.execute("SELECT is_admin FROM users LIMIT 1")
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise
        print("Adding 'is_admin' column to users table...")
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    
    # 2. Create login_logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip_address VARCHAR,
            user_agent VARCHAR,
            status VARCHAR,
            timestamp VARCHAR,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    # 2.1 Add user_agent to login_logs if missing
    try:
        cursor.execute("SELECT user_agent FROM login_logs LIMIT 1")
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise
        print("Adding 'user_agent' column to login_logs table...")
        cursor.execute("ALTER TABLE login_logs ADD COLUMN user_agent VARCHAR")
        
    # 2.2 Add location to login_logs if missing
    try:
        cursor.execute("SELECT location FROM login_logs LIMIT 1")
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise
        print("Adding 'location' column to login_logs table...")
        cursor.execute("ALTER TABLE login_logs ADD COLUMN location VARCHAR")
    
    print("Checked 'login_logs' table.")

    # 3. Create ai_logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            project_id INTEGER,
            action VARCHAR,
            prompt TEXT,
            response TEXT,
            tokens INTEGER,
            status VARCHAR,
            step_key VARCHAR,
            error_type VARCHAR,
            error_message TEXT,
            attempt INTEGER,
            timestamp VARCHAR,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        )
    """)
    for column_name, column_sql in (
        ("project_id", "ALTER TABLE ai_logs ADD COLUMN project_id INTEGER"),
        ("action", "ALTER TABLE ai_logs ADD COLUMN action VARCHAR"),
        ("prompt", "ALTER TABLE ai_logs ADD COLUMN prompt TEXT"),
        ("response", "ALTER TABLE ai_logs ADD COLUMN response TEXT"),
        ("tokens", "ALTER TABLE ai_logs ADD COLUMN tokens INTEGER DEFAULT 0"),
        ("timestamp", "ALTER TABLE ai_logs ADD COLUMN timestamp VARCHAR"),
        ("status", "ALTER TABLE ai_logs ADD COLUMN status VARCHAR DEFAULT 'success'"),
        ("step_key", "ALTER TABLE ai_logs ADD COLUMN step_key VARCHAR"),
        ("error_type", "ALTER TABLE ai_logs ADD COLUMN error_type VARCHAR"),
        ("error_message", "ALTER TABLE ai_logs ADD COLUMN error_message TEXT"),
        ("attempt", "ALTER TABLE ai_logs ADD COLUMN attempt INTEGER DEFAULT 1"),
    ):
        try:
            cursor.execute(f"SELECT {column_name} FROM ai_logs LIMIT 1")
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            print(f"Adding '{column_name}' column to ai_logs table...")
            cursor.execute(column_sql)
    print("Checked 'ai_logs' table.")

    # 4. Protect scene identity against duplicate concurrent generation.
    if table_exists(cursor, "scenes"):
        cursor.execute(
            """
            SELECT project_id, scene_index, COUNT(*) AS duplicate_count
            FROM scenes
            GROUP BY project_id, scene_index
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
        duplicate_scene = cursor.fetchone()
        if duplicate_scene:
            conn.close()
            raise RuntimeError(
                "Duplicate scene indexes already exist for project "
                f"{duplicate_scene[0]} scene {duplicate_scene[1]}. "
                "Resolve the duplicates before starting the API."
            )
        else:
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_scenes_project_scene_index
                ON scenes (project_id, scene_index)
                """
            )
            print("Checked unique scene index.")

    conn.commit()
    conn.close()
    print("Legacy schema compatibility check complete.")


def upgrade_schema(*, check_admin_policy: bool = True):
    if DATABASE_URL.startswith(("sqlite+aiosqlite:///", "sqlite:///")):
        upgrade_legacy_schema()

    if check_admin_policy:
        administrators = asyncio.run(ensure_admin_policy())
        print(f"Current administrators: {administrators}")
        print("Administrator policy check complete.")

if __name__ == "__main__":
    try:
        from migrate import run_migrations

        run_migrations()
        upgrade_schema()
    except AdminProvisioningRequired as exc:
        print(f"Administrator provisioning required: {exc}", file=sys.stderr)
        sys.exit(3)
