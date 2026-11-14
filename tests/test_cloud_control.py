from pathlib import Path

from bambu_spaghetti_watch import cloud_control


def test_command_targets_the_vendored_cli():
    seen = []

    def runner(cmd, **kwargs):
        seen.append(cmd)
        class R:
            returncode = 0
            stdout = "Pause: confirmed"
            stderr = ""
        return R()

    ok, message = cloud_control.run(
        "pause", host="192.168.1.50", serial="01S00A123456789",
        job="Part A", runner=runner,
    )
    assert ok is True
    assert "confirmed" in message
    cmd = seen[0]
    assert str(cloud_control.CLI) in cmd
    assert "pause" in cmd
    assert "--host" in cmd and "192.168.1.50" in cmd
    assert "--serial" in cmd and "01S00A123456789" in cmd
    assert "--job" in cmd and "Part A" in cmd
    assert "--yes" in cmd


def test_a_failed_command_reports_the_reason():
    def runner(cmd, **kwargs):
        class R:
            returncode = 3
            stdout = ""
            stderr = "Pause: rejected (printer busy)"
        return R()

    ok, message = cloud_control.run(
        "pause", host="h", serial="s", job="j", runner=runner,
    )
    assert ok is False
    assert "rejected" in message


def test_missing_bridge_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_control, "CLI", tmp_path / "bambu.py")
    ok, message = cloud_control.run("pause", host="h", serial="s", job="j")
    assert ok is False
    assert "bambu-headless" in message


def test_rejects_unknown_actions():
    ok, message = cloud_control.run("explode", host="h", serial="s", job="j")
    assert ok is False
    assert "explode" in message
