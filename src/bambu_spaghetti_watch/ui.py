"""Menu bar surface.

The pure part (``icon_for``) is importable without pyobjc, so the state
vocabulary stays testable. ``MenuBarApp`` composes a ``rumps.App`` instead of
subclassing one, so importing this module never requires rumps at all.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Coroutine

ICONS = {
    "idle": "○",
    "watching": "●",
    "warning": "▲",
    "error": "✕",
}

# SF Symbols for the real status item; the text glyphs above are what the
# tests assert and what shows in terminals.
SYMBOLS = {
    "idle": "circle",
    "watching": "circle.fill",
    "warning": "exclamationmark.triangle.fill",
    "error": "xmark.octagon.fill",
}

APP_NAME = "Bambu Spaghetti Watch"
STUDIO_PATH = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
FRAME_DIR = Path.home() / "Library" / "Logs" / "bambu-spaghetti-watch"

LOGGER = logging.getLogger(__name__)


def icon_for(status: str, verdict: str) -> str:
    """Map (loop status, last verdict) to one menu bar glyph."""
    if status == "error":
        return ICONS["error"]
    if verdict in ("warning", "failure"):
        return ICONS["warning"]
    if status == "idle":
        return ICONS["idle"]
    return ICONS["watching"]


def symbol_for(status: str, verdict: str) -> str:
    """SF Symbol name for the status item, same rules as ``icon_for``."""
    if status == "error":
        return SYMBOLS["error"]
    if verdict in ("warning", "failure"):
        return SYMBOLS["warning"]
    if status == "idle":
        return SYMBOLS["idle"]
    return SYMBOLS["watching"]


class LoopRunner:
    """One long-lived event loop on a background thread.

    A fresh ``asyncio.run`` per tick would tear down anything that owns a
    socket — the RTSPS TLS proxy binds a port inside the loop and is handed a
    dead one on the next tick, which makes ffmpeg sit out its full timeout and
    freezes the menu bar while the main thread waits for it.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="bsw-loop", daemon=True
        )
        self._thread.start()

    def run(self, coro: Coroutine, timeout: float | None = None) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)


class MenuBarApp:
    """Thin shell: a rumps app whose timer runs one pipeline step per tick."""

    def __init__(self, cfg: Any, pipeline: Any, printer: Any, control: Any = None) -> None:
        import rumps  # imported here so tests never need pyobjc

        self._rumps = rumps
        self.cfg = cfg
        self.pipeline = pipeline
        self.printer = printer
        self.control = control

        self._app = rumps.App(APP_NAME, title=ICONS["idle"], quit_button="Quit")
        self._status_item = rumps.MenuItem("Status: starting")
        self._score_item = rumps.MenuItem("Failure score: 0.00")
        self._app.menu = [
            self._status_item,
            self._score_item,
            None,
            rumps.MenuItem("Show last frame", callback=self._show_frame),
            None,
            rumps.MenuItem("Pause print", callback=self._pause),
            rumps.MenuItem("Resume print", callback=self._resume),
            None,
            rumps.MenuItem("Pause watching (for Bambu Studio)", callback=self._pause_watching),
            rumps.MenuItem("Resume watching", callback=self._resume_watching),
        ]
        self._timer = rumps.Timer(self._tick, max(1, int(cfg.interval_sec)))
        self._runner = LoopRunner()
        self._paused_for_studio = False
        self._set_symbol(symbol_for("idle", "safe"))
        self._studio_watcher = threading.Thread(
            target=self._watch_studio, name="studio-watch", daemon=True
        )
        self._studio_watcher.start()

    def _watch_studio(self) -> None:
        """Release the cloud session only while Bambu Studio is starting.

        Studio cannot launch while this process holds the session, but it can
        share it once its own login has finished. So: on a new Studio process,
        release, let it start, then reconnect while Studio stays open.
        """
        def studio_running() -> bool:
            try:
                return subprocess.run(
                    ["pgrep", "-f", STUDIO_PATH], stdout=subprocess.DEVNULL
                ).returncode == 0
            except OSError:
                return False

        was = studio_running()
        while True:
            time.sleep(1.0)
            now = studio_running()
            if now and not was:
                LOGGER.info("Bambu Studio launched; releasing the cloud session")
                try:
                    self.printer.stop()
                except Exception:
                    LOGGER.exception("could not stop the cloud printer")
                for _ in range(25):
                    time.sleep(1.0)
                    if not studio_running():
                        break
                LOGGER.info("Bambu Studio is up; reconnecting the cloud session")
                try:
                    self.printer.start()
                except Exception:
                    LOGGER.exception("could not restart the cloud printer")
            was = now

    def _pause_watching(self, _sender: Any) -> None:
        """Release the cloud session so Bambu Studio can start.

        The Bambu cloud allows one session per account at a time; Studio cannot
        open while this process holds it.
        """
        self._paused_for_studio = True
        try:
            self._timer.stop()
        except Exception:
            pass
        self.printer.stop()
        self._status_item.title = "Status: paused (Bambu Studio)"

    def _resume_watching(self, _sender: Any) -> None:
        self._paused_for_studio = False
        self.printer.start()
        try:
            self._timer.start()
        except Exception:
            pass
        self._status_item.title = "Status: resuming"

    def _show_frame(self, _sender: Any) -> None:
        frame = getattr(self.pipeline, "last_frame", None)
        if not frame:
            # The loop only grabs while a job is running; this menu item is a
            # way to check the camera (and the mint) at any time.
            try:
                frame = self._runner.run(self.pipeline.snapshot(), timeout=45)
            except Exception:
                frame = None
        if not frame:
            self._rumps.alert(
                "No frame yet",
                "The cloud live view returned no frame. Run `bambu-watch --probe` "
                "to see whether the cloud session, camera or model layer failed.",
            )
            return
        FRAME_DIR.mkdir(parents=True, exist_ok=True)
        path = FRAME_DIR / "last-frame.jpg"
        path.write_bytes(frame)
        subprocess.run(["open", str(path)], check=False)

    def _send(self, action: str) -> None:
        from bambu_spaghetti_watch.pipeline import send_control

        ok, message = send_control(self.printer, self.control, action)
        if ok:
            self._status_item.title = f"Status: {action} sent"
            return

        self._status_item.title = f"Status: {action} failed"
        self._rumps.alert(f"{action.capitalize()} failed", message)

    def _pause(self, _sender: Any) -> None:
        self._send("pause")

    def _resume(self, _sender: Any) -> None:
        self._send("resume")

    def run(self) -> None:
        try:
            from AppKit import NSApplication

            # No Dock icon: this is a menu bar accessory, not an app window.
            NSApplication.sharedApplication().setActivationPolicy_(1)
        except Exception:
            LOGGER.debug("could not switch to accessory activation policy", exc_info=True)

        self._timer.start()
        try:
            self._app.run()
        finally:
            self._runner.stop()
