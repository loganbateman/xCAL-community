"""Direct member editing stays responsive and confirms real permission writes."""
from unittest.mock import Mock

from PySide6.QtWidgets import QMessageBox, QPushButton

from test_team_onboarding_qt import active_team_settings, access_entry, immediate


def loaded_settings(monkeypatch):
    settings = active_team_settings(monkeypatch)
    read = Mock(return_value=[access_entry()])
    monkeypatch.setattr("utils.team_membership.list_team_access", read)
    monkeypatch.setattr("qt.views.settings_qt.run_async", immediate)
    settings._refresh_team_members()
    return settings, read


def test_reopening_team_tab_reuses_list_but_refresh_is_explicit(monkeypatch):
    settings, read = loaded_settings(monkeypatch)
    settings._settings_tab_changed(1)
    assert read.call_count == 1
    settings._members_refresh.click()
    assert read.call_count == 2
    settings.deleteLater()


def test_role_change_cancel_then_save_updates_list_without_refetch(monkeypatch):
    settings, read = loaded_settings(monkeypatch)
    change = Mock()
    monkeypatch.setattr("utils.team_access.change_member", change)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.No))
    picker = settings._members_table.cellWidget(0, 2)
    picker.setCurrentIndex(picker.findData("reader"))
    change.assert_not_called()
    assert picker.currentData() == "writer"
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.Yes))
    picker.setCurrentIndex(picker.findData("reader"))
    change.assert_called_once_with("friend", role="reader", remove=False)
    assert settings._members_table.cellWidget(0, 2).currentData() == "reader"
    assert read.call_count == 1
    settings.deleteLater()


def test_failed_role_write_restores_previous_selection(monkeypatch):
    settings, _read = loaded_settings(monkeypatch)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    monkeypatch.setattr("utils.team_access.change_member", Mock(side_effect=PermissionError("revoked")))
    picker = settings._members_table.cellWidget(0, 2)
    settings._members_table.cellWidget(0, 3).findChild(QPushButton).click()
    assert picker.currentData() == "writer"
    assert picker.isEnabled()
    assert not settings._member_write_busy
    assert settings._members_table.rowCount() == 1
    settings.deleteLater()


def test_trash_button_confirms_before_removing_access(monkeypatch):
    settings, _read = loaded_settings(monkeypatch)
    change = Mock()
    monkeypatch.setattr("utils.team_access.change_member", change)
    confirm = Mock(return_value=QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "question", confirm)
    picker = settings._members_table.cellWidget(0, 2)
    assert picker.findData("remove") == -1
    button = settings._members_table.cellWidget(0, 3).findChild(QPushButton)
    assert not button.icon().isNull()
    button.click()
    change.assert_not_called()
    assert "Are you sure" in confirm.call_args.args[2]
    confirm.return_value = QMessageBox.Yes
    button.click()
    change.assert_called_once_with("friend", role="remove", remove=True)
    assert settings._members_table.rowCount() == 0
    settings.deleteLater()


def test_owner_and_inherited_access_have_no_role_picker(monkeypatch):
    settings, _read = loaded_settings(monkeypatch)
    entry = access_entry()
    entry["role"] = "owner"
    settings._render_team_members([entry], {"can_manage": True})
    assert settings._members_table.cellWidget(0, 2) is None
    entry["role"] = "writer"
    entry["inherited"] = True
    settings._render_team_members([entry], {"can_manage": True})
    assert settings._members_table.cellWidget(0, 2) is None
    settings.deleteLater()
