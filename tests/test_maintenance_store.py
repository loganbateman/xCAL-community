from copy import deepcopy
from types import SimpleNamespace

import pytest

import maintenance
import maintenance_store as store


class Client:
    def __init__(self, row=None):
        self.row = deepcopy(row)
        self.offline = False
        self.calls = []

    def table(self, table):
        assert table == "app_docs"
        return Query(self)


class Query:
    def __init__(self, client):
        self.client = client
        self.action = "select"
        self.payload = None
        self.filters = {}

    def select(self, _columns):
        return self

    def limit(self, _number):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def is_(self, key, _null):
        self.filters[key] = None
        return self

    def update(self, payload):
        self.action, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", payload
        return self

    def execute(self):
        client = self.client
        client.calls.append((self.action, self.filters, self.payload))
        if client.offline:
            raise ConnectionError("offline")
        if self.action == "select":
            return SimpleNamespace(data=[deepcopy(client.row)] if client.row else [])
        if self.action == "insert" and client.row:
            raise RuntimeError("duplicate key")
        if self.action == "update" and (
                not client.row or client.row.get("updated_at") != self.filters["updated_at"]):
            return SimpleNamespace(data=[])
        client.row = {**self.payload, "updated_at": "revision-after-save"}
        return SimpleNamespace(data=[deepcopy(client.row)])


@pytest.fixture
def client(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(store, "CACHE_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store.db, "get_client", lambda: client)
    monkeypatch.setattr(store.google_drive_client, "audit_identity", lambda: "Tester <tester@example.test>")
    return client


def test_online_defaults_offline_cache_and_create(client):
    snapshot = store.load_snapshot()
    assert snapshot.available and not snapshot.exists
    policy = deepcopy(snapshot.policy)
    policy["items"][-2]["every"] = 7
    saved = store.save_policy(policy, snapshot)
    assert saved.exists and saved.updated_at == "revision-after-save"
    assert client.row["updated_by"] == "Tester <tester@example.test>"
    client.offline = True
    cached = store.load_snapshot()
    assert not cached.available and cached.policy == policy
    assert cached.updated_at == saved.updated_at
    with pytest.raises(RuntimeError, match="Connect to team catalog"):
        store.save_policy(policy, cached)


def test_existing_schedule_guarded_update(client):
    client.row = {"data": maintenance.default_policy(), "updated_at": "before"}
    snapshot = store.load_snapshot()
    store.save_policy(snapshot.policy, snapshot)
    action, filters, _payload = client.calls[-1]
    assert action == "update" and filters == {"key": store.DOC_KEY, "updated_at": "before"}


def test_stale_schedule_cannot_overwrite_new_team_changes(client):
    client.row = {"data": maintenance.default_policy(), "updated_at": "before"}
    snapshot = store.load_snapshot()
    client.row["updated_at"] = "newer"
    before = deepcopy(client.row)
    with pytest.raises(store.PolicyConflict, match="Someone changed"):
        store.save_policy(snapshot.policy, snapshot)
    assert client.row == before


def test_concurrent_first_create_is_not_upserted(client):
    snapshot = store.load_snapshot()
    client.row = {"data": maintenance.default_policy(), "updated_at": "created-by-teammate"}
    with pytest.raises(store.PolicyConflict, match="created while"):
        store.save_policy(snapshot.policy, snapshot)
    assert client.row["updated_at"] == "created-by-teammate"


def test_corrupt_cache_falls_back_to_defaults(client):
    client.offline = True
    store.CACHE_PATH.write_text("not-json", encoding="utf-8")
    assert store.load_snapshot().policy == maintenance.default_policy()


def test_unwritable_cache_does_not_turn_successful_save_into_failure(client, monkeypatch):
    snapshot = store.load_snapshot()
    monkeypatch.setattr(store, "NamedTemporaryFile", lambda **_kwargs: (_ for _ in ()).throw(PermissionError("locked")))
    assert store.save_policy(snapshot.policy, snapshot).exists
