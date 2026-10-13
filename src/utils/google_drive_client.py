"""Authenticated Google Drive storage for XRK files."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import config

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_PATH = config.USER_DATA_DIR / "google_token.json"
IDENTITY_PATH = config.USER_DATA_DIR / "google_identity.json"
_auth_lock = threading.Lock()
_identity_lock = threading.Lock()
_service_local = threading.local()
_current_user: "GoogleUser | None" = None


class GoogleDriveError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleUser:
    """Read-only identity reported by the authenticated Google account."""

    name: str
    email: str
    verified: bool = True

    @property
    def audit_label(self) -> str:
        return f"{self.name} <{self.email}>"


@dataclass(frozen=True)
class DriveNode:
    """A single item (folder or file) inside a Drive folder."""
    id: str
    name: str
    is_folder: bool
    size: int | None = None
    relative_path: str = ""
    local_path: str | None = None
    pending_upload: bool = False
    mime_type: str | None = None
    recorded_at: str | None = None
    timestamp_source: str | None = None

    @property
    def is_xrk(self) -> bool:
        return not self.is_folder and self.name.lower().endswith(".xrk")

    @property
    def is_telemetry(self) -> bool:
        """Return whether this file belongs in a session's Data tab."""
        if self.is_folder:
            return False
        lower = self.name.casefold()
        return (
            lower.endswith((".xrk", ".ibt", ".csv"))
            or lower.endswith(".manifest.json")
        )

    @property
    def is_video(self) -> bool:
        return (
            not self.is_folder
            and Path(self.name).suffix.casefold() in {".mp4", ".mov"})


def _client_file_path() -> Path:
    path = Path(config.GOOGLE_OAUTH_CLIENT_FILE)
    return path if path.is_absolute() else config.APP_ROOT / path


def _load_credentials() -> Credentials | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise GoogleDriveError(f"Could not read Google sign-in token: {exc}") from exc


def _save_credentials(credentials: Credentials) -> None:
    try:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    except OSError as exc:
        raise GoogleDriveError(f"Could not save Google sign-in token: {exc}") from exc


def _credentials(interactive: bool) -> Credentials | None:
    with _auth_lock:
        credentials = _load_credentials()
        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                _save_credentials(credentials)
                return credentials
            except RefreshError:
                TOKEN_PATH.unlink(missing_ok=True)

        if not interactive:
            return None

        client_file = _client_file_path()
        if not client_file.exists():
            raise GoogleDriveError(
                f"Google OAuth client file not found: {client_file}"
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
                authorization_prompt_message="Opening Google sign-in in your browser...",
                success_message="Google Drive connected. You can close this browser tab.",
            )
        except (OSError, ValueError) as exc:
            raise GoogleDriveError(f"Google sign-in failed: {exc}") from exc

        _save_credentials(credentials)
        return credentials


def _service(interactive: bool = True) -> Resource:
    credentials = _credentials(interactive)
    if not credentials:
        raise GoogleDriveError("Google Drive is not signed in.")
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _worker_service() -> Resource:
    """Reuse one Drive client per thread without sharing unsafe HTTP objects."""
    service = getattr(_service_local, "drive", None)
    if service is None:
        service = _service()
        _service_local.drive = service
    return service


def _load_cached_user() -> GoogleUser | None:
    if not IDENTITY_PATH.exists():
        return None
    try:
        payload = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
        name = str(payload.get("name") or "").strip()
        email = str(payload.get("email") or "").strip().lower()
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return GoogleUser(name, email, verified=False) if name and email else None


def _save_cached_user(user: GoogleUser) -> None:
    try:
        IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_PATH.write_text(json.dumps({
            "name": user.name,
            "email": user.email,
        }), encoding="utf-8")
    except OSError as exc:
        raise GoogleDriveError(
            f"Could not save Google editor identity: {exc}") from exc


def cached_user() -> GoogleUser | None:
    """Return the last Google-verified identity for offline attribution."""
    global _current_user
    with _identity_lock:
        if _current_user is None:
            _current_user = _load_cached_user()
        return _current_user


