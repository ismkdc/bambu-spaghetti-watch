"""Signed control commands, through the user's installed Bambu Studio plugin.

Firmware since Jan 2025 verifies every control command and answers
``84033543 mqtt message verify failed`` to anything unsigned. The only way to
send a verified command without Developer Mode is to let Studio's networking
plugin sign it.

Re-implementing that bring-up is a long detour (device table, selected machine,
certificate store), so this module calls the vendored `bambu-headless` CLI,
which already does it on macOS and is tested upstream. Nothing is copied from
Bambu; the plugin stays in the user's Studio installation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

CLI = Path(__file__).resolve().parents[2] / "vendor" / "bambu-headless" / "bambu.py"

ACTIONS = ("pause", "resume", "cancel")

Runner = Callable[..., object]


def available() -> bool:
    return CLI.exists()


def run(
    action: str,
    host: str,
    serial: str,
    job: str,
    timeout: int = 150,
    runner: Runner | None = None,
) -> tuple[bool, str]:
    """Send one control command. Returns ``(ok, message)``; never raises."""
    if action not in ACTIONS:
        return False, f"unsupported control action {action!r}"

    if not CLI.exists():
        return False, (
            "signed control needs the vendored bambu-headless bridge, which is "
            f"missing at {CLI}"
        )

    if not job:
        return False, "no job name known yet; wait for the first status report"

    command = [
        sys.executable,
        str(CLI),
        action,
        "--host",
        host,
        "--serial",
        serial,
        "--job",
        job,
        "--yes",
    ]

    runner = runner or (lambda cmd, **kw: subprocess.run(cmd, capture_output=True, text=True, timeout=kw["timeout"]))
    try:
        result = runner(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{action} timed out after {timeout}s"
    except OSError as err:
        return False, f"{action} could not run: {err}"

    output = (getattr(result, "stdout", "") or "").strip()
    error = (getattr(result, "stderr", "") or "").strip()

    if getattr(result, "returncode", 1) == 0:
        return True, output or f"{action} sent"
    return False, error or output or f"{action} failed with exit {result.returncode}"
