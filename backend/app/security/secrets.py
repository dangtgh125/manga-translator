"""Provider credential encryption.

Secrets are encrypted with a master key that lives outside the DB/image/data volume
(a mounted secret file). Without a master key the API refuses to store credentials.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


class SecretsUnavailable(RuntimeError):
    pass


def _derive_fernet_key(raw: bytes) -> bytes:
    # Accept any sufficiently long random material; derive a 32-byte urlsafe key.
    if len(raw) < 32:
        raise SecretsUnavailable("master key must be at least 32 bytes of random data")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    s = get_settings()
    raw: bytes | None = None
    key_file = s.master_key_file
    if key_file is not None and str(key_file) not in ("", ".") and key_file.is_file():
        raw = key_file.read_bytes().strip()
    elif s.master_key:
        raw = s.master_key.encode()
    if not raw:
        raise SecretsUnavailable("no master key configured (MT_MASTER_KEY_FILE); credentials cannot be stored")
    return Fernet(_derive_fernet_key(raw))


def reset_secret_cache() -> None:
    _fernet.cache_clear()


def secrets_available() -> bool:
    try:
        _fernet()
        return True
    except SecretsUnavailable:
        return False


def encrypt_secret(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise SecretsUnavailable("stored credential cannot be decrypted with current master key") from exc
