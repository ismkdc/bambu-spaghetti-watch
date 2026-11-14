"""Zero-configuration bootstrap: find the printer, and find its access code.

Two sources, matched on the serial number:

- **SSDP** on UDP 2021 gives the printer's local address, model code and name.
  Bambu printers answer a plain UPnP M-SEARCH; no credentials are needed.
- **Bambu Studio's** ``BambuStudio.conf`` holds ``{"access_code": {serial: code}}``
  in clear text once the user has added the printer there.

So the user config file only has to exist for overrides.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

STUDIO_CONF = (
    Path.home() / "Library" / "Application Support" / "BambuStudio" / "BambuStudio.conf"
)

# Last printer that answered, so a missed SSDP round does not stop the app.
CACHE = Path.home() / "Library" / "Caches" / "bambu-watch" / "last_printer.json"

SSDP_ADDR = ("239.255.255.250", 2021)
SSDP_TARGETS = ("239.255.255.250", "255.255.255.255")
SSDP_ST = "urn:bambulab-com:device:3dprinter:1"

_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    f"ST: {SSDP_ST}\r\n\r\n"
).encode()


@dataclass(frozen=True)
class PrinterInfo:
    host: str
    serial: str
    model: str = ""
    name: str = ""
    version: str = ""


@dataclass(frozen=True)
class Resolved:
    host: str
    serial: str
    access_code: str
    model: str = ""


def _host_from_location(value: str) -> str:
    """SSDP's Location is usually a bare IP, sometimes a URL."""
    if value.startswith(("http://", "https://")):
        return urlparse(value).hostname or value
    return value.split("/")[0]


def parse_ssdp_response(text: str) -> PrinterInfo | None:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip().lower()] = value.strip()

    location = fields.get("location", "")
    serial = fields.get("usn", "")
    if not location or not serial:
        return None

    return PrinterInfo(
        host=_host_from_location(location),
        serial=serial,
        model=fields.get("devmodel.bambu.com", ""),
        name=fields.get("devname.bambu.com", ""),
        version=fields.get("devversion.bambu.com", ""),
    )


def read_studio_access_codes(path: Path = STUDIO_CONF) -> dict[str, str]:
    """``{serial: access_code}`` from Bambu Studio's config, or ``{}``."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}

    codes = data.get("access_code")
    if not isinstance(codes, dict):
        return {}

    return {str(k): str(v) for k, v in codes.items() if isinstance(v, str) and v}


def read_studio_serials(path: Path = STUDIO_CONF) -> list[str]:
    """Every printer serial Studio has saved, in either access-code map."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for key in ("access_code", "user_access_code"):
        block = data.get(key)
        if isinstance(block, dict):
            out.extend(str(k) for k, v in block.items() if isinstance(v, str) and v)
    return list(dict.fromkeys(out))


def read_studio_slicer_uuid(path: Path = STUDIO_CONF) -> str:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return ""
    app = data.get("app")
    return str(app.get("slicer_uuid", "")) if isinstance(app, dict) else ""


def _cache_read() -> PrinterInfo | None:
    try:
        data = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return None
    host = str(data.get("host", "")).strip()
    serial = str(data.get("serial", "")).strip()
    if not host or not serial:
        return None
    return PrinterInfo(host=host, serial=serial, model=str(data.get("model", "")))


def _cache_write(info: PrinterInfo) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(
            json.dumps({"host": info.host, "serial": info.serial, "model": info.model})
        )
    except OSError:
        pass


