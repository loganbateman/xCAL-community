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


def test_invite_contains_no_tokens_and_round_trips():
    code = team.encode_invite(profile())
    decoded = team.decode_invite(code)
    assert decoded == {"schema": 1, "team_id": "team-id", "folder_id": "test-folder", "team_name": "Test team"}
    assert "email" not in decoded and "token" not in decoded


@pytest.mark.parametrize("code", ["bad", "xcal-team-v1:%%%%", "xcal-team-v1:" + "a" * 3000])
def test_invalid_invite_rejected_before_google_calls(code, monkeypatch):
    read = Mock()
    monkeypatch.setattr(team.drive, "folder_details", read)
    with pytest.raises(ValueError):
        team.validate_join(code)
    read.assert_not_called()


def test_existing_editor_who_is_not_owner_cannot_invite(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "current_user", lambda **_kwargs: GoogleUser("Editor", "editor@example.test"))
    service = Mock()
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    with pytest.raises(PermissionError, match="owner"):
        team.invite_member("new@example.test")
    service.permissions.assert_not_called()


def test_drive_sharing_capability_required_even_for_owner(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "folder_details", lambda _id: {"id": "test-folder", "capabilities": {"canShare": False}})
    with pytest.raises(PermissionError, match="cannot invite"):
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
    assert team.decode_invite(invitation["code"])["folder_id"] == "test-folder"


def test_repeated_invite_does_not_create_duplicate_permissions(owner, monkeypatch):
    service = Mock()
    service.permissions.return_value.list.return_value.execute.return_value = {"permissions": [
        {"id": "member", "type": "user", "emailAddress": "member@example.test", "role": "writer"}]}
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    monkeypatch.setattr(team.db, "fetch_app_doc", lambda _key: profile())
    team.invite_member("member@example.test")
    service.permissions.return_value.create.assert_not_called()
    service.permissions.return_value.update.assert_not_called()


def test_viewer_join_is_rejected_in_editor_beta(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "folder_details", lambda _id: {"id": "test-folder", "capabilities": {"canAddChildren": False}})
    with pytest.raises(PermissionError, match="Editor"):
        team.validate_join(team.encode_invite(profile()))


def test_forwarded_code_does_not_grant_google_permission(owner, monkeypatch):
    monkeypatch.setattr(team.drive, "folder_details", Mock(side_effect=PermissionError("No Drive access")))
    service = Mock()
    monkeypatch.setattr(team.drive, "_worker_service", lambda: service)
    with pytest.raises(PermissionError, match="No Drive access"):
        team.validate_join(team.encode_invite(profile()))
    service.permissions.assert_not_called()


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


def test_separate_account_joins_existing_team_without_writes(owner, monkeypatch):
    from database.drive_store import DriveStore
    from utils.google_drive_client import GoogleUser
    monkeypatch.setattr(team.drive, "current_user", lambda **kwargs: GoogleUser("Friend", "friend@example.test"))
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
    result = team.validate_join(team.encode_invite(profile()))
    assert result["account"] == "friend@example.test"
    assert result["profile"]["id"] == "team-id"
    journal.append.assert_not_called()


def test_wrong_team_code_is_rejected_even_with_drive_editor_access(owner, monkeypatch):
    store = Mock()
    store.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"data": dict(profile(), id="different-team")}]
    monkeypatch.setattr(team, "DriveStore", lambda *args: store)
    monkeypatch.setattr(team, "DriveJournal", Mock())
    with pytest.raises(ValueError, match="does not match"):
        team.validate_join(team.encode_invite(profile()))
