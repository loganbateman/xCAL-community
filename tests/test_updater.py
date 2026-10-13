from __future__ import annotations

import sys
import hashlib
import tempfile
import unittest
from pathlib import Path
import re
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import config
from utils import updater


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _DownloadResponse:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield self._content


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.original_version = config.APP_VERSION
        config.APP_VERSION = "0.4.0"

    def tearDown(self):
        config.APP_VERSION = self.original_version

    @patch("utils.updater._manifest_update", return_value=None)
    @patch("utils.updater.requests.get")
    def test_detects_new_release_and_prefers_installer(self, get, _manifest):
        get.return_value = _Response(
            payload={
                "tag_name": "v0.5.0",
                "html_url": "https://github.com/loganbateman/xCAL-community/releases/v0.5.0",
                "assets": [
                    {
                        "name": "xCAL-0.5.0.zip",
                        "browser_download_url": "https://example.com/app.zip",
                    },
                    {
                        "name": "xCAL-Setup-0.5.0.exe",
                        "browser_download_url": "https://example.com/setup.exe",
                    },
                ],
            },
        )

        info = updater.check_for_update()

        self.assertTrue(info.update_available)
        self.assertEqual(info.latest_version, "0.5.0")
        self.assertEqual(info.asset_name, "xCAL-Setup-0.5.0.exe")
        self.assertEqual(info.download_url, "https://example.com/setup.exe")

    @patch("utils.updater._manifest_update", return_value=None)
    @patch("utils.updater.requests.get")
    def test_private_repo_returns_actionable_message(self, get, _manifest):
        get.return_value = _Response(status_code=404)

        info = updater.check_for_update()

        self.assertFalse(info.update_available)
        self.assertIn("private", info.message.lower())

    @patch("utils.updater._manifest_update", return_value=None)
    @patch("utils.updater.requests.get")
    def test_current_release_is_not_update(self, get, _manifest):
        get.return_value = _Response(payload={"tag_name": "v0.4.0"})

        info = updater.check_for_update()

        self.assertFalse(info.update_available)
        self.assertIn("up to date", info.message)

    @patch("utils.updater.db.fetch_app_doc")
    def test_manifest_can_report_private_repo_update(self, fetch_app_doc):
        fetch_app_doc.return_value = {
            "version": "0.6.0",
            "release_url": "https://github.com/loganbateman/xCAL-community/releases/tag/v0.6.0",
            "download_url": "https://github.com/loganbateman/xCAL-community/releases/download/v0.6.0/xCAL-Setup-0.6.0.exe",
            "asset_name": "xCAL-Setup-0.6.0.exe",
            "sha256": "a" * 64,
            "chunk_keys": ["app_update_chunk_000", "app_update_chunk_001"],
        }

        info = updater.check_for_update()

        self.assertTrue(info.update_available)
        self.assertEqual(info.latest_version, "0.6.0")
        self.assertEqual(info.asset_name, "xCAL-Setup-0.6.0.exe")
        self.assertEqual(info.sha256, "a" * 64)
        self.assertEqual(info.chunk_keys, (
            "app_update_chunk_000", "app_update_chunk_001"))

    @patch("utils.updater.db.fetch_app_doc")
    def test_v041_through_v043_detect_v051_manifest(self, fetch_app_doc):
        fetch_app_doc.return_value = {
            "version": "0.5.1",
            "release_url": (
                "https://github.com/loganbateman/xCAL-community/releases/tag/v0.5.1"),
            "download_url": (
                "https://github.com/loganbateman/xCAL-community/releases/download/"
                "v0.5.1/xCAL-Setup-0.5.1.exe"),
            "asset_name": "xCAL-Setup-0.5.1.exe",
        }

        for old_version in ("0.4.1", "0.4.2", "0.4.3"):
            with self.subTest(old_version=old_version):
                config.APP_VERSION = old_version
                info = updater.check_for_update()
                self.assertTrue(info.update_available)
                self.assertEqual(info.latest_version, "0.5.1")
                self.assertEqual(
                    info.download_url,
                    "https://github.com/loganbateman/xCAL-community/releases/download/"
                    "v0.5.1/xCAL-Setup-0.5.1.exe",
                )

    @patch("utils.updater.requests.get")
    def test_download_installer_verifies_manifest_hash(self, get):
        content = b"verified installer bytes"
        get.return_value = _DownloadResponse(content)
        info = updater.UpdateInfo(
            current_version="0.4.3",
            latest_version="0.5.5",
            update_available=True,
            download_url=(
                "https://github.com/loganbateman/xCAL-community/releases/download/"
                "v0.5.5/xCAL-Setup-0.5.5.exe"),
            asset_name="xCAL-Setup-0.5.5.exe",
            sha256=hashlib.sha256(content).hexdigest(),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
                "utils.updater.tempfile.gettempdir", return_value=directory):
            path = updater.download_installer(info)

            self.assertEqual(path.read_bytes(), content)

    def test_download_installer_rejects_non_release_url(self):
        info = updater.UpdateInfo(
            current_version="0.5.4",
            latest_version="0.5.5",
            update_available=True,
            download_url="https://example.com/xCAL-Setup-0.5.5.exe",
            asset_name="xCAL-Setup-0.5.5.exe",
        )

        with self.assertRaisesRegex(ValueError, "official xCAL release"):
            updater.download_installer(info)

    @patch("utils.updater.db.fetch_app_doc")
    @patch("utils.updater.requests.get")
    def test_download_installer_reassembles_shared_chunks(self, get, fetch_doc):
        import base64
        chunks = [b"private GitHub ", b"release bridge"]
        content = b"".join(chunks)
        documents = {
            f"app_update_chunk_{index:03d}": {
                "version": "0.5.6",
                "index": index,
                "content": base64.b64encode(chunk).decode("ascii"),
            }
            for index, chunk in enumerate(chunks)
        }
        fetch_doc.side_effect = lambda key: documents[key]
        info = updater.UpdateInfo(
            current_version="0.5.5",
            latest_version="0.5.6",
            update_available=True,
            download_url=(
                "https://github.com/loganbateman/xCAL-community/releases/download/"
                "v0.5.6/xCAL-Setup-0.5.6.exe"),
            asset_name="xCAL-Setup-0.5.6.exe",
            sha256=hashlib.sha256(content).hexdigest(),
            chunk_keys=tuple(documents),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
                "utils.updater.tempfile.gettempdir", return_value=directory):
            path = updater.download_installer(info)

            self.assertEqual(path.read_bytes(), content)
        get.assert_not_called()


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


if __name__ == "__main__":
    unittest.main()
