import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest
from dotenv import dotenv_values

from utils import workspace_backup as backup


def workspace(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    media = root / "offline_sessions" / "sample.txt"
    media.parent.mkdir()
    media.write_text("local sample")
    with sqlite3.connect(root / "xcal_offline.db") as connection:
        connection.execute("CREATE TABLE offline_files(local_id TEXT,local_path TEXT,pending INTEGER)")
        connection.execute("INSERT INTO offline_files VALUES(?,?,1)", ("local", str(media)))
    with sqlite3.connect(root / "catalog.sqlite3") as connection:
        connection.execute("CREATE TABLE work(value TEXT)")
        connection.execute("INSERT INTO work VALUES('pending work')")
    return root


def test_roundtrip_preserves_databases_pending_work_and_rebases_media(tmp_path):
    source = workspace(tmp_path)
    original = (source / "catalog.sqlite3").read_bytes()
    archive = backup.export_workspace(source, tmp_path / "backup.zip")
    recovered = backup.restore_workspace(archive, tmp_path / "recovery")
    restored = recovered / "teams" / "local"
    assert dotenv_values(recovered / "settings.env") == {"GOOGLE_DRIVE_FOLDER_ID": "", "XCAL_RECOVERY_MODE": "1"}
    with sqlite3.connect(restored / "catalog.sqlite3") as connection:
        assert connection.execute("SELECT value FROM work").fetchone() == ("pending work",)
    with sqlite3.connect(restored / "xcal_offline.db") as connection:
        filename, pending = connection.execute("SELECT local_path,pending FROM offline_files").fetchone()
        assert Path(filename).is_relative_to(restored)
        assert Path(filename).read_text() == "local sample"
        assert pending == 1
    assert (source / "catalog.sqlite3").read_bytes() == original
    second = backup.restore_workspace(archive, recovered.parent)
    assert second != recovered
    assert restored.is_dir()


def test_wal_transactions_are_included_in_export(tmp_path):
    source = workspace(tmp_path)
    connection = sqlite3.connect(source / "catalog.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO work VALUES('committed WAL')")
        connection.commit()
        archive = backup.export_workspace(source, tmp_path / "backup.zip")
        restored = backup.restore_workspace(archive, tmp_path / "recovery")
        with sqlite3.connect(restored / "teams/local/catalog.sqlite3") as saved:
            assert saved.execute("SELECT COUNT(*) FROM work").fetchone()[0] == 2
    finally:
        connection.close()


def test_export_failure_preserves_prior_backup(monkeypatch, tmp_path):
    source = workspace(tmp_path)
    target = tmp_path / "backup.zip"
    target.write_bytes(b"previous backup")
    monkeypatch.setattr("utils.atomic_files.os.replace", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError):
        backup.export_workspace(source, target)
    assert target.read_bytes() == b"previous backup"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "folder\\escape", "CON.txt", "safe/../escape", "google_token.json"])
def test_restore_rejects_unsafe_paths_without_publishing(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as saved:
        saved.writestr(name, b"bad")
    parent = tmp_path / "recovery"
    with pytest.raises(ValueError):
        backup.restore_workspace(archive, parent)
    assert list(parent.iterdir()) == []


def test_restore_detects_tampering_and_keeps_active_workspace(tmp_path):
    source = workspace(tmp_path)
    exported = backup.export_workspace(source, tmp_path / "good.zip")
    with zipfile.ZipFile(exported) as archive, zipfile.ZipFile(tmp_path / "bad.zip", "w") as changed:
        for item in archive.infolist():
            changed.writestr(item.filename, b"tampered" if item.filename.endswith("sample.txt") else archive.read(item))
    with pytest.raises(ValueError, match="integrity"):
        backup.restore_workspace(tmp_path / "bad.zip", tmp_path / "recover")
    assert list((tmp_path / "recover").iterdir()) == []
    assert (source / "offline_sessions/sample.txt").read_text() == "local sample"


def test_old_unversioned_backup_is_rejected_with_reexport_guidance(tmp_path):
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as saved:
        saved.writestr("data.txt", "old format")
    with pytest.raises(ValueError, match="Re-export"):
        backup.restore_workspace(archive, tmp_path / "recover")


def test_credentials_are_never_exported(tmp_path):
    source = workspace(tmp_path)
    (source / "oauth-client.json").write_text("synthetic private file")
    with pytest.raises(ValueError, match="Credentials"):
        backup.export_workspace(source, tmp_path / "backup.zip")
    assert not (tmp_path / "backup.zip").exists()


def test_corrupt_database_with_valid_archive_digest_is_rejected(tmp_path):
    payload = b"not SQLite"
    archive = tmp_path / "bad-db.zip"
    manifest = {"format": "xcal-workspace", "version": 1, "source_root": "old",
                "files": {"catalog.sqlite3": {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}}}
    with zipfile.ZipFile(archive, "w") as saved:
        saved.writestr(backup.MANIFEST, json.dumps(manifest))
        saved.writestr("catalog.sqlite3", payload)
    with pytest.raises(sqlite3.DatabaseError):
        backup.restore_workspace(archive, tmp_path / "recover")
    assert list((tmp_path / "recover").iterdir()) == []


def test_open_recovery_uses_separate_user_root_and_disables_cloud(monkeypatch, tmp_path):
    launch = Mock()
    monkeypatch.setattr(backup.subprocess, "Popen", launch)
    backup.open_recovery(tmp_path)
    args = launch.call_args.kwargs
    assert args["env"]["XCAL_DATA_DIR"] == str(tmp_path.resolve())
    assert args["env"]["XCAL_RECOVERY_MODE"] == "1"
    assert args["env"]["GOOGLE_DRIVE_FOLDER_ID"] == ""


def test_actual_catalog_events_survive_restore_without_upload(monkeypatch, tmp_path):
    from database.drive_store import DriveStore
    source = tmp_path / "team"
    store = DriveStore(source / "catalog.sqlite3")
    store.table("inventory_items").insert({"id": "part-1", "name": "Recovery part", "category": "Cage"}).execute()
    store.table("app_docs").upsert({"key": "team_profile", "data": {"id": "team", "name": "Recovery team"}}, on_conflict="key").execute()
    pending = store.pending_count()
    archive = backup.export_workspace(source, tmp_path / "actual.zip")
    root = backup.restore_workspace(archive, tmp_path / "recover")
    recovered = DriveStore(root / "teams/local/catalog.sqlite3")
    monkeypatch.setattr("config.GOOGLE_DRIVE_FOLDER_ID", "")
    assert recovered.sync()["local_only"]
    assert recovered.pending_count() == pending
    assert recovered.table("inventory_items").select("*").execute().data[0]["name"] == "Recovery part"
    assert recovered.table("app_docs").select("*").execute().data[0]["data"]["name"] == "Recovery team"


def test_cancelled_recovery_does_not_publish_partial_copy(tmp_path):
    source = workspace(tmp_path)
    archive = backup.export_workspace(source, tmp_path / "backup.zip")
    def cancel():
        raise InterruptedError("closing")
    with pytest.raises(InterruptedError):
        backup.restore_workspace(archive, tmp_path / "recover", check_cancel=cancel)
    assert list((tmp_path / "recover").iterdir()) == []
