import cv2
import numpy as np

from bambu_spaghetti_watch.detector import Detector, decode_rows, patch_cfg


class FakeNet:
    """Stands in for cv2.dnn_Net so frame-size handling is testable without
    the 193 MB weights."""

    def __init__(self, rows):
        self._rows = rows
        self.blob_shape = None

    def setInput(self, blob):
        self.blob_shape = blob.shape

    def forward(self, names):
        return [self._rows]


def test_opencv_still_has_the_darknet_importer():
    # Protects the opencv < 5 pin: OpenCV 5.0 removed readNetFromDarknet, and
    # the model ships as darknet weights.
    assert hasattr(cv2.dnn, "readNetFromDarknet")


def test_patch_replaces_reorg3d():
    out = patch_cfg("[reorg3d]\nstride=2\n")
    assert "[reorg3d]" not in out
    assert "[reorg]\nstride=2" in out


def test_patch_lowers_region_threshold():
    out = patch_cfg("[region]\nthresh = .6\n")
    assert "thresh = 0.01" in out


def test_decode_uses_objectness_as_score():
    rows = np.array([[0.5, 0.5, 0.2, 0.2, 0.9, 0.9],
                     [0.1, 0.1, 0.2, 0.2, 0.05, 0.05]])
    dets = decode_rows(rows, thresh=0.08, nms=0.45)
    assert len(dets) == 1
    assert dets[0][0] == 0.9


def test_decode_suppresses_overlapping_boxes():
    rows = np.array([[0.5, 0.5, 0.2, 0.2, 0.90, 0.90],
                     [0.51, 0.51, 0.2, 0.2, 0.80, 0.80],
                     [0.2, 0.2, 0.1, 0.1, 0.70, 0.70]])
    assert len(decode_rows(rows, thresh=0.08, nms=0.45)) == 2


def test_decode_of_empty_rows_is_empty():
    assert decode_rows(np.zeros((0, 6)), thresh=0.08, nms=0.45) == []


def test_infer_resizes_any_frame_to_the_network_input():
    rows = np.array([[0.5, 0.5, 0.2, 0.2, 0.9, 0.9]])
    net = FakeNet(rows)
    detector = Detector(net, ["out"])

    for shape in [(720, 1280, 3), (1080, 1920, 3)]:
        dets = detector.infer(np.zeros(shape, np.uint8))
        assert net.blob_shape == (1, 3, 416, 416)
        assert len(dets) == 1
