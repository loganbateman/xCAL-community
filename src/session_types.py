"""Shared session classifications, cached locally and stored by stable folder ID."""
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
import json
import re

import config
import database.client as db
from utils import google_drive_client

TYPES = {"practice": "Practice", "qualifying": "Qualifying", "race": "Race"}
PREFIX = "session_type:"
CACHE_PATH = config.DATA_DIR / "session_types.json"
_lock = RLock()


def cached_types():
    try:
        value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return {key: kind for key, kind in value.items() if kind in TYPES}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _cache(values):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_PATH.parent,
                            prefix="session-types-", suffix=".tmp", delete=False) as stream:
        path = Path(stream.name)
        json.dump(values, stream)
    try:
        path.replace(CACHE_PATH)
    finally:
        path.unlink(missing_ok=True)


def classify(session_id, name="", path="", types=None):
    values = cached_types() if types is None else types
    if values.get(session_id) in TYPES:
        return values[session_id]
    # Inspect the leaf name first so a parent's race title does not classify
    # a nested practice or qualifying session as race day.
    text = (name or path.split(" / ")[-1]).casefold()
    if re.search(r"\bqual(?:ifying|ification|ify)?\b", text):
        return "qualifying"
    if re.search(r"\b(practice|testing|test)\b", text):
        return "practice"
    if re.search(r"\brace\b", text):
        return "race"
    return "practice"


def refresh():
    rows = (db.get_client().table("app_docs").select("key,data")
            .like("key", PREFIX + "%").execute().data or [])
    values = {}
    for row in rows:
        key, data = row.get("key", ""), row.get("data")
        if key.startswith(PREFIX) and isinstance(data, dict) and data.get("type") in TYPES:
            values[key[len(PREFIX):]] = data["type"]
    with _lock:
        _cache(values)
    return values


def save(session_id, kind):
    if not session_id or kind not in TYPES:
        raise ValueError("Choose Practice, Qualifying, or Race for a saved session.")
    client = db.get_client()
    key = PREFIX + str(session_id)
    rows = client.table("app_docs").select("data,updated_at").eq("key", key).limit(1).execute().data or []
    payload = {"data": {"schema": 1, "type": kind},
               "updated_by": google_drive_client.audit_identity()}
    if rows:
        query = client.table("app_docs").update(payload).eq("key", key)
        stamp = rows[0].get("updated_at")
        query = query.eq("updated_at", stamp) if stamp else query.is_("updated_at", "null")
        result = query.execute().data or []
    else:
        result = client.table("app_docs").insert({"key": key, **payload}).execute().data or []
    if not result:
        raise RuntimeError("Session type changed while saving. Reopen the session and try again.")
    with _lock:
        values = cached_types()
        values[str(session_id)] = kind
        _cache(values)
    return kind
