"""FastAPI application: routers, security headers, error mapping, startup migrations."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import repo
from .config import get_settings
from .db import migrate
from .logging_utils import configure_logging, safe_error
from .platform_check import warn_sme_without_sve
from .routers import health, jobs, pages, projects, providers
from .security.paths import UnsafePathError

log = logging.getLogger(__name__)


class BodyTooLarge(HTTPException):
    """Request body exceeded ``max_upload_total_bytes``.

    Subclasses HTTPException so FastAPI's body-parsing wrapper re-raises it untouched (anything else becomes
    a generic 400) and the default handler answers 413 regardless of where in the stack it surfaces.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value.decode("latin-1").strip())
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    """Bound the whole request body at reception (PLAN §10 P1.5).

    Two layers, both read ``max_upload_total_bytes`` per request so tests/ops can tune it without restart:
    1. a declared Content-Length above the budget is refused with 413 before a single body byte is read;
    2. ``receive`` is wrapped to count delivered bytes, so chunked/undeclared bodies (or a client lying about
       Content-Length) abort with 413 as soon as the budget is exceeded. The multipart parser spools file parts
       to /tmp (tmpfs, charged to the container memory cgroup); aborting mid-stream is what keeps a single
       request from exhausting it. Starlette closes every spooled temp file when the stream raises.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = get_settings().max_upload_total_bytes
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            # Normally handled inside ExceptionMiddleware; this catches it when raised from an outer layer.
            if response_started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        resp = JSONResponse({"detail": "request body too large"}, status_code=413, headers={"Connection": "close"})
        await resp(scope, receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging(os.environ.get("MT_LOG_LEVEL", "INFO"))
    warn_sme_without_sve()
    migrate()
    s = get_settings()
    s.tmp_dir.mkdir(parents=True, exist_ok=True)
    log.info("api started (data_dir=%s, ocr_engine=%s)", s.data_dir, s.ocr_engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Manga Translator API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(pages.router)
    app.include_router(jobs.router)
    app.include_router(providers.router)

    # Added first so it sits inside security_headers: its 413s also get the headers below.
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(repo.NotFound)
    async def _nf(_: Request, exc: repo.NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(repo.VersionConflict)
    async def _vc(_: Request, exc: repo.VersionConflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(repo.LimitExceeded)
    async def _le(_: Request, exc: repo.LimitExceeded) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=413)

    @app.exception_handler(UnsafePathError)
    async def _up(_: Request, exc: UnsafePathError) -> JSONResponse:
        return JSONResponse({"detail": "invalid path"}, status_code=400)

    @app.exception_handler(Exception)
    async def _any(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error: %s", safe_error(exc))
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app


app: Any = create_app()
