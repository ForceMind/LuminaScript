import asyncio
from collections import deque
import math
import time

from core.config import settings


_failures: dict[str, deque[float]] = {}
_lock = asyncio.Lock()


async def get_retry_after(key: str) -> int:
    now = time.monotonic()
    cutoff = now - settings.login_attempt_window_seconds
    async with _lock:
        attempts = _failures.get(key)
        if not attempts:
            return 0
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            _failures.pop(key, None)
            return 0
        if len(attempts) < settings.login_attempt_max:
            return 0
        return max(
            1,
            math.ceil(
                attempts[0] + settings.login_attempt_window_seconds - now
            ),
        )


async def record_failure(key: str) -> None:
    now = time.monotonic()
    cutoff = now - settings.login_attempt_window_seconds
    async with _lock:
        attempts = _failures.setdefault(key, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        attempts.append(now)

        if len(_failures) > 10000:
            expired_keys = [
                candidate
                for candidate, candidate_attempts in _failures.items()
                if not candidate_attempts or candidate_attempts[-1] <= cutoff
            ]
            for candidate in expired_keys:
                _failures.pop(candidate, None)


async def clear_failures(key: str) -> None:
    async with _lock:
        _failures.pop(key, None)
