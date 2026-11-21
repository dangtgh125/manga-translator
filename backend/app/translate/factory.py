"""Build a provider instance from a stored profile or an inline test request."""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..security.secrets import decrypt_secret
from .base import ProviderError, TranslationProvider


def build_provider(
    provider: str, model_id: str, endpoint: str | None, options: dict[str, Any], secret: str | None
) -> TranslationProvider:
    s = get_settings()
    if provider == "mock":
        if not s.mock_providers:
            raise ProviderError("mock provider is disabled outside test mode", retryable=False)
        from .mock import MockProvider

        return MockProvider(model_id, options, secret)
    if provider == "google":
        from .google import GoogleProvider

        return GoogleProvider(model_id, secret, options)
    if provider == "anthropic":
        from .anthropic_messages import AnthropicProvider

        return AnthropicProvider(model_id, secret, options)
    if provider in ("openai", "responses_compatible"):
        from .openai_responses import ResponsesProvider

        return ResponsesProvider(provider, model_id, secret, endpoint, options)
    raise ProviderError(f"unknown provider '{provider}'", retryable=False)


def provider_from_profile(profile: dict[str, Any]) -> TranslationProvider:
    import json

    secret = decrypt_secret(profile["secret_enc"]) if profile.get("secret_enc") else None
    return build_provider(
        profile["provider"],
        profile["model_id"],
        profile.get("endpoint"),
        json.loads(profile.get("options_json") or "{}"),
        secret,
    )
