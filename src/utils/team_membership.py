"""Owner-invited teams backed by actual Drive permissions, not secret join codes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from googleapiclient.errors import HttpError

import config
import database.client as db
from database.drive_store import DriveStore
from database.drive_transport import DriveJournal, JOURNAL_NAME
from utils import google_drive_client as drive
from utils.atomic_files import atomic_write_text

TEAM_DOC = "team_profile"
TEAM_CACHE_PATH = config.USER_DATA_DIR / "team_accounts.json"


def cached_teams(account_email):
    """Return non-authoritative team shortcuts for one Google account."""
    try:
        payload = json.loads(TEAM_CACHE_PATH.read_text(encoding="utf-8"))
        rows = payload.get("accounts", {}).get(account_email.casefold(), [])
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        return []
    teams = []
    for row in rows:
        profile, folder = row.get("profile", {}), row.get("folder", {})
        if (isinstance(profile.get("id"), str)
                and isinstance(profile.get("name"), str)
                and re.fullmatch(r"[A-Za-z0-9_-]+", folder.get("id", ""))):
            teams.append({"profile": profile, "folder": folder,
                          "account": account_email.casefold(), "verified": False})
    return teams


def remember_teams(account_email, teams):
    """Cache verified team labels; this cache never grants Drive access."""
    try:
        payload = json.loads(TEAM_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {"schema": 1, "accounts": {}}
    accounts = payload.setdefault("accounts", {})
    accounts[account_email.casefold()] = [
        {"profile": item["profile"], "folder": {
            "id": item["folder"]["id"],
            "name": item["folder"].get("name", "Google Drive folder")}}
        for item in teams]
    TEAM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(TEAM_CACHE_PATH, json.dumps(payload, ensure_ascii=False))


def validate_team_shortcut(team):
    """Revalidate cached Drive access without replaying the full journal."""
    folder_id = team.get("folder", {}).get("id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("This saved team shortcut is invalid.")
    folder = drive.folder_details(folder_id)
    if not folder.get("capabilities", {}).get("canAddChildren"):
        raise PermissionError(
            "This Google account no longer has Editor access to that team.")
    service = drive._worker_service()
    matches = service.files().list(
        q=(f"'{folder_id}' in parents and name = '{JOURNAL_NAME}' and "
           f"mimeType = '{drive.FOLDER_MIME}' and trashed = false"),
        fields="files(id)", pageSize=2, supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute().get("files", [])
    if len(matches) != 1:
        raise ValueError("This folder is not an initialized xCAL team.")
    return {"profile": team["profile"], "folder": folder,
            "account": team["account"], "verified": True}


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
    return {"email": email, "team": profile["name"]}


def discover_accessible_teams():
    """Find editable xCAL teams visible to the signed-in Google account.

    Discovery is read-only. Named Google Drive permission is the sole
    membership proof, so there is no separate xCAL invitation code.
    """
    user = drive.current_user(interactive=False, refresh=True, allow_cached=False)
    if not user.verified:
        raise PermissionError("Sign in to Google before looking for your teams.")
    service = drive._worker_service()
    journals, token = [], None
    while True:
        response = service.files().list(
            q=(f"name = '{JOURNAL_NAME}' and mimeType = '{drive.FOLDER_MIME}' "
               "and trashed = false"),
            spaces="drive", corpora="user", pageSize=1000, pageToken=token,
            fields="nextPageToken,files(id,parents)", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        journals.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            break

    teams, seen = [], set()
    for journal in journals:
        for folder_id in journal.get("parents", []):
            if folder_id in seen or not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
                continue
            seen.add(folder_id)
            try:
                folder = drive.folder_details(folder_id)
                if not folder.get("capabilities", {}).get("canAddChildren"):
                    continue
                with TemporaryDirectory(prefix="xcal-team-login-") as temporary:
                    store = DriveStore(
                        Path(temporary) / "preview.sqlite3",
                        DriveJournal(folder_id))
                    store.sync(read_only=True)
                    rows = (store.table("app_docs").select("data")
                            .eq("key", TEAM_DOC).execute().data)
                profile = rows[0]["data"] if rows else None
                if (not profile or profile.get("folder_id") != folder_id
                        or not profile.get("id") or not profile.get("name")):
                    continue
                teams.append({"profile": profile, "folder": folder,
                              "account": user.email, "verified": True})
            except (HttpError, OSError, RuntimeError, ValueError, PermissionError):
                # A stale or unrelated folder must not block valid teams.
                continue
    teams = sorted(teams, key=lambda item: (
        item["profile"]["name"].casefold(),
        item["folder"].get("name", "").casefold(),
        item["folder"]["id"]))
    remember_teams(user.email, teams)
    return teams
