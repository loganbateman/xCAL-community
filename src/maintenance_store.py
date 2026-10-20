"""Cached, guarded team maintenance settings on the existing team catalog app_docs."""
from dataclasses import dataclass
from pathlib import Path
import json
from tempfile import NamedTemporaryFile

import config
import database.client as db
import maintenance
from utils import google_drive_client

DOC_KEY = "maintenance_config"
CACHE_PATH = config.DATA_DIR / "maintenance_config_cache.json"


class PolicyConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicySnapshot:
    policy: dict
    updated_at: str | None = None
    exists: bool = False
    available: bool = False


def cached_snapshot():
    try:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return PolicySnapshot(maintenance.normalize_policy(cached["policy"]), cached.get("updated_at"), cached.get("exists", False))
    except (OSError, ValueError, KeyError, TypeError):
        return PolicySnapshot(maintenance.default_policy())


def _cache(snapshot):
    temporary = None
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_PATH.parent, prefix="maintenance-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"policy": snapshot.policy, "updated_at": snapshot.updated_at, "exists": snapshot.exists}, stream)
        temporary.replace(CACHE_PATH)
    except OSError:
        pass  # A successful cloud save must not be reported as failed.
    finally:
        if temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_snapshot():
    try:
        result = db.get_client().table("app_docs").select("data,updated_at").eq("key", DOC_KEY).limit(1).execute()
        rows = result.data or []
        row = rows[0] if rows else None
        snapshot = PolicySnapshot(maintenance.normalize_policy(row["data"] if row else None),
                                  row.get("updated_at") if row else None, bool(row), True)
        _cache(snapshot)
        return snapshot
    except Exception:
        return cached_snapshot()


def save_policy(policy, expected):
    policy = maintenance.normalize_policy(policy)
    if not expected.available:
        raise RuntimeError("Connect to team catalog before changing the shared schedule. Checklist ticks still save offline with the setup.")
    payload = {"data": policy, "updated_by": google_drive_client.audit_identity()}
    if expected.exists:
        query = db.get_client().table("app_docs").update(payload).eq("key", DOC_KEY)
        query = query.eq("updated_at", expected.updated_at) if expected.updated_at else query.is_("updated_at", "null")
        rows = query.execute().data or []
        if not rows:
            raise PolicyConflict("Someone changed the maintenance schedule. Reopen Settings to compare the latest version; your form has not been discarded.")
    else:
        try:
            rows = db.get_client().table("app_docs").insert({"key": DOC_KEY, **payload}).execute().data or []
        except Exception as exc:
            latest = load_snapshot()
            if latest.exists:
                raise PolicyConflict("A maintenance schedule was created while you were editing. Reload it before saving.") from exc
            raise
        if not rows:
            raise RuntimeError("team catalog did not confirm the schedule save.")
    snapshot = PolicySnapshot(policy, rows[0].get("updated_at"), True, True)
    _cache(snapshot)
    return snapshot
