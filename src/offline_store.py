"""Durable local session packages and reconnect synchronization.

This is intentionally separate from ``cache.py``. The normal UI cache is
disposable; offline session packages contain user work and must survive cache
refreshes, app restarts, and loss of connectivity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Callable
from uuid import uuid4

import config
from database.models import InventoryItem, Setup


OFFLINE_DB_PATH = config.DATA_DIR / "xcal_offline.db"
OFFLINE_ROOT = config.DATA_DIR / "offline_sessions"
_INVALID_PATH = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class OfflineSessionInfo:
    session_id: str
    name: str
    path: str
    season: str
    downloaded_at: str | None
    include_xrks: bool
    include_videos: bool
    file_count: int
    pending_count: int
    last_error: str | None


@dataclass(frozen=True)
class SyncReport:
    setups_synced: int = 0
    setup_deletions_synced: int = 0
    files_synced: int = 0
    inventory_synced: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingSyncSummary:
    """Small, UI-friendly snapshot of work still waiting for the cloud."""

    files: int = 0
    setups: int = 0
    inventory: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.files + self.setups + self.inventory


@dataclass(frozen=True)
class PendingSyncItem:
    """One user-visible operation in the cloud sync queue."""

    key: str
    name: str
    kind: str
    destination: str
    size: int | None = None
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    OFFLINE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(OFFLINE_DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS video_catalog (
            session_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(session_id,file_id)
        );
        CREATE TABLE IF NOT EXISTS offline_sessions (
            session_id       TEXT PRIMARY KEY,
            name             TEXT NOT NULL DEFAULT '',
            path             TEXT NOT NULL DEFAULT '',
            season           TEXT NOT NULL DEFAULT '',
            downloaded_at    TEXT,
            include_xrks     INTEGER NOT NULL DEFAULT 0,
            include_videos   INTEGER NOT NULL DEFAULT 0,
            last_error       TEXT
        );
        CREATE TABLE IF NOT EXISTS offline_setups (
            session_id       TEXT NOT NULL,
            cache_id         TEXT NOT NULL,
            remote_id        TEXT,
            payload          TEXT NOT NULL,
            base_updated_at  TEXT,
            pending          INTEGER NOT NULL DEFAULT 0,
            error            TEXT,
            saved_at         TEXT NOT NULL,
            PRIMARY KEY (session_id, cache_id)
        );
        CREATE INDEX IF NOT EXISTS offline_setups_session_idx
            ON offline_setups(session_id, pending);
        CREATE TABLE IF NOT EXISTS offline_setup_aliases (
            local_id         TEXT PRIMARY KEY,
            remote_id        TEXT NOT NULL,
            synced_at        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offline_setup_deletions (
            setup_id         TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL DEFAULT '',
            error            TEXT,
            recorded_at      TEXT,
            timestamp_source TEXT,
            saved_at         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offline_inventory (
            item_id          TEXT PRIMARY KEY,
            remote_id        TEXT,
            category         TEXT NOT NULL,
            payload          TEXT NOT NULL,
            base_payload     TEXT,
            base_updated_at  TEXT,
            pending          INTEGER NOT NULL DEFAULT 0,
            error            TEXT,
            saved_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS offline_inventory_category_idx
            ON offline_inventory(category);
        CREATE TABLE IF NOT EXISTS offline_inventory_aliases (
            local_id         TEXT PRIMARY KEY,
            remote_id        TEXT NOT NULL,
            remote_revision  INTEGER NOT NULL DEFAULT 1,
            synced_at        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offline_files (
            local_id         TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            drive_file_id    TEXT,
            name             TEXT NOT NULL,
            relative_path    TEXT NOT NULL,
            size             INTEGER,
            local_path       TEXT NOT NULL,
            pending          INTEGER NOT NULL DEFAULT 0,
            error            TEXT,
            saved_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS offline_files_session_idx
            ON offline_files(session_id, pending);
    """)
    # Upgrade durable stores created before offline Parts edits were supported.
    inventory_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(offline_inventory)").fetchall()
    }
    for name, definition in (
            ("remote_id", "TEXT"),
            ("base_payload", "TEXT"),
            ("base_updated_at", "TEXT"),
            ("pending", "INTEGER NOT NULL DEFAULT 0"),
            ("error", "TEXT")):
        if name not in inventory_columns:
            connection.execute(
                f"ALTER TABLE offline_inventory ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS offline_inventory_pending_idx "
        "ON offline_inventory(pending)")
    session_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(offline_sessions)").fetchall()
    }
    if "include_xrks" not in session_columns:
        # Before this option existed, every downloaded package included XRKs.
        connection.execute(
            "ALTER TABLE offline_sessions ADD COLUMN include_xrks "
            "INTEGER NOT NULL DEFAULT 1")
    file_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(offline_files)").fetchall()
    }
    for name in ("recorded_at", "timestamp_source"):
        if name not in file_columns:
            connection.execute(
                f"ALTER TABLE offline_files ADD COLUMN {name} TEXT")
    return connection


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_component(value: str, fallback="item") -> str:
    value = _INVALID_PATH.sub("_", (value or "").strip()).rstrip(". ")
    return value or fallback


def session_directory(session_id: str) -> Path:
    key = sha256(str(session_id).encode("utf-8")).hexdigest()[:20]
    return OFFLINE_ROOT / key


def _safe_destination(session_id: str, relative_path: str) -> Path:
    parts = [
        _safe_component(part)
        for part in Path(relative_path.replace("\\", "/")).parts
        if part not in ("", ".", "..", "/")
    ]
    root = (session_directory(session_id) / "files").resolve()
    destination = root.joinpath(*parts).resolve()
    if root != destination and root not in destination.parents:
        raise ValueError("Offline file path escaped its session package")
    return destination


def _setup_payload(setup: Setup) -> dict:
    payload = setup.to_dict(include_nulls=True)
    payload.update({
        "id": setup.id,
        "created_at": setup.created_at,
        "updated_at": setup.updated_at,
        "_driver_name": setup.driver_name,
        "_chassis_name": setup.chassis_name,
    })
    return payload


def _setup_from_payload(payload: dict) -> Setup:
    setup = Setup.from_dict(payload)
    setup.driver_name = payload.get("_driver_name") or setup.driver_name
    setup.chassis_name = payload.get("_chassis_name") or setup.chassis_name
    return setup