def current_user(
        interactive: bool = False, refresh: bool = False,
        allow_cached: bool = True) -> GoogleUser:
    """Return the Google profile tied to the current Drive credentials.

    A successfully verified profile is cached in the per-user data directory so
    offline setup edits retain the same locked attribution. The cache is never
    editable through xCAL and is cleared when the Google account disconnects.
    """
    global _current_user
    with _identity_lock:
        if _current_user is not None and not refresh:
            return _current_user

    try:
        response = (_service(interactive=interactive).about()
                    .get(fields="user(displayName,emailAddress)")
                    .execute())
        payload = response.get("user") or {}
        name = str(payload.get("displayName") or "").strip()
        email = str(payload.get("emailAddress") or "").strip().lower()
        if not name or not email:
            raise GoogleDriveError(
                "Google did not return a profile name and email for this account.")
        user = GoogleUser(name, email, verified=True)
        _save_cached_user(user)
        with _identity_lock:
            _current_user = user
        return user
    except (GoogleDriveError, RefreshError, HttpError, TransportError, OSError):
        if allow_cached:
            user = cached_user()
            if user is not None:
                return user
        raise


def audit_user_or_none(refresh: bool = False) -> GoogleUser | None:
    """Return trusted audit identity without making local saves depend on I/O.

    Google/HTTP clients can surface several different exception types for DNS,
    TLS, token-refresh, and transport failures. Attribution is important, but a
    transient identity lookup must never prevent a durable offline save. A
    previously Google-verified cached profile is preferred; otherwise callers
    can mark the entry pending and resolve it before cloud synchronization.
    """
    try:
        return current_user(
            interactive=False, refresh=refresh, allow_cached=True)
    except Exception:
        return cached_user()


def audit_identity() -> str:
    """Canonical actor text stored in existing audit columns."""
    user = audit_user_or_none()
    if user is not None:
        return user.audit_label
    import getpass
    return f"Local editor: {getpass.getuser()} (not Google verified)"


def authorize() -> GoogleUser:
    return current_user(
        interactive=True, refresh=True, allow_cached=False)


def disconnect() -> None:
    global _current_user
    with _identity_lock:
        _current_user = None
    TOKEN_PATH.unlink(missing_ok=True)
    IDENTITY_PATH.unlink(missing_ok=True)
    if hasattr(_service_local, "drive"):
        delattr(_service_local, "drive")


def check_connection() -> bool:
    if not config.GOOGLE_DRIVE_FOLDER_ID:
        return False
    try:
        service = _service(interactive=False)
        service.files().get(
            fileId=config.GOOGLE_DRIVE_FOLDER_ID,
            fields="id,mimeType",
            supportsAllDrives=True,
        ).execute()
        return True
    except (GoogleDriveError, HttpError, TransportError, OSError):
        return False


def _require_folder_id() -> str:
    if not config.GOOGLE_DRIVE_FOLDER_ID:
        raise GoogleDriveError("Missing GOOGLE_DRIVE_FOLDER_ID in .env")
    return config.GOOGLE_DRIVE_FOLDER_ID


def root_folder_id() -> str:
    """The configured team root folder in Drive."""
    return _require_folder_id()


FOLDER_MIME = "application/vnd.google-apps.folder"

IGNORED_LEGACY_SESSION_FOLDER_IDS = set()

# A session folder starts with a date, e.g. "2026-04-25 (Race 69)".
_SESSION_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")


