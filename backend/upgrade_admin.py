import sqlite3
import asyncio
from database import init_db
from passlib.context import CryptContext
import os

DB_FILE = "lumina_v2.db"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password):
    return pwd_context.hash(password)

def upgrade_schema():
    print(f"Checking database schema in {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 1. Add is_admin to users
    try:
        cursor.execute("SELECT is_admin FROM users LIMIT 1")
    except sqlite3.OperationalError:
        print("Adding 'is_admin' column to users table...")
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    
    # 2. Create login_logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip_address VARCHAR,
            status VARCHAR,
            timestamp VARCHAR,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
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
            timestamp VARCHAR,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        )
    """)
    print("Checked 'ai_logs' table.")

    # 4. Enforce Single Admin Policy
    # User Requirement: "Only one admin allowed, delete all others and recreate/ensure the specific one."
    
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASS", "admin123")
    
    # Check current admins
    cursor.execute("SELECT id, username FROM users WHERE is_admin = 1")
    admins = cursor.fetchall()
    
    # Strategy:
    # 1. If strict reset is requested OR if we want to enforce "Only one exists and it must be ADMIN_USER"
    #    The user specifically asked: "delete all and let me recreate [the one I want]"
    
    # Let's clean up ANY admin that doesn't match the current ENV target or just wipe all admins if prompted.
    # To be safe but compliant with the user's strong request:
    # We will remove ALL admins first, then ensure the target admin exists.
    # This guarantees "Only One" and "Recreated".

    if len(admins) > 0:
        print(f"Found {len(admins)} admin(s). Enforcing single-admin policy...")
        # Remove admin privilege from everyone
        cursor.execute("UPDATE users SET is_admin = 0 WHERE is_admin = 1")
    
    # Now ensure the target admin exists and has privileges
    hashed = get_password_hash(admin_pass)
    
    cursor.execute("SELECT id FROM users WHERE username = ?", (admin_user,))
    target_user = cursor.fetchone()
    
    if target_user:
        print(f"Updating privileges for admin: {admin_user}")
        cursor.execute("UPDATE users SET is_admin = 1, hashed_password = ? WHERE username = ?", (hashed, admin_user))
    else:
        print(f"Creating sole admin user: {admin_user}")
        cursor.execute(
            "INSERT INTO users (username, hashed_password, is_admin) VALUES (?, ?, ?)", 
            (admin_user, hashed, 1)
        )

    # Clean up: Verify only one admin exists
    cursor.execute("SELECT username FROM users WHERE is_admin = 1")
    final_admins = cursor.fetchall()
    print(f"Admin Policy Enforced. Current Admin: {[u[0] for u in final_admins]}")

    conn.commit()
    conn.close()
    print("Schema upgrade & Admin check complete.")

if __name__ == "__main__":
    if os.path.exists(DB_FILE):
        upgrade_schema()
    else:
        print("Database not found. Initializing new DB first...")
        asyncio.run(init_db())
        upgrade_schema()
