"""MQTT link to the printer: status in, commands out.

Wire facts (verified against bambuddy's ``services/bambu_mqtt.py`` and against
Bambu Studio's networking plugin strings):

- Printer broker: ``ssl://<ip>:8883``, username ``bblp``, password = access code
- topics: ``device/<serial>/report`` (subscribe), ``device/<serial>/request``
- pause is ``{"print": {"command": "pause", "sequence_id": "0"}}``

Some firmware ships encrypted report payloads (``enc_msg``). We treat anything
unparseable as "state unknown" rather than inventing a state.
"""

from __future__ import annotations

import json
import secrets
import ssl
from typing import Any

import paho.mqtt.client as mqtt

MQTT_PORT = 8883
USERNAME = "bblp"

UNKNOWN = {"gcode_state": None, "machine": None}


def topics(serial: str) -> tuple[str, str]:
    """Return ``(report_topic, request_topic)`` for a printer serial."""
    return f"device/{serial}/report", f"device/{serial}/request"


def pause_command() -> dict[str, Any]:
    """The exact payload Bambu's firmware accepts for a pause."""
    return {"print": {"command": "pause", "sequence_id": "0"}}


def resume_command() -> dict[str, Any]:
    return {"print": {"command": "resume", "sequence_id": "0"}}


def parse_report(payload: bytes | str) -> dict[str, Any]:
    """Pull the two fields the app needs out of one report message."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return dict(UNKNOWN)

    if not isinstance(data, dict):
        return dict(UNKNOWN)

    print_block = data.get("print")
    if not isinstance(print_block, dict):
        return dict(UNKNOWN)

    state = print_block.get("gcode_state")
    machine = print_block.get("machine")
    return {
        "gcode_state": state if isinstance(state, str) else None,
        "machine": machine if isinstance(machine, str) else None,
    }


class PrinterClient:
    """A thin paho wrapper; the loop runs in paho's own thread."""

    def __init__(self, host: str, serial: str, access_code: str) -> None:
        self.host = host
        self.port = MQTT_PORT
        self.report_topic, self.request_topic = topics(serial)
        self._state: str | None = None
        self._machine: str | None = None
        self.last_raw: dict[str, Any] = {}

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bsw-{secrets.token_hex(4)}",
        )
        self._client.username_pw_set(USERNAME, access_code)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    @property
    def state(self) -> str | None:
        """Last reported ``gcode_state``, or ``None`` while unknown."""
        return self._state

    @property
    def machine(self) -> str | None:
        return self._machine

    @property
    def job_name(self) -> str | None:
        """The print's name, as the printer reports it (``subtask_name``)."""
        name = (self.last_raw.get("print") or {}).get("subtask_name")
        return name if isinstance(name, str) and name else None

    @property
    def developer_mode(self) -> bool | None:
        """Whether the printer accepts unsigned commands.

        Firmware since Jan 2025 verifies every control command (pause, stop,
        light, temperatures) and answers ``84033543`` when it cannot. The
        toggle is reported as bit 29 of ``print.fun``: clear means Developer
        Mode is on. ``None`` means no report has arrived yet.
        """
        raw_fun = (self.last_raw.get("print") or {}).get("fun")
        if not isinstance(raw_fun, str):
            return None
        try:
            value = int(raw_fun, 16)
        except ValueError:
            return None
        return ((value >> 29) & 1) == 0

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(self.report_topic, qos=1)

    def _on_message(self, client, userdata, message) -> None:
        try:
            raw = json.loads(message.payload)
            if isinstance(raw, dict):
                self.last_raw = raw
        except (ValueError, TypeError):
            pass

        parsed = parse_report(message.payload)
        if parsed["gcode_state"] is not None:
            self._state = parsed["gcode_state"]
        if parsed["machine"] is not None:
            self._machine = parsed["machine"]

    def start(self) -> None:
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def pause(self) -> bool:
        """Publish a pause. False means the broker did not accept it."""
        info = self._client.publish(
            self.request_topic, json.dumps(pause_command()), qos=1
        )
        return info.rc == 0

    def resume(self) -> bool:
        info = self._client.publish(
            self.request_topic, json.dumps(resume_command()), qos=1
        )
        return info.rc == 0
