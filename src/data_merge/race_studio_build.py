from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .aim_ibt import convert
from .aim_merge import merge_aim_tires
from .rpm_sync import estimate_rpm_time_map, read_aim_rpm, read_tire_rpm


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RaceStudioBuildReport:
    merged_csv: Path
    ibt: Path
    manifest: Path
    synchronization: str
    scale: float
    offset_s: float
    drift_ppm: float
    residual_rms_s: float
    ibt_frames: int
    ibt_channels: int


def build_race_studio_package(
    aim_csv: str | Path,
    tire_csv: str | Path,
    output_dir: str | Path,
    *,
    anchors: Sequence[tuple[float, float]] | None = None,
    rpm_sync: bool = False,
    rpm_field: str = "rpm",
    rpm_tolerance_rpm: float = 500.0,
    min_rpm_match_fraction: float = 0.60,
    channel_mapping: dict[str, str] | None = None,
    name: str = "race_studio_tire_merge",
) -> RaceStudioBuildReport:
    if rpm_sync == bool(anchors):
        raise ValueError("select exactly one synchronization source: RPM or anchors")
    aim_path = Path(aim_csv)
    tire_path = Path(tire_csv)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    rpm_report = None
    if rpm_sync:
        rpm_report = estimate_rpm_time_map(
            read_aim_rpm(aim_path), read_tire_rpm(tire_path, rpm_field),
            rpm_tolerance_rpm=rpm_tolerance_rpm,
            min_match_fraction=min_rpm_match_fraction,
        )
        selected_anchors = rpm_report.window_anchors
        synchronization = "rpm_correlation"
    else:
        selected_anchors = tuple(anchors or ())
        synchronization = "manual_anchors"

    merged_path = destination / f"{name}.merged.csv"
    ibt_path = destination / f"{name}.ibt"
    manifest_path = destination / f"{name}.manifest.json"
    merge_report = merge_aim_tires(
        aim_path, tire_path, merged_path, selected_anchors
    )
    channel_export = {}
    frames, channels = convert(
        merged_path,
        ibt_path,
        details=channel_export,
        channel_mapping=channel_mapping,
    )
    report = RaceStudioBuildReport(
        merged_csv=merged_path,
        ibt=ibt_path,
        manifest=manifest_path,
        synchronization=synchronization,
        scale=merge_report.time_map.scale,
        offset_s=merge_report.time_map.offset_s,
        drift_ppm=merge_report.time_map.drift_ppm,
        residual_rms_s=merge_report.time_map.residual_rms_s,
        ibt_frames=frames,
        ibt_channels=channels,
    )
    payload = {
        **{key: str(value) if isinstance(value, Path) else value for key, value in asdict(report).items()},
        "aim_csv": aim_path.name,
        "tire_csv": tire_path.name,
        "input_sha256": {
            "aim_csv": _sha256(aim_path),
            "tire_csv": _sha256(tire_path),
        },
        "anchors": [list(pair) for pair in selected_anchors],
        "rpm_confidence": None if rpm_report is None else {
            "coarse_correlation": rpm_report.coarse_score,
            "ambiguity_margin": rpm_report.ambiguity_margin,
            "window_count": len(rpm_report.window_anchors),
            "rpm_tolerance_rpm": rpm_report.rpm_tolerance_rpm,
            "match_fraction": rpm_report.match_fraction,
            "matched_samples": rpm_report.matched_samples,
            "compared_samples": rpm_report.compared_samples,
        },
        "channel_export": channel_export,
        "drk_status": (
            "First enable Preferences > Conversion > Settings > iRacing > "
            "All channels. Then convert the IBT with Race Studio Conversion "
            "of Files and select AiM DRK [RS2Analysis data]."
        ),
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report


def _anchor(text: str) -> tuple[float, float]:
    try:
        tire_s, aim_s = text.split(":", 1)
        return float(tire_s), float(aim_s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("anchor must be TIRE_SECONDS:AIM_SECONDS") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a synchronized AiM CSV and Race Studio-compatible IBT."
    )
    parser.add_argument("--aim", required=True, type=Path)
    parser.add_argument("--tires", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--name", default="race_studio_tire_merge")
    synchronization = parser.add_mutually_exclusive_group(required=True)
    synchronization.add_argument("--rpm-sync", action="store_true")
    synchronization.add_argument("--anchor", action="append", type=_anchor)
    parser.add_argument("--rpm-field", default="rpm")
    parser.add_argument("--rpm-tolerance-rpm", type=float, default=500.0)
    parser.add_argument("--min-rpm-match-fraction", type=float, default=0.60)
    args = parser.parse_args(argv)
    report = build_race_studio_package(
        args.aim,
        args.tires,
        args.output_dir,
        anchors=args.anchor,
        rpm_sync=args.rpm_sync,
        rpm_field=args.rpm_field,
        rpm_tolerance_rpm=args.rpm_tolerance_rpm,
        min_rpm_match_fraction=args.min_rpm_match_fraction,
        name=args.name,
    )
    print(f"Merged CSV: {report.merged_csv}")
    print(f"IBT bridge: {report.ibt}")
    print(f"Manifest: {report.manifest}")
    print(
        f"Sync: {report.synchronization}; drift {report.drift_ppm:.1f} ppm; "
        f"RMS {report.residual_rms_s:.4f} s"
    )
    print("Final step: convert the IBT to AiM DRK in Race Studio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
