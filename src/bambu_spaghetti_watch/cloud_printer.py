"""Printer status and control through Bambu's cloud session.

The networking plugin is Bambu's own client. It signs in with the user's Studio
session, receives the printer's ``push_status`` reports and signs control
commands. No printer LAN address and no local-network permission are involved,
so this works remotely and survives macOS's Local Network privacy gate.

``start()`` returns immediately and connects in a background thread: the menu
bar can appear before the network or the cloud is ready, and the loop simply
reports "unknown" until the first report arrives.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from bambu_spaghetti_watch import plugin

LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30.0
POLL_SECONDS = 0.5
# The cloud's incremental push_status omits gcode_state; progress fields mean
# the printer is working. After this long without one, treat it as idle again.
STALE_SECONDS = 180.0


class CloudPrinter:
    """Same interface as the old LAN ``PrinterClient``, over the cloud."""

    signed_control = True

    def __init__(self, serial: str, access_code: str = "", model: str = "auto") -> None:
        self.serial = serial
        self.access_code = access_code
        self.model = model
        self.last_raw: dict[str, Any] = {}
        self.last_error: str | None = None

        self._state: str | None = None
        self._machine: str | None = None
        self._bridge: plugin.Bridge | None = None
        self._agent = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._sequence = 0
        self._lock = threading.Lock()
        self._seen_report = False
        self._last_progress = 0.0

    @property
    def state(self) -> str | None:
        if self._state == "RUNNING" and self._last_progress:
            if time.monotonic() - self._last_progress > STALE_SECONDS:
                return "IDLE"
        return self._state

    @property
    def machine(self) -> str | None:
        return self._machine

    @property
    def job_name(self) -> str | None:
        name = (self.last_raw.get("print") or {}).get("subtask_name")
        return name if isinstance(name, str) and name else None

    @property
    def developer_mode(self) -> bool | None:
        # The cloud path signs every command, so the printer's verification
        # state does not change which route is used.
        return None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="cloud-printer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            bridge = plugin.Bridge()
            self._bridge = bridge
            LOGGER.info("plugin %s", bridge.version())
            agent = bridge.create_agent()
            if not agent:
                self.last_error = f"create_agent failed: {bridge.err()}"
                return
            self._agent = agent
            LOGGER.info("callbacks=%s", bridge.install_callbacks(agent))
            bridge.setup_cloud(agent)
            from bambu_spaghetti_watch.bootstrap import read_studio_slicer_uuid

            bridge.set_headers(agent, read_studio_slicer_uuid())
            bridge.init_log(agent)
            LOGGER.info("start=%s", bridge.start(agent))
            LOGGER.info("connect_server=%s", bridge.connect_server(agent))

            deadline = time.monotonic() + CONNECT_TIMEOUT
            while self._running and time.monotonic() < deadline:
                if bridge.server_connected(agent) and bridge.is_user_login(agent):
                    break
                time.sleep(POLL_SECONDS)

            LOGGER.info("connected: server=%s login=%s",
                        bridge.server_connected(agent), bridge.is_user_login(agent))
            self._read_device_table(bridge, agent)
            LOGGER.info("select=%s", bridge.select_machine(agent, self.serial))
            LOGGER.info("cert=%s", bridge.install_device_cert(agent, self.serial))
            # Incremental push_status omits unchanged fields; ask for a full one.
            bridge.send(agent, self.serial,
                        '{"pushing":{"sequence_id":"0","command":"pushall","version":1,"push_target":1}}',
                        1, 0)
            LOGGER.info("polling")
            self._poll(bridge)
        except Exception as err:  # keep the app alive; the UI shows the error
            LOGGER.exception("cloud printer failed")
            self.last_error = str(err)

    def _read_device_table(self, bridge: plugin.Bridge, agent) -> None:
        body = bridge.user_print_info(agent)
        if not body or ":" not in body:
            return
        _code, payload = body.split(":", 1)
        try:
            data = json.loads(payload)
        except ValueError:
            return
        for device in data.get("devices", []):
            if device.get("dev_id") == self.serial:
                self._machine = device.get("dev_model_name") or device.get("dev_product_name")
                break

    def _poll(self, bridge: plugin.Bridge) -> None:
        while self._running:
            try:
                chunk = bridge.buffer()
                if chunk:
                    if not self._seen_report:
                        LOGGER.info("first report: %s", chunk[:300])
                        self._seen_report = True
                    bridge.clear_buffer()
                    for line in chunk.decode("utf-8", "replace").splitlines():
                        self._consume(line)
            except Exception as err:
                self.last_error = str(err)
            time.sleep(POLL_SECONDS)

    def _consume(self, line: str) -> None:
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        block = data.get("print")
        if not isinstance(block, dict):
            return
        self.last_raw = data
        state = block.get("gcode_state")
        if isinstance(state, str):
            self._state = state
            self._last_progress = time.monotonic()
        elif any(key in block for key in ("mc_percent", "percent", "mc_remaining_time", "layer_num")):
            # Incremental reports omit gcode_state; progress means "working".
            self._state = "RUNNING"
            self._last_progress = time.monotonic()

    def _send(self, command: str) -> bool:
        with self._lock:
            if self._agent is None or self._bridge is None:
                return False
            self._sequence += 1
            payload = json.dumps(
                {"print": {"command": command, "sequence_id": str(self._sequence)}}
            )
            return self._bridge.send(self._agent, self.serial, payload) == 0

    def pause(self) -> bool:
        return self._send("pause")

    def resume(self) -> bool:
        return self._send("resume")

    def stop_print(self) -> bool:
        return self._send("stop")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._agent is not None and self._bridge is not None:
            try:
                self._bridge.destroy(self._agent)
            except Exception:
                pass
            self._agent = None
