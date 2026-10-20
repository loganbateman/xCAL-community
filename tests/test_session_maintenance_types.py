import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import maintenance as m
import maintenance_store
import session_types as st
from database.models import Setup
from utils.google_drive_client import SessionFolder
from qt.views import session_detail_qt
from qt.views.sessions_qt import NewSessionDialog, SessionsView
from qt.views.maintenance_settings_qt import MaintenanceSettingsDialog


@pytest.fixture(autouse=True)
def isolated_types(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CACHE_PATH", tmp_path / "types.json")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def mixed():
    sessions, setups = [], []
    for index, kind in enumerate(["Practice", "Race", "Practice", "Qualifying", "Race", "Practice", "Practice"], 1):
        name = f"2026-10-{index:02} {kind}"
        session = SessionFolder(str(index), name, name, "2026", ())
        sessions.append(session)
        setups.append(Setup(id=str(index), drive_folder_id=str(index),
                            drive_folder_path=name, chassis_name="Test kart"))
    policy = m.default_policy()
    policy.update(start_date="2026-01-01", start_session_id="", start_session_name="")
    policy["items"] = [{"id": "service", "label": "Service", "enabled": True,
                        "kind": "interval", "every": 5, "unit": "sessions",
                        "by_session_type": {
                            "practice": {"enabled": True, "every": 3, "unit": "sessions"},
                            "qualifying": {"enabled": True, "every": 2, "unit": "sessions"},
                            "race": {"enabled": True, "every": 1, "unit": "sessions"}}}]
    return sessions, setups, policy


def row(state):
    return (*state.rows, *state.future)[0]


def test_intervals_count_only_matching_session_types(mixed):
    sessions, setups, policy = mixed
    assert row(m.checklist(policy, setups[0], sessions, setups, types={})).remaining == 2
    assert row(m.checklist(policy, setups[1], sessions, setups, types={})).due
    assert row(m.checklist(policy, setups[2], sessions, setups, types={})).remaining == 1
    assert row(m.checklist(policy, setups[3], sessions, setups, types={})).remaining == 1
    assert row(m.checklist(policy, setups[5], sessions, setups, types={})).due


def test_race_service_resets_practice_interval_without_double_counting(mixed):
    sessions, setups, policy = mixed
    race = setups[1]
    effective = row(m.checklist(policy, race, sessions, setups, types={})).item
    race.data[m.CHECKS_KEY] = m.set_checked({}, m.kart_key(race), race.drive_folder_id,
                                           effective, True, None,
                                           now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    duplicate = deepcopy(setups[2])
    duplicate.id = "duplicate"
    assert row(m.checklist(policy, setups[2], sessions, setups + [duplicate], types={})).remaining == 2
    assert row(m.checklist(policy, setups[5], sessions, setups, types={})).remaining == 1
    assert row(m.checklist(policy, setups[6], sessions, setups, types={})).due


def test_manual_classification_overrides_name_for_intervals(mixed):
    sessions, setups, policy = mixed
    assert row(m.checklist(policy, setups[0], sessions, setups, types={"1": "race"})).due
    assert st.classify("1", "Practice", types={"1": "qualifying"}) == "qualifying"


def test_hidden_type_and_calendar_override(mixed):
    sessions, setups, policy = mixed
    policy["items"][0]["by_session_type"]["race"] = {"enabled": False}
    state = m.checklist(policy, setups[1], sessions, setups, types={})
    assert state.rows == state.future == ()
    policy["items"][0]["by_session_type"]["practice"] = {"enabled": True, "every": 4, "unit": "days"}
    policy["start_date"] = "2026-10-01"
    assert row(m.checklist(policy, setups[0], sessions, setups, types={})).remaining == 4
    assert row(m.checklist(policy, setups[5], sessions, setups, types={})).due


@pytest.mark.parametrize("rule", [{"enabled": True, "every": True, "unit": "sessions"},
                                  {"enabled": True, "every": 0, "unit": "sessions"},
                                  {"enabled": True, "every": 2, "unit": "hours"},
                                  {"enabled": "yes"}])
def test_invalid_override_is_rejected(mixed, rule):
    _sessions, _setups, policy = mixed
    policy["items"][0]["by_session_type"]["race"] = rule
    with pytest.raises(ValueError):
        m.normalize_policy(policy)


@pytest.mark.parametrize("name,expected", [("Practice 4", "practice"), ("Qualifying", "qualifying"),
                                           ("Race 2026", "race"), ("Purdue GP Test", "practice")])
def test_legacy_names_are_inferred(name, expected):
    assert st.classify("id", name, types={}) == expected


def test_schedule_editor_roundtrips_overrides_and_hiding(app, mixed):
    sessions, _setups, policy = mixed
    policy["items"][0]["by_session_type"]["qualifying"] = {"enabled": False}
    dialog = MaintenanceSettingsDialog(maintenance_store.PolicySnapshot(policy, available=True), sessions)
    assert dialog.policy() == m.normalize_policy(policy)
    dialog._table.cellWidget(0, 7).setValue(2)
    assert dialog.policy()["items"][0]["by_session_type"]["race"]["every"] == 2
    dialog.deleteLater()


def test_every_setup_check_keeps_independent_day_and_session_overrides(app, mixed):
    sessions, _setups, policy = mixed
    item = policy["items"][0]
    item.update(kind="check")
    item.pop("every")
    item.pop("unit")
    item["by_session_type"]["practice"].update(every=7, unit="days")
    dialog = MaintenanceSettingsDialog(maintenance_store.PolicySnapshot(policy, available=True), sessions)
    assert dialog.policy() == m.normalize_policy(policy)
    dialog.show()
    app.processEvents()
    assert not dialog._table.cellWidget(0, 3).isVisible()
    dialog._table.cellWidget(0, 7).unit.setCurrentIndex(1)
    assert dialog.policy()["items"][0]["by_session_type"]["race"]["unit"] == "days"
    dialog.deleteLater()


def test_type_filter_and_new_session_choices(app, mixed):
    sessions, _setups, _policy = mixed
    view = SessionsView()
    view._sessions = sessions
    view._type_filter.setCurrentIndex(view._type_filter.findData("race"))
    assert view._count.text().startswith("2 sessions")
    dialog = NewSessionDialog(["2026"])
    assert [dialog._session_type.itemData(i) for i in range(3)] == list(st.TYPES)
    view.deleteLater()
    dialog.deleteLater()


def test_type_save_keeps_folder_identity_and_reports_change(app, monkeypatch):
    detail = session_detail_qt.SessionDetailView()
    detail._folder_id, detail._folder_name, detail._folder_path = "folder", "Practice", "2026 / Practice"
    events, writes = [], []
    detail.session_updated.connect(lambda *args: events.append(args))
    monkeypatch.setattr(session_detail_qt.QInputDialog, "getItem", lambda *args: ("Race", True))
    monkeypatch.setattr(st, "save", lambda folder, kind: writes.append((folder, kind)) or kind)
    monkeypatch.setattr(session_detail_qt, "run_async", lambda parent, work, on_done, on_error: on_done(work()))
    detail._edit_session_type()
    assert writes == [("folder", "race")]
    assert events == [("folder", "2026 / Practice", "Practice", "")]
    assert detail._type_button.text() == "Race session"
    detail.deleteLater()


def test_classification_save_uses_guarded_stable_id_document(monkeypatch):
    calls = []

    class Query:
        def __init__(self):
            self.action, self.filters = "select", {}
        def select(self, _columns):
            return self
        def limit(self, _count):
            return self
        def eq(self, key, value):
            self.filters[key] = value
            return self
        def update(self, payload):
            self.action = "update"
            assert payload["data"]["type"] == "race"
            return self
        def execute(self):
            calls.append((self.action, self.filters.copy()))
            return SimpleNamespace(data=[{"updated_at": "revision", "data": {"type": "practice"}}])

    monkeypatch.setattr(st.db, "get_client", lambda: SimpleNamespace(table=lambda table: Query()))
    monkeypatch.setattr(st.google_drive_client, "audit_identity", lambda: "Test editor")
    st.save("stable-folder-id", "race")
    assert calls[-1] == ("update", {"key": "session_type:stable-folder-id", "updated_at": "revision"})
    assert st.cached_types() == {"stable-folder-id": "race"}


def test_classification_refresh_filters_documents_before_downloading(monkeypatch):
    filters = []

    class Query:
        def select(self, columns):
            assert columns == "key,data"
            return self
        def like(self, field, pattern):
            filters.append((field, pattern))
            return self
        def execute(self):
            assert filters == [("key", "session_type:%")]
            return SimpleNamespace(data=[{"key": "session_type:folder", "data": {"type": "race"}}])

    monkeypatch.setattr(st.db, "get_client", lambda: SimpleNamespace(table=lambda table: Query()))
    assert st.refresh() == {"folder": "race"}
