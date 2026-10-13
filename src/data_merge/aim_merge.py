from __future__ import annotations

import argparse
import bisect
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


TIRE_CHANNELS = ("LF", "RF", "LR", "RR")
AIM_CHANNEL_NAMES = tuple(f"{channel} Tire Temp" for channel in TIRE_CHANNELS)


@dataclass(frozen=True)
class TireSample:
    time_s: float
    temperatures_f: tuple[float | None, ...]


@dataclass(frozen=True)
class TimeMap:
    scale: float
    offset_s: float
    residual_rms_s: float

    def to_aim_time(self, tire_time_s: float) -> float:
        return self.scale * tire_time_s + self.offset_s

    @property
    def drift_ppm(self) -> float:
        return (self.scale - 1.0) * 1_000_000.0


@dataclass(frozen=True)
class MergeReport:
    aim_rows: int
    populated_rows: int
    time_map: TimeMap
    output: Path


def _finite_float(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _find_aim_header(rows: Sequence[list[str]]) -> int:
    for index, row in enumerate(rows):
        if len(row) < 2 or row[0].strip().lower() != "time":
            continue
        units = next((item for item in rows[index + 1:] if item), None)
        if units and len(units) == len(row) and units[0].strip().lower() == "s":
            return index
    raise ValueError("AiM CSV channel header was not found")


def read_aim_rows(path: str | Path) -> tuple[list[list[str]], int, int]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    header_index = _find_aim_header(rows)
    units_index = next(
        (index for index in range(header_index + 1, len(rows)) if rows[index]),
        -1,
    )
    if units_index < 0 or len(rows[units_index]) != len(rows[header_index]):
        raise ValueError("AiM CSV units row was not found or has the wrong width")
    return rows, header_index, units_index


def read_tire_samples(path: str | Path) -> list[TireSample]:
    samples: list[TireSample] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        if "monotonic_timestamp_ns" not in fields:
            raise ValueError("TireLogger CSV is missing monotonic_timestamp_ns")
        required = {f"{channel}_temperature_f" for channel in TIRE_CHANNELS}
        missing = sorted(required - fields)
        if missing:
            raise ValueError(f"TireLogger CSV is missing: {', '.join(missing)}")

        first_ns: int | None = None
        for row in reader:
            try:
                timestamp_ns = int(row["monotonic_timestamp_ns"])
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid TireLogger monotonic timestamp") from exc
            if first_ns is None:
                first_ns = timestamp_ns
            samples.append(
                TireSample(
                    time_s=(timestamp_ns - first_ns) / 1e9,
                    temperatures_f=tuple(
                        _finite_float(row.get(f"{channel}_temperature_f"))
                        for channel in TIRE_CHANNELS
                    ),
                )
            )
    if len(samples) < 2:
        raise ValueError("TireLogger CSV needs at least two samples")
    if any(right.time_s <= left.time_s for left, right in zip(samples, samples[1:])):
        raise ValueError("TireLogger timestamps must increase strictly")
    return samples


def fit_time_map(anchors: Iterable[tuple[float, float]]) -> TimeMap:
    pairs = list(anchors)
    if not pairs:
        raise ValueError("at least one synchronization anchor is required")
    if any(not math.isfinite(value) for pair in pairs for value in pair):
        raise ValueError("synchronization anchors must be finite")

    if len(pairs) == 1:
        tire_s, aim_s = pairs[0]
        return TimeMap(scale=1.0, offset_s=aim_s - tire_s, residual_rms_s=0.0)

    mean_tire = sum(pair[0] for pair in pairs) / len(pairs)
    mean_aim = sum(pair[1] for pair in pairs) / len(pairs)
    denominator = sum((pair[0] - mean_tire) ** 2 for pair in pairs)
    if denominator == 0:
        raise ValueError("synchronization anchors need distinct TireLogger times")
    scale = sum(
        (tire_s - mean_tire) * (aim_s - mean_aim)
        for tire_s, aim_s in pairs
    ) / denominator
    if scale <= 0:
        raise ValueError("synchronization anchors produced a non-positive time scale")
    offset = mean_aim - scale * mean_tire
    residuals = [aim_s - (scale * tire_s + offset) for tire_s, aim_s in pairs]
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return TimeMap(scale=scale, offset_s=offset, residual_rms_s=rms)


def _interpolate(
    aim_time_s: float,
    mapped_times: Sequence[float],
    samples: Sequence[TireSample],
    max_gap_s: float,
) -> tuple[float | None, ...]:
    right_index = bisect.bisect_left(mapped_times, aim_time_s)
    if right_index < len(mapped_times) and math.isclose(
        mapped_times[right_index], aim_time_s, rel_tol=0.0, abs_tol=1e-9
    ):
        return samples[right_index].temperatures_f
    if right_index == 0 or right_index == len(mapped_times):
        return (None,) * len(TIRE_CHANNELS)

    left_index = right_index - 1
    left_time = mapped_times[left_index]
    right_time = mapped_times[right_index]
    if right_time - left_time > max_gap_s:
        return (None,) * len(TIRE_CHANNELS)
    fraction = (aim_time_s - left_time) / (right_time - left_time)
    result: list[float | None] = []
    for left_value, right_value in zip(
        samples[left_index].temperatures_f,
        samples[right_index].temperatures_f,
    ):
        if left_value is None or right_value is None:
            result.append(None)
        else:
            result.append(left_value + fraction * (right_value - left_value))
    return tuple(result)


def merge_aim_tires(
    aim_path: str | Path,
    tire_path: str | Path,
    output_path: str | Path,
    anchors: Iterable[tuple[float, float]],
    *,
    max_gap_s: float = 0.5,
) -> MergeReport:
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    rows, header_index, units_index = read_aim_rows(aim_path)
    samples = read_tire_samples(tire_path)
    time_map = fit_time_map(anchors)
    mapped_times = [time_map.to_aim_time(sample.time_s) for sample in samples]

    rows[header_index].extend(AIM_CHANNEL_NAMES)
    rows[units_index].extend(("F",) * len(TIRE_CHANNELS))

    populated = 0
    aim_rows = 0
    for index in range(units_index + 1, len(rows)):
        row = rows[index]
        if not row:
            continue
        aim_time = _finite_float(row[0])
        if aim_time is None:
            continue
        aim_rows += 1
        values = _interpolate(aim_time, mapped_times, samples, max_gap_s)
        row.extend("" if value is None else f"{value:.3f}" for value in values)
        if any(value is not None for value in values):
            populated += 1

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
    return MergeReport(aim_rows, populated, time_map, output)


def _parse_anchor(value: str) -> tuple[float, float]:
    try:
        tire_text, aim_text = value.split(":", 1)
        return float(tire_text), float(aim_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "anchor must be TIRE_SECONDS:AIM_SECONDS"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge TireLogger temperatures into a Race Studio AiM CSV."
    )
    parser.add_argument("--aim", required=True, help="Race Studio AiM CSV export")
    parser.add_argument("--tires", required=True, help="matching TireLogger CSV")
    parser.add_argument("--output", required=True, help="new merged AiM CSV")
    synchronization = parser.add_mutually_exclusive_group(required=True)
    synchronization.add_argument(
        "--anchor",
        action="append",
        type=_parse_anchor,
        metavar="TIRE_S:AIM_S",
        help="matching event times; use two or more to correct clock drift",
    )
    synchronization.add_argument(
        "--rpm-sync",
        action="store_true",
        help="automatically estimate offset and drift from matching RPM channels",
    )
    parser.add_argument(
        "--rpm-field",
        default="rpm",
        help="TireLogger RPM column used with --rpm-sync (default: rpm)",
    )
    parser.add_argument(
        "--rpm-tolerance-rpm", type=float, default=500.0,
        help="maximum absolute RPM difference counted as a match (default: 500)",
    )
    parser.add_argument(
        "--min-rpm-match-fraction", type=float, default=0.60,
        help="required fraction of compared RPM samples within tolerance (default: 0.60)",
    )
    parser.add_argument(
        "--max-gap-s",
        type=float,
        default=0.5,
        help="largest TireLogger sample gap that may be interpolated (default: 0.5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rpm_report = None
    anchors = args.anchor
    if args.rpm_sync:
        from .rpm_sync import estimate_rpm_time_map, read_aim_rpm, read_tire_rpm

        rpm_report = estimate_rpm_time_map(
            read_aim_rpm(args.aim), read_tire_rpm(args.tires, args.rpm_field),
            rpm_tolerance_rpm=args.rpm_tolerance_rpm,
            min_match_fraction=args.min_rpm_match_fraction,
        )
        anchors = rpm_report.window_anchors
    report = merge_aim_tires(
        args.aim,
        args.tires,
        args.output,
        anchors,
        max_gap_s=args.max_gap_s,
    )
    print(f"Wrote {report.output}")
    print(f"AiM rows: {report.aim_rows}; rows with tire data: {report.populated_rows}")
    print(
        f"Time mapping: aim_s = {report.time_map.scale:.12f} * tire_s "
        f"+ {report.time_map.offset_s:.6f}"
    )
    print(
        f"Clock drift: {report.time_map.drift_ppm:.1f} ppm; "
        f"anchor RMS error: {report.time_map.residual_rms_s:.6f} s"
    )
    if rpm_report is not None:
        print(
            f"RPM sync: {len(rpm_report.window_anchors)} windows; "
            f"correlation {rpm_report.coarse_score:.3f}; "
            f"ambiguity margin {rpm_report.ambiguity_margin:.3f}"
        )
        print(
            f"RPM agreement: {rpm_report.match_fraction:.1%} "
            f"within {rpm_report.rpm_tolerance_rpm:g} RPM"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
