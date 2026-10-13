"""
Supabase client — singleton with connection-status helpers.
All DB access goes through the functions exposed here.
"""
from __future__ import annotations
from copy import deepcopy
import json
import sys
import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from uuid import uuid4

# Allow running src/ files directly during development
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from typing import Optional, List
from database.drive_store import DriveStore
import config
from utils import google_drive_client
from utils.debug import log
from database.models import (
    Driver, Chassis, Setup, Track, InventoryItem, InventoryMovement,
    InventoryRevision,
)
from inventory_fields import (
    CATEGORIES, CONDITION_OPTIONS, ENTITY_CATEGORIES,
    MAX_INVENTORY_COST, MAX_INVENTORY_QUANTITY, STATUS_OPTIONS, TRACKING_MODES,
    fields_for_category,
)

_client: Optional[DriveStore] = None
_connected: bool = False


class ConcurrentEditError(RuntimeError):
    """Raised when another person's edit overlaps the fields being saved."""

    def __init__(self, fields, entity="setup"):
        self.fields = tuple(fields)
        labels = ", ".join(
            str(field).replace("extra_fields.", "").replace("_", " ")
            for field in self.fields)
        super().__init__(
            f"Another person changed this {entity} while you were editing it. "
            f"Your changes were not overwritten. Conflicting fields: {labels}. "
            "Reload the latest version, review those fields, and save again.")


def get_client() -> DriveStore:
    global _client
    if _client is None:
        _client = DriveStore(config.DATA_DIR / "catalog.sqlite3")
    return _client


def reset_client() -> None:
    global _client, _connected
    _client = None
    _connected = False


def check_connection() -> bool:
    global _connected
    try:
        get_client().sync()
        get_client().table("tracks").select("id").limit(1).execute()
        _connected = True
    except Exception as exc:
        log.debug("check_connection failed: %s", exc)
        _connected = False
    return _connected


# ── Drivers ───────────────────────────────────────────────────────────────────

def fetch_drivers() -> List[Driver]:
    res = get_client().table("drivers").select("*").order("name").execute()
    return [Driver.from_dict(r) for r in (res.data or [])]


def create_driver(driver: Driver) -> Driver:
    res = get_client().table("drivers").insert(driver.to_dict()).execute()
    return Driver.from_dict(res.data[0])


def update_driver(driver: Driver) -> Driver:
    res = (get_client().table("drivers")
           .update(driver.to_dict())
           .eq("id", driver.id)
           .execute())
    return Driver.from_dict(res.data[0])


def delete_driver(driver_id: str) -> None:
    get_client().table("drivers").delete().eq("id", driver_id).execute()


# ── Chassis ───────────────────────────────────────────────────────────────────

def fetch_chassis() -> List[Chassis]:
    res = get_client().table("chassis").select("*").order("name").execute()
    return [Chassis.from_dict(r) for r in (res.data or [])]


def create_chassis(chassis: Chassis) -> Chassis:
    res = get_client().table("chassis").insert(chassis.to_dict()).execute()
    return Chassis.from_dict(res.data[0])


def update_chassis(c: Chassis) -> Chassis:
    res = (get_client().table("chassis")
           .update(c.to_dict())
           .eq("id", c.id)
           .execute())
    return Chassis.from_dict(res.data[0])


def delete_chassis(chassis_id: str) -> None:
    baseline = (get_client().table("setups")
                .select("id,name")
                .eq("is_baseline", True)
                .eq("chassis_id", chassis_id)
                .limit(1)
                .execute())
    if baseline.data:
        name = baseline.data[0].get("name") or "Unnamed baseline"
        raise ValueError(
            f"Reassign or delete baseline '{name}' before deleting this chassis")
    get_client().table("chassis").delete().eq("id", chassis_id).execute()


# ── Parts inventory ───────────────────────────────────────────────────────────────────────────

