from unittest.mock import Mock

import pytest

from utils import team_membership as team
from utils.google_drive_client import GoogleUser


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(team.drive, "root_folder_id", lambda: "test-folder")
    monkeypatch.setattr(team.drive, "current_user", lambda **_kwargs: GoogleUser("Owner", "owner@example.test"))
    monkeypatch.setattr(team.drive, "folder_details", lambda _id: {
        "id": "test-folder", "name": "Test", "owners": [{"emailAddress": "owner@example.test"}],
        "capabilities": {"canShare": True, "canAddChildren": True}})


def profile():
    return {"schema": 1, "id": "team-id", "folder_id": "test-folder", "name": "Test team"}


def test_verified_team_profiles_are_cached_per_google_account(monkeypatch, tmp_path):
    monkeypatch.setattr(team, "TEAM_CACHE_PATH", tmp_path / "team_accounts.json")
    entry = {"profile": profile(),
             "folder": {"id": "test-folder", "name": "Team Drive"},
             "account": "owner@example.test", "verified": True}
    team.remember_teams("OWNER@example.test", [entry])
    cached = team.cached_teams("owner@example.test")
    assert cached[0]["profile"] == profile()
    assert cached[0]["folder"]["name"] == "Team Drive"
    assert cached[0]["verified"] is False
    assert team.cached_teams("other@example.test") == []


def test_cached_team_requires_live_editor_access(monkeypatch):
    entry = {"profile": profile(),
             "folder": {"id": "test-folder", "name": "Team Drive"},
             "account": "owner@example.test", "verified": False}
    monkeypatch.setattr(team.drive, "folder_details", lambda _folder: {
        "id": "test-folder", "name": "Team Drive",
        "capabilities": {"canAddChildren": True}})
    service = Mock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "journal"}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    verified = team.validate_team_shortcut(entry)
    assert verified["verified"] is True
    assert verified["folder"]["id"] == "test-folder"


def test_existing_editor_who_is_not_owner_cannot_invite(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "current_user", lambda **_kwargs: GoogleUser("Editor", "editor@example.test"))
    monkeypatch.setattr(team.drive, "folder_details", lambda _id: {"capabilities": {"canShare": False}})
    service = Mock()
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    with pytest.raises(PermissionError, match="permission"):
        team.invite_member("new@example.test")
    service.permissions.assert_not_called()


def test_drive_sharing_capability_required_even_for_owner(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "folder_details", lambda _id: {"id": "test-folder", "capabilities": {"canShare": False}})
    with pytest.raises(PermissionError, match="permission"):
        team.invite_member("new@example.test")


def test_owner_invite_grants_named_account_writer_access_only(owner, monkeypatch):
    service = Mock()
    service.permissions.return_value.list.return_value.execute.return_value = {"permissions": []}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda _key: profile())
    invitation = team.invite_member("NEW@example.test")
    service.permissions.return_value.create.assert_called_once_with(
        fileId="test-folder", body={"type": "user", "role": "writer", "emailAddress": "new@example.test"},
        sendNotificationEmail=True, supportsAllDrives=True, fields="id")
    assert invitation == {"email": "new@example.test", "team": "Test team"}


def test_repeated_invite_does_not_create_duplicate_permissions(owner, monkeypatch):
    service = Mock()
    service.permissions.return_value.list.return_value.execute.return_value = {"permissions": [
        {"id": "member", "type": "user", "emailAddress": "member@example.test", "role": "writer"}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda _key: profile())
    team.invite_member("member@example.test")
    service.permissions.return_value.create.assert_not_called()
    service.permissions.return_value.update.assert_not_called()


def test_owner_creates_one_profile_only(owner, monkeypatch):
    store = Mock()
    write = Mock()
    monkeypatch.setattr(team.db, "get_client", lambda: store)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda _key: None)
    monkeypatch.setattr(team.db, "save_app_doc", write)
    created = team.create_team("Our beta team")
    assert created["name"] == "Our beta team"
    assert created["created_by"] == "owner@example.test"
    assert store.sync.call_count == 2
    write.assert_called_once_with(team.TEAM_DOC, created)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda _key: created)
    with pytest.raises(ValueError, match="already"):
        team.create_team("Duplicate")


