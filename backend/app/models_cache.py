"""Model cache integrity: record sha256 of downloaded model files on first use, verify afterwards.

Models are fetched by the OCR libraries from their official sources into MODELS_DIR (a volume).
A lock file `models.lock.json` pins each file's checksum; later starts verify and refuse to run
with tampered files. Missing files are simply downloaded again by the libraries.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
LOCK_NAME = "models.lock.json"
MODEL_SUFFIXES = {
    ".onnx",
    ".pdmodel",
    ".pdiparams",
    ".json",
    ".yml",
    ".yaml",
    ".safetensors",
    ".bin",
    ".txt",
}


class ModelIntegrityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _model_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        if (
            p.is_file()
            and p.suffix in MODEL_SUFFIXES
            and p.name != LOCK_NAME
            and ".locks" not in p.parts
            and ".no_exist" not in p.parts
        ):
            out.append(p)
    return sorted(out)


def verify_model_cache(root: Path) -> dict[str, str]:
    """Verify existing model files against the lock; record new ones. Raise on mismatch."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_NAME
    lock: dict[str, str] = {}
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelIntegrityError("models.lock.json is corrupt") from exc
    changed = False
    for path in _model_files(root):
        rel = str(path.relative_to(root))
        digest = _sha256(path)
        if rel in lock:
            if lock[rel] != digest:
                raise ModelIntegrityError(f"checksum mismatch for model file {rel}")
        else:
            lock[rel] = digest
            changed = True
    if changed:
        lock_path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")
        log.info("model lock updated (%s files)", len(lock))
    return lock
