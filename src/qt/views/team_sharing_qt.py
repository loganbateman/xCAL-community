"""Explicit, confirmed Google Drive team-permission controls."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QMessageBox

import config
from qt.workers import run_async
from utils import team_access


class _SharingDialog(QDialog):
    """Do not dismiss a live permission write with Escape or the window close button."""
    busy = False

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


class TeamSharingDialog(_SharingDialog):
    def __init__(self, context, entries, folder, on_changed, parent=None):
        super().__init__(parent)
        dialog = self
        dialog.setWindowTitle("Team sharing")
        dialog.setMinimumWidth(540)
        layout = QVBoxLayout(dialog)
        description = QLabel(
            "The Google Drive folder owner is the team owner. Sharing-capable editors can manage access. "
            "Changes take effect on Google Drive, not just this device.")
        description.setWordWrap(True)
        layout.addWidget(description)
        members = QComboBox()
        for entry in entries:
            if entry["type"] == "user":
                members.addItem(f"{entry['email']} ({entry['role']})", entry)
        layout.addWidget(members)
        actions = QComboBox()
        if context["can_manage"]:
            actions.addItem("Make Editor", "writer")
            actions.addItem("Make Viewer", "reader")
            actions.addItem("Make Commenter", "commenter")
            actions.addItem("Remove folder access", "remove")
        if context["owner"] and not context["shared_drive"]:
            actions.addItem("Offer folder ownership to selected member", "offer")
            actions.addItem("Allow all editors to manage sharing", "enable_admins")
            actions.addItem("Only the owner can manage sharing", "disable_admins")
        if any(entry.get("pending_owner") and entry["email"].casefold() == context["email"].casefold()
               for entry in entries):
            actions.addItem("Accept ownership offered to me", "accept")
        layout.addWidget(actions)
        note = QLabel(
            "Editor administration applies to ALL editors, not a separate per-person xCAL role. "
            "Folder ownership transfer requires the recipient's acceptance and does not transfer ownership "
            "of existing files inside. Google account and organization restrictions still apply. "
            "Viewers and Commenters cannot edit team data in xCAL.")
        note.setWordWrap(True)
        layout.addWidget(note)
        apply = QPushButton("Review change...")
        apply.setEnabled(actions.count() > 0)
        layout.addWidget(apply)
        close = QPushButton("Close")
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)

        def execute():
            action, entry = actions.currentData(), members.currentData()
            if not action or (not entry and action not in ("enable_admins", "disable_admins")):
                return
            if action == "accept":
                entry = next(row for row in entries if row.get("pending_owner")
                             and row["email"].casefold() == context["email"].casefold())
            subject = "all editors" if action in ("enable_admins", "disable_admins") else entry["email"]
            if QMessageBox.question(dialog, "Confirm Google Drive change",
                    f"{actions.currentText()}\n\nApplies to: {subject}\n\n"
                    "This changes real Google Drive permissions. Continue?") != QMessageBox.Yes:
                return
            apply.setEnabled(False)
            close.setEnabled(False)
            dialog.busy = True

            def work():
                if folder != config.GOOGLE_DRIVE_FOLDER_ID:
                    raise RuntimeError("The active team changed. Reopen sharing management.")
                if action in ("enable_admins", "disable_admins"):
                    team_access.set_editor_admins(action == "enable_admins")
                elif action == "offer":
                    team_access.offer_ownership(entry["id"])
                elif action == "accept":
                    team_access.accept_ownership(entry["id"])
                else:
                    team_access.change_member(entry["id"], role=action, remove=action == "remove")

            def saved(_result):
                dialog.busy = False
                dialog.accept()
                on_changed()

            def error(message):
                dialog.busy = False
                apply.setEnabled(True)
                close.setEnabled(True)
                QMessageBox.warning(dialog, "Sharing change did not finish", str(message))

            run_async(dialog, work, on_done=saved, on_error=error)

        apply.clicked.connect(execute)
