from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bootstrap_security import INSECURE_SECRET_KEYS


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    secret_key: str = ""
    access_token_expire_minutes: int = Field(default=120, ge=5, le=43200)

    database_url: str = "sqlite+aiosqlite:///./lumina_v2.db"
    sql_echo: bool = False

    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://maas-api.cn-huabei-1.xf-yun.com/v1"
    llm_model_id: str = "xopglm47blth2"
    llm_timeout_seconds: int = Field(default=90, ge=10, le=600)
    llm_max_concurrency: int = Field(default=5, ge=1, le=20)
    llm_stream_response: bool = False

    login_attempt_window_seconds: int = Field(default=300, ge=60, le=86400)
    login_attempt_max: int = Field(default=10, ge=3, le=100)
    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=30.0)
    worker_lease_seconds: int = Field(default=900, ge=30, le=86400)

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        url = str(value or "").strip()
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://"):]

        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return url
        raw_path = url[len(prefix):]
        if not raw_path or raw_path == ":memory:":
            return url

        path = Path(raw_path)
        if path.is_absolute():
            return url
        resolved_path = (BASE_DIR / path).resolve()
        return f"{prefix}{resolved_path.as_posix()}"

    def require_secure_secret_key(self) -> str:
        value = self.secret_key.strip()
        if value in INSECURE_SECRET_KEYS or len(value) < 32:
            raise RuntimeError(
                "SECRET_KEY is missing or insecure. Run "
                "`python bootstrap_security.py` or configure a strong value "
                "in backend/.env before starting the API."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
