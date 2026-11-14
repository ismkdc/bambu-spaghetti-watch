"""Re-exec under Studio's executable-path shim.

The networking plugin only signs cloud requests when ``_NSGetExecutablePath()``
looks like the installed Studio binary. ``vendor/bambu-headless`` ships the
shim (AGPL-3.0); this module loads it before the plugin is opened.

``DYLD_INSERT_LIBRARIES`` must be set before launch, so the first call re-execs
the process. The shim also breaks Python's venv detection (Python finds its
prefix through the same call), so ``PYTHONPATH`` carries the venv site-packages
across the exec.
"""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "vendor/bambu-headless/h2d_host_identity.dylib"
STUDIO_EXE = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"


def ensure() -> None:
    if os.environ.get("BSW_SHIM") == "1" or not SHIM.exists():
        return
    # Tests mock the plugin and must not replace the pytest process.
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return
    site = sysconfig.get_paths()["purelib"]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [site, str(ROOT / "src")]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = ":".join(parts)
    env["DYLD_INSERT_LIBRARIES"] = str(SHIM)
    env["H2D_STUDIO_EXECUTABLE"] = STUDIO_EXE
    env["BSW_SHIM"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, env)
