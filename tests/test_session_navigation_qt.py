import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QLabel, QLineEdit,
    QFileDialog, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from database.models import InventoryItem, Setup
from inventory_fields import BASELINE_SETUP_CATEGORY, PARTS_CATEGORIES
import offline_store
import setup_fields as sf
from utils import google_drive_client
from qt.views.session_detail_qt import (
    ManageDataFilesDialog, SessionDetailView, SessionSettingsDialog,
    _session_folder_name)
from qt.views.session_workspace_qt import SessionWorkspace
from qt.views.parts_qt import (
    ALL_PARTS_CATEGORY, PartDialog, PartsView, _BaselineCard,
    _save_inventory_changes)
from qt.views.setup_builder_qt import (
    SetupBuilderView, _PartPickerDialog, _SetupHistoryDialog,
    _SetupOptionsDialog, _baseline_name, _run_history_lines,
    _split_legacy_run_timestamp)
from qt.views.settings_qt import SettingsView
from qt.views.setups_qt import SetupsView
from qt.views.compare_qt import _Slot
from qt.views.tire_data_merge_qt import RPM_TOLERANCE, TireDataMergeDialog
from qt.sync_queue_qt import SyncQueueDialog
from qt.widgets import install_first_click_select_all


def _app():
    return QApplication.instance() or QApplication([])


def _session(index):
    return SimpleNamespace(
        id=f"session-{index}",
        name=f"2026-08-{index + 1:02d} Session {index}",
        path=f"Season / 2026-08-{index + 1:02d} Session {index}",
    )


def test_session_header_omits_redundant_offline_file_buttons():
    _app()
    detail = SessionDetailView()
    assert detail._tabs.tabText(0) == "Setup/Notes"
    button_texts = {
        button.text() for button in detail.findChildren(QPushButton)}

    assert "Update Offline Files" not in button_texts
    assert "Download Files" not in button_texts
    assert "Add / Queue Files" not in button_texts
    assert "Open Offline Folder" not in button_texts
    assert "+ Add Files" in button_texts
    assert "Merge Tires" in button_texts
    assert "Manage" in button_texts
    assert "+ Add Video" in button_texts
    detail.deleteLater()


def test_add_data_files_accepts_a_batch(monkeypatch):
    _app()
    detail = SessionDetailView()
    detail._folder_id = "session-1"
    queued = []
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        lambda *_args, **_kwargs: (["run-1.xrk", "run-2.xrk"], ""))
    detail._queue_local_files = lambda paths: queued.extend(paths)

    detail._upload()

    assert queued == ["run-1.xrk", "run-2.xrk"]
    detail.deleteLater()


def test_tire_data_merge_defaults_to_reviewed_500_rpm_workflow():
    _app()
    dialog = TireDataMergeDialog("Practice")

    assert dialog.windowTitle() == "Tire Data Merge"
    assert dialog._name.text() == "Tire Data"
    assert RPM_TOLERANCE == 500
    assert not dialog._export.isEnabled()
    assert "500 RPM" in " ".join(
        label.text() for label in dialog.findChildren(QLabel))
    assert "All channels" in " ".join(
        label.text() for label in dialog.findChildren(QLabel))
    dialog.deleteLater()


def test_ibt_card_downloads_to_computer_instead_of_opening_file_association():
    _app()
    detail = SessionDetailView()
    node = google_drive_client.DriveNode(
        id="ibt", name="Tire Data.ibt", is_folder=False)

    card = detail._data_card(node)
    buttons = card.findChildren(QPushButton)

    assert [button.text() for button in buttons] == ["Download to this computer"]
    assert "Downloads folder" in buttons[0].toolTip()
    card.deleteLater()
    detail.deleteLater()


def test_data_cards_distinguish_drive_state_from_device_copy():
    _app()
    detail = SessionDetailView()
    pending = google_drive_client.DriveNode(
        id="local-1", name="Pending.xrk", is_folder=False,
        local_path="C:/cache/Pending.xrk", pending_upload=True)
    cached = google_drive_client.DriveNode(
        id="drive-1", name="Cached.xrk", is_folder=False,
        local_path="C:/cache/Cached.xrk")
    cloud = google_drive_client.DriveNode(
        id="drive-2", name="Cloud.xrk", is_folder=False)

    pending_card = detail._data_card(pending)
    cached_card = detail._data_card(cached)
    cloud_card = detail._data_card(cloud)
    pending_text = " ".join(
        label.text() for label in pending_card.findChildren(QLabel))
    cached_text = " ".join(
        label.text() for label in cached_card.findChildren(QLabel))
    cloud_text = " ".join(
        label.text() for label in cloud_card.findChildren(QLabel))

    assert "On device" in pending_text
    assert "Waiting to upload" in pending_text
    assert "On device" in cached_text
    assert "Not on device" in cloud_text
    assert "Google Drive" not in pending_text + cached_text + cloud_text
    detail.deleteLater()