def _same_setup_content(left: Setup, right: Setup) -> bool:
    """Compare cloud-stored setup fields without server timestamps."""
    return left.to_dict(include_nulls=True) == right.to_dict(include_nulls=True)


def _inventory_payload(item: InventoryItem) -> dict:
    payload = item.to_dict(include_nulls=True)
    payload.update({
        "id": item.id,
        "revision": item.revision,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    })
    return payload


def ensure_session(session_id: str, name="", path="", season="") -> None:
    if not session_id:
        raise ValueError("A session ID is required for offline storage")
    connection = _connect()
    with connection:
        connection.execute("""
            INSERT INTO offline_sessions(session_id,name,path,season)
            VALUES(?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END,
                path=CASE WHEN excluded.path<>'' THEN excluded.path ELSE path END,
                season=CASE WHEN excluded.season<>'' THEN excluded.season ELSE season END
        """, (session_id, name or "", path or "", season or ""))
    connection.close()
    session_directory(session_id).mkdir(parents=True, exist_ok=True)


def _cache_session_row(
        connection: sqlite3.Connection, session_id: str,
        name="", path="", season="") -> None:
    connection.execute("""
        INSERT INTO offline_sessions(session_id,name,path,season)
        VALUES(?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
            name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END,
            path=CASE WHEN excluded.path<>'' THEN excluded.path ELSE path END,
            season=CASE WHEN excluded.season<>'' THEN excluded.season ELSE season END
    """, (session_id, name or "", path or "", season or ""))


def _setup_session_values(setup: Setup) -> tuple[str, str, str, str]:
    session_id = setup.drive_folder_id or (
        "__baselines__" if setup.is_baseline else "__unassigned__")
    path = setup.drive_folder_path or ""
    parts = [part.strip() for part in path.split(" / ") if part.strip()]
    name = parts[-1] if parts else (setup.track or setup.name or "Saved Setups")
    season = parts[0] if len(parts) > 1 else ""
    return session_id, name, path, season


