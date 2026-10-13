from copy import deepcopy
import json
import re
from unittest.mock import patch
import pytest

from database.drive_transport import DriveJournal, JOURNAL_NAME
from utils.google_drive_client import FOLDER_MIME


class Result:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return deepcopy(self.value)


class Files:
    def __init__(self):
        self.items = {"root": {"id": "root", "name": "Team", "mimeType": FOLDER_MIME,
                               "capabilities": {"canAddChildren": True}}}
        self.payloads = {}
        self.reads = 0
        self.creates = []

    def get(self, fileId, **kwargs):
        assert kwargs["supportsAllDrives"]
        return Result(self.items[fileId])

    def list(self, q, **kwargs):
        assert kwargs["supportsAllDrives"] and kwargs["includeItemsFromAllDrives"]
        parent = re.search(r"'([^']+)' in parents", q)[1]
        matches = [item for item in self.items.values() if parent in item.get("parents", [])]
        named = re.search(r"name = '([^']+)'", q)
        if named:
            matches = [item for item in matches if item["name"] == named[1]]
        return Result({"files": matches})

    def create(self, body, media_body=None, **kwargs):
        assert kwargs["supportsAllDrives"]
        identity = str(len(self.items))
        item = {**deepcopy(body), "id": identity}
        if media_body:
            self.payloads[identity] = media_body._fd.getvalue()
            item["size"] = len(self.payloads[identity])
        self.items[identity] = item
        self.creates.append(item)
        return Result({"id": identity})

    def get_media(self, fileId, **kwargs):
        assert kwargs["supportsAllDrives"]
        self.reads += 1
        return Result(self.payloads[fileId])


class Service:
    def __init__(self):
        self.api = Files()

    def files(self):
        return self.api


@pytest.fixture
def drive():
    service = Service()
    with patch("database.drive_transport.drive._worker_service", return_value=service):
        yield service


def event(identity="event-one"):
    return {"schema": 1, "id": identity, "parents": [], "table": "setups", "key": "setup",
            "before": None, "after": {"id": "setup", "name": "Practice"}}


def test_owner_initializes_only_one_catalog_and_does_not_move_media(drive):
    first = DriveJournal.initialize("root")
    assert DriveJournal.initialize("root") == first
    assert len(drive.api.creates) == 1
    assert drive.api.creates[0]["name"] == JOURNAL_NAME
    assert drive.api.creates[0]["parents"] == ["root"]


def test_viewer_cannot_initialize_catalog(drive):
    drive.api.items["root"]["capabilities"]["canAddChildren"] = False
    with pytest.raises(ValueError, match="create files"):
        DriveJournal.initialize("root")
    assert not drive.api.creates


def test_duplicate_catalog_folders_fail_without_overwrites(drive):
    for number in (1, 2):
        drive.api.items[str(number)] = {"id": str(number), "name": JOURNAL_NAME,
                                         "parents": ["root"], "mimeType": FOLDER_MIME}
    with pytest.raises(ValueError, match="ambiguous"):
        DriveJournal.initialize("root")
    assert not drive.api.creates


def test_append_retry_and_unknown_payload_reads(drive):
    DriveJournal.initialize("root")
    journal = DriveJournal("root")
    journal.append(event())
    journal.append(event())
    assert len(drive.api.creates) == 2  # folder plus one event
    assert journal.read_unknown(set()) == [event()]
    before = drive.api.reads
    assert journal.read_unknown({"event-one"}) == []
    assert drive.api.reads == before


def test_mismatched_existing_event_is_never_replaced(drive):
    DriveJournal.initialize("root")
    journal = DriveJournal("root")
    journal.append(event())
    changed = event()
    changed["after"]["name"] = "Different"
    with pytest.raises(ValueError, match="not overwritten"):
        journal.append(changed)
    assert len(drive.api.creates) == 2


def test_journal_rejects_mismatched_filename(drive):
    DriveJournal.initialize("root")
    journal = DriveJournal("root")
    journal.append(event())
    saved = next(item for item in drive.api.items.values() if item["name"] == "event-one.json")
    drive.api.payloads[saved["id"]] = json.dumps(event("different-id")).encode()
    with pytest.raises(ValueError, match="filename"):
        journal.read_unknown(set())


def test_invalid_root_id_is_not_sent_to_google(drive):
    with pytest.raises(ValueError, match="Invalid"):
        DriveJournal("root' or trashed=true")
    assert not drive.api.creates
