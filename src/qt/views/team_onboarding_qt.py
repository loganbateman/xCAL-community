"""First-login team choice, with no writes until Settings confirms connection."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QStackedWidget, QWidget,
)

from qt.workers import run_async
from utils import google_drive_client as drive
from utils.team_membership import decode_invite


class TeamOnboardingDialog(QDialog):
    def __init__(self, parent=None, client_ready=False):
        super().__init__(parent)
        self.setWindowTitle("Welcome to xCAL Community")
        self.resize(520, 360)
        self.selection = None
        self.folder = None
        self.client_ready = client_ready
        root = QVBoxLayout(self)
        self._pages = QStackedWidget()
        root.addWidget(self._pages)
        start = QWidget()
        choices = QVBoxLayout(start)
        title = QLabel("Create a Team or Join a Team")
        title.setObjectName("H1")
        title.setWordWrap(True)
        choices.addWidget(title)
        hint = QLabel(
            "Choose how you want to get started. Your team will own its "
            "Google Drive; members will never need to configure OAuth files.")
        hint.setWordWrap(True)
        choices.addWidget(hint)
        self.create_choice = QPushButton("Create a Team")
        self.create_choice.setObjectName("Primary")
        self.create_choice.setMinimumHeight(52)
        self.create_choice.clicked.connect(lambda: self._pages.setCurrentIndex(1))
        choices.addWidget(self.create_choice)
        self.join_choice = QPushButton("Join a Team")
        self.join_choice.setMinimumHeight(52)
        self.join_choice.clicked.connect(lambda: self._pages.setCurrentIndex(2))
        choices.addWidget(self.join_choice)
        choices.addStretch(1)
        self._pages.addWidget(start)
        create = QWidget()
        create_layout = QVBoxLayout(create)
        create_layout.addWidget(QLabel("Create your team"))
        self.team_name = QLineEdit()
        self.team_name.setPlaceholderText("Team name")
        self.team_name.setMaxLength(80)
        create_layout.addWidget(self.team_name)
        self.folder_label = QLabel("Choose a separate test folder in Google Drive.")
        self.folder_label.setWordWrap(True)
        create_layout.addWidget(self.folder_label)
        self.choose_folder = QPushButton("Choose Google Drive folder…")
        self.choose_folder.setEnabled(client_ready)
        self.choose_folder.clicked.connect(self._choose_folder)
        create_layout.addWidget(self.choose_folder)
        self.create_continue = QPushButton("Continue · Create Team")
        self.create_continue.setObjectName("Primary")
        self.create_continue.setEnabled(False)
        self.create_continue.clicked.connect(self._create)
        self.team_name.textChanged.connect(self._update_create)
        create_layout.addWidget(self.create_continue)
        create_layout.addStretch(1)
        self._pages.addWidget(create)
        join = QWidget()
        join_layout = QVBoxLayout(join)
        join_layout.addWidget(QLabel("Join your team"))
        joining = QLabel("The owner invites your Google email and sends an xCAL invitation code. We check your Drive permission before connecting.")
        joining.setWordWrap(True)
        join_layout.addWidget(joining)
        self.invite_code = QLineEdit()
        self.invite_code.setPlaceholderText("Paste invitation code")
        self.invite_code.setMaxLength(2048)
        join_layout.addWidget(self.invite_code)
        self.join_continue = QPushButton("Continue · Join Team")
        self.join_continue.setObjectName("Primary")
        self.join_continue.setEnabled(client_ready)
        self.join_continue.clicked.connect(self._join)
        join_layout.addWidget(self.join_continue)
        join_layout.addStretch(1)
        self._pages.addWidget(join)
        self.status = QLabel(
            "" if client_ready else
            "Google sign-in is not available in this local beta yet. Nothing "
            "is wrong with your account, and no action is needed from you. "
            "This build will not use the original app's client.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        bottom = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self._pages.setCurrentIndex(0))
        bottom.addWidget(back)
        bottom.addStretch(1)
        cancel = QPushButton("Exit xCAL" if not client_ready else "Close")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        root.addLayout(bottom)

    def _choose_folder(self):
        from qt.views.drive_folder_picker_qt import DriveFolderPicker
        self.choose_folder.setEnabled(False)
        def done(_user):
            self.choose_folder.setEnabled(True)
            picker = DriveFolderPicker(self)
            if picker.exec() == QDialog.Accepted:
                self.folder = picker.folder
                self.folder_label.setText(self.folder["name"])
                self._update_create()
        def failed(message):
            self.choose_folder.setEnabled(True)
            self.status.setText(message)
        run_async(self, drive.authorize, on_done=done, on_error=failed)

    def _update_create(self):
        self.create_continue.setEnabled(self.client_ready and bool(self.folder) and bool(self.team_name.text().strip()))

    def _create(self):
        if not self.folder or not self.team_name.text().strip() or not self.client_ready:
            return
        self.selection = {"action": "create", "name": self.team_name.text().strip(), "folder": self.folder}
        self.accept()

    def _join(self):
        if not self.client_ready:
            return
        try:
            decode_invite(self.invite_code.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.selection = {"action": "join", "code": self.invite_code.text().strip()}
        self.accept()
