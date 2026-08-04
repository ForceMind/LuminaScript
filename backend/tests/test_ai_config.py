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
            api_protocol="responses",
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
    assert public["api_protocol"] == "responses"
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
    assert candidate.api_protocol == "chat_completions"
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

    async def fake_concurrency_test(config, concurrency):
        assert config.api_key == "new-secret"
        assert concurrency == 5
        return {
            "requested": 5,
            "succeeded": 5,
            "failed": 0,
            "supported": True,
            "recommended_max_concurrency": 5,
            "response_preview": "OK",
            "errors": [],
        }

    monkeypatch.setattr(admin_routes, "test_llm_connection", fake_test)
    monkeypatch.setattr(admin_routes, "test_llm_concurrency", fake_concurrency_test)
    response = await admin_routes.test_ai_config(
        schemas.AIConfigTestRequest(
            base_url="https://example.com/v1",
            model_id="new-model",
            api_key="new-secret",
        ),
        models.User(id=1, username="admin", hashed_password="unused", is_admin=1),
    )

    assert response == {
        "success": True,
        "message": "连接与 5 路并发测试均成功",
        "response_preview": "OK",
        "concurrency_requested": 5,
        "concurrency_succeeded": 5,
        "concurrency_failed": 0,
        "concurrency_supported": True,
        "recommended_max_concurrency": 5,
        "error_messages": [],
    }


@pytest.mark.asyncio
async def test_concurrency_test_reports_single_request_provider_limit(monkeypatch):
    active_requests = 0

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            nonlocal active_requests
            active_requests += 1
            overloaded = active_requests > 1
            try:
                await __import__("asyncio").sleep(0.01)
                if overloaded:
                    raise RuntimeError("provider only allows one request")
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                    usage=None,
                )
            finally:
                active_requests -= 1

        async def close(self):
            return None

    monkeypatch.setattr(llm_config, "AsyncOpenAI", FakeClient)
    config = llm_config.LLMRuntimeConfig(
        api_key="secret",
        base_url="https://single.example.com/v1",
        model_id="model",
        timeout_seconds=30,
        max_concurrency=3,
    )

    result = await llm_config.test_llm_concurrency(config, 3)

    assert result["requested"] == 3
    assert result["succeeded"] == 1
    assert result["failed"] == 2
    assert result["supported"] is False
    assert result["recommended_max_concurrency"] == 1


@pytest.mark.asyncio
async def test_model_list_uses_stored_profile_key(monkeypatch):
    profile = llm_config.LLMRuntimeConfig(
        api_key="profile-secret",
        base_url="https://models.example.com/v1",
        model_id="model-a",
        timeout_seconds=90,
        max_concurrency=2,
        profile_id="profile-a",
    )
    monkeypatch.setattr(
        admin_routes,
        "get_llm_profile_store",
        lambda: llm_config.LLMProfileStore(
            active_profile="profile-a",
            profiles=[profile],
        ),
    )

    async def fake_list_models(**kwargs):
        assert kwargs == {
            "base_url": "https://models.example.com/v1",
            "api_key": "profile-secret",
            "timeout_seconds": 60,
        }
        return ["gpt-5.4", "gpt-5.6-sol"]

    monkeypatch.setattr(admin_routes, "list_llm_models", fake_list_models)
    response = await admin_routes.get_ai_models(
        schemas.AIModelListRequest(
            base_url="https://models.example.com/v1/",
            profile_id="profile-a",
            timeout_seconds=60,
        ),
        models.User(id=1, username="admin", hashed_password="unused", is_admin=1),
    )

    assert response == {"models": ["gpt-5.4", "gpt-5.6-sol"]}


