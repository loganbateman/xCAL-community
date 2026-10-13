import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from data_merge.race_studio_build import build_race_studio_package


class RaceStudioBuildTests(unittest.TestCase):
    def test_builds_merged_csv_ibt_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aim = root / "aim.csv"
            tires = root / "tires.csv"
            output = root / "output"
            with aim.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows([
                    ["Format", "AiM CSV File"],
                    ["Session", "Test Track"],
                    ["Duration", "1.0"],
                    [],
                    ["Time", "RPM", "GPS Speed", "GPS Latitude", "GPS Longitude"],
                    ["s", "rpm", "mph", "deg", "deg"],
                    [],
                    ["0.0", "5000", "10", "40", "-86"],
                    ["0.5", "6000", "20", "40.001", "-86.001"],
                    ["1.0", "5500", "15", "40.002", "-86.002"],
                ])
            tires.write_text(
                "monotonic_timestamp_ns,LF_temperature_f,RF_temperature_f,LR_temperature_f,RR_temperature_f\n"
                "1000000000,150,151,152,153\n"
                "1500000000,160,161,162,163\n"
                "2000000000,170,181,182,183\n",
                encoding="utf-8",
            )

            report = build_race_studio_package(
                aim, tires, output, anchors=[(0.0, 0.0)], name="test"
            )

            self.assertTrue(report.merged_csv.is_file())
            self.assertTrue(report.ibt.is_file())
            self.assertTrue(report.manifest.is_file())
            manifest = json.loads(report.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["synchronization"], "manual_anchors")
            self.assertIn("Race Studio", manifest["drk_status"])
            self.assertEqual(manifest["aim_csv"], "aim.csv")
            self.assertEqual(len(manifest["input_sha256"]["aim_csv"]), 64)
            self.assertEqual(len(manifest["input_sha256"]["tire_csv"]), 64)
            channel_export = manifest["channel_export"]
            self.assertEqual(channel_export["source_channel_count"], 8)
            self.assertEqual(
                channel_export["ibt_channel_count"], report.ibt_channels)
            self.assertIn("All channels", channel_export["conversion_note"])
            self.assertIn("All channels", manifest["drk_status"])

    def test_end_to_end_rpm_curve_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aim = root / "aim.csv"
            tires = root / "tires.csv"
            output = root / "output"
            offset = 3.4

            def rpm(t):
                return 7600 + 1700 * math.sin(t * .23) + 500 * math.sin(t * .061 + .4) \
                    + (1100 if 38 < t < 42 else 0)

            with aim.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows([
                    ["Format", "AiM CSV File"], ["Duration", "100"], [],
                    ["Time", "RPM", "GPS Speed", "GPS Latitude", "GPS Longitude"],
                    ["s", "rpm", "mph", "deg", "deg"], [],
                ])
                for i in range(2001):
                    t = i / 20
                    writer.writerow([f"{t:.3f}", f"{rpm(t):.3f}", "30", "40", "-86"])
            with tires.open("w", newline="", encoding="utf-8") as handle:
                fields = ["monotonic_timestamp_ns", "rpm", "LF_temperature_f",
                          "RF_temperature_f", "LR_temperature_f", "RR_temperature_f"]
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for i in range(1537):
                    t = i / 16
                    writer.writerow({
                        "monotonic_timestamp_ns": 8_000_000_000 + round(t * 1e9),
                        "rpm": f"{rpm(t + offset) * 1.015:.3f}",
                        "LF_temperature_f": "170", "RF_temperature_f": "171",
                        "LR_temperature_f": "172", "RR_temperature_f": "173",
                    })

            report = build_race_studio_package(
                aim, tires, output, rpm_sync=True, rpm_tolerance_rpm=500,
                min_rpm_match_fraction=.80, name="rpm_synced",
            )
            manifest = json.loads(report.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["synchronization"], "rpm_correlation")
            self.assertAlmostEqual(report.offset_s, offset, delta=.1)
            self.assertGreater(manifest["rpm_confidence"]["match_fraction"], .9)
            self.assertTrue(report.ibt.is_file())


if __name__ == "__main__":
    unittest.main()
