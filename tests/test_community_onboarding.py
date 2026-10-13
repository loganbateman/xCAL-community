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
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    settings._oauth_file.setText(str(client_file(tmp_path, [])))
    settings._save_drive_connection()
    assert warning.call_count == 1
    authorize.assert_not_called()
    settings.deleteLater()


def test_service_account_key_is_rejected(tmp_path, monkeypatch):
    app()
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    settings._oauth_file.setText(str(client_file(tmp_path, {"type": "service_account"})))
    settings._save_drive_connection()
    assert "Desktop" in warning.call_args.args[2]
    settings.deleteLater()


def test_oauth_endpoints_must_be_google_not_arbitrary_server(tmp_path, monkeypatch):
    app()
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    settings._oauth_file.setText(str(client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://example.invalid/auth", "token_uri": "https://example.invalid/token"}})))
    settings._save_drive_connection()
    assert "official" in warning.call_args.args[2]
    settings.deleteLater()


def test_cancelled_connection_does_not_change_config_or_authorize(tmp_path, monkeypatch):
    app()
    authorize = Mock()
    monkeypatch.setattr("utils.google_drive_client.authorize", authorize)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.No)
    monkeypatch.setattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    original = config.GOOGLE_OAUTH_CLIENT_FILE
    settings = SettingsView()
    settings._drive_folder.setText("new-team")
    settings._oauth_file.setText(str(client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}})))
    settings._save_drive_connection()
    assert config.GOOGLE_OAUTH_CLIENT_FILE == original
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
    settings = SettingsView()
    settings._drive_folder.setText("different-team")
    settings._oauth_file.setText(str(client_file(tmp_path, {"installed": {
        "client_id": "test-client", "client_secret": "synthetic-placeholder",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}})))
    settings._save_drive_connection()
    assert "pending" in warning.call_args.args[2]
    authorize.assert_not_called()
    settings.deleteLater()