def test_data_and_video_cards_show_recording_timestamp_source():
    _app()
    detail = SessionDetailView()
    recorded_at = "2026-10-09T15:42:07-04:00"
    xrk = google_drive_client.DriveNode(
        id="xrk", name="run.xrk", is_folder=False,
        recorded_at=recorded_at, timestamp_source="XRK logger time")
    video = google_drive_client.DriveNode(
        id="video", name="camera.mov", is_folder=False,
        recorded_at=recorded_at, timestamp_source="Video metadata")

    data_card = detail._data_card(xrk)
    video_card = detail._video_card(video)
    data_text = " ".join(
        label.text() for label in data_card.findChildren(QLabel))
    video_text = " ".join(
        label.text() for label in video_card.findChildren(QLabel))

    assert "Recorded Oct 9, 2026" in data_text
    assert "XRK logger time" not in data_text
    assert "Recorded Oct 9, 2026" in video_text
    assert "Video metadata" not in video_text
    data_tooltips = " ".join(
        label.toolTip() for label in data_card.findChildren(QLabel))
    video_tooltips = " ".join(
        label.toolTip() for label in video_card.findChildren(QLabel))
    assert "XRK logger time" in data_tooltips
    assert "Video metadata" in video_tooltips
    detail.deleteLater()


def test_video_listing_is_on_demand_and_cached_play_works_offline(tmp_path, monkeypatch):
    _app()
    detail = SessionDetailView()
    detail._folder_id = "video-session"
    cached = tmp_path / "cached.mov"
    cached.write_bytes(b"MOV")
    nodes = [
        google_drive_client.DriveNode(
            id="cached", name="cached.mov", is_folder=False,
            size=3, local_path=str(cached)),
        google_drive_client.DriveNode(
            id="cloud", name="cloud.mov", is_folder=False, size=3),
    ]
    monkeypatch.setattr(google_drive_client, "download_file",
                        lambda *args: (_ for _ in ()).throw(
                            AssertionError("Rendering must not download")))
    detail._render_videos(nodes, "Offline")
    buttons = detail.findChildren(QPushButton)
    assert any(button.text() == "Cached · Play" and button.isEnabled()
               for button in buttons)
    assert any(button.text() == "Download / Play" and not button.isEnabled()
               for button in buttons)
    assert not detail._download_all_videos.isEnabled()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.append(Path(url.toLocalFile())) or True)
    detail._open_video(nodes[0])
    assert opened == [cached]
    detail.deleteLater()


def test_xrk_cards_offer_open_all_and_individual_actions_without_checkboxes():
    _app()
    detail = SessionDetailView()
    nodes = [
        google_drive_client.DriveNode(
            id="xrk-1", name="run-1.xrk", is_folder=False),
        google_drive_client.DriveNode(
            id="xrk-2", name="run-2.xrk", is_folder=False),
        google_drive_client.DriveNode(
            id="csv", name="notes.csv", is_folder=False),
    ]

    detail._render_data(nodes)
    file_checks = [
        checkbox for checkbox in detail.findChildren(QCheckBox)
        if checkbox.accessibleName().startswith("Select run-")]
    open_buttons = [
        button for button in detail.findChildren(QPushButton)
        if button.text() == "Open in Race Studio"]

    assert not file_checks
    assert not detail._open_all_xrks_button.isHidden()
    assert detail._open_all_xrks_button.isEnabled()
    assert detail._open_all_xrks_button.text() == (
        "Open All in Race Studio (2)")
    assert len(open_buttons) == 2
    detail.deleteLater()


def test_open_all_xrks_prepares_one_folder_and_opens_it(
        tmp_path, monkeypatch):
    _app()
    detail = SessionDetailView()
    detail._folder_id = "session-1"
    paths = []
    nodes = []
    for index in range(2):
        path = tmp_path / f"run-{index}.xrk"
        path.write_bytes(b"XRK")
        paths.append(path)
        nodes.append(google_drive_client.DriveNode(
            id=f"xrk-{index}", name=path.name, is_folder=False,
            size=path.stat().st_size, local_path=str(path)))
    detail._render_data(nodes)
    progress = []
    opened_folders = []

    def immediate(_parent, work, on_done=None, on_error=None,
                  on_progress=None):
        try:
            def update(value):
                progress.append(value)
                if on_progress:
                    on_progress(value)
            result = work(update)
            if on_done:
                on_done(result)
        except Exception as exc:
            if on_error:
                on_error(str(exc))

    monkeypatch.setattr(
        "qt.views.session_detail_qt.run_async_progress", immediate)
    monkeypatch.setattr(
        QDesktopServices, "openUrl",
        lambda url: opened_folders.append(Path(url.toLocalFile())) or True)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    imported_folders = []
    monkeypatch.setattr(
        "qt.views.session_detail_qt.import_xrk_files",
        lambda files, progress=None: imported_folders.append(files) or SimpleNamespace(
            imported=2, skipped=0, errors=0))
    monkeypatch.setattr(
        "qt.views.session_detail_qt.run_async",
        lambda parent, work, on_done=None, **kwargs: on_done(work()))

    detail._open_all_xrks()

    assert imported_folders == [paths]
    assert not opened_folders
    assert progress[-1] == 100
    assert not detail._xrk_bulk_active
    assert "2 imported" in (
        detail._open_all_xrks_button.toolTip())
    detail.deleteLater()


