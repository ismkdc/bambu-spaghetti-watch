from pathlib import Path

import pytest

from bambu_spaghetti_watch.camera import CloudCamera, make_camera
from bambu_spaghetti_watch.config import Config


def cfg(**kwargs) -> Config:
    base = dict(host="192.168.1.50", serial="01S00A123456789", access_code="12345678")
    base.update(kwargs)
    return Config(**base)


def test_make_camera_returns_the_cloud_adapter():
    assert isinstance(make_camera(cfg()), CloudCamera)


def test_model_does_not_change_the_adapter():
    # The cloud tunnel is model independent; discovery's model code is only
    # used for MQTT/telemetry.
    for model in ("P1S", "P2S", "A1", "X1C", "N7", None):
        assert isinstance(make_camera(cfg(), model=model), CloudCamera)


def test_cloud_camera_is_configured_in_this_repo():
    # The bridge and libBambuSource exist in a normal checkout.
    camera = CloudCamera(cfg())
    assert camera.configuration_problem is None


def test_cloud_camera_reports_a_missing_bridge(monkeypatch):
    from bambu_spaghetti_watch.camera import cloud

    monkeypatch.setattr(cloud, "_bridge_dir", lambda: None)
    assert "bridge" in (CloudCamera(cfg()).configuration_problem or "")


def test_cloud_camera_reports_a_missing_library(monkeypatch):
    from bambu_spaghetti_watch.camera import bambu_source

    monkeypatch.setattr(bambu_source, "LIB", Path("/nonexistent/libBambuSource.dylib"))
    assert "libBambuSource" in (CloudCamera(cfg()).configuration_problem or "")


def test_model_dir_is_optional():
    assert Path.home().exists()
