from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from utils.atomic_files import atomic_write_text, update_settings
from utils import google_drive_client as drive


def test_replace_failure_preserves_original_and_cleans_temporary(monkeypatch, tmp_path):
    target = tmp_path / "state.json"
    target.write_text("original")
    monkeypatch.setattr("utils.atomic_files.os.replace", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError):
        atomic_write_text(target, "replacement")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_multi_setting_commit_is_all_or_nothing(monkeypatch, tmp_path):
    target = tmp_path / "settings.env"
    target.write_text("GOOGLE_DRIVE_FOLDER_ID='old'\nOTHER='preserved'\n")
    from dotenv import set_key
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted")
        return set_key(*args, **kwargs)
    monkeypatch.setattr("dotenv.set_key", fail_second)
    with pytest.raises(OSError):
        update_settings(target, {"GOOGLE_DRIVE_FOLDER_ID": "new", "GOOGLE_OAUTH_CLIENT_FILE": "new.json"})
    assert dotenv_values(target) == {"GOOGLE_DRIVE_FOLDER_ID": "old", "OTHER": "preserved"}
    assert list(tmp_path.iterdir()) == [target]


def test_successful_settings_commit_preserves_unrelated_keys(tmp_path):
    target = tmp_path / "settings.env"
    target.write_text("OTHER='keep'\n")
    update_settings(target, {"GOOGLE_DRIVE_FOLDER_ID": "new", "GOOGLE_OAUTH_CLIENT_FILE": "new.json"})
    assert dotenv_values(target) == {"OTHER": "keep", "GOOGLE_DRIVE_FOLDER_ID": "new", "GOOGLE_OAUTH_CLIENT_FILE": "new.json"}


def test_interrupted_token_save_preserves_existing_credentials(monkeypatch, tmp_path):
    target = tmp_path / "google_token.json"
    target.write_text('{"token":"synthetic-old"}')
    monkeypatch.setattr(drive, "TOKEN_PATH", target)
    monkeypatch.setattr("utils.atomic_files.os.replace", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(drive.GoogleDriveError):
        drive._save_credentials(Mock(to_json=lambda: '{"token":"synthetic-new"}'))
    assert target.read_text() == '{"token":"synthetic-old"}'


def test_recovery_refuses_google_auth_before_loading_tokens(monkeypatch):
    monkeypatch.setattr("config.RECOVERY_MODE", True)
    load = Mock()
    monkeypatch.setattr(drive, "_load_credentials", load)
    with pytest.raises(drive.GoogleDriveError, match="recovery"):
        drive._credentials(interactive=True)
    load.assert_not_called()
