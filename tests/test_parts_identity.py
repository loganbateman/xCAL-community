from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database.models import InventoryItem, Setup
from utils import team_naming


class PartsIdentityTests(unittest.TestCase):
    def test_inventory_drivers_match_karts_by_number(self):
        items = [
            InventoryItem(
                id="driver", category="Drivers", name="Alex Davis",
                specs={"race_number": "11"}),
            InventoryItem(
                id="kart", category="Karts", name="2021 Birel",
                specs={"kart_number": "11"}),
        ]

        self.assertEqual(team_naming.from_inventory(items), {
            "assignments": [{
                "driver": "Alex Davis",
                "number": "11",
                "kart": "2021 Birel",
            }],
        })

    def test_setup_uses_pinned_parts_names_over_legacy_joins(self):
        setup = Setup.from_dict({
            "extra_fields": {
                "_identity_part_revisions": {
                    "driver": {
                        "item_id": "driver", "revision": 2,
                        "name": "Alex Davis",
                    },
                    "chassis": {
                        "item_id": "kart", "revision": 1,
                        "name": "2021 Birel",
                    },
                },
            },
            "drivers": {"name": "Legacy Alex"},
            "chassis": {"name": "Birel"},
        })

        self.assertEqual(setup.driver_name, "Alex Davis")
        self.assertEqual(setup.chassis_name, "2021 Birel")


if __name__ == "__main__":
    unittest.main()
