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


def test_extract_responses_content_supports_sdk_and_wire_shapes():
    assert connection_test.extract_responses_content({"output_text": "直接正文"}) == "直接正文"
    assert connection_test.extract_responses_content(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "协议"},
                        {"type": "output_text", "text": "成功"},
                    ],
                }
            ]
        }
    ) == "协议成功"


def test_extract_responses_sse_content_combines_semantic_events():
    body = (
        'data: {"type":"response.output_text.delta","delta":"流式"}\n\n'
        'data: {"type":"response.output_text.delta","delta":"成功"}\n\n'
        'data: {"type":"response.completed","response":{"output":[]}}\n\n'
    ).encode("utf-8")
    assert connection_test.extract_responses_sse_content(body) == "流式成功"


def test_recommendation_does_not_emit_false_config_when_all_probes_fail(capsys):
    failed = connection_test.ProbeResult(False, 500, 0.1, error="unsupported")
    connection_test.print_recommendation(
        "https://example.com/v1",
        "gpt-5.6-sol",
        failed,
        failed,
        failed,
        failed,
        60,
    )
    output = capsys.readouterr().out
    assert "没有检测到可填写的有效配置" in output
    assert "仅流式响应：开启" not in output


def test_recommendation_selects_streaming_responses_profile(capsys):
    failed = connection_test.ProbeResult(False, 500, 0.1, error="unsupported")
    passed = connection_test.ProbeResult(True, 200, 0.1, preview="OK")
    connection_test.print_recommendation(
        "https://example.com/v1",
        "gpt-5.6-sol",
        failed,
        failed,
        failed,
        passed,
        60,
    )
    output = capsys.readouterr().out
    assert "接口协议：Responses API（/responses）" in output
    assert '"接口协议值": "responses"' in output
    assert "仅流式响应：开启" in output


def test_safe_message_redacts_key():
    assert connection_test.safe_message("bad key secret-key", "secret-key") == "bad key ***"
