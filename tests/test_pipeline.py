import asyncio
from pathlib import Path

import cv2
import numpy as np

from bambu_spaghetti_watch.config import Config
from bambu_spaghetti_watch.pipeline import Pipeline


def _jpeg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()


JPEG = _jpeg()


def fake_cfg(failure_action="pause") -> Config:
    return Config(
        host="192.168.1.50",
        serial="01S00A123456789",
        access_code="12345678",
        interval_sec=5,
        failure_action=failure_action,
        model_dir=Path("/tmp"),
    )


class FakeCamera:
    def __init__(self, count=200):
        self.count = count

    async def grab(self):
        if self.count <= 0:
            return None
        self.count -= 1
        return JPEG


class FakeDetector:
    def __init__(self, scores):
        self.scores = list(scores)

    def infer(self, frame):
        score = self.scores.pop(0) if self.scores else 0.0
        return [(score, (0.0, 0.0, 10.0, 10.0))] if score else []


class FakePrinter:
    def __init__(self, state="RUNNING"):
        self.state = state
        self.paused = 0

    def pause(self):
        self.paused += 1
        return True


def test_idle_printer_skips_inference():
    called = []
    detector = FakeDetector([1.0] * 10)
    pipe = Pipeline(fake_cfg(), FakeCamera(), detector, FakePrinter("IDLE"),
                    action=lambda v, s: called.append(v))
    assert asyncio.run(pipe.step()) == "idle"
    assert called == []
    assert len(detector.scores) == 10


def test_failure_pauses_once_then_cools_down():
    scores = [0.0] * 300 + [1.0] * 20
    printer = FakePrinter()
    actions = []
    pipe = Pipeline(fake_cfg(), FakeCamera(len(scores)), FakeDetector(scores), printer,
                    action=lambda v, s: actions.append(v))
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert printer.paused == 1
    assert actions == ["failure"]


def test_missing_frame_is_reported_and_does_not_raise():
    class DeadCamera:
        async def grab(self):
            return None

    pipe = Pipeline(fake_cfg(), DeadCamera(), FakeDetector([]), FakePrinter(),
                    action=lambda v, s: None)
    assert asyncio.run(pipe.step()) == "no_frame"


def test_undecodable_frame_is_reported_and_does_not_raise():
    class GarbageCamera:
        async def grab(self):
            return b"not-a-jpeg"

    pipe = Pipeline(fake_cfg(), GarbageCamera(), FakeDetector([]), FakePrinter(),
                    action=lambda v, s: None)
    assert asyncio.run(pipe.step()) == "no_frame"


def test_notify_action_does_not_pause_the_printer():
    scores = [0.0] * 300 + [1.0] * 20
    printer = FakePrinter()
    pipe = Pipeline(fake_cfg(failure_action="notify"), FakeCamera(len(scores)),
                    FakeDetector(scores), printer, action=lambda v, s: None)
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert printer.paused == 0


def test_none_action_suppresses_the_action_entirely():
    scores = [0.0] * 300 + [1.0] * 20
    calls = []
    printer = FakePrinter()
    pipe = Pipeline(fake_cfg(failure_action="none"), FakeCamera(len(scores)),
                    FakeDetector(scores), printer, action=lambda v, s: calls.append(v))
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert calls == []
    assert printer.paused == 0


def test_notify_action_fires_once_not_per_failure_frame():
    scores = [0.0] * 300 + [1.0] * 20
    calls = []
    pipe = Pipeline(fake_cfg(failure_action="notify"), FakeCamera(len(scores)),
                    FakeDetector(scores), FakePrinter(), action=lambda v, s: calls.append(v))
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert calls == ["failure"]


def test_last_frame_is_kept_for_the_ui():
    pipe = Pipeline(fake_cfg(), FakeCamera(1), FakeDetector([0.0]), FakePrinter(),
                    action=lambda v, s: None)
    asyncio.run(pipe.step())
    assert pipe.last_frame == JPEG


def test_failed_pause_is_counted():
    class FailingPrinter(FakePrinter):
        def pause(self):
            super().pause()
            return False

    scores = [0.0] * 300 + [1.0] * 20
    pipe = Pipeline(fake_cfg(), FakeCamera(len(scores)), FakeDetector(scores),
                    FailingPrinter(), action=lambda v, s: None)
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert pipe.pause_failures == 1


