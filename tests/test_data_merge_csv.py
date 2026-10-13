import csv
import tempfile
import unittest
from pathlib import Path

from data_merge.aim_merge import fit_time_map, merge_aim_tires


class AimMergeTests(unittest.TestCase):
    def test_time_map_corrects_offset_and_drift(self):
        mapping = fit_time_map([(10.0, 12.0), (110.0, 112.01), (210.0, 212.02)])

        self.assertAlmostEqual(mapping.scale, 1.0001)
        self.assertAlmostEqual(mapping.offset_s, 1.999)
        self.assertAlmostEqual(mapping.residual_rms_s, 0.0, places=10)

    def test_merges_tires_onto_100_hz_aim_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aim = root / "aim.csv"
            tires = root / "tires.csv"
            output = root / "merged.csv"
            aim.write_text(
                '\n'.join(
                    [
                        '"Format","AiM CSV File"',
                        '"Sample Rate","100"',
                        '',
                        '"Time","RPM"',
                        '"s","rpm"',
                        '',
                        '"0.000","1000"',
                        '"0.010","1010"',
                        '"0.020","1020"',
                        '"0.030","1030"',
                        '"0.040","1040"',
                    ]
                ) + '\n',
                encoding="utf-8",
            )
            tires.write_text(
                '\n'.join(
                    [
                        "monotonic_timestamp_ns,LF_temperature_f,RF_temperature_f,LR_temperature_f,RR_temperature_f",
                        "1000000000,100,110,120,130",
                        "1100000000,110,120,130,140",
                    ]
                ) + '\n',
                encoding="utf-8",
            )

            report = merge_aim_tires(aim, tires, output, [(0.0, 0.0)])

            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[3][-4:], [
                "LF Tire Temp", "RF Tire Temp", "LR Tire Temp", "RR Tire Temp"
            ])
            self.assertEqual(rows[4][-4:], ["F", "F", "F", "F"])
            self.assertEqual(rows[7][-4:], ["101.000", "111.000", "121.000", "131.000"])
            self.assertEqual(report.aim_rows, 5)
            self.assertEqual(report.populated_rows, 5)

    def test_does_not_bridge_large_or_invalid_tire_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aim = root / "aim.csv"
            tires = root / "tires.csv"
            output = root / "merged.csv"
            aim.write_text(
                '"Format","AiM CSV File"\n\n"Time","RPM"\n"s","rpm"\n\n"0.500","1"\n',
                encoding="utf-8",
            )
            tires.write_text(
                "monotonic_timestamp_ns,LF_temperature_f,RF_temperature_f,LR_temperature_f,RR_temperature_f\n"
                "1000000000,100,110,120,130\n"
                "2000000000,110,,130,140\n",
                encoding="utf-8",
            )

            merge_aim_tires(aim, tires, output, [(0.0, 0.0)], max_gap_s=0.25)

            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[-1][-4:], ["", "", "", ""])


if __name__ == "__main__":
    unittest.main()
