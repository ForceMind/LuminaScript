import os
import sys
import tempfile
from pathlib import Path

import pytest_asyncio


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

handle = tempfile.NamedTemporaryFile(
    prefix="luminascript_pytest_",
    suffix=".db",
    delete=False,
)
handle.close()
TEST_DATABASE_PATH = Path(handle.name).resolve()

os.environ["DATABASE_URL"] = (
    f"sqlite+aiosqlite:///{TEST_DATABASE_PATH.as_posix()}"
)
os.environ["SECRET_KEY"] = "pytest-only-secret-key-with-at-least-32-characters"
os.environ["LLM_API_KEY"] = "pytest-only"

import database  # noqa: E402
import models  # noqa: E402

database.engine.echo = False


@pytest_asyncio.fixture(autouse=True)
async def reset_database():
    async with database.engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.drop_all)
        await connection.run_sync(models.Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def cleanup_test_database():
    yield
    await database.engine.dispose()
    for candidate in (
        TEST_DATABASE_PATH,
        Path(f"{TEST_DATABASE_PATH}-journal"),
        Path(f"{TEST_DATABASE_PATH}-wal"),
        Path(f"{TEST_DATABASE_PATH}-shm"),
    ):
        candidate.unlink(missing_ok=True)
