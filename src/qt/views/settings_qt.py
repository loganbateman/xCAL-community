"""Settings page (Qt) — cloud config, Google sign-in, connection test, packets."""
from __future__ import annotations
from pathlib import Path
import json
import re
import time

from PySide6.QtCore import Qt, QUrl, Signal, QSize
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QPushButton, QFrame, QScrollArea,
                               QMessageBox, QLineEdit, QFileDialog, QDialog,
                               QListWidget, QPlainTextEdit, QTabWidget, QComboBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView)

import config
import offline_store
import database.client as db
import packets
from utils import google_drive_client, updater
from utils.atomic_files import update_settings
from utils import workspace_backup
from qt.workers import run_async, check_interruption


class SettingsView(QWidget):
    install_requested = Signal(object)
    maintenance_changed = Signal()

    def __init__(self, on_reconnect=None, on_naming_changed=None, parent=None, on_setup_fields_changed=None):
        super().__init__(parent)
        self._on_reconnect = on_reconnect
        self._on_naming_changed = on_naming_changed
        self._on_setup_fields_changed = on_setup_fields_changed
        self._maintenance_loading = False
        self._maintenance_dialog = None
        self._connection_checking = False
        self._connection_checked_at = 0.0
        self._pending_team_name = None
        self._team_profile = None
        self._members_folder = None
        self._members_generation = 0
        self._members_loading = False
        self._member_write_busy = False
        self._member_entries = []
        self._member_authority = None
        self._members_loaded_at = 0.0
        self._sharing_allowed = False
        self._invite_busy = False
        self._build()

    def _build(self):
        self.setObjectName("SettingsPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(12)

        h1 = QLabel("Settings")
        h1.setObjectName("H1")
        outer.addWidget(h1)
        subtitle = QLabel("Manage your account, team, and workspace.")
        subtitle.setObjectName("Muted")
        outer.addWidget(subtitle)

        connection = QWidget()
        connection_lay = QVBoxLayout(connection)
        connection_lay.setContentsMargins(0, 12, 0, 0)
        connection_lay.setSpacing(10)

        self._conn_banner = QLabel("Checking connection...")
        self._conn_banner.setStyleSheet(
            f"color: {config.COLOR_TEXT_MUTED}; font-size:15px; font-weight:700;")
        connection_lay.addWidget(self._conn_banner)

        self._settings_tabs = QTabWidget()
        self._settings_tabs.setObjectName("SettingsTabs")
        connection_lay.addWidget(self._settings_tabs, 1)
        sections = []
        for name in ("Account", "Team", "Workspace"):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            body.setMaximumWidth(1040)
            column = QVBoxLayout(body)
            column.setContentsMargins(4, 20, 16, 16)
            column.setSpacing(16)
            scroll.setWidget(body)
            self._settings_tabs.addTab(scroll, name)
            sections.append(column)
        col, team_col, workspace_col = sections
        drive_card = self._build_drive_setup_card()
        team_col.addWidget(self._build_team_card())
        parameters = QPushButton("Edit fields && part types")
        parameters.clicked.connect(self._edit_team_parameters)
        workspace_col.addWidget(self._action_card(
            "Fields", "Setup sections, tuning parameters and part types.", parameters))

        self._maintenance_button = QPushButton("Edit checklist && intervals")
        self._maintenance_button.clicked.connect(self._edit_maintenance)
        workspace_col.addWidget(self._action_card(
            "Maintenance", "Pre-session checks and service intervals.", self._maintenance_button))

        # Cloud services card
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 16, 20, 18)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        title = QLabel("Google account")
        title.setObjectName("H2")
        grid.addWidget(title, 0, 0, 1, 2)

        row = 1

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

        hint = QLabel("Your account identifies the changes you make, including while offline.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, row, 0, 1, 2)
        row += 1

        btns = QHBoxLayout()
        signin = QPushButton("Sign in with Google")
        self._signin = signin
        signin.setObjectName("Primary")
        signin.clicked.connect(self._sign_in)
        signin.setEnabled(Path(config.GOOGLE_OAUTH_CLIENT_FILE).is_file())
        self._signout = QPushButton("Sign out of Google")
        self._signout.setObjectName("Ghost")
        self._signout.clicked.connect(self._sign_out)
        self._signout.setEnabled(False)
        test = QPushButton("Test connection")
        test.setObjectName("Ghost")
        test.clicked.connect(lambda _checked=False: self._verify_connection(force=True))
        for b in (signin, self._signout, test):
            b.setCursor(Qt.PointingHandCursor)
            btns.addWidget(b)
        btns.addStretch(1)
        grid.addLayout(btns, row, 0, 1, 2)
        col.addWidget(card)
        col.addWidget(drive_card)

        export = QPushButton("Export backup")
        export.clicked.connect(self._export_workspace)
        restore = QPushButton("Restore backup")
        restore.clicked.connect(self._restore_workspace)
        backup_actions = QWidget()
        backup_row = QHBoxLayout(backup_actions)
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.addWidget(export)
        backup_row.addWidget(restore)
        workspace_col.addWidget(self._action_card(
            "Backups", "Export a private copy or restore into a separate offline workspace.", backup_actions))
        workspace_col.addWidget(self._build_updates_card())
        review_baselines = QPushButton("Review baselines")
        review_baselines.clicked.connect(self._review_duplicate_baselines)
        conflicts = QPushButton("Review conflicts")
        conflicts.clicked.connect(self._review_conflicts)
        review_actions = QWidget()
        review_row = QHBoxLayout(review_actions)
        review_row.setContentsMargins(0, 0, 0, 0)
        review_row.addWidget(conflicts)
        review_row.addWidget(review_baselines)
        workspace_col.addWidget(self._action_card(
            "Review", "Resolve shared-edit conflicts and duplicate baselines.", review_actions))

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.hide()
        connection_lay.addWidget(self._status)
        for column in sections:
            column.addStretch(1)

        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.PointingHandCursor)
            if not button.objectName():
                button.setObjectName("Secondary")
                button.style().unpolish(button)
                button.style().polish(button)

        outer.addWidget(connection, 1)
        self._settings_tabs.currentChanged.connect(self._settings_tab_changed)

    def _edit_maintenance(self):
        if self._maintenance_dialog is not None:
            self._maintenance_dialog.raise_()
            self._maintenance_dialog.activateWindow()
            return
        if self._maintenance_loading:
            return
        self._maintenance_loading = True
        self._maintenance_button.setEnabled(False)
        import cache
        import maintenance_store
        from qt.views.maintenance_settings_qt import MaintenanceSettingsDialog
        def done(snapshot):
            self._maintenance_loading = False
            sessions, _age = cache.load_sessions()
            dialog = MaintenanceSettingsDialog(snapshot, sessions, self)
            self._maintenance_dialog = dialog
            dialog.saved.connect(lambda _policy: self.maintenance_changed.emit())
            dialog.finished.connect(self._maintenance_dialog_closed)
            dialog.open()
        def error(message):
            self._maintenance_loading = False
            self._maintenance_button.setEnabled(True)
            QMessageBox.warning(self, "Maintenance settings", message)
        run_async(self, maintenance_store.load_snapshot, on_done=done,
                  on_error=error)

    def _maintenance_dialog_closed(self, _result):
        self._maintenance_dialog = None
        self._maintenance_button.setEnabled(True)

    def _action_card(self, title, description, button):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("H2")
        heading.setStyleSheet(
            f"border-left: 3px solid {config.COLOR_ACCENT}; padding-left: 10px; font-weight: 700;")
        layout.addWidget(heading)
        hint = QLabel(description)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(button, 0, Qt.AlignLeft)
        return card

    def _build_drive_setup_card(self):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel("Team Drive folder")
        title.setObjectName("H2")
        layout.addWidget(title)
        client_ready = Path(config.GOOGLE_OAUTH_CLIENT_FILE).is_file()
        hint = QLabel("Choose a folder for your team's shared workspace.")
        self._drive_folder_hint = hint
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._drive_folder_label = QLabel("Folder link")
        layout.addWidget(self._drive_folder_label)
        self._drive_folder = QLineEdit(config.GOOGLE_DRIVE_FOLDER_ID)
        self._drive_folder.setPlaceholderText("Paste a shared Drive folder URL")
        layout.addWidget(self._drive_folder)
        self._drive_folder_link = QLabel(
            '<a href="team-folder" style="color: #a8c7fa;">Open team folder in Google Drive ↗</a>')
        self._drive_folder_link.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self._drive_folder_link.linkActivated.connect(self._open_team_drive_folder)
        self._drive_folder_link.hide()
        layout.addWidget(self._drive_folder_link)
        self._pick_drive = QPushButton("Choose a folder from Google Drive…")
        self._pick_drive.clicked.connect(self._pick_drive_folder)
        self._pick_drive.setEnabled(client_ready)
        layout.addWidget(self._pick_drive, 0, Qt.AlignLeft)
        self._connect_drive = QPushButton("Save connection")
        self._connect_drive.setObjectName("Primary")
        self._connect_drive.clicked.connect(self._save_drive_connection)
        layout.addWidget(self._connect_drive, 0, Qt.AlignLeft)
        help_button = QPushButton("Setup guide")
        self._drive_setup_help = help_button
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/loganbateman/xCAL-community/blob/main/docs/GOOGLE_DRIVE_SETUP.md")))
        layout.addWidget(help_button, 0, Qt.AlignLeft)
        return card

    def _open_team_drive_folder(self, _link=None):
        folder = config.clean_drive_folder_id(config.GOOGLE_DRIVE_FOLDER_ID)
        if not config.RECOVERY_MODE and re.fullmatch(r"[A-Za-z0-9_-]+", folder):
            QDesktopServices.openUrl(QUrl(f"https://drive.google.com/drive/folders/{folder}"))

    def _pick_drive_folder(self):
        if self._require_no_team():
            return
        from qt.views.drive_folder_picker_qt import DriveFolderPicker
        self._pick_drive.setEnabled(False)
        def done(_user):
            self._pick_drive.setEnabled(True)
            picker = DriveFolderPicker(self)
            if picker.exec() == QDialog.Accepted and picker.folder:
                self._drive_folder.setText(picker.folder["id"])
                self._set_status(f"Selected {picker.folder['name']}. Nothing has been created yet; connect when ready.", config.COLOR_TEXT_MUTED)
        def failed(message):
            self._pick_drive.setEnabled(True)
            self._set_status(f"Google sign-in did not finish: {message}", config.COLOR_WARNING)
        run_async(self, google_drive_client.authorize, on_done=done, on_error=failed)

    def _build_team_card(self):
        from utils import team_membership
        card = QFrame()
        card.setObjectName("Card")
        root = QVBoxLayout(card)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        heading = QLabel("Your team & invitations")
        heading.setObjectName("H2")
        root.addWidget(heading)
        self._team_status = QLabel()
        self._team_status.setWordWrap(True)
        root.addWidget(self._team_status)
        onboarding = QPushButton("Create a Team or Join a Team…")
        self._onboarding_button = onboarding
        onboarding.setObjectName("Secondary")
        onboarding.clicked.connect(self._open_team_onboarding)
        root.addWidget(onboarding, 0, Qt.AlignLeft)
        self._leave_team_button = QPushButton("Leave team")
        self._leave_team_button.setObjectName("Danger")
        self._leave_team_button.clicked.connect(self._leave_team)
        root.addWidget(self._leave_team_button, 0, Qt.AlignLeft)
        self._switch_team_button = QPushButton("Switch team")
        self._switch_team_button.clicked.connect(self._switch_team)
        root.addWidget(self._switch_team_button, 0, Qt.AlignLeft)
        self._members_panel = QWidget()
        self._members_panel.setObjectName("TeamMembersPanel")
        self._members_panel.setStyleSheet("QWidget#TeamMembersPanel { background: transparent; }")
        members_layout = QVBoxLayout(self._members_panel)
        members_layout.setContentsMargins(0, 8, 0, 8)
        members_header = QHBoxLayout()
        members_title = QLabel("People with Drive access")
        members_title.setObjectName("H2")
        members_header.addWidget(members_title, 1)
        self._members_refresh = QPushButton("Refresh")
        self._members_refresh.setObjectName("Ghost")
        self._members_refresh.clicked.connect(lambda _checked=False: self._refresh_team_members(force=True))
        members_header.addWidget(self._members_refresh)
        self._manage_sharing = QPushButton("Advanced sharing...")
        self._manage_sharing.clicked.connect(self._open_sharing_management)
        members_header.addWidget(self._manage_sharing)
        members_layout.addLayout(members_header)
        self._members_table = QTableWidget(0, 4)
        self._members_table.setHorizontalHeaderLabels(["Name", "Google email", "Role", ""])
        self._members_table.verticalHeader().setDefaultSectionSize(54)
        self._members_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._members_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._members_table.setAlternatingRowColors(True)
        self._members_table.verticalHeader().hide()
        self._members_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._members_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._members_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._members_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._members_table.setColumnWidth(3, 56)
        self._members_table.setMinimumHeight(140)
        self._members_table.setMaximumHeight(300)
        members_layout.addWidget(self._members_table)
        self._members_status = QLabel("Open the Team tab to load current Drive access.")
        self._members_status.setObjectName("Muted")
        self._members_status.setWordWrap(True)
        members_layout.addWidget(self._members_status)
        root.addWidget(self._members_panel)
        self._team_name = QLineEdit()
        self._team_name.setPlaceholderText("Team name")
        self._team_name.setMaxLength(80)
        self._team_name.hide()
        root.addWidget(self._team_name)
        self._create_team = QPushButton("Create team in the connected folder")
        self._create_team.clicked.connect(self._create_team_profile)
        self._create_team.hide()
        root.addWidget(self._create_team)
        self._invite_email = QLineEdit()
        self._invite_email.setPlaceholderText("Teammate's Google email")
        root.addWidget(self._invite_email)
        self._invite_member = QPushButton("Invite teammate · grant Editor access")
        self._invite_member.clicked.connect(self._send_team_invite)
        root.addWidget(self._invite_member, 0, Qt.AlignLeft)
        hint = QLabel(
            "Invitations grant Editor access through Google Drive. Teammates "
            "accept Google's email, then log in to xCAL—no join code needed.")
        self._invite_hint = hint
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        profile = db.fetch_app_doc(team_membership.TEAM_DOC)
        self._show_team_profile(profile)
        return card

    def _show_team_profile(self, profile):
        folder = config.GOOGLE_DRIVE_FOLDER_ID if profile and not config.RECOVERY_MODE else None
        if folder != self._members_folder:
            self._members_generation += 1
            self._members_folder = folder
            self._members_loading = False
            self._member_entries = []
            self._member_authority = None
            self._members_loaded_at = 0.0
            self._sharing_allowed = False
            self._members_table.setRowCount(0)
            self._members_refresh.setEnabled(True)
            self._members_status.setText("Open the Team tab to load current Drive access.")
        self._members_panel.setVisible(bool(folder))
        self._team_profile = profile
        self._onboarding_button.setVisible(not bool(profile))
        self._leave_team_button.setVisible(bool(profile))
        self._switch_team_button.setVisible(bool(profile))
        self._invite_hint.setVisible(bool(profile))
        self._drive_folder.setReadOnly(bool(profile))
        setup_visible = not bool(profile) and not config.RECOVERY_MODE
        self._drive_folder.setVisible(setup_visible)
        self._drive_folder_label.setVisible(setup_visible)
        self._drive_folder_link.setVisible(bool(folder))
        self._drive_setup_help.setVisible(setup_visible)
        self._drive_folder_hint.setText(
            "Your team's shared files." if folder else
            "Cloud access is disabled in this recovery copy." if config.RECOVERY_MODE else
            "Choose a folder for your team's shared workspace.")
        self._pick_drive.setVisible(not bool(profile))
        self._connect_drive.setVisible(not bool(profile))
        if profile:
            self._pending_team_name = None
            self._drive_folder.setText(config.GOOGLE_DRIVE_FOLDER_ID)
        self._team_status.setText(f"Team: {profile['name']}" if profile else "Sign in and choose a team to get started.")
        self._create_team.setEnabled(bool(config.GOOGLE_DRIVE_FOLDER_ID) and not profile)
        self._invite_member.setEnabled(bool(profile) and self._sharing_allowed and not self._invite_busy)
        self._invite_member.setVisible(bool(profile))
        self._invite_email.setVisible(bool(profile))
        if config.RECOVERY_MODE:
            for widget in (self._onboarding_button, self._leave_team_button,
                           self._switch_team_button, self._invite_hint,
                           self._invite_email, self._invite_member, self._pick_drive, self._connect_drive):
                widget.hide()
            self._drive_folder.setReadOnly(True)
            self._team_status.setText("Offline recovery copy. Team records are preserved; cloud access is disabled.")

    def _settings_tab_changed(self, index):
        if index == 1:
            self._refresh_team_members(force=False)

    def _refresh_team_members(self, force=True):
        if self._members_loading or self._member_write_busy or config.RECOVERY_MODE or not self._team_profile:
            return
        folder = config.GOOGLE_DRIVE_FOLDER_ID
        if not folder or self._team_profile.get("folder_id") != folder:
            self._members_table.setRowCount(0)
            self._members_status.setText("Connect this team's Drive folder to view access.")
            return
        if not force and self._member_entries and time.monotonic() - self._members_loaded_at < 60:
            return
        from utils import team_membership, team_access
        self._members_generation += 1
        generation = self._members_generation
        self._members_loading = True
        self._members_refresh.setEnabled(False)
        self._members_status.setText(
            "Refreshing Drive access…" if self._members_table.rowCount() else "Loading Drive access…")

        def current():
            return generation == self._members_generation and folder == config.GOOGLE_DRIVE_FOLDER_ID

        def done(result):
            if not current():
                return
            entries, authority = result
            self._member_entries = entries
            self._member_authority = authority
            self._members_loaded_at = time.monotonic()
            self._sharing_allowed = authority["can_manage"]
            self._invite_member.setEnabled(self._sharing_allowed and not self._invite_busy)
            self._members_loading = False
            self._members_refresh.setEnabled(True)
            self._render_team_members(entries, authority)
            self._members_status.setText(
                "Click a person's role to change or remove their access. "
                "This list shows Drive access, not online status." if entries else
                "Google Drive returned no visible sharing entries for this account.")

        def failed(_message):
            if not current():
                return
            self._members_loading = False
            self._members_refresh.setEnabled(True)
            self._sharing_allowed = False
            self._member_authority = None
            self._member_entries = []
            self._members_loaded_at = 0.0
            self._invite_member.setEnabled(False)
            self._members_table.setRowCount(0)
            self._members_status.setText(
                "Refresh failed. Showing the last known Drive access list."
                if self._members_table.rowCount() else
                "Could not load Drive access. Check your connection and folder permissions, then Refresh.")

        def load():
            entries = team_membership.list_team_access(folder)
            return entries, team_access.authority()

        run_async(self, load, on_done=done, on_error=failed)

    def _render_team_members(self, entries, authority):
        """Render Drive members with direct role controls for manageable people."""
        roles = {"owner": "Owner", "organizer": "Manager", "fileOrganizer": "Content manager",
                 "writer": "Editor", "commenter": "Commenter", "reader": "Viewer"}
        self._members_table.setRowCount(len(entries))
        current_email = authority.get("email", "").casefold()
        for row, entry in enumerate(entries):
            self._members_table.removeCellWidget(row, 2)
            self._members_table.removeCellWidget(row, 3)
            for column, value in enumerate((entry.get("name") or "—", entry.get("email") or "—")):
                self._members_table.setItem(row, column, QTableWidgetItem(value))

            manageable = (
                authority.get("can_manage")
                and entry.get("type") == "user"
                and not entry.get("inherited")
                and entry.get("role") not in ("owner", "organizer")
                and entry.get("email", "").casefold() != current_email
            )
            if manageable:
                # Keep an underlying text item for accessibility, automated
                # inspection and clean copy/paste even though the visible cell
                # uses a direct role picker.
                self._members_table.setItem(
                    row, 2, QTableWidgetItem(roles.get(entry.get("role"), entry.get("role", "—"))))
                picker = QComboBox()
                picker.setObjectName("MemberRolePicker")
                picker.setMinimumWidth(132)
                picker.setStyleSheet(
                    "QComboBox#MemberRolePicker { padding: 4px 28px 4px 10px; "
                    "min-height: 28px; color: #f1f3f4; background: #202124; }"
                    "QComboBox#MemberRolePicker QAbstractItemView { "
                    "color: #f1f3f4; background: #202124; selection-background-color: #44474e; }")
                for label, value in (("Editor", "writer"), ("Commenter", "commenter"), ("Viewer", "reader")):
                    picker.addItem(label, value)
                picker.setCurrentIndex(max(0, picker.findData(entry.get("role"))))
                picker.currentIndexChanged.connect(
                    lambda _index, item=entry, control=picker: self._change_team_member_role(item, control))
                self._members_table.setCellWidget(row, 2, picker)
                remove = QPushButton()
                remove.setObjectName("RemoveTeamMember")
                remove.setIcon(QIcon(str(config.ASSETS_DIR / "icon_trash.svg")))
                remove.setIconSize(QSize(20, 20))
                remove.setFixedSize(36, 36)
                remove.setToolTip("Remove this person's team folder access")
                remove.setAccessibleName(f"Remove {entry.get('name') or entry.get('email')} from team")
                remove.setStyleSheet(
                    "QPushButton#RemoveTeamMember { padding: 0px; border: none; background: transparent; }"
                    "QPushButton#RemoveTeamMember:hover { background: #523033; border-radius: 6px; }")
                remove.clicked.connect(
                    lambda _checked=False, item=entry, control=picker:
                    self._change_team_member_role(item, control, action="remove"))
                host = QWidget()
                remove_layout = QHBoxLayout(host)
                remove_layout.setContentsMargins(0, 0, 0, 0)
                remove_layout.addWidget(remove, 0, Qt.AlignCenter)
                self._members_table.setCellWidget(row, 3, host)
            else:
                role_item = QTableWidgetItem(roles.get(entry.get("role"), entry.get("role", "—")))
                if entry.get("inherited"):
                    role_item.setToolTip("Inherited access must be changed on the parent folder in Google Drive.")
                self._members_table.setItem(row, 2, role_item)

    def _change_team_member_role(self, entry, picker, action=None):
        """Apply a role selection directly from the members table."""
        from utils import team_access
        if self._member_write_busy or config.RECOVERY_MODE:
            return
        folder = config.GOOGLE_DRIVE_FOLDER_ID
        action = action or picker.currentData()
        previous = entry.get("role")
        if not action or action == previous:
            return
        labels = {"writer": "Editor", "commenter": "Commenter", "reader": "Viewer", "remove": "Remove access"}
        email = entry.get("email") or entry.get("name") or "this member"
        prompt = (
            f"Are you sure you want to remove {email} from this team's Google Drive folder?"
            if action == "remove" else f"Change {email} to {labels[action]}?")
        if QMessageBox.question(self, "Confirm team access", prompt) != QMessageBox.Yes:
            picker.blockSignals(True)
            picker.setCurrentIndex(max(0, picker.findData(previous)))
            picker.blockSignals(False)
            return
        picker.setEnabled(False)
        self._member_write_busy = True
        self._members_table.setEnabled(False)
        self._members_refresh.setEnabled(False)
        self._manage_sharing.setEnabled(False)
        self._members_status.setText(f"Updating access for {email}…")

        def work():
            if folder != config.GOOGLE_DRIVE_FOLDER_ID:
                raise RuntimeError("The active team changed. Reopen the Team tab.")
            team_access.change_member(entry["id"], role=action, remove=action == "remove")

        def finish():
            self._member_write_busy = False
            self._members_table.setEnabled(True)
            self._members_refresh.setEnabled(True)
            self._manage_sharing.setEnabled(True)
            return folder == config.GOOGLE_DRIVE_FOLDER_ID

        def saved(_result):
            if not finish():
                return
            if action == "remove":
                self._member_entries = [row for row in self._member_entries if row.get("id") != entry.get("id")]
            else:
                entry["role"] = action
            self._members_loaded_at = time.monotonic()
            self._render_team_members(self._member_entries, self._member_authority)
            self._members_status.setText(
                f"Removed {email} from the team." if action == "remove" else f"{email} is now {labels[action]}.")

        def failed(message):
            if not finish():
                return
            picker.blockSignals(True)
            picker.setCurrentIndex(max(0, picker.findData(previous)))
            picker.blockSignals(False)
            picker.setEnabled(True)
            self._members_status.setText("The Drive access change did not finish.")
            QMessageBox.warning(self, "Could not change team access", str(message))

        run_async(self, work, on_done=saved, on_error=failed)

    def _open_sharing_management(self):
        """Open advanced ownership and sharing-policy controls, reusing loaded data."""
        from utils import team_access, team_membership
        if config.RECOVERY_MODE or not self._team_profile:
            return
        folder = config.GOOGLE_DRIVE_FOLDER_ID
        self._manage_sharing.setEnabled(False)

        def open_dialog(context, entries):
            self._manage_sharing.setEnabled(True)
            if folder != config.GOOGLE_DRIVE_FOLDER_ID:
                return
            from qt.views.team_sharing_qt import TeamSharingDialog

            def changed():
                self._members_loaded_at = 0.0
                self._refresh_team_members(force=True)
                self._set_status("Google Drive sharing updated.", config.COLOR_SUCCESS)

            TeamSharingDialog(context, entries, folder, changed, self).exec()

        if self._member_authority is not None and self._member_entries:
            open_dialog(self._member_authority, self._member_entries)
            return

        def load():
            context = team_access.authority()
            return context, team_membership.list_team_access(folder)

        def failed(message):
            self._manage_sharing.setEnabled(True)
            self._set_status(f"Could not manage sharing: {message}", config.COLOR_WARNING)

        def done(result):
            context, entries = result
            if folder != config.GOOGLE_DRIVE_FOLDER_ID:
                self._manage_sharing.setEnabled(True)
                return
            self._member_authority = context
            self._member_entries = entries
            self._members_loaded_at = time.monotonic()
            open_dialog(context, entries)

        run_async(self, load, on_done=done, on_error=failed)

    def _review_duplicate_baselines(self):
        store = db.get_client()
        groups = store.baseline_duplicates()
        if not groups:
            QMessageBox.information(self, "Baselines", "No duplicate kart/track baselines were found locally. Sync first to include team changes.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Review duplicate baselines")
        dialog.resize(640, 500)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Select the variant to keep active. Other variants are removed from the active list; their original journal entries remain preserved. Sync before reviewing.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        choices = QComboBox()
        for group in groups:
            for row in group:
                choices.addItem(f"{row.get('name') or 'Baseline'} | {row.get('track') or 'All tracks'} | {row.get('updated_at', '')} | {row['id'][:8]}", (row, group))
        layout.addWidget(choices)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        layout.addWidget(preview)

        def show():
            row, _group = choices.currentData()
            preview.setPlainText(json.dumps(row, indent=2, ensure_ascii=False))

        choices.currentIndexChanged.connect(show)
        show()
        keep = QPushButton("Keep selected variant")
        layout.addWidget(keep)
        close = QPushButton("Cancel")
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)

        def consolidate():
            row, group = choices.currentData()
            if QMessageBox.question(dialog, "Consolidate baselines",
                    f"Keep this baseline and remove {len(group) - 1} duplicate(s) from the active catalog?\n\nOriginal journal history is retained. This will sync to your team.") != QMessageBox.Yes:
                return
            try:
                store.consolidate_baselines(row["id"], group)
            except Exception as exc:
                QMessageBox.warning(dialog, "Could not consolidate", str(exc))
                return
            dialog.accept()
            self.maintenance_changed.emit()
            self._set_status("Baseline group consolidated locally. Sync to share the decision.", config.COLOR_SUCCESS)

        keep.clicked.connect(consolidate)
        dialog.exec()

    def _require_no_team(self):
        if config.RECOVERY_MODE:
            self._set_status("This is an offline recovery copy. Open your normal app to manage a team.", config.COLOR_WARNING)
            return True
        if not self._team_profile:
            return False
        self._set_status("Leave your current team before creating or joining another team.", config.COLOR_WARNING)
        return True

    def _leave_team(self):
        if not self._team_profile:
            return
        if not self._connect_drive.isEnabled():
            self._set_status("Wait for the connection attempt to finish before leaving.", config.COLOR_WARNING)
            return
        if db.get_client().pending_count() or offline_store.pending_sync_summary().total:
            QMessageBox.warning(self, "Unsynced changes", "Sync your pending changes before leaving this team. Your local work is still saved.")
            return
        if QMessageBox.question(
                self, "Leave team",
                f"Leave {self._team_profile['name']} on this device?\n\n"
                "xCAL will restart and let you create or join a team. "
                "Shared files and your local workspace will be kept. "
                "Your Google Drive sharing permissions will not change.") != QMessageBox.Yes:
            return
        try:
            update_settings(config.ENV_PATH, {"GOOGLE_DRIVE_FOLDER_ID": ""})
        except OSError as exc:
            self._set_status(f"Could not leave team: {exc}", config.COLOR_WARNING)
            return
        self._pending_team_name = None
        self._leave_team_button.setEnabled(False)
        if hasattr(self.window(), "restart"):
            self.window().restart()
        else:
            self.window().close()

    def _open_team_onboarding(self, _checked=False, initial_user=None):
        if self._require_no_team():
            return False
        if not self._connect_drive.isEnabled():
            QMessageBox.information(self, "Connection in progress", "Wait for the current connection attempt to finish.")
            return False
        from qt.views.team_onboarding_qt import TeamOnboardingDialog
        dialog = TeamOnboardingDialog(
            self, client_ready=Path(config.GOOGLE_OAUTH_CLIENT_FILE).is_file(),
            initial_user=initial_user)
        if dialog.exec() != QDialog.Accepted or not dialog.selection:
            return False
        choice = dialog.selection
        if choice["action"] == "create":
            self._pending_team_name = choice["name"]
            self._drive_folder.setText(choice["folder"]["id"])
            self._save_drive_connection()
        else:
            self._activate_team(choice["team"])
        return True

    def _activate_team(self, team):
        try:
            update_settings(config.ENV_PATH, {
                "GOOGLE_DRIVE_FOLDER_ID": team["folder"]["id"]})
        except OSError as exc:
            self._set_status(f"Could not save team profile: {exc}",
                             config.COLOR_WARNING)
            return
        QMessageBox.information(
            self, "Opening team",
            f"Opening {team['profile']['name']} as {team['account']}. "
            "xCAL will restart into that team's workspace.")
        if hasattr(self.window(), "restart"):
            self.window().restart()
        else:
            self.window().close()

    def _switch_team(self):
        if config.RECOVERY_MODE or not self._team_profile:
            return
        if (db.get_client().pending_count()
                or offline_store.pending_sync_summary().total):
            QMessageBox.warning(
                self, "Unsynced changes",
                "Sync pending changes before switching teams. Your local work "
                "has not been deleted.")
            return
        from qt.views.team_onboarding_qt import TeamOnboardingDialog
        dialog = TeamOnboardingDialog(
            self, client_ready=Path(config.GOOGLE_OAUTH_CLIENT_FILE).is_file(),
            allow_create=False)
        if dialog.exec() == QDialog.Accepted and dialog.selection:
            self._activate_team(dialog.selection["team"])

    def _create_team_profile(self):
        if self._require_no_team():
            return
        from utils import team_membership
        if QMessageBox.question(self, "Create test team", "Create a team profile in the currently connected Drive folder? No existing data or videos will be moved.") != QMessageBox.Yes:
            return
        self._create_team.setEnabled(False)
        name = self._team_name.text()
        def failed(message):
            self._create_team.setEnabled(True)
            self._set_status(f"Team not created: {message}", config.COLOR_WARNING)
        run_async(self, lambda: team_membership.create_team(name),
                  on_done=self._show_team_profile, on_error=failed)

    def _send_team_invite(self):
        from utils import team_membership
        if self._invite_busy or not self._sharing_allowed or config.RECOVERY_MODE:
            return
        email = self._invite_email.text().strip()
        if QMessageBox.question(self, "Invite teammate", f"Grant {email} Editor access to this team's Google Drive folder?\n\nThey will be able to edit and delete team files. Google will send a sharing email for new invitations.") != QMessageBox.Yes:
            return
        self._invite_member.setEnabled(False)
        self._invite_busy = True
        def done(invitation):
            self._invite_busy = False
            self._refresh_team_members()
            QMessageBox.information(
                self, "Invitation sent",
                f"{invitation['email']} now has Editor access to "
                f"{invitation['team']}. They should accept Google's Drive "
                "sharing email, open xCAL Community, and choose "
                "\"Already in a team? Log in.\"")
        def failed(message):
            self._invite_busy = False
            self._sharing_allowed = False
            self._refresh_team_members()
            self._set_status(f"Invitation did not finish: {message}", config.COLOR_WARNING)
        run_async(self, lambda: team_membership.invite_member(email), on_done=done, on_error=failed)

    def _edit_team_parameters(self):
        from qt.views.team_setup_fields_qt import TeamSetupFieldsDialog
        from utils import team_setup_fields
        def done(document):
            applied = self._on_setup_fields_changed(document) if self._on_setup_fields_changed else False
            self._set_status("Team parameters saved." + (" Open a setup to use them." if applied else " Restart xCAL to use them."), config.COLOR_SUCCESS)
            if self._on_reconnect:
                self._on_reconnect()
        dialog = TeamSetupFieldsDialog(team_setup_fields.load(), self, on_saved=done)
        dialog.exec()

    def _export_workspace(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Export workspace", "xCAL-workspace.zip", "ZIP (*.zip)")
        if not filename:
            return
        workspace = config.DATA_DIR
        self._set_status("Exporting private workspace backup…", config.COLOR_TEXT_MUTED)
        run_async(self, lambda: workspace_backup.export_workspace(workspace, filename, check_interruption),
                  on_done=lambda _result: self._set_status("Workspace exported. Store it privately; it contains team data.", config.COLOR_SUCCESS),
                  on_error=lambda message: self._set_status(f"Backup failed: {message}", config.COLOR_WARNING))

    def _restore_workspace(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Restore workspace backup", "", "ZIP (*.zip)")
        if not filename:
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose where to keep the recovery copy")
        if not parent:
            return
        if QMessageBox.question(self, "Restore backup",
                "Create a new offline recovery copy here?\n\n"
                "Your active workspace and pending edits will stay in place. "
                "The recovery copy cannot connect to Google Drive.") != QMessageBox.Yes:
            return
        def done(root):
            self._set_status(f"Recovery copy saved to {root}", config.COLOR_SUCCESS)
            if QMessageBox.question(self, "Recovery ready",
                    "Open the recovered workspace in a separate offline window?") == QMessageBox.Yes:
                try:
                    workspace_backup.open_recovery(root)
                except OSError as exc:
                    self._set_status(f"Recovery was saved, but could not be opened: {exc}", config.COLOR_WARNING)
        self._set_status("Validating and restoring backup...", config.COLOR_TEXT_MUTED)
        run_async(self, lambda: workspace_backup.restore_workspace(filename, parent, check_interruption), on_done=done,
                  on_error=lambda message: self._set_status(f"Restore failed: {message}", config.COLOR_WARNING))

    def _save_drive_connection(self):
        if self._require_no_team():
            return
        from database.drive_transport import DriveJournal
        if not self._connect_drive.isEnabled():
            return
        folder = config.clean_drive_folder_id(self._drive_folder.text())
        team_name = self._pending_team_name
        client_path = Path(config.GOOGLE_OAUTH_CLIENT_FILE).resolve()
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
                raise ValueError("Paste a Google Drive folder URL or folder ID.")
            client = json.loads(client_path.read_text(encoding="utf-8-sig"))
            if not isinstance(client, dict) or not isinstance(client.get("installed"), dict):
                raise ValueError("Google sign-in is not configured correctly in this build. Install the latest xCAL Community.")
            installed = client.get("installed", {})
            if not installed.get("client_id") or not installed.get("client_secret"):
                raise ValueError("Google sign-in is incomplete in this build. Install the latest xCAL Community.")
            if (installed.get("auth_uri") not in ("https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth")
                    or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
                raise ValueError("Google sign-in configuration is invalid in this build. Install the latest xCAL Community.")
        except OSError:
            QMessageBox.warning(self, "Google sign-in unavailable",
                                "This build is missing its Google sign-in setup. Install the latest xCAL Community; no configuration files are needed.")
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Drive setup", str(exc))
            return
        if (config.GOOGLE_DRIVE_FOLDER_ID and folder != config.GOOGLE_DRIVE_FOLDER_ID
                and (db.get_client().pending_count() or offline_store.pending_sync_summary().total)):
            QMessageBox.warning(self, "Unsynced catalog", "Sync or export this team's pending catalog edits before switching Drives. They have not been moved or deleted.")
            return
        if QMessageBox.question(self, "Connect team Drive", "Create or use the .xcal-catalog-v1 folder inside this Drive?\n\nYour existing videos and data will not be moved. Other members need Editor access to this folder. You may be asked to sign in to Google.") != QMessageBox.Yes:
            return
        self._connect_drive.setEnabled(False)

        def work():
            credentials = google_drive_client._load_credentials()
            if credentials is not None and credentials.client_id != installed["client_id"]:
                google_drive_client.disconnect()
            google_drive_client.authorize()
            if team_name:
                from utils import team_membership
                team_membership._owner_context(folder)
            result = DriveJournal.initialize(folder)
            if team_name:
                from utils import team_membership
                team_membership.create_team_in_folder(folder, team_name)
            return result

        def done(_result):
            try:
                update_settings(config.ENV_PATH, {"GOOGLE_DRIVE_FOLDER_ID": folder})
            except OSError as exc:
                failed(f"Could not save connection settings: {exc}")
                return
            self._connect_drive.setEnabled(True)
            QMessageBox.information(self, "Connection saved", "Connection saved. xCAL will close so the next launch loads this team's isolated workspace. Your previous local workspace has not been moved or deleted.")
            if hasattr(self.window(), "restart"):
                self.window().restart()
            else:
                self.window().close()

        def failed(message):
            self._pending_team_name = None
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
        self._packets_btn.setObjectName("Secondary")
        self._packets_btn.setCursor(Qt.PointingHandCursor)
        self._packets_btn.clicked.connect(self._generate_packets)
        row.addWidget(self._packets_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._packets_status = QLabel("")
        self._packets_status.setObjectName("Muted")
        lay.addWidget(self._packets_status)
        return card

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
        self._updates_btn.setObjectName("Secondary")
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
                info.update_available and info.download_url and info.sha256
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

    def _verify_connection(self, force=False):
        if config.RECOVERY_MODE:
            self._show_google_user(None)
            self._set_banner("Offline recovery copy - cloud sync is disabled", config.COLOR_WARNING)
            return
        if self._connection_checking:
            return
        if not force and time.monotonic() - self._connection_checked_at < 60:
            self._show_google_user(google_drive_client.cached_user())
            return
        self._connection_checking = True
        self._set_banner("Checking connection...", config.COLOR_TEXT_MUTED)

        def _check():
            db_ok = db.check_connection()
            drive_ok = google_drive_client.check_connection()
            user = (google_drive_client.current_user(
                interactive=False, refresh=True)
                if drive_ok else google_drive_client.cached_user())
            return db_ok, drive_ok, user

        def _done(res):
            self._connection_checking = False
            self._connection_checked_at = time.monotonic()
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
            self._connection_checking = False
            self._connection_checked_at = time.monotonic()
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
            self._verify_connection(force=True)
            from utils import team_membership
            if not db.fetch_app_doc(team_membership.TEAM_DOC):
                self._open_team_onboarding(initial_user=user)

        run_async(self, google_drive_client.authorize, on_done=_done,
                  on_error=lambda m: self._set_status(
                      f"Google sign-in failed: {m}", config.COLOR_WARNING))

    def _sign_out(self):
        if (db.get_client().pending_count()
                or offline_store.pending_sync_summary().total):
            QMessageBox.warning(
                self, "Unsynced changes",
                "Sync pending changes before signing out. Your local work has "
                "not been deleted.")
            return
        user = google_drive_client.cached_user()
        label = user.email if user else "this Google account"
        if QMessageBox.question(
                self, "Sign out of Google",
                f"Sign out {label} from xCAL Community on this device?\n\n"
                "Team files and Drive permissions will not be changed.") != QMessageBox.Yes:
            return
        try:
            google_drive_client.disconnect()
            update_settings(config.ENV_PATH, {"GOOGLE_DRIVE_FOLDER_ID": ""})
        except OSError as exc:
            self._set_status(f"Could not finish signing out: {exc}",
                             config.COLOR_WARNING)
            return
        if hasattr(self.window(), "restart"):
            self.window().restart()
        else:
            self.window().close()

    def _show_google_user(self, user):
        self._google_name.setText(user.name if user else "Not signed in")
        suffix = "  (offline cache)" if user and not user.verified else ""
        self._google_email.setText((user.email + suffix) if user else "\u2014")
        self._google_name.setStyleSheet(
            f"font-weight:600; color:"
            f"{config.COLOR_SUCCESS if user else config.COLOR_WARNING};")
        self._signin.setVisible(not bool(user))
        self._signout.setEnabled(bool(user))

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setVisible(bool(text))
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
