import database.client as db
from database.drive_store import DriveStore
import maintenance_store
import session_types


def test_schedule_and_session_types_save_offline_in_community_catalog(tmp_path, monkeypatch):
    catalog = DriveStore(tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(db, "get_client", lambda: catalog)
    monkeypatch.setattr(maintenance_store, "CACHE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr(session_types, "CACHE_PATH", tmp_path / "types.json")
    monkeypatch.setattr(maintenance_store.google_drive_client, "audit_identity", lambda: "Local test editor")
    snapshot = maintenance_store.load_snapshot()
    assert snapshot.available
    policy = snapshot.policy
    policy["items"][-1]["by_session_type"] = {"race": {"enabled": True, "every": 1, "unit": "sessions"}}
    saved = maintenance_store.save_policy(policy, snapshot)
    session_types.save("local-session", "race")
    # Reopen durable storage: no transport, network connection or original-team
    # credentials are needed to edit either setting in Community.
    reopened = DriveStore(catalog.path)
    monkeypatch.setattr(db, "get_client", lambda: reopened)
    assert maintenance_store.load_snapshot().policy == saved.policy
    session_types.CACHE_PATH.unlink()
    assert session_types.refresh() == {"local-session": "race"}
    assert session_types.classify("local-session", "Practice") == "race"
