"""Session detail page (Qt) — one Drive folder shown as a session.

Two tabs:
  * Setup — the kart setups saved for this session (view / edit / delete)
  * Data  — the session's .xrk telemetry (download / open, upload), grouped by kart
"""
from __future__ import annotations
import shutil
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QFrame, QTabWidget,
                               QMessageBox, QFileDialog, QDialog,
                               QProgressBar, QFormLayout, QComboBox,
                               QDialogButtonBox, QLineEdit, QListWidget,
                               QListWidgetItem, QAbstractItemView, QInputDialog)
import config
from qt.responsive import FlowLayout
import session_types
import database.client as db
import offline_store
import setup_fields as sf
from database.models import Setup
from utils import google_drive_client
from utils import team_naming
from utils.format import format_size
from utils.media_timestamp import format_media_timestamp
from utils.race_studio import import_xrk_files
from utils.xrk_meta import parse_xrk_filename
from qt.workers import run_async, run_async_progress
from qt.widgets import DatePickerEdit
from qt.views.assign_files_qt import AssignFilesDialog
from qt.views.tire_data_merge_qt import TireDataMergeDialog
from qt.views.sessions_qt import _parse, _season_sort_key


def _session_folder_name(date_text: str, event_name: str) -> str:
    return " ".join(
        part for part in (date_text.strip(), event_name.strip()) if part)


