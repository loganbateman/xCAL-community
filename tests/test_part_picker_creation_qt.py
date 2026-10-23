from unittest.mock import Mock
import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from database.models import InventoryItem
from qt.views.setup_builder_qt import _PartPickerDialog, SetupBuilderView


def test_create_in_branch_saves_and_selects_new_part(monkeypatch):
    app = QApplication.instance() or QApplication([])
    part = InventoryItem(id="new-cage", category="Cage", name="New cage")
    editor = Mock(result_item=part)
    editor.exec.return_value = QDialog.Accepted
    factory = Mock(return_value=editor)
    save = Mock(return_value=part)
    monkeypatch.setattr("qt.views.parts_qt.PartDialog", factory)
    monkeypatch.setattr("utils.inventory_service.create_inventory_item", save)
    monkeypatch.setattr("qt.views.setup_builder_qt.run_async",
                        lambda parent, work, on_done, on_error: on_done(work()))
    picker = _PartPickerDialog("Cage", [])
    created = Mock()
    picker.partCreated.connect(created)
    picker._search.setText("unrelated search")
    picker._create_part()
    factory.assert_called_once_with("Cage", parent=picker)
    editor._category.setEnabled.assert_called_once_with(False)
    save.assert_called_once_with(part)
    created.assert_called_once_with(part)
    assert picker._list.currentItem().data(Qt.UserRole).id == part.id
    assert picker.selected_item is None  # Still requires Select Part.
    picker.accept()
    assert picker.selected_item == part
    picker.deleteLater()
    app.processEvents()


def test_cancelled_part_creation_does_not_save(monkeypatch):
    app = QApplication.instance() or QApplication([])
    editor = Mock()
    editor.exec.return_value = QDialog.Rejected
    monkeypatch.setattr("qt.views.parts_qt.PartDialog", Mock(return_value=editor))
    save = Mock()
    monkeypatch.setattr("utils.inventory_service.create_inventory_item", save)
    picker = _PartPickerDialog("Cage", [])
    picker._create_part()
    save.assert_not_called()
    assert picker._list.count() == 0
    picker.deleteLater()
    app.processEvents()


def test_failed_part_creation_keeps_picker_usable(monkeypatch):
    app = QApplication.instance() or QApplication([])
    editor = Mock(result_item=InventoryItem(category="Cage", name="New cage"))
    editor.exec.return_value = QDialog.Accepted
    monkeypatch.setattr("qt.views.parts_qt.PartDialog", Mock(return_value=editor))
    monkeypatch.setattr("qt.views.setup_builder_qt.run_async",
                        lambda parent, work, on_done, on_error: on_error("Save failed"))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    picker = _PartPickerDialog("Cage", [])
    picker._create_part()
    assert picker.isEnabled()
    assert not picker._creating
    assert picker._list.count() == 0
    warning.assert_called_once()
    picker.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("baseline", [False, True])
def test_new_identity_parts_can_save_via_shortcut_without_reopening(monkeypatch, baseline):
    app = QApplication.instance() or QApplication([])
    def immediate(parent, work, on_done=None, on_error=None):
        result = work()
        if on_done:
            on_done(result)
    monkeypatch.setattr("qt.views.setup_builder_qt.run_async", immediate)
    def editor(category, **kwargs):
        result = Mock(result_item=InventoryItem(id="new-" + category, category=category,
                                               name="New " + category, revision=1))
        result.exec.return_value = QDialog.Accepted
        return result
    monkeypatch.setattr("qt.views.parts_qt.PartDialog", editor)
    monkeypatch.setattr("utils.inventory_service.create_inventory_item", lambda part: part)
    def create_and_select(picker):
        picker._create_part()
        picker.accept()
        return picker.result()
    monkeypatch.setattr(_PartPickerDialog, "exec", create_and_select)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "information", warning)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.record_setup_change", lambda *args: None)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.setup_identity_pending", lambda setup: False)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.fetch_conflicting_baseline", lambda *args, **kwargs: None)
    saved = []
    def persist(setup):
        setup.id = "saved-setup"
        saved.append(setup)
        return setup
    monkeypatch.setattr("qt.views.setup_builder_qt.db.create_setup", persist)
    builder = SetupBuilderView()
    builder._is_baseline = baseline
    builder._folder_id = "test-session"
    builder._name.setText("New identity regression")
    builder.show()
    app.processEvents()
    for widget, category in ((builder._driver, "Drivers"), (builder._chassis_cb, "Karts"), (builder._track, "Tracks")):
        builder._open_identity_picker(widget, category)
        assert widget.text() == "New " + category
    builder._save_btn.setEnabled(True)
    builder._save_via_shortcut()
    warning.assert_not_called()
    assert len(saved) == 1
    assert saved[0].driver_name == "New Drivers"
    assert saved[0].chassis_name == "New Karts"
    assert saved[0].track == "New Tracks"
    refs = saved[0].data["_identity_part_revisions"]
    assert refs["driver"]["item_id"] == "new-Drivers"
    assert refs["chassis"]["item_id"] == "new-Karts"
    assert refs["track"]["item_id"] == "new-Tracks"
    builder.close()
    builder.deleteLater()
    app.processEvents()


def test_unlisted_typed_identity_still_cannot_save(monkeypatch):
    app = QApplication.instance() or QApplication([])
    warning = Mock()
    persist = Mock()
    monkeypatch.setattr(QMessageBox, "information", warning)
    monkeypatch.setattr("qt.views.setup_builder_qt.db.create_setup", persist)
    builder = SetupBuilderView()
    builder._folder_id = "test-session"
    builder._driver.setText("Not a real inventory record")
    builder._save()
    assert "Driver" in warning.call_args.args[2]
    persist.assert_not_called()
    builder.deleteLater()
    app.processEvents()
