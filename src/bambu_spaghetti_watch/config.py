"""Configuration for bambu-spaghetti-watch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "bambu-spaghetti-watch" / "config.json"

FAILURE_ACTIONS = ("notify", "pause", "stop", "none")
CAMERA_MODES = ("cloud",)
SENSITIVITIES = ("low", "medium", "high")


@dataclass(frozen=True)
class Config:
    """Everything the app needs to reach one printer.

    The credentials may all be empty: ``bootstrap.resolve`` fills them from
    SSDP and Bambu Studio's own config. A config file only has to exist for
    overrides.
    """

    host: str = ""
    serial: str = ""
    access_code: str = ""
    interval_sec: int = 5
    model: str = "auto"
    camera_mode: str = "cloud"
    failure_action: str = "notify"
    sensitivity: str = "medium"
    model_dir: Path | None = None


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def load_config(path: Path) -> Config:
    """Read a JSON config file and validate it.

    Raises ValueError naming the offending field; the CLI turns that into a
    readable message instead of a traceback.
    """
    data = json.loads(Path(path).read_text())

    host = _text(data, "host")
    serial = _text(data, "serial")
    access_code = _text(data, "access_code")

    failure_action = data.get("failure_action", "notify")
    if failure_action not in FAILURE_ACTIONS:
        raise ValueError(
            f"failure_action must be one of {FAILURE_ACTIONS}, got {failure_action!r}"
        )

    camera_mode = data.get("camera_mode", "cloud")
    if camera_mode not in CAMERA_MODES:
        raise ValueError(
            f"camera_mode must be one of {CAMERA_MODES}, got {camera_mode!r}"
        )

    sensitivity = data.get("sensitivity", "medium")
    if sensitivity not in SENSITIVITIES:
        raise ValueError(
            f"sensitivity must be one of {SENSITIVITIES}, got {sensitivity!r}"
        )

    interval_sec = int(data.get("interval_sec", 5))
    if interval_sec < 1:
        raise ValueError(f"interval_sec must be >= 1, got {interval_sec}")

    model_dir = data.get("model_dir")
    if model_dir is not None:
        model_dir = Path(model_dir).expanduser()

    return Config(
        host=host,
        serial=serial,
        access_code=access_code,
        interval_sec=interval_sec,
        model=str(data.get("model", "auto")),
        camera_mode=camera_mode,
        failure_action=failure_action,
        sensitivity=sensitivity,
        model_dir=model_dir,
    )
