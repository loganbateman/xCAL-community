from dataclasses import dataclass, field
from typing import Optional


def _optional_float(value) -> Optional[float]:
    """Normalize Postgres NUMERIC values returned as numbers or strings."""

    if value is None or value == "":
        return None
    return float(value)


@dataclass
class Driver:
    id: Optional[str] = None
    name: str = ""
    number: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Driver":
        return cls(
            id=d.get("id"),
            name=d.get("name", ""),
            number=d.get("number"),
            notes=d.get("notes"),
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "name": self.name,
            "number": self.number,
            "notes": self.notes,
        }.items() if v is not None}


@dataclass
class Chassis:
    id: Optional[str] = None
    name: str = ""
    manufacturer: Optional[str] = None
    model_year: Optional[int] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Chassis":
        return cls(
            id=d.get("id"),
            name=d.get("name", ""),
            manufacturer=d.get("manufacturer"),
            model_year=d.get("model_year"),
            notes=d.get("notes"),
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model_year": self.model_year,
            "notes": self.notes,
        }.items() if v is not None}


@dataclass
class Track:
    id: Optional[str] = None
    name: str = ""
    location: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(
            id=d.get("id"),
            name=d.get("name", ""),
            location=d.get("location"),
            notes=d.get("notes"),
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "name": self.name,
            "location": self.location,
            "notes": self.notes,
        }.items() if v is not None}


@dataclass
class InventoryItem:
    """A serialized asset, set, or bulk-stock item in Parts inventory."""

    id: Optional[str] = None
    revision: int = 1
    category: str = ""
    name: str = ""
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    part_number: Optional[str] = None
    tracking_mode: str = "serialized"
    quantity: float = 1.0
    unit: str = "each"
    status: str = "available"
    condition: str = "good"
    supplier: Optional[str] = None
    cost: Optional[float] = None
    notes: Optional[str] = None
    specs: dict = field(default_factory=dict)
    archived_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    revision_id: Optional[str] = None
    merge_notice: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "InventoryItem":
        quantity = _optional_float(d.get("quantity"))
        return cls(
            id=d.get("id"),
            revision=int(d.get("revision") or 1),
            category=d.get("category") or "",
            name=d.get("name") or "",
            manufacturer=d.get("manufacturer"),
            model=d.get("model"),
            part_number=d.get("part_number"),
            tracking_mode=d.get("tracking_mode") or "serialized",
            quantity=1.0 if quantity is None else quantity,
            unit=d.get("unit") or "each",
            status=d.get("status") or "available",
            condition=d.get("condition") or "good",
            supplier=d.get("supplier"),
            cost=_optional_float(d.get("cost")),
            notes=d.get("notes"),
            specs=dict(d.get("specs") or {}),
            archived_at=d.get("archived_at"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            created_by=d.get("created_by"),
            updated_by=d.get("updated_by"),
            revision_id=d.get("revision_id"),
            merge_notice=dict(d.get("_merge_notice") or {}),
        )

    def to_dict(self, include_nulls: bool = False) -> dict:
        out = {
            "category": self.category,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "part_number": self.part_number,
            "tracking_mode": self.tracking_mode,
            "quantity": self.quantity,
            "unit": self.unit,
            "status": self.status,
            "condition": self.condition,
            "supplier": self.supplier,
            "cost": self.cost,
            "notes": self.notes,
            "specs": self.specs or {},
            "archived_at": self.archived_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }
        if include_nulls:
            return out
        return {key: value for key, value in out.items() if value is not None}


@dataclass
class InventoryRevision:
    """Immutable snapshot of one inventory item definition revision."""

    item_id: Optional[str] = None
    revision: int = 1
    snapshot: dict = field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "InventoryRevision":
        return cls(
            item_id=d.get("item_id"),
            id=d.get("id") or (d.get("snapshot") or {}).get("revision_id"),
            revision=int(d.get("revision") or 1),
            snapshot=dict(d.get("snapshot") or {}),
            created_by=d.get("created_by"),
            created_at=d.get("created_at"),
        )


@dataclass
class InventoryMovement:
    """An immutable quantity change recorded for one inventory item."""

    id: Optional[str] = None
    item_id: Optional[str] = None
    quantity_delta: float = 0.0
    quantity_before: float = 0.0
    quantity_after: float = 0.0
    reason: str = "Stock adjustment"
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "InventoryMovement":
        return cls(
            id=d.get("id"),
            item_id=d.get("item_id"),
            quantity_delta=_optional_float(d.get("quantity_delta")) or 0.0,
            quantity_before=_optional_float(d.get("quantity_before")) or 0.0,
            quantity_after=_optional_float(d.get("quantity_after")) or 0.0,
            reason=d.get("reason") or "Stock adjustment",
            notes=d.get("notes"),
            created_by=d.get("created_by"),
            created_at=d.get("created_at"),
        )

    def to_dict(self) -> dict:
        return {key: value for key, value in {
            "item_id": self.item_id,
            "quantity_delta": self.quantity_delta,
            "quantity_before": self.quantity_before,
            "quantity_after": self.quantity_after,
            "reason": self.reason,
            "notes": self.notes,
            "created_by": self.created_by,
        }.items() if value is not None}


@dataclass
class Setup:
    id: Optional[str] = None
    session_id: Optional[str] = None
    drive_folder_id: Optional[str] = None
    drive_folder_path: Optional[str] = None
    driver_id: Optional[str] = None
    chassis_id: Optional[str] = None
    name: str = ""
    is_baseline: bool = False
    track: Optional[str] = None
    # All detailed sheet fields (tire pressures, temps, carb, checklist, …)
    data: dict = field(default_factory=dict)
    # Meta
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Joined fields (not stored)
    driver_name: Optional[str] = None
    chassis_name: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Setup":
        s = cls()
        s.id = d.get("id")
        s.session_id = d.get("session_id")
        s.drive_folder_id = d.get("drive_folder_id")
        s.drive_folder_path = d.get("drive_folder_path")
        s.driver_id = d.get("driver_id")
        s.chassis_id = d.get("chassis_id")
        s.name = d.get("name") or ""
        s.is_baseline = bool(d.get("is_baseline"))
        s.track = d.get("track")
        s.data = dict(d.get("extra_fields") or {})
        s.notes = d.get("notes")
        s.created_by = d.get("created_by")
        s.created_at = d.get("created_at")
        s.updated_at = d.get("updated_at")
        if "drivers" in d and d["drivers"]:
            s.driver_name = d["drivers"].get("name")
        if "chassis" in d and d["chassis"]:
            s.chassis_name = d["chassis"].get("name")
        identity = s.data.get("_identity_part_revisions") or {}
        driver_ref = identity.get("driver") or {}
        chassis_ref = identity.get("chassis") or {}
        if driver_ref.get("name"):
            s.driver_name = str(driver_ref["name"])
        if chassis_ref.get("name"):
            s.chassis_name = str(chassis_ref["name"])
        return s

    def to_dict(self, include_nulls: bool = False) -> dict:
        out = {
            "session_id": self.session_id,
            "drive_folder_id": self.drive_folder_id,
            "drive_folder_path": self.drive_folder_path,
            "driver_id": self.driver_id,
            "chassis_id": self.chassis_id,
            "name": self.name or None,
            "is_baseline": self.is_baseline,
            "track": self.track,
            "extra_fields": self.data or {},
            "notes": self.notes,
            "created_by": self.created_by,
        }
        if include_nulls:
            return out
        return {k: v for k, v in out.items() if v is not None}

    def get(self, key: str, default=None):
        return self.data.get(key, default)
