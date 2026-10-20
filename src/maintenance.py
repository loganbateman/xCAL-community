"""Configurable pre-session checks and per-kart maintenance intervals.

Sessions count once per kart, regardless of duplicate setups or number of runs.
Completion records live in the setup JSON and use the existing safe offline save
and concurrent-merge path. No work is marked complete automatically.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import re
import session_types

CHECKS_KEY = "maintenance_checks"
DEFAULT_START = "1970-01-01"
DEFAULT_START_NAME = ""


def default_policy():
    basics = [
        ("track-width", "Track widths set; hubs and fasteners secured"),
        ("wheels", "Wheels mounted; wheel fasteners checked"),
        ("chain", "Chain aligned; tension and lubrication checked"),
        ("motor-mount", "Motor-mount butterflies and locking hardware secured"),
        ("steering", "Steering, tie rods, and mounting hardware checked"),
        ("controls", "Brake function and throttle return checked"),
    ]
    items = [{"id": key, "label": label, "kind": "check", "enabled": True}
             for key, label in basics]
    items.extend([
        {"id": "replace-wheel-nuts", "label": "Replace wheel lug nuts (team interval)",
         "kind": "interval", "unit": "sessions", "every": 5, "enabled": True},
        {"id": "chain-wear", "label": "Inspect chain and sprockets for wear",
         "kind": "interval", "unit": "sessions", "every": 5, "enabled": True},
    ])
    return {"schema": 1, "start_date": DEFAULT_START, "start_session_id": "",
            "start_session_name": DEFAULT_START_NAME, "items": items}


def normalize_policy(value):
    if value is None:
        return default_policy()
    if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("items"), list):
        raise ValueError("Unsupported maintenance configuration.")
    try:
        start_date = date.fromisoformat(value["start_date"]).isoformat()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Choose a valid maintenance start date.") from exc
    if len(value["items"]) > 100:
        raise ValueError("Use no more than 100 checklist/interval items.")
    items, seen = [], set()
    for raw in value["items"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid maintenance item.")
        key, label = raw.get("id"), raw.get("label")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) or key in seen:
            raise ValueError("Maintenance items need unique stable IDs.")
        if not isinstance(label, str) or not label.strip() or len(label) > 160:
            raise ValueError("Checklist labels must have 1–160 characters.")
        if raw.get("kind") not in ("check", "interval") or not isinstance(raw.get("enabled", True), bool):
            raise ValueError("Choose a basic check or interval item.")
        item = {"id": key, "label": label.strip(), "kind": raw["kind"], "enabled": raw.get("enabled", True)}
        if item["kind"] == "interval":
            every = raw.get("every")
            if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 10000:
                raise ValueError("Intervals must be whole numbers between 1 and 10,000.")
            if raw.get("unit") not in ("sessions", "days"):
                raise ValueError("Intervals can count sessions or calendar days.")
            item.update(every=every, unit=raw["unit"])
        overrides = raw.get("by_session_type", {})
        if not isinstance(overrides, dict) or set(overrides) - session_types.TYPES.keys():
            raise ValueError("Choose valid session types for maintenance overrides.")
        if overrides:
            item["by_session_type"] = {}
            for kind, rule in overrides.items():
                if not isinstance(rule, dict) or not isinstance(rule.get("enabled", True), bool):
                    raise ValueError("Invalid session maintenance rule.")
                if rule.get("enabled", True):
                    every = rule.get("every")
                    if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 10000:
                        raise ValueError("Session intervals must be between 1 and 10,000.")
                    if rule.get("unit") not in ("sessions", "days"):
                        raise ValueError("Session intervals can count sessions or days.")
                    item["by_session_type"][kind] = {"enabled": True, "every": every, "unit": rule["unit"]}
                else:
                    item["by_session_type"][kind] = {"enabled": False}
        items.append(item)
        seen.add(key)
    return {"schema": 1, "start_date": start_date,
            "start_session_id": str(value.get("start_session_id") or ""),
            "start_session_name": str(value.get("start_session_name") or ""), "items": items}


def kart_key(setup):
    refs = (setup.data or {}).get("_identity_part_revisions") or {}
    kart = refs.get("chassis") or {}
    if kart.get("item_id"):
        return "part:" + str(kart["item_id"])
    if setup.chassis_id:
        return "chassis:" + str(setup.chassis_id)
    name = (setup.chassis_name or "").strip().casefold()
    return "name:" + name if name else None


def _kart_aliases(setups):
    """Bridge legacy chassis/name rows only when the identity is unambiguous."""
    names = {}
    for setup in setups:
        name = (setup.chassis_name or "").strip().casefold()
        if not name:
            continue
        keys = names.setdefault(name, set())
        keys.add(kart_key(setup))
        if setup.chassis_id:
            keys.add("chassis:" + str(setup.chassis_id))
    aliases = {}
    for name, keys in names.items():
        parts = {key for key in keys if key.startswith("part:")}
        chassis = {key for key in keys if key.startswith("chassis:")}
        if len(parts) <= 1 and len(chassis) <= 1:
            canonical = next(iter(parts or chassis), "name:" + name)
            for key in keys | {"name:" + name}:
                aliases.setdefault(key, set()).add(canonical)
    return {key: next(iter(values)) for key, values in aliases.items() if len(values) == 1}


def _records(value):
    return value if isinstance(value, dict) else {}


def session_date(name):
    match = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", name or "")
    try:
        return date.fromisoformat(match[1]) if match else None
    except ValueError:
        return None


def record_key(kart, session_id, item_id):
    return hashlib.sha256(json_key(kart, session_id, item_id).encode()).hexdigest()


def json_key(*values):
    import json
    return json.dumps(values, ensure_ascii=False)


def set_checked(records, kart, session_id, item, checked, actor, now=None):
    if not kart or not session_id:
        raise ValueError("Choose a kart and dated session before recording checks.")
    records = deepcopy(_records(records))
    completed = now or datetime.now(timezone.utc)
    timestamp = completed.astimezone(timezone.utc).isoformat()
    key = record_key(kart, session_id, item["id"])
    records[key] = {"id": key, "kart_key": kart, "session_id": session_id,
                    "item_id": item["id"], "label": item["label"], "kind": item["kind"],
                    "checked": bool(checked), "completed_at": timestamp if checked else None,
                    "completed_date": completed.astimezone().date().isoformat() if checked else None,
                    "recorded_at": timestamp, "actor_name": actor.name if actor else "Pending Google verification",
                    "actor_email": actor.email if actor else "", "identity_pending": actor is None}
    if item["kind"] == "interval":
        records[key].update(interval=item["every"], unit=item["unit"])
    return records


@dataclass(frozen=True)
class ChecklistRow:
    item: dict
    checked: bool = False
    completed_at: str | None = None
    actor: str = ""
    due: bool = False
    overdue: int = 0
    remaining: int | None = None


@dataclass(frozen=True)
class ChecklistState:
    rows: tuple[ChecklistRow, ...]
    future: tuple[ChecklistRow, ...]
    ordinal: int
    kart: str | None
    active: bool
    message: str


def checklist(policy, current, sessions, setups, records=None, types=None):
    policy = normalize_policy(policy)
    catalog = [setup for setup in setups if not current.id or setup.id != current.id] + [current]
    aliases = _kart_aliases(catalog)
    def identity(key):
        return aliases.get(key, key)
    target_kart = identity(kart_key(current))
    if current.is_baseline or not current.drive_folder_id:
        return ChecklistState((), (), 0, target_kart, False,
                              "Maintenance is logged on dated session setups, not baseline templates.")
    if not target_kart:
        return ChecklistState((), (), 0, None, False, "Choose a kart to track its checklist and intervals.")
    records = deepcopy(_records(records if records is not None else (current.data or {}).get(CHECKS_KEY)))
    info = {}
    for session in sessions:
        day = session_date(session.name) or session_date(session.path)
        if day:
            info[session.id] = (day, session.name.casefold(), str(session.id))
    for setup in catalog:
        if setup.drive_folder_id not in info:
            day = session_date(setup.drive_folder_path)
            if day:
                info[setup.drive_folder_id] = (day, (setup.drive_folder_path or "").split(" / ")[-1].casefold(), str(setup.drive_folder_id))
    target = info.get(current.drive_folder_id)
    if target is None:
        return ChecklistState((), (), 0, target_kart, False, "A dated session is needed to calculate maintenance intervals.")
    types = session_types.cached_types() if types is None else types
    kinds = {key: session_types.classify(key, entry[1], types=types) for key, entry in info.items()}
    current_kind = kinds[current.drive_folder_id]
    start = (date.fromisoformat(policy["start_date"]), "", "")
    anchor_id = policy["start_session_id"]
    if not anchor_id:
        anchor_id = next((session.id for session in sessions if session.name == policy["start_session_name"]), "")
    if anchor_id in info:
        start = info[anchor_id]
    used = {setup.drive_folder_id for setup in catalog
            if not setup.is_baseline and identity(kart_key(setup)) == target_kart
            and setup.drive_folder_id in info and start <= info[setup.drive_folder_id] <= target}
    ordered = sorted(used, key=lambda identity: info[identity])
    ordinal = ordered.index(current.drive_folder_id) + 1 if current.drive_folder_id in ordered else 0
    if ordinal == 0:
        return ChecklistState((), (), 0, target_kart, False,
                              f"Maintenance tracking starts at {policy['start_session_name'] or policy['start_date']}.")
    # Immutable-looking completion records are merged by their stable slot and
    # most recent recorded timestamp, so old copies cannot resurrect an undo.
    history, own_records = {}, {}
    def merge_events(source, destination):
        for event in _records(source).values():
            if (isinstance(event, dict) and identity(event.get("kart_key")) == target_kart
                    and isinstance(event.get("item_id"), str) and event.get("session_id")):
                key = record_key(target_kart, event["session_id"], event["item_id"])
                if key not in destination or str(event.get("recorded_at") or "") >= str(destination[key].get("recorded_at") or ""):
                    destination[key] = event
    for setup in catalog:
        merge_events((setup.data or {}).get(CHECKS_KEY), history)
    merge_events(records, history)
    merge_events(records, own_records)
    rows, future = [], []
    for item in policy["items"]:
        if not item["enabled"]:
            continue
        override = item.get("by_session_type", {}).get(current_kind)
        if override is not None:
            if not override["enabled"]:
                continue
            item = {**item, **override, "kind": "interval", "session_type": current_kind}
        own = own_records.get(record_key(target_kart, current.drive_folder_id, item["id"]), {})
        if item["kind"] == "interval":
            own = history.get(record_key(target_kart, current.drive_folder_id, item["id"]), own)
        checked = own.get("checked") is True and own.get("kind") == item["kind"]
        if item["kind"] == "check":
            rows.append(ChecklistRow(item, checked, own.get("completed_at"), own.get("actor_name", "")))
            continue
        completions = [event for event in history.values()
                       if event.get("item_id") == item["id"] and event.get("kind") == "interval" and event.get("checked") is True
                       and event.get("session_id") in used
                       and info[event["session_id"]] <= target]
        previous = max(completions, key=lambda event: info[event["session_id"]], default=None)
        if item["unit"] == "sessions":
            counted = [key for key in ordered if override is None or kinds[key] == current_kind]
            current_ordinal = len(counted)
            previous_ordinal = sum(info[key] <= info[previous["session_id"]] for key in counted) if previous else 0
            next_due = previous_ordinal + item["every"]
            remaining = max(0, next_due - current_ordinal)
            due = current_ordinal >= next_due
            overdue = max(0, current_ordinal - next_due)
        else:
            day = start[0]
            if previous and previous.get("completed_at"):
                try:
                    day = (date.fromisoformat(previous["completed_date"]) if previous.get("completed_date")
                           else datetime.fromisoformat(previous["completed_at"]).astimezone().date())
                except (TypeError, ValueError):
                    pass
            elapsed = (target[0] - day).days
            remaining = max(0, item["every"] - elapsed)
            due = elapsed >= item["every"]
            overdue = max(0, elapsed - item["every"])
        row = ChecklistRow(item, checked, own.get("completed_at"), own.get("actor_name", ""),
                           due and not checked, overdue, remaining)
        (rows if row.due or checked else future).append(row)
    return ChecklistState(tuple(rows), tuple(future), ordinal, target_kart, True,
                          f"Session {ordinal} for this kart since {policy['start_session_name'] or policy['start_date']}. Distinct sessions count once; runs do not.")


def resolve_identities(setup, user):
    records = deepcopy(_records((setup.data or {}).get(CHECKS_KEY)))
    changed = False
    for event in records.values():
        if isinstance(event, dict) and event.get("identity_pending") is True:
            event["actor_name"], event["actor_email"] = user.name, user.email
            event.pop("identity_pending", None)
            changed = True
    if changed:
        setup.data = {**(setup.data or {}), CHECKS_KEY: records}
    return changed
