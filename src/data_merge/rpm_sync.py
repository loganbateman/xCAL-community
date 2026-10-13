from __future__ import annotations

import argparse
import bisect
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .aim_merge import TimeMap, fit_time_map, read_aim_rows


@dataclass(frozen=True)
class RpmPoint:
    time_s: float
    rpm: float


@dataclass(frozen=True)
class RpmSyncReport:
    time_map: TimeMap
    coarse_offset_s: float
    coarse_score: float
    ambiguity_margin: float
    window_anchors: tuple[tuple[float, float], ...]
    rpm_tolerance_rpm: float
    match_fraction: float
    matched_samples: int
    compared_samples: int


@dataclass(frozen=True)
class _OffsetScore:
    correlation: float
    match_fraction: float
    matched: int
    compared: int

    @property
    def objective(self) -> float:
        # Shape identifies the event; value agreement rejects a similar-looking
        # curve with the wrong RPM scale (for example, wrong pulses/revolution).
        return 0.6 * self.correlation + 0.4 * self.match_fraction


def _finite(value: str | None) -> float | None:
    try:
        result = float(value or "")
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def read_aim_rpm(path: str | Path) -> list[RpmPoint]:
    rows, header_index, units_index = read_aim_rows(path)
    header = rows[header_index]
    try:
        time_index = header.index("Time")
        rpm_index = header.index("RPM")
    except ValueError as exc:
        raise ValueError("AiM CSV must contain Time and RPM channels") from exc
    points = []
    for row in rows[units_index + 1:]:
        if len(row) <= max(time_index, rpm_index):
            continue
        time_s = _finite(row[time_index])
        rpm = _finite(row[rpm_index])
        if time_s is not None and rpm is not None:
            points.append(RpmPoint(time_s, rpm))
    return _validated(points, "AiM")


def read_tire_rpm(path: str | Path, rpm_field: str = "rpm") -> list[RpmPoint]:
    points = []
    first_ns: int | None = None
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        if "monotonic_timestamp_ns" not in fields or rpm_field not in fields:
            raise ValueError(
                f"TireLogger CSV must contain monotonic_timestamp_ns and {rpm_field}"
            )
        for row in reader:
            rpm = _finite(row.get(rpm_field))
            try:
                timestamp_ns = int(row["monotonic_timestamp_ns"])
            except (TypeError, ValueError):
                continue
            if first_ns is None:
                first_ns = timestamp_ns
            if rpm is not None:
                points.append(RpmPoint((timestamp_ns - first_ns) / 1e9, rpm))
    return _validated(points, "TireLogger")


def _validated(points: Sequence[RpmPoint], label: str) -> list[RpmPoint]:
    result = list(points)
    if len(result) < 20:
        raise ValueError(f"{label} RPM requires at least 20 valid samples")
    if any(right.time_s <= left.time_s for left, right in zip(result, result[1:])):
        raise ValueError(f"{label} RPM timestamps must increase strictly")
    return result


def _sample(
    points: Sequence[RpmPoint], times: Sequence[float], target: float,
    max_gap_s: float = 0.5,
) -> float | None:
    right = bisect.bisect_left(times, target)
    if right == 0 or right == len(points):
        return None
    left = right - 1
    span = times[right] - times[left]
    if span <= 0 or span > max_gap_s:
        return None
    fraction = (target - times[left]) / span
    return points[left].rpm + fraction * (points[right].rpm - points[left].rpm)


def _score_offset(
    aim: Sequence[RpmPoint],
    tire: Sequence[RpmPoint],
    aim_times: Sequence[float],
    tire_times: Sequence[float],
    offset_s: float,
    start_tire_s: float | None = None,
    end_tire_s: float | None = None,
    max_samples: int = 300,
    rpm_tolerance_rpm: float = 500.0,
    time_scale: float = 1.0,
) -> _OffsetScore:
    start = max(tire_times[0], (aim_times[0] - offset_s) / time_scale)
    end = min(tire_times[-1], (aim_times[-1] - offset_s) / time_scale)
    if start_tire_s is not None:
        start = max(start, start_tire_s)
    if end_tire_s is not None:
        end = min(end, end_tire_s)
    if end - start < 5.0:
        return _OffsetScore(-1.0, 0.0, 0, 0)
    count = max(20, min(max_samples, math.floor((end - start) * 5.0)))
    pairs = []
    for index in range(count):
        tire_time = start + (end - start) * index / (count - 1)
        tire_value = _sample(tire, tire_times, tire_time)
        aim_value = _sample(aim, aim_times, time_scale * tire_time + offset_s)
        if tire_value is not None and aim_value is not None:
            pairs.append((tire_value, aim_value))
    if len(pairs) < 20:
        return _OffsetScore(-1.0, 0.0, 0, 0)
    mean_tire = sum(pair[0] for pair in pairs) / len(pairs)
    mean_aim = sum(pair[1] for pair in pairs) / len(pairs)
    numerator = sum((a - mean_tire) * (b - mean_aim) for a, b in pairs)
    tire_energy = sum((a - mean_tire) ** 2 for a, _ in pairs)
    aim_energy = sum((b - mean_aim) ** 2 for _, b in pairs)
    if tire_energy < 1.0 or aim_energy < 1.0:
        return _OffsetScore(-1.0, 0.0, 0, len(pairs))
    correlation = numerator / math.sqrt(tire_energy * aim_energy)
    matched = sum(
        abs(tire_rpm - aim_rpm) <= rpm_tolerance_rpm
        for tire_rpm, aim_rpm in pairs
    )
    return _OffsetScore(correlation, matched / len(pairs), matched, len(pairs))


