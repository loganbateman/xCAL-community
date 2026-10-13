"""Settings page (Qt) — cloud config, Google sign-in, connection test, packets."""
from __future__ import annotations
from pathlib import Path
import json
import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QPushButton, QFrame, QScrollArea,
                               QMessageBox, QLineEdit, QFileDialog, QDialog,
                               QListWidget, QPlainTextEdit)

import config
import offline_store
import database.client as db
import packets
from utils import google_drive_client, updater
from qt.workers import run_async

class SettingsView(QWidget):
    install_requested = Signal(object)

    def __init__(self, on_reconnect=None, on_naming_changed=None, parent=None):
        super().__init__(parent)
        self._on_reconnect = on_reconnect
        self._on_naming_changed = on_naming_changed
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(12)

        h1 = QLabel("Settings")
        h1.setObjectName("H1")
        outer.addWidget(h1)

        connection = QWidget()
        connection_lay = QVBoxLayout(connection)
        connection_lay.setContentsMargins(0, 12, 0, 0)
        connection_lay.setSpacing(10)

        self._conn_banner = QLabel("Checking connection...")
        self._conn_banner.setStyleSheet(
            f"color: {config.COLOR_TEXT_MUTED}; font-size:15px; font-weight:700;")
        connection_lay.addWidget(self._conn_banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(16)
        scroll.setWidget(body)
        connection_lay.addWidget(scroll, 1)
        col.addWidget(self._build_drive_setup_card())

        # Cloud services card
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 16, 20, 18)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        title = QLabel("Team Services")
        title.setObjectName("H2")
        grid.addWidget(title, 0, 0, 1, 2)

        env_vals = self._read_env(config.ENV_PATH)
        service_rows = [
            ("Catalog storage", ("Local SQLite + team Google Drive", config.COLOR_TEXT)),
            ("Team Drive", self._service_state(
                env_vals.get("GOOGLE_DRIVE_FOLDER_ID"),
                "Folder configured",
                "Missing folder",
            )),
            ("Google OAuth", self._oauth_state(env_vals)),
        ]

        row = 1
        for label, (text, color) in service_rows:
            lab = QLabel(label)
            lab.setObjectName("Muted")
            grid.addWidget(lab, row, 0)
            value = QLabel(text)
            value.setStyleSheet(f"color: {color}; font-weight: 600;")
            grid.addWidget(value, row, 1)
            row += 1

        editor_label = QLabel("Signed-in editor")
        editor_label.setObjectName("Muted")
        grid.addWidget(editor_label, row, 0)
        self._google_name = QLabel("Checking Google account\u2026")
        self._google_name.setStyleSheet("font-weight:600;")
        grid.addWidget(self._google_name, row, 1)
        row += 1

        email_label = QLabel("Google email")
        email_label.setObjectName("Muted")
        grid.addWidget(email_label, row, 0)
        self._google_email = QLabel("\u2014")
        self._google_email.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(self._google_email, row, 1)
        row += 1

        hint = QLabel(
            "The Google account above is the locked editor identity used for "
            "setup changes, part revisions, and stock history. It remains "
            "available for attribution while working offline.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, row, 0, 1, 2)
        row += 1

        btns = QHBoxLayout()
        signin = QPushButton("Sign in / refresh Google account")
        signin.setObjectName("Primary")
        signin.clicked.connect(self._sign_in)
        test = QPushButton("Test connection")
        test.setObjectName("Ghost")
        test.clicked.connect(self._verify_connection)
        for b in (signin, test):
            b.setCursor(Qt.PointingHandCursor)
            btns.addWidget(b)
        btns.addStretch(1)
        grid.addLayout(btns, row, 0, 1, 2)
        col.addWidget(card)

        col.addWidget(self._build_packets_card())
        col.addWidget(self._build_updates_card())
        conflicts = QPushButton("Review shared-edit conflicts")
        conflicts.clicked.connect(self._review_conflicts)
        col.addWidget(conflicts)

        self._status = QLabel("")
        col.addWidget(self._status)
        col.addStretch(1)

        outer.addWidget(connection, 1)

    def _build_drive_setup_card(self):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        title = QLabel("Connect your team's Google Drive")
        title.setObjectName("H2")
        layout.addWidget(title)
        hint = QLabel("No Supabase account needed. The team owner selects a shared folder and a Google Desktop OAuth client JSON. Each teammate signs in with their own Google account. This preview uses full Drive permission; only connect accounts you intend to authorize.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._drive_folder = QLineEdit(config.GOOGLE_DRIVE_FOLDER_ID)
        self._drive_folder.setPlaceholderText("Paste a shared Drive folder URL")
        layout.addWidget(self._drive_folder)
        self._oauth_file = QLineEdit(config.GOOGLE_OAUTH_CLIENT_FILE)
        self._oauth_file.setPlaceholderText("Google Desktop OAuth client JSON")
        layout.addWidget(self._oauth_file)
        choose = QPushButton("Choose OAuth client JSON")
        choose.clicked.connect(self._choose_oauth_client)
        layout.addWidget(choose)
        self._connect_drive = QPushButton("Save connection and initialize catalog")
        self._connect_drive.setObjectName("Primary")
        self._connect_drive.clicked.connect(self._save_drive_connection)
        layout.addWidget(self._connect_drive)
        help_button = QPushButton("Setup guide")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/loganbateman/xCAL-community/blob/main/docs/GOOGLE_DRIVE_SETUP.md")))
        layout.addWidget(help_button)
        export = QPushButton("Export this workspace backup")
        export.clicked.connect(self._export_workspace)
        layout.addWidget(export)
        return card

    def _export_workspace(self):
        import shutil
        import sqlite3
        from tempfile import TemporaryDirectory
        filename, _ = QFileDialog.getSaveFileName(self, "Export workspace", "xCAL-workspace.zip", "ZIP (*.zip)")
        if not filename:
            return
        workspace = config.DATA_DIR
        def work():
            # Only this workspace, never OAuth files or another team's data.
            # SQLite backup includes WAL transactions; raw copying a live DB
            # could otherwise produce an incomplete export.
            with TemporaryDirectory() as temporary:
                target = Path(temporary) / "workspace"
                shutil.copytree(workspace, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("*.db", "*.sqlite3", "*-wal", "*-shm"))
                for source_path in [*workspace.rglob("*.db"), *workspace.rglob("*.sqlite3")]:
                    destination = target / source_path.relative_to(workspace)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as saved:
                        source.backup(saved)
                shutil.make_archive(str(Path(filename).with_suffix("")), "zip", target)
        self._set_status("Exporting private workspace backup…", config.COLOR_TEXT_MUTED)
        run_async(self, work,
                  on_done=lambda _result: self._set_status("Workspace exported. Store it privately; it contains team data.", config.COLOR_SUCCESS),
                  on_error=lambda message: self._set_status(f"Backup failed: {message}", config.COLOR_WARNING))

    def _choose_oauth_client(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Google Desktop OAuth client", "", "JSON (*.json)")
        if filename:
            self._oauth_file.setText(filename)

    def _save_drive_connection(self):
        from database.drive_transport import DriveJournal
        from dotenv import set_key
        folder = config.clean_drive_folder_id(self._drive_folder.text())
        client_path = Path(self._oauth_file.text()).expanduser().resolve()
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
                raise ValueError("Paste a Google Drive folder URL or folder ID.")
            client = json.loads(client_path.read_text(encoding="utf-8-sig"))
            installed = client.get("installed", {})
            if not installed.get("client_id") or not installed.get("client_secret"):
                raise ValueError("Choose a Desktop OAuth client JSON, not a token or service-account key.")
            if (installed.get("auth_uri") not in ("https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth")
                    or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
                raise ValueError("The OAuth client must use Google's official authorization and token endpoints.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Drive setup", str(exc))
            return
        if (config.GOOGLE_DRIVE_FOLDER_ID and folder != config.GOOGLE_DRIVE_FOLDER_ID
                and (db.get_client().pending_count() or offline_store.pending_sync_summary().total)):
            QMessageBox.warning(self, "Unsynced catalog", "Sync or export this team's pending catalog edits before switching Drives. They have not been moved or deleted.")
            return
        if QMessageBox.question(self, "Connect team Drive", "Create or use the .xcal-catalog-v1 folder inside this Drive?\n\nYour existing videos and data will not be moved. Other members need Editor access to this folder. You may be asked to sign in to Google.") != QMessageBox.Yes:
            return
        previous_client = config.GOOGLE_OAUTH_CLIENT_FILE
        config.GOOGLE_OAUTH_CLIENT_FILE = str(client_path)
        self._connect_drive.setEnabled(False)

        def work():
            credentials = google_drive_client._load_credentials()
            if credentials is not None and credentials.client_id != installed["client_id"]:
                google_drive_client.disconnect()
            google_drive_client.authorize()
            return DriveJournal.initialize(folder)

        def done(_result):
            config.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            saved_client = config.USER_DATA_DIR / "oauth-client.json"
            saved_client.write_text(json.dumps(client), encoding="utf-8")
            config.ENV_PATH.touch(exist_ok=True)
            set_key(str(config.ENV_PATH), "GOOGLE_DRIVE_FOLDER_ID", folder)
            set_key(str(config.ENV_PATH), "GOOGLE_OAUTH_CLIENT_FILE", str(saved_client))
            self._connect_drive.setEnabled(True)
            config.GOOGLE_OAUTH_CLIENT_FILE = previous_client
            QMessageBox.information(self, "Connection saved", "Connection saved. xCAL will close so the next launch loads this team's isolated workspace. Your previous local workspace has not been moved or deleted.")
            self.window().close()

        def failed(message):
            config.GOOGLE_OAUTH_CLIENT_FILE = previous_client
            self._connect_drive.setEnabled(True)
            self._set_status(f"Drive setup did not finish: {message}", config.COLOR_WARNING)

        run_async(self, work, on_done=done, on_error=failed)

    def _review_conflicts(self):
        store = db.get_client()
        entries = store.conflicts()
        if not entries:
            QMessageBox.information(self, "Shared-edit conflicts", "No unresolved catalog conflicts on this device. Sync to check the latest team edits.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Review conflicting team edits")
        dialog.resize(780, 560)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for entry in entries:
            listing.addItem(f"{entry['event']['table']} · {', '.join(entry['fields'])}")
        layout.addWidget(listing)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        layout.addWidget(preview)
        listing.currentRowChanged.connect(lambda row: preview.setPlainText(json.dumps(
            {"current": entries[row]["current"], "other_edit": entries[row]["event"]["after"]}, indent=2)) if row >= 0 else None)
        listing.setCurrentRow(0)
        for label, field in (("Keep current version", "current"), ("Use other edit", "after")):
            button = QPushButton(label)
            def resolve(_checked=False, selected_field=field):
                entry = entries[listing.currentRow()]
                if QMessageBox.question(dialog, "Resolve conflict", "Apply this version? Both original edits remain in Drive history.") != QMessageBox.Yes:
                    return
                value = entry["current"] if selected_field == "current" else entry["event"]["after"]
                try:
                    store.resolve(entry["event"]["table"], entry["event"]["key"], value,
                                  expected=entry["current"])
                except RuntimeError as exc:
                    QMessageBox.warning(dialog, "Refresh comparison", str(exc))
                    return
                dialog.accept()
                if self._on_reconnect:
                    self._on_reconnect()
            button.clicked.connect(resolve)
            layout.addWidget(button)
        dialog.exec()

    def _build_packets_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(8)

        title = QLabel("Packets")
        title.setObjectName("H2")
        lay.addWidget(title)
        desc = QLabel(
            "Scan the team Drive and auto-create a per-kart setup \u201cpacket\u201d "
            "for every session that doesn\u2019t have one yet. Existing setups are "
            "left untouched.")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        row = QHBoxLayout()
        self._packets_btn = QPushButton("Generate packets from Drive")
        self._packets_btn.setObjectName("Primary")
        self._packets_btn.setCursor(Qt.PointingHandCursor)
        self._packets_btn.clicked.connect(self._generate_packets)
        row.addWidget(self._packets_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._packets_status = QLabel("")
        self._packets_status.setObjectName("Muted")
        lay.addWidget(self._packets_status)
        return card

    def _service_state(self, value: str | None, ready: str,
                       missing: str) -> tuple[str, str]:
        if (value or "").strip():
            return ready, config.COLOR_TEXT
        return missing, config.COLOR_WARNING

    def _oauth_state(self, env_vals: dict) -> tuple[str, str]:
        filename = (env_vals.get("GOOGLE_OAUTH_CLIENT_FILE")
                    or config.GOOGLE_OAUTH_CLIENT_FILE).strip()
        path = config.APP_ROOT / filename
        if path.exists():
            return f"{filename} ready", config.COLOR_TEXT
        return f"{filename} missing", config.COLOR_WARNING

    def _build_updates_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(8)

        title = QLabel("Updates")
        title.setObjectName("H2")
        lay.addWidget(title)

        current = QLabel(f"Installed version: {config.APP_VERSION}")
        current.setObjectName("Muted")
        lay.addWidget(current)

        row = QHBoxLayout()
        self._updates_btn = QPushButton("Check for updates")
        self._updates_btn.setObjectName("Primary")
        self._updates_btn.setCursor(Qt.PointingHandCursor)
        self._updates_btn.clicked.connect(self._check_for_updates)
        row.addWidget(self._updates_btn)

        self._release_btn = QPushButton("Open release")
        self._release_btn.setObjectName("Ghost")
        self._release_btn.setCursor(Qt.PointingHandCursor)
        self._release_btn.clicked.connect(lambda: self._open_update_url("release"))
        self._release_btn.setVisible(False)
        row.addWidget(self._release_btn)

        self._download_btn = QPushButton("Install Update")
        self._download_btn.setObjectName("Ghost")
        self._download_btn.setCursor(Qt.PointingHandCursor)
        self._download_btn.clicked.connect(self._install_update)
        self._download_btn.setVisible(False)
        row.addWidget(self._download_btn)

        row.addStretch(1)
        lay.addLayout(row)

        self._updates_status = QLabel("")
        self._updates_status.setObjectName("Muted")
        self._updates_status.setWordWrap(True)
        lay.addWidget(self._updates_status)
        self._latest_update = None
        return card

    def _generate_packets(self):
        self._packets_btn.setEnabled(False)
        self._packets_status.setText("Scanning Drive and building packets\u2026")
        self._packets_status.setStyleSheet(f"color: {config.COLOR_TEXT_MUTED};")

        def _done(res):
            self._packets_btn.setEnabled(True)
            res = res or {}
            self._packets_status.setText(
                f"{res.get('created', 0)} packet(s) created, "
                f"{res.get('skipped', 0)} already existed across "
                f"{res.get('sessions', 0)} session(s).")
            self._packets_status.setStyleSheet(f"color: {config.COLOR_SUCCESS};")

        def _err(msg):
            self._packets_btn.setEnabled(True)
            self._packets_status.setText(f"Packet generation stopped: {msg}")
            self._packets_status.setStyleSheet(f"color: {config.COLOR_WARNING};")

        run_async(self, packets.generate_packets, on_done=_done, on_error=_err)

    def _check_for_updates(self):
        self._updates_btn.setEnabled(False)
        self._release_btn.setVisible(False)
        self._download_btn.setVisible(False)
        self._updates_status.setText("Checking GitHub Releases...")
        self._updates_status.setStyleSheet(f"color: {config.COLOR_TEXT_MUTED};")

        def _done(info):
            self._updates_btn.setEnabled(True)
            self._latest_update = info
            self._updates_status.setText(info.message)
            self._updates_status.setStyleSheet(
                f"color: {config.COLOR_SUCCESS if info.update_available else config.COLOR_TEXT_MUTED};"
            )
            self._release_btn.setVisible(bool(info.release_url))
            can_install = bool(
                info.update_available and info.download_url
                and info.asset_name.lower().endswith(".exe"))
            self._download_btn.setVisible(can_install)
            self._download_btn.setToolTip(info.asset_name or "")

        def _err(message):
            self._updates_btn.setEnabled(True)
            self._updates_status.setText(f"Update check failed: {message}")
            self._updates_status.setStyleSheet("color: #e74c3c;")

        run_async(self, updater.check_for_update, on_done=_done, on_error=_err)

    def _open_update_url(self, kind: str):
        info = self._latest_update
        if not info:
            return
        url = info.download_url if kind == "download" else info.release_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _install_update(self):
        info = self._latest_update
        if not info or not info.update_available or not info.download_url:
            return
        answer = QMessageBox.question(
            self,
            "Install xCAL Update",
            f"Download and install xCAL {info.latest_version} now?\n\n"
            "xCAL will close while the installer replaces this version, then "
            "the updated app will reopen.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        self._updates_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._updates_status.setText(
            f"Downloading xCAL {info.latest_version} installer...")
        self._updates_status.setStyleSheet(f"color: {config.COLOR_TEXT_MUTED};")

        def _done(path):
            self._updates_status.setText("Installer ready. Closing xCAL...")
            self._updates_status.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self.install_requested.emit(path)

        def _error(message):
            self._updates_btn.setEnabled(True)
            self._download_btn.setEnabled(True)
            self._updates_status.setText(f"Update download failed: {message}")
            self._updates_status.setStyleSheet(f"color: {config.COLOR_WARNING};")

        run_async(
            self, lambda: updater.download_installer(info),
            on_done=_done, on_error=_error)

    # ── Backend ──────────────────────────────────────────────────────────────

    def _read_env(self, path: Path) -> dict:
        vals = {}
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    vals[k.strip()] = v.strip().strip("'\"")
        return vals

    def _verify_connection(self):
        self._set_banner("Checking connection...", config.COLOR_TEXT_MUTED)

        def _check():
            db_ok = db.check_connection()
            drive_ok = google_drive_client.check_connection()
            user = (google_drive_client.current_user(
                interactive=False, refresh=True)
                if drive_ok else google_drive_client.cached_user())
            return db_ok, drive_ok, user

        def _done(res):
            dbok, stok, user = res
            self._show_google_user(user)
            if dbok and stok:
                self._set_banner("Connected to Google Drive · catalog available locally",
                                 config.COLOR_SUCCESS)
            else:
                failed = [n for n, ok in (("Catalog sync", dbok),
                                          ("Google Drive", stok)) if not ok]
                self._set_banner(f"Needs attention: {', '.join(failed)}",
                                 config.COLOR_WARNING)

        def _error(message):
            self._show_google_user(google_drive_client.cached_user())
            self._set_banner(
                f"Connection check failed: {message}", config.COLOR_WARNING)

        run_async(self, _check, on_done=_done, on_error=_error)

    def _sign_in(self):
        self._set_status("Opening Google sign-in in your browser\u2026",
                         config.COLOR_TEXT_MUTED)

        def _done(user):
            self._show_google_user(user)
            self._set_status(
                f"Signed in as {user.name}.", config.COLOR_SUCCESS)
            if self._on_reconnect:
                self._on_reconnect()
            self._verify_connection()

        run_async(self, google_drive_client.authorize, on_done=_done,
                  on_error=lambda m: self._set_status(
                      f"Google sign-in failed: {m}", config.COLOR_WARNING))

    def _show_google_user(self, user):
        self._google_name.setText(user.name if user else "Not signed in")
        suffix = "  (offline cache)" if user and not user.verified else ""
        self._google_email.setText((user.email + suffix) if user else "\u2014")
        self._google_name.setStyleSheet(
            f"font-weight:600; color:"
            f"{config.COLOR_SUCCESS if user else config.COLOR_WARNING};")

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _set_banner(self, text, color):
        self._conn_banner.setText(text)
        self._conn_banner.setStyleSheet(
            f"color: {color}; font-size:15px; font-weight:700;")

    def showEvent(self, event):  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._verify_connection()

    def on_show(self):
        pass