def test_ibt_download_path_uses_downloads_and_avoids_overwrite(
        tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "Tire Data.ibt").write_bytes(b"old")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    destination = SessionDetailView._available_download_path("Tire Data.ibt")

    assert destination == downloads / "Tire Data (2).ibt"


def test_manage_data_files_uses_checkboxes_and_preserves_nodes():
    _app()
    nodes = [
        google_drive_client.DriveNode(
            id="example-1", name="Example.csv", is_folder=False),
        google_drive_client.DriveNode(
            id="example-2", name="Tire Data.ibt", is_folder=False),
    ]
    dialog = ManageDataFilesDialog(nodes)

    assert dialog._list.selectionMode() == QAbstractItemView.NoSelection
    assert all(
        dialog._list.item(index).checkState() == Qt.Unchecked
        for index in range(dialog._list.count()))
    assert not dialog._rename_button.isEnabled()
    assert not dialog._delete_button.isEnabled()
    assert "Cloud only" in dialog._list.item(0).text()
    dialog._check_all(Qt.Checked)
    assert dialog.selected_nodes() == nodes
    assert not dialog._rename_button.isEnabled()
    assert dialog._delete_button.isEnabled()
    dialog._list.item(1).setCheckState(Qt.Unchecked)
    assert dialog._rename_button.isEnabled()
    assert {button.text() for button in dialog.findChildren(QPushButton)} >= {
        "Select All", "Clear", "Rename Checked…", "Delete Checked…"}
    dialog.deleteLater()