def test_existing_viewer_invitation_upgrades_only_named_permission(owner, monkeypatch):
    service = Mock()
    service.permissions.return_value.list.return_value.execute.return_value = {"permissions": [
        {"id": "viewer", "type": "user", "emailAddress": "friend@example.test", "role": "reader"}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda key: profile())
    result = team.invite_member("friend@example.test")
    service.permissions.return_value.update.assert_called_once_with(
        fileId="test-folder", permissionId="viewer", body={"role": "writer"}, supportsAllDrives=True)
    service.permissions.return_value.create.assert_not_called()
    assert result["email"] == "friend@example.test"


def test_permission_pagination_does_not_duplicate_existing_member(owner, monkeypatch):
    service = Mock()
    service.permissions.return_value.list.return_value.execute.side_effect = [
        {"permissions": [], "nextPageToken": "next"},
        {"permissions": [{"id": "friend", "type": "user", "emailAddress": "friend@example.test", "role": "writer"}]}]
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda key: profile())
    team.invite_member("friend@example.test")
    assert service.permissions.return_value.list.call_count == 2
    service.permissions.return_value.create.assert_not_called()


def test_signed_in_editor_discovers_existing_team_without_writes(owner, monkeypatch):
    from database.drive_store import DriveStore
    from utils.google_drive_client import GoogleUser
    monkeypatch.setattr(team.drive, "current_user", lambda **kwargs: GoogleUser("Friend", "friend@example.test"))
    service = Mock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "journal", "parents": ["test-folder"]}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    journal = Mock()
    journal.read_unknown.return_value = []
    def populated_store(path, transport):
        store = DriveStore(path, transport)
        store.table("app_docs").upsert({"key": team.TEAM_DOC, "data": profile()}, on_conflict="key").execute()
        # Represents a previously published team profile, not a pending local write.
        with store.connection() as connection:
            connection.execute("UPDATE events SET uploaded=1")
        return store
    monkeypatch.setattr(team, "DriveStore", populated_store)
    monkeypatch.setattr(team, "DriveJournal", lambda folder: journal)
    result = team.discover_accessible_teams()
    assert result[0]["account"] == "friend@example.test"
    assert result[0]["profile"]["id"] == "team-id"
    journal.append.assert_not_called()


def test_discovery_ignores_team_profile_for_a_different_folder(owner, monkeypatch):
    service = Mock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "journal", "parents": ["test-folder"]}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    store = Mock()
    store.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"data": dict(profile(), id="different-team")}]
    store.table.return_value.select.return_value.eq.return_value.execute.return_value.data[0]["data"]["folder_id"] = "other-folder"
    monkeypatch.setattr(team, "DriveStore", lambda *args: store)
    monkeypatch.setattr(team, "DriveJournal", Mock())
    assert team.discover_accessible_teams() == []


def test_access_list_is_read_only_paginated_and_excludes_deleted_expired(monkeypatch):
    service = Mock()
    read = Mock(return_value=service)
    monkeypatch.setattr(team.drive, "_service", read)
    service.permissions.return_value.list.return_value.execute.side_effect = [
        {"nextPageToken": "page2", "permissions": [
            {"id": "editor", "type": "user", "displayName": "Friend", "emailAddress": "friend@example.test", "role": "writer"},
            {"id": "removed", "type": "user", "role": "writer", "deleted": True},
            {"id": "expired", "type": "user", "role": "reader", "expirationTime": "2000-01-01T00:00:00Z"}]},
        {"permissions": [
            {"id": "owner", "type": "user", "emailAddress": "owner@example.test", "role": "owner"},
            {"id": "group", "type": "group", "emailAddress": "team@example.test", "role": "reader",
             "permissionDetails": [{"inherited": True}]},
            {"id": "link", "type": "anyone", "role": "reader"}]}]
    result = team.list_team_access("test-folder")
    assert [row["id"] for row in result] == ["owner", "editor", "link", "group"]
    assert next(row for row in result if row["id"] == "group")["inherited"]
    read.assert_called_once_with(interactive=False)
    assert service.permissions.return_value.list.call_args.kwargs["pageToken"] == "page2"
    service.permissions.return_value.create.assert_not_called()
    service.permissions.return_value.update.assert_not_called()
    service.permissions.return_value.delete.assert_not_called()


def test_access_list_does_not_require_owner_privileges(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "current_user", lambda **kwargs: GoogleUser("Editor", "editor@example.test"))
    service = Mock()
    service.permissions.return_value.list.return_value.execute.return_value = {"permissions": []}
    monkeypatch.setattr(team.drive, "_service", Mock(return_value=service))
    assert team.list_team_access("test-folder") == []


def test_access_list_rejects_invalid_folder_before_network(monkeypatch):
    service = Mock()
    monkeypatch.setattr(team.drive, "_service", service)
    with pytest.raises(ValueError):
        team.list_team_access("bad/folder")
    service.assert_not_called()
