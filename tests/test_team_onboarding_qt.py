from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from qt.views.drive_folder_picker_qt import DriveFolderPicker
from qt.views.team_onboarding_qt import TeamOnboardingDialog
from qt.views.settings_qt import SettingsView
from utils import team_membership


def app():
    return QApplication.instance() or QApplication([])


def immediate(_parent, work, on_done=None, on_error=None, **_kwargs):
    try:
        result = work()
    except Exception as exc:
        if on_error:
            on_error(str(exc))
    else:
        if on_done:
            on_done(result)


def test_first_login_has_only_create_or_join_choices():
    app()
    dialog = TeamOnboardingDialog(client_ready=False)
    assert dialog._pages.currentIndex() == 0
    assert dialog.create_choice.text() == "Create a Team"
    assert dialog.join_choice.text() == "Join a Team"
    assert "Nothing is wrong with your account" in dialog.status.text()
    assert "original app's client" in dialog.status.text()
    dialog.create_choice.click()
    assert dialog._pages.currentIndex() == 1
    assert not dialog.choose_folder.isEnabled()
    assert not dialog.create_continue.isEnabled()
    dialog.join_choice.click()
    assert dialog._pages.currentIndex() == 2
    assert not dialog.join_continue.isEnabled()
    dialog.deleteLater()


def test_creation_requires_name_and_selected_folder_and_performs_no_write():
    app()
    dialog = TeamOnboardingDialog(client_ready=True)
    dialog.create_choice.click()
    dialog.folder = {"id": "test-folder", "name": "Separate test folder"}
    dialog.team_name.setText("Beta team")
    assert dialog.create_continue.isEnabled()
    dialog.create_continue.click()
    assert dialog.result() == QDialog.Accepted
    assert dialog.selection == {"action": "create", "name": "Beta team", "folder": dialog.folder}
    dialog.deleteLater()


def test_join_code_validation_is_local_and_requires_permission_check_later():
    app()
    dialog = TeamOnboardingDialog(client_ready=True)
    dialog.join_choice.click()
    dialog.invite_code.setText("bad code")
    dialog.join_continue.click()
    assert dialog.selection is None
    code = team_membership.encode_invite({"id": "team", "folder_id": "folder", "name": "Beta"})
    dialog.invite_code.setText(code)
    dialog.join_continue.click()
    assert dialog.selection == {"action": "join", "code": code}
    dialog.deleteLater()


def test_folder_picker_can_select_child_without_opening_it(monkeypatch):
    app()
    read = Mock(return_value=[{"id": "test-folder", "name": "Separate test folder"}])
    details = Mock(return_value={"id": "test-folder", "name": "Separate test folder", "capabilities": {"canAddChildren": True}})
    monkeypatch.setattr("qt.views.drive_folder_picker_qt.run_async", immediate)
    monkeypatch.setattr("utils.google_drive_client.browse_folders", read)
    monkeypatch.setattr("utils.google_drive_client.folder_details", details)
    dialog = DriveFolderPicker()
    assert not dialog._select.isEnabled()
    dialog._enter(dialog._list.item(0))
    assert dialog._path[-1]["id"] == "root"
    dialog._list.setCurrentRow(0)
    assert dialog._select.isEnabled()
    assert dialog._list.item(0).data(Qt.UserRole)["id"] == "test-folder"
    dialog._select.click()
    details.assert_called_once_with("test-folder")
    assert dialog.folder["id"] == "test-folder"
    dialog.deleteLater()


def test_folder_browser_errors_do_not_allow_wrong_folder_selection(monkeypatch):
    app()
    monkeypatch.setattr("qt.views.drive_folder_picker_qt.run_async", immediate)
    monkeypatch.setattr("utils.google_drive_client.browse_folders", Mock(side_effect=OSError("Offline")))
    dialog = DriveFolderPicker()
    dialog._enter(dialog._list.item(0))
    assert dialog._status.text() == "Offline"
    assert not dialog._select.isEnabled()
    assert dialog._list.count() == 0
    dialog.deleteLater()


def test_folder_picker_can_create_folder_in_current_location(monkeypatch):
    app()
    read = Mock(side_effect=[
        [{"id": "existing", "name": "Existing folder"}],
        [
            {"id": "existing", "name": "Existing folder"},
            {"id": "created", "name": "New team folder"},
        ],
    ])
    create = Mock(return_value="created")
    monkeypatch.setattr("qt.views.drive_folder_picker_qt.run_async", immediate)
    monkeypatch.setattr("utils.google_drive_client.browse_folders", read)
    monkeypatch.setattr("utils.google_drive_client.create_folder", create)
    monkeypatch.setattr(
        QInputDialog, "getText", Mock(return_value=("New team folder", True)))

    dialog = DriveFolderPicker()
    dialog._enter(dialog._list.item(0))
    assert dialog._new_folder.isEnabled()
    dialog._new_folder.click()

    create.assert_called_once_with("New team folder", "root")
    assert read.call_count == 2
    assert dialog._list.currentItem().data(Qt.UserRole)["id"] == "created"
    assert dialog._select.text() == "Use selected folder"
    dialog.deleteLater()


def active_team_settings(monkeypatch):
    app()
    monkeypatch.setattr("utils.team_access.authority", Mock(return_value={"can_manage": True}))
    profile = {"id": "team", "name": "Existing team", "folder_id": "test-folder"}
    monkeypatch.setattr("database.client.fetch_app_doc", Mock(return_value=profile))
    monkeypatch.setattr("config.GOOGLE_DRIVE_FOLDER_ID", "test-folder")
    return SettingsView()


