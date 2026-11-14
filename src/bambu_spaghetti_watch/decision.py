"""Temporal smoothing for Obico failure scores.

A port of Obico's own decision logic (obico-server ``lib/prediction.py``:
``update_prediction_with_detections`` and ``is_failing``, parameters from
``FD_1ST_GEN_PARAMS``), by way of bambuddy's ``obico_smoothing.py``.
Both are AGPL-3.0; keep this attribution if you redistribute.

The shape of the idea:

- per-frame ``p`` is the sum of detection confidences
- ``ewm_mean`` is an exponentially weighted mean (alpha = 2 / (span + 1))
- ``rolling_short`` is the mean of this print's last 310 frames
- ``rolling_long`` is the printer's mean over its last 7200 frames, kept
  across prints: whatever the camera always sees (plate texture, labels,
  shadows) is baseline, not failure
- the score is ``ewm_mean - rolling_long``

Everything here is pure: no I/O, no clock, no logging.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence

EWM_SPAN = 12
EWM_ALPHA = 2.0 / (EWM_SPAN + 1)
ROLLING_SHORT = 310
ROLLING_LONG = 7200
WARMUP_FRAMES = 30
ROLLING_MEAN_SHORT_MULTIPLE = 3.8
ESCALATING_FACTOR = 1.75

BASE_LOW = 0.38
BASE_HIGH = 0.78

SENSITIVITY_MULT = {
    "low": 1.25,
    "medium": 1.0,
    "high": 0.75,
}


def thresholds(sensitivity: str = "medium") -> tuple[float, float]:
    """Return the (low, high) score thresholds for a sensitivity setting."""
    mult = SENSITIVITY_MULT.get(sensitivity, 1.0)
    return BASE_LOW * mult, BASE_HIGH * mult


def score_from_detections(detections: Sequence[tuple[float, tuple]]) -> float:
    """Sum of detection confidences, which is Obico's per-frame ``p``."""
    return float(sum(d[0] for d in detections))


class _RollingMean:
    """Exact mean of the last ``size`` values."""

    def __init__(self, size: int) -> None:
        self._buf: deque[float] = deque(maxlen=size)
        self._sum = 0.0

    def add(self, value: float) -> None:
        if len(self._buf) == self._buf.maxlen:
            self._sum -= self._buf[0]
        self._buf.append(value)
        self._sum += value

    @property
    def mean(self) -> float:
        return self._sum / len(self._buf) if self._buf else 0.0


class Baseline(_RollingMean):
    """A printer's long-term mean; outlives individual prints."""

    def __init__(self) -> None:
        super().__init__(ROLLING_LONG)


@dataclass
class Reading:
    """One frame's smoothed state, as ``classify`` sees it."""

    score: float = 0.0
    short_rise: float = 0.0
    warmup: bool = True


@dataclass
class PrintState:
    """Per-print smoothing state.

    ``reset_print`` starts a new print but deliberately keeps ``baseline``:
    the printer's idea of "normal" must survive across prints, otherwise
    every print re-learns its own bed texture as a failure.
    """

    baseline: Baseline = field(default_factory=Baseline)
    frame_count: int = 0
    ewm_mean: float = 0.0
    short: _RollingMean = field(default_factory=lambda: _RollingMean(ROLLING_SHORT))
    last: Reading = field(default_factory=Reading)

    def update(self, p: float) -> Reading:
        """Fold one frame's score in, and return the smoothed reading."""
        if not math.isfinite(p):
            p = 0.0

        self.frame_count += 1
        self.ewm_mean = EWM_ALPHA * p + (1.0 - EWM_ALPHA) * self.ewm_mean
        self.short.add(p)
        self.baseline.add(p)

        long_mean = self.baseline.mean
        self.last = Reading(
            score=self.ewm_mean - long_mean,
            short_rise=self.short.mean - long_mean,
            warmup=self.frame_count <= WARMUP_FRAMES,
        )
        return self.last

    def reset_print(self) -> None:
        self.frame_count = 0
        self.ewm_mean = 0.0
        self.short = _RollingMean(ROLLING_SHORT)
        self.last = Reading()


def classify(reading: Reading, sensitivity: str = "medium") -> str:
    """Return ``"safe"``, ``"warning"`` or ``"failure"`` for one reading."""
    if reading.warmup:
        return "safe"

    low, high = thresholds(sensitivity)

    if reading.score >= high:
        return "failure"
    if reading.score >= low and reading.score >= ROLLING_MEAN_SHORT_MULTIPLE * reading.short_rise:
        return "warning"
    return "safe"
