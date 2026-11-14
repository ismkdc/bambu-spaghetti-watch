import json

from bambu_spaghetti_watch.bootstrap import (
    PrinterInfo,
    choose_device,
    parse_ssdp_response,
    read_studio_access_codes,
)

SSDP = (
    "HTTP/1.1 200 OK\r\n"
    "Location: 192.168.1.50\r\n"
    "ST: urn:bambulab-com:device:3dprinter:1\r\n"
    "USN: 01S00A123456789\r\n"
    "DevModel.bambu.com: N7\r\n"
    "DevName.bambu.com: KUNDAKCI\r\n"
    "DevVersion.bambu.com: 01.03.00.00\r\n"
    "DevConnect.bambu.com: cloud\r\n"
    "\r\n"
)


def test_parse_ssdp_response():
    d = parse_ssdp_response(SSDP)
    assert d.host == "192.168.1.50"
    assert d.serial == "01S00A123456789"
    assert d.model == "N7"
    assert d.name == "KUNDAKCI"
    assert d.version == "01.03.00.00"


def test_parse_ssdp_garbage_is_none():
    assert parse_ssdp_response("hello") is None
    assert parse_ssdp_response("HTTP/1.1 200 OK\r\n\r\n") is None


def test_read_studio_codes(tmp_path):
    p = tmp_path / "BambuStudio.conf"
    p.write_text(json.dumps({"access_code": {"S1": "abc12345"}}))
    assert read_studio_access_codes(p) == {"S1": "abc12345"}


def test_read_studio_codes_missing_file(tmp_path):
    assert read_studio_access_codes(tmp_path / "nope.conf") == {}


def test_read_studio_codes_bad_json(tmp_path):
    p = tmp_path / "x.conf"
    p.write_text("{not json")
    assert read_studio_access_codes(p) == {}


def test_read_studio_codes_ignores_other_keys(tmp_path):
    p = tmp_path / "BambuStudio.conf"
    p.write_text(json.dumps({"access_code": {"S1": "abc"}, "app": {"x": 1}}))
    assert read_studio_access_codes(p) == {"S1": "abc"}


def test_choose_device_when_there_is_only_one():
    d = parse_ssdp_response(SSDP)
    assert choose_device([d]) is d


def test_choose_device_by_serial():
    d = parse_ssdp_response(SSDP)
    other = PrinterInfo(host="1.2.3.4", serial="OTHER", model="P1S", name="x")
    assert choose_device([other, d], serial="01S00A123456789") is d


def test_choose_device_is_none_when_ambiguous():
    a = PrinterInfo(host="1.1.1.1", serial="A", model="P1S", name="a")
    b = PrinterInfo(host="2.2.2.2", serial="B", model="P1S", name="b")
    assert choose_device([a, b]) is None


def test_choose_device_by_serial_with_no_match_is_none():
    a = PrinterInfo(host="1.1.1.1", serial="A", model="P1S", name="a")
    assert choose_device([a], serial="Z") is None
