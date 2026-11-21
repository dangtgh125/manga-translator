"""Google Cloud Translation v3 adapter (translateText with explicit language codes and optional glossary).

Credential: a service-account JSON with the minimal Cloud Translation role, stored encrypted.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from ..config import get_settings
from ..logging_utils import redact
from .base import ProviderError, RegionText, TranslatedRegion, TranslationContext

log = logging.getLogger(__name__)
_LANG = {"ja": "ja", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW", "vi": "vi", "en": "en"}
_SCOPE = "https://www.googleapis.com/auth/cloud-translation"
_BASE = "https://translation.googleapis.com/v3"
_MAX_STRINGS = 128
_MAX_CHARS = 25_000
# Refresh the access token this long before google-auth reports it as expired, so a request
# that is already in flight never carries a token that expires mid-way through a long job.
_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)


class GoogleProvider:
    kind = "google"
    data_recipient = "Google Cloud Translation (translation.googleapis.com)"

    def __init__(self, model_id: str, secret: str | None, options: dict[str, Any] | None = None) -> None:
        self.model_id = model_id or "nmt"
        self.secret = secret or ""
        self.options = options or {}
        self.location = str(self.options.get("location") or "global")
        # The google-auth credentials object owns the token and its expiry; it is kept for the
        # lifetime of the provider and refreshed under a lock (translate_batch may run from a
        # ThreadPool). A bare token string is never cached beyond the credentials' validity.
        self._creds: Any | None = None
        self._auth_lock = threading.Lock()

    def _project(self) -> str:
        pid = self.options.get("project_id")
        if not pid:
            try:
                pid = json.loads(self.secret).get("project_id")
            except (json.JSONDecodeError, AttributeError):
                pid = None
        if not pid:
            raise ProviderError("Google project_id is required", retryable=False)
        return str(pid)

    def _load_credentials(self) -> Any:
        """Build the service-account credentials object (no network; the token is fetched on refresh)."""
        if not self.secret:
            raise ProviderError("Google service-account JSON is required", retryable=False, status=401)
        try:
            info = json.loads(self.secret)
        except json.JSONDecodeError as exc:
            raise ProviderError("Google credential is not valid JSON", retryable=False) from exc
        try:
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Google auth failed: {redact(str(exc))[:200]}", retryable=False, status=401) from exc

    @staticmethod
    def _needs_refresh(creds: Any) -> bool:
        """True when the credentials hold no usable token, or one that expires within the safety margin."""
        if not getattr(creds, "token", None) or not getattr(creds, "valid", False):
            return True
        expiry = getattr(creds, "expiry", None)
        if expiry is None:
            return False
        # google-auth stores expiry as a naive UTC datetime; tolerate aware values too.
        now = datetime.now(UTC) if expiry.tzinfo is not None else datetime.now(UTC).replace(tzinfo=None)
        return bool(now + _TOKEN_REFRESH_MARGIN >= expiry)

    def _access_token(self) -> str:
        """Return a token valid for at least the safety margin, refreshing the kept credentials if needed."""
        with self._auth_lock:
            if self._creds is None:
                self._creds = self._load_credentials()
            creds = self._creds
            if self._needs_refresh(creds):
                try:
                    from google.auth.transport.requests import Request

                    creds.refresh(Request())
                except Exception as exc:  # noqa: BLE001
                    raise ProviderError(
                        f"Google auth failed: {redact(str(exc))[:200]}", retryable=False, status=401
                    ) from exc
                log.debug("google access token refreshed (expiry=%s)", getattr(creds, "expiry", None))
            token = getattr(creds, "token", None)
            if not token:
                raise ProviderError("Google auth returned no access token", retryable=False, status=401)
            return str(token)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        s = get_settings()
        # Resolve the token immediately before each request so a long job never reuses an expired one.
        token = self._access_token()
        try:
            with requests.request(
                method,
                f"{_BASE}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=s.provider_timeout_seconds,
                allow_redirects=False,
                stream=True,
            ) as resp:
                raw = b""
                for chunk in resp.iter_content(65536):
                    raw += chunk
                    if len(raw) > s.provider_max_response_bytes:
                        raise ProviderError("provider response too large", retryable=False)
                status_code = int(resp.status_code)
        except requests.Timeout as exc:
            raise ProviderError("Google request timed out", retryable=True) from exc
        except requests.RequestException as exc:
            raise ProviderError(f"Google connection error: {exc.__class__.__name__}", retryable=True) from exc
        if status_code >= 400:
            if status_code == 401:
                # Token rejected (revoked/rotated): drop the credentials so the next call re-authenticates.
                with self._auth_lock:
                    self._creds = None
            retryable = status_code == 429 or status_code >= 500
            raise ProviderError(
                f"Google HTTP {status_code}: {redact(raw[:300].decode('utf-8', 'replace'))}",
                retryable=retryable,
                status=status_code,
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Google returned non-JSON body", retryable=True) from exc

    def test_connection(self) -> dict[str, Any]:
        project = self._project()
        data = self._call("GET", f"/projects/{project}/locations/{self.location}/supportedLanguages")
        langs = [item.get("languageCode", "") for item in data.get("languages", [])]
        msg = "connected"
        if self.options.get("glossary_id"):
            msg += f"; glossary '{self.options['glossary_id']}' will be used"
        return {
            "ok": True,
            "message": msg,
            "models": [f"{self.location}:{self.model_id}"] if langs else [],
        }

    def translate_batch(self, items: list[RegionText], ctx: TranslationContext) -> list[TranslatedRegion]:
        project = self._project()
        target = _LANG.get(ctx.target_lang, ctx.target_lang)
        results: list[TranslatedRegion] = []
        # Group by explicit source language; unknown -> let Google detect.
        groups: dict[str, list[RegionText]] = {}
        for it in items:
            groups.setdefault(_LANG.get(it.source_lang, ""), []).append(it)
        for src, group in groups.items():
            for chunk in _chunks(group):
                body: dict[str, Any] = {
                    "contents": [it.text for it in chunk],
                    "targetLanguageCode": target,
                    "mimeType": "text/plain",
                }
                if src:
                    body["sourceLanguageCode"] = src
                glossary_id = self.options.get("glossary_id")
                if glossary_id:
                    body["glossaryConfig"] = {
                        "glossary": f"projects/{project}/locations/{self.location}/glossaries/{glossary_id}"
                    }
                data = self._call("POST", f"/projects/{project}/locations/{self.location}:translateText", body)
                translations = data.get("glossaryTranslations") or data.get("translations") or []
                if len(translations) != len(chunk):
                    raise ProviderError("Google returned a different number of translations", retryable=True)
                for it, tr in zip(chunk, translations, strict=True):
                    results.append(TranslatedRegion(it.region_id, str(tr.get("translatedText", "")).strip()))
        by_id = {r.region_id: r for r in results}
        return [by_id[it.region_id] for it in items]


def _chunks(items: list[RegionText]) -> list[list[RegionText]]:
    out: list[list[RegionText]] = []
    cur: list[RegionText] = []
    chars = 0
    for it in items:
        if cur and (len(cur) >= _MAX_STRINGS or chars + len(it.text) > _MAX_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(it)
        chars += len(it.text)
    if cur:
        out.append(cur)
    return out
