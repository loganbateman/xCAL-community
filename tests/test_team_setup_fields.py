from copy import deepcopy
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import setup_fields as sf
from database.models import Setup
from qt.views.setup_builder_qt import SetupBuilderView, _run_history_lines
from utils import team_setup_fields as fields


@pytest.fixture(autouse=True)
def restore_fields():
    original = deepcopy(sf.CUSTOM_FIELD_DEFINITIONS)
    yield
    sf.CUSTOM_FIELD_DEFINITIONS = original


def app():
    return QApplication.instance() or QApplication([])


def test_custom_parameters_keep_builtin_fields_and_stable_ids():
    before = {field.key for field in sf.all_value_fields()}
    document = fields.add_parameter(None, "front", "Steering feel", kind="text")
    fields.apply(document)
    assert {field.key for field in sf.all_value_fields()} >= before
    field = sf.custom_fields("front")[0]
    assert field.label == "Steering feel"
    assert field in sf.section_fields("front")
    assert fields.normalize(document) == document


def test_duplicate_labels_and_unsupported_sections_are_rejected():
    document = fields.add_parameter(None, "front", "Grip")
    with pytest.raises(ValueError, match="different labels"):
        fields.add_parameter(document, "front", "grip")
    with pytest.raises(ValueError, match="section"):
        fields.add_parameter(document, "unsupported", "Grip")
    assert len(fields.add_parameter(document, "rear", "Grip")["fields"]) == 2


def test_custom_fields_cannot_shadow_original_storage_keys():
    document = fields.add_parameter(None, "rear", "Axle notes")
    document["fields"][0]["key"] = "axle"
    with pytest.raises(ValueError, match="stable"):
        fields.normalize(document)


def test_extra_field_rendering_and_independent_run_values():
    app()
    document = fields.add_parameter(None, "rear", "Rear grip", "%", "num")
    fields.apply(document)
    key = document["fields"][0]["key"]
    builder = SetupBuilderView()
    assert key in builder._fields
    builder._set_field_text(builder._fields[key], "40")
    builder._add_run()
    builder._set_field_text(builder._fields[key], "45")
    builder._go_to_run(0)
    assert builder._field_text(builder._fields[key]) == "40"
    builder._go_to_run(1)
    assert builder._field_text(builder._fields[key]) == "45"
    state = builder._form_state()
    assert dict(state["fields"])[key] == "40"
    builder.deleteLater()


def test_hiding_parameter_preserves_saved_run_values_and_history_labels():
    app()
    document = fields.add_parameter(None, "front", "Front grip", "units")
    key = document["fields"][0]["key"]
    document["fields"][0]["active"] = False
    fields.apply(document)
    builder = SetupBuilderView()
    setup = Setup(data={key: "baseline", sf.RUNS_KEY: [
        {"id": "one"}, {"id": "two", "setup": {"values": {key: "run two"}, "inventory_refs": {}}}]})
    builder._setup = setup
    builder._populate(setup)
    assert key not in builder._fields
    builder._go_to_run(1)
    assert builder._serialized_runs()[1]["setup"]["values"][key] == "run two"
    assert builder._baseline_run_setup["values"][key] == "baseline"
    before = [{"id": "two", "setup": {"values": {key: "old"}}}]
    after = [{"id": "two", "setup": {"values": {key: "new"}}}]
    assert "Front grip (units)" in _run_history_lines(before, after)[0]
    builder.deleteLater()


def test_numeric_custom_fields_reject_nan_and_bad_run_values(monkeypatch):
    app()
    document = fields.add_parameter(None, "engine", "Engine load", "%", "num")
    fields.apply(document)
    key = document["fields"][0]["key"]
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    builder = SetupBuilderView()
    builder._is_baseline = True
    builder._set_field_text(builder._fields[key], "nan")
    builder._save()
    assert warning.call_count == 1
    assert "finite number" in warning.call_args.args[2]
    assert not builder.is_saving()
    builder.deleteLater()


def test_new_sheet_does_not_inherit_hidden_parameters_from_previous_sheet():
    app()
    document = fields.add_parameter(None, "tires", "Tire batch")
    key = document["fields"][0]["key"]
    document["fields"][0]["active"] = False
    fields.apply(document)
    builder = SetupBuilderView()
    builder._setup = Setup(data={key: "old private sheet"})
    builder._populate(builder._setup)
    builder._setup = Setup(data={})
    builder._populate(builder._setup)
    assert key not in builder._baseline_run_setup["values"]
    builder.deleteLater()
