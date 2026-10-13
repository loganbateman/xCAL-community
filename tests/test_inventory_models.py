from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database.models import InventoryItem, InventoryRevision
from inventory_fields import (
    CATEGORIES, CONDITION_OPTIONS, STATUS_OPTIONS, TRACKING_MODES,
    fields_for_category,
)


class InventoryModelTests(unittest.TestCase):
    def test_requested_categories_are_seeded_in_display_order(self):
        self.assertEqual(CATEGORIES, (
            "Tires", "Engines", "Carbs", "Axles", "Karts", "Drivers",
            "Tracks", "Rear Hubs", "Front Hubs", "Rims", "Front Torsion Bar",
            "Fuel", "Cage", "Clutches", "Exhausts", "Front Spacers",
            "General Consumables",
        ))

    def test_tracks_are_revisioned_identity_parts(self):
        self.assertIn("Tracks", CATEGORIES)
        self.assertEqual(
            {field.key for field in fields_for_category("Tracks")},
            {"location", "configuration", "surface", "length", "direction"},
        )

    def test_clutches_offer_wet_and_dry_types(self):
        fields = {field.key: field for field in fields_for_category("Clutches")}

        self.assertEqual(fields["clutch_type"].options, ("Wet", "Dry"))

    def test_exhausts_capture_pipe_type(self):
        fields = {
            field.key: field for field in fields_for_category("Exhausts")
        }

        self.assertEqual(set(fields), {"pipe_type"})
        self.assertEqual(fields["pipe_type"].label, "Pipe Type")

    def test_front_spacers_have_only_useful_spacer_details(self):
        fields = {
            field.key: field for field in fields_for_category("Front Spacers")
        }

        self.assertEqual(set(fields), {"thickness", "material"})
        self.assertEqual(fields["thickness"].unit, "mm")

    def test_general_consumables_use_clean_common_fields(self):
        self.assertEqual(fields_for_category("General Consumables"), ())

    def test_category_field_keys_are_unique(self):
        for category in CATEGORIES:
            keys = [field.key for field in fields_for_category(category)]
            self.assertEqual(len(keys), len(set(keys)), category)

    def test_inventory_item_normalizes_postgres_numeric_strings(self):
        item = InventoryItem.from_dict({
            "category": "Fuel",
            "name": "Race fuel",
            "revision": 4,
            "quantity": "12.500",
            "cost": "89.99",
        })

        self.assertEqual(item.revision, 4)
        self.assertEqual(item.quantity, 12.5)
        self.assertEqual(item.cost, 89.99)

    def test_inventory_revision_keeps_immutable_snapshot(self):
        revision = InventoryRevision.from_dict({
            "item_id": "axle-id",
            "revision": 3,
            "snapshot": {"name": "N Axle", "specs": {"diameter": 50}},
            "created_by": "Michael",
        })

        self.assertEqual(revision.revision, 3)
        self.assertEqual(revision.snapshot["name"], "N Axle")

    def test_full_update_payload_keeps_explicit_nulls(self):
        item = InventoryItem(category="Axles", name="Medium axle")

        payload = item.to_dict(include_nulls=True)

        self.assertIn("supplier", payload)
        self.assertIsNone(payload["supplier"])
        self.assertNotIn("revision", payload)

    def test_default_values_use_database_canonical_options(self):
        item = InventoryItem(category="Rims", name="Front rim")

        self.assertIn(item.tracking_mode, TRACKING_MODES)
        self.assertIn(item.status, STATUS_OPTIONS)
        self.assertIn(item.condition, CONDITION_OPTIONS)


if __name__ == "__main__":
    unittest.main()
