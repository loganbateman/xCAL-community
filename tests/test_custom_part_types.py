from copy import deepcopy
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication, QListWidget, QMessageBox

import inventory_fields
import setup_fields as sf
from utils import team_setup_fields as fields
from qt.views.parts_qt import PartDialog
from qt.views.setup_builder_qt import SetupBuilderView
from qt.views.team_setup_fields_qt import TeamSetupFieldsDialog


@pytest.fixture(autouse=True)
def restore_registry():
    original = deepcopy(sf.CUSTOM_FIELD_DEFINITIONS)
    yield
    fields.apply(original)


def custom_document():
    attribute = dict(key="spec_" + uuid4().hex, label="Offset", kind="number", unit="mm")
    document = fields.add_part_type(None, "Seat brackets", [attribute])
    document, section = fields.add_section(document, "Seat mounting")
    return fields.add_parameter(document, section, "Bracket", kind="part", category="Seat brackets"), section, attribute


def test_custom_definition_roundtrip_and_part_form():
    application = QApplication.instance() or QApplication([])
    document, section, attribute = custom_document()
    assert fields.normalize(document) == document
    fields.apply(document)
    assert "Seat brackets" in inventory_fields.CATEGORIES
    assert inventory_fields.fields_for_category("Seat brackets")[0].key == attribute["key"]
    part = PartDialog("Seat brackets")
    assert part._selected_category() == "Seat brackets"
    builder = SetupBuilderView()
    assert document["fields"][0]["key"] in builder._fields
    assert sf.custom_fields(section)
    part.deleteLater()
    builder.deleteLater()
    application.processEvents()


def test_builtin_names_and_duplicate_attributes_cannot_be_shadowed():
    with pytest.raises(ValueError, match="unique"):
        fields.add_part_type(None, "tires", [])
    document, _section, attribute = custom_document()
    with pytest.raises(ValueError, match="unique"):
        fields.add_part_type(document, "Another type", [attribute, attribute])
    with pytest.raises(ValueError, match="different names"):
        fields.add_section(document, "Front End")


def test_preview_follows_sections_and_new_fields_appear_immediately():
    application = QApplication.instance() or QApplication([])
    document, section, _attribute = custom_document()
    dialog = TeamSetupFieldsDialog(document)
    assert not dialog.findChildren(QListWidget)
    assert "top_pill_position" in dialog._preview_cells or len(dialog._preview_cells) >= 9
    dialog._section.setCurrentIndex(dialog._section.findData(section))
    assert len(dialog._preview_cells) == 1
    key = document["fields"][0]["key"]
    dialog._preview_cells[key].click()
    assert dialog._label.text() == "Bracket"
    assert dialog._label.isEnabled()
    dialog._new_field.click()
    dialog._label.setText("Seat position")
    assert dialog._commit_editor()
    assert len(dialog._preview_cells) == 2
    new_key = dialog.document["fields"][-1]["key"]
    assert new_key in dialog._preview_cells
    dialog._section.setCurrentIndex(dialog._section.findData("rear"))
    assert new_key not in dialog._preview_cells
    dialog.deleteLater()
    application.processEvents()


