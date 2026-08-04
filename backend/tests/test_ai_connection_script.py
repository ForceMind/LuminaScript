from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import test_ai_connection as connection_test  # noqa: E402


def test_normalize_api_base():
    assert connection_test.normalize_api_base("https://example.com") == "https://example.com/v1"
    assert connection_test.normalize_api_base("https://example.com/v1/") == "https://example.com/v1"


def test_extract_model_ids_supports_openai_and_simple_shapes():
    assert connection_test.extract_model_ids(
        {"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]}
    ) == ["model-a", "model-b"]
    assert connection_test.extract_model_ids({"models": ["model-c"]}) == ["model-c"]


def test_extract_sse_content_combines_chunks():
    body = (
        'data: {"choices":[{"delta":{"content":"流式"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"成功"}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")
    assert connection_test.extract_sse_content(body) == "流式成功"


def test_safe_message_redacts_key():
    assert connection_test.safe_message("bad key secret-key", "secret-key") == "bad key ***"
