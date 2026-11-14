"""Read a frame from Bambu's own ``libBambuSource`` live-view library.

The cloud live-view URL (``bambu:///tutk?...`` or ``bambu:///local/...``) is
opaque: it names a tunnel, not an RTSP endpoint. ``libBambuSource`` owns that
tunnel and hands back H.264 access units, which ffmpeg decodes to one JPEG.

The library ships with Bambu Studio; nothing is redistributed. The URL itself
is minted elsewhere (see :mod:`bambu_spaghetti_watch.camera.cloud`).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path

LIB = Path.home() / "Library/Application Support/BambuStudio/plugins/libBambuSource.dylib"

SUCCESS, STREAM_END, WOULD_BLOCK, BUFFER_LIMIT = 0, 1, 2, 3
DEFAULT_SECONDS = 20.0


class _Sample(ctypes.Structure):
    _fields_ = [
        ("itrack", ctypes.c_int),
        ("size", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
        ("decode_time", ctypes.c_ulonglong),
    ]


class _StreamInfo(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("sub_type", ctypes.c_int),
        ("format", ctypes.c_byte * 32),
        ("format_type", ctypes.c_int),
        ("format_size", ctypes.c_int),
        ("max_frame_size", ctypes.c_int),
        ("format_buffer", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_LOGGER = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)


def _lib() -> ctypes.CDLL:
    if not LIB.exists():
        raise FileNotFoundError(f"missing {LIB}")
    lib = ctypes.CDLL(str(LIB))
    lib.Bambu_Create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    lib.Bambu_Create.restype = ctypes.c_int
    lib.Bambu_Open.argtypes = [ctypes.c_void_p]
    lib.Bambu_Open.restype = ctypes.c_int
    lib.Bambu_StartStream.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    lib.Bambu_StartStream.restype = ctypes.c_int
    lib.Bambu_GetStreamCount.argtypes = [ctypes.c_void_p]
    lib.Bambu_GetStreamCount.restype = ctypes.c_int
    lib.Bambu_ReadSample.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Sample)]
    lib.Bambu_ReadSample.restype = ctypes.c_int
    lib.Bambu_Close.argtypes = [ctypes.c_void_p]
    lib.Bambu_Destroy.argtypes = [ctypes.c_void_p]
    lib.Bambu_GetLastErrorMsg.restype = ctypes.c_char_p
    lib.Bambu_SetLogger.argtypes = [ctypes.c_void_p, _LOGGER, ctypes.c_void_p]
    return lib


def read_h264(url: str, seconds: float = DEFAULT_SECONDS, max_samples: int = 40) -> bytes | None:
    """Open the tunnel, read access units, and return them concatenated."""
    lib = _lib()
    tunnel = ctypes.c_void_p()
    rc = lib.Bambu_Create(ctypes.byref(tunnel), url.encode())
    if rc != SUCCESS or not tunnel:
        return None

    logger = _LOGGER(lambda ctx, level, msg: None)
    try:
        lib.Bambu_SetLogger(tunnel, logger, None)

        deadline = _monotonic() + seconds
        while _monotonic() < deadline:
            rc = lib.Bambu_Open(tunnel)
            if rc == SUCCESS:
                break
            if rc != WOULD_BLOCK:
                return None
            _sleep(0.1)
        else:
            return None

        while _monotonic() < deadline:
            rc = lib.Bambu_StartStream(tunnel, True)
            if rc == SUCCESS:
                break
            if rc != WOULD_BLOCK:
                return None
            _sleep(0.1)
        else:
            return None

        chunks: list[bytes] = []
        sample = _Sample()
        while len(chunks) < max_samples and _monotonic() < deadline + seconds:
            rc = lib.Bambu_ReadSample(tunnel, ctypes.byref(sample))
            if rc == SUCCESS and sample.size > 0:
                chunks.append(ctypes.string_at(sample.buffer, sample.size))
            elif rc == WOULD_BLOCK:
                _sleep(0.05)
            elif rc == STREAM_END:
                break
            else:
                break
        return b"".join(chunks) if chunks else None
    finally:
        try:
            lib.Bambu_Close(tunnel)
            lib.Bambu_Destroy(tunnel)
        except Exception:
            pass


def to_jpeg(h264: bytes | None) -> bytes | None:
    """Decode the first frame of an H.264 byte stream to JPEG."""
    if not h264:
        return None
    handle, path = tempfile.mkstemp(suffix=".h264")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(h264)
        out = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "h264", "-i", path,
             "-frames:v", "1", "-f", "image2", "pipe:1"],
            capture_output=True, timeout=60,
        )
        return out.stdout or None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def grab_frame(url: str, seconds: float = DEFAULT_SECONDS) -> bytes | None:
    """Convenience: URL in, one JPEG out."""
    return to_jpeg(read_h264(url, seconds=seconds))


def _monotonic() -> float:
    import time
    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)
