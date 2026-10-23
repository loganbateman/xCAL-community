"""Team administration follows live Google Drive capabilities, never cached roles."""
import config
from utils import google_drive_client as drive


def authority(require_manage=False, require_owner=False):
    if config.RECOVERY_MODE:
        raise PermissionError("Member management is disabled in offline recovery.")
    folder_id = drive.root_folder_id()
    user = drive.current_user(interactive=False, refresh=True, allow_cached=False)
    if not user.verified:
        raise PermissionError("Verify your Google sign-in online before managing members.")
    folder = drive.folder_details(folder_id)
    owner = any(row.get("emailAddress", "").casefold() == user.email.casefold() for row in folder.get("owners", []))
    manage = bool(folder.get("capabilities", {}).get("canShare"))
    if require_owner and not owner:
        raise PermissionError("Only the current Drive folder owner can change ownership or sharing policy.")
    if require_manage and not manage:
        raise PermissionError("Your Google account does not have permission to manage this folder's sharing.")
    return {"folder_id": folder_id, "email": user.email, "owner": owner, "can_manage": manage,
            "shared_drive": bool(folder.get("driveId")), "writers_can_share": bool(folder.get("writersCanShare")),
            "url": f"https://drive.google.com/drive/folders/{folder_id}"}


def _member(context, permission_id):
    permission = drive._worker_service().permissions().get(
        fileId=context["folder_id"], permissionId=permission_id,
        fields="id,type,emailAddress,role,pendingOwner,permissionDetails(inherited)", supportsAllDrives=True).execute()
    if permission.get("type") != "user":
        raise PermissionError("Manage group, domain and link access in Google Drive.")
    if any(row.get("inherited") for row in permission.get("permissionDetails", [])):
        raise PermissionError("This access is inherited. Change it on the parent folder or shared drive in Google Drive.")
    return permission


def change_member(permission_id, role=None, remove=False):
    if role not in ("writer", "reader", "commenter") and not remove:
        raise ValueError("Choose Editor, Viewer or Commenter access.")
    context = authority(require_manage=True)
    permission = _member(context, permission_id)
    if permission.get("role") in ("owner", "organizer"):
        raise PermissionError("The owner or shared-drive manager cannot be removed or demoted here.")
    if permission.get("emailAddress", "").casefold() == context["email"].casefold():
        raise PermissionError("Use Leave team to disconnect this device; manage your own Drive access in Google Drive.")
    service = drive._worker_service().permissions()
    if remove:
        service.delete(fileId=context["folder_id"], permissionId=permission_id, supportsAllDrives=True).execute()
    else:
        service.update(fileId=context["folder_id"], permissionId=permission_id,
                       body={"role": role}, supportsAllDrives=True, fields="id").execute()


def set_editor_admins(enabled):
    context = authority(require_owner=True)
    if context["shared_drive"]:
        raise PermissionError("Shared-drive administrators are managed in Google Drive.")
    drive._worker_service().files().update(fileId=context["folder_id"],
        body={"writersCanShare": bool(enabled)}, fields="id,writersCanShare", supportsAllDrives=True).execute()


def offer_ownership(permission_id):
    context = authority(require_owner=True, require_manage=True)
    permission = _member(context, permission_id)
    if context["shared_drive"] or permission.get("role") == "owner":
        raise PermissionError("This ownership transfer is not supported for this folder.")
    drive._worker_service().permissions().update(fileId=context["folder_id"], permissionId=permission_id,
        body={"role": "writer", "pendingOwner": True}, supportsAllDrives=True, fields="id,pendingOwner").execute()


def accept_ownership(permission_id):
    context = authority()
    permission = _member(context, permission_id)
    if (context["shared_drive"] or not permission.get("pendingOwner")
            or permission.get("emailAddress", "").casefold() != context["email"].casefold()):
        raise PermissionError("Only the invited prospective owner can accept this transfer.")
    drive._worker_service().permissions().update(fileId=context["folder_id"], permissionId=permission_id,
        body={"role": "owner"}, transferOwnership=True, supportsAllDrives=True, fields="id,role").execute()