def estimate_rpm_time_map(
    aim_points: Sequence[RpmPoint],
    tire_points: Sequence[RpmPoint],
    *,
    min_overlap_s: float = 30.0,
    rpm_tolerance_rpm: float = 500.0,
    min_match_fraction: float = 0.60,
) -> RpmSyncReport:
    if not 0 < rpm_tolerance_rpm <= 5000:
        raise ValueError("rpm_tolerance_rpm must be above 0 and no more than 5000")
    if not 0 < min_match_fraction <= 1:
        raise ValueError("min_match_fraction must be above 0 and no more than 1")
    aim = _validated(aim_points, "AiM")
    tire = _validated(tire_points, "TireLogger")
    aim_times = [point.time_s for point in aim]
    tire_times = [point.time_s for point in tire]
    low = aim_times[0] - tire_times[-1] + min_overlap_s
    high = aim_times[-1] - tire_times[0] - min_overlap_s
    if high <= low:
        raise ValueError("RPM recordings do not have enough possible overlap")

    coarse: list[tuple[float, float, _OffsetScore]] = []
    step = 0.25
    count = math.floor((high - low) / step) + 1
    for index in range(count):
        offset = low + index * step
        metrics = _score_offset(
            aim, tire, aim_times, tire_times, offset,
            rpm_tolerance_rpm=rpm_tolerance_rpm,
        )
        coarse.append((metrics.objective, offset, metrics))
    coarse.sort(key=lambda item: item[0], reverse=True)
    best_objective, coarse_offset, best_metrics = coarse[0]
    distinct_scores = [
        objective for objective, offset, _ in coarse
        if abs(offset - coarse_offset) >= 1.0
    ]
    ambiguity = best_objective - max(distinct_scores, default=-1.0)
    if best_metrics.correlation < 0.55:
        raise ValueError(
            f"RPM curve-shape confidence is too low ({best_metrics.correlation:.3f})"
        )
    if best_metrics.match_fraction < min_match_fraction:
        raise ValueError(
            f"only {best_metrics.match_fraction:.1%} of RPM samples agree within "
            f"{rpm_tolerance_rpm:g} RPM (minimum {min_match_fraction:.1%})"
        )

    overlap_start = max(tire_times[0], aim_times[0] - coarse_offset)
    overlap_end = min(tire_times[-1], aim_times[-1] - coarse_offset)
    window_s = min(60.0, max(20.0, (overlap_end - overlap_start) / 4.0))
    anchors = []
    center = overlap_start + window_s / 2.0
    while center <= overlap_end - window_s / 2.0 + 1e-9:
        candidates = []
        for index in range(81):
            offset = coarse_offset - 1.0 + index * 0.025
            metrics = _score_offset(
                aim,
                tire,
                aim_times,
                tire_times,
                offset,
                center - window_s / 2.0,
                center + window_s / 2.0,
                max_samples=400,
                rpm_tolerance_rpm=rpm_tolerance_rpm,
            )
            candidates.append((metrics.objective, offset, metrics))
        _, offset, metrics = max(candidates, key=lambda item: item[0])
        if metrics.correlation >= 0.60 and metrics.match_fraction >= min_match_fraction:
            anchors.append((center, center + offset))
        center += window_s
    if len(anchors) < 2:
        raise ValueError("RPM trace did not provide two trustworthy synchronization windows")
    mapping = fit_time_map(anchors)
    final_metrics = _score_offset(
        aim, tire, aim_times, tire_times, mapping.offset_s,
        rpm_tolerance_rpm=rpm_tolerance_rpm,
        time_scale=mapping.scale,
    )
    if final_metrics.match_fraction < min_match_fraction:
        raise ValueError(
            f"final RPM agreement is only {final_metrics.match_fraction:.1%} "
            f"within {rpm_tolerance_rpm:g} RPM "
            f"(minimum {min_match_fraction:.1%})"
        )
    return RpmSyncReport(
        mapping, coarse_offset, best_metrics.correlation, ambiguity, tuple(anchors),
        rpm_tolerance_rpm, final_metrics.match_fraction,
        final_metrics.matched, final_metrics.compared,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate TireLogger-to-AiM offset and clock drift from matching RPM traces."
    )
    parser.add_argument("--aim", required=True, type=Path)
    parser.add_argument("--tires", required=True, type=Path)
    parser.add_argument("--rpm-field", default="rpm")
    parser.add_argument("--rpm-tolerance-rpm", type=float, default=500.0)
    parser.add_argument("--min-match-fraction", type=float, default=0.60)
    args = parser.parse_args(argv)
    report = estimate_rpm_time_map(
        read_aim_rpm(args.aim), read_tire_rpm(args.tires, args.rpm_field),
        rpm_tolerance_rpm=args.rpm_tolerance_rpm,
        min_match_fraction=args.min_match_fraction,
    )
    print(
        f"aim_s = {report.time_map.scale:.12f} * tire_s "
        f"+ {report.time_map.offset_s:.6f}"
    )
    print(
        f"drift={report.time_map.drift_ppm:.1f} ppm; "
        f"RMS={report.time_map.residual_rms_s:.4f} s; "
        f"coarse correlation={report.coarse_score:.3f}; "
        f"ambiguity margin={report.ambiguity_margin:.3f}"
    )
    print(
        f"RPM agreement={report.match_fraction:.1%} "
        f"({report.matched_samples}/{report.compared_samples} within "
        f"{report.rpm_tolerance_rpm:g} RPM)"
    )
    for tire_s, aim_s in report.window_anchors:
        print(f"--anchor {tire_s:.6f}:{aim_s:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