@pytest.mark.asyncio
async def test_list_llm_models_deduplicates_results(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "secret"
            self.models = SimpleNamespace(list=self.list_models)

        async def list_models(self):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(id="model-a"),
                    SimpleNamespace(id="model-b"),
                    SimpleNamespace(id="model-a"),
                ]
            )

        async def close(self):
            return None

    monkeypatch.setattr(llm_config, "AsyncOpenAI", FakeClient)

    assert await llm_config.list_llm_models(
        base_url="https://models.example.com/v1",
        api_key="secret",
        timeout_seconds=60,
    ) == ["model-a", "model-b"]


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
async def test_generation_supports_responses_api(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="Responses 完成",
            usage=SimpleNamespace(total_tokens=13),
        )

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_create),
    )
    runtime = llm_config.LLMRuntimeConfig(
        api_key="runtime-secret",
        base_url="https://runtime.example.com/v1",
        model_id="gpt-5.6-sol",
        timeout_seconds=45,
        max_concurrency=2,
        api_protocol="responses",
    )
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task_type: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: fake_client)

    messages = [{"role": "user", "content": "hello"}]
    content, tokens = await llm.raw_generation(messages)

    assert content == "Responses 完成"
    assert tokens == 13
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["input"] == messages
    assert captured["stream"] is False
    assert "temperature" not in captured


@pytest.mark.asyncio
async def test_generation_aggregates_responses_stream_events(monkeypatch):
    captured = {}

    class FakeStream:
        def __aiter__(self):
            async def events():
                yield SimpleNamespace(type="response.output_text.delta", delta="流式")
                yield SimpleNamespace(type="response.output_text.delta", delta="完成")
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(usage=SimpleNamespace(total_tokens=17)),
                )
            return events()

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeStream()

    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    runtime = llm_config.LLMRuntimeConfig(
        api_key="runtime-secret",
        base_url="https://runtime.example.com/v1",
        model_id="gpt-5.6-sol",
        timeout_seconds=45,
        max_concurrency=2,
        api_protocol="responses",
        stream_response=True,
    )
    monkeypatch.setattr(llm, "get_routed_llm_configs", lambda task_type: [runtime])
    monkeypatch.setattr(llm, "_get_client", lambda config: fake_client)

    content, tokens = await llm.raw_generation(
        [{"role": "user", "content": "hello"}]
    )

    assert content == "流式完成"
    assert tokens == 17
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
async def test_connection_test_supports_responses_profile(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            assert kwargs["input"] == [{"role": "user", "content": "Reply with OK."}]
            assert kwargs["stream"] is False
            return SimpleNamespace(output_text="OK", usage=None)

        async def close(self):
            return None

    monkeypatch.setattr(llm_config, "AsyncOpenAI", FakeClient)
    config = llm_config.LLMRuntimeConfig(
        api_key="responses-secret",
        base_url="https://responses.example.com/v1",
        model_id="gpt-5.6-sol",
        timeout_seconds=45,
        max_concurrency=2,
        api_protocol="responses",
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


@pytest.mark.asyncio
async def test_real_openai_client_parses_responses_protocol():
    def completed_response():
        return {
            "id": "resp-local",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "id": "msg-local",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "annotations": [],
                            "logprobs": [],
                            "text": "协议通过",
                        }
                    ],
                }
            ],
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None},
            "store": True,
            "temperature": 1.0,
            "text": {"format": {"type": "text"}, "verbosity": "medium"},
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 3,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 5,
            },
            "metadata": {},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert payload["input"] == [{"role": "user", "content": "hello"}]
        if not payload["stream"]:
            return httpx.Response(200, json=completed_response())

        events = [
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "item_id": "msg-local",
                "output_index": 0,
                "content_index": 0,
                "delta": "协议",
                "logprobs": [],
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 2,
                "item_id": "msg-local",
                "output_index": 0,
                "content_index": 0,
                "delta": "通过",
                "logprobs": [],
            },
            {
                "type": "response.completed",
                "sequence_number": 3,
                "response": completed_response(),
            },
        ]
        body = "".join(
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in events
        )
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
    try:
        for stream in (False, True):
            runtime = llm_config.LLMRuntimeConfig(
                api_key="local-test",
                base_url="http://local.test/v1",
                model_id="gpt-5.6-sol",
                timeout_seconds=45,
                max_concurrency=2,
                api_protocol="responses",
                stream_response=stream,
            )
            content, tokens = await llm_config.create_llm_text_response(
                client,
                runtime,
                [{"role": "user", "content": "hello"}],
                temperature=0.7,
            )
            assert content == "协议通过"
            assert tokens == 5
    finally:
        await client.close()