def session_info(session_id: str) -> OfflineSessionInfo | None:
    if not OFFLINE_DB_PATH.exists() or not session_id:
        return None
    connection = _connect()
    row = connection.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM offline_files f
                 WHERE f.session_id=s.session_id) AS file_count,
               ((SELECT COUNT(*) FROM offline_files f
                  WHERE f.session_id=s.session_id AND f.pending=1) +
                (SELECT COUNT(*) FROM offline_setups u
                  WHERE u.session_id=s.session_id AND u.pending=1)) AS pending_count
          FROM offline_sessions s WHERE s.session_id=?
    """, (session_id,)).fetchone()
    connection.close()
    if not row:
        return None
    return OfflineSessionInfo(
        session_id=row["session_id"], name=row["name"], path=row["path"],
        season=row["season"], downloaded_at=row["downloaded_at"],
        include_xrks=bool(row["include_xrks"]),
        include_videos=bool(row["include_videos"]),
        file_count=int(row["file_count"] or 0),
        pending_count=int(row["pending_count"] or 0),
        last_error=row["last_error"],
    )


def list_session_folders():
    """Return every durable session package for offline navigation.

    Import the Drive-shaped models lazily so this store remains independent of
    the online client during startup. Pending-only sessions are included too:
    user work must remain reachable even if a full package download has not yet
    completed.
    """
    if not OFFLINE_DB_PATH.exists():
        return []
    from utils.google_drive_client import SessionFolder

    connection = _connect()
    rows = connection.execute("""
        SELECT session_id,name,path,season
          FROM offline_sessions
         WHERE session_id<>'__baselines__'
           AND (downloaded_at IS NOT NULL OR
                EXISTS (SELECT 1 FROM offline_files f
                         WHERE f.session_id=offline_sessions.session_id) OR
                EXISTS (SELECT 1 FROM offline_setups u
                         WHERE u.session_id=offline_sessions.session_id))
    """).fetchall()
    connection.close()
    sessions = []
    for row in rows:
        xrks = tuple(
            node for node in load_files(row["session_id"])
            if node.name.casefold().endswith(".xrk"))
        sessions.append(SessionFolder(
            id=row["session_id"], name=row["name"] or "Offline Session",
            path=row["path"] or row["name"] or "Offline Session",
            season=row["season"] or "Offline Sessions", xrks=xrks))
    return sessions


def cache_inventory(items) -> None:
    connection = _connect()
    now = _now()
    with connection:
        connection.execute("DELETE FROM offline_inventory WHERE pending=0")
        connection.executemany("""
            INSERT OR IGNORE INTO offline_inventory(
                item_id,remote_id,category,payload,base_payload,
                base_updated_at,pending,error,saved_at)
            VALUES(?,?,?,?,NULL,NULL,0,NULL,?)
        """, [
            (str(item.id), str(item.id), item.category,
             _json(_inventory_payload(item)), now)
            for item in items if item.id
        ])
    connection.close()


def load_inventory(category: str | None = None) -> list[InventoryItem]:
    if not OFFLINE_DB_PATH.exists():
        return []
    connection = _connect()
    if category:
        rows = connection.execute(
            "SELECT payload,pending FROM offline_inventory WHERE category=?",
            (category,)).fetchall()
    else:
        rows = connection.execute(
            "SELECT payload,pending FROM offline_inventory").fetchall()
    connection.close()
    items = []
    for row in rows:
        item = InventoryItem.from_dict(json.loads(row["payload"]))
        if row["pending"]:
            item._offline_pending = True
        items.append(item)
    return items


def pending_inventory(category: str | None = None) -> list[InventoryItem]:
    if not OFFLINE_DB_PATH.exists():
        return []
    connection = _connect()
    query = "SELECT payload FROM offline_inventory WHERE pending=1"
    params = ()
    if category:
        query += " AND category=?"
        params = (category,)
    rows = connection.execute(query, params).fetchall()
    connection.close()
    items = [InventoryItem.from_dict(json.loads(row["payload"])) for row in rows]
    for item in items:
        item._offline_pending = True
    return items


def merge_pending_inventory(items) -> list[InventoryItem]:
    """Overlay durable local edits on a fresh server result."""
    merged = {str(item.id): item for item in items if item.id}
    for item in pending_inventory():
        merged[str(item.id)] = item
    return list(merged.values())


def cache_inventory_item(item: InventoryItem) -> None:
    if not item.id:
        return
    connection = _connect()
    with connection:
        pending = connection.execute(
            "SELECT pending FROM offline_inventory WHERE item_id=?",
            (str(item.id),)).fetchone()
        if not pending or not pending["pending"]:
            connection.execute("""
                INSERT OR REPLACE INTO offline_inventory(
                    item_id,remote_id,category,payload,base_payload,
                    base_updated_at,pending,error,saved_at)
                VALUES(?,?,?,?,NULL,NULL,0,NULL,?)
            """, (str(item.id), str(item.id), item.category,
                  _json(_inventory_payload(item)), _now()))
    connection.close()


def _inventory_definition(item: InventoryItem) -> dict:
    payload = item.to_dict(include_nulls=True)
    for key in ("quantity", "archived_at", "created_by", "updated_by"):
        payload.pop(key, None)
    return payload


def queue_inventory_item(
        item: InventoryItem, previous: InventoryItem | None = None) -> InventoryItem:
    """Save a complete Parts edit locally for conflict-safe reconnect sync."""
    from utils import google_drive_client

    saved = InventoryItem.from_dict(_inventory_payload(item))
    user = google_drive_client.audit_user_or_none()
    actor = (user.audit_label if user is not None
             else "Pending Google verification")
    now = _now()
    if previous is None:
        saved.id = saved.id or f"offline-part-{uuid4()}"
        saved.created_by = saved.created_by or actor
        saved.created_at = saved.created_at or now
        saved.revision = 1
        remote_id = None
        base_payload = None
        base_updated_at = None
    else:
        if not previous.id:
            raise ValueError("Inventory item ID is required for an offline update")
        saved.id = previous.id
        saved.created_by = previous.created_by
        saved.created_at = previous.created_at
        definition_changed = (
            _inventory_definition(saved) != _inventory_definition(previous))
        saved.revision = int(previous.revision or 1) + int(definition_changed)
        saved.updated_by = actor if definition_changed else previous.updated_by
        remote_id = str(previous.id)
        base_payload = _json(_inventory_payload(previous))
        base_updated_at = previous.updated_at
    saved.updated_at = now

    connection = _connect()
    existing = connection.execute("""
        SELECT remote_id,base_payload,base_updated_at
          FROM offline_inventory WHERE item_id=? AND pending=1
    """, (str(saved.id),)).fetchone()
    if existing:
        remote_id = existing["remote_id"]
        base_payload = existing["base_payload"]
        base_updated_at = existing["base_updated_at"]
    with connection:
        connection.execute("""
            INSERT OR REPLACE INTO offline_inventory(
                item_id,remote_id,category,payload,base_payload,
                base_updated_at,pending,error,saved_at)
            VALUES(?,?,?,?,?,?,1,NULL,?)
        """, (
            str(saved.id), remote_id, saved.category,
            _json(_inventory_payload(saved)), base_payload,
            base_updated_at, now,
        ))
    connection.close()
    saved._offline_pending = True
    return saved


def resolve_inventory_id(item_id: str | None) -> tuple[str, int] | None:
    if not item_id or not str(item_id).startswith("offline-part-"):
        return None
    if not OFFLINE_DB_PATH.exists():
        return None
    connection = _connect()
    row = connection.execute("""
        SELECT remote_id,remote_revision FROM offline_inventory_aliases
         WHERE local_id=?
    """, (item_id,)).fetchone()
    connection.close()
    return ((row["remote_id"], int(row["remote_revision"] or 1))
            if row else None)


def cache_setups(
        session_id: str, setups, name="", path="", season="") -> None:
    """Replace one session's cloud rows while preserving pending local edits."""
    setups = list(setups or [])
    connection = _connect()
    now = _now()
    with connection:
        deleting_ids = {
            row["setup_id"] for row in connection.execute(
                "SELECT setup_id FROM offline_setup_deletions").fetchall()
        }
        if session_id != "__baselines__":
            _cache_session_row(connection, session_id, name, path, season)
        # Preserve locally edited rows. A cloud refresh must never erase them.
        connection.execute(
            "DELETE FROM offline_setups WHERE session_id=? AND pending=0",
            (session_id,))
        for setup in setups:
            if str(setup.id or "") in deleting_ids:
                continue
            cache_id = str(setup.id or f"offline-{uuid4()}")
            pending = connection.execute(
                "SELECT pending FROM offline_setups WHERE session_id=? AND cache_id=?",
                (session_id, cache_id)).fetchone()
            if pending and pending["pending"]:
                continue
            connection.execute("""
                INSERT OR REPLACE INTO offline_setups(
                    session_id,cache_id,remote_id,payload,base_updated_at,
                    pending,error,saved_at)
                VALUES(?,?,?,?,?,0,NULL,?)
            """, (
                session_id, cache_id, setup.id, _json(_setup_payload(setup)),
                setup.updated_at, now,
            ))
    connection.close()


def cache_setup_catalog(baselines, setups) -> None:
    """Durably cache the complete lightweight setup catalog.

    Setup JSON is small and essential trackside data, so unlike XRK/video files
    it is cached automatically without asking the user to download a package.
    """
    all_setups = list(baselines or []) + list(setups or [])
    connection = _connect()
    now = _now()
    with connection:
        deleting_ids = {
            row["setup_id"] for row in connection.execute(
                "SELECT setup_id FROM offline_setup_deletions").fetchall()
        }
        connection.execute("DELETE FROM offline_setups WHERE pending=0")
        for setup in all_setups:
            if str(setup.id or "") in deleting_ids:
                continue
            session_id, name, path, season = _setup_session_values(setup)
            if session_id != "__baselines__":
                _cache_session_row(
                    connection, session_id, name, path, season)
            cache_id = str(setup.id or f"offline-{uuid4()}")
            pending = connection.execute("""
                SELECT pending FROM offline_setups
                 WHERE session_id=? AND cache_id=?
            """, (session_id, cache_id)).fetchone()
            if pending and pending["pending"]:
                continue
            connection.execute("""
                INSERT OR REPLACE INTO offline_setups(
                    session_id,cache_id,remote_id,payload,base_updated_at,
                    pending,error,saved_at)
                VALUES(?,?,?,?,?,0,NULL,?)
            """, (
                session_id, cache_id, setup.id,
                _json(_setup_payload(setup)), setup.updated_at, now,
            ))
    connection.close()


