import os
from pathlib import Path

import pytest
from app.security.paths import UnsafePathError, resolve_under, sanitize_display_name
from app.security.secrets import (
    SecretsUnavailable,
    decrypt_secret,
    encrypt_secret,
    secrets_available,
)


def test_resolve_under_blocks_traversal(tmp_path: Path):
    base = tmp_path / "data"
    base.mkdir()
    assert resolve_under(base, "projects/x/y.png").is_relative_to(base.resolve())
    for bad in ("../etc/passwd", "/etc/passwd", "a/../../b", "~root/x"):
        with pytest.raises(UnsafePathError):
            resolve_under(base, bad)


def test_resolve_under_blocks_symlink_escape(tmp_path: Path):
    base = tmp_path / "data"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, base / "link")
    with pytest.raises(UnsafePathError):
        resolve_under(base, "link/file")


def test_sanitize_display_name():
    assert sanitize_display_name("../../etc/passwd") == "etc_passwd" or ".." not in sanitize_display_name(
        "../../etc/passwd"
    )
    assert "/" not in sanitize_display_name("a/b\\c.png")
    assert sanitize_display_name("") == "page"
    assert sanitize_display_name("x\x00y\n.png") == "xy.png"
    assert len(sanitize_display_name("a" * 500)) == 120


def test_secret_roundtrip_and_missing_key(monkeypatch):
    assert secrets_available()
    token = encrypt_secret("sk-test-123")
    assert b"sk-test" not in token
    assert decrypt_secret(token) == "sk-test-123"
    monkeypatch.delenv("MT_MASTER_KEY")
    from app.config import reset_settings_cache
    from app.security.secrets import reset_secret_cache

    reset_settings_cache()
    reset_secret_cache()
    assert not secrets_available()
    with pytest.raises(SecretsUnavailable):
        encrypt_secret("x")
