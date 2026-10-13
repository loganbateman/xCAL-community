"""
Local SQLite cache of the Drive session tree.

Walking Google Drive on every open is slow (several seconds). This caches the
walked session folders + their .xrk files to a local SQLite file next to the app,
so the UI can render instantly from disk and refresh Drive in the background.

Public API:
    load_sessions()  -> (list[SessionFolder], age_seconds|None)   # instant, from cache
    save_sessions(sessions)                                       # after a Drive walk
    cache_age_seconds() -> float | None
"""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path

import config
from utils.google_drive_client import SessionFolder, DriveNode

# Keep navigation state with user data. The install directory is replaceable by
# the updater, while a user's downloaded/offline workflow must survive it.
_DB_PATH = config.DATA_DIR / "xcal_cache.db"
_META_KEY = "sessions_saved_at"
_COUNTS_META_KEY = "setup_counts_saved_at"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS session_cache (
            id      TEXT PRIMARY KEY,
            name    TEXT,
            path    TEXT,
            season  TEXT,
            xrks    TEXT
        )""")
    con.execute("CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_count_cache (
            folder_id TEXT PRIMARY KEY,
            count     INTEGER NOT NULL
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS setup_summary_cache (
            folder_id TEXT PRIMARY KEY,
            summary   TEXT NOT NULL
        )""")
    return con


def save_sessions(sessions) -> None:
    try:
        con = _conn()
        with con:
            con.execute("DELETE FROM session_cache")
            con.executemany(
                "INSERT OR REPLACE INTO session_cache(id,name,path,season,xrks) "
                "VALUES(?,?,?,?,?)",
                [(s.id, s.name, s.path, s.season,
                  json.dumps([{"id": x.id, "name": x.name,
                               "is_folder": x.is_folder, "size": x.size}
                              for x in s.xrks]))
                 for s in sessions],
            )
            con.execute("INSERT OR REPLACE INTO cache_meta(key,value) VALUES(?,?)",
                        (_META_KEY, str(time.time())))
        con.close()
    except Exception:
        pass  # cache is best-effort; never block the app on it


def load_sessions():
    """Return (sessions, age_seconds) or ([], None) if no/failed cache."""
    if not _DB_PATH.exists():
        return [], None
    try:
        con = _conn()
        rows = con.execute(
            "SELECT id,name,path,season,xrks FROM session_cache").fetchall()
        meta = con.execute("SELECT value FROM cache_meta WHERE key=?",
                           (_META_KEY,)).fetchone()
        con.close()
    except Exception:
        return [], None

    sessions = []
    for rid, name, path, season, xrks_json in rows:
        try:
            xrks = tuple(
                DriveNode(id=x["id"], name=x["name"],
                          is_folder=x.get("is_folder", False), size=x.get("size"))
                for x in json.loads(xrks_json or "[]")
            )
        except Exception:
            xrks = tuple()
        sessions.append(SessionFolder(id=rid, name=name, path=path,
                                      season=season, xrks=xrks))
    age = (time.time() - float(meta[0])) if meta else None
    return sessions, age


def save_setup_counts(counts: dict) -> None:
    """Cache per-session setup badges alongside the Drive session snapshot."""
    try:
        con = _conn()
        with con:
            con.execute("DELETE FROM setup_count_cache")
            con.executemany(
                "INSERT INTO setup_count_cache(folder_id,count) VALUES(?,?)",
                [(str(folder_id), int(count))
                 for folder_id, count in (counts or {}).items()],
            )
            con.execute(
                "INSERT OR REPLACE INTO cache_meta(key,value) VALUES(?,?)",
                (_COUNTS_META_KEY, str(time.time())),
            )
        con.close()
    except Exception:
        pass


def load_setup_counts() -> tuple[dict | None, float | None]:
    """Return (cached setup counts, age), or (None, None) when unavailable."""
    if not _DB_PATH.exists():
        return None, None
    try:
        con = _conn()
        meta = con.execute(
            "SELECT value FROM cache_meta WHERE key=?",
            (_COUNTS_META_KEY,),
        ).fetchone()
        if not meta:
            con.close()
            return None, None
        rows = con.execute(
            "SELECT folder_id,count FROM setup_count_cache").fetchall()
        con.close()
        counts = {folder_id: int(count) for folder_id, count in rows}
        return counts, time.time() - float(meta[0])
    except Exception:
        return None, None


def invalidate_setup_counts() -> None:
    """Mark setup badges stale after a known setup mutation."""
    if not _DB_PATH.exists():
        return
    try:
        con = _conn()
        with con:
            con.execute(
                "DELETE FROM cache_meta WHERE key=?", (_COUNTS_META_KEY,))
        con.close()
    except Exception:
        pass


def save_setup_summaries(summaries: dict) -> None:
    """Cache already-formatted, setup-derived identity labels per session."""
    try:
        con = _conn()
        with con:
            con.execute("DELETE FROM setup_summary_cache")
            con.executemany(
                "INSERT INTO setup_summary_cache(folder_id,summary) VALUES(?,?)",
                [(str(folder_id), json.dumps(list(labels or [])))
                 for folder_id, labels in (summaries or {}).items()],
            )
        con.close()
    except Exception:
        pass


def load_setup_summaries() -> dict:
    """Load cached setup-derived identity labels, or an empty mapping."""
    if not _DB_PATH.exists():
        return {}
    try:
        con = _conn()
        rows = con.execute(
            "SELECT folder_id,summary FROM setup_summary_cache").fetchall()
        con.close()
        return {folder_id: list(json.loads(summary or "[]"))
                for folder_id, summary in rows}
    except Exception:
        return {}


def cache_age_seconds():
    _, age = load_sessions()
    return age


def clear() -> None:
    try:
        Path(_DB_PATH).unlink(missing_ok=True)
    except Exception:
        pass
