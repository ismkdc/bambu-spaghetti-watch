from bambu_spaghetti_watch.cli import build_parser


def test_probe_flag():
    args = build_parser().parse_args(["--probe"])
    assert args.probe is True
    assert args.config.endswith("config.json")


def test_default_is_run_mode():
    args = build_parser().parse_args([])
    assert args.probe is False and args.once is False


def test_config_path_is_overridable(tmp_path):
    args = build_parser().parse_args(["--config", str(tmp_path / "x.json")])
    assert args.config.endswith("x.json")


def test_settings_flag():
    args = build_parser().parse_args(["--settings"])
    assert args.settings is True
    assert args.probe is False


def test_summarise_settings_picks_the_camera_and_network_blocks():
    from bambu_spaghetti_watch.cli import summarise_settings

    report = {
        "print": {
            "ipcam": {"rtsp_url": "disable", "resolution": "1080p"},
            "xcam": {"spaghetti_detector": True},
            "gcode_state": "RUNNING",
        }
    }
    blocks = summarise_settings(report)
    assert blocks["ipcam"]["rtsp_url"] == "disable"
    assert blocks["xcam"]["spaghetti_detector"] is True
    assert "net" not in blocks


def test_summarise_settings_tolerates_a_report_without_blocks():
    from bambu_spaghetti_watch.cli import summarise_settings

    assert summarise_settings({"print": {"gcode_state": "IDLE"}}) == {}


def test_wait_for_report_polls_until_a_state_arrives():
    import asyncio
    import time

    from bambu_spaghetti_watch.cli import wait_for_report

    class Flipping:
        def __init__(self):
            self.reads = 0

        @property
        def state(self):
            self.reads += 1
            return "RUNNING" if self.reads >= 3 else None

    printer = Flipping()
    started = time.monotonic()
    assert asyncio.run(wait_for_report(printer, timeout=5.0, poll=0.01)) is True
    assert printer.reads >= 3
    assert time.monotonic() - started < 2.0


def test_wait_for_report_gives_up():
    import asyncio

    from bambu_spaghetti_watch.cli import wait_for_report

    class Dead:
        state = None

    assert asyncio.run(wait_for_report(Dead(), timeout=0.05, poll=0.01)) is False


def test_a_missing_cloud_bridge_fails_fast_before_discovery(tmp_path, capsys, monkeypatch):
    import json

    from bambu_spaghetti_watch import cli
    from bambu_spaghetti_watch.camera import cloud

    monkeypatch.setattr(cloud, "_bridge_dir", lambda: None)
    # No host/serial/access_code: a preflight that ran after discovery would
    # block on SSDP here, which is exactly the bug this test guards against.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera_mode": "cloud"}))

    assert cli.main(["--config", str(path)]) == 2

    out = capsys.readouterr().out
    assert "camera error" in out
    assert "bridge" in out
    assert "Traceback" not in out


def test_the_cli_asks_the_camera_whether_it_is_configured(monkeypatch):
    from bambu_spaghetti_watch import cli
    from bambu_spaghetti_watch.config import Config

    class Configured:
        configuration_problem = None

    monkeypatch.setattr(cli, "make_camera", lambda *args, **kwargs: Configured())
    assert cli.cloud_camera_problem(Config(camera_mode="cloud")) is None


def test_probe_is_blocked_by_a_missing_bridge_connection_too(tmp_path, capsys, monkeypatch):
    import json

    from bambu_spaghetti_watch import cli
    from bambu_spaghetti_watch.camera import cloud

    monkeypatch.setattr(cloud, "_bridge_dir", lambda: None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera_mode": "cloud"}))

    assert cli.main(["--config", str(path), "--probe"]) == 2
    assert "camera error" in capsys.readouterr().out


def test_settings_still_reaches_the_printer_in_cloud_mode(tmp_path, capsys, monkeypatch):
    import json

    from bambu_spaghetti_watch import cli

    seen = {}

    async def fake_settings(cfg):
        seen["serial"] = cfg.serial
        return 0

    monkeypatch.setattr(cli, "_with_cloud_config", lambda cfg: cfg)
    monkeypatch.setattr(cli, "settings", fake_settings)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"serial": "01S00A123456789", "camera_mode": "cloud"}))

    assert cli.main(["--config", str(path), "--settings"]) == 0
    assert "camera error" not in capsys.readouterr().out
    assert seen["serial"] == "01S00A123456789"


def test_probe_reports_an_unconfigured_camera_instead_of_raising(monkeypatch, capsys):
    import asyncio

    from bambu_spaghetti_watch import cli
    from bambu_spaghetti_watch.camera import CloudCamera
    from bambu_spaghetti_watch.config import Config

    class FakePrinter:
        state = "IDLE"
        machine = "P2S"
        port = 8883

        def start(self):
            pass

        def stop(self):
            pass

    from bambu_spaghetti_watch.camera import cloud

    monkeypatch.setattr(cloud, "_bridge_dir", lambda: None)
    monkeypatch.setattr(cli, "CloudPrinter", lambda *args, **kwargs: FakePrinter())
    monkeypatch.setattr(cli, "make_camera", lambda *args, **kwargs: CloudCamera())

    cfg = Config(host="h", serial="s", access_code="a")
    assert asyncio.run(cli.probe(cfg)) == 2
    assert "camera    :" in capsys.readouterr().out
