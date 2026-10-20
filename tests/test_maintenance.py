from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import maintenance as m
import database.client as db
from database.models import Setup
from utils.google_drive_client import SessionFolder, GoogleUser


@pytest.fixture
def season():
    sessions, setups = [], []
    for number in range(1, 13):
        day = date(2026, 10, 6) + timedelta(days=number - 1)
        name = f"{day} Halloween 100 Practice {number}"
        session = SessionFolder(f"session-{number}", name, f"2026 Season / {name}", "2026 Season", ())
        sessions.append(session)
        setups.append(Setup(id=f"setup-{number}", drive_folder_id=session.id,
                            drive_folder_path=session.path, chassis_id="kart-11", chassis_name="Kart 11"))
    return sessions, setups


def interval(state):
    return next((row for row in (*state.rows, *state.future) if row.item["id"] == "replace-wheel-nuts"), None)


def complete(setup, checked=True, item=None, now=None):
    setup.data[m.CHECKS_KEY] = m.set_checked(
        setup.data.get(m.CHECKS_KEY, {}), m.kart_key(setup), setup.drive_folder_id,
        item or m.default_policy()["items"][-2], checked, GoogleUser("Tester", "tester@example.test"),
        now=now or datetime(2026, 10, 10, 18, tzinfo=timezone.utc))


@pytest.mark.parametrize("number,due,remaining", [(1, False, 4), (4, False, 1), (5, True, 0), (6, True, 0)])
def test_fifth_session_inclusive_and_overdue_persists(season, number, due, remaining):
    sessions, setups = season
    state = m.checklist(None, setups[number - 1], sessions, setups)
    assert state.ordinal == number
    assert interval(state).due is due
    assert interval(state).remaining == remaining
    assert len([row for row in state.rows if row.item["kind"] == "check"]) == 6


def test_duplicate_setups_and_runs_count_once_other_karts_do_not_count(season):
    sessions, setups = season
    copies = []
    for setup in setups:
        clone = deepcopy(setup)
        clone.id += "-duplicate"
        clone.data["runs"] = [{"id": str(i)} for i in range(15)]
        copies.append(clone)
        other = deepcopy(setup)
        other.id += "-other"
        other.chassis_id, other.chassis_name = "kart-42", "Kart 42"
        copies.append(other)
    state = m.checklist(None, setups[4], sessions, setups + copies)
    assert state.ordinal == 5
    assert interval(state).due
    # A kart first used at Practice 5 starts its own session count at 1.
    newcomer = deepcopy(setups[4])
    newcomer.chassis_id = "new-kart"
    newcomer.chassis_name = "New kart"
    assert m.checklist(None, newcomer, sessions, setups).ordinal == 1


def test_service_timestamp_and_editor_reset_next_interval(season):
    sessions, setups = season
    complete(setups[4])
    record = next(iter(setups[4].data[m.CHECKS_KEY].values()))
    assert record["completed_at"] == "2026-10-10T18:00:00+00:00"
    assert record["actor_email"] == "tester@example.test"
    state = m.checklist(None, setups[4], sessions, setups)
    assert interval(state).checked and not interval(state).due
    assert interval(m.checklist(None, setups[8], sessions, setups)).remaining == 1
    assert interval(m.checklist(None, setups[9], sessions, setups)).due
    assert interval(m.checklist(None, setups[3], sessions, setups)).remaining == 1


def test_other_kart_service_does_not_reset_current_kart(season):
    sessions, setups = season
    other = deepcopy(setups[4])
    other.id = "other"
    other.chassis_id, other.chassis_name = "kart-42", "Kart 42"
    complete(other)
    assert interval(m.checklist(None, setups[5], sessions, setups + [other])).due


def test_undo_wins_over_old_copy_across_multiple_sheets(season):
    sessions, setups = season
    complete(setups[4])
    other = deepcopy(setups[4])
    other.id = "another-sheet-same-session"
    complete(other, False, now=datetime(2026, 10, 10, 19, tzinfo=timezone.utc))
    state = m.checklist(None, setups[5], sessions, setups + [other])
    assert interval(state).due
    same_session = m.checklist(None, setups[4], sessions, setups + [other])
    assert not interval(same_session).checked and interval(same_session).due


def test_basic_checks_are_per_sheet_and_not_inherited_by_next_session(season):
    sessions, setups = season
    complete(setups[0], item=m.default_policy()["items"][0])
    assert m.checklist(None, setups[0], sessions, setups).rows[0].checked
    assert not m.checklist(None, setups[1], sessions, setups).rows[0].checked


def test_calendar_day_interval_uses_actual_service_timestamp(season):
    sessions, setups = season
    policy = m.default_policy()
    item = policy["items"][-2]
    item.update(unit="days", every=4)
    assert interval(m.checklist(policy, setups[4], sessions, setups)).due
    complete(setups[4], item=item)
    assert interval(m.checklist(policy, setups[5], sessions, setups)).remaining == 3
    assert interval(m.checklist(policy, setups[8], sessions, setups)).due


