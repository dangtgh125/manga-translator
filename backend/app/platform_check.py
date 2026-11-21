"""Startup diagnostics for known platform quirks (logged, never fatal)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def cpu_flags() -> set[str]:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("features", "flags")):
                return set(line.split(":", 1)[1].split())
    except OSError:
        pass
    return set()


def warn_sme_without_sve() -> bool:
    flags = cpu_flags()
    if "sme" in flags and "sve" not in flags:
        log.warning(
            "CPU advertises SME without SVE (typical of Apple-silicon VMs); native wheels may crash with "
            "SIGILL. Build with COMPAT_SME_VM=1 (Makefile auto-detects on Podman)."
        )
        return True
    return False