def test_sync_queue_shows_destination_and_transfer_progress(
        tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(
        offline_store, "OFFLINE_ROOT", tmp_path / "sessions")
    source = tmp_path / "run.xrk"
    source.write_bytes(b"telemetry")
    offline_store.queue_local_file("session-1", source)
    item = offline_store.pending_sync_items()[0]

    dialog = SyncQueueDialog()
    dialog.set_connections(True, True)
    dialog.set_event({
        "key": item.key, "state": "syncing", "percent": 42,
        "error": None,
    })
    text = " ".join(
        label.text() for label in dialog.findChildren(QLabel))
    bars = dialog.findChildren(QProgressBar)

    assert "Google Drive" in text
    assert "Catalog: Connected" in text
    assert "Setups and Parts are available locally" in text
    assert "Uploading" in text
    assert [bar.value() for bar in bars] == [42]
    assert not dialog._retry.isEnabled()
    dialog.set_event({
        "key": item.key, "state": "error", "percent": 42,
        "error": "Connection interrupted",
    })
    assert dialog._retry.isEnabled()
    dialog.set_syncing(True)
    assert not dialog._retry.isEnabled()
    assert dialog._retry.text() == "Syncing…"
    dialog.deleteLater()


def test_data_file_management_renames_and_trashes_drive_files(monkeypatch):
    _app()
    detail = SessionDetailView()
    detail._folder_id = "session-1"
    detail._load_media = lambda: None
    remote = google_drive_client.DriveNode(
        id="drive-1", name="Example.csv", is_folder=False)
    pending = google_drive_client.DriveNode(
        id="local-1", name="Pending.csv", is_folder=False,
        pending_upload=True)
    renamed = []
    locally_renamed = []
    trashed = []
    locally_removed = []

    def immediate(_parent, work, on_done=None, on_error=None, **_kwargs):
        try:
            result = work()
        except Exception as exc:
            if on_error:
                on_error(str(exc))
            return None
        if on_done:
            on_done(result)
        return None

    monkeypatch.setattr("qt.views.session_detail_qt.run_async", immediate)
    monkeypatch.setattr(
        google_drive_client, "rename_file",
        lambda file_id, name: renamed.append((file_id, name)))
    monkeypatch.setattr(
        offline_store, "rename_local_file",
        lambda session_id, file_id, name:
        locally_renamed.append((session_id, file_id, name)))
    monkeypatch.setattr(
        google_drive_client, "trash_file", trashed.append)
    monkeypatch.setattr(
        offline_store, "remove_local_file",
        lambda session_id, file_id:
        locally_removed.append((session_id, file_id)))
    monkeypatch.setattr(
        "qt.views.session_detail_qt.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.Yes)

    detail._rename_data_file(remote, "Practice.csv")
    detail._delete_data_files([remote, pending])

    assert renamed == [("drive-1", "Practice.csv")]
    assert locally_renamed == [
        ("session-1", "drive-1", "Practice.csv")]
    assert trashed == ["drive-1"]
    assert locally_removed == [
        ("session-1", "drive-1"), ("session-1", "local-1")]
    detail.deleteLater()


def test_rapid_session_navigation_loads_only_the_final_selection():
    _app()
    sessions = [_session(index) for index in range(12)]
    workspace = SessionWorkspace()
    loads = []
    workspace.detail.load = lambda *args: loads.append(args)
    workspace.open(sessions[0], sessions)
    loads.clear()

    for _ in range(30):
        workspace._navigate_rows(-1)

    assert loads == []
    QTest.qWait(300)
    assert len(loads) == 1
    assert loads[0][0] == workspace._active_id
    workspace.deleteLater()


def test_rapid_session_clicks_load_only_the_final_selection():
    _app()
    sessions = [_session(index) for index in range(8)]
    workspace = SessionWorkspace()
    loads = []
    workspace.detail.load = lambda *args: loads.append(args)
    workspace.open(sessions[0], sessions)
    loads.clear()

    for session in sessions[1:]:
        workspace._on_pick(session)

    assert loads == []
    QTest.qWait(300)
    assert len(loads) == 1
    assert loads[0][:3] == (
        sessions[-1].id, sessions[-1].path, sessions[-1].name)
    workspace.deleteLater()


def test_part_picker_opens_unfiltered_with_current_part_first():
    _app()
    items = [
        InventoryItem(id="a", category="Karts", name="2017 TonyKart"),
        InventoryItem(id="selected", category="Karts", name="2022 RedSpeed"),
        InventoryItem(id="z", category="Karts", name="2025 TonyKart"),
    ]

    dialog = _PartPickerDialog(
        "Karts", items, "2022 RedSpeed", {"item_id": "selected"})

    assert dialog._search.text() == ""
    assert dialog._list.count() == 3
    assert dialog._list.item(0).data(0x0100).id == "selected"
    assert dialog._list.item(0).text() == ""
    assert dialog._list.currentItem() is None
    current_card = dialog._list.itemWidget(dialog._list.item(0))
    assert "(Currently Selected)" in current_card.text()
    assert "#4c2930" in current_card.styleSheet()

    dialog._list.setCurrentRow(1)

    assert "(Currently Selected)" in current_card.text()
    assert "#4c2930" in current_card.styleSheet()
    candidate_card = dialog._list.itemWidget(dialog._list.item(1))
    assert "(New Selection)" in candidate_card.text()
    assert "#237a49" in candidate_card.styleSheet()
    assert dialog._select_btn.isEnabled()
    dialog.deleteLater()


def test_part_picker_supports_name_z_to_a_while_current_stays_pinned():
    _app()
    items = [
        InventoryItem(id="a", category="Karts", name="Alpha"),
        InventoryItem(id="current", category="Karts", name="Middle"),
        InventoryItem(id="z", category="Karts", name="Zulu"),
    ]
    dialog = _PartPickerDialog(
        "Karts", items, "Middle", {"item_id": "current"})

    dialog._sort.setCurrentText("Name Z–A")

    assert [
        dialog._list.item(row).data(Qt.UserRole).id
        for row in range(dialog._list.count())
    ] == ["current", "z", "a"]
    dialog.deleteLater()


def test_old_rear_corner_hub_and_wheels_fill_new_axle_controls():
    _app()
    builder = SetupBuilderView()
    setup = Setup(data={
        "wheels": "AMV 9F",
        "rl_hub_material": "OTK Mag Rear Hub",
        "_inventory_part_revisions": {
            "wheels": {"item_id": "rim-1", "revision": 2,
                       "name": "AMV 9F"},
            "rl_hub_material": {"item_id": "hub-1", "revision": 3,
                                "name": "OTK Mag Rear Hub"},
        },
    })

    builder._populate(setup)

    assert builder._fields["rim_type"].text() == "AMV 9F"
    assert builder._fields["rear_hub_material"].text() == "OTK Mag Rear Hub"
    assert builder._fields["rim_type"]._inventory_revision_ref["item_id"] == "rim-1"
    assert (builder._fields["rear_hub_material"]
            ._inventory_revision_ref["item_id"] == "hub-1")
    assert "rl_hub_material" not in builder._fields
    assert "rr_hub_material" not in builder._fields
    builder.deleteLater()


def test_session_settings_separates_date_name_and_previews_drive_path():
    _app()
    dialog = SessionSettingsDialog(
        "2026-10-12 Halloween Hundred Prac 1",
        "2026-2027 Season / 2026-10-12 Halloween Hundred Prac 1",
        ["2025-26 Season", "2026-2027 Season"],
    )

    assert dialog._date.text() == "2026-10-12"
    assert dialog._name.text() == "Halloween Hundred Prac 1"
    assert dialog.values() == (
        "2026-2027 Season", "2026-10-12 Halloween Hundred Prac 1")
    assert dialog._path_preview.text() == (
        "2026-2027 Season / 2026-10-12 Halloween Hundred Prac 1")
    assert _session_folder_name("", "Halloween 100 2026") == (
        "Halloween 100 2026")
    dialog.deleteLater()


def test_baseline_setups_live_in_parts_and_use_the_setup_builder():
    _app()
    assert PARTS_CATEGORIES[-1] == BASELINE_SETUP_CATEGORY
    parts = PartsView()
    assert parts.current_category() == ALL_PARTS_CATEGORY
    assert parts._category_filter.isEditable()
    baseline_row = parts._category_filter.findData(BASELINE_SETUP_CATEGORY)
    parts._category_filter.setCurrentIndex(baseline_row)
    baseline = Setup(
        id="baseline-1", name="2025 TonyKart · Purdue GP Baseline",
        is_baseline=True, chassis_name="2025 TonyKart", track="Purdue GP")
    parts._items = [baseline]
    parts._render()

    entry = parts._parts_list.item(0)
    assert isinstance(parts._parts_list.itemWidget(entry), _BaselineCard)
    assert parts._metadata_btn.text() == "Open Setup"
    assert parts._stock_btn.isHidden()

    opened = []
    parts.open_baseline.connect(opened.append)
    parts._open_metadata()
    assert opened == [baseline]
    parts.deleteLater()


def test_add_part_requires_a_searchable_category_before_showing_details():
    _app()
    dialog = PartDialog(None)

    assert dialog._category.isEditable()
    assert dialog._selected_category() == ""
    assert not dialog._spec_host.isVisibleTo(dialog)

    spacer_row = dialog._category.findData("Front Spacers")
    dialog._category.setCurrentIndex(spacer_row)

    assert dialog._selected_category() == "Front Spacers"
    assert set(dialog._spec_edits) == {"thickness", "material"}
    dialog.deleteLater()


def test_category_reselection_selects_old_text_and_shows_every_option():
    app = _app()
    install_first_click_select_all(app)
    parts = PartsView()
    tires_row = parts._category_filter.findData("Tires")
    parts._category_filter.setCurrentIndex(tires_row)

    line_edit = parts._category_filter.lineEdit()
    parts._search.setFocus()
    app.processEvents()
    QTest.mouseClick(line_edit, Qt.LeftButton)
    app.processEvents()

    assert line_edit.selectedText() == "Tires"
    assert parts._category_filter.completer().completionCount() == (
        parts._category_filter.count())

    QTest.keyClicks(line_edit, "eng")
    assert line_edit.text() == "eng"

    # Once the user is already working in the field, another click should
    # place the caret normally instead of selecting the entire value again.
    QTest.mouseClick(line_edit, Qt.LeftButton)
    app.processEvents()
    assert line_edit.selectedText() == ""
    parts.deleteLater()


def test_every_line_edit_selects_once_on_initial_mouse_focus():
    app = _app()
    install_first_click_select_all(app)
    host = QWidget()
    layout = QVBoxLayout(host)
    current = QLineEdit("Cut N")
    other = QLineEdit("other")
    layout.addWidget(current)
    layout.addWidget(other)
    host.show()
    other.setFocus()
    app.processEvents()

    QTest.mouseClick(current, Qt.LeftButton)
    app.processEvents()
    assert current.selectedText() == "Cut N"

    QTest.mouseClick(current, Qt.LeftButton)
    app.processEvents()
    assert current.selectedText() == ""
    host.close()


def test_setup_builder_has_clutch_inventory_selector():
    _app()
    builder = SetupBuilderView()

    assert builder._fields["clutch"].isReadOnly()
    assert sf.INVENTORY_FIELD_CATEGORIES["clutch"] == "Clutches"
    builder.deleteLater()


def test_compare_picker_popup_shows_full_setup_labels():
    _app()
    slot = _Slot("A", lambda: None)
    label = (
        "2026-10-17 Halloween 100 Practice 3  \u00b7  Redspeed Race Setup  "
        "(Carson Bowers \u00b7 2022 RedSpeed)")
    slot.combo.resize(220, 30)
    slot.set_entries([(label, Setup(id="setup-1"))])

    assert slot.combo.itemData(0, Qt.ToolTipRole) == label
    assert slot.combo.view().minimumWidth() > slot.combo.width()
    assert slot.combo.completer().popup().minimumWidth() == (
        slot.combo.view().minimumWidth())
    slot.frame.deleteLater()


def test_setup_builder_buffers_form_until_setup_is_populated(monkeypatch):
    _app()
    pending = {}

    def capture_async(_parent, _fn, on_done=None, on_error=None):
        pending["done"] = on_done
        pending["error"] = on_error

    monkeypatch.setattr(
        "qt.views.setup_builder_qt.run_async", capture_async)
    builder = SetupBuilderView()
    setup = Setup(name="Race setup", data={"toe": "ONE BOX OUT"})

    builder.load_setup(
        "session-1", "2026 Season / Race", "Race", setup)

    assert builder._content_stack.currentWidget() is builder._loading_page
    assert builder._fields["toe"].text() == ""

    pending["done"](([], [], [], {}))

    assert builder._content_stack.currentWidget() is builder._scroll
    assert builder._fields["toe"].text() == "ONE BOX OUT"
    builder.deleteLater()


def test_setup_secondary_actions_are_consolidated_into_settings():
    _app()
    builder = SetupBuilderView()
    builder._set_save_status("\u2713  Saved", "#3fb765")
    builder._setup = Setup(id="setup-1", name="Race setup", data={
        "_change_log": [{
            "id": "event-1",
            "action": "updated",
            "actor_name": "Alex Racer",
            "actor_email": "alex@example.edu",
            "changed_at": "2026-08-31T12:00:00+00:00",
            "changes": {"gear_ratio": {
                "before": "11/82", "after": "11/80"}},
        }],
    })

    assert builder._save_status.text() == "\u2713  Saved"
    assert not builder._save_status.isHidden()
    assert builder._options_btn.text() == "Setup Settings"
    assert not hasattr(builder, "_history_btn")
    assert not hasattr(builder, "_dest_btn")
    assert not hasattr(builder, "_copy_btn")
    options = _SetupOptionsDialog(builder)
    assert options.load_button.isEnabled()
    assert options.destination_button.isEnabled()
    assert options.history_button.isEnabled()
    dialog = _SetupHistoryDialog(builder._setup)
    assert dialog.windowTitle() == "Setup History"
    options.deleteLater()
    dialog.deleteLater()
    builder.deleteLater()


def test_run_revision_history_is_readable_and_hides_internal_ids():
    before = [{"id": "internal-1", "run_number": "1", "notes": "Loose"}]
    after = [
        {"id": "internal-1", "run_number": "1", "notes": "Balanced"},
        {"id": "internal-2", "run_number": "2",
         "run_date": "2026-09-03 14:27"},
    ]

    lines = _run_history_lines(before, after)

    assert lines == [
        "Run 1 \u00b7 Session Notes / Driver Feedback: Loose \u2192 Balanced",
        "Added Run 2 (2026-09-03 14:27)",
    ]
    assert "internal-" not in " ".join(lines)


def test_run_setups_are_independent_and_baseline_survives_navigation():
    _app()
    builder = SetupBuilderView()
    key = next(iter(builder._fields))
    builder._set_field_text(builder._fields[key], "baseline")
    builder._add_run()
    assert builder._field_text(builder._fields[key]) == "baseline"
    builder._set_field_text(builder._fields[key], "run two")
    builder._add_run()
    assert builder._field_text(builder._fields[key]) == "run two"
    builder._go_to_run(1)
    builder._set_field_text(builder._fields[key], "changed two")
    builder._go_to_run(0)
    assert builder._field_text(builder._fields[key]) == "baseline"
    assert not builder._remove_run_btn.isEnabled()
    builder._go_to_run(2)
    assert builder._field_text(builder._fields[key]) == "run two"
    state = builder._form_state()
    assert dict(state["fields"])[key] == "baseline"
    assert state["runs"][1][sf.RUN_SETUP_KEY]["values"][key] == "changed two"
    builder._initial_state = state
    builder._go_to_run(0)
    assert builder._form_state() == state
    builder.deleteLater()


def test_blank_baseline_run_is_preserved_when_later_runs_exist():
    _app()
    builder = SetupBuilderView()
    builder._load_runs([{"id": "baseline"}, {"id": "second", "notes": "Test"}])
    runs = builder._serialized_runs()
    assert [run["id"] for run in runs] == ["baseline", "second"]
    builder.deleteLater()


def test_run_setup_history_is_field_level_not_snapshot_json():
    key = sf.all_value_fields()[0].key
    before = [{"id": "private-id", sf.RUN_SETUP_KEY: {
        "values": {key: "old"}, "inventory_refs": {"item_id": "private-part"}}}]
    after = [{"id": "private-id", sf.RUN_SETUP_KEY: {
        "values": {key: "new"}, "inventory_refs": {"item_id": "private-part"}}}]
    lines = _run_history_lines(before, after)
    assert len(lines) == 1
    assert "old → new" in lines[0]
    assert "private-" not in lines[0]


def test_setup_builder_saves_locally_when_identity_service_is_offline(
        tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(
        offline_store, "OFFLINE_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.google_drive_client.audit_user_or_none",
        lambda **_kwargs: None)
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.create_setup",
        lambda _setup: (_ for _ in ()).throw(
            AssertionError("pending identity must not upload yet")))

    def _immediate(_parent, work, on_done=None, on_error=None, **_kwargs):
        try:
            result = work()
        except Exception as exc:
            if on_error:
                on_error(str(exc))
            return None
        if on_done:
            on_done(result)
        return None

    monkeypatch.setattr("qt.views.setup_builder_qt.run_async", _immediate)
    builder = SetupBuilderView()
    builder._folder_id = "session-1"
    builder._folder_path = "2026 Season / Offline Test"
    builder._folder_name = "Offline Test"
    builder._name.setText("Offline failsafe setup")
    saved = []
    builder.saved.connect(saved.append)

    builder._save()

    assert len(saved) == 1
    assert saved[0]._offline_pending is True
    assert offline_store.pending_count("session-1") == 1
    assert offline_store.load_setups("session-1")[0].name == (
        "Offline failsafe setup")
    assert "Saved offline" in builder._save_status.text()
    builder.deleteLater()


def test_setup_sheet_supports_unlimited_click_through_runs():
    _app()
    builder = SetupBuilderView()
    builder._run_fields["run_number"].setText("1")
    builder._feedback.setPlainText("Initial balance was loose.")

    for number in range(2, 13):
        builder._add_run()
        builder._run_fields["run_number"].setText(str(number))
        builder._feedback.setPlainText(f"Full notes for run {number}.")

    runs = builder._serialized_runs()
    assert len(runs) == 12
    assert builder._run_position.text() == "Run 12 of 12"
    assert runs[0]["notes"] == "Initial balance was loose."
    assert runs[-1]["notes"] == "Full notes for run 12."

    builder._previous_run()
    assert builder._run_position.text() == "Run 11 of 12"
    assert builder._feedback.toPlainText() == "Full notes for run 11."
    assert builder._feedback.minimumHeight() >= 190
    assert builder._notes.minimumHeight() >= 190
    builder.deleteLater()


def test_legacy_single_run_fields_open_in_new_run_log():
    setup = Setup(data={
        "run_number": "4",
        "session_number": "2",
        "fastest_lap": 48.91,
        "driver_feedback": "Needed more front grip.",
    })

    runs = SetupBuilderView._runs_from_setup(setup)

    assert len(runs) == 1
    assert runs[0]["run_number"] == "4"
    assert runs[0]["session_number"] == "2"
    assert runs[0]["fastest_lap"] == 48.91
    assert runs[0]["notes"] == "Needed more front grip."
    assert runs[0]["id"]


def test_new_run_defaults_to_separate_editable_local_date_and_time():
    before = datetime.now().replace(second=0, microsecond=0)
    run = SetupBuilderView._blank_run(3)
    after = datetime.now().replace(second=0, microsecond=0)

    timestamp = datetime.strptime(
        f'{run["run_date"]} {run["run_time"]}', "%Y-%m-%d %I:%M %p")
    assert before <= timestamp <= after
    assert run["run_number"] == "3"

    _app()
    builder = SetupBuilderView()
    date_edit = builder._run_fields["run_date"]
    time_edit = builder._run_fields["run_time"]
    assert date_edit.isReadOnly()
    assert isinstance(time_edit, QComboBox)
    assert time_edit.isEditable()
    choices = [time_edit.itemText(i) for i in range(time_edit.count())]
    assert any(choice.endswith("AM") for choice in choices)
    assert any(choice.endswith("PM") for choice in choices)
    time_edit.setEditText("2:27 PM")
    assert time_edit.currentText() == "2:27 PM"
    assert "session_number" not in builder._run_fields
    builder.deleteLater()


def test_combined_run_timestamp_is_split_for_existing_setups():
    run = _split_legacy_run_timestamp({
        "id": "run-1", "run_date": "2026-09-03 14:27"})

    assert run["run_date"] == "2026-09-03"
    assert run["run_time"] == "2:27 PM"


def test_settings_identity_is_read_only_google_profile():
    _app()
    settings = SettingsView()
    labels = [label.text() for label in settings.findChildren(QLabel)]

    assert "Signed-in editor" in labels
    assert "Google email" in labels
    assert "Your Name" not in labels
    assert set(settings.findChildren(QLineEdit)) == {
        settings._drive_folder, settings._oauth_file, settings._team_name,
        settings._invite_email, settings._join_code}
    settings.deleteLater()


def test_global_setups_page_uses_downloaded_sessions_offline(monkeypatch):
    _app()
    cached = Setup(
        id="setup-1", drive_folder_id="session-1", name="Offline Race Setup")
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.load_setup_catalog",
        lambda: [cached])

    setups = SetupsView._offline_setups()

    assert [(setup.id, setup.name) for setup in setups] == [
        ("setup-1", "Offline Race Setup")]


def test_global_setups_page_can_delete_a_setup(monkeypatch):
    _app()
    setup = Setup(id="setup-1", name="Unwanted Setup")
    deleted = []
    queued = []
    completed = []

    monkeypatch.setattr(
        "qt.views.setups_qt.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(
        "qt.views.setups_qt.db.delete_setup",
        lambda setup_id: deleted.append(setup_id))
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.resolve_setup_id",
        lambda setup_id: setup_id)
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.queue_setup_deletion",
        lambda setup_id, session_id=None: queued.append(
            (setup_id, session_id)))
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.complete_setup_deletion",
        lambda setup_id: completed.append(setup_id))

    def immediate(_parent, fn, on_done=None, on_error=None):
        result = fn()
        if on_done:
            on_done(result)

    monkeypatch.setattr("qt.views.setups_qt.run_async", immediate)
    view = SetupsView()
    view._setups = [setup]

    view._delete_setup(setup)

    assert deleted == ["setup-1"]
    assert queued == [("setup-1", None)]
    assert completed == ["setup-1"]
    assert view._setups == []
    view.deleteLater()


