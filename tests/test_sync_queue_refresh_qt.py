from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QProgressBar

from database.drive_store import DriveStore
from offline_store import PendingSyncItem, PendingSyncSummary
from qt.main_window import MainWindow
from qt.sync_queue_qt import SyncQueueDialog


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def test_catalog_queue_hides_internal_document_and_record_ids(tmp_path):
    store = DriveStore(tmp_path / "catalog.sqlite3")
    store.table("app_docs").insert({"key": "session_type:opaque-folder-id", "data": {"type": "race"}}).execute()
    store.table("app_docs").insert({"key": "maintenance_config", "data": {}}).execute()
    store.table("setups").insert({"id": "opaque-setup-id", "name": "Test 2"}).execute()
    store.table("setups").insert({"id": "another-opaque-setup-id"}).execute()
    names = {item.name for item in store.queue_items()}
    assert names == {"Session type · Race", "Maintenance schedule", "Test 2", "Setup change"}
    assert not any("opaque" in name or "session_type:" in name for name in names)


def window_for(dialog):
    return SimpleNamespace(_sidebar=Mock(), _cloud_connected=True,
                           _supabase_connected=True, _drive_connected=True,
                           _sync_queue_dialog=dialog)


@pytest.mark.parametrize("data", [None, [], "invalid", {"type": []}])
def test_unknown_session_type_payload_has_safe_readable_label(data):
    from database.catalog_rules import catalog_event_name
    event = {"table": "app_docs", "key": "session_type:private-id", "after": {"data": data}}
    assert catalog_event_name(event) == "Session type setting"


def test_open_queue_clears_after_background_connection_sync_without_upload_job(app, monkeypatch):
    pending = [PendingSyncItem("catalog:a", "Test setup", "Catalog change", "Google Drive")]
    monkeypatch.setattr("offline_store.pending_sync_items", lambda: list(pending))
    monkeypatch.setattr("offline_store.pending_sync_summary", lambda: PendingSyncSummary(setups=len(pending)))
    dialog = SyncQueueDialog()
    dialog.show()
    app.processEvents()
    assert dialog._summary.text() == "1 item waiting"
    pending.clear()  # The connection check synced the journal before any upload job.
    MainWindow._refresh_sync_indicator(window_for(dialog))
    assert dialog._summary.text() == "Queue empty"
    assert not dialog.findChildren(QProgressBar)
    dialog.close()
    dialog.deleteLater()


def test_queue_refresh_preserves_active_transfer_progress(app, monkeypatch):
    item = PendingSyncItem("catalog:a", "Test setup", "Catalog change", "Google Drive")
    monkeypatch.setattr("offline_store.pending_sync_items", lambda: [item])
    monkeypatch.setattr("offline_store.pending_sync_summary", lambda: PendingSyncSummary(setups=1))
    dialog = SyncQueueDialog()
    dialog.show()
    dialog.set_event({"key": item.key, "state": "syncing", "percent": 42})
    MainWindow._refresh_sync_indicator(window_for(dialog))
    assert [bar.value() for bar in dialog.findChildren(QProgressBar)] == [42]
    dialog.close()
    dialog.deleteLater()
