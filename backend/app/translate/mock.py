"""Mock provider for tests: deterministic output plus configurable failure modes."""

from __future__ import annotations

import threading
import time
from typing import Any

from .base import (
    ProviderError,
    RegionText,
    TranslatedRegion,
    TranslationContext,
    validate_ai_output,
)

_counters: dict[str, int] = {}
_lock = threading.Lock()


class MockProvider:
    kind = "mock"
    data_recipient = "nobody (mock provider; no data leaves the machine)"

    def __init__(
        self,
        model_id: str = "mock-1",
        options: dict[str, Any] | None = None,
        secret: str | None = None,
    ) -> None:
        self.model_id = model_id or "mock-1"
        self.options = options or {}
        self.secret = secret

    def test_connection(self) -> dict[str, Any]:
        if self.options.get("fail_test"):
            raise ProviderError("mock test failure", retryable=False, status=401)
        return {"ok": True, "message": "mock provider ready", "models": ["mock-1", "mock-2"]}

    def _should_fail(self) -> str | None:
        mode = self.options.get("fail_mode")
        if not mode:
            return None
        times = int(self.options.get("fail_times", 1))
        key = self.options.get("fail_key", "default")
        with _lock:
            n = _counters.get(key, 0)
            if n >= times:
                return None
            _counters[key] = n + 1
        return str(mode)

    def translate_batch(self, items: list[RegionText], ctx: TranslationContext) -> list[TranslatedRegion]:
        delay = float(self.options.get("delay_seconds", 0))
        if delay:
            time.sleep(delay)
        mode = self._should_fail()
        if mode == "429":
            raise ProviderError("rate limited", retryable=True, status=429, retry_after=0.01)
        if mode == "500":
            raise ProviderError("server error", retryable=True, status=503)
        if mode == "timeout":
            raise ProviderError("timeout", retryable=True)
        if mode == "auth":
            raise ProviderError("unauthorized", retryable=False, status=401)
        if mode == "bad_schema":
            return validate_ai_output('{"nope": 1}', items)
        if mode == "missing_id":
            raw = {
                "translations": [
                    {"region_id": it.region_id, "translated_text": "x", "needs_context": False} for it in items[1:]
                ]
            }
            return validate_ai_output(raw, items)
        prefix = self.options.get("prefix", f"[{ctx.target_lang}]")
        raw = {
            "translations": [
                {
                    "region_id": it.region_id,
                    "translated_text": f"{prefix} {it.text}",
                    "needs_context": len(it.text) < 2,
                }
                for it in items
            ]
        }
        return validate_ai_output(raw, items)


def reset_mock_counters() -> None:
    with _lock:
        _counters.clear()
