from datetime import datetime, timezone
from pathlib import Path
import struct
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils.media_timestamp import (  # noqa: E402
    extract_recording_timestamp, format_media_timestamp,
    normalize_stored_timestamp)


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def test_mp4_movie_header_yields_embedded_recording_time(tmp_path):
    expected = datetime(2026, 10, 9, 15, 42, 7, tzinfo=timezone.utc)
    quicktime_seconds = int(expected.timestamp()) + 2_082_844_800
    mvhd = _box(
        b"mvhd",
        b"\x00\x00\x00\x00" + struct.pack(">I", quicktime_seconds)
        + struct.pack(">I", quicktime_seconds) + b"\x00" * 20,
    )
    video = tmp_path / "camera.mp4"
    video.write_bytes(_box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov", mvhd))

    timestamp = extract_recording_timestamp(video)

    assert timestamp.recorded_at == "2026-10-09T15:42:07+00:00"
    assert timestamp.source == "Video metadata"
    assert "Recorded Oct 9, 2026 at" in format_media_timestamp(
        timestamp.recorded_at, timestamp.source)


def test_smartycam_movie_header_preserves_local_camera_clock(tmp_path):
    camera_clock = datetime(2026, 10, 8, 18, 30, 29, tzinfo=timezone.utc)
    quicktime_seconds = int(camera_clock.timestamp()) + 2_082_844_800
    mvhd = _box(
        b"mvhd",
        b"\x00\x00\x00\x00" + struct.pack(">I", quicktime_seconds)
        + struct.pack(">I", quicktime_seconds) + b"\x00" * 20,
    )
    video = tmp_path / "Alex_SCHD0796.MP4"
    video.write_bytes(_box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov", mvhd))

    timestamp = extract_recording_timestamp(video)
    value = datetime.fromisoformat(timestamp.recorded_at)

    assert (value.year, value.month, value.day, value.hour, value.minute) == (
        2026, 10, 8, 18, 30)
    assert timestamp.source == "SmartyCam time"


def test_legacy_smartycam_utc_value_is_corrected_without_changing_clock():
    timestamp = normalize_stored_timestamp(
        "Alex_SCHD0796.MP4", "2026-10-08T18:30:29+00:00",
        "Video metadata")
    value = datetime.fromisoformat(timestamp.recorded_at)

    assert (value.hour, value.minute, value.second) == (18, 30, 29)
    assert timestamp.source == "SmartyCam time"


def test_invalid_xrk_uses_file_date_without_calling_native_sdk(
        tmp_path, monkeypatch):
    telemetry = tmp_path / "run.xrk"
    telemetry.write_bytes(b"not an XRK container")
    monkeypatch.setattr(
        "utils.media_timestamp._load_xrk_dll",
        lambda: (_ for _ in ()).throw(AssertionError("native DLL was called")),
    )

    timestamp = extract_recording_timestamp(telemetry)

    assert timestamp.recorded_at
    assert timestamp.source == "File date"
    assert format_media_timestamp(
        timestamp.recorded_at, timestamp.source).startswith("Dated ")