class SessionSettingsDialog(QDialog):
    """Edit the user-owned metadata encoded by a Drive session folder."""

    def __init__(self, folder_name, folder_path, seasons, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session Settings")
        self.setMinimumWidth(500)
        form = QFormLayout(self)

        current_date, current_name = _parse(folder_name or "")
        path_parts = [
            part.strip() for part in (folder_path or "").split(" / ")
            if part.strip()]
        current_season = path_parts[0] if len(path_parts) > 1 else ""

        self._name = QLineEdit(current_name or "")
        self._name.setPlaceholderText("Halloween 100 2026")
        form.addRow("Session / event name", self._name)

        self._date = DatePickerEdit()
        self._date.setText(current_date or "")
        form.addRow("Date", self._date)

        self._season = QComboBox()
        self._season.setEditable(True)
        choices = sorted(
            {value for value in seasons if value} | ({current_season}
             if current_season else set()),
            key=_season_sort_key,
        )
        self._season.addItems(choices)
        self._season.setCurrentText(current_season)
        self._season.setToolTip(
            "Changing this moves the existing session folder and its contents")
        form.addRow("Season / parent folder", self._season)

        self._path_preview = QLabel()
        self._path_preview.setObjectName("Muted")
        self._path_preview.setWordWrap(True)
        form.addRow("Drive path", self._path_preview)
        self._name.textChanged.connect(self._update_preview)
        self._date.textChanged.connect(self._update_preview)
        self._season.currentTextChanged.connect(self._update_preview)
        self._update_preview()

        hint = QLabel(
            "The same Drive folder is renamed or moved, so its files, videos, "
            "setups, and folder ID stay linked.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_preview(self, *_args):
        season, folder_name = self.values()
        self._path_preview.setText(
            " / ".join(part for part in (season, folder_name) if part)
            or "—")

    def values(self):
        return (
            self._season.currentText().strip(),
            _session_folder_name(self._date.text(), self._name.text()),
        )


class ManageDataFilesDialog(QDialog):
    """Rename one or delete several files from a session's Data tab."""

    def __init__(self, nodes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Data Files")
        self.resize(650, 480)
        self.action = None
        self.new_name = None
        root = QVBoxLayout(self)
        title = QLabel("Manage Data Files")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "Check one file to rename, or check several files to delete. "
            "Drive files move to Trash; pending local uploads are canceled.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        for node in nodes:
            if node.pending_upload:
                location = "Not yet on Drive · Copy on this device"
            elif node.local_path:
                location = "In Google Drive · Copy on this device"
            else:
                location = "In Google Drive · Cloud only"
            label = f"{node.name}  ·  {location}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, node)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self._list.addItem(item)
        root.addWidget(self._list, 1)
        self._selection_status = QLabel("0 files checked")
        self._selection_status.setObjectName("Muted")
        root.addWidget(self._selection_status)
        row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.setObjectName("Ghost")
        select_all.clicked.connect(lambda: self._check_all(Qt.Checked))
        row.addWidget(select_all)
        clear = QPushButton("Clear")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self._check_all(Qt.Unchecked))
        row.addWidget(clear)
        self._rename_button = QPushButton("Rename Checked…")
        self._rename_button.setObjectName("Ghost")
        self._rename_button.clicked.connect(self._choose_rename)
        row.addWidget(self._rename_button)
        self._delete_button = QPushButton("Delete Checked…")
        self._delete_button.setObjectName("Danger")
        self._delete_button.clicked.connect(self._choose_delete)
        row.addWidget(self._delete_button)
        row.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        root.addLayout(row)
        self._list.itemChanged.connect(self._update_selection)
        self._update_selection()

    def selected_nodes(self):
        return [
            self._list.item(index).data(Qt.UserRole)
            for index in range(self._list.count())
            if self._list.item(index).checkState() == Qt.Checked
        ]

    def _check_all(self, state):
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(state)

    def _update_selection(self, *_args):
        count = len(self.selected_nodes())
        self._selection_status.setText(
            f"{count} file{'s' if count != 1 else ''} checked")
        self._rename_button.setEnabled(count == 1)
        self._delete_button.setEnabled(count > 0)

    def _choose_rename(self):
        selected = self.selected_nodes()
        if len(selected) != 1:
            QMessageBox.information(
                self, "Check One File", "Check exactly one file to rename.")
            return
        old_name = selected[0].name
        new_name, accepted = QInputDialog.getText(
            self, "Rename Data File", "New filename", text=old_name)
        new_name = new_name.strip()
        if not accepted:
            return
        if (not new_name or new_name in {".", ".."}
                or any(char in new_name for char in '<>:"/\\|?*')):
            QMessageBox.warning(
                self, "Invalid Filename",
                "Use a filename without path or Windows filename characters.")
            return
        if Path(new_name).suffix.casefold() != Path(old_name).suffix.casefold():
            QMessageBox.warning(
                self, "Keep File Type",
                f"Keep the {Path(old_name).suffix or 'current'} file extension.")
            return
        self.action = "rename"
        self.new_name = new_name
        self.accept()

    def _choose_delete(self):
        if not self.selected_nodes():
            QMessageBox.information(
                self, "Check Files", "Check one or more files to delete.")
            return
        self.action = "delete"
        self.accept()


class SessionDetailView(QWidget):
    back = Signal()
    delete_session = Signal(str)
    setups_changed = Signal()
    session_updated = Signal(str, str, str, str)  # id, path, name, season
    edit_setup = Signal(object, str, str, str)   # setup|None, folder_id, path, name
    view_setup = Signal(object)                  # setup
    sync_status_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder_id = None
        self._folder_path = ""
        self._folder_name = ""
        self._season = ""
        self._available_seasons = []
        self._load_generation = 0
        self._video_transfer_active = False
        self._video_buttons = []
        self._video_nodes = []
        self._videos_offline = False
        self._data_nodes = []
        self._xrk_nodes = []
        self._data_open_buttons = []
        self._xrk_bulk_active = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(8)

        top = FlowLayout()
        backb = QPushButton("\u2190  Back to Sessions")
        backb.setObjectName("Primary")
        backb.setCursor(Qt.PointingHandCursor)
        backb.clicked.connect(self.back.emit)
        top.addWidget(backb)
        self._crumb = QLabel("")
        self._crumb.setWordWrap(True)
        self._crumb.setObjectName("Muted")
        top.addWidget(self._crumb)
        top.addStretch(1)
        self._settings_button = QPushButton()
        self._settings_button.setObjectName("Ghost")
        self._settings_button.setIcon(QIcon(str(
            config.ASSETS_DIR / "icon_settings.svg")))
        self._settings_button.setIconSize(QSize(20, 20))
        self._settings_button.setFixedSize(42, 38)
        self._settings_button.setCursor(Qt.PointingHandCursor)
        self._settings_button.setToolTip("Session settings")
        self._settings_button.setAccessibleName("Session settings")
        self._settings_button.clicked.connect(self._edit_session_settings)
        top.addWidget(self._settings_button)
        self._type_button = QPushButton("Session type")
        self._type_button.setObjectName("Secondary")
        self._type_button.clicked.connect(self._edit_session_type)
        top.addWidget(self._type_button)
        delete_session = QPushButton("Delete Session")
        delete_session.setObjectName("Danger")
        delete_session.setCursor(Qt.PointingHandCursor)
        delete_session.clicked.connect(
            lambda: self.delete_session.emit(self._folder_id)
            if self._folder_id else None)
        top.addWidget(delete_session)
        root.addLayout(top)

        self._title = QLabel("Session")
        self._title.setWordWrap(True)
        self._title.setObjectName("H1")
        root.addWidget(self._title)

        offline_row = QHBoxLayout()
        self._offline_status = QLabel("")
        self._offline_status.setObjectName("Muted")
        offline_row.addWidget(self._offline_status, 1)
        root.addLayout(offline_row)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        # Setup tab
        setup_tab = QWidget()
        st = QVBoxLayout(setup_tab)
        st.setContentsMargins(14, 12, 14, 12)
        sh = FlowLayout()
        sl = QLabel("Setups")
        sl.setObjectName("H2")
        sh.addWidget(sl)
        sh.addStretch(1)
        newb = QPushButton("New Setup")
        newb.setObjectName("Primary")
        newb.setCursor(Qt.PointingHandCursor)
        newb.clicked.connect(lambda: self.edit_setup.emit(
            None, self._folder_id, self._folder_path, self._folder_name))
        sh.addWidget(newb)
        st.addLayout(sh)
        self._setups_scroll, self._setups_lay = self._scroll_area()
        st.addWidget(self._setups_scroll, 1)
        self._tabs.addTab(setup_tab, "Setup/Notes")

        # Data tab
        data_tab = QWidget()
        dt = QVBoxLayout(data_tab)
        dt.setContentsMargins(14, 12, 14, 12)
        dh = FlowLayout()
        dl = QLabel("Session Data")
        dl.setObjectName("H2")
        dh.addWidget(dl)
        dh.addStretch(1)
        assignb = QPushButton("Assign Names")
        assignb.setObjectName("Ghost")
        assignb.setCursor(Qt.PointingHandCursor)
        assignb.setToolTip(
            "Link each .xrk to a driver, then rename & sort into driver folders")
        assignb.clicked.connect(self._assign_files)
        dh.addWidget(assignb)
        mergeb = QPushButton("Merge Tires")
        mergeb.setObjectName("Primary")
        mergeb.setCursor(Qt.PointingHandCursor)
        mergeb.setToolTip(
            "Align a Race Studio CSV and TireLogger CSV using their RPM traces")
        mergeb.clicked.connect(self._merge_tire_data)
        dh.addWidget(mergeb)
        manageb = QPushButton("Manage")
        manageb.setObjectName("Ghost")
        manageb.setCursor(Qt.PointingHandCursor)
        manageb.setToolTip("Rename or delete session data files")
        manageb.clicked.connect(self._manage_data_files)
        dh.addWidget(manageb)
        upb = QPushButton("+ Add Files")
        upb.setObjectName("Ghost")
        upb.setCursor(Qt.PointingHandCursor)
        upb.setToolTip(
            "Select one or many Race Studio 3 files and add them to this session")
        upb.clicked.connect(self._upload)
        dh.addWidget(upb)
        self._open_all_xrks_button = QPushButton(
            "Open All in Race Studio")
        self._open_all_xrks_button.setObjectName("Primary")
        self._open_all_xrks_button.setCursor(Qt.PointingHandCursor)
        self._open_all_xrks_button.setToolTip(
            "Open Race Studio and import every XRK, using saved copies first")
        self._open_all_xrks_button.clicked.connect(self._open_all_xrks)
        self._open_all_xrks_button.setVisible(False)
        dh.addWidget(self._open_all_xrks_button)
        dt.addLayout(dh)

        self._xrk_bulk_progress = QProgressBar()
        self._xrk_bulk_progress.setRange(0, 100)
        self._xrk_bulk_progress.setTextVisible(True)
        self._xrk_bulk_progress.setFixedHeight(20)
        self._xrk_bulk_progress.setVisible(False)
        dt.addWidget(self._xrk_bulk_progress)
        self._data_scroll, self._data_lay = self._scroll_area()
        dt.addWidget(self._data_scroll, 1)
        self._tabs.addTab(data_tab, "Data")

        # Video Review tab
        video_tab = QWidget()
        vt = QVBoxLayout(video_tab)
        vt.setContentsMargins(14, 12, 14, 12)
        vh = FlowLayout()
        vl = QLabel("Videos")
        vl.setObjectName("H2")
        vh.addWidget(vl)
        self._video_status = QLabel("")
        self._video_status.setObjectName("Muted")
        vh.addWidget(self._video_status)
        vh.addStretch(1)
        self._refresh_videos = QPushButton("Refresh")
        self._refresh_videos.setObjectName("Ghost")
        self._refresh_videos.setCursor(Qt.PointingHandCursor)
        self._refresh_videos.clicked.connect(self._load_videos)
        vh.addWidget(self._refresh_videos)
        self._download_all_videos = QPushButton("Download All")
        self._download_all_videos.setObjectName("Ghost")
        self._download_all_videos.clicked.connect(self._cache_all_videos)
        vh.addWidget(self._download_all_videos)
        upload_video = QPushButton("+ Add Video")
        upload_video.setObjectName("Ghost")
        upload_video.setCursor(Qt.PointingHandCursor)
        upload_video.clicked.connect(self._upload_video)
        vh.addWidget(upload_video)
        vt.addLayout(vh)
        self._video_progress = QProgressBar()
        self._video_progress.setRange(0, 100)
        self._video_progress.setTextVisible(True)
        self._video_progress.setFixedHeight(22)
        self._video_progress.setVisible(False)
        vt.addWidget(self._video_progress)
        self._video_scroll, self._video_lay = self._scroll_area()
        vt.addWidget(self._video_scroll, 1)
        self._tabs.addTab(video_tab, "Video Review")

    def _scroll_area(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 4, 8, 4)
        lay.setSpacing(6)
        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll, lay

    @staticmethod
    def _clear(lay):
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Public ────────────────────────────────────────────────────────────────

    def load(self, folder_id, folder_path, folder_name, season="", seasons=None):
        self._load_generation += 1
        self._folder_id = folder_id
        self._folder_path = folder_path
        self._folder_name = folder_name
        self._season = season or ""
        self._available_seasons = list(seasons or [])
        self._crumb.setText(folder_path or "")
        self._title.setText(folder_name or "Session")
        self._type_button.setText(session_types.TYPES[session_types.classify(folder_id, folder_name, folder_path)] + " session")
        self._update_offline_status()
        self.refresh()

    def _update_offline_status(self, message=None):
        info = offline_store.session_info(self._folder_id)
        if message:
            self._offline_status.setText(message)
        elif info and info.downloaded_at:
            stamp = info.downloaded_at[:16].replace("T", " ")
            pending = (
                f" · {info.pending_count} waiting for cloud upload"
                if info.pending_count else " · cloud uploads complete")
            videos = " · videos included" if info.include_videos else ""
            xrks = " · XRKs included" if info.include_xrks else ""
            self._offline_status.setText(
                f"Setup sheets cached · {info.file_count} offline files · "
                f"updated {stamp}{xrks}{videos}{pending}")
        elif info and info.pending_count:
            self._offline_status.setText(
                f"Saved on this device · {info.pending_count} waiting for "
                "cloud upload")
        elif info:
            self._offline_status.setText(
                "Setup sheets cached automatically · session files remain in Drive")
        else:
            self._offline_status.setText(
                "Setup sheets cache automatically · session files remain in Drive")

    def _queue_local_files(self, paths):
        paths = list(paths or [])
        if not paths or not self._folder_id:
            return
        try:
            for path in paths:
                offline_store.queue_local_file(
                    self._folder_id, path, self._folder_name,
                    self._folder_path, self._season)
        except Exception as exc:
            QMessageBox.critical(self, "Local Save Failed", str(exc))
            return
        self._update_offline_status()
        self.sync_status_changed.emit()
        self._render_local_media()

        def _synced(report):
            self._update_offline_status(
                (f"\u2713 {len(paths)} file(s) synced to Google Drive."
                 if not report.errors else
                 "Files are saved locally; failed uploads will retry automatically."))
            self.sync_status_changed.emit()
            self._load_media()

        run_async(
            self, offline_store.sync_pending, on_done=_synced,
            on_error=lambda _message: self._sync_attempt_failed())

    def _sync_attempt_failed(self):
        self._update_offline_status(
            "Files are saved locally and waiting for internet.")
        self.sync_status_changed.emit()

    def _upload_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add session video", "",
            "Video files (*.mp4 *.mov);;All files (*.*)")
        self._queue_local_files(paths)

    def refresh(self):
        self._load_setups()
        self._load_media()

    def refresh_setups(self):
        """Reload setup records without rescanning unchanged Drive telemetry."""
        self._load_setups()

    def _edit_session_type(self):
        if not self._folder_id:
            return
        kinds = list(session_types.TYPES)
        current = session_types.classify(self._folder_id, self._folder_name, self._folder_path)
        label, accepted = QInputDialog.getItem(
            self, "Session type", "Classify this session for filters and maintenance intervals:",
            list(session_types.TYPES.values()), kinds.index(current), False)
        if not accepted:
            return
        kind = kinds[list(session_types.TYPES.values()).index(label)]
        folder_id, folder_path, folder_name, season = self._folder_id, self._folder_path, self._folder_name, self._season
        self._type_button.setEnabled(False)

        def done(saved):
            self._type_button.setEnabled(True)
            if self._folder_id == folder_id:
                self._type_button.setText(session_types.TYPES[saved] + " session")
            self.session_updated.emit(folder_id, folder_path, folder_name, season)

        def failed(message):
            self._type_button.setEnabled(True)
            QMessageBox.warning(self, "Session type was not saved", message)

        run_async(self, lambda: session_types.save(folder_id, kind), on_done=done, on_error=failed)

    def _edit_session_settings(self):
        if not self._folder_id:
            return
        dialog = SessionSettingsDialog(
            self._folder_name, self._folder_path,
            self._available_seasons, self)
        if dialog.exec() != QDialog.Accepted:
            return
        new_season, new_name = dialog.values()
        if not new_name:
            QMessageBox.information(
                self, "Name Required", "Enter a session or event name.")
            return
        if any(separator in value for value in (new_season, new_name)
               for separator in ("/", "\\")):
            QMessageBox.information(
                self, "Invalid Folder Name",
                "Session and season names cannot contain / or \\ characters.")
            return

        folder_id = self._folder_id
        new_path = " / ".join(
            part for part in (new_season, new_name) if part)
        if new_name == self._folder_name and new_path == self._folder_path:
            return
        self._settings_button.setEnabled(False)

        def _update():
            google_drive_client.update_session_folder(
                folder_id, new_name, new_season or None)
            warning = None
            try:
                db.update_setup_folder_path(folder_id, new_path)
            except Exception as exc:  # Drive edit succeeded; keep UI truthful.
                warning = str(exc)
            return new_path, warning

        def _updated(result):
            path, warning = result
            self._settings_button.setEnabled(True)
            self._folder_name = new_name
            self._folder_path = path
            self._season = new_season
            self._crumb.setText(path)
            self._title.setText(new_name)
            self.session_updated.emit(
                folder_id, path, new_name, new_season)
            if warning:
                QMessageBox.warning(
                    self, "Session Updated",
                    "The Drive folder was updated, but setup breadcrumbs could "
                    f"not be synchronized yet:\n\n{warning}")

        def _error(message):
            self._settings_button.setEnabled(True)
            QMessageBox.critical(self, "Update Session Failed", message)

        run_async(
            self, _update, on_done=_updated, on_error=_error)

    # ── Setups ────────────────────────────────────────────────────────────────

    def _load_setups(self):
        self._clear(self._setups_lay)
        if not self._folder_id:
            return
        loading = QLabel("Loading\u2026")
        loading.setObjectName("Muted")
        self._setups_lay.addWidget(loading)
        self._setups_lay.addStretch(1)
        fid = self._folder_id
        generation = self._load_generation
        cached_now = offline_store.load_setups(fid)
        if cached_now:
            self._render_setups(
                cached_now, "Local offline copy — checking Drive catalog…")

        def _render(setups, err=None):
            if generation == self._load_generation and fid == self._folder_id:
                self._render_setups(setups, err)

        def _loaded(setups):
            offline_store.cache_setups(
                fid, setups, self._folder_name, self._folder_path, self._season)
            _render(setups)

        def _failed(message):
            cached = offline_store.load_setups(fid)
            _render(
                cached,
                "Offline copy — cloud refresh unavailable" if cached
                else message)

        run_async(self, lambda: db.fetch_setups_by_folder(fid),
                  on_done=_loaded, on_error=_failed)

    def _render_setups(self, setups, err=None):
        self._clear(self._setups_lay)
        if err:
            msg = QLabel(f"\u26A0 {err}")
            msg.setStyleSheet(
                "color: #e6a020;" if setups else "color: #e74c3c;")
            msg.setWordWrap(True)
            self._setups_lay.addWidget(msg)
        if not setups:
            msg = QLabel("No setups yet. Click 'New Setup'.")
            msg.setObjectName("Muted")
            self._setups_lay.addWidget(msg)
        else:
            for s in setups:
                self._setups_lay.addWidget(self._setup_card(s))
        self._setups_lay.addStretch(1)

    def _setup_card(self, setup: Setup) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(4)

        name = QLabel(setup.name or "Setup")
        name.setObjectName("CardTitle")
        lay.addWidget(name)

        meta = []
        if setup.driver_name:
            meta.append(f"{setup.driver_name}")
        num = setup.get("kart_number") or sf.kart_number_for(setup.chassis_name)
        if setup.chassis_name:
            meta.append(f"{setup.chassis_name}" + (f" #{num}" if num else ""))
        elif num:
            meta.append(f"#{num}")
        if setup.get("tire_compound"):
            meta.append(f"{setup.get('tire_compound')}")
        tele = setup.get("telemetry") or []
        if tele:
            meta.append(f"{len(tele)} files")
        latest_change = db.latest_setup_change(setup)
        if latest_change:
            meta.append(
                f"Last changed by "
                f"{latest_change.get('actor_name') or 'Unknown'}")
        elif setup.created_by:
            meta.append(f"Created by {setup.created_by}")
        meta.append(
            "Waiting to sync" if getattr(setup, "_offline_pending", False)
            else "\u2713 Saved in catalog")
        sub = QLabel("  \u00b7  ".join(meta) if meta else "No details yet")
        sub.setObjectName("CardSub")
        lay.addWidget(sub)

        btns = QHBoxLayout()
        btns.addStretch(1)
        openb = QPushButton("Open \u2192")
        openb.setObjectName("Primary")
        openb.clicked.connect(lambda: self.edit_setup.emit(
            setup, self._folder_id, self._folder_path, self._folder_name))
        openb.setCursor(Qt.PointingHandCursor)
        btns.addWidget(openb)
        deleteb = QPushButton("Delete")
        deleteb.setObjectName("Danger")
        deleteb.setCursor(Qt.PointingHandCursor)
        deleteb.clicked.connect(lambda: self._delete_setup(setup))
        btns.addWidget(deleteb)
        lay.addLayout(btns)

        # Double-click anywhere on the card opens it, mirroring the Open button.
        card.setCursor(Qt.PointingHandCursor)
        card.setToolTip("Double-click to open")
        card.mouseDoubleClickEvent = lambda _e, s=setup: self.edit_setup.emit(
            s, self._folder_id, self._folder_path, self._folder_name)
        return card

    def _delete_setup(self, setup: Setup):
        answer = QMessageBox.warning(
            self,
            "Delete Setup?",
            f"Permanently delete “{setup.name or 'this setup'}”?\n\n"
            "The session and its .xrk telemetry will not be deleted.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        def _deleted(_result):
            self._load_setups()
            self.setups_changed.emit()

        run_async(
            self,
            lambda: db.delete_setup(setup.id),
            on_done=_deleted,
            on_error=lambda message: QMessageBox.critical(
                self, "Delete Setup Failed", message),
        )

    # ── Data ────────────────────────────────────────────────────────────────

    def _load_media(self):
        """Populate telemetry and videos from one Drive subtree traversal."""
        self._clear(self._data_lay)
        self._clear(self._video_lay)
        if not self._folder_id:
            return
        data_loading = QLabel("Loading telemetry…")
        data_loading.setObjectName("Muted")
        self._data_lay.addWidget(data_loading)
        self._data_lay.addStretch(1)
        video_loading = QLabel("Looking for MP4 and MOV files…")
        video_loading.setObjectName("Muted")
        self._video_lay.addWidget(video_loading)
        self._video_lay.addStretch(1)
        self._video_status.setText("Scanning session folder…")
        folder_id = self._folder_id
        generation = self._load_generation
        if offline_store.load_files(folder_id):
            self._render_local_media()
            self._video_status.setText("Local files ready · checking Drive…")

        def _render(result):
            if (generation != self._load_generation
                    or folder_id != self._folder_id):
                return
            xrks, videos = result
            offline_store.cache_video_catalog(folder_id, videos)
            local = offline_store.load_files(folder_id)
            xrks = self._merge_local_nodes(
                xrks, local, ".xrk", ".ibt", ".csv", ".json")
            videos = self._merge_local_nodes(videos, local, ".mp4", ".mov")
            self._render_data(xrks)
            self._render_videos(videos)

        def _error(message):
            if (generation != self._load_generation
                    or folder_id != self._folder_id):
                return
            local = offline_store.load_files(folder_id)
            xrks = [node for node in local if node.is_telemetry]
            videos = offline_store.load_video_catalog(folder_id)
            offline_message = (
                "Offline copy — Drive refresh unavailable" if local
                else message)
            self._render_data(xrks, offline_message)
            self._render_videos(videos, offline_message)

        run_async(
            self,
            lambda: google_drive_client.list_session_media_recursive(folder_id),
            on_done=_render, on_error=_error)

    @staticmethod
    def _merge_local_nodes(remote, local, *extensions):
        extensions = {extension.casefold() for extension in extensions}
        relevant = [
            node for node in local
            if Path(node.name).suffix.casefold() in extensions]
        local_by_drive_id = {
            node.id: node for node in relevant
            if not str(node.id).startswith("local-")}
        merged = [local_by_drive_id.get(node.id, node) for node in (remote or [])]
        known = {node.id for node in merged}
        merged.extend(node for node in relevant if node.id not in known)
        return merged

    def _render_local_media(self):
        local = offline_store.load_files(self._folder_id)
        xrks = [node for node in local if node.is_telemetry]
        videos = offline_store.load_video_catalog(self._folder_id)
        self._render_data(
            xrks,
            "Showing copies on this device; cloud state is listed per file")
        self._render_videos(
            videos,
            "Showing copies on this device; cloud state is listed per file")

    def _assign_files(self):
        if not self._folder_id:
            return
        fid = self._folder_id
        session_name = self._folder_name

        def _fetch():
            xrks = google_drive_client.list_xrks_recursive(fid)
            try:
                rows = team_naming.from_inventory(
                    db.fetch_inventory_items())["assignments"]
            except Exception:
                rows = []
            if not rows:
                doc = db.fetch_app_doc(team_naming.DOC_KEY)
                rows = (
                    team_naming.normalize(doc)["assignments"] if doc
                    else team_naming.from_drivers(
                        db.fetch_drivers())["assignments"])
            roster = [r for r in rows if r.get("driver")]
            return xrks, roster

        def _open(result):
            xrks, roster = result
            if not xrks:
                QMessageBox.information(
                    self, "No Files",
                    "This session has no .xrk files to assign yet.")
                return
            if not roster:
                QMessageBox.information(
                    self, "No Drivers",
                    "Add drivers in Parts \u2192 Drivers first.")
                return
            dialog = AssignFilesDialog(fid, session_name, xrks, roster, self)
            if dialog.exec() == QDialog.Accepted:
                self._load_data()

        run_async(self, _fetch, on_done=_open,
                  on_error=lambda m: QMessageBox.critical(
                      self, "Load Failed", m))

    def _load_data(self):
        self._clear(self._data_lay)
        if not self._folder_id:
            return
        loading = QLabel("Loading\u2026")
        loading.setObjectName("Muted")
        self._data_lay.addWidget(loading)
        self._data_lay.addStretch(1)
        fid = self._folder_id
        generation = self._load_generation

        def _fetch():
            return google_drive_client.list_telemetry_recursive(fid)

        def _render(xrks, err=None):
            if generation == self._load_generation and fid == self._folder_id:
                local = offline_store.load_files(fid)
                self._render_data(
                    self._merge_local_nodes(
                        xrks, local, ".xrk", ".ibt", ".csv", ".json"), err)

        run_async(self, _fetch, on_done=_render,
                  on_error=lambda message: _render(
                      [], "Offline copy — Drive refresh unavailable"
                      if offline_store.load_files(fid) else message))

    def _render_data(self, xrks, err=None):
        self._clear(self._data_lay)
        self._data_nodes = list(xrks or [])
        self._xrk_nodes = [node for node in self._data_nodes if node.is_xrk]
        self._data_open_buttons = []
        if err:
            msg = QLabel(f"\u26A0 {err}")
            msg.setStyleSheet(
                "color: #e6a020;" if xrks else "color: #e74c3c;")
            msg.setWordWrap(True)
            self._data_lay.addWidget(msg)
        if not xrks:
            msg = QLabel(
                "No session data yet. Upload telemetry or merge tire data.")
            msg.setObjectName("Muted")
            self._data_lay.addWidget(msg)
        else:
            groups: dict[str, list] = {}
            for n in xrks:
                m = parse_xrk_filename(n.name) if n.is_xrk else None
                key = (f"#{m.number} {m.chassis}".strip()
                       if m and m.number else
                       "Tire Data" if not n.is_xrk else "Other")
                groups.setdefault(key, []).append(n)
            multi = len(groups) > 1
            for key in sorted(groups):
                if multi:
                    gh = QLabel(f"{key}")
                    gh.setObjectName("SeasonHdr")
                    self._data_lay.addWidget(gh)
                for n in groups[key]:
                    self._data_lay.addWidget(self._data_card(n))
        self._open_all_xrks_button.setVisible(bool(self._xrk_nodes))
        self._update_xrk_bulk_controls()
        self._data_lay.addStretch(1)

    def _data_card(self, node) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 10, 12, 10)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        name = QLabel(node.name)
        name.setObjectName("CardTitle")
        mid.addWidget(name)
        sub = self._media_card_summary(node)
        subl = QLabel(sub)
        subl.setObjectName("CardSub")
        subl.setToolTip(self._media_timestamp_tooltip(node))
        mid.addWidget(subl)
        lay.addLayout(mid, 1)

        suffix = Path(node.name).suffix.casefold()
        download_only = suffix == ".ibt"
        openb = QPushButton(
            "Download to this computer" if download_only else
            "Open in Race Studio" if suffix == ".xrk" else "Open File")
        openb.setObjectName("Ghost")
        openb.setCursor(Qt.PointingHandCursor)
        openb.setToolTip(
            "Save a copy of the IBT in your Downloads folder"
            if download_only else "Download and open this session data file")
        openb.clicked.connect(
            lambda: self._download(node, download_only=download_only))
        lay.addWidget(openb)
        self._data_open_buttons.append(openb)
        return card

    @staticmethod
    def _media_card_summary(node):
        parts = [format_size(node.size)]
        timestamp = format_media_timestamp(
            node.recorded_at, node.timestamp_source, compact=True)
        if timestamp != "Time unavailable":
            parts.append(timestamp.rsplit(" (", 1)[0])
        if node.pending_upload:
            parts.extend(["On device", "Waiting to upload"])
        else:
            parts.append("On device" if node.local_path else "Not on device")
        return "   ·   ".join(parts)

    @staticmethod
    def _media_timestamp_tooltip(node):
        timestamp = format_media_timestamp(
            node.recorded_at, node.timestamp_source)
        cloud = (
            "Waiting to upload to Google Drive" if node.pending_upload
            else "Available on Google Drive")
        device = (
            "A copy is saved on this device" if node.local_path
            else "No copy is saved on this device")
        return f"{timestamp}\n{cloud}\n{device}"

    def _update_xrk_bulk_controls(self, *_args):
        count = len(self._xrk_nodes)
        self._open_all_xrks_button.setText(
            f"Open All in Race Studio ({count})" if count
            else "Open All in Race Studio")
        self._open_all_xrks_button.setEnabled(
            bool(count) and not self._xrk_bulk_active)

    def _set_xrk_bulk_active(self, active):
        self._xrk_bulk_active = active
        for button in self._data_open_buttons:
            button.setEnabled(not active)
        self._xrk_bulk_progress.setVisible(active)
        self._update_xrk_bulk_controls()

    def _open_all_xrks(self):
        nodes = list(self._xrk_nodes)
        if not nodes or self._xrk_bulk_active:
            return
        self._set_xrk_bulk_active(True)
        self._xrk_bulk_progress.setValue(0)
        self._xrk_bulk_progress.setFormat(
            f"Preparing all {len(nodes)} XRKs for Race Studio... %p%")
        session_id = self._folder_id
        folder_key = sha256(
            str(session_id).encode("utf-8")).hexdigest()[:16]

        def _prepare(progress):
            paths = []
            total = len(nodes)
            for index, node in enumerate(nodes):
                local_path = Path(node.local_path) if node.local_path else None
                if local_path and local_path.is_file():
                    paths.append(local_path)
                    progress(round((index + 1) / total * 100))
                    continue
                node_key = sha256(
                    str(node.id).encode("utf-8")).hexdigest()[:16]
                safe_name = Path(node.name).name or "session-data.xrk"
                destination = (
                    config.DATA_DIR / "telemetry_cache"
                    / folder_key / node_key / safe_name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if (destination.is_file() and node.size is not None
                        and destination.stat().st_size == node.size):
                    offline_store._record_remote_file(session_id, node, destination)
                    paths.append(destination)
                    progress(round((index + 1) / total * 100))
                    continue
                partial = destination.with_name(destination.name + ".part")
                partial.unlink(missing_ok=True)

                def file_progress(percent, item_index=index):
                    progress(round(
                        (item_index + percent / 100) / total * 100))

                google_drive_client.download_file(
                    node.id, partial, file_progress)
                partial.replace(destination)
                offline_store._record_remote_file(session_id, node, destination)
                paths.append(destination)
                progress(round((index + 1) / total * 100))
            return paths

        def _reveal(result):
            paths = result
            self._xrk_bulk_progress.setRange(0, 100)
            self._xrk_bulk_progress.setValue(0)
            self._xrk_bulk_progress.setFormat("Opening XRKs in Race Studio... %p%")

            def imported(report):
                self._set_xrk_bulk_active(False)
                self._xrk_bulk_progress.setRange(0, 100)
                summary = (f"{len(paths)} XRKs opened; {report.imported} imported, {report.skipped} already "
                           f"present, {report.errors} errors")
                self._open_all_xrks_button.setToolTip(summary)
                if report.errors:
                    QMessageBox.warning(self, "Race Studio Import", summary)

            def fallback(message):
                self._set_xrk_bulk_active(False)
                self._xrk_bulk_progress.setRange(0, 100)
                QMessageBox.warning(
                    self, "Race Studio Import Failed",
                    f"{message}\n\nYour files are saved in xCAL. "
                    "Close any Race Studio import or confirmation dialog, "
                    "and try Open All again.")

            run_async_progress(self, lambda progress: import_xrk_files(paths, progress),
                               on_done=imported, on_error=fallback,
                               on_progress=self._xrk_bulk_progress.setValue)

        def _failed(message):
            self._set_xrk_bulk_active(False)
            QMessageBox.critical(
                self, "Open All XRKs Failed",
                "The session files remain unchanged. xCAL could not prepare "
                f"the full batch:\n\n{message}")

        run_async_progress(
            self, _prepare, on_done=_reveal, on_error=_failed,
            on_progress=self._xrk_bulk_progress.setValue)

    @staticmethod
    def _available_download_path(name):
        downloads = Path.home() / "Downloads"
        safe_name = Path(name).name or "session-data.ibt"
        candidate = downloads / safe_name
        number = 2
        while candidate.exists():
            candidate = downloads / (
                f"{Path(safe_name).stem} ({number}){Path(safe_name).suffix}")
            number += 1
        return candidate

    def _download(self, node, download_only=False):
        local_path = Path(node.local_path) if node.local_path else None
        download_path = (
            self._available_download_path(node.name) if download_only else None)

        def _dl():
            if download_path:
                download_path.parent.mkdir(parents=True, exist_ok=True)
                partial = download_path.with_name(download_path.name + ".part")
                partial.unlink(missing_ok=True)
                if local_path and local_path.is_file():
                    shutil.copy2(local_path, partial)
                else:
                    google_drive_client.download_file(node.id, partial)
                partial.replace(download_path)
                return download_path
            if local_path and local_path.is_file():
                return local_path
            import hashlib
            cache_key = hashlib.sha256(str(node.id).encode()).hexdigest()[:24]
            dest = config.DATA_DIR / "telemetry_cache" / cache_key / Path(node.name).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_name(dest.name + ".part")
            partial.unlink(missing_ok=True)
            google_drive_client.download_file(node.id, partial)
            partial.replace(dest)
            return dest

        def _open(path):
            if download_only:
                QMessageBox.information(
                    self, "Download Complete",
                    f"The IBT was saved to your Downloads folder:\n\n{path}\n\n"
                    "Import this file from Race Studio when you are ready.")
                return
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            QMessageBox.information(
                self, "Open Session Data",
                f"The file was downloaded to:\n{path}\n\n"
                "Windows does not have an associated application. For XRK or "
                "IBT files, use Race Studio 3 Analysis → Import File(s).")

        run_async(self, _dl, on_done=_open,
                  on_error=lambda m: QMessageBox.critical(self, "Download failed", m))

    def _upload(self):
        if not self._folder_id:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Race Studio 3 data files", "",
            "Session data (*.xrk *.ibt *.csv *.json);;All files (*.*)")
        if not paths:
            return
        self._queue_local_files(paths)

    def _merge_tire_data(self):
        if not self._folder_id:
            return
        dialog = TireDataMergeDialog(self._folder_name, self)
        dialog.completed.connect(self._tire_merge_completed)
        dialog.exec()

    def _tire_merge_completed(self, result):
        output_text = result.get("temporary_output")
        output = Path(output_text) if output_text else None
        try:
            self._queue_local_files(result.get("files") or [])
        finally:
            temp_root = Path(tempfile.gettempdir()).resolve()
            if (output is not None and output.is_dir()
                    and output.name.startswith("xcal-tire-merge-")
                    and temp_root in output.resolve().parents):
                shutil.rmtree(output, ignore_errors=True)
        QMessageBox.information(
            self, "Tire Data Added",
            "The source CSVs, merged CSV, Tire Data IBT, and sync manifest "
            "were added to this session.\n\nBefore converting, open Race Studio "
            "Preferences â†’ Conversion â†’ Settings and enable iRacing â†’ All "
            "channels. Then use Conversion of Files and select AiM DRK "
            "[RS2Analysis data]. Without All channels, Race Studio can omit "
            "Exhaust Temp and other extra sensors. For direct comparisons, "
            "set the imported WaterTemp channel's persistent alias/function "
            "to Exhaust Temp; xCAL uses it as the EGT transport slot. "
            "use AiMRawLatAccel, AiMRawLongAccel, and AiMRawYawRate; Race "
            "Studio recalculates its similarly named GPS channels from the "
            "60 Hz bridge GPS stream.")

    def _manage_data_files(self):
        if not self._data_nodes:
            QMessageBox.information(
                self, "No Data Files", "There are no data files to manage.")
            return
        dialog = ManageDataFilesDialog(self._data_nodes, self)
        if dialog.exec() != QDialog.Accepted or not dialog.action:
            return
        selected = dialog.selected_nodes()
        if dialog.action == "rename":
            self._rename_data_file(selected[0], dialog.new_name)
        elif dialog.action == "delete":
            self._delete_data_files(selected)

    def _rename_data_file(self, node, new_name):
        if new_name == node.name:
            return
        session_id = self._folder_id
        self._update_offline_status("Renaming data file…")

        def work():
            if not str(node.id).startswith("local-"):
                google_drive_client.rename_file(node.id, new_name)
            offline_store.rename_local_file(session_id, node.id, new_name)

        run_async(
            self, work,
            on_done=lambda _result: self._data_files_changed("File renamed."),
            on_error=lambda message: self._data_file_change_failed(
                "Rename Failed", message),
        )

    def _delete_data_files(self, nodes):
        names = "\n".join(
            f"• {node.name}"
            + (" (cancel pending upload)" if node.pending_upload else "")
            for node in nodes)
        if QMessageBox.question(
                self, "Delete Data Files",
                f"Delete {len(nodes)} checked file(s)? Drive copies will move "
                "to Trash. Pending uploads will be canceled and removed from "
                f"this computer.\n\n{names}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        session_id = self._folder_id
        self._update_offline_status(f"Deleting {len(nodes)} data file(s)…")

        def work():
            errors = []
            for node in nodes:
                is_pending_local = (
                    node.pending_upload or str(node.id).startswith("local-"))
                if is_pending_local:
                    try:
                        offline_store.remove_local_file(session_id, node.id)
                    except Exception as exc:
                        errors.append(f"{node.name}: {exc}")
                    continue
                try:
                    google_drive_client.trash_file(node.id)
                except Exception as exc:
                    errors.append(f"{node.name}: {exc}")
                    continue
                try:
                    # Cache cleanup must not turn a successful Drive deletion
                    # into a failed file operation.
                    offline_store.remove_local_file(session_id, node.id)
                except Exception:
                    pass
            if errors:
                raise RuntimeError(
                    "Some files could not be deleted:\n\n" + "\n".join(errors))

        run_async(
            self, work,
            on_done=lambda _result: self._data_files_changed(
                f"Deleted {len(nodes)} data file(s)."),
            on_error=lambda message: self._data_file_change_failed(
                "Delete Failed", message),
        )

    def _data_files_changed(self, message):
        self._update_offline_status(message)
        self._load_media()

    def _data_file_change_failed(self, title, message):
        self._update_offline_status("Data file change failed.")
        self._load_media()
        QMessageBox.critical(self, title, message)

    # ── Video Review ───────────────────────────────────────────────────────

    def _load_videos(self):
        self._clear(self._video_lay)
        if not self._folder_id:
            return
        self._video_status.setText("Scanning session folder…")
        loading = QLabel("Looking for MP4 and MOV files…")
        loading.setObjectName("Muted")
        self._video_lay.addWidget(loading)
        self._video_lay.addStretch(1)
        folder_id = self._folder_id
        generation = self._load_generation
        local_now = [
            node for node in offline_store.load_files(folder_id)
            if Path(node.name).suffix.casefold() in {".mp4", ".mov"}]
        if local_now:
            self._render_videos(
                local_now, "Local offline copy — checking Drive…")

        def _render(videos, err=None):
            if (generation == self._load_generation
                    and folder_id == self._folder_id):
                if not err:
                    offline_store.cache_video_catalog(folder_id, videos)
                local = offline_store.load_files(folder_id)
                self._render_videos(
                    self._merge_local_nodes(
                        videos, local, ".mp4", ".mov"), err)

        def _failed(message):
            local = offline_store.load_video_catalog(folder_id)
            _render(
                local,
                "Offline copy — Drive refresh unavailable" if local
                else message)

        run_async(
            self,
            lambda: google_drive_client.list_videos_recursive(folder_id),
            on_done=_render,
            on_error=_failed)

    def _render_videos(self, videos, err=None):
        self._clear(self._video_lay)
        self._video_buttons = []
        videos = list(videos or [])
        self._videos_offline = bool(err)
        normalized = []
        for node in videos:
            cached = self._cached_video_path(node)
            normalized.append(replace(node, local_path=str(cached) if cached else None))
        videos = normalized
        self._video_nodes = videos
        self._download_all_videos.setEnabled(
            bool(videos) and not err and not self._video_transfer_active)
        if err:
            self._video_status.setText(
                f"{len(videos)} local video(s)" if videos else "Video scan failed")
            message = QLabel(f"⚠ {err}")
            message.setStyleSheet(
                "color: #e6a020;" if videos else "color: #e74c3c;")
            message.setWordWrap(True)
            self._video_lay.addWidget(message)
        if not videos:
            self._video_status.setText("No videos found")
            message = QLabel(
                "No loose .mp4 or .mov videos were found in this session folder.")
            message.setObjectName("Muted")
            message.setWordWrap(True)
            self._video_lay.addWidget(message)
        else:
            self._video_status.setText(f"{len(videos)} video(s)")
            for node in sorted(videos, key=lambda video: video.name.casefold()):
                self._video_lay.addWidget(self._video_card(node))
        self._video_lay.addStretch(1)

    def _video_card(self, node) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 9, 12, 9)

        details = QVBoxLayout()
        details.setSpacing(2)
        name = QLabel(node.name)
        name.setObjectName("CardTitle")
        name.setWordWrap(True)
        details.addWidget(name)
        size_text = self._media_card_summary(node)
        size = QLabel(size_text)
        size.setObjectName("CardSub")
        size.setToolTip(self._media_timestamp_tooltip(node))
        details.addWidget(size)
        layout.addLayout(details, 1)

        cached = self._cached_video_path(node)
        open_button = QPushButton("Cached · Play" if cached else "Download / Play")
        open_button.setProperty("requires_connection", not bool(cached))
        open_button.setEnabled(bool(cached) or not self._videos_offline)
        open_button.setObjectName("Primary")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setToolTip(
            "Download to xCAL's temporary video cache and open in your default player")
        open_button.clicked.connect(lambda: self._open_video(node))
        layout.addWidget(open_button)
        self._video_buttons.append(open_button)

        download_button = QPushButton("Download")
        download_button.setProperty("requires_connection", not bool(cached))
        download_button.setEnabled(bool(cached) or not self._videos_offline)
        download_button.setObjectName("Ghost")
        download_button.setCursor(Qt.PointingHandCursor)
        download_button.clicked.connect(lambda: self._download_video(node))
        layout.addWidget(download_button)
        self._video_buttons.append(download_button)
        if self._video_transfer_active:
            open_button.setEnabled(False)
            download_button.setEnabled(False)
        return card

    @staticmethod
    def _safe_video_name(name):
        return Path(name).name or "session-video.mp4"

    def _open_video(self, node):
        local_path = self._cached_video_path(node)
        if local_path:
            self._open_downloaded_video(local_path, node.name)
            return
        destination = offline_store.video_cache_path(self._folder_id, node)
        if (destination.exists() and node.size is not None
                and destination.stat().st_size == node.size):
            self._open_downloaded_video(destination, node.name)
            return
        self._start_video_transfer(node, destination, open_after=True)

    def _cached_video_path(self, node, folder_id=None):
        folder_id = folder_id or self._folder_id
        candidates = [
            Path(node.local_path) if node.local_path else None,
            offline_store.video_cache_path(folder_id, node),
        ]
        return next((path for path in candidates if path and path.is_file()
                     and (node.size is None or path.stat().st_size == node.size)), None)

    def _cache_all_videos(self):
        if self._video_transfer_active or self._videos_offline:
            return
        nodes = list(self._video_nodes)
        folder_id = self._folder_id
        generation = self._load_generation
        if not nodes:
            return
        self._video_transfer_active = True
        self._set_video_controls_enabled(False)
        self._video_progress.setVisible(True)
        self._video_progress.setValue(0)
        self._video_progress.setFormat("Downloading videos... %p%")

        def download(progress):
            for index, node in enumerate(nodes):
                from qt.workers import check_interruption
                check_interruption()
                if not self._cached_video_path(node, folder_id):
                    destination = offline_store.video_cache_path(folder_id, node)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    partial = destination.with_name(destination.name + ".part")
                    try:
                        google_drive_client.download_file(
                            node.id, partial,
                            lambda percent, i=index: progress(round(
                                (i + percent / 100) / len(nodes) * 100)))
                        partial.replace(destination)
                    finally:
                        partial.unlink(missing_ok=True)
                progress(round((index + 1) / len(nodes) * 100))
            return nodes

        def done(result):
            self._finish_video_transfer()
            if generation == self._load_generation and folder_id == self._folder_id:
                self._render_videos(result)
                self._video_status.setText("Videos cached on this device")

        def failed(message):
            self._finish_video_transfer()
            if generation == self._load_generation:
                self._render_videos(nodes)
                self._video_error("Download All Failed", message)

        run_async_progress(self, download, on_done=done, on_error=failed,
                           on_progress=self._video_progress.setValue)

    def _download_video(self, node):
        suggested = Path.home() / "Downloads" / self._safe_video_name(node.name)
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self, "Download Session Video", str(suggested),
            "Video files (*.mp4 *.mov);;All files (*.*)")
        if not destination:
            return
        destination = Path(destination)
        self._start_video_transfer(node, destination, open_after=False)

    def _start_video_transfer(self, node, destination: Path, open_after: bool):
        if self._video_transfer_active:
            QMessageBox.information(
                self, "Video Transfer In Progress",
                "Please wait for the current video to finish. xCAL transfers "
                "one video at a time to keep the app responsive.")
            return

        self._video_transfer_active = True
        self._set_video_controls_enabled(False)
        self._video_progress.setValue(0)
        self._video_progress.setFormat(f"{node.name}  —  %p%")
        self._video_progress.setVisible(True)
        self._video_status.setText(f"Downloading {node.name}…")
        partial = destination.with_name(destination.name + ".part")
        local_path = self._cached_video_path(node)
        folder_id = self._folder_id
        generation = self._load_generation

        def _download(progress):
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial.unlink(missing_ok=True)
            if local_path and local_path.is_file():
                shutil.copy2(local_path, partial)
                progress(100)
            else:
                google_drive_client.download_file(node.id, partial, progress)
            partial.replace(destination)
            if not node.pending_upload:
                offline_store._record_remote_file(folder_id, node, destination)
            return destination

        def _done(path):
            self._finish_video_transfer()
            if generation != self._load_generation or folder_id != self._folder_id:
                return
            self._video_nodes = [
                replace(item, local_path=str(path)) if item.id == node.id else item
                for item in self._video_nodes]
            self._render_videos(self._video_nodes, "Offline" if self._videos_offline else None)
            if open_after:
                self._open_downloaded_video(path, node.name)
            else:
                self._video_status.setText(f"Downloaded to {path}")

        def _error(message):
            partial.unlink(missing_ok=True)
            self._finish_video_transfer()
            self._video_error(
                "Open Video Failed" if open_after else "Download Failed", message)

        run_async_progress(
            self, _download, on_done=_done, on_error=_error,
            on_progress=lambda percent: self._update_video_progress(
                node.name, percent))

    def _update_video_progress(self, name: str, percent: int):
        self._video_progress.setValue(percent)
        self._video_status.setText(f"Downloading {name}… {percent}%")

    def _set_video_controls_enabled(self, enabled: bool):
        self._refresh_videos.setEnabled(enabled)
        self._download_all_videos.setEnabled(
            enabled and bool(self._video_nodes) and not self._videos_offline)
        for button in self._video_buttons:
            button.setEnabled(enabled and not (
                self._videos_offline and button.property("requires_connection")))

    def _finish_video_transfer(self):
        self._video_transfer_active = False
        self._video_progress.setVisible(False)
        self._set_video_controls_enabled(True)

    def _open_downloaded_video(self, path: Path, name: str):
        self._video_status.setText(f"Opened {name}")
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.information(
                self, "Choose a Video Player",
                f"The video downloaded to:\n{path}\n\n"
                "Windows does not currently have an app associated with this format.")

    def _video_error(self, title, message):
        self._video_status.setText("Video action failed")
        QMessageBox.critical(self, title, message)
