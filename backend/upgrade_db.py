import sqlite3
import os

DB_PATH = "backend/lumina_v2.db"

def upgrade():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        print("Adding project_type column...")
        c.execute("ALTER TABLE projects ADD COLUMN project_type VARCHAR DEFAULT 'movie'")
        print("Success.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column project_type already exists.")
        else:
            print(f"Error: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    upgrade()
