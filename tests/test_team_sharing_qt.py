from unittest.mock import Mock

from PySide6.QtWidgets import QComboBox, QPushButton, QMessageBox

from qt.views.team_sharing_qt import TeamSharingDialog
from test_team_onboarding_qt import app, immediate


def dialog(monkeypatch, owner=True, manage=True):
    app()
    monkeypatch.setattr("config.GOOGLE_DRIVE_FOLDER_ID", "folder")
    context = {"email": "owner@example.test", "owner": owner, "can_manage": manage, "shared_drive": False}
    entry = {"id": "member", "email": "friend@example.test", "type": "user", "role": "writer"}
    return TeamSharingDialog(context, [entry], "folder", Mock())


def test_view_only_account_has_no_management_actions(monkeypatch):
    window = dialog(monkeypatch, owner=False, manage=False)
    assert window.findChildren(QComboBox)[1].count() == 0
    assert not window.findChildren(QPushButton)[0].isEnabled()
    window.deleteLater()


def test_sharing_changes_require_explicit_confirmation(monkeypatch):
    window = dialog(monkeypatch)
    change = Mock()
    monkeypatch.setattr("utils.team_access.change_member", change)
    monkeypatch.setattr("qt.views.team_sharing_qt.run_async", immediate)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.No))
    window.findChildren(QPushButton)[0].click()
    change.assert_not_called()
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.Yes))
    window.findChildren(QPushButton)[0].click()
    change.assert_called_once_with("member", role="writer", remove=False)
    window.deleteLater()


def test_escape_does_not_close_pending_permission_write(monkeypatch):
    window = dialog(monkeypatch)
    window.show()
    window.busy = True
    window.reject()
    assert window.isVisible()
    window.busy = False
    window.reject()
    assert not window.isVisible()
    window.deleteLater()