def test_pause_is_never_sent_when_developer_mode_is_off():
    class SecuredPrinter(FakePrinter):
        developer_mode = False

    scores = [0.0] * 300 + [1.0] * 20
    printer = SecuredPrinter()
    pipe = Pipeline(fake_cfg(), FakeCamera(len(scores)), FakeDetector(scores),
                    printer, action=lambda v, s: None)
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert printer.paused == 0
    assert pipe.pause_blocked == 1


def test_secured_printer_uses_the_signed_control_path():
    calls = []

    class SecuredPrinter(FakePrinter):
        developer_mode = False
        job_name = "Part A"

        def pause(self):
            raise AssertionError("unsigned pause must not be published")

    def control(action, job):
        calls.append((action, job))
        return True, "Pause: confirmed"

    scores = [0.0] * 300 + [1.0] * 20
    pipe = Pipeline(fake_cfg(), FakeCamera(len(scores)), FakeDetector(scores),
                    SecuredPrinter(), action=lambda v, s: None, control=control)
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert calls == [("pause", "Part A")]
    assert pipe.controls_sent == 1
    assert pipe.pause_failures == 0


def test_failed_signed_control_is_counted():
    def control(action, job):
        return False, "rejected"

    class SecuredPrinter(FakePrinter):
        developer_mode = False
        job_name = "Part A"

    scores = [0.0] * 300 + [1.0] * 20
    pipe = Pipeline(fake_cfg(), FakeCamera(len(scores)), FakeDetector(scores),
                    SecuredPrinter(), action=lambda v, s: None, control=control)
    for _ in range(len(scores)):
        asyncio.run(pipe.step())
    assert pipe.pause_failures == 1
    assert pipe.controls_sent == 0


def test_send_control_uses_signed_path_when_the_printer_verifies():
    from bambu_spaghetti_watch.pipeline import send_control

    calls = []

    class SecuredPrinter(FakePrinter):
        developer_mode = False
        job_name = "Part A"

        def pause(self):
            raise AssertionError("unsigned pause must not be published")

    ok, _ = send_control(SecuredPrinter(), lambda a, j: (calls.append((a, j)) or (True, "ok")), "pause")
    assert ok is True
    assert calls == [("pause", "Part A")]


def test_send_control_falls_back_to_mqtt_when_developer_mode_is_on():
    from bambu_spaghetti_watch.pipeline import send_control

    printer = FakePrinter()          # developer_mode is None -> unknown, try MQTT
    ok, _ = send_control(printer, None, "pause")
    assert ok is True
    assert printer.paused == 1


def test_send_control_never_publishes_unsigned_to_a_verifying_printer():
    from bambu_spaghetti_watch.pipeline import send_control

    class SecuredPrinter(FakePrinter):
        developer_mode = False
        job_name = "Part A"

        def pause(self):
            raise AssertionError("must not publish")

    ok, message = send_control(SecuredPrinter(), None, "pause")
    assert ok is False
    assert "Developer Mode" in message


def test_an_unconfigured_camera_degrades_to_no_frame_instead_of_raising():
    # base.py lets a transport raise CameraNotConfigured; step() documents
    # that it does not raise for camera trouble. Both must be true at once.
    from bambu_spaghetti_watch.camera import CameraNotConfigured

    class Unconfigured:
        async def grab(self):
            raise CameraNotConfigured("cloud camera is not configured")

    class RunningPrinter:
        state = "RUNNING"
        developer_mode = None
        job_name = None

    pipeline = Pipeline(fake_cfg(), Unconfigured(), FakeDetector([0.0]), RunningPrinter())
    assert asyncio.run(pipeline.step()) == "no_frame"


def test_a_persistently_unconfigured_camera_logs_once(caplog):
    import logging

    from bambu_spaghetti_watch.camera import CameraNotConfigured

    class Unconfigured:
        async def grab(self):
            raise CameraNotConfigured("cloud camera is not configured")

    class RunningPrinter:
        state = "RUNNING"
        developer_mode = None
        job_name = None

    pipeline = Pipeline(fake_cfg(), Unconfigured(), FakeDetector([0.0]), RunningPrinter())

    with caplog.at_level(logging.ERROR, logger="bambu_spaghetti_watch.pipeline"):
        for _ in range(3):
            assert asyncio.run(pipeline.step()) == "no_frame"

    assert len(caplog.records) == 1
    assert "not configured" in caplog.records[0].getMessage()
