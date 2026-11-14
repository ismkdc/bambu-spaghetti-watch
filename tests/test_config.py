import json

import pytest

from bambu_spaghetti_watch.config import Config, load_config


def test_loads_minimal_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"host": "192.168.1.50", "serial": "01S00A123456789",
                             "access_code": "12345678"}))
    cfg = load_config(p)
    assert cfg.host == "192.168.1.50"
    assert cfg.interval_sec == 5
    assert cfg.failure_action == "stop"
    assert cfg.camera_mode == "cloud"
    assert cfg.sensitivity == "medium"


def test_accepts_cloud_camera_mode(tmp_path):
    # An explicit "cloud" mode is a documented, selectable state: it exists so
    # the app can say why there is no remote camera instead of guessing.
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"camera_mode": "cloud"}))
    assert load_config(p).camera_mode == "cloud"


def test_rejects_unknown_camera_mode(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"camera_mode": "satellite"}))
    with pytest.raises(ValueError, match="camera_mode"):
        load_config(p)


def test_rejects_unknown_failure_action(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"host": "h", "serial": "s", "access_code": "a",
                             "failure_action": "explode"}))
    with pytest.raises(ValueError, match="failure_action"):
        load_config(p)


def test_credentials_may_be_missing_until_resolution(tmp_path):
    # host/serial/access_code can come from SSDP plus Bambu Studio's config,
    # so a config file that omits them is valid input, not an error.
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"host": "h"}))
    cfg = load_config(p)
    assert cfg.serial == ""
    assert cfg.access_code == ""
