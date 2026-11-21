"""Random, URL-safe identifiers. Never derived from user input."""

from __future__ import annotations

import secrets

_PREFIXES = {"project": "prj", "page": "pg", "region": "rg", "job": "job", "provider": "prv"}


def new_id(kind: str) -> str:
    prefix = _PREFIXES[kind]
    return f"{prefix}_{secrets.token_urlsafe(16)}"
