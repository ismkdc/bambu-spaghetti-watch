"""Command line entry point: run the menu bar app, or probe the hardware.

Everything goes through Bambu's cloud session: status, camera and control. The
printer's LAN address is not used, so the app starts at login without a local
network permission and works from another network too.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from bambu_spaghetti_watch import host_identity
from bambu_spaghetti_watch.bootstrap import BootstrapError, resolve_cloud
from bambu_spaghetti_watch.camera import make_camera
from bambu_spaghetti_watch.camera.base import CameraNotConfigured
from bambu_spaghetti_watch.cloud_printer import CloudPrinter
from bambu_spaghetti_watch.config import DEFAULT_CONFIG_PATH, Config, load_config
from bambu_spaghetti_watch.detector import Detector
from bambu_spaghetti_watch.pipeline import Pipeline

PROBE_WAIT_SECONDS = 30.0

LOGGER = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("BSW_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _notify(verdict: str, score: float) -> None:
    """macOS notification, raised only for a cooldown-gated failure."""
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "Failure score {score:.2f}" '
            f'with title "Bambu Spaghetti Watch"',
        ],
        check=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bambu-watch",
        description="Detect print failures on a Bambu Lab printer and pause it.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"path to config.json (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="check the cloud session, camera and model, then exit",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="print the printer's camera and AI-detection settings, then exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single detection step and print the verdict",
    )
    return parser


SETTINGS_BLOCKS = ("ipcam", "xcam", "net")
SETTINGS_WAIT_SECONDS = 15.0


async def wait_for_report(printer: Any, timeout: float, poll: float = 0.25) -> bool:
    """Wait for the first status report; a fixed sleep races the cloud."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if printer.state is not None:
            return True
        await asyncio.sleep(poll)
    return False


def summarise_settings(report: dict) -> dict[str, dict]:
    """Pick the blocks worth printing out of one full report payload."""
    block = report.get("print") or {}
    return {
        key: block[key] for key in SETTINGS_BLOCKS if isinstance(block.get(key), dict)
    }


def model_paths(cfg: Config) -> tuple[Path, Path]:
    base = cfg.model_dir or Path(__file__).parent / "models"
    return base / "model.cfg", base / "model-weights.darknet"


async def probe(cfg: Config) -> int:
    print(f"printer   : {cfg.serial} (cloud)")
    print(f"camera    : {cfg.camera_mode}")
    print()

    printer = CloudPrinter(cfg.serial, cfg.access_code, cfg.model)
    printer.start()
    print("cloud     : connecting to the Bambu account ...")

    if not await wait_for_report(printer, PROBE_WAIT_SECONDS):
        print("cloud     : no status received — "
              f"{printer.last_error or 'check the Bambu Studio session'}")
        printer.stop()
        return 1

    model = printer.machine or cfg.model
    print(f"cloud     : ok, state={printer.state} machine={model}")

    camera = make_camera(cfg, model)
    problem = getattr(camera, "configuration_problem", None)
    if problem:
        print(f"camera    : {problem}")
        printer.stop()
        return 2
    started = time.monotonic()
    try:
        frame = await camera.grab()
    except CameraNotConfigured as err:
        print(f"camera    : {err}")
        printer.stop()
        return 2
    elapsed_ms = (time.monotonic() - started) * 1000

    if frame is None:
        print(f"camera    : no frame after {elapsed_ms:.0f} ms")
        detail = getattr(camera, "last_error", None)
        if detail:
            print(f"            {detail}")
        print("            check the Bambu Studio session and the installed plugin")
        printer.stop()
        return 1

    print(f"camera    : {len(frame)} bytes in {elapsed_ms:.0f} ms")

    cfg_path, weights_path = model_paths(cfg)
    if not (cfg_path.exists() and weights_path.exists()):
        print(f"model     : missing weights in {weights_path.parent}")
        print("            download model.cfg + model-weights.darknet there")
        printer.stop()
        return 2

    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
    detector = Detector.load(cfg_path, weights_path)

    started = time.monotonic()
    detections = detector.infer(image)
    infer_ms = (time.monotonic() - started) * 1000
    print(f"model     : {len(detections)} detections in {infer_ms:.0f} ms")

    detector.close()
    printer.stop()
    return 0


