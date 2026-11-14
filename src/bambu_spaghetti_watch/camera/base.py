"""Camera adapters: one interface, one implementation per printer family."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class CameraNotConfigured(RuntimeError):
    """A camera transport exists but has no working configuration yet.

    Unlike network trouble, this is not something the detection loop can
    recover from by retrying. A transport that can raise it must expose a
    non-``None`` ``configuration_problem`` so the CLI can refuse the config
    before any connection is attempted.
    """


@runtime_checkable
class Camera(Protocol):
    """A source of single JPEG frames."""

    async def grab(self) -> bytes | None:
        """Return one complete JPEG, or ``None`` if the frame could not be read.

        Adapters never raise for network trouble: a dead camera must degrade
        to "no frame" so the detection loop keeps running.

        A transport that has no working configuration at all is different: it
        raises ``CameraNotConfigured`` rather than pretending to be offline.
        """
