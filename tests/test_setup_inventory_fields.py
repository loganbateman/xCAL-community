from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import setup_fields as sf
from inventory_fields import CATEGORIES


class SetupInventoryFieldTests(unittest.TestCase):
    def test_every_inventory_selector_maps_to_a_real_setup_field_and_category(self):
        setup_keys = {field.key for field in sf.all_value_fields()}

        self.assertTrue(sf.INVENTORY_FIELD_CATEGORIES)
        self.assertLessEqual(set(sf.INVENTORY_FIELD_CATEGORIES), setup_keys)
        self.assertLessEqual(
            set(sf.INVENTORY_FIELD_CATEGORIES.values()), set(CATEGORIES))

    def test_axle_and_engine_are_inventory_backed_setup_fields(self):
        self.assertEqual(sf.INVENTORY_FIELD_CATEGORIES["axle"], "Axles")
        self.assertEqual(sf.INVENTORY_FIELD_CATEGORIES["engine"], "Engines")
        self.assertEqual(sf.INVENTORY_FIELD_CATEGORIES["clutch"], "Clutches")
        self.assertEqual(
            sf.INVENTORY_FIELD_CATEGORIES["exhaust_pipe"], "Exhausts")

    def test_run_date_and_time_are_separate_fields(self):
        run_date = next(field for field in sf.META_FIELDS
                        if field.key == "run_date")

        self.assertEqual(run_date.label, "Date")
        self.assertEqual(run_date.kind, "date")
        self.assertEqual(sf.RUN_TIME_FIELD.key, "run_time")
        self.assertEqual(sf.RUN_TIME_FIELD.kind, "time")
        self.assertNotIn(
            "session_number", {field.key for field in sf.RUN_FIELDS})

    def test_front_and_rear_track_controls_are_axle_level(self):
        self.assertEqual(
            [(field.key, field.display_label)
             for field in sf.FRONT_TRACK_FIELDS],
            [
                ("front_hub_material", "Hub Type"),
                ("rim_type", "Front Rims"),
                ("front_track_spacers", "Front Spacers"),
                ("front_track_width", "Overall Track Width (mm)"),
            ],
        )
        self.assertEqual(
            [(field.key, field.display_label)
             for field in sf.REAR_TRACK_FIELDS],
            [
                ("rear_hub_material", "Hub Type"),
                ("rear_rim_type", "Rear Rims"),
                ("rear_track", "Overall Track Width"),
            ],
        )
        self.assertNotIn("front_track_spacers", {
            field.key for field in sf.FRONT_FIELDS})
        self.assertNotIn("rim_type", {field.key for field in sf.TIRE_FIELDS})
        self.assertNotIn("wheels", {field.key for field in sf.TIRE_FIELDS})
        self.assertNotIn("hub_material", {
            field.key for field in sf.REAR_CORNER_FIELDS})

    def test_front_and_rear_rims_use_the_same_inventory_category(self):
        self.assertEqual(sf.INVENTORY_FIELD_CATEGORIES["rim_type"], "Rims")
        self.assertEqual(
            sf.INVENTORY_FIELD_CATEGORIES["rear_rim_type"], "Rims")
        self.assertEqual(
            sf.INVENTORY_FIELD_CATEGORIES["rear_hub_material"], "Rear Hubs")
        self.assertEqual(
            sf.INVENTORY_FIELD_CATEGORIES["front_track_spacers"],
            "Front Spacers")


if __name__ == "__main__":
    unittest.main()