def cache_setup(setup: Setup) -> None:
    """Upsert one successful setup save into the automatic offline catalog."""
    if not setup or not setup.id:
        return
    session_id, name, path, season = _setup_session_values(setup)
    connection = _connect()
    with connection:
        deleting = connection.execute(
            "SELECT 1 FROM offline_setup_deletions WHERE setup_id=?",
            (str(setup.id),),
        ).fetchone()
        if not deleting:
            if session_id != "__baselines__":
                _cache_session_row(connection, session_id, name, path, season)
            pending = connection.execute("""
                SELECT pending FROM offline_setups
                 WHERE session_id=? AND cache_id=?
            """, (session_id, str(setup.id))).fetchone()
            if not pending or not pending["pending"]:
                connection.execute("""
                    INSERT OR REPLACE INTO offline_setups(
                        session_id,cache_id,remote_id,payload,base_updated_at,
                        pending,error,saved_at)
                    VALUES(?,?,?,?,?,0,NULL,?)
                """, (
                    session_id, str(setup.id), setup.id,
                    _json(_setup_payload(setup)), setup.updated_at, _now(),
                ))
    connection.close()


def delete_cached_setup(setup_id: str | None) -> bool:
    """Remove one setup from the durable catalog by local or remote ID."""
    if not setup_id or not OFFLINE_DB_PATH.exists():
        return False
    setup_id = str(setup_id)
    connection = _connect()
    with connection:
        cursor = connection.execute(
            "DELETE FROM offline_setups WHERE cache_id=? OR remote_id=?",
            (setup_id, setup_id),
        )
        connection.execute(
            "DELETE FROM offline_setup_aliases "
            "WHERE local_id=? OR remote_id=?",
            (setup_id, setup_id),
        )
    connection.close()
    return bool(cursor.rowcount)


def queue_setup_deletion(
        setup_id: str | None, session_id: str | None = None) -> bool:
    """Hide a setup locally and durably retry its remote deletion."""
    if not setup_id:
        return False
    setup_id = str(setup_id)
    remote_id = resolve_setup_id(setup_id)
    if not remote_id and setup_id.startswith("offline-"):
        delete_cached_setup(setup_id)
        return False
    remote_id = str(remote_id or setup_id)
    connection = _connect()
    with connection:
        connection.execute("""
            INSERT OR REPLACE INTO offline_setup_deletions(
                setup_id,session_id,error,saved_at)
            VALUES(?,?,NULL,?)
        """, (remote_id, str(session_id or ""), _now()))
        connection.execute(
            "DELETE FROM offline_setups WHERE cache_id=? OR remote_id=?",
            (setup_id, remote_id),
        )
        connection.execute(
            "DELETE FROM offline_setup_aliases "
            "WHERE local_id=? OR remote_id=?",
            (setup_id, remote_id),
        )
    connection.close()
    return True


def complete_setup_deletion(setup_id: str | None) -> None:
    if not setup_id or not OFFLINE_DB_PATH.exists():
        return
    connection = _connect()
    with connection:
        connection.execute(
            "DELETE FROM offline_setup_deletions WHERE setup_id=?",
            (str(setup_id),),
        )
    connection.close()


def load_setup_catalog(include_baselines=False) -> list[Setup]:
    """Return every automatically cached setup without requiring file downloads."""
    if not OFFLINE_DB_PATH.exists():
        return []
    connection = _connect()
    query = "SELECT payload FROM offline_setups"
    params = ()
    if not include_baselines:
        query += " WHERE session_id<>?"
        params = ("__baselines__",)
    query += " ORDER BY saved_at DESC, cache_id"
    rows = connection.execute(query, params).fetchall()
    connection.close()
    return [_setup_from_payload(json.loads(row["payload"])) for row in rows]


def load_setups(session_id: str) -> list[Setup]:
    if not OFFLINE_DB_PATH.exists() or not session_id:
        return []
    connection = _connect()
    rows = connection.execute("""
        SELECT payload FROM offline_setups WHERE session_id=?
        ORDER BY saved_at, cache_id
    """, (session_id,)).fetchall()
    connection.close()
    return [_setup_from_payload(json.loads(row["payload"])) for row in rows]


def resolve_setup_id(setup_id: str | None) -> str | None:
    if not setup_id or not str(setup_id).startswith("offline-"):
        return setup_id
    if not OFFLINE_DB_PATH.exists():
        return None
    connection = _connect()
    row = connection.execute(
        "SELECT remote_id FROM offline_setup_aliases WHERE local_id=?",
        (setup_id,)).fetchone()
    connection.close()
    return row["remote_id"] if row else None


def save_pending_setup(setup: Setup) -> Setup:
    session_id = setup.drive_folder_id or "__baselines__"
    ensure_session(
        session_id,
        name=("Baseline Setups" if setup.is_baseline else "Offline Session"),
        path=setup.drive_folder_path or "")
    saved = Setup.from_dict(_setup_payload(setup))
    saved.driver_name = setup.driver_name
    saved.chassis_name = setup.chassis_name
    resolved_id = resolve_setup_id(saved.id)
    if resolved_id:
        saved.id = resolved_id
    if not saved.id:
        saved.id = f"offline-{uuid4()}"
        base_updated_at = None
    else:
        base_updated_at = setup.updated_at
    saved.updated_at = _now()
    if not saved.created_at:
        saved.created_at = saved.updated_at
    connection = _connect()
    existing = connection.execute("""
        SELECT base_updated_at FROM offline_setups
        WHERE session_id=? AND cache_id=? AND pending=1
    """, (session_id, saved.id)).fetchone()
    if existing:
        base_updated_at = existing["base_updated_at"]
    with connection:
        connection.execute("""
            INSERT OR REPLACE INTO offline_setups(
                session_id,cache_id,remote_id,payload,base_updated_at,
                pending,error,saved_at)
            VALUES(?,?,?,?,?,1,NULL,?)
        """, (
            session_id, saved.id,
            None if str(saved.id).startswith("offline-") else saved.id,
            _json(_setup_payload(saved)), base_updated_at, _now(),
        ))
    connection.close()
    saved._offline_pending = True
    return saved


