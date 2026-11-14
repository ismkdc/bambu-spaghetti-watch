#!/usr/bin/env python3
"""Mint a cloud camera URL through Bambu Studio's own networking plugin.

Drives bridge/libbswbridge.dylib in-process:
  shim -> self-attach flag hook -> agent -> header map -> start ->
  connect_server -> device table -> select machine -> get_camera_url.

The plugin gates get_camera_url on a per-device "encrypt flag" that is only
set after a certificate exchange. The exchange is unreliable headless, so this
tool installs a one-byte in-process hook (bridge/force_flag.js) that makes the
gate pass; the /ttcode request itself is still signed by the plugin and its
result is what the cloud returns.

Nothing is redistributed: the plugin and the account stay where the user put
them.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
STUDIO = HOME / "Library/Application Support/BambuStudio"
# Keep the agent out of Studio's config dir (it writes state and takes locks).
AGENT_DIR = HOME / "Library/Application Support/BambuSpaghettiWatch"
PLUGIN = STUDIO / "plugins/libbambu_networking.dylib"
STUDIO_CERT = Path("/Applications/BambuStudio.app/Contents/Resources/cert")
BRIDGE = Path(__file__).with_name("libbswbridge.dylib")
FLAG_HOOK = Path(__file__).with_name("force_flag.js")
SHIM = Path(__file__).resolve().parent.parent / "vendor/bambu-headless/h2d_host_identity.dylib"
STUDIO_EXE = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"


def ensure_host_identity(no_shim: bool = False) -> None:
    """Re-exec under the process-local executable-path shim.

    The plugin signs cloud requests only when _NSGetExecutablePath() looks like
    the installed Studio binary. Without this the ttcode PoP answers 403.
    """
    if no_shim or os.environ.get("BSW_SHIM") == "1" or not SHIM.exists():
        return
    import sysconfig
    site = sysconfig.get_paths()["purelib"]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = site + (":" + existing if existing else "")
    env["DYLD_INSERT_LIBRARIES"] = str(SHIM)
    env["H2D_STUDIO_EXECUTABLE"] = STUDIO_EXE
    env["BSW_SHIM"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


def _seed_agent_dir() -> None:
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


def conf() -> dict:
    return json.loads((STUDIO / "BambuStudio.conf").read_text())


def serial_from_conf() -> str:
    data = conf()
    codes = {**data.get("user_access_code", {}), **data.get("access_code", {})}
    codes = [k for k, v in codes.items() if v]
    if len(codes) != 1:
        raise SystemExit(f"expected exactly one saved printer, found {len(codes)}")
    return codes[0]


def access_code(serial: str) -> str | None:
    data = conf()
    return data.get("access_code", {}).get(serial) or data.get("user_access_code", {}).get(serial)


class Bridge:
    def __init__(self) -> None:
        if not BRIDGE.exists():
            raise SystemExit(f"missing {BRIDGE}; build it with the command in bridge/README.md")
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
            "bsw_server_connected": ([c], ctypes.c_int),
            "bsw_is_user_login": ([c], ctypes.c_int),
            "bsw_connect_server": ([c], ctypes.c_int),
            "bsw_user_print_info": ([c, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_select_machine": ([c, ctypes.c_char_p], ctypes.c_int),
            "bsw_install_device_cert": ([c, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_camera_url": ([c, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            "bsw_camera_url_result": ([], ctypes.c_char_p),
            "bsw_buffer": ([], ctypes.c_char_p),
            "bsw_buffer_contains": ([ctypes.c_char_p], ctypes.c_int),
            "bsw_last_error": ([], ctypes.c_char_p),
            "bsw_destroy_agent": ([c], ctypes.c_int),
        }
        for name, (args, restype) in decls.items():
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = restype
        self.lib = lib

    def err(self) -> str:
        return (self.lib.bsw_last_error() or b"").decode("utf-8", "replace")


def self_attach_flag_hook() -> object | None:
    """Load force_flag.js into this process with Frida. Returns the session."""
    try:
        import frida
    except ImportError:
        return None
    if not FLAG_HOOK.exists():
        return None
    session = frida.attach(os.getpid())
    script = session.create_script(FLAG_HOOK.read_text())
    script.on("message", lambda message, _data: None)
    script.load()
    time.sleep(0.5)  # let the poll install the hook
    return session


def mint(serial: str, *, dev_ver: str = "01.03.00.00",
         protocols: str = '"tutk","agora"', timeout: float = 40.0,
         wait: float = 0.5, cert_tries: int = 1, force_flag: bool = True,
         skip_cert: bool = False, no_headers: bool = False,
         verbose: bool = False) -> str | None:
    def log(message: str) -> None:
        if verbose:
            print(message, flush=True)

    b = Bridge()
    version = b.lib.bsw_plugin_version(str(PLUGIN).encode())
    log(f"plugin : {version.decode() if version else b.err()}")
    if not version:
        return None

    session = None
    log_dir = Path(tempfile.mkdtemp(prefix="bsw-log-"))
    _seed_agent_dir()
    agent = b.lib.bsw_create_agent(str(PLUGIN).encode(), str(log_dir).encode(),
                                   str(AGENT_DIR).encode())
    if not agent:
        log(f"create_agent failed: {b.err()}")
        return None
    log(f"agent  : {agent}")

    # Install the encrypt-flag hook after the plugin is mapped, so the poll in
    # force_flag.js resolves the module immediately.
    if force_flag:
        session = self_attach_flag_hook()
    log(f"flag hook: {'on' if session else 'off'}")

    try:
        log(f"callbacks: {b.lib.bsw_install_callbacks(agent)}")
        log(f"cloud    : {b.lib.bsw_setup_cloud(agent, str(STUDIO_CERT).encode(), b'US')}")
        if not no_headers:
            slicer_uuid = conf().get("app", {}).get("slicer_uuid", "")
            log(f"headers  : {b.lib.bsw_set_headers(agent, slicer_uuid.encode())}")
        log(f"init_log : {b.lib.bsw_init_log(agent)}")
        log(f"start    : {b.lib.bsw_start(agent)}")

        log(f"connect_server: {b.lib.bsw_connect_server(agent)}")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if b.lib.bsw_server_connected(agent) and b.lib.bsw_is_user_login(agent):
                break
            time.sleep(0.5)
        log(f"server   : {b.lib.bsw_server_connected(agent)}  login: {b.lib.bsw_is_user_login(agent)}")

        body = ctypes.create_string_buffer(400_000)
        rc = b.lib.bsw_user_print_info(agent, body, len(body))
        log(f"device table: rc={rc} bytes={len(body.value)}")

        log(f"select   : {b.lib.bsw_select_machine(agent, serial.encode())}")

        if not skip_cert:
            installed = False
            for attempt in range(1, max(1, cert_tries) + 1):
                b.lib.bsw_install_device_cert(agent, serial.encode(), 0)
                deadline = time.monotonic() + wait
                while time.monotonic() < deadline:
                    if b.lib.bsw_buffer_contains(b"device_cert_installed"):
                        installed = True
                        break
                    time.sleep(0.25)
                log(f"cert try {attempt}: installed={installed}")
                if installed:
                    break

        key = f"{serial}|{dev_ver}|{protocols}"
        log(f"mint key : {key}")
        rc = b.lib.bsw_camera_url(agent, key.encode(), int(timeout * 1000))
        url = b.lib.bsw_camera_url_result()
        log(f"mint rc  : {rc}")
        if rc != 0:
            log(f"mint err : {b.err()}")
            return None
        text = url.decode("utf-8", "replace") if url else None
        if text and "bambu:///" not in text:
            log(f"server   : {text}")
            return None
        return text
    finally:
        b.lib.bsw_destroy_agent(agent)
        if session:
            session.detach()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None)
    ap.add_argument("--dev-ver", default="01.03.00.00")
    ap.add_argument("--protocols", default='"tutk","agora"')
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--wait", type=float, default=0.5)
    ap.add_argument("--cert-tries", type=int, default=1)
    ap.add_argument("--skip-cert", action="store_true")
    ap.add_argument("--no-force-flag", action="store_true")
    ap.add_argument("--pause", type=float, default=0.0)
    ap.add_argument("--no-shim", action="store_true")
    ap.add_argument("--no-headers", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ensure_host_identity(args.no_shim)
    serial = args.serial or serial_from_conf()

    if args.pause:
        print(f"pid      : {os.getpid()}", flush=True)
        time.sleep(args.pause)

    url = mint(serial, dev_ver=args.dev_ver,
               protocols=args.protocols, timeout=args.timeout, wait=args.wait,
               cert_tries=args.cert_tries, force_flag=not args.no_force_flag,
               skip_cert=args.skip_cert, no_headers=args.no_headers, verbose=True)
    if not url:
        return 1
    print(url)
    if args.out:
        Path(args.out).write_text(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
