"""SQLite access with WAL mode and ordered SQL migrations."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def migrate(path: Path | None = None) -> None:
    db_path = path or get_settings().db_path
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = int(sql_file.name.split("_", 1)[0])
                if version in applied:
                    continue
                log.info("applying migration %s", sql_file.name)
                # version is an int parsed from our own filenames; applied_at is generated here.
                marker = f"INSERT INTO schema_migrations(version, applied_at) VALUES ({int(version)}, '{utcnow()}');"  # noqa: S608
                script = "BEGIN;\n" + sql_file.read_text(encoding="utf-8") + "\n" + marker + "\nCOMMIT;"
                try:
                    conn.executescript(script)
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        finally:
            conn.close()


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = _connect(path or get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
