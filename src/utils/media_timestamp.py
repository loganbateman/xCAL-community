"""Recording-start timestamps for AiM telemetry and session video files."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import struct
import sys
import threading

import config


@dataclass(frozen=True)
class MediaTimestamp:
    recorded_at: str | None = None
    source: str | None = None


_QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
_SMARTYCAM_NAME = re.compile(r"SCHD\d+", re.IGNORECASE)
_XRK_LOCK = threading.Lock()
_XRK_DLL = None
_XRK_DLL_DIRECTORY = None


class _Tm(ctypes.Structure):
    _fields_ = [
        ("tm_sec", ctypes.c_int),
        ("tm_min", ctypes.c_int),
        ("tm_hour", ctypes.c_int),
        ("tm_mday", ctypes.c_int),
        ("tm_mon", ctypes.c_int),
        ("tm_year", ctypes.c_int),
        ("tm_wday", ctypes.c_int),
        ("tm_yday", ctypes.c_int),
        ("tm_isdst", ctypes.c_int),
    ]


def _sdk_directories() -> list[Path]:
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS) / "aim_xrk")
    roots.extend([
        config.APP_ROOT / "aim_xrk",
        config.APP_ROOT / "build" / "vendor" / "aim_xrk",
    ])
    return roots


def _load_xrk_dll():
    global _XRK_DLL, _XRK_DLL_DIRECTORY
    if _XRK_DLL is not None:
        return _XRK_DLL
    for folder in _sdk_directories():
        dll_path = folder / "MatLabXRK-2022-64-ReleaseU.dll"
        if not dll_path.is_file():
            continue
        # Keep the directory handle alive as long as the DLL. Its libxml,
        # pthread and zlib dependencies are shipped beside it.
        if hasattr(os, "add_dll_directory"):
            _XRK_DLL_DIRECTORY = os.add_dll_directory(str(folder))
        dll = ctypes.CDLL(str(dll_path))
        dll.open_file.argtypes = [ctypes.c_char_p]
        dll.open_file.restype = ctypes.c_int
        dll.get_date_and_time.argtypes = [ctypes.c_int]
        dll.get_date_and_time.restype = ctypes.POINTER(_Tm)
        dll.close_file_i.argtypes = [ctypes.c_int]
        dll.close_file_i.restype = ctypes.c_int
        _XRK_DLL = dll
        return dll
    return None


def _xrk_timestamp(path: Path) -> MediaTimestamp:
    """Read the logger's acquisition start through AiM's official XRK DLL."""
    try:
        # The vendor DLL does not safely reject every arbitrary/corrupt input.
        # Validate the XRK container signature before crossing the native API.
        with path.open("rb") as handle:
            if handle.read(5) != b"<hCNF":
                return MediaTimestamp()
    except OSError:
        return MediaTimestamp()
    with _XRK_LOCK:
        try:
            dll = _load_xrk_dll()
            if dll is None:
                return MediaTimestamp()
            file_index = dll.open_file(os.fsencode(str(path.resolve())))
            if file_index <= 0:
                return MediaTimestamp()
            try:
                value = dll.get_date_and_time(file_index)
                if not value:
                    return MediaTimestamp()
                tm = value.contents
                recorded = datetime(
                    tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                    tm.tm_hour, tm.tm_min, tm.tm_sec,
                ).astimezone()
                return MediaTimestamp(recorded.isoformat(), "XRK logger time")
            finally:
                dll.close_file_i(file_index)
        except (OSError, ValueError, OverflowError):
            return MediaTimestamp()


def _box_header(handle, offset: int, limit: int):
    if offset + 8 > limit:
        return None
    handle.seek(offset)
    header = handle.read(16)
    if len(header) < 8:
        return None
    size32, box_type = struct.unpack(">I4s", header[:8])
    header_size = 8
    if size32 == 1:
        if len(header) < 16:
            return None
        size = struct.unpack(">Q", header[8:16])[0]
        header_size = 16
    elif size32 == 0:
        size = limit - offset
    else:
        size = size32
    if size < header_size or offset + size > limit:
        return None
    return box_type, offset + header_size, offset + size


def _find_mvhd(handle, start: int, limit: int, depth=0):
    offset = start
    while offset < limit:
        box = _box_header(handle, offset, limit)
        if box is None:
            return None
        box_type, payload_start, box_end = box
        if box_type == b"mvhd":
            return payload_start, box_end
        if box_type in {b"moov", b"udta"} and depth < 3:
            found = _find_mvhd(
                handle, payload_start, box_end, depth=depth + 1)
            if found:
                return found
        offset = box_end
    return None


def _video_timestamp(path: Path) -> MediaTimestamp:
    """Read the movie creation time without decoding or scanning video data."""
    try:
        with path.open("rb") as handle:
            found = _find_mvhd(handle, 0, path.stat().st_size)
            if not found:
                return MediaTimestamp()
            payload_start, box_end = found
            handle.seek(payload_start)
            version_flags = handle.read(4)
            if len(version_flags) != 4:
                return MediaTimestamp()
            version = version_flags[0]
            width = 8 if version == 1 else 4
            raw = handle.read(width)
            if len(raw) != width or payload_start + 4 + width > box_end:
                return MediaTimestamp()
            seconds = int.from_bytes(raw, "big")
            # Zero and one are common placeholders, not useful dates.
            if seconds <= 1:
                return MediaTimestamp()
            value = datetime.fromtimestamp(
                _QUICKTIME_EPOCH.timestamp() + seconds, tz=timezone.utc)
            # Reject corrupt/sentinel values outside useful camera dates.
            if not 1990 <= value.year <= datetime.now(timezone.utc).year + 2:
                return MediaTimestamp()
            if _SMARTYCAM_NAME.search(path.name):
                # SmartyCam writes local wall-clock fields into mvhd. Treating
                # those fields as UTC shifted Indianapolis footage four hours.
                value = value.replace(tzinfo=None).astimezone()
                return MediaTimestamp(value.isoformat(), "SmartyCam time")
            return MediaTimestamp(value.isoformat(), "Video metadata")
    except (OSError, OverflowError, ValueError, struct.error):
        return MediaTimestamp()


def extract_recording_timestamp(path: str | Path) -> MediaTimestamp:
    """Return the best timestamp embedded in a supported local media file."""
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix == ".xrk":
        value = _xrk_timestamp(path)
    elif suffix in {".mp4", ".mov"}:
        value = _video_timestamp(path)
    else:
        return MediaTimestamp()
    if value.recorded_at:
        return value
    try:
        fallback = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return MediaTimestamp(fallback.isoformat(), "File date")
    except (OSError, OverflowError, ValueError):
        return MediaTimestamp()


def normalize_stored_timestamp(
        name: str, recorded_at: str | None,
        source: str | None) -> MediaTimestamp:
    """Correct timestamps saved before SmartyCam wall-clock handling."""
    if (not recorded_at or source != "Video metadata"
            or not _SMARTYCAM_NAME.search(name or "")):
        return MediaTimestamp(recorded_at, source)
    try:
        old = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        corrected = old.replace(tzinfo=None).astimezone()
        return MediaTimestamp(corrected.isoformat(), "SmartyCam time")
    except (TypeError, ValueError, OverflowError):
        return MediaTimestamp(recorded_at, source)


def format_media_timestamp(
        recorded_at: str | None, source: str | None, compact=False) -> str:
    """Format stored RFC 3339 metadata for a compact, honest card label."""
    if not recorded_at:
        return "Time unavailable"
    try:
        value = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if value.tzinfo is not None:
            value = value.astimezone()
        pattern = (
            "%b %d, %Y · %I:%M %p" if compact
            else "%b %d, %Y at %I:%M:%S %p")
        stamp = value.strftime(pattern).replace(" 0", " ")
    except (TypeError, ValueError):
        return "Time unavailable"
    prefix = (
        "Recorded"
        if source in {"XRK logger time", "SmartyCam time", "Video metadata"}
        else "Dated")
    return f"{prefix} {stamp} ({source or 'file metadata'})"
