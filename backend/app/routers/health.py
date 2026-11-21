from __future__ import annotations

from fastapi import APIRouter

from .. import schemas
from ..config import get_settings
from ..security.secrets import secrets_available
from ..worker.runner import heartbeat_is_fresh

router = APIRouter(tags=["health"])
VERSION = "0.1.0"


@router.get("/api/health", response_model=schemas.HealthOut)
def health() -> schemas.HealthOut:
    s = get_settings()
    hb = s.worker_heartbeat_file
    alive = heartbeat_is_fresh(hb) if hb.exists() else None
    return schemas.HealthOut(
        status="ok",
        version=VERSION,
        ocr_engine=s.ocr_engine,
        secrets_available=secrets_available(),
        worker_alive=alive,
    )
