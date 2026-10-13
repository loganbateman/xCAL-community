from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database import client as db
from database.models import Setup
from utils.google_drive_client import GoogleUser


def test_setup_diff_tracks_parts_and_ignores_history():
    before = Setup(
        name="Race setup", driver_name="Alex",
        data={
            "gear_ratio": "11/82",
            "_change_log": [{"id": "old-event"}],
        })
    after = Setup(
        name="Race setup", driver_name="Alex",
        data={
            "gear_ratio": "11/80",
            "front_ride_height": "High",
            "_change_log": [],
        })

    changes = db._setup_changes(before, after)

    assert changes["gear_ratio"] == {
        "before": "11/82", "after": "11/80"}
    assert changes["front_ride_height"] == {
        "before": None, "after": "High"}
    assert "_change_log" not in changes


def test_setup_diff_avoids_telemetry_bloat_but_tracks_part_revision():
    before = Setup(data={
        "telemetry": [{"id": "large-file", "name": "run.xrk"}],
        "engine": "Senior Max",
        "_inventory_part_revisions": {
            "engine": {"item_id": "engine-1", "revision": 1,
                       "name": "Senior Max"}},
    })
    after = Setup(data={
        "telemetry": [{"id": "different-file", "name": "race.xrk"}],
        "engine": "Senior Max",
        "_inventory_part_revisions": {
            "engine": {"item_id": "engine-1", "revision": 2,
                       "name": "Senior Max"}},
    })

    changes = db._setup_changes(before, after)

    assert "telemetry" not in changes
    assert changes["engine__part_revision"]["before"]["revision"] == 1
    assert changes["engine__part_revision"]["after"]["revision"] == 2


def test_setup_change_uses_google_name_and_email(monkeypatch):
    monkeypatch.setattr(
        db.google_drive_client, "current_user",
        lambda **_kwargs: GoogleUser("Alex Racer", "alex@example.edu"))
    before = Setup(name="Race setup", data={"toe": "0"})
    after = Setup(name="Race setup", data={"toe": "2"})

    recorded = db.record_setup_change(after, before)
    entry = db.setup_change_log(after)[0]

    assert recorded is True
    assert entry["actor_name"] == "Alex Racer"
    assert entry["actor_email"] == "alex@example.edu"
    assert entry["changes"]["toe"] == {"before": "0", "after": "2"}
    assert entry["id"]
    assert entry["changed_at"]


def test_setup_change_is_saved_pending_when_google_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        db.google_drive_client, "audit_user_or_none", lambda **_kwargs: None)
    setup = Setup(name="Offline setup", data={"toe": "2"})

    recorded = db.record_setup_change(setup, None)
    entry = db.setup_change_log(setup)[0]

    assert recorded is True
    assert entry["actor_name"] == "Pending Google verification"
    assert entry["actor_email"] == ""
    assert entry["identity_pending"] is True
    assert setup.created_by == "Pending Google verification"
    assert db.setup_identity_pending(setup) is True


def test_pending_setup_identity_resolves_without_changing_event(monkeypatch):
    setup = Setup(
        created_by="Pending Google verification",
        data={"_change_log": [{
            "id": "offline-event",
            "actor_name": "Pending Google verification",
            "actor_email": "",
            "identity_pending": True,
        }]})
    monkeypatch.setattr(
        db.google_drive_client, "current_user",
        lambda **_kwargs: GoogleUser("Alex Racer", "alex@example.edu"))

    changed = db.resolve_setup_change_identities(setup)
    entry = db.setup_change_log(setup)[0]

    assert changed is True
    assert entry["id"] == "offline-event"
    assert entry["actor_name"] == "Alex Racer"
    assert entry["actor_email"] == "alex@example.edu"
    assert "identity_pending" not in entry
    assert setup.created_by == "Alex Racer <alex@example.edu>"
    assert db.setup_identity_pending(setup) is False


def test_server_and_offline_history_merge_without_duplicates():
    server = Setup(data={"_change_log": [
        {"id": "server", "actor_name": "Server Editor"}]})
    incoming = Setup(data={"_change_log": [
        {"id": "older", "actor_name": "Earlier Editor"},
        {"id": "server", "actor_name": "Server Editor"},
        {"id": "offline", "actor_name": "Offline Editor"},
    ]})

    merged = db._merge_setup_history(server, incoming)

    assert [entry["id"] for entry in merged] == [
        "server", "older", "offline"]


def test_legacy_history_merge_does_not_duplicate_identical_events():
    legacy = {
        "actor_name": "Early Editor",
        "changed_at": "2026-08-01T12:00:00+00:00",
        "changes": {"toe": {"before": "0", "after": "2"}},
    }
    server = Setup(data={"_change_log": [legacy]})
    incoming = Setup(data={"_change_log": [legacy]})

    merged = db._merge_setup_history(server, incoming)

    assert merged == [legacy]


