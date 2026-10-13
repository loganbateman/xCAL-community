import csv
import math
import struct
import tempfile
import unittest
from pathlib import Path

from data_merge.aim_ibt import (
    bridge_headings, convert, interpolated_row, physical_beacons,
    read_aim_channel_schema, suggest_channel_mapping)


class AimIbtTests(unittest.TestCase):
    def test_channel_mapping_prefers_native_gps_names_for_aim_export(self):
        mapping = suggest_channel_mapping([
            "Time", "GPS LonAcc", "GPS LatAcc", "GPS Gyro",
        ])

        self.assertEqual(mapping["longitudinal_acceleration"], "GPS LonAcc")
        self.assertEqual(mapping["lateral_acceleration"], "GPS LatAcc")
        self.assertEqual(mapping["yaw_rate"], "GPS Gyro")

    def test_channel_mapping_prefers_direct_channels_for_iracing_export(self):
        mapping = suggest_channel_mapping([
            "Time", "GPS LonAcc", "LongAccel", "GPS LatAcc", "LatAccel",
            "GPS Gyro", "YawRate",
        ])

        self.assertEqual(mapping["longitudinal_acceleration"], "LongAccel")
        self.assertEqual(mapping["lateral_acceleration"], "LatAccel")
        self.assertEqual(mapping["yaw_rate"], "YawRate")

    def test_bridge_heading_rejects_quarter_turn_export_glitches(self):
        rows = [
            {"Time": "0.00", "GPS Heading": "0.0", "GPS Gyro": "10.0"},
            {"Time": "0.01", "GPS Heading": "90.0", "GPS Gyro": "10.0"},
            {"Time": "0.02", "GPS Heading": "-179.8", "GPS Gyro": "10.0"},
            {"Time": "0.03", "GPS Heading": "-89.7", "GPS Gyro": "10.0"},
            {"Time": "0.04", "GPS Heading": "0.4", "GPS Gyro": "10.0"},
        ]

        headings = bridge_headings(rows)

        self.assertEqual(len(headings), len(rows))
        self.assertEqual(headings, [0.0, 0.1, 0.2, 0.3, 0.4])

    def test_interpolation_wraps_gps_heading_across_north(self):
        sampled = interpolated_row(
            {"Time": "0", "GPS Heading": "359"},
            {"Time": "1", "GPS Heading": "1"},
            0.5,
            ["Time", "GPS Heading"],
            ["s", "deg"],
        )

        self.assertAlmostEqual(float(sampled["GPS Heading"]), 0.0)

    def test_uses_session_metadata_and_preserves_source_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            output = root / "output.ibt"
            rows = [
                ["Format", "AiM CSV File"],
                ["Session", "Test Track"],
                ["Vehicle", "11"],
                ["Racer", "Alex Driver"],
                ["Championship", "Race"],
                ["Date", "Saturday, April 25, 2026"],
                ["Time", "2:36 PM"],
                ["Duration", "1.000"],
                ["Beacon Markers", "0.500", "1.000"],
                [],
                ["Time", "GPS Speed", "GPS Latitude", "GPS Longitude", "RPM", "LF Tire Temp", "Custom Channel"],
                ["s", "mph", "deg", "deg", "rpm", "F", "psi"],
                [],
                ["0.000", "10", "40.0", "-86.0", "5000", "", "12.5"],
                ["1.000", "20", "40.1", "-86.1", "6000", "200", "13.5"],
            ]
            with source.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(rows)

            details = {}
            frame_count, variable_count = convert(
                source, output, details=details)
            data = output.read_bytes()
            session_length, session_offset, header_variable_count = struct.unpack_from(
                "<iii", data, 16
            )
            _, data_offset = struct.unpack_from("<ii", data, 48)

            self.assertEqual(frame_count, 61)
            self.assertEqual(variable_count, header_variable_count)
            self.assertIn(b"AiM_Custom_Channel", data[:session_offset])
            self.assertIn(b"Test Track", data[session_offset:session_offset + session_length])
            self.assertIn(b"Alex Driver", data[session_offset:session_offset + session_length])
            self.assertTrue(math.isnan(struct.unpack_from("<f", data, data_offset + 72)[0]))
            self.assertEqual(details["source_channel_count"], 6)
            self.assertEqual(details["ibt_channel_count"], variable_count)
            self.assertIn({
                "csv_name": "Custom Channel",
                "csv_unit": "psi",
                "ibt_name": "AiM_Custom_Channel",
            }, details["source_channels"])
            buffer_length = struct.unpack_from("<i", data, 36)[0]
            custom = next(
                item for item in details["source_channels"]
                if item["csv_name"] == "Custom Channel")
            custom_variable = next(
                index for index in range(variable_count)
                if data[144 + index * 144 + 16:144 + index * 144 + 48]
                .split(b"\0", 1)[0].decode() == custom["ibt_name"])
            custom_offset = struct.unpack_from(
                "<i", data, 144 + custom_variable * 144 + 4)[0]
            midpoint = struct.unpack_from(
                "<f", data, data_offset + 30 * buffer_length + custom_offset)[0]
            self.assertAlmostEqual(midpoint, 13.0, places=5)

    def test_preserves_raw_accelerations_and_corrects_iracing_lateral_sign(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            output = root / "output.ibt"
            with source.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows([
                    ["Format", "AiM CSV File"],
                    ["Duration", "0.1"],
                    [],
                    ["Time", "GPS LatAcc", "GPS LonAcc", "GPS Gyro",
                     "Exhaust Temp", "Internal Batt"],
                    ["s", "g", "g", "deg/s", "F", "V"],
                    [],
                    ["0.0", "1.0", "2.0", "3.0", "212", "4.1"],
                    ["0.1", "2.0", "4.0", "6.0", "392", "4.2"],
                ])

            details = {}
            _frames, count = convert(source, output, details=details)
            data = output.read_bytes()
            _, data_offset = struct.unpack_from("<ii", data, 48)
            variables = {}
            for index in range(count):
                base = 144 + index * 144
                name = data[base + 16:base + 48].split(b"\0", 1)[0].decode()
                variables[name] = struct.unpack_from("<i", data, base + 4)[0]

            self.assertTrue({
                "AiMRawLatAccel", "AiMRawLongAccel", "AiMRawYawRate",
                "WaterTemp", "Voltage",
            }.issubset(variables))
            self.assertAlmostEqual(
                struct.unpack_from(
                    "<f", data, data_offset + variables["WaterTemp"])[0],
                100.0,
            )
            self.assertAlmostEqual(
                struct.unpack_from(
                    "<f", data, data_offset + variables["Voltage"])[0],
                4.1,
                places=5,
            )
            self.assertEqual(
                details["transport_aliases"][0]["ibt_name"], "WaterTemp")
            source_mapping = {
                item["csv_name"]: item["ibt_name"]
                for item in details["source_channels"]}
            self.assertEqual(source_mapping["GPS LatAcc"], "AiMRawLatAccel")
            self.assertEqual(source_mapping["GPS LonAcc"], "AiMRawLongAccel")
            self.assertEqual(source_mapping["GPS Gyro"], "AiMRawYawRate")
            self.assertAlmostEqual(
                struct.unpack_from(
                    "<f", data, data_offset + variables["LatAccel"])[0],
                -9.80665,
                places=4,
            )
            self.assertAlmostEqual(
                struct.unpack_from(
                    "<f", data, data_offset + variables["LongAccel"])[0],
                2.0 * 9.80665,
                places=4,
            )
            self.assertAlmostEqual(
                struct.unpack_from(
                    "<f", data, data_offset + variables["AiMRawLatAccel"])[0],
                1.0,
                places=4,
            )

    def test_explicit_channel_mapping_controls_motion_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            output = root / "output.ibt"
            with source.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows([
                    ["Format", "AiM CSV File"],
                    ["Duration", "0.1"],
                    [],
                    ["Time", "GPS LatAcc", "LatAccel", "GPS LonAcc",
                     "LongAccel", "GPS Gyro", "YawRate"],
                    ["s", "g", "m/s^2", "g", "m/s^2", "deg/s", "rad/s"],
                    [],
                    ["0.0", "1", "2", "3", "4", "5", "0.5"],
                    ["0.1", "1", "2", "3", "4", "5", "0.5"],
                ])

            details = {}
            convert(source, output, details=details, channel_mapping={
                "lateral_acceleration": "LatAccel",
                "longitudinal_acceleration": "LongAccel",
                "yaw_rate": "YawRate",
            })
            data = output.read_bytes()
            _, data_offset = struct.unpack_from("<ii", data, 48)

            self.assertAlmostEqual(
                struct.unpack_from("<f", data, data_offset + 56)[0],
                -2.0,
                places=5,
            )
            self.assertAlmostEqual(
                struct.unpack_from("<f", data, data_offset + 60)[0],
                4.0,
                places=5,
            )
            self.assertAlmostEqual(
                struct.unpack_from("<f", data, data_offset + 64)[0],
                0.5,
                places=5,
            )
            self.assertEqual(details["canonical_channel_mapping"], {
                "lateral_acceleration": "LatAccel",
                "longitudinal_acceleration": "LongAccel",
                "yaw_rate": "YawRate",
            })

    def test_schema_reader_skips_metadata_time_row(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text(
                '"Time","2:07 PM"\n"Sample Rate","100"\n\n'
                '"Time","RPM","Exhaust Temp"\n'
                '"s","rpm","F"\n\n"0.0","5000","950"\n',
                encoding="utf-8",
            )

            channels, units = read_aim_channel_schema(source)

            self.assertEqual(channels, ["Time", "RPM", "Exhaust Temp"])
            self.assertEqual(units, ["s", "rpm", "F"])

    def test_session_end_marker_is_not_a_beacon(self):
        markers = physical_beacons(
            {"Beacon Markers": ["1.0", "2.0", "3.0"]}, 3.0
        )
        self.assertEqual(markers, (1.0, 2.0))


if __name__ == "__main__":
    unittest.main()
