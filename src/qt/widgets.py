"""Reusable Qt widgets/helpers."""
from __future__ import annotations

from PySide6.QtCore import QDate, QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCalendarWidget, QComboBox, QCompleter, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QLineEdit, QVBoxLayout,
)


class FirstClickSelectAllFilter(QObject):
    """Select an editable line field once when the user first enters it.

    The mouse event is allowed to finish before selection is applied. A second
    click while the field already owns focus therefore behaves normally and
    lets the user place the caret or adjust the selection without fighting the
    application.
    """

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if (
            isinstance(watched, QLineEdit)
            and watched.isEnabled()
            and not watched.isReadOnly()
        ):
            if (
                event.type() == QEvent.FocusIn
                and event.reason() == Qt.MouseFocusReason
                and not _combo_is_logically_active(watched)
            ):
                QTimer.singleShot(0, watched.selectAll)
            elif (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
                and watched.hasFocus()
                and watched.text()
                and watched.selectedText() == watched.text()
            ):
                # Some Qt styles preserve a select-all range on a plain second
                # click. Put the caret at the actual click instead.
                cursor_position = watched.cursorPositionAt(
                    event.position().toPoint())
                QTimer.singleShot(
                    0,
                    lambda edit=watched, pos=cursor_position:
                    edit.setCursorPosition(pos),
                )
        return super().eventFilter(watched, event)


def install_first_click_select_all(app: QApplication) -> None:
    """Install the shared first-click behavior once for every line editor."""

    if getattr(app, "_xcal_first_click_select_all", None) is not None:
        return
    event_filter = FirstClickSelectAllFilter(app)
    app.installEventFilter(event_filter)
    # QApplication owns the QObject, and this explicit reference also keeps
    # the Python wrapper alive for the duration of the process.
    app._xcal_first_click_select_all = event_filter


def _combo_is_logically_active(edit: QLineEdit) -> bool:
    """Return whether ``edit`` or its completion workflow is still active."""

    combo = edit.parentWidget()
    if not isinstance(combo, QComboBox):
        return False
    if getattr(combo, "_xcal_logically_active", False):
        return True
    completer = combo.completer()
    return bool(completer is not None and completer.popup().isVisible())


class ScrollSafeComboBox(QComboBox):
    """Combo that only changes through an intentional click/keyboard action."""

    def enable_reselect_search(self):
        """Select the old value and reopen all completions when clicked."""
        if not self.isEditable() or getattr(
                self, "_xcal_reselect_search", False):
            return
        self._xcal_reselect_search = True
        self._xcal_logically_active = False
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        mouse_focus = (
            event.type() == QEvent.FocusIn
            and event.reason() == Qt.MouseFocusReason
            and not self._xcal_logically_active
        )
        pre_focus_click = (
            event.type() == QEvent.MouseButtonPress
            and not watched.hasFocus()
            and not self._xcal_logically_active
        )
        if (getattr(self, "_xcal_reselect_search", False)
                and watched is self.lineEdit()
                and (mouse_focus or pre_focus_click)):
            self._xcal_logically_active = True
            # Run after Qt places the caret. The selected value is then
            # replaced by the user's very first keystroke.
            QTimer.singleShot(0, self._reopen_all_completions)
        elif (getattr(self, "_xcal_reselect_search", False)
              and watched is self.lineEdit()
              and event.type() == QEvent.FocusOut):
            QTimer.singleShot(0, self._reset_logical_focus_if_left)
        return super().eventFilter(watched, event)

    def _reset_logical_focus_if_left(self):
        line_edit = self.lineEdit()
        focus = QApplication.focusWidget()
        completer = self.completer()
        popup = completer.popup() if completer is not None else None
        if focus in (self, line_edit):
            return
        if popup is not None and (
                focus is popup or (focus is not None and popup.isAncestorOf(focus))):
            return
        self._xcal_logically_active = False

    def _reopen_all_completions(self):
        line_edit = self.lineEdit()
        completer = self.completer()
        if line_edit is None or completer is None:
            return
        line_edit.selectAll()
        completer.setCompletionPrefix("")
        completer.complete()
        line_edit.setFocus()

    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        # Ignoring propagates the wheel event to the surrounding scroll area.
        event.ignore()


class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """Spin box whose hover area does not trap page scrolling."""

    def textFromValue(self, value):  # noqa: N802 (Qt override)
        """Keep useful decimals without presenting whole stock as ``2.000``."""
        text = f"{value:.{self.decimals()}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        event.ignore()


def make_combo_searchable(
        combo: QComboBox, placeholder: str | None = None,
        allow_custom: bool = False) -> QComboBox:
    """Allow typing any substring to filter a combo's existing options.

    The popup uses contains-matching, case-insensitively. With `placeholder`, the
    field shows grey hint text (no item selected) rather than a real placeholder
    row, so nothing has to be deleted before typing; the caret still opens the
    full list. Set ``allow_custom`` for fields that should also retain text not
    present in the supplied option list.
    """
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    if placeholder:
        combo.lineEdit().setPlaceholderText(placeholder)
    completer = QCompleter(combo.model(), combo)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setMaxVisibleItems(14)
    combo.setCompleter(completer)
    # Keep a Python reference (also useful for tests/introspection).
    combo._xcal_completer = completer

    def _commit_text():
        text = combo.currentText().strip()
        index = combo.findText(text, Qt.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif allow_custom:
            combo.setEditText(text)
        elif placeholder is not None:
            combo.setCurrentIndex(-1)  # revert to the grey hint, not item 0
        elif combo.currentIndex() < 0 and combo.count():
            combo.setCurrentIndex(0)

    def _activate_completion(text):
        index = combo.findText(text, Qt.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)

    completer.activated[str].connect(_activate_completion)
    combo.lineEdit().editingFinished.connect(_commit_text)
    return combo


class DatePickerEdit(QLineEdit):
    """A compact optional ISO-date field that opens a calendar when clicked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Select date…")
        self.setClearButtonEnabled(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to choose a date; use the clear button to remove it")

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._choose_date()

    def _choose_date(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose date")
        layout = QVBoxLayout(dialog)
        calendar = QCalendarWidget(dialog)
        calendar.setGridVisible(True)
        # Week numbers (44, 45, 46…) add noise to a simple date picker.
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        parsed = QDate.fromString(self.text().strip(), Qt.ISODate)
        calendar.setSelectedDate(parsed if parsed.isValid() else QDate.currentDate())
        layout.addWidget(calendar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        clear_button = buttons.addButton("Clear date", QDialogButtonBox.ResetRole)
        clear_button.clicked.connect(lambda: (self.clear(), dialog.reject()))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        calendar.clicked.connect(lambda _date: dialog.accept())

        if dialog.exec() == QDialog.Accepted:
            self.setText(calendar.selectedDate().toString(Qt.ISODate))
