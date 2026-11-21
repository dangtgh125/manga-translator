"""FastAPI dependencies."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from .db import connection


def get_db() -> Iterator[sqlite3.Connection]:
    with connection() as conn:
        yield conn
