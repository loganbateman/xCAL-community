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
    fields.apply(original)


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

def test_editing_parameter_preserves_storage_key_and_rejects_duplicates():
    document = fields.add_parameter(None, "front", "Original", "mm", "num")
    key = document["fields"][0]["key"]
    document = fields.edit_parameter(document, key, "rear", "Renamed", "in", "num")
    assert document["fields"][0]["key"] == key
    assert document["fields"][0]["section"] == "rear"
    assert document["fields"][0]["label"] == "Renamed"
    document = fields.add_parameter(document, "rear", "Other")
    with pytest.raises(ValueError, match="different labels"):
        fields.edit_parameter(document, key, "rear", "Other")
    assert document["fields"][0]["label"] == "Renamed"


def test_custom_parts_picker_and_hiding_preserve_values_and_links():
    app()
    document = fields.add_parameter(None, "rear", "Test cage", kind="part", category="Cage")
    fields.apply(document)
    key = document["fields"][0]["key"]
    assert sf.INVENTORY_FIELD_CATEGORIES[key] == "Cage"
    builder = SetupBuilderView()
    reference = {"item_id": "cage", "revision": 3, "name": "Cage"}
    builder._set_field_text(builder._fields[key], "Cage", reference)
    builder._add_run()
    builder._set_field_text(builder._fields[key], "Cage", {**reference, "revision": 4})
    builder._update_dirty_styles()
    assert builder._fields[key].property("baselineChanged")
    runs = builder._serialized_runs()
    builder.deleteLater()
    document["fields"][0]["active"] = False
    fields.apply(document)
    assert key not in sf.INVENTORY_FIELD_CATEGORIES
    hidden = SetupBuilderView()
    setup = Setup(data={key: "Cage", "_inventory_part_revisions": {key: reference}, sf.RUNS_KEY: runs})
    hidden._setup = setup
    hidden._populate(setup)
    assert key not in hidden._fields
    assert hidden._baseline_run_setup["values"][key] == "Cage"
    assert hidden._baseline_run_setup["inventory_refs"][key] == reference
    assert hidden._serialized_runs()[1][sf.RUN_SETUP_KEY]["inventory_refs"][key]["revision"] == 4
    hidden.deleteLater()


def test_settings_dialog_edits_selected_field_and_adds_parts_picker():
    from qt.views.team_setup_fields_qt import TeamSetupFieldsDialog
    app()
    document = fields.add_parameter(None, "front", "Original")
    key = document["fields"][0]["key"]
    dialog = TeamSetupFieldsDialog(document)
    dialog._select_key(key)
    dialog._label.setText("Updated")
    assert dialog._commit_editor()
    assert dialog.document["fields"][0]["key"] == key
    assert dialog.document["fields"][0]["label"] == "Updated"
    dialog._section.setCurrentIndex(dialog._section.findData("rear"))
    dialog._new_field.click()
    dialog._label.setText("Rear consumable")
    dialog._kind.setCurrentIndex(dialog._kind.findData("part"))
    dialog._category.setCurrentText("General Consumables")
    assert dialog._commit_editor()
    assert len(dialog.document["fields"]) == 2
    assert dialog.document["fields"][1]["category"] == "General Consumables"
    dialog.deleteLater()


def test_invalid_parts_category_cannot_be_saved():
    with pytest.raises(ValueError, match="existing Parts category"):
        fields.add_parameter(None, "front", "Invalid part", kind="part", category="Unregistered")

def test_main_window_applies_new_fields_without_discarding_active_edits(monkeypatch):
    from qt.main_window import MainWindow
    app()
    for name in ("_connect", "_refresh_offline_catalogs", "_load_team_naming", "_check_for_updates_on_startup", "_navigate"):
        monkeypatch.setattr(MainWindow, name, lambda *_args, **_kwargs: None)
    if hasattr(fields, "cached"):
        monkeypatch.setattr(fields, "cached", lambda: fields.normalize(None))
    else:
        monkeypatch.setattr(fields, "load", lambda: fields.normalize(None))
    monkeypatch.setattr("database.client.fetch_app_doc", lambda _key: None)
    window = MainWindow()
    window._stack.setCurrentWidget(window._settings)
    document = fields.add_parameter(None, "rear", "New field")
    assert window._setup_parameters_changed(document)
    key = document["fields"][0]["key"]
    assert key in window._setup_builder._fields
    current = window._setup_builder
    window._stack.setCurrentWidget(current)
    next_document = fields.add_parameter(document, "front", "Another field")
    assert not window._setup_parameters_changed(next_document)
    assert window._setup_builder is current
    assert sf.CUSTOM_FIELD_DEFINITIONS == document
    window._connection_timer.stop()
    window._stack.setCurrentWidget(window._settings)
    window.close()
    window.deleteLater()
