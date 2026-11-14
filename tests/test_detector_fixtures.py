"""Check that the shipped model separates real spaghetti from healthy prints.

Fixtures are CC-BY-4.0; see ``fixtures/print_failure/ATTRIBUTION.md``. The
check is deliberately coarse: one frame per direction, no smoothing, no
threshold tuning. It proves the model sees the failure at all, not that the
decision layer would pause on a single frame.
"""
from pathlib import Path

import cv2
import pytest

from bambu_spaghetti_watch.cli import model_paths
from bambu_spaghetti_watch.config import Config
from bambu_spaghetti_watch.detector import Detector

FIXTURES = Path(__file__).parent / "fixtures/print_failure"


@pytest.fixture(scope="module")
def detector():
    cfg_path, weights_path = model_paths(Config())
    if not (cfg_path.exists() and weights_path.exists()):
        pytest.skip("model weights are not installed")
    instance = Detector.load(cfg_path, weights_path)
    try:
        yield instance
    finally:
        instance.close()


def _best(detector: Detector, path: Path) -> float:
    image = cv2.imread(str(path))
    assert image is not None, f"cannot read {path}"
    return max((score for score, _box in detector.infer(image)), default=0.0)


def test_spaghetti_frames_score_higher_than_healthy_ones(detector):
    spaghetti = [_best(detector, p) for p in sorted((FIXTURES / "spaghetti").glob("*.jpg"))]
    healthy = [_best(detector, p) for p in sorted((FIXTURES / "healthy").glob("*.jpg"))]

    assert spaghetti and healthy
    assert min(spaghetti) > 0.15, spaghetti
    assert max(healthy) < 0.05, healthy
    assert min(spaghetti) > max(healthy) + 0.10
