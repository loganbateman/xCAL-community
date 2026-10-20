from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox
from PySide6.QtCore import QCoreApplication, QEvent

import maintenance as m
import maintenance_store
import offline_store
from database.models import Setup, Chassis
from qt.maintenance_checklist import MaintenanceChecklist
from qt.views.maintenance_settings_qt import MaintenanceSettingsDialog
from qt.views.setup_builder_qt import SetupBuilderView, _maintenance_history_lines
from utils.google_drive_client import GoogleUser, SessionFolder


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(offline_store, "OFFLINE_DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(offline_store, "OFFLINE_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(maintenance_store, "CACHE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr("qt.views.setup_builder_qt.cache.load_sessions", lambda: ([], None))
    monkeypatch.setattr("qt.maintenance_checklist.google_drive_client.cached_user", lambda: GoogleUser("Tester", "test@example.test"))
    session_name = "2026-10-06 Test Practice"
    session = SessionFolder("s1", session_name, "2026 Season / " + session_name, "2026 Season", ())
    setup = Setup(id="setup1", name="Test setup", drive_folder_id=session.id, drive_folder_path=session.path,
                  chassis_id="kart11", chassis_name="Kart 11")
    return session, setup


def test_widget_ticks_timestamp_without_upload_and_refresh_does_not_tick(app, context):
    session, setup = context
    card = MaintenanceChecklist()
    card.refresh(m.default_policy(), setup, [session], [setup])
    assert card.records() == {}
    changes = []
    card.changed.connect(lambda: changes.append(True))
    card.findChildren(QCheckBox)[0].click()
    app.processEvents()
    record = next(iter(card.records().values()))
    assert record["checked"] and record["completed_at"]
    assert record["actor_name"] == "Tester"
    assert len(changes) == 1 and setup.data == {}
    card.refresh(m.default_policy(), setup, [session], [setup])
    assert len(changes) == 1
    card.deleteLater()


def test_builder_dirty_revert_runs_and_copy_do_not_inherit_checks(app, context):
    session, setup = context
    builder = SetupBuilderView()
    builder._folder_id, builder._folder_path = session.id, session.path
    builder._folder_name = session.name
    builder._setup = setup
    builder._chassis = [Chassis(id="kart11", name="Kart 11")]
    builder._populate(setup)
    builder._initial_state = builder._form_state()
    builder._maintenance.findChildren(QCheckBox)[0].click()
    assert builder.has_unsaved_changes()
    before = builder._maintenance.records()
    builder._add_run()
    assert builder._maintenance.records() == before
    builder.revert_changes()
    assert not builder._maintenance.records()
    assert not builder.has_unsaved_changes()
    source = deepcopy(setup)
    source.data[m.CHECKS_KEY] = before
    builder._populate(source, inherit=True)
    assert builder._maintenance.records() == {}
    builder.deleteLater()


def test_builder_preserves_offline_legacy_kart_id(app, context):
    session, setup = context
    builder = SetupBuilderView()
    builder._folder_id, builder._folder_path = session.id, session.path
    builder._setup = setup
    builder._populate(setup)
    assert builder._maintenance._state.kart == "chassis:kart11"
    builder.deleteLater()


def test_checklist_saves_with_offline_setup_and_reloads(app, context, monkeypatch):
    session, setup = context
    monkeypatch.setattr("qt.views.setup_builder_qt.db.google_drive_client.audit_user_or_none", lambda **_kwargs: None)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.update_setup", lambda *_args, **_kwargs: pytest.fail("must not upload with pending identity"))
    def immediate(_parent, work, on_done=None, on_error=None):
        result = work()
        if on_done:
            on_done(result)
    monkeypatch.setattr("qt.views.setup_builder_qt.run_async", immediate)
    builder = SetupBuilderView()
    builder._folder_id, builder._folder_path = session.id, session.path
    builder._folder_name = session.name
    builder._setup = setup
    builder._chassis = [Chassis(id="kart11", name="Kart 11")]
    builder._populate(setup)
    builder._maintenance.findChildren(QCheckBox)[0].click()
    records = builder._maintenance.records()
    builder._save()
    loaded = offline_store.load_setups(session.id)[0]
    assert loaded.data[m.CHECKS_KEY] == records
    assert loaded.chassis_id == "kart11"
    assert builder._maintenance.records() == records
    assert not builder.has_unsaved_changes()
    builder.deleteLater()


def test_settings_anchor_interval_changes_disable_and_rename(app, context):
    session, _setup = context
    snapshot = maintenance_store.PolicySnapshot(m.default_policy(), available=True)
    dialog = MaintenanceSettingsDialog(snapshot, [session])
    dialog._start_session.setCurrentIndex(1)
    assert dialog.policy()["start_session_id"] == session.id
    dialog._table.cellWidget(6, 3).setValue(7)
    assert dialog.policy()["items"][6]["id"] == "replace-wheel-nuts"
    assert dialog.policy()["items"][6]["every"] == 7
    dialog._table.cellWidget(6, 0).setChecked(False)
    assert dialog.policy()["items"][6]["enabled"] is False
    dialog._table.item(6, 1).setText("Replace my new hardware")
    assert dialog.policy()["items"][6]["id"] != "replace-wheel-nuts"
    dialog._table.cellWidget(0, 2).setCurrentIndex(1)
    assert dialog._table.cellWidget(0, 3).isEnabled()
    dialog.deleteLater()


def test_settings_keeps_uncached_anchor_and_offline_save_disabled(app, context):
    session, _setup = context
    policy = m.default_policy()
    policy["start_session_id"] = session.id
    dialog = MaintenanceSettingsDialog(maintenance_store.PolicySnapshot(policy), [])
    assert dialog.policy() == policy
    assert not dialog._save.isEnabled()
    dialog.deleteLater()


def test_history_is_readable_without_record_hashes(app, context):
    session, setup = context
    records = m.set_checked({}, m.kart_key(setup), session.id, m.default_policy()["items"][0], True, GoogleUser("Tester", "test@example.test"))
    lines = _maintenance_history_lines({}, records)
    assert len(lines) == 1 and "Completed" in lines[0] and "Track widths" in lines[0]
    assert next(iter(records)) not in lines[0]


def test_responsive_checklist_preserves_records_and_visible_controls(app, context):
    session, setup = context
    card = MaintenanceChecklist()
    card.refresh(m.default_policy(), setup, [session], [setup])
    card.resize(1000, 400)
    card.show()
    app.processEvents()
    card.findChildren(QCheckBox)[0].click()
    records = card.records()
    card.resize(600, 700)
    app.processEvents()
    assert card._columns == 1
    assert len(card.findChildren(QCheckBox)) == 6
    assert card.records() == records
    card.close()
    card.deleteLater()


def test_main_window_wires_maintenance_without_cloud_actions(app, context, monkeypatch):
    from qt.main_window import MainWindow
    for name in ("_connect", "_refresh_offline_catalogs", "_load_team_naming", "_check_for_updates_on_startup", "_navigate"):
        monkeypatch.setattr(MainWindow, name, lambda *_args, **_kwargs: None)
    window = MainWindow()
    calls = []
    monkeypatch.setattr(window._setup_builder, "refresh_maintenance", lambda **kwargs: calls.append(kwargs))
    window._settings.maintenance_changed.emit()
    assert calls == [{"reload_context": True}]
    window._connection_timer.stop()
    window.deleteLater()
