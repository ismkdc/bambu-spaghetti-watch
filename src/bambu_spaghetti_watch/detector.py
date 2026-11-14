"""Obico failure detector, running the darknet weights through cv2.dnn.

Why cv2.dnn and not darknet: AlexeyAB's darknet has no NEON path, and measures
~1.2 s/frame on Apple Silicon. The same network through OpenCV measures ~27 ms.

The shipped ``model.cfg`` cannot be fed to OpenCV as-is:

- OpenCV has no ``[reorg3d]`` parser (darknet's variant of ``[reorg]``)
- the region layer's ``thresh = .6`` zeroes class scores before the region has
  a chance to score low-confidence boxes, and Obico deliberately runs at 0.08

Both are patched in memory, on a temporary copy; the file on disk is untouched.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

THRESH = 0.08
NMS = 0.45
INPUT_SIZE = 416

_THRESH_RE = re.compile(r"thresh\s*=\s*\.6\b")


def patch_cfg(text: str) -> str:
    """Return ``model.cfg`` with the two changes OpenCV needs."""
    text = text.replace("[reorg3d]", "[reorg]")
    return _THRESH_RE.sub("thresh = 0.01", text)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / union) if union > 0 else 0.0


def decode_rows(
    rows: np.ndarray, thresh: float = THRESH, nms: float = NMS
) -> list[tuple[float, tuple[float, float, float, float]]]:
    """Turn region-layer rows into ``(score, (xc, yc, w, h))`` detections.

    The model has a single class, so the objectness column *is* the score;
    OpenCV's region layer reports the class column as objectness squared,
    which would silently halve every score.
    """
    if rows.size == 0:
        return []

    scores = rows[:, 4]
    rows = rows[scores > thresh]
    scores = scores[scores > thresh]
    if len(rows) == 0:
        return []

    order = np.argsort(scores)[::-1]
    boxes = rows[:, :4]
    x1 = boxes[:, 0] - boxes[:, 2] / 2
    y1 = boxes[:, 1] - boxes[:, 3] / 2
    x2 = boxes[:, 0] + boxes[:, 2] / 2
    y2 = boxes[:, 1] + boxes[:, 3] / 2
    corners = np.stack([x1, y1, x2, y2], axis=1)

    keep: list[int] = []
    for index in order:
        if all(_iou(corners[index], corners[k]) <= nms for k in keep):
            keep.append(index)

    return [(float(scores[i]), tuple(float(v) for v in boxes[i])) for i in keep]


class Detector:
    """Loaded network plus the post-processing Obico applies."""

    def __init__(self, net: Any, out_names: Sequence[str]) -> None:
        self._net = net
        self._out_names = list(out_names)
        self._tmpdir: str | None = None

    @classmethod
    def load(cls, cfg_path: Path, weights_path: Path) -> "Detector":
        cfg_text = patch_cfg(Path(cfg_path).read_text())
        tmpdir = tempfile.mkdtemp(prefix="bsw-cfg-")
        patched = Path(tmpdir) / "model.cfg"
        patched.write_text(cfg_text)

        try:
            net = cv2.dnn.readNetFromDarknet(str(patched), str(weights_path))
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise

        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        detector = cls(net, net.getUnconnectedOutLayersNames())
        detector._tmpdir = tmpdir
        return detector

    def infer(self, frame: np.ndarray) -> list[tuple[float, tuple[float, float, float, float]]]:
        blob = cv2.dnn.blobFromImage(
            frame, 1 / 255.0, (INPUT_SIZE, INPUT_SIZE), swapRB=True, crop=False
        )
        self._net.setInput(blob)
        outputs = self._net.forward(self._out_names)
        rows = np.concatenate(
            [out.reshape(-1, out.shape[-1]) for out in outputs], axis=0
        )
        return decode_rows(rows)

    def close(self) -> None:
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