def test_calendar_completion_date_is_stored_separately_from_utc_clock(season):
    _sessions, setups = season
    clock = datetime(2026, 10, 11, 2, tzinfo=timezone.utc)
    complete(setups[4], now=clock)
    record = next(iter(setups[4].data[m.CHECKS_KEY].values()))
    assert record["completed_at"] == clock.isoformat()
    assert record["completed_date"] == clock.astimezone().date().isoformat()


def test_anchor_prevents_counting_previous_season(season):
    sessions, setups = season
    earlier = Setup(id="old", drive_folder_id="old-session", drive_folder_path="2025-10-06 Halloween 100 Practice 1",
                    chassis_id="kart-11", chassis_name="Kart 11")
    catalog = [earlier] + setups
    policy = m.default_policy()
    policy.update(start_date="2026-10-06")
    assert not m.checklist(policy, earlier, sessions, catalog).active
    assert m.checklist(policy, setups[4], sessions, catalog).ordinal == 5
    policy.update(start_session_id=sessions[1].id, start_session_name=sessions[1].name)
    assert m.checklist(policy, setups[4], sessions, catalog).ordinal == 4


def test_offline_catalog_can_get_dates_from_saved_paths(season):
    _sessions, setups = season
    assert interval(m.checklist(None, setups[4], [], setups)).due


def test_disabled_item_hides_without_mutating_records(season):
    sessions, setups = season
    complete(setups[4])
    before = deepcopy(setups[4].data)
    policy = m.default_policy()
    policy["items"][-2]["enabled"] = False
    assert interval(m.checklist(policy, setups[5], sessions, setups)) is None
    assert setups[4].data == before


def test_legacy_name_chassis_and_part_bridge_only_if_unambiguous(season):
    sessions, setups = season
    complete(setups[4])
    # New Parts identity links the same older chassis; also bridge name-only copies.
    setups[0].chassis_id = None
    setups[5].data["_identity_part_revisions"] = {"chassis": {"item_id": "part-11", "revision": 2}}
    state = m.checklist(None, setups[5], sessions, setups)
    assert state.ordinal == 6 and state.kart == "part:part-11"
    assert interval(state).remaining == 4
    other = deepcopy(setups[5])
    other.id, other.chassis_id = "distinct", None
    other.data = {"_identity_part_revisions": {"chassis": {"item_id": "different-part"}}}
    assert m.checklist(None, other, sessions, setups + [other]).ordinal == 1


@pytest.mark.parametrize("change,message", [
    ({"start_date": "bad"}, "start date"),
    ({"schema": 3}, "Unsupported"),
    ({"items": [{"id": "x", "label": "", "kind": "check"}]}, "labels"),
    ({"items": [{"id": "x", "label": "Test", "kind": "interval", "every": True, "unit": "sessions"}]}, "whole numbers"),
])
def test_policy_validation(change, message):
    with pytest.raises(ValueError, match=message):
        m.normalize_policy({**m.default_policy(), **change})


def test_baseline_missing_kart_and_undated_session_are_inactive(season):
    sessions, setups = season
    assert not m.checklist(None, Setup(is_baseline=True), sessions, setups).active
    assert not m.checklist(None, Setup(drive_folder_id="session-1"), sessions, setups).active
    assert not m.checklist(None, Setup(drive_folder_id="unknown", chassis_name="Kart 11"), sessions, setups).active


def test_pending_identity_resolution_preserves_completion_time(season):
    _sessions, setups = season
    setup = setups[0]
    records = m.set_checked({}, m.kart_key(setup), setup.drive_folder_id,
                            m.default_policy()["items"][0], True, None)
    setup.data[m.CHECKS_KEY] = records
    before = next(iter(records.values()))["completed_at"]
    assert db._apply_setup_identity(setup, SimpleNamespace(name="Verified", email="verified@example.test"))
    record = next(iter(setup.data[m.CHECKS_KEY].values()))
    assert record["completed_at"] == before
    assert record["actor_name"] == "Verified" and not record.get("identity_pending")


def test_concurrent_different_checks_merge_same_service_conflicts(season):
    _sessions, setups = season
    base = setups[0]
    local, remote = deepcopy(base), deepcopy(base)
    complete(local, item=m.default_policy()["items"][0])
    complete(remote, item=m.default_policy()["items"][1])
    merged, conflicts = db.merge_setup_changes(base, local, remote)
    assert not conflicts and len(merged.data[m.CHECKS_KEY]) == 2
    local, remote = deepcopy(base), deepcopy(base)
    complete(local)
    complete(remote, now=datetime(2026, 10, 10, 19, tzinfo=timezone.utc))
    _merged, conflicts = db.merge_setup_changes(base, local, remote)
    assert conflicts


def test_malformed_optional_records_do_not_break_setup(season):
    sessions, setups = season
    setups[0].data[m.CHECKS_KEY] = ["invalid old record"]
    assert m.checklist(None, setups[4], sessions, setups).active
