import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from database.models import Setup
from qt.views.sessions_qt import (
    _display, _season_sort_key, _session_sort_key, _setup_identity_labels)
from qt.views.session_workspace_qt import _workspace_sort_key


def _session(name, path=None):
    return SimpleNamespace(name=name, path=path or name)


def test_seasons_sort_newest_first_and_custom_folders_last():
    seasons = [
        "GENERAL ONBOARDS",
        "2024-25 Testing",
        "Indianapolis Kassy Data",
        "2026-2027 Season",
        "2023-24 Testing",
        "2025-26 Season",
    ]

    assert sorted(seasons, key=_season_sort_key) == [
        "2026-2027 Season",
        "2025-26 Season",
        "2024-25 Testing",
        "2023-24 Testing",
        "GENERAL ONBOARDS",
        "Indianapolis Kassy Data",
    ]


def test_sessions_sort_by_date_and_recognize_undated_event_years():
    sessions = [
        _session("Halloween Hundred 2026"),
        _session("2026-06-01 Early race"),
        _session("2026-10-31 Halloween Hundred"),
        _session("Club notes"),
    ]

    assert [s.name for s in sorted(sessions, key=_session_sort_key)] == [
        "Halloween Hundred 2026",
        "2026-10-31 Halloween Hundred",
        "2026-06-01 Early race",
        "Club notes",
    ]


def test_undated_event_displays_its_name_instead_of_parent_season():
    event = _session(
        "Halloween Hundred 2026",
        "2026-2027 Season / Halloween Hundred 2026")

    assert _display(event) == (None, "Halloween Hundred 2026", None)


def test_nested_kart_folder_uses_its_parent_session_date():
    nested = _session("12", "2026-2027 Season / 2026-08-24 Race / 12")
    older = _session("2026-08-01 Practice")

    assert sorted([older, nested], key=_session_sort_key) == [nested, older]


def test_open_session_keeps_newest_first_workspace_order():
    current = SimpleNamespace(
        id="current", name="2026-10-08 Practice", path="2026-10-08 Practice")
    newest_dated = SimpleNamespace(
        id="dated", name="2026-10-17 Race", path="2026-10-17 Race")

    ordered = sorted(
        [newest_dated, current],
        key=lambda session: _workspace_sort_key(session, current.id))

    assert ordered == [newest_dated, current]


def test_homepage_identity_labels_come_from_saved_setups():
    birel = Setup(
        driver_name="Alexander Davis",
        chassis_name="2021 Birel",
        data={"kart_number": "11"},
    )
    redspeed = Setup(
        driver_name="Carson Bowers",
        chassis_name="2022 RedSpeed",
        data={"kart_number": "42"},
    )

    assert _setup_identity_labels([birel, redspeed]) == [
        "Alexander Davis", "#11 2021 Birel",
        "Carson Bowers", "#42 2022 RedSpeed",
    ]
