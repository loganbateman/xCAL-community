import json
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QMessageBox

import config
from qt.views.settings_qt import SettingsView


def app():
    return QApplication.instance() or QApplication([])


def client_file(tmp_path, value):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_invalid_json_shape_cannot_start_google_authorization(tmp_path, monkeypatch):
    app()
    warning = Mock()
    authorize = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(client_file(tmp_path, [])))
    settings = SettingsView()
    settings._show_team_profile(None)
    settings._drive_folder.setText("new-team")
    settings._save_drive_connection()
    assert warning.call_count == 1
    authorize.assert_not_called()
    settings.deleteLater()


def test_service_account_key_is_rejected(tmp_path, monkeypatch):
    app()
    warning = Mock()
    authorize = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(client_file(tmp_path, {"type": "service_account"})))
    settings = SettingsView()
    settings._show_team_profile(None)
    settings._drive_folder.setText("new-team")
    settings._save_drive_connection()
    assert "Install the latest xCAL Community" in warning.call_args.args[2]
    authorize.assert_not_called()
    settings.deleteLater()


def test_oauth_endpoints_must_be_google_not_arbitrary_server(tmp_path, monkeypatch):
    app()
    warning = Mock()
    authorize = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://example.invalid/auth", "token_uri": "https://example.invalid/token"}})))
    settings = SettingsView()
    settings._show_team_profile(None)
    settings._drive_folder.setText("new-team")
    settings._save_drive_connection()
    assert "Install the latest xCAL Community" in warning.call_args.args[2]
    authorize.assert_not_called()
    settings.deleteLater()


def test_cancelled_connection_does_not_change_config_or_authorize(tmp_path, monkeypatch):
    app()
    authorize = Mock()
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.No)
    monkeypatch.setattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    client = client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}})
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(client))
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    settings._save_drive_connection()
    assert config.GOOGLE_OAUTH_CLIENT_FILE == str(client)
    authorize.assert_not_called()
    settings.deleteLater()


def test_switching_team_with_pending_edits_is_blocked(tmp_path, monkeypatch):
    app()
    warning = Mock()
    authorize = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(config, "GOOGLE_DRIVE_FOLDER_ID", "current-team")
    monkeypatch.setattr("qt.views.settings_qt.db.get_client", lambda: type("Pending", (), {"pending_count": lambda self: 1})())
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}})))
    settings = SettingsView()
    settings._drive_folder.setText("different-team")
    settings._save_drive_connection()
    assert "pending" in warning.call_args.args[2]
    authorize.assert_not_called()
    settings.deleteLater()


def test_failed_settings_commit_keeps_old_client_and_configuration(tmp_path, monkeypatch):
    app()
    monkeypatch.setattr("database.client.fetch_app_doc", Mock(return_value=None))
    monkeypatch.setattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    monkeypatch.setattr(config, "USER_DATA_DIR", tmp_path)
    old = client_file(tmp_path, {"installed": {
        "client_id": "community-synthetic-client", "client_secret": "synthetic",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}})
    original_bytes = old.read_bytes()
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_FILE", str(old))
    env = tmp_path / "settings.env"
    env.write_text("GOOGLE_DRIVE_FOLDER_ID=''\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    monkeypatch.setattr("utils.google_drive_client._load_credentials", Mock(return_value=None))
    monkeypatch.setattr("utils.google_drive_client.authorize", Mock())
    monkeypatch.setattr("database.drive_transport.DriveJournal.initialize", Mock())
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.Yes))
    monkeypatch.setattr("qt.views.settings_qt.update_settings", Mock(side_effect=OSError("interrupted")))
    def immediate(parent, work, on_done, on_error):
        on_done(work())
    monkeypatch.setattr("qt.views.settings_qt.run_async", immediate)
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    restart = Mock()
    settings.restart = restart
    settings._save_drive_connection()
    assert old.read_bytes() == original_bytes
    assert env.read_text() == "GOOGLE_DRIVE_FOLDER_ID=''\n"
    assert config.GOOGLE_OAUTH_CLIENT_FILE == str(old)
    assert settings._connect_drive.isEnabled()
    assert "Could not save connection settings" in settings._status.text()
    restart.assert_not_called()
    settings.deleteLater()