def _record_remote_file(session_id: str, node, local_path: Path) -> None:
    from utils.media_timestamp import extract_recording_timestamp
    embedded = extract_recording_timestamp(local_path)
    recorded_at = (
        embedded.recorded_at
        if embedded.source in {
            "XRK logger time", "SmartyCam time", "Video metadata"}
        else node.recorded_at or embedded.recorded_at
    )
    timestamp_source = (
        embedded.source
        if embedded.source in {
            "XRK logger time", "SmartyCam time", "Video metadata"}
        else node.timestamp_source or embedded.source
    )
    connection = _connect()
    relative_path = node.relative_path or node.name
    with connection:
        connection.execute("""
            INSERT OR REPLACE INTO offline_files(
                local_id,session_id,drive_file_id,name,relative_path,size,
                local_path,pending,error,recorded_at,timestamp_source,saved_at)
            VALUES(?,?,?,?,?,?,?,0,NULL,?,?,?)
        """, (
            f"drive-{node.id}", session_id, node.id, node.name,
            relative_path, node.size, str(local_path), recorded_at,
            timestamp_source, _now(),
        ))
    connection.close()


def queue_local_file(
        session_id: str, source: str | Path, name="", path="", season=""):
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    ensure_session(session_id, name, path, season)
    local_id = f"local-{uuid4()}"
    safe_name = _safe_component(source.name, "upload.bin")
    destination = session_directory(session_id) / "uploads" / local_id / safe_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    from utils.media_timestamp import extract_recording_timestamp
    timestamp = extract_recording_timestamp(destination)
    relative_path = f"Local Uploads/{safe_name}"
    connection = _connect()
    with connection:
        connection.execute("""
            INSERT INTO offline_files(
                local_id,session_id,drive_file_id,name,relative_path,size,
                local_path,pending,error,recorded_at,timestamp_source,saved_at)
            VALUES(?,?,?,?,?,?,?,1,NULL,?,?,?)
        """, (
            local_id, session_id, None, safe_name, relative_path,
            destination.stat().st_size, str(destination),
            timestamp.recorded_at, timestamp.source, _now(),
        ))
    connection.close()
    return destination


def cache_video_catalog(session_id, nodes):
    """Save a video listing without downloading video contents."""
    connection = _connect()
    with connection:
        connection.execute("DELETE FROM video_catalog WHERE session_id=?",
                           (session_id,))
        connection.executemany(
            "INSERT INTO video_catalog VALUES(?,?,?)",
            [(session_id, node.id, _json({
                "id": node.id, "name": node.name, "is_folder": False,
                "size": node.size, "recorded_at": node.recorded_at,
                "timestamp_source": node.timestamp_source,
                "relative_path": node.relative_path,
            })) for node in nodes if not node.pending_upload])
    connection.close()


def video_cache_path(session_id, node):
    return (OFFLINE_ROOT / sha256(str(session_id).encode()).hexdigest()[:16]
            / "videos" / sha256(str(node.id).encode()).hexdigest()[:16]
            / _safe_component(node.name, "video.mp4"))


def load_video_catalog(session_id):
    from utils.google_drive_client import DriveNode
    local = {node.id: node for node in load_files(session_id) if node.is_video}
    if not OFFLINE_DB_PATH.exists():
        return list(local.values())
    connection = _connect()
    rows = connection.execute(
        "SELECT payload FROM video_catalog WHERE session_id=?",
        (session_id,)).fetchall()
    connection.close()
    for row in rows:
        node = DriveNode(**json.loads(row["payload"]))
        if node.id in local:
            continue
        path = video_cache_path(session_id, node)
        if path.is_file() and (node.size is None or path.stat().st_size == node.size):
            node = replace(node, local_path=str(path))
        local[node.id] = node
    return list(local.values())


def load_files(session_id: str, pending_only=False):
    """Return DriveNode-compatible rows backed by durable local paths."""
    if not OFFLINE_DB_PATH.exists() or not session_id:
        return []
    from utils.google_drive_client import DriveNode
    connection = _connect()
    query = "SELECT * FROM offline_files WHERE session_id=?"
    params: list = [session_id]
    if pending_only:
        query += " AND pending=1"
    rows = connection.execute(query, params).fetchall()
    connection.close()
    nodes = []
    timestamp_updates = []
    for row in rows:
        path = Path(row["local_path"])
        if not path.is_file():
            continue
        recorded_at = row["recorded_at"]
        timestamp_source = row["timestamp_source"]
        supported_media = path.suffix.casefold() in {".xrk", ".mp4", ".mov"}
        if supported_media:
            from utils.media_timestamp import (
                extract_recording_timestamp, normalize_stored_timestamp)
            timestamp = normalize_stored_timestamp(
                row["name"], recorded_at, timestamp_source)
            if not timestamp.recorded_at:
                timestamp = extract_recording_timestamp(path)
            recorded_at = timestamp.recorded_at
            timestamp_source = timestamp.source
            if (recorded_at and (
                    recorded_at != row["recorded_at"]
                    or timestamp_source != row["timestamp_source"])):
                timestamp_updates.append(
                    (recorded_at, timestamp_source, row["local_id"]))
        nodes.append(DriveNode(
            id=row["drive_file_id"] or row["local_id"],
            name=row["name"], is_folder=False, size=row["size"],
            relative_path=row["relative_path"], local_path=str(path),
            pending_upload=bool(row["pending"]),
            recorded_at=recorded_at,
            timestamp_source=timestamp_source,
        ))
    if timestamp_updates:
        connection = _connect()
        with connection:
            connection.executemany(
                "UPDATE offline_files SET recorded_at=?,timestamp_source=? "
                "WHERE local_id=?", timestamp_updates)
        connection.close()
    return nodes


def rename_local_file(session_id: str, file_id: str, new_name: str) -> bool:
    """Rename a cached/queued session file and keep its upload row consistent."""
    safe_name = _safe_component(new_name, "session-data.bin")
    connection = _connect()
    row = connection.execute(
        "SELECT * FROM offline_files WHERE session_id=? "
        "AND (local_id=? OR drive_file_id=?) LIMIT 1",
        (session_id, file_id, file_id),
    ).fetchone()
    if row is None:
        connection.close()
        return False
    source = Path(row["local_path"])
    destination = source.with_name(safe_name)
    if destination != source and destination.exists():
        connection.close()
        raise FileExistsError(f"A local file named {safe_name} already exists")
    if source.is_file() and destination != source:
        source.rename(destination)
    relative_parent = str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)
    relative_path = (
        f"{relative_parent[0]}/{safe_name}"
        if len(relative_parent) == 2 else safe_name)
    with connection:
        connection.execute(
            "UPDATE offline_files SET name=?,relative_path=?,local_path=?,"
            "size=?,saved_at=? WHERE local_id=?",
            (safe_name, relative_path, str(destination),
             destination.stat().st_size if destination.is_file() else row["size"],
             _now(), row["local_id"]),
        )
    connection.close()
    return True


