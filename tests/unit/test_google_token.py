"""Google adapter: access-token refresh across a long job, thread-safety and HTTP response hygiene.

google-auth and the network are both mocked: fake modules are injected into ``sys.modules`` so the
adapter's lazy ``from google.oauth2 import service_account`` / ``google.auth.transport.requests``
imports resolve to controllable stand-ins, and ``requests.request`` is replaced by a fake response.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from app.translate import google as google_mod
from app.translate.base import ProviderError, RegionText, TranslationContext
from app.translate.google import GoogleProvider

SECRET = json.dumps({"type": "service_account", "project_id": "proj-1", "client_email": "sa@example"})
CTX = TranslationContext(target_lang="vi")


def _utcnow() -> datetime:
    # google-auth keeps ``expiry`` as a naive UTC datetime.
    return datetime.now(UTC).replace(tzinfo=None)


class FakeCredentials:
    """Minimal stand-in for google.oauth2.service_account.Credentials."""

    def __init__(self) -> None:
        self.token: str | None = None
        self.expiry: datetime | None = None
        self.refresh_calls = 0
        self.refresh_delay = 0.0
        self.fail_refresh: Exception | None = None
        self.lifetime = timedelta(hours=1)
        self.rebuilt: list[FakeCredentials] = []  # credentials objects built after this one

    @property
    def expired(self) -> bool:
        return self.expiry is not None and _utcnow() >= self.expiry

    @property
    def valid(self) -> bool:
        return self.token is not None and not self.expired

    def refresh(self, request) -> None:  # noqa: ANN001
        assert isinstance(request, FakeRequest)
        if self.refresh_delay:
            time.sleep(self.refresh_delay)
        if self.fail_refresh is not None:
            raise self.fail_refresh
        self.refresh_calls += 1
        self.token = f"tok-{self.refresh_calls}"
        self.expiry = _utcnow() + self.lifetime


class FakeRequest:
    pass


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> FakeCredentials:
    fake = FakeCredentials()
    seen_info: list = []

    class _Credentials:
        @staticmethod
        def from_service_account_info(info, scopes):  # noqa: ANN001
            seen_info.append((info, scopes))
            if len(seen_info) == 1:
                return fake
            # Like google-auth, a rebuilt credentials object starts without a token.
            fresh = FakeCredentials()
            fake.rebuilt.append(fresh)
            return fresh

    sa_mod = types.ModuleType("google.oauth2.service_account")
    sa_mod.Credentials = _Credentials  # type: ignore[attr-defined]
    req_mod = types.ModuleType("google.auth.transport.requests")
    req_mod.Request = FakeRequest  # type: ignore[attr-defined]
    for name, mod in {
        "google": types.ModuleType("google"),
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.service_account": sa_mod,
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": req_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    fake.seen_info = seen_info  # type: ignore[attr-defined]
    return fake


class FakeResponse:
    def __init__(self, status_code: int, payload: bytes) -> None:
        self.status_code = status_code
        self._payload = payload
        self.closed = False
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.close()

    def close(self) -> None:
        self.closed = True

    def iter_content(self, size: int):
        assert not self.closed
        for i in range(0, len(self._payload), size):
            yield self._payload[i : i + size]


def _install_http(monkeypatch: pytest.MonkeyPatch, *, status: int = 200, body: bytes | None = None):
    """Replace requests.request; returns (auth headers seen, responses created)."""
    headers: list[str] = []
    responses: list[FakeResponse] = []
    lock = threading.Lock()

    def fake_request(method, url, **kwargs):  # noqa: ANN001
        assert url.startswith("https://translation.googleapis.com/v3/")
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True and kwargs["timeout"] > 0
        req_body = kwargs.get("json") or {}
        if body is not None:
            payload = body
        elif "contents" in req_body:
            payload = json.dumps({"translations": [{"translatedText": "ok"}] * len(req_body["contents"])}).encode()
        else:  # GET supportedLanguages (test_connection)
            payload = json.dumps({"languages": [{"languageCode": "vi"}]}).encode()
        resp = FakeResponse(status, payload)
        with lock:
            headers.append(kwargs["headers"]["Authorization"])
            responses.append(resp)
        return resp

    monkeypatch.setattr(google_mod.requests, "request", fake_request)
    return headers, responses


def _page(n: int) -> list[RegionText]:
    return [RegionText(f"rg_{n}", f"text {n}", source_lang="ja")]


def test_token_expired_mid_job_is_refreshed_and_old_token_never_resent(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch)
    p = GoogleProvider("nmt", SECRET)

    # page 1: first request obtains the token
    assert p.translate_batch(_page(1), CTX)[0].text == "ok"
    assert creds.refresh_calls == 1 and headers == ["Bearer tok-1"]

    # token expires while the multi-page job is running
    creds.expiry = _utcnow() - timedelta(seconds=1)
    assert not creds.valid

    # page 2: the adapter notices before sending and refreshes; the old token is not sent again
    assert p.translate_batch(_page(2), CTX)[0].text == "ok"
    assert creds.refresh_calls == 2 and headers == ["Bearer tok-1", "Bearer tok-2"]

    # page 3: still valid -> no extra refresh, reuse tok-2
    assert p.translate_batch(_page(3), CTX)[0].text == "ok"
    assert creds.refresh_calls == 2 and headers == ["Bearer tok-1", "Bearer tok-2", "Bearer tok-2"]
    assert "Bearer tok-1" not in headers[1:]
    # the credentials object is kept on the instance, and no bare token is cached beside it
    assert p._creds is creds and not hasattr(p, "_token")
    assert creds.seen_info[0][0]["project_id"] == "proj-1" and creds.seen_info[0][1] == [google_mod._SCOPE]


def test_token_expiring_within_safety_margin_is_refreshed_early(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch)
    p = GoogleProvider("nmt", SECRET)
    p.translate_batch(_page(1), CTX)
    assert creds.refresh_calls == 1

    # Still valid per google-auth, but inside the 60 s margin -> refresh before the next request.
    creds.expiry = _utcnow() + timedelta(seconds=30)
    assert creds.valid
    p.translate_batch(_page(2), CTX)
    assert creds.refresh_calls == 2 and headers == ["Bearer tok-1", "Bearer tok-2"]

    # Comfortably outside the margin -> no refresh.
    creds.expiry = _utcnow() + timedelta(minutes=10)
    p.translate_batch(_page(3), CTX)
    assert creds.refresh_calls == 2 and headers[-1] == "Bearer tok-2"


def test_refresh_failure_maps_to_provider_error_and_sends_nothing(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch)
    p = GoogleProvider("nmt", SECRET)
    creds.fail_refresh = RuntimeError("invalid_grant: account disabled key=AIzaSySECRETSECRETSECRET")
    with pytest.raises(ProviderError) as ei:
        p.translate_batch(_page(1), CTX)
    assert ei.value.status == 401 and ei.value.retryable is False
    assert "Google auth failed" in str(ei.value)
    assert headers == []  # no HTTP request without a token

    # A later refresh that succeeds recovers the job on the same provider instance.
    creds.fail_refresh = None
    assert p.translate_batch(_page(1), CTX)[0].text == "ok"
    assert headers == ["Bearer tok-1"]


def test_refresh_failure_mid_job_after_expiry(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch)
    p = GoogleProvider("nmt", SECRET)
    p.translate_batch(_page(1), CTX)
    creds.expiry = _utcnow() - timedelta(seconds=1)
    creds.fail_refresh = RuntimeError("boom")
    with pytest.raises(ProviderError) as ei:
        p.translate_batch(_page(2), CTX)
    assert ei.value.status == 401
    assert headers == ["Bearer tok-1"]  # the expired token was never re-sent


def test_missing_or_bad_secret_is_provider_error(monkeypatch, creds):
    _install_http(monkeypatch)
    with pytest.raises(ProviderError) as ei:
        GoogleProvider("nmt", "", {"project_id": "proj-1"}).translate_batch(_page(1), CTX)
    assert ei.value.status == 401
    with pytest.raises(ProviderError) as ei:
        GoogleProvider("nmt", "{not json", {"project_id": "proj-1"}).translate_batch(_page(1), CTX)
    assert "not valid JSON" in str(ei.value)


def test_concurrent_batches_refresh_once_under_lock(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch)
    creds.refresh_delay = 0.05  # widen the race window
    p = GoogleProvider("nmt", SECRET)
    with ThreadPoolExecutor(max_workers=8) as ex:
        outs = list(ex.map(lambda n: p.translate_batch(_page(n), CTX), range(8)))
    assert all(o[0].text == "ok" for o in outs)
    assert creds.refresh_calls == 1
    assert headers == ["Bearer tok-1"] * 8


def test_http_responses_are_closed_after_reading(monkeypatch, creds):
    _, responses = _install_http(monkeypatch)
    p = GoogleProvider("nmt", SECRET)
    p.translate_batch(_page(1), CTX)
    p.test_connection()
    assert len(responses) == 2 and all(r.entered and r.closed for r in responses)


def test_http_response_closed_on_error_status_and_oversize(monkeypatch, creds):
    _, responses = _install_http(monkeypatch, status=503, body=b'{"error":"backend"}')
    p = GoogleProvider("nmt", SECRET)
    with pytest.raises(ProviderError) as ei:
        p.translate_batch(_page(1), CTX)
    assert ei.value.status == 503 and ei.value.retryable is True
    assert responses[-1].closed

    monkeypatch.setenv("MT_PROVIDER_MAX_RESPONSE_BYTES", "10")
    from app.config import reset_settings_cache

    reset_settings_cache()
    _, responses = _install_http(monkeypatch, body=b"x" * 100)
    with pytest.raises(ProviderError, match="too large"):
        p.translate_batch(_page(1), CTX)
    assert responses[-1].closed


def test_http_401_drops_credentials_so_next_call_reauthenticates(monkeypatch, creds):
    headers, _ = _install_http(monkeypatch, status=401, body=b'{"error":"UNAUTHENTICATED"}')
    p = GoogleProvider("nmt", SECRET)
    with pytest.raises(ProviderError) as ei:
        p.translate_batch(_page(1), CTX)
    assert ei.value.status == 401 and p._creds is None

    headers2, _ = _install_http(monkeypatch)
    p.translate_batch(_page(2), CTX)
    # credentials were rebuilt from the secret and freshly refreshed; the rejected token was not reused
    assert len(creds.rebuilt) == 1 and creds.rebuilt[0].refresh_calls == 1 and p._creds is creds.rebuilt[0]
    assert headers == ["Bearer tok-1"] and headers2 == ["Bearer tok-1"] and creds.refresh_calls == 1
