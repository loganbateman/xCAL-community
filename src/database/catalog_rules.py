"""Deterministic catalog identities shared by local writes and journal replay."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5


def catalog_event_name(event):
    """Readable queue labels; immutable event keys remain internal."""
    record = event.get("after") or event.get("before") or {}
    table = event.get("table")
    if table == "app_docs":
        key = str(event.get("key") or "")
        if key.startswith("session_type:"):
            data = record.get("data")
            kind = data.get("type") if isinstance(data, dict) else None
            kind = kind if isinstance(kind, str) else None
            label = {"practice": "Practice", "qualifying": "Qualifying", "race": "Race"}.get(kind)
            return f"Session type · {label}" if label else "Session type setting"
        return {"team_profile": "Team profile", "team_setup_fields": "Setup field settings",
                "maintenance_config": "Maintenance schedule", "team_naming": "Team naming settings",
                "cheat_sheet": "Cheat sheet"}.get(key, "Team setting")
    if record.get("name"):
        return str(record["name"])
    if table == "inventory_item_revisions":
        snapshot = record.get("snapshot")
        name = snapshot.get("name") if isinstance(snapshot, dict) else None
        return f"{name} · revision" if name else "Part revision"
    return {"setups": "Setup change", "inventory_items": "Part change",
            "inventory_movements": "Stock adjustment", "drivers": "Driver change",
            "chassis": "Kart change", "tracks": "Track change"}.get(table, "Catalog change")


def baseline_scope(record):
    if not record or not record.get("is_baseline"):
        return None
    refs = (record.get("extra_fields") or {}).get("_identity_part_revisions") or {}
    kart = (refs.get("chassis") or {}).get("item_id") or record.get("chassis_id") or ""
    track = " ".join(str(record.get("track") or "").split()).casefold()
    return str(kart), track


def baseline_id(record):
    scope = baseline_scope(record)
    return str(uuid5(NAMESPACE_URL, "xcal-baseline-v1:" + repr(scope))) if scope else None


def event_rank(event):
    value = (event.get("after") or {}).get("updated_at") or event.get("created_at") or ""
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp(), event["id"]
    except (ValueError, TypeError, OverflowError):
        return 0.0, event["id"]


def changed_leaves(before, after, path=()):
    """Yield changed dictionary paths; lists remain atomic values."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                yield path + (key,)
            else:
                yield from changed_leaves(before[key], after[key], path + (key,))
    elif before != after:
        yield path


def apply_path(target, incoming, path):
    target = deepcopy(target)
    source, destination = incoming, target
    for key in path[:-1]:
        source = source.get(key, {}) if isinstance(source, dict) else {}
        if not isinstance(destination.get(key), dict):
            destination[key] = {}
        destination = destination[key]
    key = path[-1]
    if isinstance(source, dict) and key in source:
        destination[key] = deepcopy(source[key])
    else:
        destination.pop(key, None)
    return target
