from unittest.mock import Mock

import pytest

import config
from utils import team_access as access
from utils.google_drive_client import GoogleUser


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setattr(config, "RECOVERY_MODE", False)
    monkeypatch.setattr(access.drive, "root_folder_id", lambda: "folder")
    monkeypatch.setattr(access.drive, "current_user", lambda **kwargs: GoogleUser("Owner", "owner@example.test"))
    folder = {"owners": [{"emailAddress": "owner@example.test"}], "capabilities": {"canShare": True}}
    monkeypatch.setattr(access.drive, "folder_details", lambda _id: folder)
    service = Mock()
    service.permissions.return_value.get.return_value.execute.return_value = {
        "id": "member", "type": "user", "emailAddress": "member@example.test", "role": "writer"}
    monkeypatch.setattr(access.drive, "_worker_service", lambda: service)
    return folder, service


def test_sharing_admin_uses_live_google_capability(google, monkeypatch):
    folder, service = google
    monkeypatch.setattr(access.drive, "current_user", lambda **kwargs: GoogleUser("Admin", "admin@example.test"))
    access.change_member("member", "reader")
    service.permissions.return_value.update.assert_called_once_with(
        fileId="folder", permissionId="member", body={"role": "reader"}, supportsAllDrives=True, fields="id")
    folder["capabilities"]["canShare"] = False
    with pytest.raises(PermissionError):
        access.change_member("member", remove=True)
    service.permissions.return_value.delete.assert_not_called()


@pytest.mark.parametrize("permission", [
    {"role": "owner"}, {"role": "organizer"}, {"type": "group"},
    {"emailAddress": "owner@example.test"}, {"permissionDetails": [{"inherited": True}]},
])
def test_protected_members_cannot_be_demoted_or_removed(google, permission):
    _, service = google
    member = service.permissions.return_value.get.return_value.execute.return_value
    member.update(permission)
    with pytest.raises(PermissionError):
        access.change_member("member", remove=True)
    service.permissions.return_value.delete.assert_not_called()


def test_only_owner_controls_editor_sharing_policy(google, monkeypatch):
    _, service = google
    access.set_editor_admins(True)
    assert service.files.return_value.update.call_args.kwargs["body"] == {"writersCanShare": True}
    service.reset_mock()
    monkeypatch.setattr(access.drive, "current_user", lambda **kwargs: GoogleUser("Admin", "admin@example.test"))
    with pytest.raises(PermissionError):
        access.set_editor_admins(False)
    service.files.assert_not_called()


def test_consumer_ownership_requires_offer_and_recipient_acceptance(google, monkeypatch):
    _, service = google
    access.offer_ownership("member")
    assert service.permissions.return_value.update.call_args.kwargs["body"] == {"role": "writer", "pendingOwner": True}
    with pytest.raises(PermissionError):
        access.accept_ownership("member")
    monkeypatch.setattr(access.drive, "current_user", lambda **kwargs: GoogleUser("Member", "member@example.test"))
    service.permissions.return_value.get.return_value.execute.return_value["pendingOwner"] = True
    access.accept_ownership("member")
    assert service.permissions.return_value.update.call_args.kwargs["transferOwnership"] is True


def test_recovery_mode_never_contacts_google(google, monkeypatch):
    _, service = google
    monkeypatch.setattr(config, "RECOVERY_MODE", True)
    with pytest.raises(PermissionError):
        access.change_member("member", remove=True)
    service.permissions.assert_not_called()
