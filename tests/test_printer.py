from bambu_spaghetti_watch.printer import parse_report, pause_command, topics


def test_topics():
    report, request = topics("01S00A123456789")
    assert report == "device/01S00A123456789/report"
    assert request == "device/01S00A123456789/request"


def test_pause_payload_matches_bambu_wire_format():
    assert pause_command() == {"print": {"command": "pause", "sequence_id": "0"}}


def test_parse_report_reads_state_and_model():
    payload = b'{"print": {"gcode_state": "RUNNING", "machine": "P1S"}}'
    assert parse_report(payload) == {"gcode_state": "RUNNING", "machine": "P1S"}


def test_parse_report_tolerates_garbage():
    assert parse_report(b"\x00\x01\x02") == {"gcode_state": None, "machine": None}


def test_parse_report_tolerates_encrypted_firmware_payloads():
    # Newer firmware can send non-JSON; the app must report "unknown", not crash.
    assert parse_report(b'{"enc_msg": "AAAA"}') == {
        "gcode_state": None,
        "machine": None,
    }


class _Msg:
    def __init__(self, payload):
        self.payload = payload


def test_client_keeps_the_last_raw_report():
    from bambu_spaghetti_watch.printer import PrinterClient

    client = PrinterClient("192.168.1.50", "01S00A123456789", "12345678")
    client._on_message(None, None, _Msg(b'{"print": {"ipcam": {"rtsp_url": "disable"}}}'))
    assert client.last_raw["print"]["ipcam"]["rtsp_url"] == "disable"


def test_developer_mode_bit_is_read_from_the_fun_field():
    from bambu_spaghetti_watch.printer import PrinterClient

    client = PrinterClient("h", "s", "c")
    client._on_message(None, None, _Msg(
        b'{"print": {"fun": "60039FD1B3FF9CB7"}}'))          # bit 29 set
    assert client.developer_mode is False

    client._on_message(None, None, _Msg(
        b'{"print": {"fun": "60039FD193FF9CB7"}}'))          # bit 29 clear
    assert client.developer_mode is True


def test_developer_mode_is_unknown_before_any_report():
    from bambu_spaghetti_watch.printer import PrinterClient

    assert PrinterClient("h", "s", "c").developer_mode is None


def test_job_name_comes_from_the_subtask_field():
    from bambu_spaghetti_watch.printer import PrinterClient

    client = PrinterClient("h", "s", "c")
    client._on_message(None, None, _Msg(
        b'{"print": {"gcode_state": "RUNNING", "subtask_name": "Part A"}}'))
    assert client.job_name == "Part A"
