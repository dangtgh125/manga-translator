"""Container healthchecks without shell quoting (Podman splits CMD strings on whitespace).

usage: python -m app.healthcheck api      -> GET /api/health on localhost
       python -m app.healthcheck worker   -> heartbeat file fresher than 90s
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "api"
    if kind == "api":
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as resp:  # noqa: S310
                return 0 if resp.status == 200 else 1
        except Exception:  # noqa: BLE001
            return 1
    path = os.environ.get("MT_WORKER_HEARTBEAT_FILE", "/state/worker.heartbeat")
    try:
        return 0 if time.time() - os.stat(path).st_mtime < 90 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
