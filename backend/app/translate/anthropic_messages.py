"""Anthropic Messages API adapter using the official SDK with structured outputs."""

from __future__ import annotations

import json
from typing import Any

from ..config import get_settings
from ..logging_utils import redact
from .base import (
    OUTPUT_SCHEMA,
    ProviderError,
    RegionText,
    TranslatedRegion,
    TranslationContext,
    system_prompt,
    user_payload,
    validate_ai_output,
)

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicProvider:
    kind = "anthropic"
    data_recipient = "Anthropic (api.anthropic.com)"

    def __init__(self, model_id: str, secret: str | None, options: dict[str, Any] | None = None) -> None:
        self.model_id = model_id or DEFAULT_MODEL
        self.secret = secret or ""
        self.options = options or {}

    def _client(self) -> Any:
        import anthropic

        if not self.secret:
            raise ProviderError("Anthropic API key is required", retryable=False, status=401)
        return anthropic.Anthropic(
            api_key=self.secret,
            timeout=float(get_settings().provider_timeout_seconds),
            max_retries=0,
        )

    def _wrap(self, exc: Exception) -> ProviderError:
        import anthropic

        if isinstance(exc, anthropic.RateLimitError):
            ra = exc.response.headers.get("retry-after") if exc.response is not None else None
            return ProviderError(
                "Anthropic rate limit",
                retryable=True,
                status=429,
                retry_after=float(ra) if ra and ra.replace(".", "", 1).isdigit() else None,
            )
        if isinstance(exc, anthropic.APIStatusError):
            retryable = exc.status_code >= 500
            return ProviderError(
                f"Anthropic HTTP {exc.status_code}: {redact(str(exc.message))[:200]}",
                retryable=retryable,
                status=exc.status_code,
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderError("Anthropic connection error", retryable=True)
        return ProviderError(f"Anthropic error: {redact(str(exc))[:200]}", retryable=False)

    def test_connection(self) -> dict[str, Any]:
        import anthropic

        client = self._client()
        try:
            page = client.models.list(limit=100)
            ids = [m.id for m in page.data]
            if self.model_id not in ids:
                try:
                    client.models.retrieve(self.model_id)
                except anthropic.NotFoundError:
                    return {
                        "ok": False,
                        "message": f"model '{self.model_id}' not found",
                        "models": ids,
                    }
            return {"ok": True, "message": "connected", "models": ids}
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc) from exc

    def translate_batch(self, items: list[RegionText], ctx: TranslationContext) -> list[TranslatedRegion]:
        client = self._client()
        src = items[0].source_lang if items else "unknown"
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": int(self.options.get("max_tokens", 16000)),
            "system": system_prompt(ctx, src),
            "messages": [{"role": "user", "content": user_payload(items, ctx)}],
            "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        }
        if self.options.get("effort"):
            kwargs["output_config"]["effort"] = str(self.options["effort"])
        try:
            response = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("Anthropic declined the request (refusal)", retryable=False)
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise ProviderError("Anthropic output truncated (max_tokens)", retryable=True)
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        if not text:
            raise ProviderError("Anthropic returned no text", retryable=True)
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Anthropic returned invalid JSON", retryable=True) from exc
        return validate_ai_output(text, items)
