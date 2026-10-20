"""Release trust and interrupted-download coverage; no real network or installers."""
import hashlib
import io
from pathlib import Path
from unittest.mock import Mock
import unittest
import re

import pytest
import requests
from utils import updater

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTENT = b"synthetic executable bytes"
DIGEST = hashlib.sha256(CONTENT).hexdigest()
URL = "https://github.com/loganbateman/xCAL-community/releases/download/v9.0.0/xCAL-Setup-9.0.0.exe"


def release(digest=DIGEST, url=URL):
    return {"tag_name": "v9.0.0", "html_url": "https://untrusted.invalid",
            "assets": [{"name": "xCAL-Setup-9.0.0.exe", "browser_download_url": url,
                        "digest": "sha256:" + digest}]}


def response(payload=None, content=CONTENT, status=200, location=None):
    result = Mock(status_code=status, headers={"Location": location} if location else {})
    result.json.return_value = payload or release()
    result.iter_content.return_value = [content]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def info():
    return updater.UpdateInfo("0.1.0", latest_version="9.0.0", update_available=True,
                              asset_name="xCAL-Setup-9.0.0.exe", download_url=URL, sha256=DIGEST)


def test_team_records_cannot_supply_update_metadata(monkeypatch):
    fetch = Mock(side_effect=AssertionError("Must never read team update manifests"))
    monkeypatch.setattr("database.client.fetch_app_doc", fetch)
    get = Mock(return_value=response())
    monkeypatch.setattr(updater.requests, "get", get)
    result = updater.check_for_update()
    assert result.sha256 == DIGEST
    assert result.release_url == "https://github.com/loganbateman/xCAL-community/releases/tag/v9.0.0"
    assert not hasattr(result, "chunk_keys")
    fetch.assert_not_called()
    assert get.call_args.args[0] == updater.LATEST_RELEASE_URL
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("payload", [release(digest=""), release(url="https://attacker.invalid/app.exe")])
def test_unverified_asset_never_offers_installation(monkeypatch, payload):
    monkeypatch.setattr(updater.requests, "get", Mock(return_value=response(payload)))
    result = updater.check_for_update()
    assert result.update_available
    assert not result.download_url
    assert not result.sha256


def test_api_failure_does_not_fall_back_to_team_data(monkeypatch):
    monkeypatch.setattr(updater.requests, "get", Mock(side_effect=requests.ConnectionError()))
    assert not updater.check_for_update().update_available


def test_verified_download_and_launch_rechecks_hash(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "check_for_update", info)
    monkeypatch.setattr(updater.requests, "get", Mock(return_value=response()))
    directory = tmp_path / "download"
    directory.mkdir()
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda **kwargs: str(directory))
    target = updater.download_installer(info())
    assert target.read_bytes() == CONTENT
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    updater.launch_installer(target)
    launch.assert_called_once()
    with pytest.raises(ValueError, match="verified"):
        updater.launch_installer(target)


def test_changed_installer_cannot_execute(monkeypatch, tmp_path):
    target = tmp_path / "installer.exe"
    target.write_bytes(b"tampered")
    updater._verified_downloads[target.resolve()] = DIGEST
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    with pytest.raises(ValueError, match="changed"):
        updater.launch_installer(target)
    launch.assert_not_called()


def test_arbitrary_local_executable_cannot_launch(tmp_path):
    target = tmp_path / "anything.exe"
    target.write_bytes(CONTENT)
    with pytest.raises(ValueError, match="verified"):
        updater.launch_installer(target)


def test_forged_info_is_rejected_before_download(monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda: updater.UpdateInfo("0.1.0"))
    get = Mock()
    monkeypatch.setattr(updater.requests, "get", get)
    with pytest.raises(ValueError, match="verified"):
        updater.download_installer(info())
    get.assert_not_called()


@pytest.mark.parametrize("bad_content", [b"tampered", b""])
def test_bad_download_cleans_staging_and_never_launches(monkeypatch, tmp_path, bad_content):
    monkeypatch.setattr(updater, "check_for_update", info)
    monkeypatch.setattr(updater.requests, "get", Mock(return_value=response(content=bad_content)))
    directory = tmp_path / "download"
    directory.mkdir()
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda **kwargs: str(directory))
    with pytest.raises(ValueError):
        updater.download_installer(info())
    assert not directory.exists()


def test_stream_interruption_cleans_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "check_for_update", info)
    download = response()
    def broken_stream(**kwargs):
        yield b"partial"
        raise requests.ConnectionError("interrupted")
    download.iter_content.side_effect = broken_stream
    monkeypatch.setattr(updater.requests, "get", Mock(return_value=download))
    directory = tmp_path / "download"
    directory.mkdir()
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda **kwargs: str(directory))
    with pytest.raises(requests.ConnectionError):
        updater.download_installer(info())
    assert not directory.exists()


def test_redirect_outside_github_is_rejected_before_contact(monkeypatch):
    get = Mock(return_value=response(status=302, location="https://attacker.invalid/installer.exe"))
    monkeypatch.setattr(updater.requests, "get", get)
    with pytest.raises(ValueError, match="outside"):
        updater._download(URL, io.BytesIO())
    assert get.call_count == 1


def test_github_asset_redirect_is_allowed(monkeypatch):
    get = Mock(side_effect=[response(status=302, location="https://release-assets.githubusercontent.com/asset"),
                            response()])
    monkeypatch.setattr(updater.requests, "get", get)
    target = io.BytesIO()
    assert updater._download(URL, target) == DIGEST
    assert target.getvalue() == CONTENT


class ReleaseMetadataTests(unittest.TestCase):
    def test_community_has_independent_version_and_storage(self):
        config_source = (PROJECT_ROOT / "src" / "config.py").read_text(
            encoding="utf-8")
        app_version = re.search(
            r'APP_VERSION\s*=\s*"([^"]+)"', config_source).group(1)
        self.assertRegex(app_version, r"^\d+\.\d+\.\d+$")
        self.assertIn('"xCAL-community"', config_source)
        self.assertNotIn("ACCESS_CODE =", config_source)

    def test_no_team_credentials_are_in_source(self):
        self.assertFalse((PROJECT_ROOT / ".env").exists())
        self.assertFalse((PROJECT_ROOT / "credentials.json").exists())
        self.assertFalse((PROJECT_ROOT / "src" / "qt" / "login.py").exists())

    def test_ci_checks_tests_and_publication_safety(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
        self.assertIn("pytest -q", workflow)
        self.assertIn("check_publication.py", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7", workflow)
        self.assertNotIn("SUPABASE", workflow)

    def test_packager_does_not_bundle_team_secrets_or_supabase(self):
        spec = (PROJECT_ROOT / "xCAL.spec").read_text(encoding="utf-8")
        self.assertNotIn("supabase", spec)
        self.assertNotIn("credentials.json", spec)
        self.assertNotIn('".env"', spec)
