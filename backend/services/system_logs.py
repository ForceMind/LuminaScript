from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = (
    Path("/etc/miaobi/runtime.env"),
    PROJECT_ROOT / ".lumina_runtime",
)
LOG_ENV_KEYS = {
    "backend": "BACKEND_LOG",
    "worker": "WORKER_LOG",
    "frontend": "FRONTEND_LOG",
}
MAX_TAIL_BYTES = 2 * 1024 * 1024
MAX_FILTER_SCAN_LINES = 10_000

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)((?:\"|')(?:api[_-]?key|key|authorization|access[_-]?token|"
        r"secret|password)(?:\"|')\s*:\s*(?:\"|'))([^\"']+)"
    ),
    re.compile(
        r"(?i)((?:api[_-]?key|authorization|access[_-]?token|secret|password)"
        r"\s*[:=]\s*[\"']?)([^\s,\"']+)"
    ),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/-]{8,})"),
)


def _strip_runtime_value(value: str) -> str:
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        return normalized[1:-1]
    return normalized


def _load_runtime_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for runtime_path in RUNTIME_FILES:
        try:
            content = runtime_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in {*LOG_ENV_KEYS.values(), "PROJECT_DIR"}:
                values[key] = _strip_runtime_value(raw_value)
    return values


def resolve_log_path(source: Literal["backend", "worker", "frontend"]) -> Path:
    if source not in LOG_ENV_KEYS:
        raise ValueError("不支持的日志来源")
    runtime = _load_runtime_values()
    project_dir = Path(runtime.get("PROJECT_DIR") or PROJECT_ROOT).expanduser()
    raw_path = runtime.get(LOG_ENV_KEYS[source])
    path = Path(raw_path).expanduser() if raw_path else project_dir / f"{source}.log"
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve(strict=False)


def redact_log_secrets(content: str) -> str:
    redacted = _SECRET_PATTERNS[0].sub("sk-***", content)
    for pattern in _SECRET_PATTERNS[1:]:
        redacted = pattern.sub(r"\1***", redacted)
    return redacted


def _tail_lines(path: Path, line_limit: int) -> tuple[list[str], bool]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        file_size = handle.tell()
        position = file_size
        chunks: list[bytes] = []
        newline_count = 0

        while position > 0 and newline_count <= line_limit:
            chunk_size = min(64 * 1024, position, MAX_TAIL_BYTES - sum(map(len, chunks)))
            if chunk_size <= 0:
                break
            position -= chunk_size
            handle.seek(position)
            chunk = handle.read(chunk_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    lines = text.splitlines()
    truncated = position > 0 or len(lines) > line_limit
    if position > 0 and lines:
        # The first line may start in the middle because reading is byte-limited.
        lines = lines[1:]
    return lines[-line_limit:], truncated


def read_system_log(
    source: Literal["backend", "worker", "frontend"],
    *,
    lines: int = 300,
    keyword: str = "",
) -> dict[str, object]:
    path = resolve_log_path(source)
    normalized_keyword = keyword.strip().casefold()
    requested_lines = max(20, min(int(lines), 2_000))
    scan_lines = MAX_FILTER_SCAN_LINES if normalized_keyword else requested_lines

    try:
        stat = path.stat()
        tailed_lines, truncated = _tail_lines(path, scan_lines)
    except FileNotFoundError:
        return {
            "source": source,
            "path": str(path),
            "available": False,
            "size_bytes": 0,
            "updated_at": None,
            "line_count": 0,
            "truncated": False,
            "content": "",
        }
    except OSError as exc:
        return {
            "source": source,
            "path": str(path),
            "available": False,
            "size_bytes": 0,
            "updated_at": None,
            "line_count": 0,
            "truncated": False,
            "content": "",
            "error": f"无法读取日志：{exc.__class__.__name__}",
        }

    if normalized_keyword:
        matching_lines = [line for line in tailed_lines if normalized_keyword in line.casefold()]
        truncated = truncated or len(matching_lines) > requested_lines
        tailed_lines = matching_lines[-requested_lines:]

    content = redact_log_secrets("\n".join(tailed_lines))
    return {
        "source": source,
        "path": str(path),
        "available": True,
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "line_count": len(tailed_lines),
        "truncated": truncated,
        "content": content,
    }
