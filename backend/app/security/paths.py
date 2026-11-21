"""Filesystem safety helpers: random names, traversal/symlink protection, sanitized export names."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path


class UnsafePathError(ValueError):
    pass


def resolve_under(base: Path, relative: str | Path) -> Path:
    """Join `relative` under `base` and verify the result stays inside `base` (no traversal, no symlink escape)."""
    base_real = base.resolve()
    rel = Path(relative)
    if rel.is_absolute():
        raise UnsafePathError("absolute paths are not allowed")
    for part in rel.parts:
        if part in ("..", "") or part.startswith("~"):
            raise UnsafePathError("path traversal detected")
    candidate = base_real / rel
    # Resolve symlinks of existing prefix to catch symlink escapes.
    resolved = candidate.resolve()
    if resolved != base_real and base_real not in resolved.parents:
        raise UnsafePathError("path escapes data directory")
    if candidate.exists() and candidate.is_symlink():
        raise UnsafePathError("symlinks are not allowed")
    return resolved


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_display_name(name: str, max_len: int = 120) -> str:
    """Make a user-provided filename safe for *display and export entry names* only.

    Never used as a storage path. Strips directories, control chars, normalises unicode.
    """
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _SAFE_NAME.sub("_", name).strip("._")
    if not name:
        name = "page"
    return name[:max_len]


def secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
