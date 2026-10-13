from copy import deepcopy
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import database.client as db
from database.models import InventoryItem, Setup
import offline_store
from utils import google_drive_client
from utils.google_drive_client import DriveNode


def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(
        offline_store, "OFFLINE_ROOT", tmp_path / "sessions")


def test_video_catalog_remembers_cloud_files_without_downloading(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(google_drive_client, "download_file",
                        lambda *args: (_ for _ in ()).throw(
                            AssertionError("Listing must not download video bytes")))
    node = DriveNode(id="movie", name="camera.mov", is_folder=False, size=3)
    offline_store.cache_video_catalog("session", [node])
    listing = offline_store.load_video_catalog("session")
    assert len(listing) == 1
    assert listing[0].id == "movie"
    assert not listing[0].local_path
    cached = offline_store.video_cache_path("session", node)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"MOV")
    assert offline_store.load_video_catalog("session")[0].local_path == str(cached)
    cached.unlink()
    assert not offline_store.load_video_catalog("session")[0].local_path


def test_offline_package_keeps_setups_parts_and_queued_files(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    setup = Setup(
        id="setup-1", drive_folder_id="session-1", name="Race setup",
        driver_name="Alex", chassis_name="2022 RedSpeed",
        data={"axle": "N"})
    part = InventoryItem(id="part-1", category="Axles", name="N Axle")
    source = tmp_path / "new-run.xrk"
    source.write_bytes(b"XRK")

    offline_store.ensure_session(
        "session-1", "Race", "Season / Race", "Season")
    offline_store.cache_setups("session-1", [setup])
    offline_store.cache_inventory([part])
    queued = offline_store.queue_local_file("session-1", source)

    loaded_setup = offline_store.load_setups("session-1")[0]
    assert loaded_setup.name == "Race setup"
    assert loaded_setup.driver_name == "Alex"
    assert offline_store.load_inventory()[0].name == "N Axle"
    assert queued.read_bytes() == b"XRK"
    files = offline_store.load_files("session-1")
    assert files[0].name == "new-run.xrk"
    assert files[0].pending_upload is True
    assert offline_store.pending_count("session-1") == 1
    summary = offline_store.pending_sync_summary()
    assert (summary.files, summary.setups, summary.inventory, summary.errors) == (
        1, 0, 0, 0)
    queue = offline_store.pending_sync_items()
    assert [
        (item.name, item.kind, item.destination, item.size) for item in queue
    ] == [("new-run.xrk", "Data / video", "Google Drive", 3)]
    sessions = offline_store.list_session_folders()
    assert [(session.id, session.name) for session in sessions] == [
        ("session-1", "Race")]
    assert [node.name for node in sessions[0].xrks] == ["new-run.xrk"]


def test_queued_data_file_can_be_renamed_and_deleted(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    source = tmp_path / "Tire Data.ibt"
    source.write_bytes(b"IBT")
    offline_store.queue_local_file("session-1", source)
    node = offline_store.load_files("session-1")[0]

    assert offline_store.rename_local_file(
        "session-1", node.id, "Practice Tires.ibt")
    renamed = offline_store.load_files("session-1")[0]
    assert renamed.name == "Practice Tires.ibt"
    assert Path(renamed.local_path).read_bytes() == b"IBT"
    assert not Path(node.local_path).exists()
    assert offline_store.remove_local_file("session-1", renamed.id)
    assert offline_store.load_files("session-1") == []
    assert not Path(renamed.local_path).exists()


def test_missing_generated_file_is_cleared_when_newer_cloud_copy_replaces_it(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    old = tmp_path / "EGTV2.ibt"
    old.write_bytes(b"same-size-output")
    offline_store.queue_local_file("session-1", old)
    queued = offline_store.load_files("session-1")[0]
    Path(queued.local_path).unlink()
    replacement = DriveNode(
        "drive-new", "Tire Data.ibt", False, len(b"same-size-output"))
    cached = tmp_path / "Tire Data.ibt"
    cached.write_bytes(b"same-size-output")
    offline_store._record_remote_file("session-1", replacement, cached)

    report = offline_store.sync_pending()

    assert report.errors == ()
    assert offline_store.pending_count("session-1") == 0


def test_local_file_record_is_removed_when_cached_copy_is_locked(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    source = tmp_path / "Tire Data.ibt"
    source.write_bytes(b"IBT")
    offline_store.queue_local_file("session-1", source)
    node = offline_store.load_files("session-1")[0]
    monkeypatch.setattr(
        Path, "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("file is open")))

    assert offline_store.remove_local_file("session-1", node.id)
    assert offline_store.load_files("session-1") == []


def test_download_session_can_skip_large_videos(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        db, "fetch_setups_by_folder",
        lambda _session: [Setup(id="setup-1", name="Race setup")])
    monkeypatch.setattr(
        db, "fetch_inventory_items",
        lambda include_archived=True: [
            InventoryItem(id="part-1", category="Tires", name="R55")])
    monkeypatch.setattr(db, "fetch_baselines", lambda: [])
    monkeypatch.setattr(
        google_drive_client, "list_files_recursive",
        lambda _session: [
            DriveNode("xrk-1", "run.xrk", False, 3,
                      relative_path="Kart 11/run.xrk"),
            DriveNode("video-1", "run.mp4", False, 5,
                      relative_path="Videos/run.mp4"),
        ])

    def _download(_file_id, destination, _progress=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"XRK")

    monkeypatch.setattr(google_drive_client, "download_file", _download)

    info = offline_store.download_session_package(
        "session-1", "Race", "Season / Race", "Season",
        include_videos=False)

    assert info.file_count == 1
    assert info.include_xrks is True
    assert [node.name for node in offline_store.load_files("session-1")] == [
        "run.xrk"]
    assert offline_store.load_inventory()[0].name == "R55"


def test_setup_catalog_is_cached_without_manual_session_download(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    setups = [
        Setup(
            id="setup-1", drive_folder_id="session-1",
            drive_folder_path="2026 Season / Fall Race", name="Race setup"),
        Setup(
            id="setup-2", drive_folder_id="session-2",
            drive_folder_path="2026 Season / Spring Race", name="Practice setup"),
    ]
    baseline = Setup(id="baseline-1", name="Baseline", is_baseline=True)

    offline_store.cache_setup_catalog([baseline], setups)

    assert {setup.id for setup in offline_store.load_setup_catalog()} == {
        "setup-1", "setup-2"}
    assert {setup.id for setup in offline_store.load_setup_catalog(
        include_baselines=True)} == {"setup-1", "setup-2", "baseline-1"}
    assert {(session.id, session.name) for session
            in offline_store.list_session_folders()} == {
        ("session-1", "Fall Race"), ("session-2", "Spring Race")}


def test_cached_setup_can_be_deleted_by_remote_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    setup = Setup(
        id="setup-1", drive_folder_id="session-1", name="Blank setup")
    offline_store.cache_setups("session-1", [setup])

    assert offline_store.delete_cached_setup("setup-1")
    assert offline_store.load_setups("session-1") == []


def test_setup_deletion_is_queued_and_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    setup = Setup(
        id="setup-1", drive_folder_id="session-1", name="Blank setup")
    offline_store.cache_setups("session-1", [setup])
    assert offline_store.queue_setup_deletion("setup-1", "session-1")

    # A cloud catalog refresh must not resurrect a setup awaiting deletion.
    offline_store.cache_setup_catalog([], [setup])
    assert offline_store.load_setup_catalog() == []
    assert offline_store.pending_count() == 1
    deleted = []
    monkeypatch.setattr(
        "database.client.delete_setup", lambda setup_id: deleted.append(setup_id))

    events = []
    report = offline_store.sync_pending(events.append)

    assert deleted == ["setup-1"]
    assert report.setup_deletions_synced == 1
    assert report.errors == ()
    assert offline_store.pending_count() == 0
    assert [event["state"] for event in events] == ["syncing", "complete"]
    assert offline_store.session_info("session-1").downloaded_at is None


def test_xrk_and_video_offline_downloads_are_independently_optional(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "fetch_setups_by_folder", lambda _session: [])
    monkeypatch.setattr(db, "fetch_inventory_items", lambda **_kwargs: [])
    monkeypatch.setattr(db, "fetch_baselines", lambda: [])
    monkeypatch.setattr(
        google_drive_client, "list_files_recursive",
        lambda _session: [
            DriveNode("xrk-1", "run.xrk", False, 3),
            DriveNode("video-1", "run.mp4", False, 5),
        ])
    monkeypatch.setattr(
        google_drive_client, "download_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no optional media should download")))

    info = offline_store.download_session_package(
        "session-1", "Race", "Season / Race", "Season",
        include_xrks=False, include_videos=False)

    assert info.file_count == 0
    assert info.include_xrks is False
    assert info.include_videos is False


def test_pending_setup_and_file_sync_after_reconnection(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    source = tmp_path / "video.mov"
    source.write_bytes(b"video")
    offline_store.queue_local_file("session-1", source)
    pending_setup = offline_store.save_pending_setup(Setup(
        drive_folder_id="session-1", drive_folder_path="Season / Race",
        name="Offline notes", data={"driver_feedback": "Loose"}))
    assert pending_setup.id.startswith("offline-")

    monkeypatch.setattr(
        google_drive_client, "upload_file",
        lambda _path, _parent, **_kwargs: "drive-file-1")

    def _create(setup):
        setup.id = "remote-setup-1"
        setup.updated_at = "2026-08-27T12:00:00+00:00"
        return setup

    monkeypatch.setattr(db, "create_setup", _create)

    events = []
    report = offline_store.sync_pending(events.append)

    assert report.files_synced == 1
    assert report.setups_synced == 1
    assert report.errors == ()
    assert offline_store.pending_count() == 0
    assert [event["state"] for event in events] == [
        "syncing", "complete", "syncing", "complete"]
    assert offline_store.load_setups("session-1")[0].id == "remote-setup-1"
    assert offline_store.resolve_setup_id(pending_setup.id) == "remote-setup-1"


def test_google_attributed_setup_history_survives_offline_sync(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        db.google_drive_client, "current_user",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu", verified=False))
    setup = Setup(
        drive_folder_id="session-1", drive_folder_path="Season / Race",
        name="Offline setup", data={"gear_ratio": "11/80"})
    db.record_setup_change(setup, None)
    pending = offline_store.save_pending_setup(setup)

    cached_entry = db.setup_change_log(
        offline_store.load_setups("session-1")[0])[0]
    assert cached_entry["actor_email"] == "alex@example.edu"

    def _create(incoming):
        incoming.id = "remote-setup-1"
        incoming.updated_at = "2026-08-31T12:30:00+00:00"
        return incoming

    monkeypatch.setattr(db, "create_setup", _create)
    report = offline_store.sync_pending()

    assert report.setups_synced == 1
    assert report.errors == ()
    synced = offline_store.load_setups("session-1")[0]
    assert synced.id == "remote-setup-1"
    assert db.setup_change_log(synced)[0]["id"] == cached_entry["id"]
    assert offline_store.resolve_setup_id(pending.id) == "remote-setup-1"


def test_unattributed_setup_stays_queued_then_resolves_on_reconnect(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        db.google_drive_client, "audit_user_or_none", lambda **_kwargs: None)
    setup = Setup(
        drive_folder_id="session-1", drive_folder_path="Season / Race",
        name="Offline setup", data={"gear_ratio": "11/80"})
    db.record_setup_change(setup, None)
    pending = offline_store.save_pending_setup(setup)

    assert pending._offline_pending is True
    assert offline_store.pending_count() == 1
    assert db.setup_identity_pending(pending) is True

    monkeypatch.setattr(
        db.google_drive_client, "current_user",
        lambda **_kwargs: (_ for _ in ()).throw(
            google_drive_client.GoogleDriveError("Google is offline")))
    first_report = offline_store.sync_pending()

    assert first_report.setups_synced == 0
    assert "Google is offline" in first_report.errors[0]
    assert offline_store.pending_count() == 1

    monkeypatch.setattr(
        db.google_drive_client, "current_user",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu"))
    captured = {}

    def _create(incoming):
        captured["setup"] = deepcopy(incoming)
        incoming.id = "remote-setup-1"
        incoming.updated_at = "2026-09-03T12:30:00+00:00"
        return incoming

    monkeypatch.setattr(db, "create_setup", _create)
    second_report = offline_store.sync_pending()

    assert second_report.setups_synced == 1
    assert second_report.errors == ()
    assert offline_store.pending_count() == 0
    resolved = captured["setup"]
    assert resolved.created_by == "Alex Racer <alex@example.edu>"
    entry = db.setup_change_log(resolved)[0]
    assert entry["actor_email"] == "alex@example.edu"
    assert "identity_pending" not in entry


def test_conflicting_online_setup_keeps_offline_edit_pending(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    original = Setup(
        id="setup-1", drive_folder_id="session-1", name="Race setup",
        updated_at="2026-08-27T10:00:00+00:00")
    offline_store.ensure_session("session-1", "Race")
    offline_store.cache_setups("session-1", [original])
    original.name = "Offline edit"
    offline_store.save_pending_setup(original)

    monkeypatch.setattr(
        db, "fetch_setup",
        lambda _setup_id: Setup(
            id="setup-1", updated_at="2026-08-27T11:00:00+00:00"))
    monkeypatch.setattr(
        db, "update_setup",
        lambda _setup: (_ for _ in ()).throw(
            AssertionError("conflicting edit must not upload")))

    report = offline_store.sync_pending()

    assert report.setups_synced == 0
    assert "Conflict:" in report.errors[0]
    assert offline_store.pending_count("session-1") == 1


def test_identical_online_setup_clears_false_timestamp_conflict(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    original = Setup(
        id="setup-1", drive_folder_id="session-1", name="Race setup",
        notes="Already saved", updated_at="2026-10-07T14:32:13+00:00")
    offline_store.cache_setups("session-1", [original])
    offline_store.save_pending_setup(original)
    live = deepcopy(original)
    live.updated_at = "2026-10-07T14:33:01+00:00"
    monkeypatch.setattr(db, "fetch_setup", lambda _setup_id: live)
    monkeypatch.setattr(
        db, "update_setup",
        lambda _setup: (_ for _ in ()).throw(
            AssertionError("identical live content must not upload again")))

    report = offline_store.sync_pending()

    assert report.setups_synced == 1
    assert report.errors == ()
    assert offline_store.pending_count("session-1") == 0
    assert offline_store.load_setups("session-1")[0].updated_at == live.updated_at


def test_parts_can_be_updated_locally_and_sync_after_reconnection(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        google_drive_client, "audit_user_or_none",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu"))
    original = InventoryItem(
        id="part-1", revision=2, category="Axles", name="N Axle",
        quantity=2, updated_at="2026-09-01T10:00:00+00:00")
    edited = InventoryItem.from_dict(offline_store._inventory_payload(original))
    edited.name = "N Axle · serviced"
    edited.quantity = 3
    offline_store.cache_inventory([original])

    local = offline_store.queue_inventory_item(edited, original)

    assert local.name == "N Axle · serviced"
    assert local.quantity == 3
    assert local.revision == 3
    assert local._offline_pending is True
    assert offline_store.pending_count() == 1
    assert offline_store.load_inventory()[0]._offline_pending is True

    monkeypatch.setattr(db, "fetch_inventory_item", lambda _item_id: original)

    def _update(item):
        saved = InventoryItem.from_dict(offline_store._inventory_payload(item))
        saved.revision = 3
        saved.quantity = original.quantity
        saved.updated_at = "2026-09-02T12:00:00+00:00"
        return saved

    monkeypatch.setattr(db, "update_inventory_item", _update)

    def _adjust(_item_id, delta, _reason):
        assert delta == 1
        saved = InventoryItem.from_dict(offline_store._inventory_payload(edited))
        saved.id = "part-1"
        saved.revision = 3
        saved.updated_at = "2026-09-02T12:01:00+00:00"
        return saved

    monkeypatch.setattr(db, "adjust_inventory_stock", _adjust)

    report = offline_store.sync_pending()

    assert report.inventory_synced == 1
    assert report.errors == ()
    assert offline_store.pending_count() == 0
    synced = offline_store.load_inventory()[0]
    assert synced.name == "N Axle · serviced"
    assert synced.quantity == 3


def test_unlimited_run_notes_survive_offline_setup_round_trip(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    runs = [
        {
            "id": f"run-{number}",
            "run_number": str(number),
            "fastest_lap": f"48.{number:02d}",
            "notes": (f"Detailed balance notes for run {number}. " * 40).strip(),
        }
        for number in range(1, 16)
    ]
    setup = Setup(
        id="setup-1", drive_folder_id="session-1",
        name="Race setup", data={"runs": runs},
        notes="Long setup-sheet notes that apply to every run.")

    offline_store.cache_setups("session-1", [setup])
    loaded = offline_store.load_setups("session-1")[0]
    loaded.data["runs"][14]["notes"] += " Final adjustment."
    offline_store.save_pending_setup(loaded)
    reopened = offline_store.load_setups("session-1")[0]

    assert len(reopened.data["runs"]) == 15
    assert reopened.data["runs"][0]["id"] == "run-1"
    assert reopened.data["runs"][14]["notes"].endswith("Final adjustment.")
    assert reopened.notes == setup.notes


def test_part_created_offline_rewrites_pending_setup_reference_on_sync(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        google_drive_client, "audit_user_or_none",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu"))
    local_part = offline_store.queue_inventory_item(InventoryItem(
        category="Axles", name="New Axle"))
    setup = Setup(
        drive_folder_id="session-1", name="Race setup",
        data={"_inventory_part_revisions": {"axle": {
            "item_id": local_part.id, "revision": 1, "name": local_part.name,
        }}})
    offline_store.save_pending_setup(setup)

    def _create_part(item):
        item.id = "remote-part-1"
        item.revision = 1
        item.updated_at = "2026-09-02T12:00:00+00:00"
        return item

    captured = {}

    def _create_setup(incoming):
        captured.update(incoming.data["_inventory_part_revisions"]["axle"])
        incoming.id = "remote-setup-1"
        incoming.updated_at = "2026-09-02T12:01:00+00:00"
        return incoming

    monkeypatch.setattr(db, "create_inventory_item", _create_part)
    monkeypatch.setattr(db, "create_setup", _create_setup)

    report = offline_store.sync_pending()

    assert report.inventory_synced == 1
    assert report.setups_synced == 1
    assert report.errors == ()
    assert captured["item_id"] == "remote-part-1"
    assert captured["revision"] == 1


def test_conflicting_online_part_keeps_local_edit_pending(
        tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        google_drive_client, "audit_user_or_none",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu"))
    original = InventoryItem(
        id="part-1", category="Axles", name="N Axle",
        updated_at="2026-09-01T10:00:00+00:00")
    edited = deepcopy(original)
    edited.name = "Offline edit"
    offline_store.queue_inventory_item(edited, original)
    monkeypatch.setattr(
        db, "fetch_inventory_item",
        lambda _item_id: InventoryItem(
            id="part-1", category="Axles", name="Online edit",
            updated_at="2026-09-01T11:00:00+00:00"))

    report = offline_store.sync_pending()

    assert report.inventory_synced == 0
    assert "Conflict:" in report.errors[0]
    assert offline_store.pending_count() == 1
