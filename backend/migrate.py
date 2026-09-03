from pathlib import Path
import sqlite3
from typing import Optional

from alembic import command
from alembic.config import Config

from core.config import settings


BASE_DIR = Path(__file__).resolve().parent
BASELINE_REVISION = "20260728_0001"
OPERATIONS_REVISION = "20260804_0004"
SETUP_REVISION = "20260903_0005"
HEAD_REVISION = "20260903_0006"


def alembic_config() -> Config:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def sqlite_database_path() -> Optional[Path]:
    prefix = "sqlite+aiosqlite:///"
    if not settings.database_url.startswith(prefix):
        return None

    raw_path = settings.database_url[len(prefix):].split("?", 1)[0]
    if not raw_path or raw_path == ":memory:":
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def unversioned_sqlite_revision(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        } if "users" in tables else set()
        job_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(generation_jobs)")
        } if "generation_jobs" in tables else set()
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        } if "projects" in tables else set()
    if "users" not in tables or "alembic_version" in tables:
        return None
    if "generation_jobs" in tables:
        if (
            {
                "project_members",
                "project_versions",
                "prompt_templates",
                "backup_records",
            }.issubset(tables)
            and "daily_token_limit" in user_columns
            and "cancel_requested" in job_columns
        ):
            if {"setup_revision", "setup_cache_revision"}.issubset(project_columns):
                return HEAD_REVISION if "quick_setup_draft" in project_columns else SETUP_REVISION
            return OPERATIONS_REVISION
        return "20260728_0003"
    return BASELINE_REVISION


def run_migrations() -> None:
    config = alembic_config()
    sqlite_path = sqlite_database_path()
    unversioned_revision = (
        unversioned_sqlite_revision(sqlite_path)
        if sqlite_path
        else None
    )
    if unversioned_revision:
        import upgrade_admin

        upgrade_admin.upgrade_schema(check_admin_policy=False)
        command.stamp(config, unversioned_revision)

    command.upgrade(config, "head")


if __name__ == "__main__":
    run_migrations()