def test_sheet_preview_can_hide_and_restore_selected_parameter(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    application = QApplication.instance() or QApplication([])
    document, section, _attribute = custom_document()
    dialog = TeamSetupFieldsDialog(document)
    dialog._section.setCurrentIndex(dialog._section.findData(section))
    key = document["fields"][0]["key"]
    dialog._preview_cells[key].click()
    dialog._remove.click()
    assert not dialog.document["fields"][0]["active"]
    assert dialog._remove.text() == "Restore"
    dialog._remove.click()
    assert dialog.document["fields"][0]["active"]
    assert key in dialog._preview_cells
    dialog.deleteLater()
    application.processEvents()


def test_single_save_includes_current_editor_and_builtin_captions(monkeypatch):
    from unittest.mock import Mock
    from test_team_onboarding_qt import immediate
    application = QApplication.instance() or QApplication([])
    document = fields.add_parameter(None, "front", "Front feel")
    key = document["fields"][0]["key"]
    original = sf.FRONT_FIELDS[0]
    dialog = TeamSetupFieldsDialog(document)
    dialog._preview_cells[original.key].click()
    assert dialog._label.text() == original.label
    assert not dialog._kind.isEnabled()
    assert not dialog._remove.isEnabled()
    dialog._label.setText("Team pill position")
    assert dialog._preview_labels[original.key].text() == "Team pill position"
    dialog._preview_cells[key].click()
    assert dialog.document["builtin_overrides"][original.key]["label"] == "Team pill position"
    dialog._label.setText("Steering feedback")
    save = Mock()
    monkeypatch.setattr("qt.views.team_setup_fields_qt.run_async", immediate)
    monkeypatch.setattr(fields, "save", save)
    dialog._save.click()
    saved = save.call_args.args[0]
    assert saved["fields"][0]["label"] == "Steering feedback"
    fields.apply(saved)
    assert sf.display_field(original).label == "Team pill position"
    assert sf.display_field(original).key == original.key
    assert sf.display_field(original).kind == original.kind
    assert original.label != "Team pill position"
    dialog.deleteLater()
    application.processEvents()


def test_original_field_overrides_cannot_change_storage_type():
    original = sf.FRONT_FIELDS[0]
    with pytest.raises(ValueError, match="original value type"):
        fields.normalize({"schema": 1, "fields": [], "builtin_overrides": {
            original.key: {"label": "New label", "kind": "num"}}})


def test_drag_order_survives_definition_roundtrip_and_reaches_setup_sheet():
    application = QApplication.instance() or QApplication([])
    document = fields.add_parameter(None, "rear", "Spring preload", "turns", "num")
    key = document["fields"][0]["key"]
    dialog = TeamSetupFieldsDialog(document)
    dialog._section.setCurrentIndex(dialog._section.findData("rear"))
    original = list(dialog._visible_order)
    dialog._move_field(key, original[0])
    assert dialog._visible_order[0] == key
    assert set(dialog._visible_order) == set(original)
    assert dialog._selected_key == key
    saved = fields.normalize(dialog.document)
    fields.apply(saved)
    assert sf.section_fields("rear")[0].key == key
    builder = SetupBuilderView()
    keys = list(builder._fields)
    assert keys.index(key) < keys.index(original[0])
    dialog._move_field(key, original[1], after=True)
    assert dialog._visible_order.index(key) == dialog._visible_order.index(original[1]) + 1
    dialog.deleteLater()
    builder.deleteLater()
    application.processEvents()


def test_order_rejects_cross_section_and_duplicate_keys():
    for keys in ([sf.FRONT_FIELDS[0].key], [sf.REAR_FIELDS[0].key] * 2):
        with pytest.raises(ValueError, match="same section"):
            fields.normalize({"schema": 1, "fields": [], "field_order": {"rear": keys}})


def test_drag_target_accepts_only_same_dialog_and_section():
    from PySide6.QtCore import QMimeData, QPointF
    from unittest.mock import Mock
    from qt.parameter_drag import ParameterDragButton
    application = QApplication.instance() or QApplication([])
    callback = Mock()
    source = ParameterDragButton("Source", "source", (1, "rear"), callback)
    target = ParameterDragButton("Target", "target", (1, "rear"), callback)
    mime = QMimeData()
    mime.setData(source.MIME, b"source")
    event = Mock()
    event.source.return_value = source
    event.mimeData.return_value = mime
    event.position.return_value = QPointF(0, 0)
    target.dropEvent(event)
    assert source._pending_move == ("source", "target", False)
    callback.assert_not_called()  # Reflow waits for the native drag to end.
    event.accept.assert_called_once()
    source.scope = (2, "front")
    target.dropEvent(event)
    event.ignore.assert_called_once()
    source.deleteLater()
    target.deleteLater()
    application.processEvents()
