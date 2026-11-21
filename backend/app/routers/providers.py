from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import repo, schemas, serializers
from ..config import get_settings
from ..deps import get_db
from ..logging_utils import redact
from ..security.netguard import EndpointRejected, validate_custom_endpoint
from ..security.secrets import SecretsUnavailable, decrypt_secret, encrypt_secret, secrets_available
from ..translate.base import ProviderError
from ..translate.factory import build_provider

router = APIRouter(prefix="/api/providers", tags=["providers"])
DB = Annotated[sqlite3.Connection, Depends(get_db)]

_RECIPIENT = {
    "google": "Google Cloud Translation",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "responses_compatible": "custom endpoint",
    "mock": "nobody (mock)",
}


def _check_endpoint(provider: str, endpoint: str | None) -> str | None:
    if provider == "responses_compatible":
        if not endpoint:
            raise HTTPException(400, "endpoint is required for responses_compatible")
        try:
            validate_custom_endpoint(endpoint, resolve=False)
        except EndpointRejected as exc:
            raise HTTPException(400, f"endpoint rejected: {exc}") from exc
        return endpoint.rstrip("/")
    if provider == "openai":
        return None
    return None


def _check_mock(provider: str) -> None:
    if provider == "mock" and not get_settings().mock_providers:
        raise HTTPException(400, "mock provider is disabled")


def _encrypt(secret: str | None) -> bytes | None:
    if secret is None or secret == "":
        return None
    if not secrets_available():
        raise HTTPException(503, "credential storage unavailable: no master key configured (MT_MASTER_KEY_FILE)")
    return encrypt_secret(secret)


@router.get("", response_model=list[schemas.ProviderProfileOut])
def list_profiles(conn: DB) -> list[schemas.ProviderProfileOut]:
    return [serializers.provider_out(r) for r in repo.list_providers(conn)]


@router.post("", response_model=schemas.ProviderProfileOut, status_code=201)
def create_profile(body: schemas.ProviderProfileCreate, conn: DB) -> schemas.ProviderProfileOut:
    _check_mock(body.provider)
    endpoint = _check_endpoint(body.provider, body.endpoint)
    row = repo.create_provider(
        conn,
        body.name,
        body.provider,
        body.model_id,
        endpoint,
        _safe_options(body.options),
        _encrypt(body.secret),
    )
    return serializers.provider_out(row)


@router.patch("/{provider_id}", response_model=schemas.ProviderProfileOut)
def update_profile(provider_id: str, body: schemas.ProviderProfileUpdate, conn: DB) -> schemas.ProviderProfileOut:
    current = repo.get_provider(conn, provider_id)
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.model_id is not None:
        fields["model_id"] = body.model_id
    if body.endpoint is not None:
        fields["endpoint"] = _check_endpoint(current["provider"], body.endpoint)
    if body.options is not None:
        fields["options_json"] = json.dumps(_safe_options(body.options))
    return serializers.provider_out(repo.update_provider(conn, provider_id, fields))


@router.put("/{provider_id}/secret", response_model=schemas.ProviderProfileOut)
def set_secret(provider_id: str, body: schemas.ProviderSecretSet, conn: DB) -> schemas.ProviderProfileOut:
    repo.get_provider(conn, provider_id)
    return serializers.provider_out(repo.update_provider(conn, provider_id, {"secret_enc": _encrypt(body.secret)}))


@router.delete("/{provider_id}/secret", response_model=schemas.ProviderProfileOut)
def delete_secret(provider_id: str, conn: DB) -> schemas.ProviderProfileOut:
    repo.get_provider(conn, provider_id)
    return serializers.provider_out(repo.update_provider(conn, provider_id, {"secret_enc": None}))


@router.delete("/{provider_id}", status_code=204)
def delete_profile(provider_id: str, conn: DB) -> None:
    repo.delete_provider(conn, provider_id)


@router.post("/test", response_model=schemas.ProviderTestResult)
def test_provider(body: schemas.ProviderTestRequest, conn: DB) -> schemas.ProviderTestResult:
    """Connectivity check. Returns status + usable models; errors are redacted, secrets never echoed."""
    if body.profile_id:
        row = repo.get_provider(conn, body.profile_id)
        provider, model_id, endpoint = row["provider"], row["model_id"], row.get("endpoint")
        options = json.loads(row["options_json"] or "{}")
        try:
            secret = decrypt_secret(row["secret_enc"]) if row.get("secret_enc") else None
        except SecretsUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if body.secret:
            secret = body.secret
        if body.model_id:
            model_id = body.model_id
    else:
        if not body.provider:
            raise HTTPException(400, "provider or profile_id is required")
        provider, model_id, endpoint, options, secret = (
            body.provider,
            body.model_id or "",
            body.endpoint,
            body.options,
            body.secret,
        )
    _check_mock(provider)
    endpoint = _check_endpoint(provider, endpoint)
    try:
        inst = build_provider(provider, model_id, endpoint, _safe_options(options), secret)
        result = inst.test_connection()
        return schemas.ProviderTestResult(
            ok=bool(result.get("ok", True)),
            provider=provider,
            model_id=inst.model_id,
            message=redact(str(result.get("message", "")))[:300],
            models=[str(m) for m in result.get("models", [])][:200],
            data_recipient=inst.data_recipient,
        )
    except ProviderError as exc:
        return schemas.ProviderTestResult(
            ok=False,
            provider=provider,
            model_id=model_id,
            message=redact(str(exc))[:300],
            data_recipient=_RECIPIENT.get(provider, provider),
        )
    except SecretsUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{provider_id}/estimate/{project_id}", response_model=schemas.CostEstimate)
def estimate(provider_id: str, project_id: str, conn: DB) -> schemas.CostEstimate:
    row = repo.get_provider(conn, provider_id)
    repo.get_project(conn, project_id)
    regions = 0
    chars = 0
    for p in repo.list_pages(conn, project_id):
        for r in repo.list_regions(conn, p["id"]):
            if r["source_text"].strip():
                regions += 1
                chars += len(r["source_text"])
    note = (
        f"Text of {regions} regions ({chars} characters) will be sent to "
        f"{_RECIPIENT.get(row['provider'], row['provider'])}. "
        "Images are never sent. Check your provider's pricing for the exact cost."
    )
    return schemas.CostEstimate(regions=regions, characters=chars, provider=row["provider"], note=note)


def _safe_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep provider options small and free of secrets-like keys."""
    out: dict[str, Any] = {}
    for k, v in options.items():
        if not isinstance(k, str) or len(k) > 64:
            continue
        if any(w in k.lower() for w in ("key", "secret", "token", "password")):
            continue
        if isinstance(v, str | int | float | bool) or isinstance(v, dict | list):
            out[k] = v
    if len(json.dumps(out)) > 4000:
        raise HTTPException(400, "options too large")
    return out
