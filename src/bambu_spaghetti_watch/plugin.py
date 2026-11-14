"""ctypes bridge to Bambu Studio's networking plugin.

The plugin is Bambu's own client: it holds the user's cloud session, signs
control commands and delivers the printer's status reports. Nothing is
redistributed; the library is the one Studio installed.

Build the C ABI with ``bridge/build.sh``.
"""
from __future__ import annotations

import ctypes
import tempfile
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "bridge" / "libbswbridge.dylib"
PLUGIN = (
    Path.home() / "Library" / "Application Support" / "BambuStudio" / "plugins"
    / "libbambu_networking.dylib"
)
STUDIO = Path.home() / "Library" / "Application Support" / "BambuStudio"
# The plugin writes BambuNetworkEngine.conf and takes locks inside its config
# directory. Point the agent at a copy so Studio and the agent can run together.
AGENT_DIR = Path.home() / "Library" / "Application Support" / "BambuSpaghettiWatch"
CERT = Path("/Applications/BambuStudio.app/Contents/Resources/cert")


def agent_config_dir() -> Path:
    """A private config dir seeded from Studio's, so both can run at once."""
    try:
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("BambuStudio.conf", "BambuNetworkEngine.conf"):
            source = STUDIO / name
            target = AGENT_DIR / name
            if source.exists() and (not target.exists()
                                    or source.stat().st_mtime > target.stat().st_mtime):
                target.write_bytes(source.read_bytes())
        source_certs = STUDIO / "certs"
        target_certs = AGENT_DIR / "certs"
        if source_certs.is_dir():
            target_certs.mkdir(parents=True, exist_ok=True)
            for path in source_certs.iterdir():
                if path.is_file() and not (target_certs / path.name).exists():
                    (target_certs / path.name).write_bytes(path.read_bytes())
    except OSError:
        pass
    return AGENT_DIR


class Bridge:
    def __init__(self) -> None:
        if not BRIDGE.exists():
            raise FileNotFoundError(f"missing {BRIDGE}; run bridge/build.sh")
        lib = ctypes.CDLL(str(BRIDGE))
        c = ctypes.c_void_p
        decls = {
            "bsw_plugin_version": ([ctypes.c_char_p], ctypes.c_char_p),
            "bsw_create_agent": ([ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p], c),
            "bsw_install_callbacks": ([c], ctypes.c_int),
            "bsw_setup_cloud": ([c, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
            "bsw_set_headers": ([c, ctypes.c_char_p], ctypes.c_int),
            "bsw_init_log": ([c], ctypes.c_int),
            "bsw_start": ([c], ctypes.c_int),
            "bsw_connect_server": ([c], ctypes.c_int),
            "bsw_server_connected": ([c], ctypes.c_int),
            "bsw_is_user_login": ([c], ctypes.c_int),
            "bsw_user_print_info": ([c, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_select_machine": ([c, ctypes.c_char_p], ctypes.c_int),
            "bsw_install_device_cert": ([c, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_send": ([c, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
            "bsw_send_message": ([c, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
            "bsw_buffer": ([], ctypes.c_char_p),
            "bsw_clear_buffer": ([], None),
            "bsw_last_error": ([], ctypes.c_char_p),
            "bsw_connect": ([c, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_connected": ([], ctypes.c_int),
            "bsw_destroy_agent": ([c], ctypes.c_int),
        }
        for name, (args, restype) in decls.items():
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = restype
        self.lib = lib

    def err(self) -> str:
        return (self.lib.bsw_last_error() or b"").decode("utf-8", "replace")

    def version(self) -> str | None:
        value = self.lib.bsw_plugin_version(str(PLUGIN).encode())
        return value.decode() if value else None

    def create_agent(self, log_dir: str | None = None):
        return self.lib.bsw_create_agent(
            str(PLUGIN).encode(),
            (log_dir or tempfile.mkdtemp(prefix="bsw-app-")).encode(),
            str(agent_config_dir()).encode(),
        )

    def setup_cloud(self, agent) -> int:
        return self.lib.bsw_setup_cloud(agent, str(CERT).encode(), b"US")

    def set_headers(self, agent, slicer_uuid: str) -> int:
        return self.lib.bsw_set_headers(agent, slicer_uuid.encode())

    def install_callbacks(self, agent) -> int:
        return self.lib.bsw_install_callbacks(agent)

    def init_log(self, agent) -> int:
        return self.lib.bsw_init_log(agent)

    def start(self, agent) -> int:
        return self.lib.bsw_start(agent)

    def connect_server(self, agent) -> int:
        return self.lib.bsw_connect_server(agent)

    def server_connected(self, agent) -> bool:
        return bool(self.lib.bsw_server_connected(agent))

    def is_user_login(self, agent) -> bool:
        return bool(self.lib.bsw_is_user_login(agent))

    def user_print_info(self, agent) -> str:
        body = ctypes.create_string_buffer(400_000)
        self.lib.bsw_user_print_info(agent, body, len(body))
        return body.value.decode("utf-8", "replace")

    def select_machine(self, agent, serial: str) -> int:
        return self.lib.bsw_select_machine(agent, serial.encode())

    def install_device_cert(self, agent, serial: str, cloud_only: int = 0) -> int:
        return self.lib.bsw_install_device_cert(agent, serial.encode(), cloud_only)

    def send(self, agent, serial: str, payload: str, qos: int = 1, flag: int = 0) -> int:
        return self.lib.bsw_send(agent, serial.encode(), payload.encode(), qos, flag)

    def send_message(self, agent, serial: str, payload: str, qos: int = 1, flag: int = 0) -> int:
        return self.lib.bsw_send_message(agent, serial.encode(), payload.encode(), qos, flag)

    def buffer(self) -> bytes:
        return self.lib.bsw_buffer() or b""

    def clear_buffer(self) -> None:
        self.lib.bsw_clear_buffer()

    def connect(self, agent, serial: str, ip: str, user: str, password: str,
                use_ssl: bool) -> int:
        return self.lib.bsw_connect(agent, serial.encode(), ip.encode(),
                                    user.encode(), password.encode(), 1 if use_ssl else 0)

    def connected(self) -> bool:
        return bool(self.lib.bsw_connected())

    def destroy(self, agent) -> int:
        return self.lib.bsw_destroy_agent(agent)
