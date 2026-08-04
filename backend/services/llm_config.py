from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from core.config import BASE_DIR, settings


logger = logging.getLogger(__name__)
RUNTIME_CONFIG_PATH = BASE_DIR / ".llm_runtime.json"


class LLMRuntimeConfig(BaseModel):
    api_key: Optional[str] = Field(default=None, repr=False, max_length=4096)
    base_url: str = Field(min_length=1, max_length=2048)
    model_id: str = Field(min_length=1, max_length=256)
    timeout_seconds: int = Field(ge=10, le=600)
    max_concurrency: int = Field(ge=1, le=20)
    stream_response: bool = False
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    source: str = "environment"
    profile_id: str = "default"
    profile_name: str = "默认配置"
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是有效的 HTTP 或 HTTPS 地址")
        if parsed.username or parsed.password:
            raise ValueError("Base URL 不能包含用户名或密码")
        return normalized

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("模型 ID 不能为空")
        return normalized


def _environment_config() -> LLMRuntimeConfig:
    return LLMRuntimeConfig(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model_id=settings.llm_model_id,
        timeout_seconds=settings.llm_timeout_seconds,
        max_concurrency=settings.llm_max_concurrency,
        stream_response=settings.llm_stream_response,
        source="environment",
        profile_id="environment",
        profile_name="服务器环境变量",
    )


class LLMProfileStore(BaseModel):
    active_profile: str = "default"
    profiles: list[LLMRuntimeConfig] = Field(default_factory=list)
    routes: dict[str, list[str]] = Field(default_factory=dict)


def _read_profile_store() -> LLMProfileStore | None:
    if not RUNTIME_CONFIG_PATH.exists():
        return None
    payload = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime LLM config must be a JSON object")
    if "profiles" in payload:
        store = LLMProfileStore.model_validate(payload)
        for profile in store.profiles:
            profile.source = "admin"
        return store

    legacy = LLMRuntimeConfig.model_validate(
        {
            **_environment_config().model_dump(),
            **payload,
            "source": "admin",
            "profile_id": "default",
            "profile_name": "默认配置",
        }
    )
    return LLMProfileStore(active_profile="default", profiles=[legacy], routes={})


def get_runtime_llm_config() -> LLMRuntimeConfig:
    fallback = _environment_config()
    try:
        store = _read_profile_store()
        if not store:
            return fallback
        active = next(
            (p for p in store.profiles if p.profile_id == store.active_profile and p.enabled),
            None,
        )
        if active:
            return active
        enabled = sorted(
            (p for p in store.profiles if p.enabled),
            key=lambda item: (item.priority, item.profile_id),
        )
        return enabled[0] if enabled else fallback
    except Exception as exc:
        logger.error("无法读取运行时 AI 配置，将回退到环境变量: %s", exc)
        return fallback


def save_runtime_llm_config(
    config: LLMRuntimeConfig,
    *,
    updated_by: str,
) -> LLMRuntimeConfig:
    stored = config.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
            "source": "admin",
        }
    )
    try:
        profile_store = _read_profile_store() or LLMProfileStore()
    except Exception:
        profile_store = LLMProfileStore()
    profile_id = stored.profile_id or profile_store.active_profile or "default"
    stored.profile_id = profile_id
    replaced = False
    for index, profile in enumerate(profile_store.profiles):
        if profile.profile_id == profile_id:
            profile_store.profiles[index] = stored
            replaced = True
            break
    if not replaced:
        profile_store.profiles.append(stored)
    profile_store.active_profile = profile_id
    payload = profile_store.model_dump(exclude={"profiles": {"__all__": {"source"}}})
    temp_path = RUNTIME_CONFIG_PATH.with_name(
        f".{RUNTIME_CONFIG_PATH.name}.{uuid4().hex}.tmp"
    )
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            logger.warning("无法收紧 AI 配置文件权限: %s", temp_path)
        os.replace(temp_path, RUNTIME_CONFIG_PATH)
    finally:
        temp_path.unlink(missing_ok=True)
    return stored


def get_llm_profile_store() -> LLMProfileStore:
    try:
        return _read_profile_store() or LLMProfileStore(
            active_profile="environment",
            profiles=[_environment_config()],
            routes={},
        )
    except Exception as exc:
        logger.error("无法读取 AI 配置档案: %s", exc)
        return LLMProfileStore(
            active_profile="environment",
            profiles=[_environment_config()],
            routes={},
        )


def save_llm_profile_store(store: LLMProfileStore) -> LLMProfileStore:
    if not store.profiles:
        raise ValueError("至少保留一个 AI 配置档案")
    profile_ids = {profile.profile_id for profile in store.profiles}
    if store.active_profile not in profile_ids:
        store.active_profile = store.profiles[0].profile_id
    payload = store.model_dump(exclude={"profiles": {"__all__": {"source"}}})
    temp_path = RUNTIME_CONFIG_PATH.with_name(
        f".{RUNTIME_CONFIG_PATH.name}.{uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, RUNTIME_CONFIG_PATH)
    finally:
        temp_path.unlink(missing_ok=True)
    return store


def get_routed_llm_configs(task_type: str = "default") -> list[LLMRuntimeConfig]:
    store = get_llm_profile_store()
    by_id = {profile.profile_id: profile for profile in store.profiles if profile.enabled}
    ordered_ids: list[str] = []
    for profile_id in store.routes.get(task_type, []) + store.routes.get("default", []):
        if profile_id not in ordered_ids:
            ordered_ids.append(profile_id)
    if store.active_profile not in ordered_ids:
        ordered_ids.append(store.active_profile)
    for profile in sorted(by_id.values(), key=lambda item: (item.priority, item.profile_id)):
        if profile.profile_id not in ordered_ids:
            ordered_ids.append(profile.profile_id)
    configs = [by_id[profile_id] for profile_id in ordered_ids if profile_id in by_id]
    return configs or [_environment_config()]


def public_llm_config(config: LLMRuntimeConfig) -> dict[str, Any]:
    key = config.api_key or ""
    masked_key = ""
    if key:
        suffix = key[-4:] if len(key) >= 4 else "****"
        masked_key = f"••••••••{suffix}"
    return {
        "base_url": config.base_url,
        "model_id": config.model_id,
        "timeout_seconds": config.timeout_seconds,
        "max_concurrency": config.max_concurrency,
        "stream_response": config.stream_response,
        "api_key_configured": bool(key),
        "api_key_masked": masked_key,
        "source": config.source,
        "updated_at": config.updated_at,
        "updated_by": config.updated_by,
        "profile_id": config.profile_id,
        "profile_name": config.profile_name,
        "enabled": config.enabled,
        "priority": config.priority,
    }


async def test_llm_connection(config: LLMRuntimeConfig) -> str:
    if not config.api_key:
        raise ValueError("请先填写 API Key")

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    try:
        response = await client.chat.completions.create(
            model=config.model_id,
            messages=[{"role": "user", "content": "Reply with OK."}],
            temperature=0,
            stream=config.stream_response,
        )
        if config.stream_response:
            parts: list[str] = []
            async for chunk in response:
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    value = getattr(delta, "content", None)
                    if value:
                        parts.append(str(value))
            content = "".join(parts).strip()
        else:
            content = str(response.choices[0].message.content or "").strip()
        return content[:100]
    finally:
        await client.close()


def safe_connection_error(exc: Exception, api_key: Optional[str]) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if api_key:
        message = message.replace(api_key, "***")
    return message[:500]
