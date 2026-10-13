from copy import deepcopy
import json
import pytest

from database.drive_store import DriveStore, CatalogConflict, merge


class Journal:
    def __init__(self):
        self.events = {}
        self.fail_after_save = False

    def append(self, event):
        self.events.setdefault(event["id"], deepcopy(event))
        if self.fail_after_save:
            self.fail_after_save = False
            raise OSError("Response lost after upload")

    def read_unknown(self, known):
        return [deepcopy(event) for key, event in self.events.items() if key not in known]


@pytest.fixture
def team(tmp_path):
    journal = Journal()
    first = DriveStore(tmp_path / "first.sqlite3", journal)
    second = DriveStore(tmp_path / "second.sqlite3", journal)
    first.table("setups").insert({"id": "setup", "name": "Baseline", "notes": "original",
                                   "extra_fields": {"axle": "M", "pressure": 12}}).execute()
    first.sync()
    second.sync()
    return first, second, journal


def setup(store):
    return store.table("setups").select("*").eq("id", "setup").execute().data[0]


def test_local_save_survives_restart_without_drive(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    store = DriveStore(path, Journal())
    saved = store.table("setups").insert({"name": "Local setup"}).execute().data[0]
    assert saved["id"]
    assert DriveStore(path).table("setups").select().execute().data[0]["name"] == "Local setup"
    assert store.pending_count() == 1


def test_independent_team_edits_merge_and_converge(team):
    first, second, _ = team
    first.table("setups").update({"notes": "first driver"}).eq("id", "setup").execute()
    second.table("setups").update({"name": "Updated baseline"}).eq("id", "setup").execute()
    first.sync()
    second.sync()
    first.sync()
    assert setup(first) == setup(second)
    assert setup(first)["notes"] == "first driver"
    assert setup(first)["name"] == "Updated baseline"
    assert not first.conflicts()


def test_overlapping_edits_preserved_and_block_save_until_resolution(team):
    first, second, journal = team
    first.table("setups").update({"notes": "A"}).eq("id", "setup").execute()
    second.table("setups").update({"notes": "B"}).eq("id", "setup").execute()
    first.sync()
    second.sync()
    first.sync()
    assert setup(first) == setup(second)
    assert len(first.conflicts()) == len(second.conflicts()) == 1
    assert "notes" in first.conflicts()[0]["fields"]
    with pytest.raises(CatalogConflict):
        first.table("setups").update({"notes": "silent overwrite"}).eq("id", "setup").execute()
    first.resolve("setups", "setup", {**setup(first), "notes": "Reviewed decision"})
    first.sync()
    second.sync()
    assert setup(first) == setup(second)
    assert setup(second)["notes"] == "Reviewed decision"
    assert not first.conflicts() and not second.conflicts()
    assert {event["after"]["notes"] for event in journal.events.values()} >= {"A", "B"}


def test_same_run_field_conflicts_but_different_runs_merge():
    baseline = {"runs": [{"id": "one", "notes": "old"}, {"id": "two", "notes": "old"}]}
    left = {"runs": [{"id": "one", "notes": "A"}, {"id": "two", "notes": "old"}]}
    right = {"runs": [{"id": "one", "notes": "old"}, {"id": "two", "notes": "B"}]}
    merged, conflicts = merge(baseline, left, right)
    assert not conflicts
    assert merged["runs"][0]["notes"] == "A"
    assert merged["runs"][1]["notes"] == "B"
    right["runs"][0]["notes"] = "C"
    assert "runs.one.notes" in merge(baseline, left, right)[1]


def test_upload_response_loss_retries_without_duplicate_events(team):
    first, _, journal = team
    first.table("setups").update({"notes": "retry"}).eq("id", "setup").execute()
    journal.fail_after_save = True
    with pytest.raises(OSError):
        first.sync()
    assert first.pending_count() == 1
    first.sync()
    assert first.pending_count() == 0
    assert len(journal.events) == 2


def test_missing_parent_does_not_corrupt_local_catalog(team):
    first, _, journal = team
    before = setup(first)
    journal.events["broken"] = {"schema": 1, "id": "broken", "parents": ["missing"],
                                "table": "setups", "key": "setup", "before": before,
                                "after": {**before, "notes": "broken"}}
    with pytest.raises(ValueError, match="missing parents"):
        first.sync()
    assert setup(first) == before
    with first.connection() as connection:
        assert not connection.execute("SELECT 1 FROM events WHERE id='broken'").fetchone()


def test_delete_versus_edit_is_a_recoverable_conflict(team):
    first, second, _ = team
    first.table("setups").delete().eq("id", "setup").execute()
    second.table("setups").update({"notes": "keep me"}).eq("id", "setup").execute()
    first.sync()
    second.sync()
    first.sync()
    assert first.conflicts() and second.conflicts()
    assert first.table("setups").select().execute().data == second.table("setups").select().execute().data


def test_conditional_update_prevents_stale_local_overwrite(team):
    first, _, _ = team
    old = setup(first)["updated_at"]
    first.table("setups").update({"notes": "new"}).eq("id", "setup").eq("updated_at", old).execute()
    result = first.table("setups").update({"notes": "stale"}).eq("id", "setup").eq("updated_at", old).execute()
    assert not result.data
    assert setup(first)["notes"] == "new"


def test_stock_adjustments_are_atomic_and_have_movement_history(tmp_path):
    store = DriveStore(tmp_path / "catalog.sqlite3", Journal())
    store.table("inventory_items").insert({"id": "part", "name": "Tires", "quantity": 4}).execute()
    params = {"p_item_id": "part", "p_delta": -1, "p_reason": "Used", "p_notes": None, "p_created_by": "editor"}
    assert store.rpc("adjust_inventory_stock", params).execute().data[0]["quantity"] == 3
    movement = store.table("inventory_movements").select().execute().data[0]
    assert (movement["quantity_before"], movement["quantity_delta"], movement["quantity_after"]) == (4, -1, 3)
    with pytest.raises(ValueError):
        store.rpc("adjust_inventory_stock", {**params, "p_delta": -5}).execute()
    assert store.table("inventory_items").select().execute().data[0]["quantity"] == 3
    assert len(store.table("inventory_movements").select().execute().data) == 1


def test_equal_concurrent_stock_changes_do_not_silently_lose_a_movement(tmp_path):
    journal = Journal()
    first, second = [DriveStore(tmp_path / f"{name}.sqlite3", journal) for name in ("first", "second")]
    first.table("inventory_items").insert({"id": "part", "quantity": 4}).execute()
    first.sync()
    second.sync()
    params = {"p_item_id": "part", "p_delta": -1, "p_reason": "Used", "p_notes": None, "p_created_by": "editor"}
    for store in (first, second):
        store.rpc("adjust_inventory_stock", params).execute()
    first.sync()
    second.sync()
    first.sync()
    assert first.conflicts() and second.conflicts()
    assert len(first.table("inventory_movements").select().execute().data) == 2


def test_inventory_revision_history_and_app_docs(tmp_path):
    store = DriveStore(tmp_path / "catalog.sqlite3", Journal())
    part = store.table("inventory_items").insert({"id": "part", "name": "Axle"}).execute().data[0]
    assert part["revision"] == 1
    updated = store.table("inventory_items").update({"name": "Axle M"}).eq("id", "part").eq("revision", 1).execute().data[0]
    assert updated["revision"] == 2
    assert len(store.table("inventory_item_revisions").select().execute().data) == 2
    store.table("app_docs").upsert({"key": "naming", "data": {"teams": []}}, on_conflict="key").execute()
    store.table("app_docs").upsert({"key": "naming", "data": {"teams": ["team"]}}, on_conflict="key").execute()
    assert store.table("app_docs").select("data").eq("key", "naming").execute().data == [{"data": {"teams": ["team"]}}]


def test_bad_remote_identity_is_rejected_without_writes(team):
    first, _, journal = team
    journal.events["bad"] = {"schema": 1, "id": "bad", "parents": [], "table": "setups", "key": "setup",
                             "before": None, "after": {"id": "different"}}
    with pytest.raises(ValueError, match="identity"):
        first.sync()
    assert setup(first)["name"] == "Baseline"


def test_unfiltered_mutations_and_unknown_collections_are_rejected(team):
    first, _, _ = team
    with pytest.raises(ValueError, match="Unfiltered"):
        first.table("setups").delete().execute()
    with pytest.raises(ValueError, match="Unknown"):
        first.table("secrets")


def test_catalog_is_portable_json_not_credentials(team):
    first, _, journal = team
    assert json.loads(json.dumps(list(journal.events.values())))
    assert first.pending_count() == 0


def test_pending_uploads_are_parent_ordered():
    events = {"a-child": {"id": "a-child", "parents": ["z-parent"]},
              "z-parent": {"id": "z-parent", "parents": []}}
    assert [event["id"] for event in DriveStore._ordered_pending(events)] == ["z-parent", "a-child"]


def test_stale_conflict_preview_cannot_overwrite_new_materialized_state(team):
    first, second, _ = team
    first.table("setups").update({"notes": "first"}).eq("id", "setup").execute()
    second.table("setups").update({"notes": "second"}).eq("id", "setup").execute()
    first.sync()
    second.sync()
    first.sync()
    with pytest.raises(CatalogConflict, match="comparison"):
        first.resolve("setups", "setup", setup(first), expected={"id": "setup", "notes": "stale"})
    assert first.conflicts()
