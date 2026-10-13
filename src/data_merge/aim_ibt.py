#!/usr/bin/env python3
"""No-hardware bridge from a merged AiM CSV to iRacing IBT.

RaceStudio 3 officially imports IBT and can export imported sessions as DRK.
This bridge is intentionally separate from the production CSV merger because IBT
is a simulator interchange workaround, not an AiM-authored file format.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import statistics
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Variable:
    name: str
    kind: int
    offset: int
    units: str
    description: str


def fixed_ascii(value: str, size: int) -> bytes:
    encoded = value.encode("ascii", errors="replace")[: size - 1]
    return encoded + bytes(size - len(encoded))


def variable_header(variable: Variable) -> bytes:
    output = bytearray(144)
    struct.pack_into("<iii", output, 0, variable.kind, variable.offset, 1)
    output[16:48] = fixed_ascii(variable.name, 32)
    output[48:112] = fixed_ascii(variable.description, 64)
    output[112:144] = fixed_ascii(variable.units, 32)
    return bytes(output)


def read_aim_csv(
    source: Path,
) -> tuple[dict[str, list[str]], list[str], list[str], list[dict[str, str]]]:
    with source.open(newline="", encoding="utf-8-sig") as handle:
        raw_rows = list(csv.reader(handle))
    for index, row in enumerate(raw_rows[:-1]):
        units = raw_rows[index + 1]
        if row and row[0] == "Time" and units and units[0] == "s":
            metadata = {item[0]: item[1:] for item in raw_rows[:index] if item}
            data = [
                dict(zip(row, item))
                for item in raw_rows[index + 2:]
                if item and len(item) == len(row)
            ]
            if not data:
                raise ValueError("AiM CSV contains no data rows")
            return metadata, row, units, data
    raise ValueError("AiM channel and units rows not found")


def read_aim_channel_schema(source: str | Path) -> tuple[list[str], list[str]]:
    """Read only the channel and units rows from an AiM CSV."""
    candidate: list[str] | None = None
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if (candidate is not None and row and row[0] == "s"
                    and len(row) == len(candidate)):
                return candidate, row
            candidate = row if row and row[0] == "Time" else None
    raise ValueError("AiM channel and units rows not found")


def value(row: dict[str, str], name: str, default: float = 0.0) -> float:
    try:
        return float(row[name])
    except (KeyError, TypeError, ValueError):
        return default


def interpolated_row(
    left: dict[str, str],
    right: dict[str, str],
    fraction: float,
    header: list[str],
    units: list[str],
) -> dict[str, str]:
    """Interpolate continuous AiM channels without inventing discrete states."""
    selected = left if fraction <= 0.5 else right
    sampled = dict(selected)
    for name, unit in zip(header, units):
        if name == "Time" or unit.strip() in {"", "#"}:
            continue
        left_value = value(left, name, math.nan)
        right_value = value(right, name, math.nan)
        if math.isfinite(left_value) and math.isfinite(right_value):
            if name == "GPS Heading" and unit.strip() == "deg":
                delta = (right_value - left_value + 180.0) % 360.0 - 180.0
                interpolated = (left_value + fraction * delta) % 360.0
            else:
                interpolated = left_value + fraction * (
                    right_value - left_value)
            sampled[name] = str(interpolated)
    return sampled


def channel_unit(header: list[str], units: list[str], name: str) -> str:
    try:
        return units[header.index(name)].strip().casefold()
    except ValueError:
        return ""


def acceleration_mps2(
    row: dict[str, str], name: str, header: list[str], units: list[str]
) -> float:
    measured = value(row, name)
    unit = channel_unit(header, units, name).replace(" ", "")
    if unit in {"m/s^2", "m/s²", "m/s/s"}:
        return measured
    return measured * 9.80665


def angular_rate_rad_s(
    row: dict[str, str], name: str, header: list[str], units: list[str]
) -> float:
    measured = value(row, name)
    unit = channel_unit(header, units, name).replace(" ", "")
    if unit in {"rad/s", "rad/sec"}:
        return measured
    return measured * math.pi / 180.0


def temperature_c(
    row: dict[str, str],
    name: str,
    header: list[str],
    units: list[str],
) -> float:
    measured = value(row, name, math.nan)
    if not math.isfinite(measured):
        return math.nan
    unit = channel_unit(header, units, name)
    if unit in {"f", "°f", "degf"}:
        return (measured - 32.0) * 5.0 / 9.0
    return measured


def bridge_headings(
    rows: list[dict[str, str]],
    *,
    heading_channel: str = "GPS Heading",
    gyro_channel: str = "GPS Gyro",
    gyro_scale_to_deg_s: float = 1.0,
) -> list[float]:
    """Build a continuous heading trace from the valid AiM gyro channel.

    Some AiM exports briefly rotate GPS Heading by roughly 90 degrees per
    sample even while GPS Gyro remains continuous.  Feeding those samples into
    iRacing's velocity vector makes Race Studio derive impossible acceleration
    and yaw spikes. Use the first heading only as orientation, then integrate
    GPS Gyro so the heading derivative remains the original measured yaw rate.
    """
    if not rows:
        return []
    first = value(rows[0], heading_channel)
    headings = [first]
    previous_time = value(rows[0], "Time")
    previous_gyro = value(rows[0], gyro_channel) * gyro_scale_to_deg_s
    for row in rows[1:]:
        current_time = value(row, "Time", previous_time)
        elapsed = max(0.0, current_time - previous_time)
        current_gyro = (
            value(row, gyro_channel, previous_gyro / gyro_scale_to_deg_s)
            * gyro_scale_to_deg_s
        )
        predicted = headings[-1] + 0.5 * (previous_gyro + current_gyro) * elapsed
        headings.append(predicted)
        previous_time = current_time
        previous_gyro = current_gyro
    return headings


CANONICAL_CHANNEL_CANDIDATES = {
    "lateral_acceleration": (
        "LatAccel", "GPS LatAcc", "GPS Lat Accel",
        "Lateral Acceleration", "Lat Accel",
    ),
    "longitudinal_acceleration": (
        "LongAccel", "GPS LonAcc", "GPS Lon Accel",
        "Longitudinal Acceleration", "Lon Accel",
    ),
    "yaw_rate": (
        "YawRate", "GPS Gyro", "GPS Yaw Rate", "Yaw Rate", "Gyro",
    ),
}


def _normalized_channel_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def suggest_channel_mapping(header: list[str]) -> dict[str, str | None]:
    """Choose source channels for xCAL's canonical motion channels.

    A CSV produced from an iRacing conversion commonly contains LongAccel,
    LatAccel, and YawRate alongside Race Studio's derived GPS channels. When
    that signature is present, prefer the direct simulator channels. Native
    AiM exports normally expose only the GPS-named versions, which remain the
    fallback. The UI lets the user override every suggestion.
    """
    by_normalized = {
        _normalized_channel_name(name): name for name in header if name != "Time"
    }
    iracing_signature = all(
        _normalized_channel_name(name) in by_normalized
        for name in ("LongAccel", "LatAccel", "YawRate")
    )
    selected: dict[str, str | None] = {}
    for canonical, candidates in CANONICAL_CHANNEL_CANDIDATES.items():
        ordered = candidates if iracing_signature else (
            tuple(name for name in candidates if name.startswith("GPS "))
            + tuple(name for name in candidates if not name.startswith("GPS "))
        )
        selected[canonical] = next(
            (
                by_normalized[_normalized_channel_name(name)]
                for name in ordered
                if _normalized_channel_name(name) in by_normalized
            ),
            None,
        )
    return selected


def physical_beacons(metadata: dict[str, list[str]], duration_s: float) -> tuple[float, ...]:
    markers = []
    for text in metadata.get("Beacon Markers", ()):
        try:
            marker = float(text)
        except ValueError:
            continue
        if 0.0 < marker < duration_s - 0.001:
            markers.append(marker)
    return tuple(markers)


def ibt_channel_name(source_name: str, used: set[str]) -> str:
    # Race Studio derives its own GPS acceleration and gyro channels from the
    # resampled GPS stream. Give the original AiM samples unmistakable names so
    # they cannot be confused with those derived channels after conversion.
    raw_names = {
        "GPS LatAcc": "AiMRawLatAccel",
        "GPS LonAcc": "AiMRawLongAccel",
        "GPS Gyro": "AiMRawYawRate",
    }
    preferred = raw_names.get(source_name)
    if preferred and preferred.lower() not in used:
        used.add(preferred.lower())
        return preferred
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", source_name).strip("_") or "Channel"
    stem = f"AiM_{stem}"
    candidate = stem[:31]
    suffix = 2
    while candidate.lower() in used:
        tag = f"_{suffix}"
        candidate = f"{stem[:31 - len(tag)]}{tag}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def session_start_epoch(metadata: dict[str, list[str]]) -> int:
    date_text = (metadata.get("Date") or [""])[0]
    time_text = (metadata.get("Time") or [""])[0]
    try:
        local = datetime.strptime(
            f"{date_text} {time_text}", "%A, %B %d, %Y %I:%M %p"
        ).astimezone()
        return int(local.timestamp())
    except ValueError:
        return int(datetime.now(timezone.utc).timestamp())


def yaml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def convert(
    source: Path,
    output: Path,
    *,
    details: dict | None = None,
    channel_mapping: dict[str, str] | None = None,
) -> tuple[int, int]:
    metadata, header, units, source_rows = read_aim_csv(source)
    selected_channels = suggest_channel_mapping(header)
    for canonical, source_name in (channel_mapping or {}).items():
        if canonical not in CANONICAL_CHANNEL_CANDIDATES:
            raise ValueError(f"Unknown canonical channel: {canonical}")
        if source_name not in header:
            raise ValueError(
                f"Selected {canonical.replace('_', ' ')} channel is missing: "
                f"{source_name}")
        selected_channels[canonical] = source_name

    # A genuine iRacing disk telemetry stream is 60 Hz. Preserve the 100 Hz
    # merged CSV as the canonical data file and sample it onto iRacing's clock
    # only for this simulator-import bridge.
    tick_rate = 60
    source_times = [value(row, "Time") for row in source_rows]
    duration_text = (metadata.get("Duration") or [""])[0]
    try:
        duration_s = float(duration_text)
    except ValueError:
        duration_s = source_times[-1]
    rows: list[dict[str, str]] = []
    for index in range(math.floor(duration_s * tick_rate) + 1):
        target = index / tick_rate
        right = bisect.bisect_left(source_times, target)
        if right == 0:
            sampled = dict(source_rows[0])
        elif right == len(source_rows):
            sampled = dict(source_rows[-1])
        else:
            left = right - 1
            span = source_times[right] - source_times[left]
            fraction = ((target - source_times[left]) / span
                        if span > 0 else 0.0)
            sampled = interpolated_row(
                source_rows[left], source_rows[right], fraction, header, units)
        sampled["Time"] = f"{target:.9f}"
        rows.append(sampled)

    beacons = physical_beacons(metadata, duration_s)
    base_variables = (
        Variable("SessionTime", 5, 0, "s", "Seconds since recording start"),
        Variable("Speed", 4, 8, "m/s", "Vehicle speed"),
        Variable("RPM", 4, 12, "revs/min", "Engine speed"),
        Variable("Lap", 2, 16, "", "Lap number"),
        Variable("LapCompleted", 2, 20, "", "Completed laps"),
        Variable("LapDist", 4, 24, "m", "Distance since latest beacon"),
        Variable("LapDistPct", 4, 28, "%", "Lap distance percentage"),
        Variable("LapCurrentLapTime", 4, 32, "s", "Current lap elapsed time"),
        Variable("LapLastLapTime", 4, 36, "s", "Previous lap time"),
        Variable("LapBestLapTime", 4, 40, "s", "Best completed lap time"),
        Variable("SessionTick", 2, 44, "", "Session sample counter"),
        Variable("SessionNum", 2, 48, "", "Session number"),
        Variable("IsOnTrack", 1, 52, "", "Vehicle physics active"),
        Variable("OnPitRoad", 1, 53, "", "Vehicle is in pit lane"),
        Variable("LatAccel", 4, 56, "m/s^2", "Lateral acceleration"),
        Variable("LongAccel", 4, 60, "m/s^2", "Longitudinal acceleration"),
        Variable("YawRate", 4, 64, "rad/s", "Yaw rate"),
        Variable("Alt", 4, 68, "m", "GPS altitude"),
        Variable("LFtempCM", 4, 72, "C", "Left front tire temperature"),
        Variable("RFtempCM", 4, 76, "C", "Right front tire temperature"),
        Variable("LRtempCM", 4, 80, "C", "Left rear tire temperature"),
        Variable("RRtempCM", 4, 84, "C", "Right rear tire temperature"),
        Variable("VelocityX", 4, 88, "m/s", "World X velocity"),
        Variable("VelocityY", 4, 92, "m/s", "World Y velocity"),
        Variable("VelocityZ", 4, 96, "m/s", "World Z velocity"),
        Variable("YawNorth", 4, 100, "rad", "Yaw orientation relative to north"),
        Variable("RollRate", 4, 104, "rad/s", "Roll rate"),
        Variable("PitchRate", 4, 108, "rad/s", "Pitch rate"),
        Variable("VertAccel", 4, 112, "m/s^2", "Vertical acceleration"),
        Variable("Lat", 5, 120, "deg", "Latitude"),
        Variable("Lon", 5, 128, "deg", "Longitude"),
        Variable("IsOnTrackCar", 1, 136, "", "Player car is on track"),
        Variable("PlayerCarInPitStall", 1, 137, "", "Player car is in pit stall"),
        Variable(
            "WaterTemp", 4, 140, "C",
            "xCAL transport alias for original AiM Exhaust Temp"),
        Variable(
            "Voltage", 4, 144, "V",
            "xCAL transport alias for original AiM internal battery"),
    )
    used_names = {item.name.lower() for item in base_variables}
    source_variables: list[tuple[Variable, str]] = []
    next_offset = 152
    for source_name, unit in zip(header, units):
        if source_name == "Time":
            continue
        variable = Variable(
            name=ibt_channel_name(source_name, used_names),
            kind=4,
            offset=next_offset,
            units=unit,
            description=f"Original AiM channel: {source_name}",
        )
        source_variables.append((variable, source_name))
        next_offset += 4
    variables = base_variables + tuple(item[0] for item in source_variables)
    if details is not None:
        details.update({
            "source_channel_count": len(source_variables),
            "ibt_channel_count": len(variables),
            "source_channels": [
                {
                    "csv_name": source_name,
                    "csv_unit": variable.units,
                    "ibt_name": variable.name,
                }
                for variable, source_name in source_variables
            ],
            "canonical_channel_mapping": selected_channels,
            "conversion_note": (
                "Every numeric CSV channel is embedded as an AiM_* IBT "
                "variable. In Race Studio enable Preferences > Conversion > "
                "Settings > iRacing > All channels before converting the IBT. "
                "Race Studio recalculates channels named GPS LatAcc, GPS "
                "LonAcc, and GPS Gyro from the bridge GPS stream; compare the "
                "preserved AiMRawLatAccel, AiMRawLongAccel, and AiMRawYawRate "
                "channels when validating the original AiM samples."
            ),
            "transport_aliases": [
                {
                    "csv_name": "Exhaust Temp",
                    "ibt_name": "WaterTemp",
                    "reason": (
                        "iRacing has no exhaust-temperature variable; set the "
                        "imported channel alias/function to Exhaust Temp in "
                        "Race Studio"
                    ),
                },
                {
                    "csv_name": "Internal Batt",
                    "ibt_name": "Voltage",
                    "reason": "Recognized iRacing battery-voltage channel",
                },
            ],
        })
    frame_size = (next_offset + 7) // 8 * 8
    beacon_distances = []
    for marker in beacons:
        index = min(bisect.bisect_left(source_times, marker), len(source_rows) - 1)
        beacon_distances.append(value(source_rows[index], "Distance on GPS Speed"))
    lap_lengths = [
        right - left
        for left, right in zip(beacon_distances, beacon_distances[1:])
        if right > left
    ]
    track_length_m = statistics.median(lap_lengths) if lap_lengths else 420.0
    frames = bytearray()
    yaw_source = selected_channels["yaw_rate"] or "GPS Gyro"
    yaw_unit = channel_unit(header, units, yaw_source).replace(" ", "")
    headings_deg = bridge_headings(
        rows,
        gyro_channel=yaw_source,
        gyro_scale_to_deg_s=(180.0 / math.pi if yaw_unit in {
            "rad/s", "rad/sec"} else 1.0),
    )
    lap_start_distance = 0.0
    previous_lap = 0
    session_start_s = 120.0
    for frame_index, row in enumerate(rows):
        time_s = value(row, "Time")
        completed = sum(time_s >= marker for marker in beacons)
        lap = completed + 1
        distance = value(row, "Distance on GPS Speed")
        if completed != previous_lap:
            lap_start_distance = distance
            previous_lap = completed
        last_marker = beacons[completed - 1] if completed else 0.0
        current_lap_time = time_s - last_marker
        completed_times = ([beacons[0]] if beacons else []) + [
            beacons[index] - beacons[index - 1] for index in range(1, completed)
        ]
        last_lap_time = completed_times[-1] if completed_times else -1.0
        full_lap_times = completed_times[1:]
        best_lap_time = min(full_lap_times) if full_lap_times else -1.0
        frame = bytearray(frame_size)
        struct.pack_into("<d", frame, 0, session_start_s + time_s)
        struct.pack_into("<f", frame, 8, value(row, "GPS Speed") * 0.44704)
        struct.pack_into("<f", frame, 12, value(row, "RPM"))
        struct.pack_into("<i", frame, 16, lap)
        struct.pack_into("<i", frame, 20, completed)
        struct.pack_into("<f", frame, 24, max(0.0, distance - lap_start_distance))
        lap_distance = max(0.0, distance - lap_start_distance)
        struct.pack_into("<f", frame, 28, min(1.0, lap_distance / track_length_m))
        struct.pack_into("<f", frame, 32, current_lap_time)
        struct.pack_into("<f", frame, 36, last_lap_time)
        struct.pack_into("<f", frame, 40, best_lap_time)
        struct.pack_into("<i", frame, 44, frame_index)
        struct.pack_into("<i", frame, 48, 0)
        frame[52] = 1
        frame[53] = 0
        # iRacing and AiM use opposite lateral-axis signs. Race Studio flips
        # iRacing LatAccel during DRK conversion, so negate here to reproduce
        # the original AiM trace in the converted file.
        lateral_channel = selected_channels["lateral_acceleration"]
        longitudinal_channel = selected_channels["longitudinal_acceleration"]
        yaw_channel = selected_channels["yaw_rate"]
        struct.pack_into(
            "<f", frame, 56,
            -acceleration_mps2(
                row, lateral_channel or "GPS LatAcc", header, units))
        struct.pack_into(
            "<f", frame, 60,
            acceleration_mps2(
                row, longitudinal_channel or "GPS LonAcc", header, units))
        struct.pack_into(
            "<f", frame, 64,
            angular_rate_rad_s(
                row, yaw_channel or "GPS Gyro", header, units))
        struct.pack_into("<f", frame, 68, value(row, "GPS Altitude"))
        for offset, corner in zip((72, 76, 80, 84), ("LF", "RF", "LR", "RR")):
            fahrenheit = value(row, f"{corner} Tire Temp", math.nan)
            celsius = (
                (fahrenheit - 32.0) * 5.0 / 9.0
                if math.isfinite(fahrenheit)
                else math.nan
            )
            struct.pack_into("<f", frame, offset, celsius)
        speed_mps = value(row, "GPS Speed") * 0.44704
        # Use the gyro-validated continuous heading for iRacing's orientation
        # and world velocity. The untouched AiM heading is still exported as an
        # AiM_* source channel for auditing.
        heading_rad = math.radians(headings_deg[frame_index])
        slope_rad = math.radians(value(row, "GPS Slope"))
        horizontal_speed = speed_mps * math.cos(slope_rad)
        struct.pack_into("<f", frame, 88, horizontal_speed * math.sin(heading_rad))
        struct.pack_into("<f", frame, 92, speed_mps * math.sin(slope_rad))
        struct.pack_into("<f", frame, 96, horizontal_speed * math.cos(heading_rad))
        struct.pack_into("<f", frame, 100, heading_rad)
        struct.pack_into("<f", frame, 104, 0.0)
        struct.pack_into("<f", frame, 108, math.radians(value(row, "Pitch Rate")))
        struct.pack_into("<f", frame, 112, 0.0)
        struct.pack_into("<d", frame, 120, value(row, "GPS Latitude"))
        struct.pack_into("<d", frame, 128, value(row, "GPS Longitude"))
        frame[136] = 1
        frame[137] = 0
        struct.pack_into(
            "<f", frame, 140,
            temperature_c(row, "Exhaust Temp", header, units))
        battery = value(
            row, "Internal Batt",
            value(row, "Internal Battery", math.nan))
        struct.pack_into("<f", frame, 144, battery)
        for variable, source_name in source_variables:
            struct.pack_into(
                "<f", frame, variable.offset, value(row, source_name, math.nan)
            )
        frames.extend(frame)

    def meta(name: str, fallback: str) -> str:
        return (metadata.get(name) or [fallback])[0] or fallback

    track_name = meta("Session", "Unknown Track")
    driver_name = meta("Racer", "Unknown Driver")
    car_number = meta("Vehicle", "0")
    event_name = meta("Championship", "Imported AiM Session")
    initials = "".join(part[0] for part in driver_name.split() if part)[:3].upper() or "NA"
    yaml_text = (
        "WeekendInfo:\n"
        f"  TrackName: {yaml_string(track_name)}\n"
        f"  TrackDisplayName: {yaml_string(track_name)}\n"
        "  TrackConfigName: Imported\n"
        f"  TrackLength: {yaml_string(f'{track_length_m / 1000.0:.3f} km')}\n"
        '  TrackPitSpeedLimit: "0.0 kph"\n'
        "  TrackType: road course\n"
        "  TrackID: 0\n"
        f"  EventType: {yaml_string(event_name)}\n"
        "  Category: Road\n"
        "  SimMode: full\n"
        "  TeamRacing: 0\n"
        "  NumCarClasses: 1\n"
        "  WeekendOptions:\n"
        '    NumStarters: "1"\n'
        "SessionInfo:\n"
        "  CurrentSessionNum: 0\n"
        "  Sessions:\n"
        "    - SessionNum: 0\n"
        "      SessionLaps: unlimited\n"
        f"      SessionTime: {yaml_string(f'{duration_s:.3f} sec')}\n"
        f"      SessionType: {yaml_string(event_name)}\n"
        f"      SessionName: {yaml_string(f'{track_name} Tire Merge')}\n"
        "      ResultsPositions:\n"
        "        - Position: 1\n"
        "          ClassPosition: 0\n"
        "          CarIdx: 0\n"
        f"          Lap: {len(beacons)}\n"
        "DriverInfo:\n"
        "  DriverCarIdx: 0\n"
        f"  DriverUserID: {yaml_string(car_number)}\n"
        f"  DriverCarNumber: {yaml_string(car_number)}\n"
        "  DriverCarID: 0\n"
        "  Drivers:\n"
        "    - CarIdx: 0\n"
        f"      UserID: {yaml_string(car_number)}\n"
        f"      UserName: {yaml_string(driver_name)}\n"
        f"      AbbrevName: {yaml_string(driver_name)}\n"
        f"      Initials: {yaml_string(initials)}\n"
        f"      CarNumber: {yaml_string(car_number)}\n"
        "      CarNumberRaw: 0\n"
        "      CarID: 0\n"
        f"      CarScreenName: {yaml_string(f'Kart {car_number}')}\n"
        "      CarScreenNameShort: Kart\n"
        "      CarClassID: 0\n"
        "      CarIsPaceCar: 0\n"
        "      IRating: 0\n"
        "      LicString: Imported\n"
    ).encode("utf-8")
    var_offset = 144
    session_offset = var_offset + len(variables) * 144
    header_bytes = bytearray(144)
    struct.pack_into("<iiiiiiiiii", header_bytes, 0, 2, 1, tick_rate, 0, len(yaml_text),
                     session_offset, len(variables), var_offset, 1, frame_size)
    # irsdk_header contains a 16-byte var-buffer descriptor at offset 48.
    # RaceStudio follows buf_offset to find disk frames; leaving this zero was
    # why the first proof-of-concept converted into an unreadable DRK.
    data_offset = session_offset + len(yaml_text)
    struct.pack_into("<ii", header_bytes, 48, len(rows) - 1, data_offset)
    start_epoch = session_start_epoch(metadata)
    struct.pack_into(
        "<qddi",
        header_bytes,
        112,
        start_epoch,
        session_start_s,
        session_start_s + duration_s,
        len(beacons),
    )
    struct.pack_into("<i", header_bytes, 140, len(rows))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        bytes(header_bytes)
        + b"".join(variable_header(item) for item in variables)
        + yaml_text
        + bytes(frames)
    )
    return len(rows), len(variables)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows, variables = convert(args.source, args.output)
    print(f"Wrote {args.output} with {rows} frames and {variables} channels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
