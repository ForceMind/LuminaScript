import sqlite3
import asyncio
import sys
from datetime import datetime, timezone
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


def _scene_quality(row: tuple) -> tuple[int, int, int, int, int, int]:
    """Rank duplicate scene rows without discarding richer generated content."""
    status = str(row[6] or "").lower()
    status_rank = {
        "completed": 4,
        "generating": 3,
        "pending": 2,
        "failed": 1,
    }.get(status, 0)
    content = str(row[4] or "").strip()
    summary = str(row[5] or "").strip()
    outline = str(row[3] or "").strip()
    return (
        int(bool(content)),
        status_rank,
        len(content),
        len(summary),
        len(outline),
        int(row[0] or 0),
    )


def resolve_duplicate_scenes(cursor: sqlite3.Cursor) -> tuple[int, int]:
    """Merge duplicate scene identities and archive every removed source row."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_duplicate_archive (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_scene_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            scene_index INTEGER NOT NULL,
            outline TEXT,
            content TEXT,
            summary TEXT,
            status VARCHAR,
            kept_scene_id INTEGER NOT NULL,
            archived_at VARCHAR NOT NULL,
            reason VARCHAR NOT NULL
        )
        """
    )
    cursor.execute(
        """
        SELECT project_id, scene_index, COUNT(*) AS duplicate_count
        FROM scenes
        GROUP BY project_id, scene_index
        HAVING COUNT(*) > 1
        ORDER BY project_id, scene_index
        """
    )
    duplicate_groups = cursor.fetchall()
    archived_rows = 0
    archived_at = datetime.now(timezone.utc).isoformat()

    for project_id, scene_index, _duplicate_count in duplicate_groups:
        cursor.execute(
            """
            SELECT id, project_id, scene_index, outline, content, summary, status
            FROM scenes
            WHERE project_id = ? AND scene_index = ?
            ORDER BY id
            """,
            (project_id, scene_index),
        )
        rows = cursor.fetchall()
        kept = max(rows, key=_scene_quality)
        kept_id = int(kept[0])

        def best_missing_value(column_index: int):
            current = kept[column_index]
            if str(current or "").strip():
                return current
            candidates = [
                row[column_index]
                for row in rows
                if str(row[column_index] or "").strip()
            ]
            return max(candidates, key=lambda value: len(str(value))) if candidates else current

        cursor.execute(
            """
            UPDATE scenes
            SET outline = ?, content = ?, summary = ?, status = ?
            WHERE id = ?
            """,
            (
                best_missing_value(3),
                best_missing_value(4),
                best_missing_value(5),
                kept[6],
                kept_id,
            ),
        )

        for row in rows:
            if int(row[0]) == kept_id:
                continue
            cursor.execute(
                """
                INSERT INTO scene_duplicate_archive (
                    source_scene_id, project_id, scene_index, outline, content,
                    summary, status, kept_scene_id, archived_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, kept_id, archived_at, "duplicate project_id/scene_index"),
            )
            cursor.execute("DELETE FROM scenes WHERE id = ?", (row[0],))
            archived_rows += 1

    return len(duplicate_groups), archived_rows

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
        duplicate_groups, archived_rows = resolve_duplicate_scenes(cursor)
        if duplicate_groups:
            print(
                "Resolved duplicate scene indexes: "
                f"groups={duplicate_groups}, archived_rows={archived_rows}. "
                "Original rows are preserved in 'scene_duplicate_archive'."
            )
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
