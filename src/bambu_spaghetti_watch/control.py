"""Signed control through the vendored bambu-headless bridge.

Firmware since 2025 verifies every control command. The plugin signs those over
the printer's local MQTT session; the cloud user session does not expose that
publish. So pause/resume/cancel use the vendored bridge, which does the same
bring-up Studio does, while status and camera stay on the cloud.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "vendor" / "bambu-headless" / "bambu.py"


def available() -> bool:
    return CLI.exists()


def run(action: str, host: str, serial: str, job: str,
        timeout: int = 150) -> tuple[bool, str]:
    """Send one signed control command. Returns ``(ok, message)``; never raises."""
    if action not in ("pause", "resume", "cancel"):
        return False, f"unsupported control action {action!r}"
    if not available():
        return False, "signed control needs vendor/bambu-headless/bambu.py"
    if not host:
        return False, "signed control needs the printer's LAN address"
    if not job:
        return False, "signed control needs the current job name"

    python = os.environ.get("BSW_PYTHON", sys.executable)
    command = [python, str(CLI), action, "--host", host, "--serial", serial,
               "--job", job, "--yes"]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{action} timed out after {timeout}s"
    except OSError as err:
        return False, f"{action} could not run: {err}"

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode == 0:
        return True, output or f"{action} sent"
    return False, error or output or f"{action} failed with exit {result.returncode}"
