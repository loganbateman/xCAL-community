"""Native, Drive-inspired folder browser for choosing a team workspace."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox, QInputDialog,
)

import config
from qt.workers import run_async
from utils import google_drive_client as drive


class DriveFolderPicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DrivePicker")
        self.setWindowTitle("Choose a Google Drive folder")
        self.resize(720, 600)
        self.folder = None
        self._path = []
        self._generation = 0
        self._nodes = []
        self._busy = False
        self._pending_select_id = None
        self._folder_icon = QIcon(str(config.ASSETS_DIR / "icon_folder.svg"))
        self._drive_icon = QIcon(str(config.ASSETS_DIR / "icon_drive.svg"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Choose your team's Drive workspace")
        title.setObjectName("H1")
        layout.addWidget(title)
        hint = QLabel(
            "Pick an existing folder or create a new one. Nothing is moved or "
            "shared until you finish creating the team.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self._back = QPushButton("Back")
        self._back.setObjectName("Ghost")
        self._back.clicked.connect(self._go_back)
        navigation.addWidget(self._back)
        self._location = QLabel("Google Drive")
        self._location.setObjectName("DriveBreadcrumb")
        self._location.setWordWrap(True)
        navigation.addWidget(self._location, 1)
        self._new_folder = QPushButton("＋ New folder")
        self._new_folder.setObjectName("DriveAction")
        self._new_folder.clicked.connect(self._create_folder)
        navigation.addWidget(self._new_folder)
        self._refresh = QPushButton("Refresh")
        self._refresh.setObjectName("Ghost")
        self._refresh.clicked.connect(self._refresh_current)
        navigation.addWidget(self._refresh)
        layout.addLayout(navigation)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Search folders in this location…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._render)
        layout.addWidget(self._filter)
        self._list = QListWidget()
        self._list.setObjectName("DriveFolderList")
        self._list.setIconSize(QSize(28, 28))
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(2)
        self._list.itemDoubleClicked.connect(self._enter)
        self._list.currentItemChanged.connect(
            lambda *_args: self._selection_changed())
        layout.addWidget(self._list, 1)

        self._status = QLabel()
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self._select = QPushButton("Use this folder")
        self._select.setObjectName("Primary")
        self._select.clicked.connect(self._choose)
        buttons.addWidget(self._select)
        layout.addLayout(buttons)
        self._show_locations()

    def _show_locations(self):
        self._path = []
        self._filter.clear()
        self._nodes = [
            {"id": "root", "name": "My Drive", "location": "children"},
            {"id": "shared", "name": "Shared with me", "location": "shared"},
            {"id": "drives", "name": "Shared drives", "location": "drives"},
        ]
        self._location.setText("Google Drive")
        self._back.setEnabled(False)
        self._new_folder.setEnabled(False)
        self._refresh.setEnabled(False)
        self._select.setEnabled(False)
        self._status.setText(
            "Choose My Drive, Shared with me, or a shared drive.")
        self._render()

    def _render(self):
        self._list.clear()
        term = self._filter.text().casefold()
        matched_pending = False
        for node in self._nodes:
            if term not in node["name"].casefold():
                continue
            item = QListWidgetItem(node["name"])
            item.setIcon(
                self._drive_icon if node.get("location") else self._folder_icon)
            item.setSizeHint(QSize(0, 48))
            item.setData(Qt.UserRole, node)
            self._list.addItem(item)
            if node.get("id") == self._pending_select_id:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                matched_pending = True
        if matched_pending:
            self._pending_select_id = None

    def _enter(self, item):
        if self._busy:
            return
        self._path.append(item.data(Qt.UserRole))
        self._load()

    def _go_back(self):
        if self._busy:
            return
        self._path.pop()
        if self._path:
            self._load()
        else:
            self._show_locations()

    def _can_create_here(self):
        if not self._path:
            return False
        node = self._path[-1]
        return not node.get("location") or node.get("id") == "root"

    def _set_busy(self, busy):
        self._busy = busy
        self._list.setEnabled(not busy)
        self._back.setEnabled(bool(self._path) and not busy)
        self._new_folder.setEnabled(self._can_create_here() and not busy)
        self._refresh.setEnabled(bool(self._path) and not busy)
        self._selection_changed()

    def _selection_changed(self):
        if not hasattr(self, "_select"):
            return
        selected = self._list.currentItem()
        node = selected.data(Qt.UserRole) if selected else None
        real_folder = bool(
            (node and not node.get("location"))
            or (self._path and not self._path[-1].get("location")))
        self._select.setEnabled(real_folder and not self._busy)
        self._select.setText(
            "Use selected folder"
            if node and not node.get("location") else "Use this folder")

    def _load(self):
        self._generation += 1
        generation = self._generation
        node = self._path[-1]
        self._set_busy(True)
        self._location.setText(
            "  ›  ".join(item["name"] for item in self._path))
        self._status.setText("Loading folders…")
        self._filter.clear()

        def done(nodes):
            if generation != self._generation:
                return
            self._nodes = nodes
            self._set_busy(False)
            self._render()
            self._status.setText(
                "Double-click a folder to open it, or select one to use it."
                if nodes else
                "This folder is empty. Use it or create a folder inside it.")

        def failed(message):
            self._set_busy(False)
            self._status.setText(message)
            self._nodes = []
            self._render()
            self._select.setEnabled(False)

        run_async(
            self,
            lambda: drive.browse_folders(
                node["id"], node.get("location", "children")),
            on_done=done, on_error=failed)

    def _refresh_current(self):
        if self._busy or not self._path:
            return
        self._load()

    def _create_folder(self):
        if self._busy or not self._can_create_here():
            return
        name, accepted = QInputDialog.getText(
            self, "New folder", "Folder name:", text="xCAL Community")
        name = name.strip()
        if not accepted:
            return
        if not name:
            QMessageBox.warning(self, "New folder", "Enter a folder name.")
            return
        if len(name) > 180 or "/" in name or "\\" in name:
            QMessageBox.warning(
                self, "New folder",
                "Use a folder name under 180 characters without slashes.")
            return
        if any(node["name"].casefold() == name.casefold()
               for node in self._nodes):
            if QMessageBox.question(
                    self, "Folder already exists",
                    f'A folder named “{name}” is already listed here. Create '
                    "another folder with the same name?") != QMessageBox.Yes:
                return
        parent_id = self._path[-1]["id"]
        self._set_busy(True)
        self._status.setText(f'Creating “{name}”…')

        def done(folder_id):
            self._pending_select_id = folder_id
            self._load()

        def failed(message):
            self._set_busy(False)
            self._status.setText(f"Could not create folder: {message}")

        run_async(
            self, lambda: drive.create_folder(name, parent_id),
            on_done=done, on_error=failed)

    def _choose(self):
        if self._busy or not self._path:
            return
        selected = self._list.currentItem()
        node = selected.data(Qt.UserRole) if selected else None
        folder_id = (
            node["id"] if node and not node.get("location")
            else self._path[-1]["id"])
        self._set_busy(True)

        def done(folder):
            self._set_busy(False)
            if not folder.get("capabilities", {}).get("canAddChildren"):
                QMessageBox.warning(
                    self, "Folder is read-only",
                    "Choose a folder where your account has Editor access.")
                return
            self.folder = folder
            self.accept()

        run_async(
            self, lambda: drive.folder_details(folder_id), on_done=done,
            on_error=lambda message: (
                self._set_busy(False), self._status.setText(message)))