def remove_local_file(session_id: str, file_id: str) -> bool:
    """Remove one cached/queued file record and its exact local copy."""
    connection = _connect()
    row = connection.execute(
        "SELECT local_id,local_path FROM offline_files WHERE session_id=? "
        "AND (local_id=? OR drive_file_id=?) LIMIT 1",
        (session_id, file_id, file_id),
    ).fetchone()
    if row is None:
        connection.close()
        return False
    local_path = Path(row["local_path"])
    with connection:
        connection.execute(
            "DELETE FROM offline_files WHERE local_id=?", (row["local_id"],))
    connection.close()
    try:
        local_path.unlink(missing_ok=True)
    except OSError:
        # The cache record is already gone. A copy held open by Race Studio
        # must not resurrect the item or make a successful Drive trash fail.
        pass
    return True


def download_session_package(
        session_id: str, name: str, path: str, season: str,
        include_xrks=True, include_videos=False,
        progress: Callable[[int], None] | None = None,
) -> OfflineSessionInfo:
    """Refresh one complete, durable offline package from cloud sources."""
    import database.client as db
    from utils import google_drive_client

    ensure_session(session_id, name, path, season)
    if progress:
        progress(1)
    try:
        setups = db.fetch_setups_by_folder(session_id)
        baselines = db.fetch_baselines()
        inventory = db.fetch_inventory_items(include_archived=True)
        files = google_drive_client.list_files_recursive(session_id)
        if not include_xrks:
            files = [node for node in files if not node.is_xrk]
        if not include_videos:
            files = [node for node in files if not node.is_video]
        cache_setups(session_id, setups)
        cache_setups("__baselines__", baselines)
        cache_inventory(inventory)

        connection = _connect()
        with connection:
            connection.execute(
                "DELETE FROM offline_files WHERE session_id=? AND pending=0",
                (session_id,))
        connection.close()

        total = max(1, len(files))
        for index, node in enumerate(files, 1):
            destination = _safe_destination(
                session_id, node.relative_path or node.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not (destination.exists() and node.size is not None
                    and destination.stat().st_size == node.size):
                partial = destination.with_name(destination.name + ".part")
                partial.unlink(missing_ok=True)
                google_drive_client.download_file(node.id, partial)
                partial.replace(destination)
            _record_remote_file(session_id, node, destination)
            if progress:
                progress(max(2, round(index / total * 98)))

        connection = _connect()
        with connection:
            connection.execute("""
                UPDATE offline_sessions
                   SET name=?,path=?,season=?,downloaded_at=?,
                       include_xrks=?,include_videos=?,last_error=NULL
                 WHERE session_id=?
            """, (
                name, path, season, _now(), int(include_xrks),
                int(include_videos), session_id))
        connection.close()
        if progress:
            progress(100)
        return session_info(session_id)
    except Exception as exc:
        connection = _connect()
        with connection:
            connection.execute(
                "UPDATE offline_sessions SET last_error=? WHERE session_id=?",
                (str(exc), session_id))
        connection.close()
        raise


def pending_count(session_id: str | None = None) -> int:
    if not OFFLINE_DB_PATH.exists():
        return 0
    connection = _connect()
    where = " WHERE session_id=?" if session_id else ""
    params = (session_id,) if session_id else ()
    files = connection.execute(
        "SELECT COUNT(*) FROM offline_files" + where +
        (" AND pending=1" if where else " WHERE pending=1"), params).fetchone()[0]
    setups = connection.execute(
        "SELECT COUNT(*) FROM offline_setups" + where +
        (" AND pending=1" if where else " WHERE pending=1"), params).fetchone()[0]
    inventory = 0
    deletion_where = " WHERE session_id=?" if session_id else ""
    deletions = connection.execute(
        "SELECT COUNT(*) FROM offline_setup_deletions" + deletion_where,
        params,
    ).fetchone()[0]
    if session_id is None:
        inventory = connection.execute(
            "SELECT COUNT(*) FROM offline_inventory WHERE pending=1").fetchone()[0]
    connection.close()
    return (int(files or 0) + int(setups or 0) + int(inventory or 0)
            + int(deletions or 0))


def _offline_pending_sync_summary() -> PendingSyncSummary:
    """Return pending counts by destination, including failed last attempts."""
    if not OFFLINE_DB_PATH.exists():
        return PendingSyncSummary()
    connection = _connect()
    files, file_errors = connection.execute(
        "SELECT COUNT(*),COUNT(CASE WHEN error IS NOT NULL THEN 1 END) "
        "FROM offline_files WHERE pending=1").fetchone()
    setups, setup_errors = connection.execute(
        "SELECT COUNT(*),COUNT(CASE WHEN error IS NOT NULL THEN 1 END) "
        "FROM offline_setups WHERE pending=1").fetchone()
    deletions, deletion_errors = connection.execute(
        "SELECT COUNT(*),COUNT(CASE WHEN error IS NOT NULL THEN 1 END) "
        "FROM offline_setup_deletions").fetchone()
    inventory, inventory_errors = connection.execute(
        "SELECT COUNT(*),COUNT(CASE WHEN error IS NOT NULL THEN 1 END) "
        "FROM offline_inventory WHERE pending=1").fetchone()
    connection.close()
    return PendingSyncSummary(
        files=int(files or 0),
        setups=int(setups or 0) + int(deletions or 0),
        inventory=int(inventory or 0),
        errors=(int(file_errors or 0) + int(setup_errors or 0)
                + int(deletion_errors or 0) + int(inventory_errors or 0)),
    )


def _offline_pending_sync_items() -> list[PendingSyncItem]:
    """Return a stable, display-ready snapshot of the pending sync queue."""
    if not OFFLINE_DB_PATH.exists():
        return []
    connection = _connect()
    files = connection.execute(
        "SELECT local_id,name,size,error FROM offline_files "
        "WHERE pending=1 ORDER BY saved_at").fetchall()
    setups = connection.execute(
        "SELECT cache_id,payload,error FROM offline_setups "
        "WHERE pending=1 ORDER BY saved_at").fetchall()
    inventory = connection.execute(
        "SELECT item_id,payload,error FROM offline_inventory "
        "WHERE pending=1 ORDER BY saved_at").fetchall()
    deletions = connection.execute(
        "SELECT setup_id,error FROM offline_setup_deletions "
        "ORDER BY saved_at").fetchall()
    connection.close()

    def payload_name(row, fallback):
        try:
            return str(json.loads(row["payload"]).get("name") or fallback)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    items = [
        PendingSyncItem(
            f"file:{row['local_id']}", row["name"], "Data / video",
            "Google Drive", row["size"], row["error"])
        for row in files
    ]
    items.extend(
        PendingSyncItem(
            f"setup:{row['cache_id']}", payload_name(row, "Setup"),
            "Setup", "Google Drive", error=row["error"])
        for row in setups
    )
    items.extend(
        PendingSyncItem(
            f"inventory:{row['item_id']}", payload_name(row, "Part"),
            "Part", "Google Drive", error=row["error"])
        for row in inventory
    )
    items.extend(
        PendingSyncItem(
            f"delete:{row['setup_id']}", "Delete setup", "Setup",
            "Google Drive", error=row["error"])
        for row in deletions
    )
    return items


def pending_sync_items() -> list[PendingSyncItem]:
    import database.client as db
    return _offline_pending_sync_items() + db.get_client().queue_items()


def pending_sync_summary() -> PendingSyncSummary:
    import database.client as db
    summary = _offline_pending_sync_summary()
    items = db.get_client().queue_items()
    return replace(summary, setups=summary.setups + len(items),
                   errors=summary.errors + sum(bool(item.error) for item in items))


def _resolved_setup_inventory_refs(setup: Setup) -> Setup:
    """Replace local Parts IDs after their queued creates reach Supabase."""
    data = dict(setup.data or {})
    changed = False
    for container_key in (
            "_inventory_part_revisions", "_identity_part_revisions"):
        container = data.get(container_key)
        if not isinstance(container, dict):
            continue
        updated = dict(container)
        for field_key, reference in container.items():
            if not isinstance(reference, dict):
                continue
            resolved = resolve_inventory_id(reference.get("item_id"))
            if not resolved:
                continue
            remote_id, remote_revision = resolved
            updated[field_key] = dict(reference)
            updated[field_key]["item_id"] = remote_id
            updated[field_key]["revision"] = remote_revision
            changed = True
        data[container_key] = updated
    if changed:
        setup.data = data
    return setup


def _discard_superseded_missing_files() -> int:
    """Drop unrecoverable queue rows replaced by a newer cloud-backed file.

    A previous upload/build can leave an empty upload directory after a later
    version of the same generated artifact was successfully stored. Matching
    session, extension, and exact byte size keeps this reconciliation narrow.
    No user file or Drive object is deleted here.
    """
    connection = _connect()
    rows = connection.execute(
        "SELECT local_id,session_id,name,size,local_path,saved_at "
        "FROM offline_files WHERE pending=1").fetchall()
    resolved = []
    for row in rows:
        if Path(row["local_path"]).is_file() or row["size"] is None:
            continue
        suffix = Path(row["name"]).suffix.casefold()
        replacements = connection.execute(
            "SELECT name FROM offline_files WHERE session_id=? AND pending=0 "
            "AND drive_file_id IS NOT NULL AND size=? AND saved_at>=?",
            (row["session_id"], row["size"], row["saved_at"]),
        ).fetchall()
        if any(Path(item["name"]).suffix.casefold() == suffix
               for item in replacements):
            resolved.append(row["local_id"])
    if resolved:
        with connection:
            connection.executemany(
                "DELETE FROM offline_files WHERE local_id=?",
                [(local_id,) for local_id in resolved],
            )
    connection.close()
    return len(resolved)


def sync_pending(progress=None) -> SyncReport:
    from qt.workers import check_interruption
    """Upload queued files and setups; retain failures for a later retry."""
    if not OFFLINE_DB_PATH.exists():
        return SyncReport()
    import database.client as db
    from utils import google_drive_client

    _discard_superseded_missing_files()
    connection = _connect()
    files = connection.execute(
        "SELECT * FROM offline_files WHERE pending=1 ORDER BY saved_at").fetchall()
    setups = connection.execute(
        "SELECT * FROM offline_setups WHERE pending=1 ORDER BY saved_at").fetchall()
    inventory = connection.execute(
        "SELECT * FROM offline_inventory WHERE pending=1 ORDER BY saved_at").fetchall()
    deletions = connection.execute(
        "SELECT * FROM offline_setup_deletions ORDER BY saved_at").fetchall()
    connection.close()
    file_count = 0
    setup_count = 0
    inventory_count = 0
    deletion_count = 0
    errors: list[str] = []

    def report(key, state, percent=None, error=None):
        if progress:
            progress({
                "key": key, "state": state, "percent": percent,
                "error": error,
            })

    for row in deletions:
        check_interruption()
        key = f"delete:{row['setup_id']}"
        report(key, "syncing")
        try:
            db.delete_setup(row["setup_id"])
            complete_setup_deletion(row["setup_id"])
            deletion_count += 1
            report(key, "complete", 100)
        except Exception as exc:
            errors.append(f"Delete setup {row['setup_id']}: {exc}")
            connection = _connect()
            with connection:
                connection.execute(
                    "UPDATE offline_setup_deletions SET error=? "
                    "WHERE setup_id=?",
                    (str(exc), row["setup_id"]),
                )
            connection.close()
            report(key, "error", error=str(exc))

    # Parts sync first so setup sheets can replace references to Parts created
    # offline with their permanent Supabase IDs in the same reconnect pass.
    for row in inventory:
        check_interruption()
        key = f"inventory:{row['item_id']}"
        report(key, "syncing")
        payload = {}
        try:
            payload = json.loads(row["payload"])
            desired = InventoryItem.from_dict(payload)
            remote_id = row["remote_id"]
            local_id = row["item_id"]
            if remote_id:
                remote = db.fetch_inventory_item(remote_id)
                if remote is None:
                    raise RuntimeError("This part no longer exists online.")
                base = row["base_updated_at"]
                if base and remote.updated_at and remote.updated_at != base:
                    raise RuntimeError(
                        "Conflict: this part changed online after the offline "
                        "copy was downloaded. Reopen it online to compare changes.")
                desired.id = remote_id
                desired.created_by = remote.created_by
                desired.created_at = remote.created_at
                definition_changed = (
                    _inventory_definition(desired)
                    != _inventory_definition(remote))
                quantity_delta = round(
                    float(desired.quantity) - float(remote.quantity), 3)
                archive_changed = bool(desired.archived_at) != bool(
                    remote.archived_at)
                saved = remote
                current_entity = remote.category in {"Karts", "Drivers", "Tracks"}
                target_entity = desired.category in {"Karts", "Drivers", "Tracks"}
                quantity_first = bool(
                    quantity_delta and target_entity and not current_entity)
                if quantity_first:
                    saved = db.adjust_inventory_stock(
                        remote_id, quantity_delta,
                        "Offline Parts quantity update")
                if definition_changed:
                    saved = db.update_inventory_item(desired)
                if quantity_delta and not quantity_first:
                    saved = db.adjust_inventory_stock(
                        remote_id, quantity_delta,
                        "Offline Parts quantity update")
                if archive_changed:
                    saved = db.archive_inventory_item(
                        remote_id, archived=bool(desired.archived_at))
            else:
                desired.id = None
                saved = db.create_inventory_item(desired)

            connection = _connect()
            with connection:
                connection.execute(
                    "DELETE FROM offline_inventory WHERE item_id=?",
                    (local_id,))
                connection.execute("""
                    INSERT OR REPLACE INTO offline_inventory(
                        item_id,remote_id,category,payload,base_payload,
                        base_updated_at,pending,error,saved_at)
                    VALUES(?,?,?,?,NULL,NULL,0,NULL,?)
                """, (
                    str(saved.id), str(saved.id), saved.category,
                    _json(_inventory_payload(saved)), _now(),
                ))
                if str(local_id).startswith("offline-part-"):
                    connection.execute("""
                        INSERT OR REPLACE INTO offline_inventory_aliases(
                            local_id,remote_id,remote_revision,synced_at)
                        VALUES(?,?,?,?)
                    """, (
                        local_id, str(saved.id), int(saved.revision or 1), _now()))
            connection.close()
            inventory_count += 1
            report(key, "complete", 100)
        except Exception as exc:
            message = f"Part {payload.get('name') or row['item_id']}: {exc}"
            errors.append(message)
            connection = _connect()
            with connection:
                connection.execute("""
                    UPDATE offline_inventory SET error=? WHERE item_id=?
                """, (str(exc), row["item_id"]))
            connection.close()
            report(key, "error", error=str(exc))

    for row in files:
        check_interruption()
        key = f"file:{row['local_id']}"
        report(key, "syncing", 0)
        try:
            path = Path(row["local_path"])
            if not path.is_file():
                raise FileNotFoundError(f"Queued file is missing: {path}")
            drive_id = google_drive_client.upload_file(
                str(path), row["session_id"],
                idempotency_key=row["local_id"],
                recorded_at=row["recorded_at"],
                timestamp_source=row["timestamp_source"],
                progress=lambda percent, item_key=key:
                report(item_key, "syncing", percent))
            connection = _connect()
            with connection:
                connection.execute("""
                    UPDATE offline_files SET drive_file_id=?,pending=0,error=NULL
                    WHERE local_id=?
                """, (drive_id, row["local_id"]))
            connection.close()
            file_count += 1
            report(key, "complete", 100)
        except Exception as exc:
            message = f"{row['name']}: {exc}"
            errors.append(message)
            connection = _connect()
            with connection:
                connection.execute(
                    "UPDATE offline_files SET error=? WHERE local_id=?",
                    (str(exc), row["local_id"]))
            connection.close()
            report(key, "error", error=str(exc))

    for row in setups:
        check_interruption()
        key = f"setup:{row['cache_id']}"
        report(key, "syncing")
        payload = {}
        try:
            payload = json.loads(row["payload"])
            setup = _resolved_setup_inventory_refs(_setup_from_payload(payload))
            # Offline saves made before any Google profile was cached carry a
            # pending marker. Resolve it before Supabase upload; on failure the
            # row stays durable and the next reconnect pass retries it.
            db.resolve_setup_change_identities(setup)
            remote_id = row["remote_id"]
            local_id = None
            if remote_id:
                remote = db.fetch_setup(remote_id)
                base = row["base_updated_at"]
                if (remote is not None and base and remote.updated_at
                        and remote.updated_at != base):
                    if _same_setup_content(setup, remote):
                        # The prior request reached Supabase but its response was
                        # lost. Treat the identical live row as confirmation.
                        saved = remote
                    else:
                        raise RuntimeError(
                            "Conflict: this setup changed online after the "
                            "offline copy was downloaded. Open it online to "
                            "compare changes.")
                else:
                    setup.id = remote_id
                    saved = db.update_setup(setup)
            else:
                local_id = setup.id
                setup.id = None
                saved = db.create_setup(setup)
            connection = _connect()
            with connection:
                connection.execute(
                    "DELETE FROM offline_setups WHERE session_id=? AND cache_id=?",
                    (row["session_id"], row["cache_id"]))
                connection.execute("""
                    INSERT OR REPLACE INTO offline_setups(
                        session_id,cache_id,remote_id,payload,base_updated_at,
                        pending,error,saved_at)
                    VALUES(?,?,?,?,?,0,NULL,?)
                """, (
                    row["session_id"], saved.id, saved.id,
                    _json(_setup_payload(saved)), saved.updated_at, _now(),
                ))
                if local_id and str(local_id).startswith("offline-"):
                    connection.execute("""
                        INSERT OR REPLACE INTO offline_setup_aliases(
                            local_id,remote_id,synced_at) VALUES(?,?,?)
                    """, (local_id, saved.id, _now()))
            connection.close()
            setup_count += 1
            report(key, "complete", 100)
        except Exception as exc:
            message = f"Setup {payload.get('name') or row['cache_id']}: {exc}"
            errors.append(message)
            connection = _connect()
            with connection:
                connection.execute("""
                    UPDATE offline_setups SET error=?
                    WHERE session_id=? AND cache_id=?
                """, (str(exc), row["session_id"], row["cache_id"]))
            connection.close()
            report(key, "error", error=str(exc))

    return SyncReport(
        setups_synced=setup_count,
        setup_deletions_synced=deletion_count,
        files_synced=file_count,
        inventory_synced=inventory_count,
        errors=tuple(errors),
    )
