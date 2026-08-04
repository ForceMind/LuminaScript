import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

import models
import schemas
from api import admin_routes
from services import llm, llm_config


def test_sqlite_export_snapshot_contains_wal_data(tmp_path):
    database_path = tmp_path / "live.db"
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE example (value TEXT NOT NULL)")
    connection.execute("INSERT INTO example (value) VALUES ('latest')")
    connection.commit()

    snapshot_bytes = admin_routes.create_sqlite_snapshot(database_path)
    snapshot_path = tmp_path / "snapshot.db"
    snapshot_path.write_bytes(snapshot_bytes)
    try:
        snapshot = sqlite3.connect(snapshot_path)
        value = snapshot.execute("SELECT value FROM example").fetchone()[0]
        snapshot.close()
    finally:
        connection.close()

    assert value == "latest"


def test_runtime_ai_config_is_persisted_without_exposing_api_key(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / ".llm_runtime.json"
    monkeypatch.setattr(llm_config, "RUNTIME_CONFIG_PATH", config_path)

    stored = llm_config.save_runtime_llm_config(
        llm_config.LLMRuntimeConfig(
            api_key="secret-key-value",
            base_url="https://example.com/v1/",
            model_id="example-model",
            timeout_seconds=120,
            max_concurrency=3,
            stream_response=True,
        ),
        updated_by="administrator",
    )
    loaded = llm_config.get_runtime_llm_config()
    public = llm_config.public_llm_config(loaded)

    assert stored.source == "admin"
    assert loaded.api_key == "secret-key-value"
    assert loaded.base_url == "https://example.com/v1"
    assert public["api_key_configured"] is True
    assert public["api_key_masked"].endswith("alue")
    assert public["stream_response"] is True
    assert "api_key" not in public
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["profiles"][0]["api_key"] == "secret-key-value"


def test_ai_config_update_keeps_existing_key_when_input_is_blank(monkeypatch):
    monkeypatch.setattr(
        admin_routes,
        "get_runtime_llm_config",
        lambda: llm_config.LLMRuntimeConfig(
            api_key="existing-secret",
            base_url="https://old.example.com/v1",
            model_id="old-model",
            timeout_seconds=90,
            max_concurrency=5,
        ),
    )
    payload = schemas.AIConfigUpdate(
        base_url="https://new.example.com/v1/",
        model_id="new-model",
        api_key="",
        timeout_seconds=150,
        max_concurrency=7,
    )

    candidate = admin_routes.build_llm_config_update(payload)

    assert candidate.api_key == "existing-secret"
    assert candidate.base_url == "https://new.example.com/v1"
    assert candidate.model_id == "new-model"
    assert candidate.timeout_seconds == 150
    assert candidate.max_concurrency == 7
    assert candidate.stream_response is False


@pytest.mark.asyncio
async def test_admin_can_test_candidate_config_without_saving(monkeypatch):
    monkeypatch.setattr(
        admin_routes,
        "get_runtime_llm_config",
        lambda: llm_config.LLMRuntimeConfig(
            api_key="existing-secret",
            base_url="https://example.com/v1",
            model_id="model",
            timeout_seconds=90,
            max_concurrency=5,
        ),
    )

    async def fake_test(config):
        assert config.api_key == "new-secret"
        return "OK"

    monkeypatch.setattr(admin_routes, "test_llm_connection", fake_test)
    response = await admin_routes.test_ai_config(
        schemas.AIConfigUpdate(
            base_url="https://example.com/v1",
            model_id="new-model",
            api_key="new-secret",
        ),
        models.User(id=1, username="admin", hashed_password="unused", is_admin=1),
    )

    assert response == {
        "success": True,
        "message": "连接测试成功",
        "response_preview": "OK",
    }


@pytest.mark.asyncio
async def test_generation_reads_runtime_config_for_each_request(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done"))],
            usage=SimpleNamespace(total_tokens=12),
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create),
        )
    )
    runtime = llm_config.LLMRuntimeConfig(
        api_key="runtime-secret",
        base_url="https://runtime.example.com/v1",
        model_id="runtime-model",
        timeout_seconds=45,
        max_concurrency=2,
    )
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task_type: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: fake_client)

    content, tokens = await llm.raw_generation(
        [{"role": "user", "content": "hello"}]
    )

    assert content == "done"
    assert tokens == 12
    assert captured["model"] == "runtime-model"
    assert captured["stream"] is False


@pytest.mark.asyncio
async def test_generation_aggregates_streaming_chunks(monkeypatch):
    captured = {}

    class FakeStream:
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="流式"))],
                    usage=None,
                )
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="完成"))],
                    usage=SimpleNamespace(total_tokens=9),
                )
            return chunks()

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeStream()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    runtime = llm_config.LLMRuntimeConfig(
        api_key="runtime-secret",
        base_url="https://runtime.example.com/v1",
        model_id="stream-model",
        timeout_seconds=45,
        max_concurrency=2,
        stream_response=True,
    )
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task_type: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: fake_client)

    content, tokens = await llm.raw_generation(
        [{"role": "user", "content": "hello"}]
    )

    assert content == "流式完成"
    assert tokens == 9
    assert captured["stream"] is True


@pytest.mark.asyncio
async def test_connection_test_supports_stream_only_profile(monkeypatch):
    class FakeStream:
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="O"))]
                )
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="K"))]
                )
            return chunks()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return FakeStream()

        async def close(self):
            return None

    monkeypatch.setattr(llm_config, "AsyncOpenAI", FakeClient)
    config = llm_config.LLMRuntimeConfig(
        api_key="stream-secret",
        base_url="https://stream.example.com/v1",
        model_id="stream-model",
        timeout_seconds=45,
        max_concurrency=2,
        stream_response=True,
    )

    assert await llm_config.test_llm_connection(config) == "OK"


@pytest.mark.asyncio
async def test_real_openai_client_parses_sse_stream(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert payload["stream"] is True
        events = [
            {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "stream-model",
                "choices": [
                    {"index": 0, "delta": {"content": "协议"}, "finish_reason": None}
                ],
            },
            {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "stream-model",
                "choices": [
                    {"index": 0, "delta": {"content": "通过"}, "finish_reason": "stop"}
                ],
            },
            {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "stream-model",
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        ]
        body = "".join(
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode("utf-8"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="local-test",
        base_url="http://local.test/v1",
        http_client=http_client,
    )
    runtime = llm_config.LLMRuntimeConfig(
        api_key="local-test",
        base_url="http://local.test/v1",
        model_id="stream-model",
        timeout_seconds=45,
        max_concurrency=2,
        stream_response=True,
    )
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task_type: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: client)
    try:
        content, tokens = await llm.raw_generation(
            [{"role": "user", "content": "hello"}]
        )
    finally:
        await client.close()

    assert content == "协议通过"
    assert tokens == 5
