"""Shared durable inventory operations, independent of Qt dialogs."""
import database.client as db
import offline_store
from database.models import InventoryItem
from inventory_fields import ENTITY_CATEGORIES


def definition_signature(item):
    definition = item.to_dict(include_nulls=True)
    for key in ("quantity", "archived_at", "created_by", "updated_by"):
        definition.pop(key, None)
    return definition


def inventory_reference(part):
    result = {"item_id": part.id, "revision": int(part.revision or 1), "name": part.name}
    if part.revision_id:
        result["revision_id"] = part.revision_id
    return result


def save_inventory_changes(
        current: InventoryItem, target: InventoryItem) -> InventoryItem:
    """Save all Parts changes online, or durably queue them when offline."""
    if (getattr(current, "_offline_pending", False)
            or str(current.id or "").startswith("offline-part-")):
        return offline_store.queue_inventory_item(target, current)
    definition_changed = (
        definition_signature(target)
        != definition_signature(current))
    quantity_delta = round(
        float(target.quantity) - float(current.quantity), 3)
    archive_changed = bool(target.archived_at) != bool(current.archived_at)
    saved = current
    try:
        current_entity = current.category in ENTITY_CATEGORIES
        target_entity = target.category in ENTITY_CATEGORIES
        quantity_first = bool(
            quantity_delta and target_entity and not current_entity)
        if quantity_first:
            saved = db.adjust_inventory_stock(
                saved.id, quantity_delta, "Quantity changed from Part Metadata")
        if definition_changed:
            target._edit_base = current
            saved = db.update_inventory_item(target)
        if quantity_delta and not quantity_first:
            saved = db.adjust_inventory_stock(
                saved.id, quantity_delta, "Quantity changed from Part Metadata")
        if archive_changed:
            saved = db.archive_inventory_item(
                saved.id, archived=bool(target.archived_at))
        offline_store.cache_inventory_item(saved)
        return saved
    except Exception:
        if db.check_connection():
            raise
        return offline_store.queue_inventory_item(target, saved)


def create_inventory_item(item: InventoryItem) -> InventoryItem:
    try:
        saved = db.create_inventory_item(item)
        offline_store.cache_inventory_item(saved)
        return saved
    except Exception:
        if db.check_connection():
            raise
        return offline_store.queue_inventory_item(item)