async def settings(cfg: Config) -> int:
    printer = CloudPrinter(cfg.serial, cfg.access_code, cfg.model)
    printer.start()

    deadline = time.monotonic() + SETTINGS_WAIT_SECONDS
    while time.monotonic() < deadline:
        if summarise_settings(printer.last_raw).get("ipcam"):
            break
        await asyncio.sleep(0.5)

    blocks = summarise_settings(printer.last_raw)
    if not blocks:
        print("no settings received — check the Bambu Studio session")
        printer.stop()
        return 1

    for name, block in blocks.items():
        print(f"print.{name}:")
        for key, value in block.items():
            print(f"  {key}: {value!r}")

    printer.stop()
    return 0


async def once(cfg: Config) -> int:
    cfg_path, weights_path = model_paths(cfg)
    if not (cfg_path.exists() and weights_path.exists()):
        print(f"missing model files in {weights_path.parent}")
        return 2

    printer = CloudPrinter(cfg.serial, cfg.access_code, cfg.model)
    printer.start()
    if not await wait_for_report(printer, PROBE_WAIT_SECONDS):
        print("no status received — check the Bambu Studio session")
        printer.stop()
        return 1

    pipeline = Pipeline(
        cfg,
        make_camera(cfg, printer.machine or cfg.model),
        Detector.load(cfg_path, weights_path),
        printer,
        action=lambda verdict, score: print(f"action: {verdict} (score {score:.2f})"),
        control=None,
    )

    verdict = await pipeline.step()
    print(f"verdict   : {verdict}")
    printer.stop()
    return 0


def cloud_camera_problem(cfg: Config) -> str | None:
    """The reason this configuration cannot yield frames, if it is known already."""
    return getattr(make_camera(cfg), "configuration_problem", None)


def _with_cloud_config(cfg: Config) -> Config:
    """Fill in the serial (and access code) from the config file or Studio."""
    resolved = resolve_cloud(cfg)
    return replace(
        cfg,
        serial=resolved.serial,
        access_code=resolved.access_code,
        model=resolved.model,
    )


def main(argv: list[str] | None = None) -> int:
    host_identity.ensure()
    _setup_logging()
    args = build_parser().parse_args(argv)
    path = Path(args.config)

    if not path.exists() and args.config != str(DEFAULT_CONFIG_PATH):
        print(f"config not found: {args.config}")
        return 2

    try:
        cfg = load_config(path) if path.exists() else Config()
    except ValueError as err:
        print(f"config error: {err}")
        return 2

    if not args.settings:
        problem = cloud_camera_problem(cfg)
        if problem:
            print(f"camera error: {problem}")
            return 2

    try:
        cfg = _with_cloud_config(cfg)
    except BootstrapError as err:
        print(f"printer error: {err}")
        return 2

    if args.settings:
        return asyncio.run(settings(cfg))
    if args.probe:
        return asyncio.run(probe(cfg))
    if args.once:
        return asyncio.run(once(cfg))

    from bambu_spaghetti_watch.ui import MenuBarApp

    cfg_path, weights_path = model_paths(cfg)
    if not (cfg_path.exists() and weights_path.exists()):
        print(f"missing model files in {weights_path.parent}")
        return 2

    printer = CloudPrinter(cfg.serial, cfg.access_code, cfg.model, start_delay=20.0)
    printer.start()

    pipeline = Pipeline(
        cfg,
        make_camera(cfg, printer.machine or cfg.model),
        Detector.load(cfg_path, weights_path),
        printer,
        action=_notify,
        control=None,
    )
    MenuBarApp(cfg, pipeline, printer, control=None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