def list_children(folder_id: str) -> list[DriveNode]:
    """List immediate children (folders + files) of a Drive folder."""
    service = _worker_service()
    nodes: list[DriveNode] = []
    page_token = None
    try:
        while True:
            response = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=("nextPageToken,files(id,name,size,mimeType,createdTime,"
                        "appProperties)"),
                pageSize=1000,
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                orderBy="folder,name",
            ).execute()
            for item in response.get("files", []):
                size = item.get("size")
                properties = item.get("appProperties") or {}
                recorded_at = properties.get("xcalRecordedAt")
                timestamp_source = properties.get("xcalTimestampSource")
                if recorded_at:
                    from utils.media_timestamp import normalize_stored_timestamp
                    normalized = normalize_stored_timestamp(
                        item["name"], recorded_at, timestamp_source)
                    recorded_at = normalized.recorded_at
                    timestamp_source = normalized.source
                if not recorded_at and item.get("createdTime"):
                    recorded_at = item["createdTime"]
                    timestamp_source = "Drive date"
                nodes.append(
                    DriveNode(
                        id=item["id"],
                        name=item["name"],
                        is_folder=item["mimeType"] == FOLDER_MIME,
                        size=int(size) if size else None,
                        mime_type=item.get("mimeType"),
                        recorded_at=recorded_at,
                        timestamp_source=timestamp_source,
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        raise GoogleDriveError(f"Could not list Google Drive folder: {exc}") from exc
    return nodes


def list_files_recursive(folder_id: str, max_depth: int = 8) -> list[DriveNode]:
    """List every file under a session with a stable relative package path."""
    if not folder_id:
        return []
    files: list[DriveNode] = []
    frontier: list[tuple[str, tuple[str, ...]]] = [(folder_id, ())]
    depth = 0
    while frontier and depth <= max_depth:
        next_frontier: list[tuple[str, tuple[str, ...]]] = []
        for parent_id, parent_path in frontier:
            for node in list_children(parent_id):
                relative_parts = parent_path + (node.name,)
                if node.is_folder:
                    next_frontier.append((node.id, relative_parts))
                else:
                    files.append(DriveNode(
                        id=node.id, name=node.name, is_folder=False,
                        size=node.size,
                        relative_path="/".join(relative_parts),
                        mime_type=node.mime_type,
                        recorded_at=node.recorded_at,
                        timestamp_source=node.timestamp_source,
                    ))
        frontier = next_frontier
        depth += 1
    return files


@dataclass(frozen=True)
class SessionFolder:
    id: str
    name: str
    path: str            # e.g. "2025-26 Season"
    season: str          # top-level ancestor name
    xrks: tuple          # tuple of DriveNode (.xrk files)


def walk_session_folders(max_depth: int = 3) -> list[SessionFolder]:
    """Recursively collect leaf/session folders under the team root.

    A "session" is a folder that has no sub-folders (a leaf). Each carries the
    .xrk files it contains, its display path, and its top-level season name.
    Folder listings are fetched in parallel so the whole tree scans quickly.
    """
    from concurrent.futures import ThreadPoolExecutor

    root = _require_folder_id()
    results: list[SessionFolder] = []
    # Dated sessions can contain per-driver folders. Keep their first listing so
    # the nested telemetry pass does not request the session folder a second time.
    dated_sessions: list[tuple[str, list[str], list[DriveNode]]] = []
    # frontier items: (folder_id, path_parts)
    frontier = [
        (c.id, [c.name]) for c in list_children(root)
        if c.is_folder and c.id not in IGNORED_LEGACY_SESSION_FOLDER_IDS
        and c.name != ".xcal-catalog-v1"
    ]
    depth = 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        while frontier and depth <= max_depth:
            listings = list(pool.map(lambda it: (it, list_children(it[0])), frontier))
            next_frontier = []
            for (folder_id, path_parts), children in listings:
                subfolders = [
                    c for c in children
                    if (c.is_folder
                        and c.id not in IGNORED_LEGACY_SESSION_FOLDER_IDS)
                ]
                name = path_parts[-1] if path_parts else ""
                dated = bool(_SESSION_DATE_RE.match(name))
                # A dated folder is a session even when it holds driver
                # sub-folders â€” gather every nested .xrk under it as one session.
                if dated and subfolders:
                    dated_sessions.append((folder_id, path_parts, children))
                    continue
                xrks = tuple(c for c in children if c.is_xrk)
                if not subfolders or depth == max_depth:
                    results.append(SessionFolder(
                        id=folder_id,
                        name=path_parts[-1] if path_parts else "",
                        path=" / ".join(path_parts),
                        season=path_parts[0] if path_parts else "",
                        xrks=xrks,
                    ))
                else:
                    for sf in subfolders:
                        next_frontier.append((sf.id, path_parts + [sf.name]))
            frontier = next_frontier
            depth += 1

        # Scan every dated session's driver folders together. The prior version
        # re-listed each session and walked all descendants serially.
        nested_xrks: list[list[DriveNode]] = []
        nested_frontier: list[tuple[str, int]] = []
        for index, (_folder_id, _path_parts, children) in enumerate(dated_sessions):
            nested_xrks.append([child for child in children if child.is_xrk])
            nested_frontier.extend(
                (child.id, index) for child in children if child.is_folder)

        nested_depth = 1  # the dated session itself was depth zero
        while nested_frontier and nested_depth <= max_depth:
            listings = list(pool.map(
                lambda item: (item, list_children(item[0])), nested_frontier))
            next_nested: list[tuple[str, int]] = []
            for (_folder_id, owner), children in listings:
                nested_xrks[owner].extend(
                    child for child in children if child.is_xrk)
                next_nested.extend(
                    (child.id, owner) for child in children if child.is_folder)
            nested_frontier = next_nested
            nested_depth += 1

    for index, (folder_id, path_parts, _children) in enumerate(dated_sessions):
        results.append(SessionFolder(
            id=folder_id,
            name=path_parts[-1] if path_parts else "",
            path=" / ".join(path_parts),
            season=path_parts[0] if path_parts else "",
            xrks=tuple(nested_xrks[index]),
        ))
    return results


def create_folder(name: str, parent_id: str) -> str:
    """Create a subfolder in Drive and return its file ID."""
    metadata = {
        "name": name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_id],
    }
    try:
        result = _service().files().create(
            body=metadata,
            fields="id",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Could not create Drive folder: {exc}") from exc
    return result["id"]


def rename_file(file_id: str, new_name: str) -> None:
    """Rename a Drive file or folder (contents and file ID are unchanged)."""
    try:
        _service().files().update(
            fileId=file_id,
            body={"name": new_name},
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Could not rename Drive item: {exc}") from exc


def upload_file(
        local_path: str, parent_id: str | None = None,
        idempotency_key: str | None = None,
        recorded_at: str | None = None,
        timestamp_source: str | None = None,
        progress: Callable[[int], None] | None = None) -> str:
    parent_id = parent_id or _require_folder_id()
    metadata = {
        "name": Path(local_path).name,
        "parents": [parent_id],
    }
    if idempotency_key:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", idempotency_key):
            raise ValueError("Invalid offline upload identifier")
        metadata["appProperties"] = {"xcalOfflineId": idempotency_key}
    if recorded_at:
        properties = metadata.setdefault("appProperties", {})
        properties["xcalRecordedAt"] = str(recorded_at)[:124]
        if timestamp_source:
            properties["xcalTimestampSource"] = str(timestamp_source)[:124]
    media = MediaFileUpload(
        local_path,
        mimetype="application/octet-stream",
        resumable=True,
    )
    try:
        service = _service()
        if idempotency_key:
            existing = service.files().list(
                q=(f"'{parent_id}' in parents and trashed = false and "
                   "appProperties has { key='xcalOfflineId' and "
                   f"value='{idempotency_key}' }}"),
                fields="files(id)", pageSize=1,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ).execute().get("files", [])
            if existing:
                return existing[0]["id"]
        request = service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        if progress is None:
            result = request.execute()
        else:
            result = None
            progress(0)
            while result is None:
                from qt.workers import check_interruption
                check_interruption()
                status, result = request.next_chunk()
                if status is not None:
                    progress(max(0, min(99, int(status.progress() * 100))))
            progress(100)
    except (OSError, HttpError) as exc:
        raise GoogleDriveError(f"Google Drive upload failed: {exc}") from exc
    return result["id"]


def download_file(
        file_id: str,
        destination: Path,
        progress: Callable[[int], None] | None = None) -> None:
    """Download a Drive file, optionally reporting completed percentage."""
    try:
        last_progress = 0
        if progress:
            progress(last_progress)
        request = _service().files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        with destination.open("wb") as target:
            downloader = MediaIoBaseDownload(
                target, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                from qt.workers import check_interruption
                check_interruption()
                status, done = downloader.next_chunk()
                check_interruption()
                if progress and status is not None:
                    percent = max(0, min(100, round(status.progress() * 100)))
                    if percent != last_progress:
                        progress(percent)
                        last_progress = percent
        if progress and last_progress != 100:
            progress(100)
    except (OSError, HttpError) as exc:
        destination.unlink(missing_ok=True)
        raise GoogleDriveError(f"Google Drive download failed: {exc}") from exc


def trash_file(file_id: str) -> None:
    try:
        _service().files().update(
            fileId=file_id,
            body={"trashed": True},
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Google Drive deletion failed: {exc}") from exc


def move_file(file_id: str, new_parent_id: str) -> None:
    """Move a file or folder into another folder (its file ID is unchanged)."""
    service = _service()
    try:
        current = service.files().get(
            fileId=file_id, fields="parents", supportsAllDrives=True,
        ).execute()
        old_parents = ",".join(current.get("parents", []))
        service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=old_parents,
            fields="id,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Could not move Drive item: {exc}") from exc


def update_session_folder(file_id: str, new_name: str,
                          season: str | None = None) -> None:
    """Rename and optionally move a session folder in one Drive update.

    The folder ID and all descendants remain unchanged, which keeps Supabase
    setup links and Drive media references intact.
    """
    root = root_folder_id()
    target_parent = root
    if season:
        target_parent = ensure_child_folder(root, season)

    existing = find_child_folder(target_parent, new_name)
    if existing and existing != file_id:
        raise GoogleDriveError(
            f'A folder named "{new_name}" already exists in '
            f'"{season or "the Drive root"}".')

    service = _service()
    try:
        current = service.files().get(
            fileId=file_id, fields="name,parents", supportsAllDrives=True,
        ).execute()
        current_parents = current.get("parents", [])
        request = {
            "fileId": file_id,
            "body": {"name": new_name},
            "fields": "id,name,parents",
            "supportsAllDrives": True,
        }
        if target_parent not in current_parents:
            request["addParents"] = target_parent
            request["removeParents"] = ",".join(current_parents)
        service.files().update(**request).execute()
    except HttpError as exc:
        raise GoogleDriveError(
            f"Could not update session folder: {exc}") from exc


def find_child_folder(parent_id: str, name: str) -> str | None:
    """Return the ID of an immediate sub-folder by name, or None."""
    for child in list_children(parent_id):
        if child.is_folder and child.name.strip().lower() == name.strip().lower():
            return child.id
    return None


def ensure_child_folder(parent_id: str, name: str) -> str:
    """Return an immediate sub-folder by name, creating it when missing."""
    existing = find_child_folder(parent_id, name)
    return existing if existing else create_folder(name, parent_id)


def create_session_folder(name: str, season: str | None = None) -> str:
    """Create a session folder under the team root (optionally within a season)."""
    parent = root_folder_id()
    if season:
        parent = ensure_child_folder(parent, season)
    return create_folder(name, parent)


def _list_files_recursive(folder_id: str, predicate, max_depth: int) -> list[DriveNode]:
    """Collect matching files under a folder without repeated folder listings."""
    found: list[DriveNode] = []
    frontier = [folder_id]
    depth = 0
    while frontier and depth <= max_depth:
        next_frontier: list[str] = []
        for fid in frontier:
            for child in list_children(fid):
                if predicate(child):
                    found.append(child)
                elif child.is_folder:
                    next_frontier.append(child.id)
        frontier = next_frontier
        depth += 1
    return found


def list_xrks_recursive(folder_id: str, max_depth: int = 3) -> list[DriveNode]:
    """Collect every .xrk under a folder, including nested driver sub-folders."""
    return _list_files_recursive(folder_id, lambda node: node.is_xrk, max_depth)


def list_telemetry_recursive(
        folder_id: str, max_depth: int = 3) -> list[DriveNode]:
    """Collect all files that belong in the session Data tab."""
    return _list_files_recursive(
        folder_id, lambda node: node.is_telemetry, max_depth)


def list_videos_recursive(folder_id: str, max_depth: int = 3) -> list[DriveNode]:
    """Collect loose SmartyCam/GoPro MP4 and MOV files under a session."""
    return _list_files_recursive(folder_id, lambda node: node.is_video, max_depth)


def list_session_media_recursive(
        folder_id: str, max_depth: int = 3,
) -> tuple[list[DriveNode], list[DriveNode]]:
    """Collect telemetry and video media with one session-folder traversal."""
    media = _list_files_recursive(
        folder_id, lambda node: node.is_telemetry or node.is_video, max_depth)
    return (
        [node for node in media if node.is_telemetry],
        [node for node in media if node.is_video],
    )
