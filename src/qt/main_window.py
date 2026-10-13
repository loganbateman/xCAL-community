"""Main window for the Qt app — sidebar + stacked pages.

Page switches use QStackedWidget.setCurrentWidget(), which swaps a fully-rendered
page in one composited step. There is no widget-by-widget assembly, so switching
is instant and tear-free (the whole reason for the Qt move).
"""
from __future__ import annotations
from dataclasses import replace
from time import monotonic
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QStackedWidget

import config
import cache
import database.client as db
import offline_store
from database.models import Setup
from utils import google_drive_client, updater
from utils import team_naming
from qt.sidebar import Sidebar
from qt.theme import stylesheet
from qt.workers import run_async
from qt.views.sessions_qt import SessionsView
from qt.views.settings_qt import SettingsView
from qt.views.cheat_sheet_qt import CheatSheetView
from qt.views.session_workspace_qt import SessionWorkspace
from qt.views.compare_qt import CompareView
from qt.views.setup_builder_qt import SetupBuilderView
from qt.views.setups_qt import SetupsView
from qt.views.parts_qt import PartsView
from qt.sync_queue_qt import SyncQueueDialog


class _SyncProgressBridge(QObject):
    """Move detailed queue events from the sync worker to the UI thread."""

    event = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._closing = False
        self._allow_close = False
        self._offline_syncing = False
        self._sync_events = {}
        self._sync_queue_dialog = None
        self._sync_progress_bridge = _SyncProgressBridge(self)
        self._sync_progress_bridge.event.connect(self._sync_progress_event)
        self._cloud_connected = False
        self._supabase_connected = False
        self._drive_connected = False
        self._catalog_cache_refreshing = False
        self._catalog_cache_last = 0.0
        self.setWindowTitle(config.APP_TITLE)
        self.resize(1280, 820)
        self.setMinimumSize(1080, 680)
        if config.LOGO_ICO.exists():
            self.setWindowIcon(QIcon(str(config.LOGO_ICO)))
        self.setStyleSheet(stylesheet())

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._sidebar = Sidebar()
        self._sidebar.navigate.connect(self._navigate)
        self._sidebar.recheck.connect(lambda: self._connect())
        self._sidebar.sync_queue_requested.connect(self._open_sync_queue)
        lay.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        # Pages
        self._sessions = SessionsView()
        self._sessions.open_session.connect(self._open_session)
        self._settings = SettingsView(
            on_reconnect=self._connect,
            on_naming_changed=self._naming_changed)
        self._settings.install_requested.connect(self._launch_update_installer)
        self._cheat = CheatSheetView()
        self._compare = CompareView()
        self._setups_page = SetupsView()
        self._setups_page.open_setup.connect(self._open_global_setup)
        self._setups_page.setups_changed.connect(
            self._sessions.refresh_setup_counts)
        self._setups_page.setups_changed.connect(self._compare.invalidate)
        self._parts = PartsView()
        self._parts.new_baseline.connect(self._new_baseline)
        self._parts.open_baseline.connect(self._open_global_setup)
        self._parts.copy_baseline.connect(self._open_baseline_copy)
        self._workspace = SessionWorkspace()
        self._workspace.back.connect(lambda: self._navigate("Sessions"))
        self._workspace.detail.edit_setup.connect(self._edit_setup)
        self._workspace.detail.view_setup.connect(self._view_setup)
        self._workspace.detail.delete_session.connect(self._delete_session)
        self._workspace.detail.session_updated.connect(self._session_updated)
        self._workspace.detail.setups_changed.connect(
            self._sessions.refresh_setup_counts)
        self._workspace.detail.setups_changed.connect(self._setup_catalog_changed)
        self._workspace.detail.sync_status_changed.connect(
            self._refresh_sync_indicator)
        self._setup_builder = SetupBuilderView()
        self._setup_builder.back.connect(self._back_from_setup)
        self._setup_builder.saved.connect(self._setup_saved)

        self._pages: dict[str, QWidget] = {
            "Sessions": self._sessions,
            "Setups": self._setups_page,
            "Parts": self._parts,
            "Compare": self._compare,
            "Cheat Sheet": self._cheat,
            "Settings": self._settings,
            "SessionWorkspace": self._workspace,
            "SetupBuilder": self._setup_builder,
        }
        for w in self._pages.values():
            self._stack.addWidget(w)

        self._navigate("Sessions")
        if not config.GOOGLE_DRIVE_FOLDER_ID:
            QTimer.singleShot(300, lambda: self._navigate("Settings"))
        self._connect()
        self._refresh_offline_catalogs(force=True)
        self._load_team_naming()
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(60_000)
        self._connection_timer.timeout.connect(lambda: self._connect())
        self._connection_timer.start()
        QTimer.singleShot(3500, self._check_for_updates_on_startup)

    def _navigate(self, label: str):
        page = self._pages.get(label)
        if page is None:
            return
        if (self._stack.currentWidget() is self._setup_builder
                and page is not self._setup_builder):
            self._confirm_leave_setup(lambda: self._navigate_now(label))
            return
        self._navigate_now(label)

    def _navigate_now(self, label: str):
        page = self._pages.get(label)
        if page is None:
            return
        # SessionWorkspace isn't a sidebar item; keep Sessions highlighted for it.
        self._sidebar.set_active("Sessions" if label == "SessionWorkspace" else label)
        self._stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()

    def _open_session(self, session):
        self._workspace.open(session, self._sessions._sessions)
        self._stack.setCurrentWidget(self._workspace)
        self._sidebar.set_active("Sessions")

    def _delete_session(self, folder_id):
        session = next(
            (s for s in self._sessions._sessions if s.id == folder_id), None)
        if session is None:
            return
        def _deleted():
            self._setup_catalog_changed()
            self._navigate("Sessions")

        self._sessions.request_delete_session(session, on_done=_deleted)

    def _session_updated(
            self, folder_id, folder_path, folder_name, season):
        updated = None
        sessions = []
        for session in self._sessions._sessions:
            if session.id == folder_id:
                session = replace(
                    session, name=folder_name, path=folder_path, season=season)
                updated = session
            sessions.append(session)
        if updated is None:
            return
        self._sessions._sessions = sessions
        cache.save_sessions(sessions)
        self._sessions._last_signature = self._sessions._signature(
            sessions, self._sessions._counts)
        self._sessions._populate_seasons()
        self._sessions._render_hero()
        self._sessions._render()
        self._workspace.open(updated, sessions)
        self._setup_catalog_changed()

    def _edit_setup(self, setup, folder_id, folder_path, folder_name):
        self._setup_return_widget = self._stack.currentWidget()
        self._setup_builder.load_setup(
            folder_id, folder_path, folder_name, setup)
        if self._setup_return_widget is self._parts:
            return_context = "Parts"
        elif self._setup_return_widget is self._setups_page:
            return_context = "Setups"
        else:
            return_context = "Session"
        self._setup_builder.set_return_context(return_context)
        self._stack.setCurrentWidget(self._setup_builder)
        self._sidebar.set_active(
            "Parts" if self._setup_return_widget is self._parts
            else "Setups" if self._setup_return_widget is self._setups_page
            else "Sessions")

    def _back_from_setup(self):
        self._confirm_leave_setup(self._return_from_setup)

    def _return_from_setup(self):
        target = getattr(self, "_setup_return_widget", self._workspace)
        self._stack.setCurrentWidget(target)
        self._sidebar.set_active(
            "Parts" if target is self._parts
            else "Setups" if target is self._setups_page else "Sessions")

    def _open_global_setup(self, setup):
        self._edit_setup(
            setup,
            setup.drive_folder_id,
            setup.drive_folder_path or "",
            setup.track or setup.name or "Setup")

    def _new_baseline(self):
        """Open a clean Setup Builder record that saves as a baseline."""
        self._edit_setup(Setup(is_baseline=True), None, "", "New Baseline")

    def _open_baseline_copy(self, setup):
        """Open a detached baseline copy that will save as a new definition."""
        self._edit_setup(setup, None, "", setup.name or "New Baseline")

    def _confirm_leave_setup(self, continue_fn):
        if self._setup_builder.is_saving():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Save in progress",
                "Please wait for the current setup save to finish.")
            return
        if not self._setup_builder.has_unsaved_changes():
            continue_fn()
            return
        from PySide6.QtWidgets import QMessageBox
        choice = QMessageBox.warning(
            self,
            "Unsaved Setup Changes",
            "This setup has unsaved changes.\n\n"
            "Save before leaving?\n\n"
            "Discard will revert the form to the last loaded/saved values.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            self._setup_builder._save(after_save=continue_fn)
        elif choice == QMessageBox.Discard:
            self._setup_builder.revert_changes()
            continue_fn()

    def _setup_saved(self, setup):
        # Setup saves only change Supabase. Avoid rescanning all Drive telemetry,
        # which made a simple save feel like a full Sessions refresh.
        offline_store.cache_setup(setup)
        if (not setup.is_baseline
                and self._setup_return_widget is self._workspace):
            self._workspace.detail.refresh_setups()
        if not setup.is_baseline:
            self._sessions.refresh_setup_counts()
        else:
            self._parts.refresh()
        self._setup_catalog_changed()
        self._refresh_sync_indicator()

    def _setup_catalog_changed(self):
        self._setups_page.refresh()
        self._compare.invalidate()

    def _view_setup(self, setup):
        self._navigate("Compare")
        self._compare.preload_a(setup)

    def _connect(self, attempt: int = 1):
        if self._closing:
            return

        def _check():
            supabase_ok = db.check_connection()
            drive_ok = google_drive_client.check_connection()
            if drive_ok:
                # Prime the locked Google identity while online so future
                # offline edits can be attributed without a network request.
                google_drive_client.audit_user_or_none(refresh=True)
            return supabase_ok, drive_ok

        def _done(result):
            supabase_ok, drive_ok = result
            ok = supabase_ok and drive_ok
            self._cloud_connected = ok
            self._supabase_connected = supabase_ok
            self._drive_connected = drive_ok
            self._refresh_sync_indicator()
            if self._sync_queue_dialog is not None:
                self._sync_queue_dialog.set_connections(
                    supabase_ok, drive_ok)
            # Setup/Parts queues target Supabase and should not wait for Drive.
            # File queue failures are retained independently by sync_pending.
            if supabase_ok:
                self._sync_offline_work()
                self._refresh_offline_catalogs()
            # Supabase free tier can be waking from a pause — the first check may
            # fail or time out. Retry a few times with backoff before giving up.
            if not ok and attempt < 4 and not self._closing:
                from PySide6.QtCore import QTimer
                self._sidebar.set_connecting()
                QTimer.singleShot(2500 * attempt,
                                  lambda: self._connect(attempt + 1))

        run_async(
            self, _check, on_done=_done,
            on_error=lambda _m: _done((False, False)))

    def _sync_offline_work(self):
        if self._closing or self._offline_syncing or offline_store.pending_sync_summary().total == 0:
            self._refresh_sync_indicator()
            return
        self._offline_syncing = True
        self._sidebar.set_syncing(
            offline_store.pending_sync_summary(),
            self._supabase_connected, self._drive_connected)
        if self._sync_queue_dialog is not None:
            self._sync_queue_dialog.set_syncing(True)

        def _done(report):
            self._offline_syncing = False
            if self._sync_queue_dialog is not None:
                self._sync_queue_dialog.set_syncing(False)
            self._refresh_sync_indicator()
            self._refresh_sync_queue()
            if (report.setups_synced or report.setup_deletions_synced
                    or report.files_synced
                    or report.inventory_synced):
                self._workspace.detail._update_offline_status()
                if self._stack.currentWidget() is self._workspace:
                    self._workspace.detail.refresh()
                self._parts.refresh()
                self._setup_catalog_changed()

        def _error(_message):
            self._offline_syncing = False
            if self._sync_queue_dialog is not None:
                self._sync_queue_dialog.set_syncing(False)
            self._refresh_sync_indicator()
            self._refresh_sync_queue()

        def work():
            report = offline_store.sync_pending(self._sync_progress_bridge.event.emit)
            if config.GOOGLE_DRIVE_FOLDER_ID:
                db.get_client().sync()
            return report

        run_async(
            self, work,
            on_done=_done, on_error=_error)

    def _open_sync_queue(self):
        if self._sync_queue_dialog is None:
            self._sync_queue_dialog = SyncQueueDialog(self)
            self._sync_queue_dialog.retry_requested.connect(self._retry_sync_queue)
        self._sync_queue_dialog._events = dict(self._sync_events)
        self._sync_queue_dialog.set_connections(
            self._supabase_connected, self._drive_connected)
        self._sync_queue_dialog.set_syncing(self._offline_syncing)
        self._sync_queue_dialog.refresh()
        self._sync_queue_dialog.show()
        self._sync_queue_dialog.raise_()
        self._sync_queue_dialog.activateWindow()

    def _retry_sync_queue(self):
        if self._cloud_connected:
            self._sync_offline_work()
        else:
            self._connect()

    def _sync_progress_event(self, event):
        self._sync_events[event["key"]] = event
        if self._sync_queue_dialog is not None:
            self._sync_queue_dialog.set_event(event)

    def _refresh_sync_queue(self):
        if self._sync_queue_dialog is not None:
            self._sync_queue_dialog.refresh()

    def _refresh_sync_indicator(self):
        """Keep the global label honest about queued local work."""
        self._sidebar.set_connection(
            self._cloud_connected,
            offline_store.pending_sync_summary(),
            supabase_connected=self._supabase_connected,
            drive_connected=self._drive_connected)

    def _refresh_offline_catalogs(self, force=False):
        """Keep essential setup and Parts data available without user action."""
        if self._catalog_cache_refreshing:
            return
        if not force and monotonic() - self._catalog_cache_last < 300:
            return
        self._catalog_cache_refreshing = True

        def _fetch():
            baselines, setups = db.fetch_setup_catalog()
            inventory = db.fetch_inventory_items(include_archived=True)
            return baselines, setups, inventory

        def _done(result):
            self._catalog_cache_refreshing = False
            self._catalog_cache_last = monotonic()
            baselines, setups, inventory = result
            offline_store.cache_setup_catalog(baselines, setups)
            offline_store.cache_inventory(inventory)

        def _error(_message):
            self._catalog_cache_refreshing = False

        run_async(self, _fetch, on_done=_done, on_error=_error)

    def _load_team_naming(self):
        def _fetch():
            try:
                inventory_doc = team_naming.from_inventory(
                    db.fetch_inventory_items())
                if inventory_doc["assignments"]:
                    return inventory_doc
            except Exception:
                pass
            doc = db.fetch_app_doc(team_naming.DOC_KEY)
            return doc or team_naming.from_drivers(db.fetch_drivers())

        run_async(
            self,
            _fetch,
            on_done=lambda doc: (
                team_naming.apply(doc), self._naming_changed()),
            on_error=lambda _message: None)

    def _check_for_updates_on_startup(self):
        run_async(
            self,
            updater.check_for_update,
            on_done=self._show_update_notice,
            on_error=lambda _message: None)

    def _show_update_notice(self, info):
        if not info or not info.update_available:
            return
        from PySide6.QtWidgets import QMessageBox

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Information)
        message.setWindowTitle("xCAL Update Available")
        message.setText(f"xCAL {info.latest_version} is available.")
        message.setInformativeText(
            f"You are running {info.current_version}. Download the latest "
            "installer from GitHub Releases when you are ready.")

        download_btn = None
        if info.download_url:
            download_btn = message.addButton(
                "Install Update", QMessageBox.AcceptRole)
        release_btn = None
        if info.release_url:
            release_btn = message.addButton(
                "Open release", QMessageBox.ActionRole)
        message.addButton("Later", QMessageBox.RejectRole)

        message.exec()
        clicked = message.clickedButton()
        if download_btn and clicked is download_btn:
            run_async(
                self,
                lambda: updater.download_installer(info),
                on_done=self._launch_update_installer,
                on_error=lambda error: QMessageBox.critical(
                    self, "Update Failed", str(error)),
            )
        elif release_btn and clicked is release_btn:
            QDesktopServices.openUrl(QUrl(info.release_url))

    def _launch_update_installer(self, path):
        from PySide6.QtWidgets import QMessageBox
        try:
            updater.launch_installer(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Update Failed", f"Could not start the installer: {exc}")
            return
        # Inno Setup is launched with /CLOSEAPPLICATIONS. Closing here releases
        # the current executable promptly; the installer relaunches the new app.
        self.close()

    def _naming_changed(self):
        if self._sessions._loaded:
            self._sessions._render_hero()
            self._sessions._render()

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        # Network calls cannot be safely force-terminated inside C extensions.
        # Request cooperative cancellation and keep closing visible until an
        # in-flight network call returns. Queued work remains durable for restart.
        from PySide6.QtCore import QThread
        if self._allow_close:
            super().closeEvent(event)
            return
        if (self._stack.currentWidget() is self._setup_builder
                and (self._setup_builder.is_saving()
                     or self._setup_builder.has_unsaved_changes())):
            event.ignore()
            self._confirm_leave_setup(self.close)
            return
        running = [t for t in self.findChildren(QThread) if t.isRunning()]
        if not running:
            super().closeEvent(event)
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            if hasattr(self, "_connection_timer"):
                self._connection_timer.stop()
            self.setWindowTitle("xCAL — Closing...")
            self.setEnabled(False)
            for thread in running:
                thread.requestInterruption()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._finish_close)

    def _finish_close(self):
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtWidgets import QApplication
        if any(t.isRunning() for t in self.findChildren(QThread)):
            QTimer.singleShot(100, self._finish_close)
            return
        self._allow_close = True
        self.close()
        QApplication.instance().quit()