def test_global_setup_delete_queues_network_failure(monkeypatch):
    _app()
    setup = Setup(id="setup-1", name="Unwanted Setup")
    queued = []
    monkeypatch.setattr(
        "qt.views.setups_qt.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.resolve_setup_id",
        lambda setup_id: setup_id)
    monkeypatch.setattr(
        "qt.views.setups_qt.offline_store.queue_setup_deletion",
        lambda setup_id, session_id=None: queued.append(setup_id))
    monkeypatch.setattr(
        "qt.views.setups_qt.db.delete_setup",
        lambda _setup_id: (_ for _ in ()).throw(
            OSError("[WinError 10035] socket unavailable")))

    def immediate(_parent, fn, on_done=None, on_error=None):
        result = fn()
        if on_done:
            on_done(result)

    monkeypatch.setattr("qt.views.setups_qt.run_async", immediate)
    view = SetupsView()
    view._setups = [setup]

    view._delete_setup(setup)

    assert queued == ["setup-1"]
    assert view._setups == []
    view.deleteLater()


def test_global_setups_page_preserves_scroll_when_card_is_deleted():
    app = _app()
    view = SetupsView()
    view.resize(800, 360)
    view._setups = [
        Setup(id=f"setup-{index}", name=f"Setup {index:02d}")
        for index in range(30)
    ]
    view._render()
    view.show()
    app.processEvents()
    scrollbar = view._all_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 100
    scrollbar.setValue(100)

    removed = view._setups.pop(10)
    view._remove_setup_card(removed)
    QTest.qWait(25)

    assert scrollbar.value() == 100
    view.close()
    view.deleteLater()


