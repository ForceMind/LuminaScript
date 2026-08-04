#!/usr/bin/env python3
"""Interactively test an OpenAI-compatible endpoint without persisting its key."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_URL = "https://ai.inno-flare.com"


@dataclass
class ProbeResult:
    ok: bool
    status: int
    elapsed_seconds: float
    preview: str = ""
    error: str = ""


def normalize_api_base(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 HTTP/HTTPS 服务地址")
    if parsed.username or parsed.password:
        raise ValueError("服务地址不能包含用户名或密码")
    if parsed.path.rstrip("/").endswith("/v1"):
        return raw
    return f"{raw}/v1"


def safe_message(value: Any, api_key: str, limit: int = 800) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    if api_key:
        text = text.replace(api_key, "***")
    return text[:limit]


def request_bytes(
    method: str,
    url: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, str, bytes, float]:
    body = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "LuminaScript-AI-Connection-Test/1.0",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            return (
                int(response.status),
                str(response.headers.get("Content-Type", "")),
                response.read(),
                time.monotonic() - started,
            )
    except HTTPError as exc:
        return (
            int(exc.code),
            str(exc.headers.get("Content-Type", "")),
            exc.read(),
            time.monotonic() - started,
        )
    except (URLError, TimeoutError, OSError) as exc:
        return 0, "", str(exc).encode("utf-8", errors="replace"), time.monotonic() - started


def parse_json_bytes(body: bytes) -> Any:
    return json.loads(body.decode("utf-8", errors="replace"))


def extract_model_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw_items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    model_ids: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
        else:
            model_id = ""
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def fetch_models(api_base: str, api_key: str, timeout: int) -> tuple[list[str], str]:
    status, _content_type, body, _elapsed = request_bytes(
        "GET", f"{api_base}/models", api_key, timeout=timeout
    )
    if status != 200:
        try:
            payload = parse_json_bytes(body)
            message = payload.get("error", {}).get("message") or payload
        except Exception:
            message = body.decode("utf-8", errors="replace")
        return [], f"HTTP {status or '连接失败'}: {safe_message(message, api_key)}"
    try:
        model_ids = extract_model_ids(parse_json_bytes(body))
    except Exception as exc:
        return [], f"模型列表不是有效 JSON：{safe_message(exc, api_key)}"
    if not model_ids:
        return [], "接口返回成功，但模型列表为空"
    return model_ids, ""


def extract_non_stream_content(payload: Any) -> str:
    try:
        choices = payload.get("choices") or []
        message = choices[0].get("message") or {}
        value = message.get("content")
        if isinstance(value, str):
            return value
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


def extract_sse_content(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    parts: list[str] = []
    saw_event = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        saw_event = True
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            value = delta.get("content")
            if isinstance(value, str):
                parts.append(value)
    if saw_event:
        return "".join(parts)
    try:
        return extract_non_stream_content(json.loads(text))
    except Exception:
        return ""


def upstream_error(body: bytes, api_key: str) -> str:
    try:
        payload = parse_json_bytes(body)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            value = error.get("message") or error
        else:
            value = payload
    except Exception:
        value = body.decode("utf-8", errors="replace")
    return safe_message(value, api_key)


def probe_chat(
    api_base: str,
    api_key: str,
    model_id: str,
    *,
    stream: bool,
    timeout: int,
) -> ProbeResult:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 16,
        "stream": stream,
    }
    status, _content_type, body, elapsed = request_bytes(
        "POST",
        f"{api_base}/chat/completions",
        api_key,
        payload=payload,
        timeout=timeout,
    )
    if status != 200:
        return ProbeResult(
            ok=False,
            status=status,
            elapsed_seconds=elapsed,
            error=upstream_error(body, api_key),
        )
    try:
        if stream:
            content = extract_sse_content(body)
        else:
            content = extract_non_stream_content(parse_json_bytes(body))
    except Exception as exc:
        return ProbeResult(
            ok=False,
            status=status,
            elapsed_seconds=elapsed,
            error=f"响应解析失败：{safe_message(exc, api_key)}",
        )
    if not content.strip():
        return ProbeResult(
            ok=False,
            status=status,
            elapsed_seconds=elapsed,
            error="请求成功，但没有解析到正文内容",
        )
    return ProbeResult(
        ok=True,
        status=status,
        elapsed_seconds=elapsed,
        preview=safe_message(content, api_key, limit=160),
    )


def choose_model(model_ids: list[str], preset: str = "") -> str:
    if preset.strip():
        return preset.strip()
    if model_ids:
        print("\n可用模型：")
        for index, model_id in enumerate(model_ids, start=1):
            print(f"  {index:>3}. {model_id}")
        while True:
            answer = input(f"\n选择模型序号或直接输入模型 ID [默认 1: {model_ids[0]}]: ").strip()
            if not answer:
                return model_ids[0]
            if answer.isdigit() and 1 <= int(answer) <= len(model_ids):
                return model_ids[int(answer) - 1]
            if answer:
                return answer
    return input("请输入要测试的模型 ID: ").strip()


def profile_id_from_url(api_base: str) -> str:
    hostname = urlparse(api_base).hostname or "custom-ai"
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", hostname).strip("-").lower()
    return value[:64] or "custom-ai"


def print_probe(label: str, result: ProbeResult) -> None:
    marker = "通过" if result.ok else "失败"
    print(f"\n{label}：{marker}（HTTP {result.status or 'N/A'}，{result.elapsed_seconds:.2f}s）")
    if result.preview:
        print(f"  返回预览：{result.preview}")
    if result.error:
        print(f"  原因：{result.error}")


def print_recommendation(
    api_base: str,
    model_id: str,
    non_stream: ProbeResult,
    stream: ProbeResult,
    timeout: int,
) -> None:
    stream_required = stream.ok and not non_stream.ok
    if stream.ok and non_stream.ok:
        recommended_stream = False
        note = "两种模式都可用，默认使用普通响应；也可以手动开启流式。"
    elif stream_required:
        recommended_stream = True
        note = "只有流式请求通过，必须开启“仅流式响应”。"
    elif non_stream.ok:
        recommended_stream = False
        note = "只有非流式请求通过，不要开启“仅流式响应”。"
    else:
        recommended_stream = True
        note = "两种模式均未通过，请先根据上方错误修复服务或令牌。"

    profile = {
        "档案 ID": profile_id_from_url(api_base),
        "显示名称": urlparse(api_base).hostname or "自建 AI",
        "Base URL": api_base,
        "模型 ID": model_id,
        "API Key": "填写刚才隐藏输入的 Key（脚本不会回显）",
        "请求超时（秒）": max(90, timeout),
        "最大并发请求数": 2,
        "仅流式响应": recommended_stream,
        "启用": True,
        "优先级": 100,
    }
    print("\n" + "=" * 64)
    print("可填写到 LuminaScript 管理后台的配置")
    print("=" * 64)
    for key, value in profile.items():
        if isinstance(value, bool):
            display = "开启" if value else "关闭"
        else:
            display = value
        print(f"{key}：{display}")
    print(f"\n判断：{note}")
    print("\n可复制的非敏感配置 JSON：")
    print(json.dumps(profile, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全测试 OpenAI 兼容 AI 服务")
    parser.add_argument("--url", default="", help="服务根地址或带 /v1 的 Base URL")
    parser.add_argument("--model", default="", help="跳过选择并测试指定模型")
    parser.add_argument("--timeout", type=int, default=60, help="单次请求超时秒数")
    parser.add_argument("--pause", action="store_true", help="结束前等待回车")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("LuminaScript AI 连接测试")
    print("Key 使用隐藏输入，不会写入文件、命令行参数或测试报告。\n")
    raw_url = args.url.strip() or input(f"服务地址 [{DEFAULT_URL}]: ").strip() or DEFAULT_URL
    try:
        api_base = normalize_api_base(raw_url)
    except ValueError as exc:
        print(f"地址错误：{exc}")
        return 2

    api_key = getpass.getpass("请输入 API Key（输入内容不会显示）: ").strip()
    if not api_key:
        print("API Key 不能为空")
        return 2

    try:
        print(f"\n正在连接：{api_base}")
        model_ids, models_error = fetch_models(api_base, api_key, args.timeout)
        if models_error:
            print(f"模型列表获取失败：{models_error}")
        else:
            print(f"模型列表获取成功，共 {len(model_ids)} 个模型。")
        model_id = choose_model(model_ids, args.model)
        if not model_id:
            print("模型 ID 不能为空")
            return 2

        print(f"\n正在测试模型：{model_id}")
        non_stream = probe_chat(
            api_base, api_key, model_id, stream=False, timeout=args.timeout
        )
        print_probe("非流式请求", non_stream)
        stream = probe_chat(
            api_base, api_key, model_id, stream=True, timeout=args.timeout
        )
        print_probe("流式请求", stream)
        print_recommendation(api_base, model_id, non_stream, stream, args.timeout)
        return 0 if non_stream.ok or stream.ok else 1
    finally:
        # Best-effort removal of the only live reference. CPython strings cannot
        # be securely zeroed, so the process exits immediately after the test.
        api_key = ""
        if args.pause:
            input("\n按回车键关闭窗口...")


if __name__ == "__main__":
    raise SystemExit(main())
