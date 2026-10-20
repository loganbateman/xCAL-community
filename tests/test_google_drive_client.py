from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from googleapiclient.errors import HttpError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import config
from utils import google_drive_client


class GoogleDriveClientTests(unittest.TestCase):
    def setUp(self):
        self.original_folder_id = config.GOOGLE_DRIVE_FOLDER_ID
        config.GOOGLE_DRIVE_FOLDER_ID = "shared-folder"
        self.original_identity_path = google_drive_client.IDENTITY_PATH
        self.original_user = google_drive_client._current_user
        self.identity_temp = tempfile.TemporaryDirectory()
        google_drive_client.IDENTITY_PATH = (
            Path(self.identity_temp.name) / "google_identity.json")
        google_drive_client._current_user = None

    def tearDown(self):
        config.GOOGLE_DRIVE_FOLDER_ID = self.original_folder_id
        google_drive_client.IDENTITY_PATH = self.original_identity_path
        google_drive_client._current_user = self.original_user
        self.identity_temp.cleanup()

    @patch("utils.google_drive_client._service")
    def test_current_user_comes_from_google_and_is_cached(self, service):
        execute = service.return_value.about.return_value.get.return_value.execute
        execute.return_value = {"user": {
            "displayName": "Alex Racer",
            "emailAddress": "Alex@Example.edu",
        }}

        user = google_drive_client.current_user(refresh=True)

        self.assertEqual(user.name, "Alex Racer")
        self.assertEqual(user.email, "alex@example.edu")
        self.assertTrue(user.verified)
        self.assertEqual(user.audit_label, "Alex Racer <alex@example.edu>")
        cached = json.loads(
            google_drive_client.IDENTITY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cached, {
            "name": "Alex Racer", "email": "alex@example.edu"})
        service.return_value.about.return_value.get.assert_called_once_with(
            fields="user(displayName,emailAddress)")

    @patch("utils.google_drive_client._service")
    def test_offline_identity_uses_last_google_verified_profile(self, service):
        google_drive_client.IDENTITY_PATH.write_text(json.dumps({
            "name": "Alex Racer", "email": "alex@example.edu",
        }), encoding="utf-8")
        service.side_effect = google_drive_client.GoogleDriveError("offline")

        user = google_drive_client.current_user(refresh=True)

        self.assertEqual(user.audit_label, "Alex Racer <alex@example.edu>")
        self.assertFalse(user.verified)

    @patch("utils.google_drive_client._service")
    def test_socket_failure_uses_last_google_verified_profile(self, service):
        google_drive_client.IDENTITY_PATH.write_text(json.dumps({
            "name": "Alex Racer", "email": "alex@example.edu",
        }), encoding="utf-8")
        service.side_effect = OSError("network unreachable")

        user = google_drive_client.current_user(refresh=True)

        self.assertEqual(user.audit_label, "Alex Racer <alex@example.edu>")
        self.assertFalse(user.verified)

    @patch("utils.google_drive_client._service")
    def test_offline_identity_refuses_unattributed_changes_without_cache(
            self, service):
        service.side_effect = google_drive_client.GoogleDriveError("offline")

        with self.assertRaisesRegex(
                google_drive_client.GoogleDriveError, "offline"):
            google_drive_client.current_user(refresh=True)

    @patch("utils.google_drive_client._service")
    def test_disabled_drive_api_has_short_actionable_error(self, service):
        response = Mock(status=403, reason="Forbidden")
        service.return_value.about.return_value.get.return_value.execute.side_effect = HttpError(
            response,
            b'{"error":{"message":"Google Drive API has not been used in project 123 before or it is disabled."}}',
        )

        with self.assertRaisesRegex(
                google_drive_client.GoogleDriveError,
                "sign-in succeeded.*Drive API is not enabled"):
            google_drive_client.current_user(
                interactive=True, refresh=True, allow_cached=False)

    @patch("utils.google_drive_client.current_user")
    def test_noncritical_audit_lookup_never_blocks_local_save(self, current_user):
        current_user.side_effect = RuntimeError(
            "Unable to find the server at www.googleapis.com")

        user = google_drive_client.audit_user_or_none(refresh=True)

        self.assertIsNone(user)

    @patch("utils.google_drive_client._service")
    @patch("utils.google_drive_client.MediaFileUpload")
    def test_upload_targets_configured_folder(self, media_upload, service):
        execute = (
            service.return_value.files.return_value.create.return_value.execute
        )
        execute.return_value = {"id": "drive-file-id"}

        file_id = google_drive_client.upload_file("practice.xrk")

        self.assertEqual(file_id, "drive-file-id")
        service.return_value.files.return_value.create.assert_called_once_with(
            body={"name": "practice.xrk", "parents": ["shared-folder"]},
            media_body=media_upload.return_value,
            fields="id",
            supportsAllDrives=True,
        )

    @patch("utils.google_drive_client._service")
    @patch("utils.google_drive_client.MediaFileUpload")
    def test_upload_preserves_recording_timestamp_in_drive_metadata(
            self, media_upload, service):
        execute = (
            service.return_value.files.return_value.create.return_value.execute
        )
        execute.return_value = {"id": "drive-file-id"}

        google_drive_client.upload_file(
            "camera.mp4", recorded_at="2026-10-09T15:42:07+00:00",
            timestamp_source="Video metadata")

        service.return_value.files.return_value.create.assert_called_once_with(
            body={
                "name": "camera.mp4",
                "parents": ["shared-folder"],
                "appProperties": {
                    "xcalRecordedAt": "2026-10-09T15:42:07+00:00",
                    "xcalTimestampSource": "Video metadata",
                },
            },
            media_body=media_upload.return_value,
            fields="id",
            supportsAllDrives=True,
        )

    @patch("utils.google_drive_client._service")
    @patch("utils.google_drive_client.MediaFileUpload")
    def test_offline_upload_retry_reuses_existing_drive_file(
            self, _media_upload, service):
        files = service.return_value.files.return_value
        files.list.return_value.execute.return_value = {
            "files": [{"id": "already-uploaded"}]}

        file_id = google_drive_client.upload_file(
            "practice.xrk", "session-folder",
            idempotency_key="local-abc123")

        self.assertEqual(file_id, "already-uploaded")
        files.create.assert_not_called()
        files.list.assert_called_once()

    @patch("utils.google_drive_client._service")
    @patch("utils.google_drive_client.MediaFileUpload")
    def test_resumable_upload_reports_progress(self, _media_upload, service):
        request = service.return_value.files.return_value.create.return_value
        status = Mock()
        status.progress.return_value = 0.42
        request.next_chunk.side_effect = [
            (status, None), (None, {"id": "drive-file-id"})]
        progress = []

        file_id = google_drive_client.upload_file(
            "practice.xrk", progress=progress.append)

        self.assertEqual(file_id, "drive-file-id")
        self.assertEqual(progress, [0, 42, 100])

    @patch("utils.google_drive_client._service")
    def test_trash_uses_recoverable_drive_operation(self, service):
        google_drive_client.trash_file("drive-file-id")

        service.return_value.files.return_value.update.assert_called_once_with(
            fileId="drive-file-id",
            body={"trashed": True},
            supportsAllDrives=True,
        )

    def test_connection_requires_folder_configuration(self):
        config.GOOGLE_DRIVE_FOLDER_ID = ""
        self.assertFalse(google_drive_client.check_connection())

    @patch("utils.google_drive_client._service")
    @patch("utils.google_drive_client.MediaIoBaseDownload")
    def test_download_reports_chunk_progress(self, media_download, service):
        class Status:
            def __init__(self, value):
                self._value = value

            def progress(self):
                return self._value

        media_download.return_value.next_chunk.side_effect = [
            (Status(0.25), False),
            (Status(1.0), True),
        ]
        updates = []
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            google_drive_client.download_file(
                "video-id", destination, updates.append)

        self.assertEqual(updates, [0, 25, 100])
        media_download.assert_called_once_with(
            ANY,
            service.return_value.files.return_value.get_media.return_value,
            chunksize=8 * 1024 * 1024,
        )

    def test_video_detection_supports_smartycam_and_gopro_formats(self):
        mp4 = google_drive_client.DriveNode(
            id="mp4", name="SmartyCam.MP4", is_folder=False)
        mov = google_drive_client.DriveNode(
            id="mov", name="GoPro Session.mov", is_folder=False)
        note = google_drive_client.DriveNode(
            id="note", name="notes.txt", is_folder=False)

        self.assertTrue(mp4.is_video)
        self.assertTrue(mov.is_video)
        self.assertFalse(note.is_video)

    def test_data_detection_supports_tire_merge_package(self):
        names = [
            "run.xrk", "Tire Data.ibt", "Tire Data.merged.csv",
            "Tire Data.manifest.json", "aim-source.csv",
        ]

        self.assertTrue(all(
            google_drive_client.DriveNode(
                id=name, name=name, is_folder=False).is_telemetry
            for name in names))
        self.assertFalse(google_drive_client.DriveNode(
            id="video", name="camera.mp4", is_folder=False).is_telemetry)

    @patch("utils.google_drive_client.list_children")
    def test_session_walk_reuses_dated_listing_and_collects_nested_xrks(self, list_children):
        folder = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=True)
        xrk = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=False, size=100)
        tree = {
            "shared-folder": [folder("season", "2026-2027 Season")],
            "season": [folder("session", "2026-08-24 Race")],
            "session": [
                xrk("direct", "direct.xrk"),
                folder("driver", "Driver A"),
            ],
            "driver": [xrk("nested", "nested.xrk")],
        }
        list_children.side_effect = lambda folder_id: tree.get(folder_id, [])

        sessions = google_drive_client.walk_session_folders()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "2026-08-24 Race")
        self.assertEqual(
            {node.name for node in sessions[0].xrks},
            {"direct.xrk", "nested.xrk"},
        )
        called_ids = [call.args[0] for call in list_children.call_args_list]
        self.assertEqual(called_ids.count("session"), 1)
        self.assertCountEqual(
            called_ids, ["shared-folder", "season", "session", "driver"])

    @patch("utils.google_drive_client.IGNORED_LEGACY_SESSION_FOLDER_IDS", {"legacy-test-folder"})
    @patch("utils.google_drive_client.list_children")
    def test_session_walk_hides_migrated_legacy_container_shells(
            self, list_children):
        legacy_id = next(iter(
            google_drive_client.IGNORED_LEGACY_SESSION_FOLDER_IDS))
        list_children.side_effect = lambda folder_id: {
            "shared-folder": [google_drive_client.DriveNode(
                id="season", name="2024-25 Testing", is_folder=True)],
            "season": [
                google_drive_client.DriveNode(
                    id=legacy_id, name="Legacy Track", is_folder=True),
                google_drive_client.DriveNode(
                    id="session", name="2025-04-12 Practice", is_folder=True),
            ],
            "session": [google_drive_client.DriveNode(
                id="xrk", name="run.xrk", is_folder=False)],
        }.get(folder_id, [])

        sessions = google_drive_client.walk_session_folders()

        self.assertEqual([session.id for session in sessions], ["session"])
        self.assertNotIn(
            legacy_id,
            [call.args[0] for call in list_children.call_args_list])

    @patch("utils.google_drive_client.list_children")
    def test_video_walk_finds_loose_and_nested_files(self, list_children):
        folder = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=True)
        file_node = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=False, size=100)
        tree = {
            "session": [
                file_node("direct", "SmartyCam.mp4"),
                folder("camera", "GoPro"),
                file_node("xrk", "run.xrk"),
            ],
            "camera": [file_node("nested", "onboard.MOV")],
        }
        list_children.side_effect = lambda folder_id: tree.get(folder_id, [])

        videos = google_drive_client.list_videos_recursive("session")

        self.assertEqual(
            {video.name for video in videos},
            {"SmartyCam.mp4", "onboard.MOV"})

    @patch("utils.google_drive_client.list_children")
    def test_combined_media_walk_lists_each_folder_once(self, list_children):
        folder = google_drive_client.DriveNode(
            id="nested", name="Driver", is_folder=True)
        list_children.side_effect = lambda folder_id: {
            "session": [
                folder,
                google_drive_client.DriveNode(
                    id="xrk", name="run.xrk", is_folder=False),
                google_drive_client.DriveNode(
                    id="ibt", name="Tire Data.ibt", is_folder=False),
            ],
            "nested": [
                google_drive_client.DriveNode(
                    id="video", name="run.mp4", is_folder=False),
            ],
        }.get(folder_id, [])

        xrks, videos = google_drive_client.list_session_media_recursive("session")

        self.assertEqual(
            [node.name for node in xrks], ["run.xrk", "Tire Data.ibt"])
        self.assertEqual([node.name for node in videos], ["run.mp4"])
        self.assertEqual(
            [call.args[0] for call in list_children.call_args_list],
            ["session", "nested"])

    @patch("utils.google_drive_client.list_children")
    def test_offline_file_walk_preserves_relative_folders(self, list_children):
        folder = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=True)
        file_node = lambda node_id, name: google_drive_client.DriveNode(
            id=node_id, name=name, is_folder=False, size=100)
        list_children.side_effect = lambda folder_id: {
            "session": [
                file_node("notes", "notes.txt"),
                folder("driver", "Kart 11"),
            ],
            "driver": [file_node("xrk", "race.xrk")],
        }.get(folder_id, [])

        files = google_drive_client.list_files_recursive("session")

        self.assertEqual(
            {node.relative_path for node in files},
            {"notes.txt", "Kart 11/race.xrk"})

    @patch("utils.google_drive_client.find_child_folder", return_value=None)
    @patch("utils.google_drive_client.ensure_child_folder",
           return_value="new-season-id")
    @patch("utils.google_drive_client._service")
    def test_session_folder_update_renames_and_moves_without_changing_id(
            self, service, ensure_child, find_child):
        files = service.return_value.files.return_value
        files.get.return_value.execute.return_value = {
            "name": "Old Session", "parents": ["old-season-id"]}

        google_drive_client.update_session_folder(
            "session-id", "2026-10-12 New Name", "2026-2027 Season")

        ensure_child.assert_called_once_with(
            "shared-folder", "2026-2027 Season")
        find_child.assert_called_once_with(
            "new-season-id", "2026-10-12 New Name")
        files.update.assert_called_once_with(
            fileId="session-id",
            body={"name": "2026-10-12 New Name"},
            fields="id,name,parents",
            supportsAllDrives=True,
            addParents="new-season-id",
            removeParents="old-season-id",
        )


if __name__ == "__main__":
    unittest.main()
