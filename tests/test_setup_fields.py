from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import setup_fields as sf


class SetupFieldTests(unittest.TestCase):
    def test_torsion_bar_is_front_only(self):
        front = {field.key: field for field in sf.FRONT_FIELDS}
        rear = {field.key: field for field in sf.REAR_FIELDS}

        self.assertEqual(front["torsion_bar"].label, "Front Torsion Bar")
        self.assertNotIn("torsion_bar", rear)

    def test_alignment_is_front_end_only(self):
        front = {field.key: field for field in sf.FRONT_FIELDS}
        corner = {field.key: field for field in sf.FRONT_CORNER_FIELDS}

        self.assertEqual(front["toe"].unit, "boxes")
        self.assertEqual(front["upper_castor_camber"].label,
                         "Top Pill Position")
        self.assertEqual(front["lower_castor_camber"].label,
                         "Bottom Pill Position")
        self.assertEqual(front["front_left_camber"].display_label,
                         "Front Left Camber (ticks in/out)")
        self.assertEqual(front["front_right_camber"].display_label,
                         "Front Right Camber (ticks in/out)")
        self.assertEqual(front["front_left_camber"].kind, "text")
        self.assertEqual(front["front_right_camber"].kind, "text")
        self.assertNotIn("toe", corner)
        self.assertNotIn("camber", corner)
        self.assertNotIn("caster", corner)

    def test_torsion_bar_keeps_inventory_mapping(self):
        self.assertEqual(
            sf.INVENTORY_FIELD_CATEGORIES["torsion_bar"],
            "Front Torsion Bar",
        )


if __name__ == "__main__":
    unittest.main()