def test_legacy_setup_without_history_remains_readable():
    setup = Setup(created_by="Legacy Name", data={"gear_ratio": "11/82"})

    assert db.setup_change_log(setup) == []


def test_latest_change_and_count_do_not_require_full_history_copy():
    setup = Setup(data={"_change_log": [
        {"id": "first", "changes": {"notes": {
            "before": None, "after": "First"}}},
        {"id": "latest", "actor_name": "Alex"},
    ]})

    latest = db.latest_setup_change(setup)

    assert db.setup_change_count(setup) == 2
    assert latest == {"id": "latest", "actor_name": "Alex"}
    latest["actor_name"] = "Changed locally"
    assert setup.data["_change_log"][-1]["actor_name"] == "Alex"


def test_concurrent_setup_edits_to_different_fields_merge():
    base = Setup(
        id="setup-1", name="Race", notes="Base",
        data={"toe": "0", "gear_ratio": "11/80"})
    local = Setup(
        id="setup-1", name="Race", notes="Local notes",
        data={"toe": "0", "gear_ratio": "11/80"})
    remote = Setup(
        id="setup-1", name="Race", notes="Base",
        data={"toe": "2", "gear_ratio": "11/80"})

    merged, conflicts = db.merge_setup_changes(base, local, remote)

    assert conflicts == ()
    assert merged.notes == "Local notes"
    assert merged.data["toe"] == "2"


def test_concurrent_setup_edits_to_same_field_are_blocked():
    base = Setup(id="setup-1", data={"toe": "0"})
    local = Setup(id="setup-1", data={"toe": "1"})
    remote = Setup(id="setup-1", data={"toe": "2"})

    merged, conflicts = db.merge_setup_changes(base, local, remote)

    assert merged.data["toe"] == "2"
    assert conflicts == ("extra_fields.toe",)
    error = db.ConcurrentEditError(conflicts)
    assert "Your changes were not overwritten" in str(error)
    assert "toe" in str(error)


def test_concurrent_nested_part_references_merge_per_field():
    base = Setup(data={"_inventory_part_revisions": {
        "axle": {"item_id": "a", "revision": 1},
        "engine": {"item_id": "e", "revision": 1},
    }})
    local = Setup(data={"_inventory_part_revisions": {
        "axle": {"item_id": "a", "revision": 2},
        "engine": {"item_id": "e", "revision": 1},
    }})
    remote = Setup(data={"_inventory_part_revisions": {
        "axle": {"item_id": "a", "revision": 1},
        "engine": {"item_id": "e", "revision": 2},
    }})

    merged, conflicts = db.merge_setup_changes(base, local, remote)

    assert conflicts == ()
    refs = merged.data["_inventory_part_revisions"]
    assert refs["axle"]["revision"] == 2
    assert refs["engine"]["revision"] == 2


def test_concurrent_merge_preserves_local_deletion_and_remote_addition():
    base = Setup(data={"old_note": "remove me"})
    local = Setup(data={})
    remote = Setup(data={"old_note": "remove me", "toe": "2"})

    merged, conflicts = db.merge_setup_changes(base, local, remote)

    assert conflicts == ()
    assert merged.data == {"toe": "2"}


def test_update_setup_conditionally_saves_auto_merged_content(monkeypatch):
    base = Setup(
        id="setup-1", name="Race", notes="Base",
        data={"toe": "0"}, updated_at="2026-10-09T10:00:00+00:00")
    local = Setup(
        id="setup-1", name="Race", notes="Local notes",
        data={"toe": "0", "_change_log": [{"id": "local-change"}]},
        updated_at=base.updated_at)
    remote = Setup(
        id="setup-1", name="Race", notes="Base",
        data={"toe": "2", "_change_log": [{"id": "remote-change"}]},
        updated_at="2026-10-09T10:01:00+00:00")
    client = MagicMock()
    query = (
        client.table.return_value.update.return_value.eq.return_value
        .eq.return_value)
    query.execute.return_value = SimpleNamespace(data=[{
        **remote.to_dict(include_nulls=True),
        "id": remote.id,
        "updated_at": "2026-10-09T10:02:00+00:00",
    }])
    monkeypatch.setattr(db, "get_client", lambda: client)
    monkeypatch.setattr(db, "fetch_setup", lambda _setup_id: remote)

    saved = db.update_setup(local, base=base)

    payload = client.table.return_value.update.call_args.args[0]
    assert payload["notes"] == "Local notes"
    assert payload["extra_fields"]["toe"] == "2"
    assert {entry["id"] for entry in payload["extra_fields"]["_change_log"]} == {
        "local-change", "remote-change"}
    client.table.return_value.update.return_value.eq.assert_called_once_with(
        "id", "setup-1")
    client.table.return_value.update.return_value.eq.return_value.eq.assert_called_once_with(
        "updated_at", remote.updated_at)
    assert saved._concurrent_merged is True
