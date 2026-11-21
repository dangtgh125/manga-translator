"""Structured logging with redaction of anything that looks like a credential.

Logs carry technical metadata only: never image bytes, OCR text, translations or keys.
"""

from __future__ import annotations

import logging
import re
import sys

# Common API key shapes plus any long high-entropy token.
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_\.=]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return redact(msg)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Third-party loggers can be chatty and may echo request bodies; keep them quiet.
    for noisy in ("httpx", "httpcore", "urllib3", "PIL", "paddlex", "paddleocr", "transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def safe_error(exc: BaseException, limit: int = 300) -> str:
    """Return a redacted, length-limited error string suitable for storing in the DB."""
    text = f"{exc.__class__.__name__}: {exc}"
    return redact(text)[:limit]
