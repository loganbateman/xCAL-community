import pytest
from PySide6.QtWidgets import QApplication

from qt.views.setup_builder_qt import SetupBuilderView
import setup_fields as sf


@pytest.fixture
def builder(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(SetupBuilderView, "refresh_maintenance", lambda self: None)
    view = SetupBuilderView()
    yield view
    view.deleteLater()
    app.processEvents()


def test_run_navigation_is_pinned_and_baseline_is_named(builder):
    assert builder.layout().indexOf(builder._run_toolbar) == 1
    assert not builder._scroll.widget().isAncestorOf(builder._run_toolbar)
    assert "Baseline Run" in builder._run_position.text()
    assert "Run 1" in builder._run_log_heading.text()
    assert not builder._previous_run_btn.isEnabled()
    assert not builder._next_run_btn.isEnabled()
    assert builder._run_toolbar.isAncestorOf(builder._add_run_btn)
    assert builder._run_toolbar.isAncestorOf(builder._remove_run_btn)
    assert not builder._remove_run_btn.isEnabled()
    builder._feedback.setPlainText("Baseline feedback")
    builder._add_run_btn.click()
    assert builder._run_index == 1
    assert builder._run_position.text() == "Run 2 of 2"
    assert builder._remove_run_btn.isEnabled()
    builder._feedback.setPlainText("Second run")
    builder._previous_run_btn.click()
    assert builder._run_index == 0
    assert builder._feedback.toPlainText() == "Baseline feedback"
    assert "Baseline Run" in builder._run_position.text()
    builder._next_run_btn.click()
    assert builder._feedback.toPlainText() == "Second run"
    assert builder._run_log_heading.text() == "Run Log — Run 2"


def test_top_bar_remove_run_keeps_confirmation_and_baseline_protection(builder, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    builder._add_run_btn.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    builder._remove_run_btn.click()
    assert len(builder._runs) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    builder._remove_run_btn.click()
    assert len(builder._runs) == 1
    assert builder._run_index == 0
    assert not builder._remove_run_btn.isEnabled()


def test_run_log_button_centers_notes_editor(builder):
    from PySide6.QtCore import QPoint
    builder.resize(1200, 800)
    builder._content_stack.setCurrentWidget(builder._scroll)
    builder.show()
    QApplication.processEvents()
    builder._run_log_btn.click()
    center = builder._feedback.mapTo(builder._scroll.widget(), QPoint(0, builder._feedback.height() // 2)).y()
    bar = builder._scroll.verticalScrollBar()
    expected = max(bar.minimum(), min(bar.maximum(), center - builder._scroll.viewport().height() // 2))
    assert bar.value() == expected
    assert abs(builder._feedback.mapTo(builder._scroll.viewport(), builder._feedback.rect().center()).y()
               - builder._scroll.viewport().height() // 2) < 5
    builder._set_identity_controls_enabled(False)
    assert not builder._run_log_btn.isEnabled()
    assert not builder._previous_run_btn.isEnabled()
    builder._set_identity_controls_enabled(True)
    assert not builder._previous_run_btn.isEnabled()


def test_baseline_difference_is_not_the_same_as_unsaved_change(builder):
    key = next(iter(builder._fields))
    field = builder._fields[key]
    builder._set_field_text(field, "baseline")
    builder._add_run_btn.click()
    assert not field.property("baselineChanged")
    builder._set_field_text(field, "different")
    assert field.property("baselineChanged")
    builder._initial_state = builder._form_state()
    builder._update_dirty_styles()
    assert field.property("baselineChanged")  # Saving must not clear yellow.
    assert not field.property("dirty")
    builder._previous_run_btn.click()
    assert builder._field_text(field) == "baseline"
    assert not field.property("baselineChanged")
    builder._next_run_btn.click()
    assert field.property("baselineChanged")
    builder._set_field_text(field, "baseline")
    assert not field.property("baselineChanged")


def test_same_name_different_part_revision_is_highlighted(builder):
    key = next(key for key in sf.INVENTORY_FIELD_CATEGORIES if key in builder._fields)
    field = builder._fields[key]
    original = {"item_id": "part", "revision": 1, "name": "Same name"}
    changed = {**original, "revision": 2}
    builder._set_field_text(field, "Same name", original)
    builder._add_run_btn.click()
    builder._set_field_text(field, "Same name", changed)
    builder._update_dirty_styles()
    assert field.property("baselineChanged")
    builder._previous_run_btn.click()
    assert not field.property("baselineChanged")
    assert field._inventory_revision_ref == original


def test_run_log_and_sheet_notes_remain_unhighlighted_but_still_dirty(builder):
    builder._initial_state = builder._form_state()
    builder._feedback.setPlainText("Baseline notes")
    builder._add_run_btn.click()
    builder._feedback.setPlainText("New notes")
    builder._notes.setPlainText("Whole-sheet notes")
    builder._run_fields["laps"].setText("12")
    for edit in (*builder._run_fields.values(), builder._feedback, builder._notes):
        assert not edit.property("baselineChanged")
        assert not edit.property("previousRunChanged")
        assert not edit.property("dirty")
    assert builder.has_unsaved_changes()
    assert not builder._save_status.isHidden()
    builder._previous_run_btn.click()
    assert builder._feedback.toPlainText() == "Baseline notes"
    assert builder._notes.toPlainText() == "Whole-sheet notes"
    assert not builder._feedback.property("baselineChanged")

def test_previous_run_and_baseline_are_independent_comparisons(builder):
    key = next(iter(builder._fields))
    field = builder._fields[key]
    builder._set_field_text(field, "baseline")
    builder._add_run_btn.click()
    builder._set_field_text(field, "run two")
    assert field.property("baselineChanged")
    assert field.property("previousRunChanged")
    builder._add_run_btn.click()
    assert field.property("baselineChanged")
    assert not field.property("previousRunChanged")  # Inherited Run 2 unchanged.
    builder._set_field_text(field, "baseline")
    assert not field.property("baselineChanged")
    assert field.property("previousRunChanged")  # Returned to baseline, unlike Run 2.
    builder._set_field_text(field, "run three")
    assert field.property("baselineChanged")
    assert field.property("previousRunChanged")
    builder._initial_state = builder._form_state()
    builder._update_dirty_styles()
    assert field.property("previousRunChanged")  # Comparison survives a save.
    assert not field.property("dirty")
    builder._go_to_run(0)
    assert not field.property("baselineChanged")
    assert not field.property("previousRunChanged")


def test_previous_run_comparison_includes_part_revision_but_not_feedback(builder):
    key = next(key for key in sf.INVENTORY_FIELD_CATEGORIES if key in builder._fields)
    field = builder._fields[key]
    original = {"item_id": "part", "revision": 1, "name": "Same name"}
    builder._set_field_text(field, "Same name", original)
    builder._feedback.setPlainText("baseline")
    builder._add_run_btn.click()
    builder._set_field_text(field, "Same name", {**original, "revision": 2})
    builder._feedback.setPlainText("run two")
    builder._add_run_btn.click()
    assert not field.property("previousRunChanged")
    builder._set_field_text(field, "Same name", original)
    builder._update_dirty_styles()
    assert field.property("previousRunChanged")
    assert not field.property("baselineChanged")
    builder._feedback.setPlainText("run two")
    assert not builder._feedback.property("previousRunChanged")
    builder._feedback.setPlainText("baseline")
    assert not builder._feedback.property("previousRunChanged")
    assert not builder._feedback.property("baselineChanged")
    for key in ("run_number", "run_date", "run_time"):
        assert not builder._run_fields[key].property("previousRunChanged")


def test_comparison_key_is_pinned_and_names_both_colors(builder):
    from PySide6.QtWidgets import QLabel
    assert builder._run_toolbar.isAncestorOf(builder._run_comparison_key)
    labels = [label.text() for label in builder._run_comparison_key.findChildren(QLabel)]
    assert labels == ["Baseline", "Previous"]
    assert "yellow fill with a red border" in builder._run_comparison_key.toolTip()
    assert builder._run_setup_context.isHidden()
    assert builder._run_toolbar.layout().count() == 1
