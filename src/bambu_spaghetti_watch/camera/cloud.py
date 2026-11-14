"""Cloud camera: mint a live-view URL, then read frames from libBambuSource.

Bambu's remote view is a TUTK tunnel, not an RTSP endpoint. The URL that names
the tunnel is minted by Studio's networking plugin, which signs the request as
the Studio client. ``bridge/camera_probe.py`` drives that plugin in a short
child process (so the shim and its Python path changes stay out of the app),
and the URL it returns is fed to ``libBambuSource`` here.

No printer access code is used for the cloud path; the user's existing Studio
session and the installed plugin are.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from bambu_spaghetti_watch.camera import bambu_source

CACHE = Path.home() / "Library/Caches/bambu-watch/liveview_url.txt"
CACHE_MAX_AGE = 1800.0
MINT_TIMEOUT = 120.0


def _bridge_dir() -> Path | None:
    override = os.environ.get("BAMBU_BRIDGE_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "bridge"
        if (candidate / "camera_probe.py").exists():
            return candidate
    return None


class CloudCamera:
    """A source of JPEG frames over Bambu's cloud live-view tunnel."""

    def __init__(self, cfg: Any = None) -> None:
        self.cfg = cfg
        self.last_error: str | None = None
        self._url: str | None = None
        self._url_at: float = 0.0
        self.configuration_problem = self._check()

    def _check(self) -> str | None:
        bridge = _bridge_dir()
        if bridge is None:
            return (
                "cloud camera needs bridge/camera_probe.py; set BAMBU_BRIDGE_DIR "
                "to the project's bridge directory"
            )
        if not (bridge / "libbswbridge.dylib").exists():
            return f"cloud camera bridge is not built: {bridge / 'libbswbridge.dylib'}"
        if not bambu_source.LIB.exists():
            return f"cloud camera library is missing: {bambu_source.LIB}"
        return None

    async def grab(self) -> bytes | None:
        return await asyncio.to_thread(self._grab_sync)

    def _grab_sync(self) -> bytes | None:
        self.last_error = None
        url = self._current_url()
        if url is None:
            self.last_error = "cloud camera could not mint a live-view URL"
            return None

        frame = bambu_source.grab_frame(url)
        if frame is None:
            # The ticket may have aged out; mint once more before giving up.
            self._url = None
            url = self._mint()
            if url is None:
                self.last_error = "cloud camera could not refresh the live-view URL"
                return None
            frame = bambu_source.grab_frame(url)
            if frame is None:
                self.last_error = "cloud camera returned no frame"
        return frame

    def _current_url(self) -> str | None:
        now = time.time()
        if self._url and now - self._url_at < CACHE_MAX_AGE:
            return self._url
        try:
            if CACHE.exists() and now - CACHE.stat().st_mtime < CACHE_MAX_AGE:
                cached = CACHE.read_text().strip()
                if cached.startswith("bambu:///"):
                    self._url, self._url_at = cached, CACHE.stat().st_mtime
                    return cached
        except OSError:
            pass
        return self._mint()

    def _mint(self) -> str | None:
        bridge = _bridge_dir()
        if bridge is None:
            return None
        command = [sys.executable, str(bridge / "camera_probe.py"),
                   "--timeout", "30", "--out", str(CACHE)]
        serial = getattr(self.cfg, "serial", None) if self.cfg is not None else None
        if serial:
            command += ["--serial", serial]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=MINT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        for line in reversed(result.stdout.splitlines()):
            line = line.strip()
            if line.startswith("bambu:///"):
                self._url, self._url_at = line, time.time()
                return line
        try:
            cached = CACHE.read_text().strip()
        except OSError:
            return None
        if cached.startswith("bambu:///"):
            self._url, self._url_at = cached, time.time()
            return cached
        return None