def _reachable(host: str, port: int = 8883, timeout: float = 1.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def discover(timeout: float = 3.0) -> list[PrinterInfo]:
    """Ask the local network for Bambu printers. Returns unique devices by serial."""
    socket_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    socket_.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    socket_.settimeout(0.5)

    found: dict[str, PrinterInfo] = {}
    try:
        for target in SSDP_TARGETS:
            try:
                socket_.sendto(_MSEARCH, (target, 2021))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _addr = socket_.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            info = parse_ssdp_response(data.decode("utf-8", "replace"))
            if info is not None:
                found.setdefault(info.serial, info)
    finally:
        socket_.close()

    return list(found.values())


def choose_device(
    devices: Iterable[PrinterInfo], serial: str | None = None
) -> PrinterInfo | None:
    """Pick the intended printer: by serial, or the only one present."""
    devices = list(devices)
    if serial:
        return next((d for d in devices if d.serial == serial), None)
    return devices[0] if len(devices) == 1 else None


class BootstrapError(RuntimeError):
    """Discovery could not produce a usable printer."""


def resolve(cfg: Any) -> Resolved:
    """Fill in whatever the config file left out.

    Explicit config always wins; discovery only fills gaps.
    """
    host = (getattr(cfg, "host", "") or "").strip()
    serial = (getattr(cfg, "serial", "") or "").strip()
    access = (getattr(cfg, "access_code", "") or "").strip()
    model = (getattr(cfg, "model", "auto") or "auto").strip()

    devices: list[PrinterInfo] = []
    if not host or not serial:
        chosen = None
        # SSDP is UDP and lossy on busy networks; a few rounds plus the last
        # known printer keeps a missed packet from stopping the app.
        for _attempt in range(3):
            devices = discover(timeout=2.5)
            chosen = choose_device(devices, serial or None)
            if chosen is not None:
                break
            cached = _cache_read()
            if cached is not None and _reachable(cached.host):
                chosen = cached
                break
            time.sleep(0.5)
        if chosen is None:
            cached = _cache_read()
            if cached is not None and _reachable(cached.host):
                chosen = cached
        if chosen is None and not host:
            if not devices:
                raise BootstrapError(
                    "no Bambu printer answered SSDP; set 'host' in the config"
                )
            raise BootstrapError(
                "several printers answered SSDP; set 'serial' in the config"
            )
        if chosen is not None:
            host = host or chosen.host
            serial = serial or chosen.serial
            if model == "auto" and chosen.model:
                model = chosen.model

    if not access:
        access = read_studio_access_codes().get(serial, "")

    if not serial:
        raise BootstrapError("could not determine the printer serial")
    if not host:
        raise BootstrapError("could not determine the printer address")
    if not access:
        raise BootstrapError(
            "no access code: add the printer to Bambu Studio, or set 'access_code'"
        )

    _cache_write(PrinterInfo(host=host, serial=serial, model=model))
    return Resolved(host=host, serial=serial, access_code=access, model=model)


def resolve_lan_host(serial: str = "") -> str:
    """LAN address for signed control, from the cache or one SSDP round."""
    cached = _cache_read()
    if cached and (not serial or cached.serial == serial):
        # No reachability probe here: launchd may deny local-network access,
        # and the control call reports the real error instead of a blank host.
        return cached.host
    devices = discover(timeout=3.0)
    chosen = choose_device(devices, serial or None)
    if chosen is not None:
        _cache_write(chosen)
        return chosen.host
    return ""


def resolve_cloud(cfg: Any) -> Resolved:
    """Cloud-only resolution: serial and model from the config or Studio.

    No SSDP and no printer address are needed. The networking plugin holds the
    account session and finds the printer through Bambu's cloud.
    """
    serial = (getattr(cfg, "serial", "") or "").strip()
    if not serial:
        serials = read_studio_serials()
        if not serials:
            raise BootstrapError(
                "no printer saved in Bambu Studio; add one there, or set 'serial'"
            )
        if len(serials) > 1:
            raise BootstrapError(
                "several printers saved in Bambu Studio; set 'serial' in the config"
            )
        serial = serials[0]

    access = (getattr(cfg, "access_code", "") or "").strip()
    if not access:
        access = read_studio_access_codes().get(serial, "")
    model = (getattr(cfg, "model", "auto") or "auto").strip()
    return Resolved(host="", serial=serial, access_code=access, model=model)
