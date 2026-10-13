from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database import client as db
from database.models import Setup


class BaselineClientTests(unittest.TestCase):
    def test_track_scope_key_normalizes_case_whitespace_and_blanks(self):
        self.assertEqual(db._track_scope_key("  Kaminsky "), "kaminsky")
        self.assertIsNone(db._track_scope_key("   "))
        self.assertIsNone(db._track_scope_key(None))

    @patch("database.client.get_client")
    def test_conflict_lookup_matches_legacy_track_format(self, get_client):
        query = MagicMock()
        get_client.return_value.table.return_value = query
        for method in ("select", "eq", "is_", "neq", "order"):
            getattr(query, method).return_value = query
        query.execute.return_value.data = [{
            "id": "other-baseline",
            "is_baseline": True,
            "chassis_id": "kart-id",
            "track": "  KAMINSKY ",
            "name": "Existing",
        }]

        result = db.fetch_conflicting_baseline(
            "kart-id", "Kaminsky", exclude_id="current-id")

        self.assertEqual(result.id, "other-baseline")
        query.neq.assert_called_once_with("id", "current-id")

    def test_setup_full_update_payload_preserves_explicit_nulls(self):
        payload = Setup(id="setup-id", name="Test").to_dict(
            include_nulls=True)

        self.assertIn("track", payload)
        self.assertIsNone(payload["track"])
        self.assertIn("driver_id", payload)
        self.assertIsNone(payload["driver_id"])


if __name__ == "__main__":
    unittest.main()
