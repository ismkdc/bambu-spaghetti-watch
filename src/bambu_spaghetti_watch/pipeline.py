"""The detection loop, with no I/O of its own beyond the objects it is given.

``step()`` is the whole unit of work: check state, grab a frame, run the model,
fold the score into the smoothing state, and maybe act. Everything it touches
is injected, so the loop is testable without a printer, a camera or weights.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import cv2
import numpy as np

from bambu_spaghetti_watch.camera.base import CameraNotConfigured
from bambu_spaghetti_watch.decision import PrintState, classify, score_from_detections

COOLDOWN_SECONDS = 60.0

LOGGER = logging.getLogger(__name__)

Action = Callable[[str, float], None]


Control = Callable[[str, str], tuple[bool, str]]


def send_control(printer: Any, control: Control | None, action: str) -> tuple[bool, str]:
    """Send pause/resume the way this printer actually accepts it.

    Firmware since Jan 2025 verifies control commands; a raw publish only earns
    HMS 0500-0500-0001-0007 on the printer's screen. When we know the printer
    verifies, the signed plugin path is the only correct one.
    """
    if getattr(printer, "developer_mode", None) is False:
        if control is None:
            return False, (
                "printer verifies MQTT commands (Developer Mode off) and no "
                "signed control path is configured"
            )
        job = getattr(printer, "job_name", None) or ""
        return control(action, job)

    if action == "pause":
        return (True, "") if printer.pause() else (False, "pause publish was rejected")
    if action == "resume":
        return (True, "") if printer.resume() else (False, "resume publish was rejected")
    return False, f"unsupported action {action!r}"


class Pipeline:
    def __init__(
        self,
        cfg: Any,
        camera: Any,
        detector: Any,
        printer: Any,
        action: Action | None = None,
        control: Callable[[str, str], tuple[bool, str]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.camera = camera
        self.detector = detector
        self.printer = printer
        self.action: Action = action or (lambda verdict, score: None)
        self.control = control

        self.decision = PrintState()
        self.last_action: float = float("-inf")
        self._was_running = False

        self.last_frame: bytes | None = None
        self.pause_failures = 0
        self.pause_blocked = 0
        self.controls_sent = 0
        self._blocked_logged = False
        self._unconfigured_logged = False

    async def step(self) -> str:
        """Run one iteration.

        Returns ``"idle"``, ``"no_frame"``, ``"safe"``, ``"warning"`` or
        ``"failure"``. Never raises for camera trouble; a dead camera reports
        ``"no_frame"`` and the caller keeps looping. A camera that is not
        configured at all reports the same, logged once rather than per tick.
        """
        running = self.printer.state == "RUNNING"

        if running and not self._was_running:
            # A new print starts a fresh smoothing state; the printer's
            # long-term baseline deliberately survives it.
            self.decision.reset_print()
        self._was_running = running

        if not running:
            return "idle"

        frame = await self._grab()
        if frame is None:
            return "no_frame"

        image = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return "no_frame"

        self.last_frame = frame

        detections = self.detector.infer(image)
        reading = self.decision.update(score_from_detections(detections))
        verdict = classify(reading, self.cfg.sensitivity)

        if verdict == "failure":
            self._maybe_act(reading)

        return verdict

    async def _grab(self) -> bytes | None:
        """A camera with no working configuration is terminal, not per-tick noise."""
        try:
            return await self.camera.grab()
        except CameraNotConfigured as err:
            if not self._unconfigured_logged:
                self._unconfigured_logged = True
                LOGGER.error("camera is not configured: %s", err)
            return None

    def _maybe_act(self, reading: Any) -> None:
        now = time.monotonic()
        if now - self.last_action < COOLDOWN_SECONDS:
            return

        self.last_action = now

        # "none" suppresses everything, notifications included: a user who
        # does not want alerts must not get them just because detection works.
        if self.cfg.failure_action != "none":
            self.action("failure", reading.score)

        # "stop" is not wired to a stop command yet; pausing is the safer
        # failure mode for a detection that could still be a false positive.
        if self.cfg.failure_action in ("pause", "stop"):
            # A verified printer rejects unsigned control commands and raises
            # HMS 0500-0500-0001-0007 on its own screen. Sending anyway only
            # litters the printer with errors, so refuse when we can tell.
            ok, message = send_control(self.printer, self.control, "pause")
            if ok:
                if getattr(self.printer, "developer_mode", None) is False:
                    self.controls_sent += 1
            else:
                if getattr(self.printer, "developer_mode", None) is False and self.control is None:
                    self.pause_blocked += 1
                    if not self._blocked_logged:
                        self._blocked_logged = True
                        LOGGER.warning("not sending pause: %s", message)
                else:
                    self.pause_failures += 1
                    LOGGER.warning("pause failed: %s", message)

    async def run_forever(self, stop: "asyncio.Event | None" = None) -> None:
        """Loop ``step()`` until stopped, sleeping the configured interval."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.step()
            except Exception:  # pragma: no cover - belt and braces in the loop
                LOGGER.exception("detection step failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cfg.interval_sec)
            except TimeoutError:
                continue