def _inventory_response_row(data, operation: str) -> dict:
    """Return one row from Supabase insert/update/RPC response data."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        return data[0]
    raise RuntimeError(f"{operation} returned no inventory item")


def _validate_inventory_item(item: InventoryItem) -> None:
    if item.category not in CATEGORIES:
        raise ValueError(f"Unknown inventory category: {item.category!r}")
    item.name = (item.name or "").strip()
    item.unit = (item.unit or "").strip() or "each"
    if not item.name:
        raise ValueError("Inventory item name is required")
    if item.tracking_mode not in TRACKING_MODES:
        raise ValueError(f"Unknown tracking mode: {item.tracking_mode!r}")
    if item.status not in STATUS_OPTIONS:
        raise ValueError(f"Unknown inventory status: {item.status!r}")
    if item.condition not in CONDITION_OPTIONS:
        raise ValueError(f"Unknown inventory condition: {item.condition!r}")

    def _date_value(value, label):
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError(f"Inventory {label} must use YYYY-MM-DD")
        value = value.strip()
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"Inventory {label} must use YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"Inventory {label} must use YYYY-MM-DD")
        return value

    def _number_value(value, label, required=False):
        if value is None:
            if required:
                raise ValueError(f"Inventory {label} is required")
            return None
        if isinstance(value, bool):
            raise ValueError(f"Inventory {label} must be a number")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Inventory {label} must be a number") from exc
        if not isfinite(value):
            raise ValueError(f"Inventory {label} must be a finite number")
        return value

    item.quantity = _number_value(item.quantity, "quantity", required=True)
    item.cost = _number_value(item.cost, "cost")
    if item.quantity < 0:
        raise ValueError("Inventory quantity cannot be negative")
    if item.quantity > MAX_INVENTORY_QUANTITY:
        raise ValueError("Inventory quantity is too large")
    if item.cost is not None and item.cost < 0:
        raise ValueError("Inventory cost cannot be negative")
    if item.cost is not None and item.cost > MAX_INVENTORY_COST:
        raise ValueError("Inventory cost is too large")
    if item.category in ENTITY_CATEGORIES:
        if item.tracking_mode != "serialized" or item.quantity != 1:
            raise ValueError(
                "Karts, Drivers, and Tracks must be serialized records with "
                "quantity 1")

    if not isinstance(item.specs, dict):
        raise ValueError("Inventory category details must be an object")
    configured_fields = {
        field.key: field for field in fields_for_category(item.category)}
    for key, field in configured_fields.items():
        value = item.specs.get(key)
        if value in (None, ""):
            continue
        if field.kind == "date":
            item.specs[key] = _date_value(value, field.label)
        elif field.kind in ("number", "integer"):
            if isinstance(value, bool):
                raise ValueError(
                    f"Inventory {field.label} must be a number")
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Inventory {field.label} must be a number") from exc
            if not isfinite(parsed):
                raise ValueError(
                    f"Inventory {field.label} must be a finite number")
            if field.kind == "integer" and not parsed.is_integer():
                raise ValueError(
                    f"Inventory {field.label} must be a whole number")
            item.specs[key] = int(parsed) if field.kind == "integer" else parsed
        elif isinstance(value, bool) or not isinstance(
                value, (str, int, float)):
            raise ValueError(f"Inventory {field.label} must be text")
        else:
            item.specs[key] = str(value).strip()


def _inventory_matches(item: InventoryItem, search: str) -> bool:
    """Case-insensitive local search, including category-specific specs."""
    terms = search.casefold().split()
    if not terms:
        return True
    common = (
        item.category, item.name, item.manufacturer, item.model,
        item.part_number, item.tracking_mode, item.unit,
        item.status, item.condition, item.supplier, item.notes,
    )
    spec_values = " ".join(
        f"{key} {value}" for key, value in (item.specs or {}).items())
    haystack = " ".join(str(value) for value in common if value is not None)
    haystack = f"{haystack} {spec_values}".casefold()
    return all(term in haystack for term in terms)


def fetch_inventory_items(
        category: Optional[str] = None,
        include_archived: bool = False,
        search: Optional[str] = None) -> List[InventoryItem]:
    """Fetch one category or all Parts items, optionally including archives.

    Search is applied locally after the typed rows are loaded.  This keeps user
    input out of PostgREST's raw ``or`` filter syntax and also searches values
    stored in the flexible ``specs`` bag.
    """
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"Unknown inventory category: {category!r}")
    query = get_client().table("inventory_items").select("*")
    if category is not None:
        query = query.eq("category", category)
    if not include_archived:
        query = query.is_("archived_at", "null")
    res = query.order("category").order("name").execute()
    items = [InventoryItem.from_dict(row) for row in (res.data or [])]
    if search:
        items = [item for item in items if _inventory_matches(item, search)]
    return items


def search_inventory_items(
        search: str,
        category: Optional[str] = None,
        include_archived: bool = False) -> List[InventoryItem]:
    """Search Parts inventory across common fields and category specs."""
    return fetch_inventory_items(category, include_archived, search)


def fetch_inventory_item(item_id: str) -> Optional[InventoryItem]:
    res = (get_client().table("inventory_items")
           .select("*")
           .eq("id", item_id)
           .limit(1)
           .execute())
    rows = res.data or []
    return InventoryItem.from_dict(rows[0]) if rows else None


def create_inventory_item(item: InventoryItem) -> InventoryItem:
    _validate_inventory_item(item)
    item.created_by = google_drive_client.audit_identity()
    res = (get_client().table("inventory_items")
           .insert(item.to_dict())
           .execute())
    return InventoryItem.from_dict(
        _inventory_response_row(res.data, "Create inventory item"))


def update_inventory_item(item: InventoryItem) -> InventoryItem:
    """Create the next immutable definition revision for an inventory item."""
    if not item.id:
        raise ValueError("Inventory item id is required for update")
    _validate_inventory_item(item)
    payload = item.to_dict(include_nulls=True)
    # Quantity has an immutable movement trail, while archival has a dedicated
    # operation.  Keep both out of general metadata updates.
    payload.pop("quantity", None)
    payload.pop("archived_at", None)
    payload.pop("created_by", None)
    payload.pop("revision", None)
    payload["updated_by"] = google_drive_client.audit_identity()
    res = (get_client().table("inventory_items")
           .update(payload)
           .eq("id", item.id)
           .eq("revision", int(item.revision or 1))
           .execute())
    if not (res.data or []):
        raise ConcurrentEditError(("part definition",), entity="part")
    return InventoryItem.from_dict(
        _inventory_response_row(res.data, "Update inventory item"))


def fetch_inventory_revisions(item_id: str) -> List[InventoryRevision]:
    """Return every immutable definition revision, newest first."""
    if not item_id:
        raise ValueError("Inventory item id is required for revision history")
    res = (get_client().table("inventory_item_revisions")
           .select("*")
           .eq("item_id", item_id)
           .order("revision", desc=True)
           .execute())
    return [InventoryRevision.from_dict(row) for row in (res.data or [])]


def archive_inventory_item(
        item_id: str, archived: bool = True) -> InventoryItem:
    """Archive or restore an item without deleting its stock history."""
    if not item_id:
        raise ValueError("Inventory item id is required for archival")
    archived_at = datetime.now(timezone.utc).isoformat() if archived else None
    res = (get_client().table("inventory_items")
           .update({"archived_at": archived_at})
           .eq("id", item_id)
           .execute())
    return InventoryItem.from_dict(
        _inventory_response_row(res.data, "Archive inventory item"))


def adjust_inventory_stock(
        item_id: str, delta: float, reason: str = "",
        notes: Optional[str] = None) -> InventoryItem:
    """Atomically change quantity and append an inventory movement record."""
    if not item_id:
        raise ValueError("Inventory item id is required for stock adjustment")
    try:
        decimal_delta = Decimal(str(delta))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Stock adjustment must be a number") from exc
    if not decimal_delta.is_finite():
        raise ValueError("Stock adjustment must be a finite number")
    if decimal_delta == 0:
        raise ValueError("Stock adjustment must be non-zero")
    try:
        if decimal_delta != decimal_delta.quantize(Decimal("0.001")):
            raise ValueError(
                "Stock adjustment cannot use more than 3 decimal places")
    except InvalidOperation as exc:
        raise ValueError("Stock adjustment is too large") from exc
    if abs(decimal_delta) > Decimal(str(MAX_INVENTORY_QUANTITY)):
        raise ValueError("Stock adjustment is too large")
    delta = float(decimal_delta)
    res = get_client().rpc("adjust_inventory_stock", {
        "p_item_id": item_id,
        "p_delta": delta,
        "p_reason": reason,
        "p_notes": notes,
        "p_created_by": google_drive_client.audit_identity(),
    }).execute()
    return InventoryItem.from_dict(
        _inventory_response_row(res.data, "Adjust inventory stock"))


def fetch_inventory_movements(
        item_id: str, limit: Optional[int] = None) -> List[InventoryMovement]:
    """Return newest-first quantity history for an inventory item."""
    query = (get_client().table("inventory_movements")
             .select("*")
             .eq("item_id", item_id)
             .order("created_at", desc=True))
    if limit is not None:
        query = query.limit(limit)
    res = query.execute()
    return [InventoryMovement.from_dict(row) for row in (res.data or [])]


# ── Setups ────────────────────────────────────────────────────────────────────

_SETUP_HISTORY_KEY = "_change_log"
_PENDING_SETUP_ACTOR = "Pending Google verification"


def _setup_snapshot(setup: Setup | None) -> dict:
    """Flatten auditable setup values while excluding the history itself."""
    if setup is None:
        return {}
    snapshot = {
        "name": setup.name or None,
        "track": setup.track,
        "driver": setup.driver_name or setup.driver_id,
        "chassis": setup.chassis_name or setup.chassis_id,
        "notes": setup.notes,
        "drive_folder_path": setup.drive_folder_path,
    }
    data = setup.data or {}
    snapshot.update({
        key: deepcopy(value)
        for key, value in data.items()
        # Telemetry and internal lookup metadata are not user-edited setup
        # values. Duplicating them into every audit event causes payload growth.
        if key != "telemetry" and not key.startswith("_")
    })
    for container_key in (
            "_inventory_part_revisions", "_identity_part_revisions"):
        for field_key, reference in (data.get(container_key) or {}).items():
            if not isinstance(reference, dict):
                continue
            snapshot[f"{field_key}__part_revision"] = {
                key: reference.get(key) for key in ("item_id", "revision", "name")
                if reference.get(key) is not None
            }
    return snapshot


def _setup_changes(before: Setup | None, after: Setup) -> dict:
    old = _setup_snapshot(before)
    new = _setup_snapshot(after)
    return {
        key: {"before": old.get(key), "after": new.get(key)}
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    }


def setup_change_log(setup: Setup | None) -> list[dict]:
    """Return a detached oldest-to-newest setup audit trail."""
    entries = ((setup.data or {}).get(_SETUP_HISTORY_KEY) or []) if setup else []
    return [deepcopy(entry) for entry in entries if isinstance(entry, dict)]


def setup_change_count(setup: Setup | None) -> int:
    """Count audit entries without copying their field diffs."""
    entries = ((setup.data or {}).get(_SETUP_HISTORY_KEY) or []) if setup else []
    return sum(isinstance(entry, dict) for entry in entries)


def latest_setup_change(setup: Setup | None) -> dict | None:
    """Return only the newest audit entry for lightweight list rendering."""
    entries = ((setup.data or {}).get(_SETUP_HISTORY_KEY) or []) if setup else []
    return next((deepcopy(entry) for entry in reversed(entries)
                 if isinstance(entry, dict)), None)


def setup_identity_pending(setup: Setup | None) -> bool:
    """Whether a locally saved setup still needs its Google audit identity."""
    if setup is None:
        return False
    return (
        setup.created_by == _PENDING_SETUP_ACTOR
        or any(entry.get("identity_pending") is True
               for entry in setup_change_log(setup))
    )


def _apply_setup_identity(setup: Setup, user) -> bool:
    history = setup_change_log(setup)
    changed = False
    for entry in history:
        if entry.get("identity_pending") is not True:
            continue
        entry["actor_name"] = user.name
        entry["actor_email"] = user.email
        entry.pop("identity_pending", None)
        changed = True
    if changed:
        setup.data = dict(setup.data or {})
        setup.data[_SETUP_HISTORY_KEY] = history
    if setup.created_by == _PENDING_SETUP_ACTOR:
        setup.created_by = user.audit_label
        changed = True
    return changed


def resolve_setup_change_identities(setup: Setup) -> bool:
    """Resolve pending local attribution before a queued Supabase upload.

    This deliberately raises while Google identity is unavailable so the
    offline row remains queued instead of uploading an unattributed audit
    event. The next automatic reconnect pass retries it.
    """
    if not setup_identity_pending(setup):
        return False
    user = google_drive_client.current_user(interactive=False)
    return _apply_setup_identity(setup, user)


def record_setup_change(setup: Setup, previous: Setup | None) -> bool:
    """Attach a field diff without making local saving depend on Google I/O."""
    changes = _setup_changes(previous, setup)
    if not changes:
        return False
    user = google_drive_client.audit_user_or_none()
    if user is not None:
        _apply_setup_identity(setup, user)
    entry = {
        "id": str(uuid4()),
        "action": "created" if previous is None else "updated",
        "actor_name": user.name if user else _PENDING_SETUP_ACTOR,
        "actor_email": user.email if user else "",
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changes": changes,
    }
    if user is None:
        entry["identity_pending"] = True
    setup.data = dict(setup.data or {})
    history = setup_change_log(setup)
    history.append(entry)
    setup.data[_SETUP_HISTORY_KEY] = history
    if previous is None:
        setup.created_by = (
            user.audit_label if user else _PENDING_SETUP_ACTOR)
    return True


def _merge_setup_history(current: Setup, incoming: Setup) -> list[dict]:
    """Keep server entries when an offline or concurrent edit arrives."""
    merged = setup_change_log(current)

    def event_key(entry: dict) -> tuple[str, str]:
        event_id = str(entry.get("id") or "").strip()
        if event_id:
            return "id", event_id
        # Early changelog builds did not assign event IDs. Use their complete,
        # stable payload as an identity so repeated offline sync attempts cannot
        # duplicate those legacy events indefinitely.
        return "legacy", json.dumps(
            entry, sort_keys=True, separators=(",", ":"), default=str)

    seen = {event_key(entry) for entry in merged}
    for entry in setup_change_log(incoming):
        key = event_key(entry)
        if key in seen:
            continue
        merged.append(entry)
        seen.add(key)
    return merged


_MISSING = object()


def _three_way_value(base, local, remote, path=""):
    """Merge one JSON-like value and return (value, conflicting paths)."""
    dictionaries = all(
        value is _MISSING or isinstance(value, dict)
        for value in (base, local, remote))
    if dictionaries and any(isinstance(value, dict)
                            for value in (base, local, remote)):
        base_dict = {} if base is _MISSING else base
        local_dict = {} if local is _MISSING else local
        remote_dict = {} if remote is _MISSING else remote
        merged = {}
        conflicts = []
        for key in sorted(set(base_dict) | set(local_dict) | set(remote_dict)):
            child_path = f"{path}.{key}" if path else str(key)
            value, child_conflicts = _three_way_value(
                base_dict.get(key, _MISSING),
                local_dict.get(key, _MISSING),
                remote_dict.get(key, _MISSING),
                child_path,
            )
            if value is not _MISSING:
                merged[key] = value
            conflicts.extend(child_conflicts)
        return merged, conflicts

    local_changed = local != base
    remote_changed = remote != base
    if local_changed and remote_changed and local != remote:
        value = _MISSING if remote is _MISSING else deepcopy(remote)
        return value, [path]
    if local_changed:
        value = _MISSING if local is _MISSING else deepcopy(local)
        return value, []
    value = _MISSING if remote is _MISSING else deepcopy(remote)
    return value, []


def merge_setup_changes(
        base: Setup, local: Setup, remote: Setup) -> tuple[Setup, tuple[str, ...]]:
    """Three-way merge concurrent setup edits without silently overwriting.

    Changes to different fields merge automatically. If both people changed the
    same field differently, the live Supabase value wins in the returned object
    and the field is reported as a conflict so callers can block the write.
    """
    merged = deepcopy(remote)
    conflicts = []
    for attr in (
            "session_id", "drive_folder_id", "drive_folder_path", "driver_id",
            "chassis_id", "name", "is_baseline", "track", "notes"):
        value, attr_conflicts = _three_way_value(
            getattr(base, attr), getattr(local, attr), getattr(remote, attr), attr)
        setattr(merged, attr, value)
        conflicts.extend(attr_conflicts)

    base_data = dict(base.data or {})
    local_data = dict(local.data or {})
    remote_data = dict(remote.data or {})
    for data in (base_data, local_data, remote_data):
        data.pop(_SETUP_HISTORY_KEY, None)
    merged_data, data_conflicts = _three_way_value(
        base_data, local_data, remote_data, "extra_fields")
    conflicts.extend(data_conflicts)
    history = _merge_setup_history(remote, local)
    if history:
        merged_data[_SETUP_HISTORY_KEY] = history
    merged.data = merged_data
    merged.created_by = remote.created_by
    merged.created_at = remote.created_at
    merged.updated_at = remote.updated_at
    if merged.driver_id == local.driver_id:
        merged.driver_name = local.driver_name
    if merged.chassis_id == local.chassis_id:
        merged.chassis_name = local.chassis_name
    return merged, tuple(conflicts)

def _track_scope_key(track: Optional[str]) -> Optional[str]:
    """Normalize a baseline track for comparisons and legacy rows."""
    key = (track or "").strip().casefold()
    return key or None


def fetch_setups_by_folder(drive_folder_id: str) -> List[Setup]:
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("drive_folder_id", drive_folder_id)
           .order("created_at")
           .execute())
    return [Setup.from_dict(r) for r in (res.data or [])]


def fetch_setup(setup_id: str) -> Optional[Setup]:
    """Fetch one setup for conflict-safe offline synchronization."""
    if not setup_id:
        return None
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("id", setup_id)
           .limit(1)
           .execute())
    rows = res.data or []
    return Setup.from_dict(rows[0]) if rows else None


def update_setup_folder_path(drive_folder_id: str, drive_folder_path: str) -> None:
    """Keep setup breadcrumbs synchronized after a Drive session is renamed."""
    if not drive_folder_id:
        raise ValueError("Drive folder ID is required")
    (get_client().table("setups")
     .update({"drive_folder_path": drive_folder_path})
     .eq("drive_folder_id", drive_folder_id)
     .execute())


def fetch_all_setups() -> List[Setup]:
    """Every non-baseline setup across all sessions (for the database view)."""
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("is_baseline", False)
           .order("created_at", desc=True)
           .execute())
    return [Setup.from_dict(r) for r in (res.data or [])]


def fetch_setup_catalog() -> tuple[List[Setup], List[Setup]]:
    """Return (baselines, saved setups) with one database round trip."""
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .order("created_at", desc=True)
           .execute())
    setups = [Setup.from_dict(row) for row in (res.data or [])]
    return (
        [setup for setup in setups if setup.is_baseline],
        [setup for setup in setups if not setup.is_baseline],
    )


def fetch_baseline_for_chassis(chassis_id: str) -> Optional[Setup]:
    """Most recent global (all-track) baseline for a chassis."""
    if not chassis_id:
        return None
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("is_baseline", True)
           .eq("chassis_id", chassis_id)
           .order("updated_at", desc=True)
           .execute())
    return next((Setup.from_dict(row) for row in (res.data or [])
                 if _track_scope_key(row.get("track")) is None), None)


def fetch_setup_folder_counts() -> dict:
    """Map drive_folder_id -> number of setups (for explorer badges)."""
    counts, _summaries = fetch_setup_folder_overview()
    return counts


def fetch_setup_folder_overview() -> tuple[dict, dict]:
    """Return setup counts and identity summaries grouped by Drive folder.

    The Sessions homepage uses this as its source of truth instead of guessing
    the kart identity from telemetry filenames or the legacy roster.
    """
    res = (get_client().table("setups")
           .select(
               "drive_folder_id,extra_fields,drivers(name),chassis(name)")
           .eq("is_baseline", False)
           .execute())
    counts: dict = {}
    summaries: dict = {}
    for r in (res.data or []):
        fid = r.get("drive_folder_id")
        if fid:
            counts[fid] = counts.get(fid, 0) + 1
            summaries.setdefault(fid, []).append(Setup.from_dict(r))
    return counts, summaries


def fetch_baselines() -> List[Setup]:
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("is_baseline", True)
           .order("track")
           .execute())
    return [Setup.from_dict(r) for r in (res.data or [])]


def fetch_baseline_for_track(track: str) -> Optional[Setup]:
    track_key = _track_scope_key(track)
    if track_key is None:
        return None
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("is_baseline", True)
           .is_("chassis_id", "null")
           .order("updated_at", desc=True)
           .execute())
    return next((Setup.from_dict(row) for row in (res.data or [])
                 if _track_scope_key(row.get("track")) == track_key), None)


def fetch_baseline_for_chassis_track(chassis_id: str, track: str) -> Optional[Setup]:
    """The baseline for a specific kart at a specific track (the merged baseline)."""
    track_key = _track_scope_key(track)
    if not chassis_id or track_key is None:
        return None
    res = (get_client().table("setups")
           .select("*, drivers(name), chassis(name)")
           .eq("is_baseline", True)
           .eq("chassis_id", chassis_id)
           .order("updated_at", desc=True)
           .execute())
    return next((Setup.from_dict(row) for row in (res.data or [])
                 if _track_scope_key(row.get("track")) == track_key), None)


def fetch_conflicting_baseline(
        chassis_id: Optional[str], track: Optional[str],
        exclude_id: Optional[str] = None) -> Optional[Setup]:
    """Find another baseline with the exact same kart/track applicability."""
    track_key = _track_scope_key(track)
    if not chassis_id and track_key is None:
        return None
    query = (get_client().table("setups")
             .select("*, drivers(name), chassis(name)")
             .eq("is_baseline", True))
    query = (query.eq("chassis_id", chassis_id) if chassis_id
             else query.is_("chassis_id", "null"))
    if exclude_id:
        query = query.neq("id", exclude_id)
    res = query.order("updated_at", desc=True).execute()
    return next((Setup.from_dict(row) for row in (res.data or [])
                 if _track_scope_key(row.get("track")) == track_key), None)


def create_setup(setup: Setup) -> Setup:
    setup.track = (setup.track or "").strip() or None
    if not setup_change_log(setup):
        record_setup_change(setup, None)
    elif not setup.created_by:
        user = google_drive_client.audit_user_or_none()
        setup.created_by = (
            user.audit_label if user else _PENDING_SETUP_ACTOR)
    res = get_client().table("setups").insert(setup.to_dict()).execute()
    return Setup.from_dict(res.data[0])


def update_setup(setup: Setup, base: Setup | None = None) -> Setup:
    """Optimistically update a setup, auto-merging non-overlapping edits."""
    setup.track = (setup.track or "").strip() or None
    original = deepcopy(setup)
    base = deepcopy(base) if base is not None else None
    merged_concurrently = False
    for _attempt in range(3):
        current = fetch_setup(setup.id)
        if current is None:
            raise RuntimeError("This setup no longer exists.")
        candidate = deepcopy(original)
        expected = current.updated_at
        if base is not None and current.updated_at != base.updated_at:
            candidate, conflicts = merge_setup_changes(base, original, current)
            if conflicts:
                raise ConcurrentEditError(conflicts)
            merged_concurrently = True
        elif (base is None and original.updated_at and current.updated_at
              and current.updated_at != original.updated_at):
            raise ConcurrentEditError(("unknown fields",))

        incoming_ids = {
            entry.get("id") for entry in setup_change_log(candidate)
            if entry.get("id")
        }
        current_ids = {
            entry.get("id") for entry in setup_change_log(current)
            if entry.get("id")
        }
        if (_setup_changes(current, candidate)
                and not (incoming_ids - current_ids)):
            record_setup_change(candidate, current)
        candidate.created_by = current.created_by
        candidate.data = dict(candidate.data or {})
        candidate.data[_SETUP_HISTORY_KEY] = _merge_setup_history(
            current, candidate)
        query = (get_client().table("setups")
                 .update(candidate.to_dict(include_nulls=True))
                 .eq("id", candidate.id))
        query = (query.eq("updated_at", expected) if expected
                 else query.is_("updated_at", "null"))
        res = query.execute()
        if res.data:
            saved = Setup.from_dict(res.data[0])
            saved.driver_name = saved.driver_name or candidate.driver_name
            saved.chassis_name = saved.chassis_name or candidate.chassis_name
            saved._concurrent_merged = merged_concurrently
            return saved
        # Someone saved in the small window after our fetch. Fetch and merge
        # again; the conditional update guarantees we never overwrite them.
    raise ConcurrentEditError(("setup changed repeatedly during save",))


def delete_setup(setup_id: str) -> None:
    get_client().table("setups").delete().eq("id", setup_id).execute()


def delete_setups_by_folder(drive_folder_id: str) -> None:
    """Delete every setup linked to a Drive session folder."""
    (get_client().table("setups")
     .delete()
     .eq("drive_folder_id", drive_folder_id)
     .execute())


# ── Tracks ────────────────────────────────────────────────────────────────────

def fetch_tracks() -> List[Track]:
    res = get_client().table("tracks").select("*").order("name").execute()
    return [Track.from_dict(r) for r in (res.data or [])]


def create_track(track: Track) -> Track:
    res = get_client().table("tracks").insert(track.to_dict()).execute()
    return Track.from_dict(res.data[0])


def delete_track(track_id: str) -> None:
    get_client().table("tracks").delete().eq("id", track_id).execute()


# ── App docs (shared key/value JSON, e.g. the Cheat Sheet) ────────────────────

def fetch_app_doc(key: str) -> Optional[dict]:
    """Return the shared JSON document for `key`, or None if missing.

    Returns None (rather than raising) when the table doesn't exist yet or the
    row hasn't been created, so callers can fall back to built-in defaults.
    """
    try:
        res = (get_client().table("app_docs")
               .select("data")
               .eq("key", key)
               .limit(1)
               .execute())
        rows = res.data or []
        if rows:
            return rows[0].get("data")
    except Exception:
        return None
    return None


def save_app_doc(key: str, data: dict) -> None:
    """Upsert the shared JSON document for `key` (pushes to the whole team)."""
    payload = {
        "key": key,
        "data": data,
        "updated_by": google_drive_client.audit_identity(),
    }
    get_client().table("app_docs").upsert(payload, on_conflict="key").execute()
