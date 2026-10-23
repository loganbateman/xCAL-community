"""Google-account-first team profile chooser for xCAL Community."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from qt.workers import run_async
from utils import google_drive_client as drive
from utils import team_membership


class TeamOnboardingDialog(QDialog):
    def __init__(self, parent=None, client_ready=False, allow_create=True,
                 initial_user=None):
        super().__init__(parent)
        self.setWindowTitle("xCAL Community profiles")
        self.resize(560, 440)
        self.selection = None
        self.folder = None
        self.user = None
        self.client_ready = client_ready
        self.saved_login_ready = drive.has_saved_credentials()
        self.allow_create = allow_create

        root = QVBoxLayout(self)
        self._pages = QStackedWidget()
        root.addWidget(self._pages, 1)
        self._build_account_page()
        self._build_team_page()
        self._build_create_page()

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        root.addWidget(self.status)
        bottom = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.clicked.connect(self._back)
        bottom.addWidget(self.back)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        root.addLayout(bottom)
        self._page_changed(0)
        if initial_user is not None:
            self._account_ready(initial_user)

    def _build_account_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Choose your Google account")
        title.setObjectName("H1")
        layout.addWidget(title)
        hint = QLabel(
            "Your Google account identifies you and determines which team "
            "profiles you can open. xCAL remembers the last account until you "
            "explicitly sign out.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        cached = drive.cached_user()
        self.account_continue = QPushButton(
            f"Continue as {cached.name}" if cached else "Sign in with Google")
        self.account_continue.setObjectName("Primary")
        self.account_continue.setMinimumHeight(52)
        self.account_continue.setEnabled(
            self.client_ready or self.saved_login_ready)
        if self.saved_login_ready and not self.client_ready:
            self.account_continue.setToolTip(
                "Continue with the Google login already saved on this device")
        self.account_continue.clicked.connect(lambda: self._authenticate(False))
        layout.addWidget(self.account_continue)
        self.account_other = QPushButton("Use another Google account")
        self.account_other.setVisible(bool(cached))
        self.account_other.setEnabled(self.client_ready)
        if not self.client_ready:
            self.account_other.setToolTip(
                "Install the latest xCAL Community to sign in with another account")
        self.account_other.clicked.connect(lambda: self._authenticate(True))
        layout.addWidget(self.account_other)
        layout.addStretch(1)
        self._pages.addWidget(page)

    def _build_team_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Choose your team")
        title.setObjectName("H1")
        layout.addWidget(title)
        self.account_label = QLabel()
        self.account_label.setObjectName("Muted")
        layout.addWidget(self.account_label)
        self.team_choices = QComboBox()
        self.team_choices.setMinimumHeight(48)
        self.team_choices.currentIndexChanged.connect(self._team_changed)
        layout.addWidget(self.team_choices)
        self.team_detail = QLabel()
        self.team_detail.setWordWrap(True)
        self.team_detail.setObjectName("Muted")
        layout.addWidget(self.team_detail)
        actions = QHBoxLayout()
        self.open_team = QPushButton("Open Team")
        self.open_team.setObjectName("Primary")
        self.open_team.clicked.connect(self._open_team)
        actions.addWidget(self.open_team)
        self.refresh_teams = QPushButton("Refresh teams")
        self.refresh_teams.clicked.connect(self._refresh)
        actions.addWidget(self.refresh_teams)
        layout.addLayout(actions)
        invite_hint = QLabel(
            "New invitation? Accept Google's Drive sharing email, then choose "
            "Refresh teams. No xCAL join code is required.")
        invite_hint.setWordWrap(True)
        invite_hint.setObjectName("Muted")
        layout.addWidget(invite_hint)
        self.create_choice = QPushButton("Create a New Team")
        self.create_choice.setVisible(self.allow_create)
        self.create_choice.clicked.connect(lambda: self._set_page(2))
        layout.addWidget(self.create_choice, 0, Qt.AlignLeft)
        layout.addStretch(1)
        self._pages.addWidget(page)

    def _build_create_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Create a new team")
        title.setObjectName("H1")
        layout.addWidget(title)
        self.team_name = QLineEdit()
        self.team_name.setPlaceholderText("Team name")
        self.team_name.setMaxLength(80)
        layout.addWidget(self.team_name)
        self.folder_label = QLabel("Choose a separate folder in Google Drive.")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self.choose_folder = QPushButton("Choose Google Drive folder…")
        self.choose_folder.clicked.connect(self._choose_folder)
        layout.addWidget(self.choose_folder)
        self.create_continue = QPushButton("Create Team")
        self.create_continue.setObjectName("Primary")
        self.create_continue.setEnabled(False)
        self.create_continue.clicked.connect(self._create)
        self.team_name.textChanged.connect(self._update_create)
        layout.addWidget(self.create_continue)
        layout.addStretch(1)
        self._pages.addWidget(page)

    def _set_page(self, index):
        self._pages.setCurrentIndex(index)
        self._page_changed(index)

    def _page_changed(self, index):
        self.back.setVisible(index != 0)

    def _back(self):
        self._set_page(1 if self._pages.currentIndex() == 2 else 0)

    def _set_account_busy(self, busy):
        self.account_continue.setEnabled(
            (self.client_ready or self.saved_login_ready) and not busy)
        self.account_other.setEnabled(self.client_ready and not busy)

    def _authenticate(self, change_account):
        self._set_account_busy(True)
        self.status.setText("Opening Google sign-in…" if change_account
                            else "Checking your Google account…")

        def work():
            if change_account:
                drive.disconnect()
            return drive.authorize()

        def done(user):
            self._set_account_busy(False)
            self._account_ready(user)

        def failed(message):
            self._set_account_busy(False)
            self.status.setText(f"Google sign-in did not finish: {message}")

        run_async(self, work, on_done=done, on_error=failed)

    def _account_ready(self, user):
        self.user = user
        self.account_label.setText(f"Signed in as {user.name} · {user.email}")
        cached = team_membership.cached_teams(user.email)
        self._populate_teams(cached)
        self._set_page(1)
        if cached:
            self.status.setText(
                "Saved team profiles are ready. Choose Refresh teams after "
                "accepting a new invitation.")
        else:
            self._refresh()

    def _populate_teams(self, teams):
        selected_id = (self.team_choices.currentData() or {}).get(
            "profile", {}).get("id")
        self.team_choices.blockSignals(True)
        self.team_choices.clear()
        for team in teams:
            label = team["profile"]["name"]
            folder_name = team["folder"].get("name")
            if folder_name and folder_name != label:
                label = f"{label} — {folder_name}"
            self.team_choices.addItem(label, team)
        if selected_id:
            for index in range(self.team_choices.count()):
                if self.team_choices.itemData(index)["profile"]["id"] == selected_id:
                    self.team_choices.setCurrentIndex(index)
                    break
        self.team_choices.blockSignals(False)
        self._team_changed()

    def _team_changed(self, _index=-1):
        team = self.team_choices.currentData()
        self.open_team.setEnabled(bool(team))
        self.team_detail.setText(
            f"Google Drive folder: {team['folder'].get('name', 'Team folder')}"
            if team else "No teams found for this account yet.")

    def _refresh(self):
        if not self.user or not self.refresh_teams.isEnabled():
            return
        self.refresh_teams.setEnabled(False)
        self.status.setText("Checking Google Drive for team profiles…")

        def done(teams):
            self.refresh_teams.setEnabled(True)
            self._populate_teams(teams)
            self.status.setText(
                f"Found {len(teams)} team{'s' if len(teams) != 1 else ''}."
                if teams else
                "No teams found. Accept a Drive invitation or create a team.")

        def failed(message):
            self.refresh_teams.setEnabled(True)
            self.status.setText(f"Could not refresh teams: {message}")

        run_async(self, team_membership.discover_accessible_teams,
                  on_done=done, on_error=failed)

    def _open_team(self):
        team = self.team_choices.currentData()
        if not team:
            return
        if team.get("verified"):
            self.selection = {"action": "open", "team": team}
            self.accept()
            return
        self.open_team.setEnabled(False)
        self.status.setText("Verifying current Drive access…")

        def done(verified):
            self.selection = {"action": "open", "team": verified}
            self.accept()

        def failed(message):
            self.open_team.setEnabled(True)
            self.status.setText(f"Could not open this team: {message}")

        run_async(self, lambda: team_membership.validate_team_shortcut(team),
                  on_done=done, on_error=failed)

    def _choose_folder(self):
        from qt.views.drive_folder_picker_qt import DriveFolderPicker
        picker = DriveFolderPicker(self)
        if picker.exec() == QDialog.Accepted:
            self.folder = picker.folder
            self.folder_label.setText(self.folder["name"])
            self._update_create()

    def _update_create(self):
        self.create_continue.setEnabled(
            bool(self.folder) and bool(self.team_name.text().strip()))

    def _create(self):
        if not self.folder or not self.team_name.text().strip():
            return
        self.selection = {
            "action": "create", "name": self.team_name.text().strip(),
            "folder": self.folder}
        self.accept()
