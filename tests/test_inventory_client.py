from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database import client as db
from database.models import InventoryItem


class InventoryClientTests(unittest.TestCase):
    def test_validation_normalizes_blank_unit(self):
        item = InventoryItem(category="Fuel", name="Race fuel", unit="")

        db._validate_inventory_item(item)

        self.assertEqual(item.unit, "each")

    def test_validation_rejects_non_finite_numbers(self):
        item = InventoryItem(
            category="Fuel", name="Race fuel", quantity=math.inf)

        with self.assertRaises(ValueError):
            db._validate_inventory_item(item)

    def test_validation_rejects_boolean_common_number(self):
        item = InventoryItem(
            category="Fuel", name="Race fuel", cost=True)

        with self.assertRaisesRegex(ValueError, "cost.*number"):
            db._validate_inventory_item(item)

    def test_validation_rejects_invalid_typed_spec(self):
        item = InventoryItem(
            category="Engines", name="Race engine",
            specs={"hours_since_rebuild": "not-a-number"})

        with self.assertRaisesRegex(ValueError, "Hours Since Rebuild"):
            db._validate_inventory_item(item)

    def test_validation_rejects_complex_text_spec(self):
        item = InventoryItem(
            category="Cage", name="Cage", specs={"material": ["steel"]})

        with self.assertRaisesRegex(ValueError, "Material.*text"):
            db._validate_inventory_item(item)

    def test_validation_rejects_non_iso_date(self):
        item = InventoryItem(
            category="Fuel", name="Race fuel",
            specs={"expiration_date": "08/24/2026"})

        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            db._validate_inventory_item(item)

    def test_entity_records_are_serialized_with_quantity_one(self):
        item = InventoryItem(
            category="Drivers", name="Driver", tracking_mode="bulk",
            quantity=2)

        with self.assertRaisesRegex(ValueError, "quantity 1"):
            db._validate_inventory_item(item)

    @patch("database.client.google_drive_client.audit_identity",
           return_value="Alex Racer <alex@example.edu>")
    @patch("database.client.get_client")
    def test_metadata_update_cannot_write_quantity(self, get_client, _identity):
        item = InventoryItem(
            id="item-id", category="Axles", name="Medium axle", quantity=4)
        table = MagicMock()
        get_client.return_value.table.return_value = table
        response = (
            table.update.return_value.eq.return_value.eq.return_value
            .execute.return_value)
        response.data = [{
            **item.to_dict(),
            "id": item.id,
        }]

        db.update_inventory_item(item)

        payload = table.update.call_args.args[0]
        self.assertNotIn("quantity", payload)
        self.assertNotIn("archived_at", payload)
        self.assertNotIn("revision", payload)
        self.assertEqual(
            payload["updated_by"], "Alex Racer <alex@example.edu>")
        table.update.return_value.eq.return_value.eq.assert_called_once_with(
            "revision", 1)

    @patch("database.client.get_client")
    def test_revision_history_is_fetched_newest_first(self, get_client):
        table = MagicMock()
        get_client.return_value.table.return_value = table
        response = (
            table.select.return_value.eq.return_value.order.return_value
            .execute.return_value)
        response.data = [{
            "item_id": "item-id", "revision": 2,
            "snapshot": {"name": "N Axle"},
        }]

        revisions = db.fetch_inventory_revisions("item-id")

        self.assertEqual(revisions[0].revision, 2)
        table.select.return_value.eq.return_value.order.assert_called_once_with(
            "revision", desc=True)

    @patch("database.client.google_drive_client.audit_identity",
           return_value="Alex Racer <alex@example.edu>")
    @patch("database.client.get_client")
    def test_stock_adjustment_uses_atomic_rpc(self, get_client, _identity):
        item = InventoryItem(
            id="item-id", category="Fuel", name="Race fuel", quantity=7)
        rpc = get_client.return_value.rpc
        rpc.return_value.execute.return_value.data = [{
            **item.to_dict(),
            "id": item.id,
        }]

        saved = db.adjust_inventory_stock("item-id", -2, "Used at race")

        self.assertEqual(saved.quantity, 7)
        rpc.assert_called_once_with("adjust_inventory_stock", {
            "p_item_id": "item-id",
            "p_delta": -2,
            "p_reason": "Used at race",
            "p_notes": None,
            "p_created_by": "Alex Racer <alex@example.edu>",
        })

    def test_stock_adjustment_rejects_non_finite_delta(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            db.adjust_inventory_stock("item-id", math.nan)

    def test_stock_adjustment_rejects_excess_precision(self):
        with self.assertRaisesRegex(ValueError, "3 decimal"):
            db.adjust_inventory_stock("item-id", 0.0004)


if __name__ == "__main__":
    unittest.main()
