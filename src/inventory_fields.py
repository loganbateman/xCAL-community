"""Category registry and category-specific fields for Parts inventory.

Every inventory item shares the typed columns defined by ``InventoryItem``.
Only attributes that vary by category live in the item's flexible ``specs``
JSON bag.  Keeping this registry in one place lets the future Parts UI build
forms and sortable category views without duplicating labels or storage keys.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryField:
    """A category-specific field stored in ``inventory_items.specs``."""

    key: str
    label: str
    kind: str = "text"  # text | number | integer | date | choice
    unit: str = ""
    options: tuple[str, ...] = ()

    @property
    def display_label(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


# Display order is intentional and is shared by category filters and forms.
CATEGORIES: tuple[str, ...] = (
    "Tires",
    "Engines",
    "Carbs",
    "Axles",
    "Karts",
    "Drivers",
    "Tracks",
    "Rear Hubs",
    "Front Hubs",
    "Rims",
    "Front Torsion Bar",
    "Fuel",
    "Cage",
    "Clutches",
    "Exhausts",
    "Front Spacers",
    "General Consumables",
)

# Baselines are full setup definitions rather than countable stock rows, but
# they live in the Parts workspace so reusable team definitions have one home.
BASELINE_SETUP_CATEGORY = "Baseline Setups"
PARTS_CATEGORIES: tuple[str, ...] = CATEGORIES + (BASELINE_SETUP_CATEGORY,)


CATEGORY_FIELDS: dict[str, tuple[InventoryField, ...]] = {
    "Tires": (
        InventoryField("compound", "Compound"),
        InventoryField("size", "Size"),
        InventoryField("set_number", "Set #"),
        InventoryField("date_code", "Date Code"),
        InventoryField("heat_cycles", "Heat Cycles", "integer"),
        InventoryField("laps", "Laps", "integer"),
        InventoryField("tread_remaining", "Tread Remaining", "number", "%"),
        InventoryField("position", "Position"),
    ),
    "Engines": (
        InventoryField("engine_number", "Engine #"),
        InventoryField("race_class", "Class"),
        InventoryField("builder", "Builder"),
        InventoryField("seal_number", "Seal #"),
        InventoryField("total_hours", "Total Hours", "number", "hr"),
        InventoryField(
            "hours_since_rebuild", "Hours Since Rebuild", "number", "hr"),
        InventoryField("last_rebuild_date", "Last Rebuild", "date"),
    ),
    "Carbs": (
        InventoryField("carb_number", "Carb #"),
        InventoryField("bore", "Bore", "number", "mm"),
        InventoryField("venturi_size", "Venturi Size", "number", "mm"),
        InventoryField("baseline_popoff", "Baseline Pop-Off", "number", "psi"),
        InventoryField("fulcrum_height", "Fulcrum Height", "number", "mm"),
        InventoryField("last_rebuild_date", "Last Rebuild", "date"),
    ),
    "Axles": (
        InventoryField("diameter", "Diameter", "number", "mm"),
        InventoryField("length", "Length", "number", "mm"),
        InventoryField("hardness", "Hardness / Stiffness"),
        InventoryField("material", "Material"),
        InventoryField("keyway", "Keyway"),
    ),
    "Karts": (
        InventoryField("kart_number", "Kart #"),
        InventoryField("model_year", "Model Year", "integer"),
        InventoryField("frame_serial", "Frame Serial #"),
        InventoryField("chassis_family", "Chassis Family"),
        InventoryField("race_class", "Class"),
    ),
    "Drivers": (
        InventoryField("race_number", "Race #"),
        InventoryField("role", "Role"),
        InventoryField("license_number", "License #"),
        InventoryField("license_expiry", "License Expiry", "date"),
    ),
    "Tracks": (
        InventoryField("location", "Location"),
        InventoryField("configuration", "Configuration"),
        InventoryField("surface", "Surface"),
        InventoryField("length", "Length"),
        InventoryField("direction", "Direction"),
    ),
    "Rear Hubs": (
        InventoryField("material", "Material"),
        InventoryField("bore", "Bore", "number", "mm"),
        InventoryField("diameter", "Diameter", "number", "mm"),
        InventoryField("length", "Length", "number", "mm"),
        InventoryField("bolt_pattern", "Bolt Pattern"),
        InventoryField("side", "Side"),
    ),
    "Front Hubs": (
        InventoryField("material", "Material"),
        InventoryField("bore", "Bore", "number", "mm"),
        InventoryField("diameter", "Diameter", "number", "mm"),
        InventoryField("length", "Length", "number", "mm"),
        InventoryField("bolt_pattern", "Bolt Pattern"),
        InventoryField("side", "Side"),
    ),
    "Rims": (
        InventoryField("diameter", "Diameter", "number", "in"),
        InventoryField("width", "Width", "number", "in"),
        InventoryField("material", "Material"),
        InventoryField("offset", "Offset", "number", "mm"),
        InventoryField("bolt_pattern", "Bolt Pattern"),
        InventoryField("set_number", "Set #"),
        InventoryField("position", "Position"),
    ),
    "Front Torsion Bar": (
        InventoryField("diameter", "Diameter", "number", "mm"),
        InventoryField("length", "Length", "number", "mm"),
        InventoryField("material", "Material"),
        InventoryField("stiffness", "Stiffness"),
        InventoryField("adjustability", "Adjustability"),
    ),
    "Fuel": (
        InventoryField("fuel_type", "Fuel Type"),
        InventoryField("octane", "Octane", "number"),
        InventoryField("batch_lot", "Batch / Lot"),
        InventoryField("container_size", "Container Size", "number"),
        InventoryField("mix_ratio", "Oil Mix Ratio"),
        InventoryField("expiration_date", "Expiration Date", "date"),
    ),
    # "Cage" is intentionally generic until the team confirms which karting
    # component this name represents.
    "Cage": (
        InventoryField("cage_type", "Type"),
        InventoryField("material", "Material"),
        InventoryField("size", "Size"),
        InventoryField("specification", "Specification"),
    ),
    "Clutches": (
        InventoryField(
            "clutch_type", "Clutch Type", "choice", options=("Wet", "Dry")),
    ),
    "Exhausts": (
        InventoryField("pipe_type", "Pipe Type"),
    ),
    "Front Spacers": (
        InventoryField("thickness", "Thickness", "number", "mm"),
        InventoryField("material", "Material"),
    ),
    # General consumables intentionally use only the clean common fields
    # (name, quantity, unit, cost, supplier, and notes).
    "General Consumables": (),
}


TRACKING_MODES: tuple[str, ...] = ("serialized", "set", "bulk")
STATUS_OPTIONS: tuple[str, ...] = (
    "available", "installed", "reserved", "service", "retired")
CONDITION_OPTIONS: tuple[str, ...] = (
    "new", "good", "worn", "needs_service", "damaged", "retired")
ENTITY_CATEGORIES: frozenset[str] = frozenset({"Karts", "Drivers", "Tracks"})
MAX_INVENTORY_QUANTITY = 999_999_999.999
MAX_INVENTORY_COST = 9_999_999_999.99


def fields_for_category(category: str) -> tuple[InventoryField, ...]:
    """Return the configured fields for ``category`` (or an empty tuple)."""

    return CATEGORY_FIELDS.get(category, ())
