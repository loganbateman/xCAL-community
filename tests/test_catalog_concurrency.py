from copy import deepcopy

import pytest

from database.drive_store import DriveStore
from test_drive_store import Journal


def part(store):
    return store.table("inventory_items").select().eq("id", "part").execute().data[0]


@pytest.fixture
def clients(tmp_path):
    journal = Journal()
    a, b = [DriveStore(tmp_path / f"{name}.sqlite3", journal) for name in ("a", "b")]
    a.table("inventory_items").insert({"id": "part", "name": "Axle", "notes": "original",
                                      "category": "Axles", "quantity": 4}).execute()
    a.sync()
    b.sync()
    return a, b, journal


def converge(a, b):
    a.sync()
    b.sync()
    a.sync()
    b.sync()
    assert part(a) == part(b)


def test_independent_part_edits_share_merged_revision_and_preserve_sources(clients):
    a, b, journal = clients
    a.table("inventory_items").update({"name": "Medium axle"}).eq("id", "part").execute()
    b.table("inventory_items").update({"notes": "Katie's notes"}).eq("id", "part").execute()
    converge(a, b)
    saved = part(a)
    assert saved["name"] == "Medium axle"
    assert saved["notes"] == "Katie's notes"
    assert saved["revision_id"].startswith("merge:")
    assert not a.conflicts()
    assert len(a.table("inventory_item_revisions").select().execute().data) == 4
    count = len(journal.events)
    converge(a, b)
    assert len(journal.events) == count
    restarted = DriveStore(a.path, journal)
    restarted.sync()
    assert part(restarted) == saved


def test_overlapping_part_edits_keep_both_sources_and_newest_local_change(clients, monkeypatch):
    a, b, _ = clients
    monkeypatch.setattr("database.drive_store._now", lambda: "2026-10-22T10:00:00+00:00")
    first = a.table("inventory_items").update({"notes": "first"}).eq("id", "part").execute().data[0]
    monkeypatch.setattr("database.drive_store._now", lambda: "2026-10-22T11:00:00+00:00")
    second = b.table("inventory_items").update({"notes": "second"}).eq("id", "part").execute().data[0]
    converge(b, a)
    assert part(a)["notes"] == "second"
    assert "notes" in part(a)["_merge_notice"]["overlapping_fields"]
    history = a.table("inventory_item_revisions").select().execute().data
    assert {row["id"] for row in history} >= {first["revision_id"], second["revision_id"]}
    merged_id = part(a)["revision_id"]
    a.table("inventory_items").update({"name": "Latest"}).eq("id", "part").execute()
    converge(a, b)
    assert any(row["id"] == merged_id for row in a.table("inventory_item_revisions").select().execute().data)


def test_stale_local_dialog_merges_without_overwriting_unedited_fields(clients):
    a, _, _ = clients
    base = deepcopy(part(a))
    a.table("inventory_items").update({"name": "Another editor"}).eq("id", "part").execute()
    a.save_part_edit("part", {**base, "notes": "my note"}, revision_id=base["revision_id"])
    assert part(a)["name"] == "Another editor"
    assert part(a)["notes"] == "my note"
    assert not a.conflicts()


def test_causal_edit_wins_even_with_clock_behind(clients, monkeypatch):
    a, b, _ = clients
    monkeypatch.setattr("database.drive_store._now", lambda: "2026-10-22T12:00:00+00:00")
    a.table("inventory_items").update({"notes": "earlier"}).eq("id", "part").execute()
    converge(a, b)
    monkeypatch.setattr("database.drive_store._now", lambda: "2026-10-22T10:00:00+00:00")
    b.table("inventory_items").update({"notes": "causally later"}).eq("id", "part").execute()
    converge(a, b)
    assert part(a)["notes"] == "causally later"


def test_stock_movements_do_not_rewrite_merged_definition_history(clients):
    a, b, journal = clients
    a.table("inventory_items").update({"name": "Medium"}).eq("id", "part").execute()
    b.table("inventory_items").update({"notes": "notes"}).eq("id", "part").execute()
    converge(a, b)
    identity = part(a)["revision_id"]
    before = a.table("inventory_item_revisions").select().eq("id", identity).execute().data[0]
    a.rpc("adjust_inventory_stock", {"p_item_id": "part", "p_delta": -1,
          "p_reason": "Used", "p_notes": None, "p_created_by": "editor"}).execute()
    converge(a, b)
    assert part(a)["quantity"] == 3
    assert part(a)["revision_id"] == identity
    assert a.table("inventory_item_revisions").select().eq("id", identity).execute().data[0] == before
    c = DriveStore(a.path.parent / "c.sqlite3", journal)
    c.sync()
    assert part(c) == part(a)
    assert not c.conflicts()


def test_simultaneous_baselines_have_one_identity_and_recoverable_conflict(tmp_path):
    journal = Journal()
    a, b = [DriveStore(tmp_path / f"{name}.sqlite3", journal) for name in ("a", "b")]
    payload = {"is_baseline": True, "chassis_id": "kart", "track": "Home Track"}
    left = a.table("setups").insert({**payload, "notes": "left"}).execute().data[0]
    right = b.table("setups").insert({**payload, "track": " home track ", "notes": "right"}).execute().data[0]
    assert left["id"] == right["id"]
    a.sync()
    b.sync()
    a.sync()
    assert len(a.table("setups").select().execute().data) == 1
    assert a.conflicts()
    assert len(journal.events) == 2


def test_baseline_duplicate_insert_and_scope_change_rejected(tmp_path):
    store = DriveStore(tmp_path / "a.sqlite3")
    store.table("setups").insert({"id": "a", "is_baseline": True, "chassis_id": "kart", "track": "Home"}).execute()
    with pytest.raises(ValueError, match="baseline already exists"):
        store.table("setups").insert({"id": "b", "is_baseline": True, "chassis_id": "kart", "track": " home "}).execute()
    store.table("setups").insert({"id": "b", "is_baseline": True, "chassis_id": "other", "track": "Home"}).execute()
    with pytest.raises(ValueError, match="baseline already exists"):
        store.table("setups").update({"chassis_id": "kart"}).eq("id", "b").execute()


def test_legacy_baseline_cleanup_is_explicit_stale_safe_and_preserves_history(tmp_path):
    from database.drive_store import CatalogConflict
    journal = Journal()
    a, b = [DriveStore(tmp_path / f"{name}.sqlite3", journal) for name in ("a", "b")]
    base = {"is_baseline": True, "chassis_id": "kart", "track": "Home"}
    a.table("setups").insert({**base, "id": "old-a", "notes": "left"}).execute()
    b.table("setups").insert({**base, "id": "old-b", "notes": "right"}).execute()
    a.sync()
    b.sync()
    a.sync()
    group = a.baseline_duplicates()[0]
    with pytest.raises(CatalogConflict, match="changed"):
        a.consolidate_baselines("old-a", [{**row, "notes": "stale"} for row in group])
    a.consolidate_baselines("old-b", group)
    a.sync()
    b.sync()
    assert not b.baseline_duplicates()
    rows = b.table("setups").select().execute().data
    assert len(rows) == 1 and rows[0]["notes"] == "right"
    assert {event["after"]["notes"] for event in journal.events.values() if event["after"]} >= {"left", "right"}
