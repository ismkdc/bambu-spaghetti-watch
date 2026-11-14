import math

import pytest

from bambu_spaghetti_watch.decision import (
    PrintState,
    Reading,
    classify,
    score_from_detections,
    thresholds,
)


def test_thresholds_scale_with_sensitivity():
    assert thresholds("medium") == pytest.approx((0.38, 0.78))
    assert thresholds("high") == pytest.approx((0.285, 0.585))
    assert thresholds("low") == pytest.approx((0.475, 0.975))


def test_score_is_sum_of_confidences():
    assert score_from_detections([(0.5, (0, 0, 1, 1)), (0.25, (0, 0, 1, 1))]) == 0.75


def test_score_of_no_detections_is_zero():
    assert score_from_detections([]) == 0.0


def test_first_frames_are_warmup_and_safe():
    s = PrintState()
    for _ in range(30):
        r = s.update(1.0)
        assert r.warmup is True
        assert classify(r) == "safe"


def test_ewm_first_frame_matches_alpha():
    s = PrintState()
    s.update(1.0)
    assert math.isclose(s.ewm_mean, 2 / 13, rel_tol=1e-9)


def test_rising_signal_escalates_to_failure():
    s = PrintState()
    for _ in range(300):
        s.update(0.0)
    reading = None
    for _ in range(60):
        reading = s.update(1.0)
    assert reading.warmup is False
    assert classify(reading) == "failure"


def test_shorter_burst_is_only_a_warning():
    s = PrintState()
    for _ in range(60):
        s.update(0.0)
    reading = None
    for _ in range(60):
        reading = s.update(1.0)
    assert classify(reading) == "warning"


def test_flat_signal_stays_safe():
    s = PrintState()
    reading = None
    for _ in range(200):
        reading = s.update(0.4)
    assert classify(reading) == "safe"


def test_reset_print_keeps_baseline():
    s = PrintState()
    for _ in range(100):
        s.update(0.5)
    long_before = s.baseline.mean
    s.reset_print()
    assert s.frame_count == 0
    assert s.baseline.mean == long_before
