"""Owner-invited teams backed by actual Drive permissions, not secret join codes."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

import config
import database.client as db
from database.drive_store import DriveStore
from database.drive_transport import DriveJournal
from utils import google_drive_client as drive

TEAM_DOC = "team_profile"
PREFIX = "xcal-team-v1:"


def list_team_access(folder_id=None):
    """Read the folder ACL, not online presence or individual group membership.

    Works for members allowed to read sharing details; no ownership or permission
    mutation is required. Missing auth must not open a browser on tab selection.
    """
    folder_id = folder_id or drive.root_folder_id()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("Invalid team Drive folder.")
    service = drive._service(interactive=False)
    entries, token = {}, None
    now = datetime.now(timezone.utc)
    while True:
        response = service.permissions().list(
            fileId=folder_id, pageSize=100, pageToken=token,
            fields="nextPageToken,permissions(id,type,displayName,emailAddress,domain,role,deleted,expirationTime,pendingOwner,permissionDetails(inherited))",
            supportsAllDrives=True).execute()
        for permission in response.get("permissions", []):
            if permission.get("deleted"):
                continue
            expiration = permission.get("expirationTime")
            if expiration:
                try:
                    if datetime.fromisoformat(expiration.replace("Z", "+00:00")) <= now:
                        continue
                except (ValueError, TypeError):
                    pass
            kind = permission.get("type", "unknown")
            email = permission.get("emailAddress") or ""
            name = permission.get("displayName") or email or permission.get("domain") or "Identity unavailable"
            if kind == "anyone":
                name = "Anyone with access to this link"
            entries[permission["id"]] = {
                "id": permission["id"], "name": name, "email": email,
                "role": permission.get("role", "unknown"), "type": kind,
                "pending_owner": bool(permission.get("pendingOwner")),
                "inherited": any(detail.get("inherited") for detail in permission.get("permissionDetails", [])),
            }
        token = response.get("nextPageToken")
        if not token:
            break
    order = {"owner": 0, "organizer": 1, "fileOrganizer": 2, "writer": 3, "commenter": 4, "reader": 5}
    return sorted(entries.values(), key=lambda entry: (order.get(entry["role"], 6), entry["name"].casefold(), entry["email"].casefold()))


def _owner_context(folder_id=None):
    folder_id = folder_id or drive.root_folder_id()
    user = drive.current_user(interactive=False, refresh=True, allow_cached=False)
    if not user.verified:
        raise PermissionError("Google sign-in must be verified online before managing invitations.")
    folder = drive.folder_details(folder_id)
    owners = {owner.get("emailAddress", "").casefold() for owner in folder.get("owners", [])}
    if not folder.get("capabilities", {}).get("canShare"):
        raise PermissionError("Your Google account cannot invite members to this Drive folder.")
    if owners and user.email.casefold() not in owners:
        raise PermissionError("Only the Drive folder owner can manage xCAL invitations.")
    if not owners and not folder.get("driveId"):
        raise PermissionError("The Drive folder owner could not be verified.")
    return folder_id, user, folder


def create_team(name):
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Enter a team name of 1–80 characters.")
    folder_id, user, _folder = _owner_context()
    db.get_client().sync()
    existing = db.fetch_app_doc(TEAM_DOC)
    if existing:
        raise ValueError("This folder already has an xCAL team. Use its existing team profile.")
    profile = {"schema": 1, "id": str(uuid4()), "name": name, "folder_id": folder_id,
               "created_by": user.email, "created_at": datetime.now(timezone.utc).isoformat(),
               "membership_policy": "google_drive_permissions"}
    db.save_app_doc(TEAM_DOC, profile)
    db.get_client().sync()
    return profile


def create_team_in_folder(folder_id, name):
    """Create from onboarding before switching the active local workspace."""
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Enter a team name of 1–80 characters.")
    folder_id, user, _folder = _owner_context(folder_id)
    store = DriveStore(config.workspace_dir(folder_id) / "catalog.sqlite3", DriveJournal(folder_id))
    store.sync()
    rows = store.table("app_docs").select("data").eq("key", TEAM_DOC).execute().data
    if rows:
        existing = rows[0]["data"]
        if existing.get("name") == name and existing.get("folder_id") == folder_id:
            return existing
        raise ValueError("This folder already belongs to another xCAL team. Choose a different test folder or use Join.")
    profile = {"schema": 1, "id": str(uuid4()), "name": name, "folder_id": folder_id,
               "created_by": user.email, "created_at": datetime.now(timezone.utc).isoformat(),
               "membership_policy": "google_drive_permissions"}
    store.table("app_docs").upsert({"key": TEAM_DOC, "data": profile, "updated_by": user.audit_label}, on_conflict="key").execute()
    store.sync()
    return profile


def encode_invite(profile):
    payload = {"schema": 1, "team_id": profile["id"], "folder_id": profile["folder_id"],
               "team_name": profile["name"]}
    return PREFIX + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def decode_invite(code):
    code = (code or "").strip()
    if len(code) > 2048 or not code.startswith(PREFIX):
        raise ValueError("Paste an xCAL team invitation code.")
    try:
        encoded = code[len(PREFIX):]
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("This invitation code is invalid.") from exc
    if (not isinstance(payload, dict) or payload.get("schema") != 1
            or not isinstance(payload.get("folder_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", payload["folder_id"])
            or not isinstance(payload.get("team_id"), str) or not payload["team_id"]):
        raise ValueError("This invitation has invalid team details.")
    return payload


def invite_member(email):
    """Called only after explicit owner confirmation; grants Editor permission."""
    email = (email or "").strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter the teammate's Google account email address.")
    from utils.team_access import authority
    folder_id = authority(require_manage=True)["folder_id"]
    profile = db.fetch_app_doc(TEAM_DOC)
    if not profile or profile.get("folder_id") != folder_id:
        raise ValueError("Create a team profile for this folder first.")
    service = drive._worker_service()
    permissions, token = [], None
    while True:
        response = service.permissions().list(fileId=folder_id, pageSize=100, pageToken=token,
                                               fields="nextPageToken,permissions(id,type,emailAddress,role)",
                                               supportsAllDrives=True).execute()
        permissions.extend(response.get("permissions", []))
        token = response.get("nextPageToken")
        if not token:
            break
    existing = next((item for item in permissions if item.get("type") == "user"
                     and item.get("emailAddress", "").casefold() == email), None)
    if existing:
        if existing.get("role") not in ("owner", "organizer", "fileOrganizer", "writer"):
            service.permissions().update(fileId=folder_id, permissionId=existing["id"],
                                           body={"role": "writer"}, supportsAllDrives=True).execute()
    else:
        service.permissions().create(fileId=folder_id,
                                       body={"type": "user", "role": "writer", "emailAddress": email},
                                       sendNotificationEmail=True, supportsAllDrives=True,
                                       fields="id").execute()
    return {"email": email, "code": encode_invite(profile), "team": profile["name"]}


def validate_join(code):
    """Read-only validation. A forwarded code never grants Drive access."""
    invite = decode_invite(code)
    user = drive.current_user(interactive=False, refresh=True, allow_cached=False)
    folder = drive.folder_details(invite["folder_id"])
    if not folder.get("capabilities", {}).get("canAddChildren"):
        raise PermissionError("Ask the team owner to invite this Google account with Editor access first.")
    with TemporaryDirectory(prefix="xcal-team-join-") as temporary:
        store = DriveStore(Path(temporary) / "preview.sqlite3", DriveJournal(invite["folder_id"]))
        store.sync(read_only=True)  # Never publish generated revisions during join validation.
        rows = store.table("app_docs").select("data").eq("key", TEAM_DOC).execute().data
        profile = rows[0]["data"] if rows else None
    if not profile or profile.get("id") != invite["team_id"] or profile.get("folder_id") != folder["id"]:
        raise ValueError("This invitation does not match the team in that Drive folder.")
    return {"profile": profile, "folder": folder, "account": user.email}
