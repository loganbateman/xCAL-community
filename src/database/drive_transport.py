"""Shared Drive transport for immutable catalog events; no Supabase required."""
import io
import json
import re

from googleapiclient.http import MediaIoBaseUpload
from utils import google_drive_client as drive

JOURNAL_NAME = ".xcal-catalog-v1"
MAX_EVENT_BYTES = 8 * 1024 * 1024


class DriveJournal:
    def __init__(self, root_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", root_id):
            raise ValueError("Invalid Drive folder ID.")
        self.root_id = root_id
        self.folder_id = None
        self.cached_files = None

    def _folder(self, service):
        if self.folder_id:
            return self.folder_id
        folders = service.files().list(
            q=f"'{self.root_id}' in parents and name = '{JOURNAL_NAME}' and trashed = false",
            fields="files(id,mimeType)", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute().get("files", [])
        if len(folders) != 1 or folders[0]["mimeType"] != drive.FOLDER_MIME:
            raise RuntimeError("Choose a team Drive in Settings and initialize its xCAL catalog first.")
        self.folder_id = folders[0]["id"]
        return self.folder_id

    @staticmethod
    def initialize(root_id):
        service = drive._worker_service()
        root = service.files().get(fileId=root_id, fields="id,mimeType,capabilities(canAddChildren)",
                                   supportsAllDrives=True).execute()
        if root["mimeType"] != drive.FOLDER_MIME or not root.get("capabilities", {}).get("canAddChildren"):
            raise ValueError("Choose a Drive folder where your Google account can create files.")
        journal = DriveJournal(root_id)
        matches = service.files().list(
            q=f"'{root_id}' in parents and name = '{JOURNAL_NAME}' and trashed = false",
            fields="files(id,mimeType)", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute().get("files", [])
        if not matches:
            service.files().create(body={"name": JOURNAL_NAME, "mimeType": drive.FOLDER_MIME,
                                         "parents": [root_id]}, fields="id", supportsAllDrives=True).execute()
        elif len(matches) != 1 or matches[0]["mimeType"] != drive.FOLDER_MIME:
            raise ValueError("This folder has ambiguous xCAL catalogs; no existing files were changed.")
        return journal._folder(service)

    def _index(self, service, refresh=False):
        folder = self._folder(service)
        if self.cached_files is not None and not refresh:
            return folder, self.cached_files
        files, token = [], None
        while True:
            response = service.files().list(
                q=f"'{folder}' in parents and trashed = false", fields="nextPageToken,files(id,name,size)",
                pageSize=1000, pageToken=token, supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute()
            files.extend(response.get("files", []))
            token = response.get("nextPageToken")
            if not token:
                self.cached_files = files
                return folder, files

    def read_unknown(self, known):
        service = drive._worker_service()
        _folder, files = self._index(service, refresh=True)
        events = {}
        for item in files:
            if not item["name"].endswith(".json") or item["name"][:-5] in known:
                continue
            if int(item.get("size", 0)) > MAX_EVENT_BYTES:
                raise ValueError("A catalog change is too large to safely read.")
            from qt.workers import check_interruption
            check_interruption()
            raw = service.files().get_media(fileId=item["id"], supportsAllDrives=True).execute()
            if len(raw) > MAX_EVENT_BYTES:
                raise ValueError("A catalog change is too large to safely read.")
            event = json.loads(raw)
            if item["name"] != f"{event['id']}.json":
                raise ValueError("A catalog event filename does not match its identity.")
            if event["id"] in events and event != events[event["id"]]:
                raise ValueError("Duplicate catalog event identities have different contents.")
            events[event["id"]] = event
        return list(events.values())

    def append(self, event):
        service = drive._worker_service()
        folder, files = self._index(service)
        name = f"{event['id']}.json"
        # An upload may succeed before its response is lost. Retrying is safe.
        matches = [item for item in files if item["name"] == name]
        for item in matches:
            saved = json.loads(service.files().get_media(fileId=item["id"], supportsAllDrives=True).execute())
            if saved != event:
                raise ValueError("An existing catalog event has different contents; it was not overwritten.")
        if matches:
            return
        raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_EVENT_BYTES:
            raise ValueError("This catalog change is too large to upload.")
        created = service.files().create(body={"name": name, "parents": [folder], "mimeType": "application/json"},
                               media_body=MediaIoBaseUpload(io.BytesIO(raw), mimetype="application/json"),
                               fields="id", supportsAllDrives=True).execute()
        files.append({"id": created["id"], "name": name, "size": len(raw)})
