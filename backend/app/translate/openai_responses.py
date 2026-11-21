"""OpenAI Responses API adapter; also used for Responses-compatible custom endpoints.

Requests go through the SSRF guard (pinned IP, HTTPS, allowlist, no redirects, bounded body).
`store: false` is sent so the provider does not retain conversation state.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..config import get_settings
from ..logging_utils import redact
from ..security.netguard import (
    EndpointRejected,
    bounded_read,
    pinned_client,
    validate_custom_endpoint,
)
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

log = logging.getLogger(__name__)
OPENAI_BASE = "https://api.openai.com/v1"


class ResponsesProvider:
    def __init__(
        self,
        kind: str,
        model_id: str,
        secret: str | None,
        endpoint: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.model_id = model_id or ("gpt-5" if kind == "openai" else "")
        self.secret = secret or ""
        self.options = options or {}
        base = OPENAI_BASE if kind == "openai" else (endpoint or "")
        if not base:
            raise ProviderError("endpoint is required for a Responses-compatible provider", retryable=False)
        self.base_url = base.rstrip("/")
        self.data_recipient = "OpenAI (api.openai.com)" if kind == "openai" else f"custom endpoint {self.base_url}"
        # structured_output: "auto" (json_schema, fall back to prompt-only JSON when the endpoint
        # returns no text), "json_schema" (never fall back) or "none" (prompt-only JSON).
        self.structured_output = str(self.options.get("structured_output", "auto"))
        self._format_unsupported = False

    def _target(self, path: str) -> Any:
        try:
            target = validate_custom_endpoint(self.base_url)
        except EndpointRejected as exc:
            raise ProviderError(f"endpoint rejected: {exc}", retryable=False) from exc
        return target, target.pinned_url + path

    def _headers(self, host: str) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json", "Host": host}
        if self.secret:
            h["Authorization"] = f"Bearer {self.secret}"
        for k, v in (self.options.get("headers") or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k.lower() not in ("host", "authorization"):
                h[k] = v
        return h

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        s = get_settings()
        target, url = self._target(path)
        # Per-profile timeout (reasoning models can take minutes per batch); bounded 5..600s.
        try:
            timeout = float(self.options.get("timeout_seconds") or s.provider_timeout_seconds)
        except (TypeError, ValueError):
            timeout = float(s.provider_timeout_seconds)
        timeout = max(5.0, min(600.0, timeout))
        try:
            with pinned_client(target, timeout=timeout) as client:
                req = client.build_request(
                    method,
                    url,
                    headers=self._headers(target.host if target.port == 443 else f"{target.host}:{target.port}"),
                    content=json.dumps(body).encode() if body is not None else None,
                    extensions={"sni_hostname": target.host},
                )
                resp = client.send(req, stream=True)
                try:
                    raw = bounded_read(resp, s.provider_max_response_bytes)
                finally:
                    resp.close()
        except httpx.TimeoutException as exc:
            raise ProviderError("provider request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider connection error: {exc.__class__.__name__}", retryable=True) from exc
        except EndpointRejected as exc:
            raise ProviderError(str(exc), retryable=False) from exc
        if resp.status_code in (301, 302, 303, 307, 308):
            raise ProviderError("provider attempted a redirect; refused", retryable=False)
        if resp.status_code >= 400:
            retry_after = None
            try:
                retry_after = float(resp.headers.get("retry-after", "")) if resp.headers.get("retry-after") else None
            except ValueError:
                retry_after = None
            snippet = redact(raw[:300].decode("utf-8", "replace"))
            retryable = resp.status_code == 429 or resp.status_code >= 500
            raise ProviderError(
                f"provider HTTP {resp.status_code}: {snippet}",
                retryable=retryable,
                status=resp.status_code,
                retry_after=retry_after,
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned non-JSON body", retryable=True) from exc

    def test_connection(self) -> dict[str, Any]:
        models: list[str] = []
        try:
            data = self._request("GET", "/models")
            models = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)][:200]
        except ProviderError as exc:
            if exc.status not in (404, 405):
                raise
        if self.model_id and models and self.model_id not in models:
            return {
                "ok": True,
                "message": f"connected; model '{self.model_id}' not listed by endpoint",
                "models": models,
            }
        return {"ok": True, "message": "connected", "models": models}

    def translate_batch(self, items: list[RegionText], ctx: TranslationContext) -> list[TranslatedRegion]:
        use_format = self.structured_output != "none" and not self._format_unsupported
        text = self._call(items, ctx, use_format)
        if not text and use_format and self.structured_output == "auto":
            # Some gateways/reasoning models return only a `reasoning` item when `text.format` is set.
            # Fall back to prompt-only JSON; validate_ai_output still enforces the schema and IDs.
            log.warning("endpoint returned no text with text.format; retrying without structured output")
            self._format_unsupported = True
            text = self._call(items, ctx, False)
        if not text:
            raise ProviderError("provider returned no text output", retryable=True)
        return validate_ai_output(text, items)

    def _call(self, items: list[RegionText], ctx: TranslationContext, with_format: bool) -> str:
        src = items[0].source_lang if items else "unknown"
        body: dict[str, Any] = {
            "model": self.model_id,
            "store": False,
            "input": [
                {"role": "system", "content": system_prompt(ctx, src)},
                {"role": "user", "content": user_payload(items, ctx)},
            ],
            "max_output_tokens": int(self.options.get("max_output_tokens", 8000)),
        }
        if with_format:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "translations",
                    "strict": True,
                    "schema": OUTPUT_SCHEMA,
                }
            }
        if self.options.get("reasoning_effort"):
            body["reasoning"] = {"effort": str(self.options["reasoning_effort"])}
        data = self._request("POST", "/responses", body)
        if data.get("status") == "incomplete":
            raise ProviderError("provider response incomplete (token limit?)", retryable=True)
        return _extract_output_text(data)


def _extract_output_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return _strip_fences(data["output_text"])
    parts: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "output_text" and isinstance(c.get("text"), str):
                parts.append(c["text"])
            elif isinstance(c, dict) and c.get("type") == "refusal":
                raise ProviderError("provider refused the request", retryable=False)
    return _strip_fences("".join(parts))


def _strip_fences(text: str) -> str:
    """Models answering without structured output sometimes wrap JSON in ``` fences."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
