"""Data directory layout. All stored files use random IDs; user names are never paths."""

from __future__ import annotations

from pathlib import Path

from .config import get_settings
from .security.paths import resolve_under, secure_mkdir


def data_root() -> Path:
    root = get_settings().data_dir
    secure_mkdir(root)
    return root


def project_dir(project_id: str) -> Path:
    p = resolve_under(data_root(), Path("projects") / project_id)
    secure_mkdir(p)
    return p


def page_original_path(project_id: str, page_id: str, fmt: str) -> Path:
    d = resolve_under(project_dir(project_id), "original")
    secure_mkdir(d)
    return d / f"{page_id}.{fmt}"


def page_work_path(project_id: str, page_id: str) -> Path:
    d = resolve_under(project_dir(project_id), "work")
    secure_mkdir(d)
    return d / f"{page_id}.png"


def page_render_path(project_id: str, page_id: str) -> Path:
    d = resolve_under(project_dir(project_id), "render")
    secure_mkdir(d)
    return d / f"{page_id}.png"


def abs_path(relative: str) -> Path:
    return resolve_under(data_root(), relative)


def rel_path(path: Path) -> str:
    return str(path.resolve().relative_to(data_root().resolve()))


def tmp_dir() -> Path:
    t = get_settings().tmp_dir
    secure_mkdir(t)
    return t
