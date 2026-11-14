#!/usr/bin/env python3
"""Score labelled print-failure fixtures with the shipped Obico model.

Fixtures: tests/fixtures/print_failure/{spaghetti,healthy}
Source: Alex-Ultra/3d-cli-print-failure-frames (CC-BY-4.0), see ATTRIBUTION.md.
"""
from pathlib import Path

import cv2

from bambu_spaghetti_watch.cli import model_paths
from bambu_spaghetti_watch.config import Config
from bambu_spaghetti_watch.detector import Detector


def main() -> int:
    cfg_path, weights_path = model_paths(Config())
    detector = Detector.load(cfg_path, weights_path)
    base = Path(__file__).resolve().parent.parent / "tests/fixtures/print_failure"
    try:
        for label in ("spaghetti", "healthy"):
            folder = base / label
            for path in sorted(folder.glob("*.jpg")):
                image = cv2.imread(str(path))
                detections = detector.infer(image) if image is not None else []
                scores = [score for score, _box in detections]
                best = max(scores, default=0.0)
                print(f"{label:9} {path.name[:16]}  n={len(detections):2}  max={best:.3f}")
    finally:
        detector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
