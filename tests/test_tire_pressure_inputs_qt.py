"""Pressure inputs belong to tire corners, not chassis sections."""
import pytest
from PySide6.QtWidgets import QApplication

from database.models import Setup
from qt.views.setup_builder_qt import SetupBuilderView
import setup_fields as sf


@pytest.fixture
def builder(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(SetupBuilderView, "refresh_maintenance", lambda self, **kwargs: None)
    view = SetupBuilderView()
    yield view
    view.deleteLater()
    app.processEvents()


def test_pressure_inputs_only_appear_in_tire_boxes(builder):
    for key in ("front_tire_pressure", "rear_tire_pressure"):
        assert key not in builder._fields
        assert key not in {field.key for field in sf.all_value_fields()}
    for corner in ("fl", "fr", "rl", "rr"):
        for temperature in ("cold", "hot"):
            assert f"{corner}_{temperature}_psi" in builder._fields


def test_corner_pressures_remain_independent_between_runs(builder):
    field = builder._fields["fl_cold_psi"]
    builder._set_field_text(field, "10")
    builder._add_run_btn.click()
    builder._set_field_text(field, "12")
    builder._previous_run_btn.click()
    assert builder._field_text(field) == "10"
    builder._next_run_btn.click()
    assert builder._field_text(field) == "12"


def test_saving_preserves_legacy_pressure_values(builder, monkeypatch):
    saved = []
    def immediate(parent, work, on_done=None, on_error=None):
        result = work()
        if on_done:
            on_done(result)
    def persist(setup, **kwargs):
        saved.append(setup)
        return setup
    monkeypatch.setattr("qt.views.setup_builder_qt.run_async", immediate)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.record_setup_change",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.setup_identity_pending",
                        lambda setup: False)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.update_setup", persist)
    legacy = Setup(id="legacy", name="Pressure regression", data={
        "front_tire_pressure": 11, "rear_tire_pressure": 12,
        "fl_cold_psi": 10, "fl_hot_psi": 14,
    })
    builder._setup = legacy
    builder._folder_id = "test-session"
    builder._populate(legacy)
    builder._set_field_text(builder._fields["fl_cold_psi"], "10.5")
    builder._save()
    assert len(saved) == 1
    assert saved[0].data["front_tire_pressure"] == 11
    assert saved[0].data["rear_tire_pressure"] == 12
    assert saved[0].data["fl_cold_psi"] == 10.5
    assert saved[0].data["fl_hot_psi"] == 14