def test_active_team_blocks_creation_join_and_drive_switch(monkeypatch):
    settings = active_team_settings(monkeypatch)
    network = Mock()
    monkeypatch.setattr("qt.views.settings_qt.run_async", network)
    assert settings._onboarding_button.isHidden()
    assert not settings._leave_team_button.isHidden()
    assert settings._pick_drive.isHidden()
    assert settings._drive_folder.isReadOnly()
    assert settings._open_team_onboarding() is False
    settings._join_invited_team()
    settings._save_drive_connection()
    settings._pick_drive_folder()
    network.assert_not_called()
    settings._show_team_profile(None)
    assert not settings._onboarding_button.isHidden()
    assert settings._leave_team_button.isHidden()
    settings.deleteLater()


def test_leave_team_blocks_unsynced_work(monkeypatch):
    settings = active_team_settings(monkeypatch)
    monkeypatch.setattr("database.client.get_client", Mock(return_value=Mock(pending_count=lambda: 1)))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    save = Mock()
    monkeypatch.setattr("dotenv.set_key", save)
    settings._leave_team()
    warning.assert_called_once()
    save.assert_not_called()
    settings.deleteLater()


def test_leave_team_only_disconnects_local_configuration(monkeypatch, tmp_path):
    settings = active_team_settings(monkeypatch)
    monkeypatch.setattr("database.client.get_client", Mock(return_value=Mock(pending_count=lambda: 0)))
    monkeypatch.setattr("offline_store.pending_sync_summary", Mock(return_value=Mock(total=0)))
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.Yes))
    env = tmp_path / "settings.env"
    monkeypatch.setattr("config.ENV_PATH", env)
    restart = Mock()
    settings.restart = restart
    settings._pending_team_name = "Stale request"
    settings._leave_team()
    from dotenv import dotenv_values
    assert dotenv_values(env)["GOOGLE_DRIVE_FOLDER_ID"] == ""
    assert settings._pending_team_name is None
    restart.assert_called_once()
    settings.deleteLater()


def access_entry():
    return {"id": "friend", "name": "Friend", "email": "friend@example.test",
            "role": "writer", "type": "user", "inherited": False}


def test_team_tab_shows_drive_access_and_refreshes(monkeypatch):
    settings = active_team_settings(monkeypatch)
    read = Mock(return_value=[access_entry()])
    monkeypatch.setattr("utils.team_membership.list_team_access", read)
    monkeypatch.setattr("qt.views.settings_qt.run_async", immediate)
    settings._settings_tabs.setCurrentIndex(1)
    read.assert_called_once_with("test-folder")
    assert settings._members_table.rowCount() == 1
    assert settings._members_table.item(0, 1).text() == "friend@example.test"
    assert settings._members_table.item(0, 2).text() == "Editor"
    read.return_value = []
    settings._members_refresh.click()
    assert settings._members_table.rowCount() == 0
    assert "no visible" in settings._members_status.text()
    settings.deleteLater()


def test_access_failure_clears_previous_members(monkeypatch):
    settings = active_team_settings(monkeypatch)
    read = Mock(return_value=[access_entry()])
    monkeypatch.setattr("utils.team_membership.list_team_access", read)
    monkeypatch.setattr("qt.views.settings_qt.run_async", immediate)
    settings._refresh_team_members()
    read.side_effect = PermissionError("revoked")
    settings._refresh_team_members()
    assert settings._members_table.rowCount() == 0
    assert settings._members_refresh.isEnabled()
    assert "Could not load" in settings._members_status.text()
    settings.deleteLater()


def test_team_change_ignores_old_access_response(monkeypatch):
    settings = active_team_settings(monkeypatch)
    callbacks = []
    monkeypatch.setattr("qt.views.settings_qt.run_async", lambda parent, work, **kwargs: callbacks.append(kwargs))
    settings._refresh_team_members()
    settings._refresh_team_members()
    assert len(callbacks) == 1
    settings._show_team_profile(None)
    callbacks[0]["on_done"]([access_entry()])
    assert settings._members_table.rowCount() == 0
    assert settings._members_panel.isHidden()
    settings.deleteLater()


def test_recovery_and_no_team_do_not_load_members(monkeypatch):
    settings = active_team_settings(monkeypatch)
    network = Mock()
    monkeypatch.setattr("qt.views.settings_qt.run_async", network)
    monkeypatch.setattr("config.RECOVERY_MODE", True)
    settings._show_team_profile(settings._team_profile)
    settings._refresh_team_members()
    assert settings._members_panel.isHidden()
    monkeypatch.setattr("config.RECOVERY_MODE", False)
    settings._show_team_profile(None)
    settings._refresh_team_members()
    network.assert_not_called()
    settings.deleteLater()


def test_invitation_controls_follow_live_sharing_permission(monkeypatch):
    settings = active_team_settings(monkeypatch)
    monkeypatch.setattr("utils.team_membership.list_team_access", Mock(return_value=[access_entry()]))
    authority = Mock(return_value={"can_manage": False})
    monkeypatch.setattr("utils.team_access.authority", authority)
    monkeypatch.setattr("qt.views.settings_qt.run_async", immediate)
    assert not settings._invite_member.isEnabled()
    settings._refresh_team_members()
    assert not settings._invite_member.isEnabled()
    authority.return_value = {"can_manage": True}
    settings._refresh_team_members()
    assert settings._invite_member.isEnabled()
    authority.side_effect = PermissionError("revoked")
    settings._refresh_team_members()
    assert not settings._invite_member.isEnabled()
    settings.deleteLater()