def test_parts_editor_falls_back_to_durable_local_update(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(
        offline_store, "OFFLINE_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(
        google_drive_client, "audit_user_or_none",
        lambda **_kwargs: google_drive_client.GoogleUser(
            "Alex Racer", "alex@example.edu"))
    monkeypatch.setattr(
        "qt.views.parts_qt.db.update_inventory_item",
        lambda _item: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr("qt.views.parts_qt.db.check_connection", lambda: False)
    current = InventoryItem(
        id="part-1", revision=1, category="Axles", name="N Axle",
        updated_at="2026-09-01T10:00:00+00:00")
    edited = InventoryItem.from_dict(current.to_dict(include_nulls=True))
    edited.id = current.id
    edited.revision = current.revision
    edited.updated_at = current.updated_at
    edited.name = "N Axle · serviced"

    saved = _save_inventory_changes(current, edited)

    assert saved._offline_pending is True
    assert offline_store.pending_count() == 1
    assert offline_store.load_inventory()[0].name == "N Axle · serviced"


def test_parts_can_sort_names_z_to_a():
    _app()
    parts = PartsView()
    parts._items = [
        InventoryItem(id="a", category="Tires", name="Alpha"),
        InventoryItem(id="z", category="Engines", name="Zulu"),
    ]

    parts._sort.setCurrentIndex(parts._sort.findData("name_desc"))
    parts._render()

    assert [
        parts._parts_list.item(row).data(Qt.UserRole + 1)
        for row in range(parts._parts_list.count())
    ] == ["z", "a"]
    parts.deleteLater()


def test_all_parts_groups_by_part_type_then_name_and_labels_each_card():
    _app()
    parts = PartsView()
    parts._items = [
        InventoryItem(id="t-z", category="Tires", name="Zulu"),
        InventoryItem(id="e-l2", category="Exhausts", name="L2 Pipe"),
        InventoryItem(id="t-a", category="Tires", name="Alpha"),
        Setup(id="baseline", name="RedSpeed", is_baseline=True),
    ]

    assert parts.current_category() == ALL_PARTS_CATEGORY
    assert parts._sort.currentData() == "category_name"
    parts._render()

    assert [
        parts._parts_list.item(row).data(Qt.UserRole + 1)
        for row in range(parts._parts_list.count())
    ] == ["baseline", "e-l2", "t-a", "t-z"]
    exhaust_card = parts._parts_list.itemWidget(parts._parts_list.item(1))
    card_text = [label.text() for label in exhaust_card.findChildren(QLabel)]
    assert "Part Type" in card_text
    assert "Exhausts" in card_text
    parts.deleteLater()


def test_baseline_name_includes_kart_and_track_and_prefill_button_is_gone():
    _app()
    builder = SetupBuilderView()

    assert _baseline_name("2025 TonyKart", "Purdue GP") == (
        "2025 TonyKart · Purdue GP Baseline")
    assert not hasattr(builder, "_prefill_btn")
    assert "Load another setup" in builder._options_btn.toolTip()
    builder._is_baseline = True
    builder._chassis_cb.setText("2025 TonyKart")
    builder._track.setText("Purdue GP")
    assert builder._name.placeholderText() == (
        "2025 TonyKart · Purdue GP Baseline")
    builder.deleteLater()
