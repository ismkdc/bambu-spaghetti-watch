"""Camera adapters for Bambu Lab printers."""

from __future__ import annotations

from typing import Any

from bambu_spaghetti_watch.camera.base import Camera, CameraNotConfigured
from bambu_spaghetti_watch.camera.cloud import CloudCamera

__all__ = [
    "Camera",
    "CameraNotConfigured",
    "CloudCamera",
    "make_camera",
]


def make_camera(cfg: Any, model: str | None = None) -> Camera:
    """The cloud adapter is the only camera transport this build ships.

    ``model`` is accepted for call-site compatibility with discovery; the cloud
    tunnel is model independent.
    """
    return CloudCamera(cfg)
