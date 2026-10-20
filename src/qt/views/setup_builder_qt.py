"""Full kart Setup Builder (Qt).

Ports the existing 79-field builder without changing its storage model:
all detailed values remain in Setup.data (JSONB), so existing setups and
baselines load unchanged. The layout is the unified top-down kart view:
front/axle bands, four tire corners around the kart image, then rear/engine/
tires, session feedback, and notes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from uuid import uuid4

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QComboBox, QPlainTextEdit, QMessageBox,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox, QCheckBox,
    QProgressBar, QStackedWidget,
)

import config
import database.client as db
import offline_store
import maintenance
import maintenance_store
import cache
from database.models import Setup
import setup_fields as sf
from utils import google_drive_client
from qt.workers import run_async
from qt.widgets import DatePickerEdit, ScrollSafeComboBox
from qt.maintenance_checklist import MaintenanceChecklist
from PIL import Image, ImageQt

NONE = "\u2014 None \u2014"

_AUDIT_LABELS = {
    maintenance.CHECKS_KEY: "Pre-session Checks / Maintenance",
    "name": "Setup Name",
    "track": "Track",
    "driver": "Driver",
    "chassis": "Kart / Chassis",
    "notes": "Notes",
    "drive_folder_path": "Session",
    "driver_feedback": "Driver Feedback",
    "runs": "Runs & Session Notes",
    "_inventory_part_revisions": "Linked Part Revisions",
    "_identity_part_revisions": "Linked Identity Revisions",
}
_AUDIT_LABELS.update({field.key: field.display_label
                      for field in sf.all_value_fields()})


def _audit_field_label(key):
    return next((item["label"] + (f" ({item['unit']})" if item["unit"] else "")
                 for item in sf.CUSTOM_FIELD_DEFINITIONS["fields"] if item["key"] == key),
                _AUDIT_LABELS.get(key, key.replace("_", " ").title()))


def _audit_value(value) -> str:
    if value in (None, ""):
        return "\u2014"
    if isinstance(value, dict):
        return ", ".join(
            f"{str(key).replace('_', ' ').title()}: {_audit_value(item)}"
            for key, item in value.items()) or "\u2014"
    if isinstance(value, list):
        return f"{len(value)} item{'s' if len(value) != 1 else ''}"
    return str(value)


def _run_name(run: dict, position: int) -> str:
    number = str(run.get("run_number") or "").strip()
    timestamp = " ".join(filter(None, (
        str(run.get("run_date") or "").strip(),
        str(run.get("run_time") or "").strip(),
    )))
    name = f"Run {number}" if number else f"Run {position + 1}"
    return f"{name} ({timestamp})" if timestamp else name


def _maintenance_history_lines(before, after) -> list[str]:
    old = before if isinstance(before, dict) else {}
    new = after if isinstance(after, dict) else {}
    lines = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        event = new.get(key) or old.get(key)
        if not isinstance(event, dict):
            continue
        state = "Completed" if event.get("checked") and key in new else "Unchecked"
        label = event.get("label") or "Maintenance item"
        timestamp = event.get("completed_at") or event.get("recorded_at")
        when = _SetupHistoryDialog._format_time(timestamp) if timestamp else ""
        lines.append(f"{label}: {state}" + (f" · {when}" if when else ""))
    return lines


def _run_history_lines(before, after) -> list[str]:
    """Describe a run-log revision without exposing JSON or internal IDs."""
    old = before if isinstance(before, list) else []
    new = after if isinstance(after, list) else []
    old_by_id = {
        item.get("id"): (index, item) for index, item in enumerate(old)
        if isinstance(item, dict) and item.get("id")
    }
    new_by_id = {
        item.get("id"): (index, item) for index, item in enumerate(new)
        if isinstance(item, dict) and item.get("id")
    }
    lines = []
    for run_id, (index, run) in new_by_id.items():
        if run_id not in old_by_id:
            lines.append(f"Added {_run_name(run, index)}")
            continue
        _old_index, previous = old_by_id[run_id]
        for key in sorted(set(previous) | set(run)):
            if key == "id" or previous.get(key) == run.get(key):
                continue
            if key == sf.RUN_SETUP_KEY:
                old_values = (previous.get(key) or {}).get("values", {})
                new_values = (run.get(key) or {}).get("values", {})
                for field in sorted(set(old_values) | set(new_values)):
                    if old_values.get(field) != new_values.get(field):
                        lines.append(
                            f"{_run_name(run, index)} · "
                            f"{_audit_field_label(field)}: "
                            f"{_audit_value(old_values.get(field))} → "
                            f"{_audit_value(new_values.get(field))}")
                continue
            label = ("Session Notes / Driver Feedback"
                     if key == sf.RUN_NOTES_KEY else _audit_field_label(key))
            lines.append(
                f"{_run_name(run, index)} \u00b7 {label}: "
                f"{_audit_value(previous.get(key))} \u2192 "
                f"{_audit_value(run.get(key))}")
    for run_id, (index, run) in old_by_id.items():
        if run_id not in new_by_id:
            lines.append(f"Removed {_run_name(run, index)}")
    if not lines and old != new:
        lines.append(
            f"Run log changed from {len(old)} to {len(new)} "
            f"run{'s' if len(new) != 1 else ''}")
    return lines


def _split_legacy_run_timestamp(run: dict) -> dict:
    """Split the short-lived combined timestamp format without losing data."""
    value = str(run.get("run_date") or "").strip()
    if run.get("run_time") or not value:
        return run
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return run
    run["run_date"] = parsed.strftime("%Y-%m-%d")
    run["run_time"] = parsed.strftime("%I:%M %p").lstrip("0")
    return run


class _SetupHistoryDialog(QDialog):
    """Read-only, Google-attributed setup field history."""

    def __init__(self, setup: Setup, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setup History")
        self.resize(760, 580)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)

        title = QLabel(f"Setup History \u00b7 {setup.name or 'Setup'}")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "Editor names and emails come from each person's Google sign-in.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        history_layout = QVBoxLayout(body)
        history_layout.setContentsMargins(4, 8, 8, 8)
        history_layout.setSpacing(10)

        entries = list(reversed(db.setup_change_log(setup)))
        if not entries:
            message = "No detailed setup changes have been recorded yet."
            if setup.created_by:
                message += f" Originally created by {setup.created_by}."
            empty = QLabel(message)
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            history_layout.addWidget(empty)

        for entry in entries:
            card = QFrame()
            card.setObjectName("Card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 11, 14, 12)
            action = "Created" if entry.get("action") == "created" else "Updated"
            actor = entry.get("actor_name") or "Unknown editor"
            header = QLabel(
                f"{action} by {actor}  \u00b7  "
                f"{self._format_time(entry.get('changed_at'))}")
            header.setObjectName("CardTitle")
            card_layout.addWidget(header)
            email = entry.get("actor_email") or ""
            if email:
                account = QLabel(email)
                account.setObjectName("CardSub")
                account.setTextInteractionFlags(Qt.TextSelectableByMouse)
                card_layout.addWidget(account)

            for key, change in (entry.get("changes") or {}).items():
                if not isinstance(change, dict):
                    continue
                if key in (sf.RUNS_KEY, maintenance.CHECKS_KEY):
                    section = QLabel(_AUDIT_LABELS[key])
                    section.setObjectName("CardTitle")
                    card_layout.addWidget(section)
                    formatter = (_run_history_lines if key == sf.RUNS_KEY
                                 else _maintenance_history_lines)
                    for line in formatter(
                            change.get("before"), change.get("after")):
                        detail = QLabel(f"\u2022 {line}")
                        detail.setWordWrap(True)
                        detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
                        card_layout.addWidget(detail)
                    continue
                base_key = key.removesuffix("__part_revision")
                label = _audit_field_label(base_key)
                if key.endswith("__part_revision"):
                    label += " Part Revision"
                detail = QLabel(
                    f"{label}:  {_audit_value(change.get('before'))}  \u2192  "
                    f"{_audit_value(change.get('after'))}")
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
                card_layout.addWidget(detail)
            history_layout.addWidget(card)

        history_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _format_time(value) -> str:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%b %d, %Y at %I:%M %p")
        except (TypeError, ValueError):
            return str(value or "Unknown time")


class _SetupOptionsDialog(QDialog):
    """One tidy home for secondary setup-sheet actions."""

    def __init__(self, builder, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setup Settings")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("Setup Settings")
        title.setObjectName("H2")
        root.addWidget(title)
        destination = builder._folder_path or builder._folder_name
        context = QLabel(
            f"Current destination: {destination or 'Not selected'}")
        context.setObjectName("Muted")
        context.setWordWrap(True)
        root.addWidget(context)

        def add_action(label, description, callback, enabled=True):
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 11, 14, 11)
            copy = QVBoxLayout()
            heading = QLabel(label)
            heading.setObjectName("CardTitle")
            copy.addWidget(heading)
            hint = QLabel(description)
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            copy.addWidget(hint)
            row.addLayout(copy, 1)
            button = QPushButton("Open\u2026")
            button.setObjectName("Ghost")
            button.setEnabled(enabled)
            button.clicked.connect(
                lambda _checked=False: self._launch(callback))
            row.addWidget(button)
            root.addWidget(card)
            return button

        self.load_button = add_action(
            "Load setup values",
            "Search any saved session setup or Parts baseline, then fill empty "
            "fields or overwrite the current setup values.",
            builder._copy_from_setup,
        )
        self.destination_button = add_action(
            "Change destination",
            "Move where this setup will be saved by choosing another session.",
            builder._choose_destination,
            enabled=not builder._is_baseline,
        )
        count = db.setup_change_count(builder._setup)
        self.history_button = add_action(
            f"Revision history ({count})" if count else "Revision history",
            "See readable changes, timestamps, and the Google account that "
            "made each revision.",
            builder._show_history,
            enabled=bool(builder._setup and builder._setup.id),
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _launch(self, callback):
        self.accept()
        QTimer.singleShot(0, callback)


class _PartPickerEdit(QLineEdit):
    """Click-to-open inventory field that does not steal the scroll wheel."""

    pickerRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click to choose an inventory part…")
        self.setCursor(Qt.PointingHandCursor)
        self._inventory_revision_ref = None

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.pickerRequested.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Space):
            self.pickerRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # Combo-compatible aliases keep old setup/baseline loading code readable.
    def currentText(self):  # noqa: N802
        return self.text()

    def setCurrentText(self, value):  # noqa: N802
        self.setText("" if value in (None, NONE) else str(value))


class _PartPickerCard(QFrame):
    """Persistent visual state for one option in the Parts picker."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("PartPickerCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        self._label = QLabel(text)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self._label)
        self.set_state()

    def set_text(self, text):
        self._label.setText(text)

    def text(self):
        return self._label.text()

    def set_state(self, current=False, new_selection=False):
        if current:
            background, border = "#4c2930", "#a01729"
        elif new_selection:
            background, border = "#237a49", "#72d69b"
        else:
            background, border = "transparent", "transparent"
        self.setStyleSheet(
            "QFrame#PartPickerCard {"
            f"background-color:{background}; border:2px solid {border};"
            "border-radius:8px; color:white;"
            "}")


class _PartPickerDialog(QDialog):
    """Search, sort, and deliberately select active or archived inventory."""

    partCreated = Signal(object)

    def __init__(self, category, items, current_text="", current_ref=None,
                 parent=None, allow_custom=True):
        super().__init__(parent)
        self.setWindowTitle(f"Choose {category}")
        self.resize(720, 560)
        self._category = category
        self._items = list(items)
        self._current_ref = current_ref or {}
        self._initial_text = current_text or ""
        self._candidate_id = None
        self.selected_item = None
        self.custom_text = None
        self._allow_custom = allow_custom
        self._creating = False

        root = QVBoxLayout(self)
        title = QLabel(f"Choose from {category}")
        title.setObjectName("H2")
        heading = QHBoxLayout()
        heading.addWidget(title, 1)
        self._new_part_btn = QPushButton("+ New part")
        self._new_part_btn.setObjectName("Ghost")
        self._new_part_btn.clicked.connect(self._create_part)
        heading.addWidget(self._new_part_btn)
        root.addLayout(heading)
        hint = QLabel(
            "Search and sort the Parts inventory. Archived parts are hidden by "
            "default, but can be shown and deliberately selected.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        controls = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search parts…")
        self._search.textChanged.connect(self._refresh)
        controls.addWidget(self._search, 1)
        self._sort = ScrollSafeComboBox()
        self._sort.addItems([
            "Name A–Z", "Name Z–A", "Newest first", "Highest revision",
            "Quantity high–low",
        ])
        self._sort.currentIndexChanged.connect(self._refresh)
        controls.addWidget(self._sort)
        self._archived = QCheckBox("Show archived")
        self._archived.toggled.connect(self._refresh)
        if any(
                item.id == self._current_ref.get("item_id")
                and item.archived_at for item in self._items):
            self._archived.blockSignals(True)
            self._archived.setChecked(True)
            self._archived.blockSignals(False)
        controls.addWidget(self._archived)
        root.addLayout(controls)

        self._list = QListWidget()
        self._list.setObjectName("SetupPickerList")
        self._list.setStyleSheet(
            "QListWidget#SetupPickerList::item:selected {"
            "background-color:transparent; border:none; color:white;"
            "}")
        self._list.itemSelectionChanged.connect(self._selection_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self._list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        self._select_btn = buttons.button(QDialogButtonBox.Ok)
        self._select_btn.setText("Select Part")
        self._select_btn.setEnabled(False)
        self._custom_btn = buttons.addButton(
            "Use typed value", QDialogButtonBox.ActionRole)
        self._custom_btn.setToolTip(
            "Use the search text as a one-off value not linked to inventory")
        self._custom_btn.clicked.connect(self._use_custom)
        self._custom_btn.setVisible(allow_custom)
        self._clear_btn = buttons.addButton(
            "Clear selection", QDialogButtonBox.ResetRole)
        self._clear_btn.clicked.connect(self._clear_selection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _create_part(self):
        from qt.views.parts_qt import PartDialog, _create_inventory_item
        if self._creating:
            return
        dialog = PartDialog(self._category, parent=self)
        # This shortcut creates inside the branch being browsed.
        dialog._category.setEnabled(False)
        if dialog.exec() != QDialog.Accepted or not dialog.result_item:
            return
        self._creating = True
        self.setEnabled(False)

        def done(part):
            self._creating = False
            self.setEnabled(True)
            self._items.append(part)
            self._candidate_id = part.id
            self._search.clear()
            self._refresh()
            if self._list.currentItem():
                self._list.scrollToItem(self._list.currentItem())
            self.partCreated.emit(part)

        def failed(message):
            self._creating = False
            self.setEnabled(True)
            QMessageBox.warning(self, "Could not create part", message)

        run_async(self, lambda: _create_inventory_item(dialog.result_item),
                  on_done=done, on_error=failed)

    def reject(self):
        if not self._creating:
            super().reject()

    @staticmethod
    def _quantity_text(item):
        quantity = float(item.quantity or 0)
        return str(int(quantity)) if quantity.is_integer() else f"{quantity:g}"

    def _is_current(self, part):
        current_id = self._current_ref.get("item_id")
        if current_id:
            return part.id == current_id
        current_name = (
            self._current_ref.get("name") or self._initial_text
        ).strip().casefold()
        return bool(
            current_name
            and (part.name or "").strip().casefold() == current_name)

    def _entry_text(self, part, new_selection=False):
        marker = (
            " (Currently Selected)" if self._is_current(part)
            else " (New Selection)" if new_selection
            else "")
        details = [
            f"Rev {int(part.revision or 1)}",
            f"Qty {self._quantity_text(part)}",
            (part.status or "").replace("_", " ").title(),
        ]
        identity = " ".join(filter(None, (part.manufacturer, part.model)))
        if identity:
            details.insert(0, identity)
        if part.archived_at:
            details.append("ARCHIVED")
        return f"{part.name}{marker}\n" + "  ·  ".join(details)

    def _refresh(self, *_args):
        self._list.blockSignals(True)
        self._list.clear()
        needle = self._search.text().strip().casefold()
        show_archived = self._archived.isChecked()
        visible = [
            item for item in self._items
            if (show_archived or not item.archived_at)
            and (not needle or needle in " ".join(filter(None, (
                item.name, item.manufacturer, item.model,
                item.part_number))).casefold())
        ]
        sort_index = self._sort.currentIndex()
        if sort_index == 1:
            visible.sort(
                key=lambda item: (item.name or "").casefold(), reverse=True)
        elif sort_index == 2:
            visible.sort(key=lambda item: item.updated_at or item.created_at or "",
                         reverse=True)
        elif sort_index == 3:
            visible.sort(key=lambda item: int(item.revision or 1), reverse=True)
        elif sort_index == 4:
            visible.sort(key=lambda item: float(item.quantity or 0), reverse=True)
        else:
            visible.sort(key=lambda item: (item.name or "").casefold())

        # Opening a picker should show the category, not filter it down to the
        # current value. Pin that current value to the top for quick context.
        current = next((item for item in visible if self._is_current(item)), None)
        if current is not None:
            visible.remove(current)
            visible.insert(0, current)

        candidate_row = -1
        for row, part in enumerate(visible):
            text = self._entry_text(part)
            # The card is the only visual text layer. Giving the underlying
            # QListWidgetItem its own display text draws a second copy beneath
            # transparent cards and makes ordinary rows look scrambled.
            entry = QListWidgetItem()
            entry.setData(Qt.UserRole, part)
            entry.setData(Qt.AccessibleTextRole, text)
            entry.setToolTip(text.replace("\n", " · "))
            entry.setSizeHint(QSize(620, 70))
            self._list.addItem(entry)
            self._list.setItemWidget(entry, _PartPickerCard(text))
            if part.id and part.id == self._candidate_id:
                candidate_row = row
        if candidate_row >= 0:
            self._list.setCurrentRow(candidate_row)
        else:
            self._list.setCurrentItem(None)
        self._list.blockSignals(False)
        self._apply_selection_styles()
        self._custom_btn.setEnabled(
            self._allow_custom and bool(self._search.text().strip()))

    def _selection_changed(self):
        selected = self._list.currentItem()
        part = selected.data(Qt.UserRole) if selected else None
        self._candidate_id = part.id if part else None
        self._apply_selection_styles()

    def _apply_selection_styles(self):
        selected = self._list.currentItem()
        for row in range(self._list.count()):
            entry = self._list.item(row)
            part = entry.data(Qt.UserRole)
            text = self._entry_text(
                part,
                new_selection=(
                    entry is selected and not self._is_current(part)))
            entry.setData(Qt.AccessibleTextRole, text)
            entry.setToolTip(text.replace("\n", " · "))
            card = self._list.itemWidget(entry)
            if isinstance(card, _PartPickerCard):
                card.set_text(text)
                card.set_state(
                    current=self._is_current(part),
                    new_selection=(
                        entry is selected and not self._is_current(part)))
        self._select_btn.setEnabled(selected is not None)

    def _use_custom(self):
        text = self._search.text().strip()
        if not text:
            return
        self.custom_text = text
        super().accept()

    def _clear_selection(self):
        self.custom_text = ""
        super().accept()

    def accept(self):
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_item = item.data(Qt.UserRole)
        super().accept()

# Session-specific readings that must never be carried over when prefilling
# from a baseline or copying another setup.
_NO_PREFILL_KEYS = {
    maintenance.CHECKS_KEY,
    "air_temp", "track_temp", "run_date", "run_number", "fastest_lap",
    "session_number", "session_length", "laps", "driver_feedback",
    sf.RUNS_KEY,
}


def _baseline_name(kart: str | None, track: str | None) -> str:
    """Build a readable baseline identity from its Kart and Track."""
    scope = " · ".join(
        value.strip() for value in (kart or "", track or "")
        if value and value.strip())
    return f"{scope} Baseline" if scope else "Baseline Setup"


class _CopySetupDialog(QDialog):
    """Searchable setup picker; avoids the unstable giant combo popup."""

    def __init__(self, setups, label_fn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copy from setup")
        self.resize(720, 520)
        self._setups = setups
        self._label_fn = label_fn
        self.selected_setup = None
        self.overwrite = False

        root = QVBoxLayout(self)
        title = QLabel("Copy values from another setup")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "Search any Parts baseline or saved session setup, select one, then choose "
            "whether to fill empty setup values or overwrite existing setup "
            "values. The destination, name, and driver stay unchanged.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search setup, driver, chassis, track\u2026")
        self._search.textChanged.connect(self._filter)
        root.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("SetupPickerList")
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self._list, 1)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Copy mode:"))
        self._mode = ScrollSafeComboBox()
        self._mode.addItems([
            "Fill empty setup values only",
            "Overwrite setup values (preserve identity)",
        ])
        mode_row.addWidget(self._mode)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._filter("")

    def _filter(self, query):
        self._list.clear()
        needle = query.strip().lower()
        for setup in self._setups:
            label = self._label_fn(setup)
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, setup)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def accept(self):
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_setup = item.data(Qt.UserRole)
        self.overwrite = self._mode.currentIndex() == 1
        super().accept()


class _DestinationDialog(QDialog):
    """Searchable picker for the session folder a setup should be saved into."""

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose destination session")
        self.resize(680, 500)
        self._folders = folders
        self.selected_folder = None

        root = QVBoxLayout(self)
        title = QLabel("Save this setup into\u2026")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel("Pick the session folder this setup belongs to.")
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search session or season\u2026")
        self._search.textChanged.connect(self._filter)
        root.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("SetupPickerList")
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._filter("")

    @staticmethod
    def _label(folder) -> str:
        path = folder.path or folder.name
        return f"{path}  \u00b7  {len(folder.xrks)} xrk" if folder.xrks else path

    def _filter(self, query):
        self._list.clear()
        needle = query.strip().lower()
        for folder in self._folders:
            label = self._label(folder)
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, folder)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def accept(self):
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_folder = item.data(Qt.UserRole)
        super().accept()


def _detect_track(*texts: str) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    if "kaminsky" in blob:
        return "Kaminsky"
    if "purdue" in blob or "pgp" in blob:
        return "Purdue GP"
    return None


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _detect_date(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        match = _DATE_RE.search(text)
        if match:
            return match.group(0)
    return None


class _KartOverlay(QWidget):
    """Large kart image with the four tire panels overlaid at wheel positions."""

    def __init__(self, builder, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(660)

        self._image = QLabel(self)
        self._image.setAlignment(Qt.AlignCenter)
        self._pixmap = None
        if config.KART_TOP_PNG.exists():
            # Crop transparent padding from the source so "560px wide" means the
            # visible kart, not a small kart surrounded by invisible margins.
            image = Image.open(config.KART_TOP_PNG).convert("RGBA")
            bbox = image.getbbox()
            if bbox:
                image = image.crop(bbox)
            self._pixmap = QPixmap.fromImage(ImageQt.ImageQt(image))

        self._cards = {
            "fl": builder._corner_card("fl"),
            "fr": builder._corner_card("fr"),
            "rl": builder._corner_card("rl"),
            "rr": builder._corner_card("rr"),
        }
        for code, card in self._cards.items():
            card.setParent(self)
            card.setObjectName("CornerCard")
            is_front = sf.CORNERS[code][2]
            accent = "#4a5568" if is_front else "#6b4a3a"
            # Slight opacity lets the kart/wheel relationship remain visible
            # where the field panel overlaps the image.
            card.setStyleSheet(
                f"QFrame#CornerCard {{"
                "background-color: rgba(52,54,59,238);"
                f"border:2px solid {accent}; border-radius:10px;"
                "}")
            card.show()

        self._image.lower()

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        w, h = self.width(), self.height()
        image_size = min(560, max(430, int(w * 0.52)))
        image_x = (w - image_size) // 2
        image_y = (h - image_size) // 2
        self._image.setGeometry(image_x, image_y, image_size, image_size)
        if self._pixmap is not None:
            self._image.setPixmap(self._pixmap.scaled(
                image_size, image_size, Qt.KeepAspectRatio,
                Qt.SmoothTransformation))

        # Panels intentionally overlap the image by ~70-100px. Their vertical
        # centers line up with the image's front/rear tire centers.
        card_w = min(475, max(335, image_x + 105))
        front_h = max(285, self._cards["fl"].sizeHint().height())
        rear_h = max(255, self._cards["rl"].sizeHint().height())
        front_y = max(8, image_y + int(image_size * 0.10) - front_h // 2)
        rear_y = min(
            h - rear_h - 8,
            image_y + int(image_size * 0.78) - rear_h // 2)
        right_x = w - card_w

        self._cards["fl"].setGeometry(0, front_y, card_w, front_h)
        self._cards["fr"].setGeometry(right_x, front_y, card_w, front_h)
        self._cards["rl"].setGeometry(0, rear_y, card_w, rear_h)
        self._cards["rr"].setGeometry(right_x, rear_y, card_w, rear_h)
        super().resizeEvent(event)


class SetupBuilderView(QWidget):
    back = Signal()
    saved = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder_id: str | None = None
        self._folder_path = ""
        self._folder_name = ""
        self._setup: Setup | None = None
        self._is_baseline = False
        self._detected_track: str | None = None
        self._drivers = []
        self._chassis = []
        self._tracks = []
        self._fields: dict[str, QWidget] = {}
        self._run_fields: dict[str, QWidget] = {}
        self._runs: list[dict] = []
        self._run_index = 0
        self._switching_run = False
        self._baseline_run_setup = None
        self._maintenance_context = None
        self._inventory_options: dict[str, list] = {}
        self._initial_state = None
        self._saving = False
        self._load_generation = 0
        self._build()

        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self._save_via_shortcut)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        top = QHBoxLayout()
        self._back_btn = QPushButton("\u2190  Back to Session")
        self._back_btn.setObjectName("Primary")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self.back.emit)
        top.addWidget(self._back_btn)

        self._save_btn = QPushButton("Save Setup")
        self._save_btn.setObjectName("Primary")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setToolTip("Save Setup (Ctrl+S)")
        self._save_btn.clicked.connect(self._save)
        top.addWidget(self._save_btn)

        self._crumb = QLabel("")
        self._crumb.setObjectName("Muted")
        top.addWidget(self._crumb)
        self._save_status = QLabel("")
        self._save_status.hide()
        top.addWidget(self._save_status)
        top.addStretch(1)

        self._options_btn = QPushButton("Setup Settings")
        self._options_btn.setObjectName("Ghost")
        self._options_btn.setCursor(Qt.PointingHandCursor)
        self._options_btn.setToolTip(
            "Load another setup, change destination, or view revision history")
        self._options_btn.clicked.connect(self._show_setup_options)
        top.addWidget(self._options_btn)
        root.addLayout(top)

        self._content_stack = QStackedWidget()
        self._loading_page = QWidget()
        loading_lay = QVBoxLayout(self._loading_page)
        loading_lay.addStretch(1)
        self._loading_label = QLabel("Loading setup…")
        self._loading_label.setObjectName("H2")
        self._loading_label.setAlignment(Qt.AlignCenter)
        loading_lay.addWidget(self._loading_label)
        loading_progress = QProgressBar()
        loading_progress.setRange(0, 0)
        loading_progress.setFixedWidth(260)
        loading_lay.addWidget(loading_progress, 0, Qt.AlignHCenter)
        loading_lay.addStretch(1)
        self._content_stack.addWidget(self._loading_page)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(4, 4, 10, 12)
        self._body.setSpacing(10)
        self._scroll.setWidget(body)
        self._content_stack.addWidget(self._scroll)
        root.addWidget(self._content_stack, 1)

        self._banner = QLabel("")
        self._banner.setStyleSheet(
            f"color:{config.COLOR_WARNING}; font-weight:700; padding:4px;")
        self._banner.hide()
        self._body.addWidget(self._banner)

        self._build_meta()
        self._build_chassis()
        self._build_runs()
        self._build_notes()
        self._maintenance = MaintenanceChecklist(self)
        self._body.addWidget(self._maintenance)
        self._maintenance.changed.connect(self._update_dirty_styles)
        self._chassis_cb.textChanged.connect(self.refresh_maintenance)
        self.refresh_maintenance()
        self._body.addStretch(1)
        self._wire_dirty_tracking()

    def _card(self, title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        if title:
            label = QLabel(title)
            label.setObjectName("H2")
            lay.addWidget(label)
        return card, lay

    def _build_meta(self):
        card, lay = self._card("Setup Identity")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        self._name = QLineEdit()
        self._driver = self._identity_picker("Drivers")
        self._chassis_cb = self._identity_picker("Karts")
        self._track = self._identity_picker("Tracks")
        self._chassis_cb.textChanged.connect(
            self._update_baseline_name_placeholder)
        self._track.textChanged.connect(
            self._update_baseline_name_placeholder)
        widgets = [
            ("Setup Name", self._name),
            ("Driver", self._driver),
            ("Kart / Chassis", self._chassis_cb),
            ("Track", self._track),
        ]
        for col, (label, widget) in enumerate(widgets):
            lab = QLabel(label)
            lab.setObjectName("Muted")
            grid.addWidget(lab, 0, col)
            grid.addWidget(widget, 1, col)
            grid.setColumnStretch(col, 1)

        for i, field in enumerate(sf.IDENTITY_META_FIELDS):
            col = i
            lab = QLabel(field.display_label)
            lab.setObjectName("Muted")
            edit = self._field_widget(field)
            self._fields[field.key] = edit
            grid.addWidget(lab, 2, col)
            grid.addWidget(edit, 3, col)
        lay.addLayout(grid)
        self._body.addWidget(card)

    def _identity_picker(self, category):
        picker = _PartPickerEdit()
        picker.setPlaceholderText(f"Click to choose from Parts → {category}…")
        picker.setToolTip(
            f"Search and select an official {category} Parts record")
        picker.pickerRequested.connect(
            lambda widget=picker, selected_category=category:
            self._open_identity_picker(widget, selected_category))
        return picker

    def _open_identity_picker(self, widget, category):
        dialog = _PartPickerDialog(
            category,
            self._inventory_options.get(category, []),
            widget.text(),
            getattr(widget, "_inventory_revision_ref", None),
            self,
            allow_custom=False,
        )
        dialog.partCreated.connect(self._remember_created_part)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.selected_item is not None:
            part = dialog.selected_item
            self._set_field_text(widget, part.name, {
                "item_id": part.id,
                "revision": int(part.revision or 1),
                "name": part.name,
            })
        elif dialog.custom_text is not None:
            self._set_field_text(widget, dialog.custom_text)
        self._update_dirty_styles()

    def _build_chassis(self):
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)

        grid.addWidget(self._field_card(
            sf.MAIN_GROUPS[0][0], sf.section_fields("front"), 4), 0, 0)
        grid.addWidget(self._field_card(
            "Front Track", sf.FRONT_TRACK_FIELDS, 4), 1, 0)

        grid.addWidget(_KartOverlay(self), 2, 0)
        grid.addWidget(self._field_card(
            "Rear Track", sf.REAR_TRACK_FIELDS, 3), 3, 0)
        grid.addWidget(self._field_card(
            sf.MAIN_GROUPS[2][0], sf.section_fields("rear"), 4), 4, 0)
        grid.addWidget(self._field_card(
            sf.MAIN_GROUPS[1][0], sf.section_fields("engine"), 4), 5, 0)
        grid.addWidget(self._field_card(
            sf.MAIN_GROUPS[3][0], sf.section_fields("tires"), 4), 6, 0)
        if sf.custom_fields("session"):
            grid.addWidget(self._field_card("Additional Session Parameters", sf.custom_fields("session"), 4), 7, 0)
        self._body.addWidget(host)

    def _field_card(self, title, fields, cols) -> QFrame:
        card, lay = self._card(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        for i, field in enumerate(fields):
            row, col = divmod(i, cols)
            grid.addLayout(self._field_cell(field), row, col)
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        return card

    def _corner_card(self, code) -> QFrame:
        name, _, is_front = sf.CORNERS[code]
        card, lay = self._card()
        accent = "#4a5568" if is_front else "#6b4a3a"
        title = QLabel(f"\u25CF  {name}")
        title.setStyleSheet(
            f"background:{accent}; color:white; font-weight:700;"
            "padding:6px 8px; border-radius:6px;")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(3)
        for i, field in enumerate(sf.corner_fields(code)):
            row, col = divmod(i, 2)
            grid.addLayout(self._field_cell(field), row, col)
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        return card

    def _field_cell(self, field) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setSpacing(2)
        lab = QLabel(field.display_label)
        lab.setObjectName("Muted")
        lab.setWordWrap(True)
        edit = self._field_widget(field)
        self._fields[field.key] = edit
        lay.addWidget(lab)
        lay.addWidget(edit)
        return lay

    @staticmethod
    def _field_text(widget) -> str:
        return widget.currentText() if isinstance(widget, QComboBox) else widget.text()

    @staticmethod
    def _set_field_text(widget, value, inventory_ref=None) -> None:
        text = "" if value is None else str(value)
        if isinstance(widget, QComboBox):
            widget.setEditText(text)
        else:
            widget.setText(text)
        if hasattr(widget, "_inventory_revision_ref"):
            widget._inventory_revision_ref = (
                dict(inventory_ref) if inventory_ref else None)

    @staticmethod
    def _clear_field(widget) -> None:
        if isinstance(widget, QComboBox):
            widget.setEditText("")
            if hasattr(widget, "_inventory_revision_ref"):
                widget._inventory_revision_ref = None
        else:
            widget.clear()
            if hasattr(widget, "_inventory_revision_ref"):
                widget._inventory_revision_ref = None

    def _field_widget(self, field) -> QWidget:
        if field.kind == "date":
            return DatePickerEdit()
        if field.kind == "time":
            picker = ScrollSafeComboBox()
            picker.setEditable(True)
            picker.setInsertPolicy(QComboBox.NoInsert)
            picker.lineEdit().setPlaceholderText("Choose time\u2026")
            picker.setToolTip(
                "Choose a time or type an exact time, including AM or PM")
            picker.addItems([
                datetime(2000, 1, 1, hour, minute).strftime(
                    "%I:%M %p").lstrip("0")
                for hour in range(24) for minute in range(0, 60, 15)
            ])
            picker.setCurrentIndex(-1)
            return picker
        if field.key in sf.INVENTORY_FIELD_CATEGORIES:
            picker = _PartPickerEdit()
            picker.pickerRequested.connect(
                lambda key=field.key: self._open_part_picker(key))
            return picker
        edit = QLineEdit()
        placeholders = {
            "upper_castor_camber": "e.g. top pill forward",
            "lower_castor_camber": "e.g. bottom pill backward",
            "front_left_camber": "e.g. 2 ticks in",
            "front_right_camber": "e.g. 1 tick out",
        }
        if field.key in placeholders:
            edit.setPlaceholderText(placeholders[field.key])
        return edit

    def _update_baseline_name_placeholder(self, *_args):
        if not self._is_baseline:
            return
        kart = self._chassis_cb.text().strip()
        track = self._track.text().strip()
        self._name.setPlaceholderText(_baseline_name(kart, track))

    def _open_part_picker(self, key):
        widget = self._fields.get(key)
        category = sf.INVENTORY_FIELD_CATEGORIES.get(key)
        if not isinstance(widget, _PartPickerEdit) or not category:
            return
        dialog = _PartPickerDialog(
            category,
            self._inventory_options.get(category, []),
            widget.text(),
            getattr(widget, "_inventory_revision_ref", None),
            self,
        )
        dialog.partCreated.connect(self._remember_created_part)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.selected_item is not None:
            part = dialog.selected_item
            self._set_field_text(widget, part.name, {
                "item_id": part.id,
                "revision": int(part.revision or 1),
                "name": part.name,
            })
        elif dialog.custom_text is not None:
            self._set_field_text(widget, dialog.custom_text)
        self._update_dirty_styles()

    def _remember_created_part(self, part):
        items = self._inventory_options.setdefault(part.category, [])
        items[:] = [item for item in items if item.id != part.id]
        items.append(part)

    def _build_runs(self):
        card, lay = self._card("Runs & Session Notes")
        nav = QHBoxLayout()
        self._previous_run_btn = QPushButton("\u2190 Previous")
        self._previous_run_btn.setObjectName("Ghost")
        self._previous_run_btn.clicked.connect(self._previous_run)
        nav.addWidget(self._previous_run_btn)
        self._run_position = QLabel("Run 1 of 1")
        self._run_position.setStyleSheet("font-weight:700;")
        self._run_position.setAlignment(Qt.AlignCenter)
        nav.addWidget(self._run_position, 1)
        self._next_run_btn = QPushButton("Next \u2192")
        self._next_run_btn.setObjectName("Ghost")
        self._next_run_btn.clicked.connect(self._next_run)
        nav.addWidget(self._next_run_btn)
        self._add_run_btn = QPushButton("+ Add Run")
        self._add_run_btn.setObjectName("Primary")
        self._add_run_btn.clicked.connect(self._add_run)
        nav.addWidget(self._add_run_btn)
        self._remove_run_btn = QPushButton("Remove Run")
        self._remove_run_btn.setObjectName("Ghost")
        self._remove_run_btn.clicked.connect(self._remove_run)
        nav.addWidget(self._remove_run_btn)
        lay.addLayout(nav)
        self._run_setup_context = QLabel("Run 1 — baseline setup")
        self._run_setup_context.setObjectName("Muted")
        lay.addWidget(self._run_setup_context)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        for i, field in enumerate(sf.RUN_FIELDS):
            row, col = divmod(i, 4)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            label = QLabel(field.display_label)
            label.setObjectName("Muted")
            edit = self._field_widget(field)
            self._run_fields[field.key] = edit
            cell.addWidget(label)
            cell.addWidget(edit)
            grid.addLayout(cell, row, col)
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)

        notes_label = QLabel("Session Notes / Driver Feedback")
        notes_label.setObjectName("Muted")
        lay.addWidget(notes_label)
        self._feedback = QPlainTextEdit()
        self._feedback.setPlaceholderText(
            "Driver feedback, balance, handling, changes made, and what to try next\u2026")
        self._feedback.setMinimumHeight(190)
        lay.addWidget(self._feedback)
        hint = QLabel(
            "Each run keeps its own conditions, lap data, and full session notes. "
            "Use Previous / Next to move between runs; add as many as needed.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._body.addWidget(card)
        self._load_runs([self._blank_run()])

    def _build_notes(self):
        card, lay = self._card("Setup Sheet Notes")
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText(
            "Long-form notes that apply to the setup sheet as a whole\u2026")
        self._notes.setMinimumHeight(190)
        lay.addWidget(self._notes)
        self._body.addWidget(card)

    @staticmethod
    def _blank_run(number: int | None = None) -> dict:
        now = datetime.now()
        run = {
            "id": str(uuid4()),
            "run_date": now.strftime("%Y-%m-%d"),
            "run_time": now.strftime("%I:%M %p").lstrip("0"),
        }
        if number is not None:
            run["run_number"] = str(number)
        return run

    @staticmethod
    def _run_is_empty(run: dict) -> bool:
        return not any(
            value not in (None, "")
            for key, value in run.items() if key != "id")

    @staticmethod
    def _runs_from_setup(setup: Setup) -> list[dict]:
        data = setup.data or {}
        stored = data.get(sf.RUNS_KEY)
        if isinstance(stored, list):
            runs = []
            for entry in stored:
                if not isinstance(entry, dict):
                    continue
                run = {
                    key: deepcopy(value) for key, value in entry.items()
                    if key == "id" or key == sf.RUN_NOTES_KEY or key == sf.RUN_SETUP_KEY
                    or key in sf.LEGACY_RUN_KEYS
                }
                run["id"] = str(run.get("id") or uuid4())
                runs.append(run)
            if runs:
                return runs

        # Upgrade the old single-run fields in memory. They remain readable and
        # are replaced by the structured run list the next time the sheet saves.
        legacy = {
            key: deepcopy(data.get(key)) for key in sf.LEGACY_RUN_KEYS
            if data.get(key) not in (None, "")
        }
        feedback = data.get("driver_feedback")
        if feedback not in (None, ""):
            legacy[sf.RUN_NOTES_KEY] = str(feedback)
        legacy["id"] = str(uuid4())
        return [legacy]

    def _load_runs(self, runs, index=0, base_values=None):
        self._baseline_run_setup = None
        self._runs = []
        self._run_index = 0
        self._baseline_run_setup = self._capture_run_setup()
        for key, value in (base_values or {}).items():
            if key.startswith("custom_") and key not in self._fields:
                self._baseline_run_setup["values"][key] = deepcopy(value)
        normalized = [
            _split_legacy_run_timestamp(deepcopy(run))
            for run in runs if isinstance(run, dict)
        ]
        self._runs = normalized or [self._blank_run()]
        for run in self._runs:
            run["id"] = str(run.get("id") or uuid4())
        for run in self._runs[1:]:
            run.setdefault(sf.RUN_SETUP_KEY, deepcopy(self._baseline_run_setup))
        self._run_index = min(max(0, index), len(self._runs) - 1)
        self._show_current_run()

    def _store_current_run(self):
        if not self._runs or self._switching_run:
            return
        run = {"id": self._runs[self._run_index].get("id") or str(uuid4())}
        for key, edit in self._run_fields.items():
            value = self._field_text(edit).strip()
            if value:
                run[key] = value
        notes = self._feedback.toPlainText().strip()
        if notes:
            run[sf.RUN_NOTES_KEY] = notes
        snapshot = self._capture_run_setup()
        if self._run_index == 0:
            self._baseline_run_setup = snapshot
        else:
            run[sf.RUN_SETUP_KEY] = snapshot
        self._runs[self._run_index] = run

    def _show_current_run(self):
        if not self._runs:
            self._runs = [self._blank_run()]
            self._run_index = 0
        run = self._runs[self._run_index]
        self._switching_run = True
        try:
            snapshot = (self._baseline_run_setup if self._run_index == 0
                        else run.get(sf.RUN_SETUP_KEY, self._baseline_run_setup))
            if snapshot:
                refs = snapshot.get("inventory_refs", {})
                for key, edit in self._fields.items():
                    self._set_field_text(edit, snapshot.get("values", {}).get(key),
                                         refs.get(key))
            for key, edit in self._run_fields.items():
                self._set_field_text(edit, run.get(key))
            self._feedback.setPlainText(str(run.get(sf.RUN_NOTES_KEY) or ""))
        finally:
            self._switching_run = False
        self._update_run_navigation()

    def _update_run_navigation(self):
        total = max(1, len(self._runs))
        self._run_position.setText(f"Run {self._run_index + 1} of {total}")
        self._run_setup_context.setText(
            "Run 1 — baseline setup used by the session" if self._run_index == 0
            else f"Editing Run {self._run_index + 1} only · baseline stays unchanged")
        self._previous_run_btn.setEnabled(self._run_index > 0)
        self._next_run_btn.setEnabled(self._run_index + 1 < total)
        self._remove_run_btn.setEnabled(self._run_index > 0)

    def _go_to_run(self, index):
        if not 0 <= index < len(self._runs):
            return
        self._store_current_run()
        self._run_index = index
        self._show_current_run()
        self._update_dirty_styles()

    def _previous_run(self):
        self._go_to_run(self._run_index - 1)

    def _next_run(self):
        self._go_to_run(self._run_index + 1)

    def _add_run(self):
        self._store_current_run()
        self._runs.append(self._blank_run(len(self._runs) + 1))
        self._runs[-1][sf.RUN_SETUP_KEY] = deepcopy(self._capture_run_setup())
        self._run_index = len(self._runs) - 1
        self._show_current_run()
        self._update_dirty_styles()
        self._run_fields["run_number"].setFocus()

    def _remove_run(self):
        if self._run_index == 0:
            return
        self._store_current_run()
        current = self._runs[self._run_index]
        if not self._run_is_empty(current) and QMessageBox.question(
                self, "Remove Run",
                f"Remove run {self._run_index + 1} and its session notes?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        if len(self._runs) == 1:
            self._runs = [self._blank_run()]
            self._run_index = 0
        else:
            self._runs.pop(self._run_index)
            self._run_index = min(self._run_index, len(self._runs) - 1)
        self._show_current_run()
        self._update_dirty_styles()

    def _serialized_runs(self, include_blank=False) -> list[dict]:
        self._store_current_run()
        return [
            deepcopy(run) for index, run in enumerate(self._runs)
            if include_blank or not self._run_is_empty(run)
            or (index == 0 and len(self._runs) > 1)
        ]

    def _capture_run_setup(self):
        previous = (self._baseline_run_setup if self._run_index == 0 else
                    self._runs[self._run_index].get(sf.RUN_SETUP_KEY, {})) if self._runs else {}
        previous = previous or {}
        values = deepcopy(previous.get("values", {}))
        if self._setup:
            for key, value in (self._setup.data or {}).items():
                if key.startswith("custom_"):
                    values.setdefault(key, value)
        values.update({key: self._field_text(edit) for key, edit in self._fields.items()})
        refs = deepcopy(previous.get("inventory_refs", {}))
        refs.update({key: deepcopy(getattr(edit, "_inventory_revision_ref", None))
                     for key, edit in self._fields.items() if key in sf.INVENTORY_FIELD_CATEGORIES})
        return {"values": values, "inventory_refs": refs}

    # ── Load / populate ───────────────────────────────────────────────────────

    def load_setup(self, folder_id, folder_path, folder_name, setup: Setup | None):
        self._save_status.hide()
        self._load_generation += 1
        load_generation = self._load_generation
        self._loading_label.setText(
            "Loading baseline…" if setup and setup.is_baseline
            else "Loading setup…")
        self._content_stack.setCurrentWidget(self._loading_page)
        self._folder_id = folder_id
        self._folder_path = folder_path or ""
        self._folder_name = folder_name or ""
        self._setup = setup
        self._maintenance_context = None
        self._maintenance.set_records((setup.data or {}).get(maintenance.CHECKS_KEY, {}) if setup else {})
        self._is_baseline = bool(setup and setup.is_baseline)
        self._initial_state = None
        self._detected_track = _detect_track(folder_name, folder_path)
        self._detected_date = _detect_date(folder_name, folder_path)
        self._crumb.setText(
            f"Baseline \u00b7 {setup.track or folder_name or 'New'}"
            if self._is_baseline
            else (folder_path or folder_name or ""))
        self._banner.setVisible(self._is_baseline)
        self._banner.setText(
            "BASELINE template \u2014 saved as reusable defaults for new setups. "
            "Choose a kart, track, or both to control where it is suggested."
            if self._is_baseline else "")
        self._back_btn.setText(
            "\u2190  Back to Setups" if self._is_baseline
            else "\u2190  Back to Session")
        self._save_btn.setText(
            "Save Baseline" if self._is_baseline else "Save Setup")
        self._save_btn.setToolTip(
            ("Save Baseline" if self._is_baseline else "Save Setup")
            + " (Ctrl+S)")
        self._options_btn.setText("Setup Settings")
        self._save_btn.setEnabled(False)
        self._clear_form()
        self._set_identity_controls_enabled(False)

        def _identity():
            try:
                drivers = db.fetch_drivers()
            except Exception:
                drivers = []
            try:
                chassis = db.fetch_chassis()
            except Exception:
                chassis = []
            try:
                tracks = [t.name for t in db.fetch_tracks()]
            except Exception:
                tracks = ["Purdue GP", "Kaminsky"]
            inventory_options: dict[str, list] = {}
            try:
                inventory_items = db.fetch_inventory_items(
                    include_archived=True)
                offline_store.cache_inventory(inventory_items)
                inventory_items = offline_store.merge_pending_inventory(
                    inventory_items)
            except Exception:
                # A downloaded Parts-name catalog keeps every picker usable at
                # the track even while Supabase is unreachable.
                inventory_items = offline_store.load_inventory()
            for item in inventory_items:
                inventory_options.setdefault(item.category, []).append(item)
            inventory_options = {
                category: sorted(
                    items,
                    key=lambda item: (
                        (item.name or "").casefold(), -int(item.revision or 1)))
                for category, items in inventory_options.items()
            }
            return drivers, chassis, tracks, inventory_options

        run_async(
            self,
            _identity,
            on_done=lambda result: self._identity_loaded(
                load_generation, result),
            on_error=lambda _message: self._identity_loaded(
                load_generation, ([], [], [], {})),
        )

    def set_return_context(self, context: str):
        """Match the Back label to the page that opened the builder."""
        self._back_btn.setText(f"\u2190  Back to {context}")

    def _set_identity_controls_enabled(self, enabled):
        for widget in (
                self._name, self._driver, self._chassis_cb, self._track,
                self._options_btn, self._feedback, self._notes,
                *self._fields.values(), *self._run_fields.values(),
                self._previous_run_btn, self._next_run_btn,
                self._add_run_btn, self._remove_run_btn):
            widget.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)
        if enabled:
            self._update_run_navigation()

    def _identity_loaded(self, load_generation, result):
        if load_generation != self._load_generation:
            return
        (self._drivers, self._chassis, self._tracks,
         self._inventory_options) = result
        if (self._setup and self._setup.track
                and self._setup.track not in self._tracks):
            self._tracks.append(self._setup.track)
        self._fill_inventory_fields()
        if self._detected_track and not self._is_baseline:
            self._track.setCurrentText(self._detected_track)
        if self._setup:
            self._populate(self._setup)
        self.refresh_maintenance(reload_context=True)
        self._update_baseline_name_placeholder()
        date_edit = self._run_fields.get("run_date")
        if (date_edit is not None and not self._field_text(date_edit).strip()
                and self._detected_date and not self._is_baseline):
            self._set_field_text(date_edit, self._detected_date)
        self._initial_state = self._form_state()
        self._update_dirty_styles()
        self._set_identity_controls_enabled(True)
        self._content_stack.setCurrentWidget(self._scroll)

    @staticmethod
    def _fill_combo(combo, values):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(NONE)
        combo.addItems(values)
        combo.blockSignals(False)

    @staticmethod
    def _identity_ref(widget):
        reference = getattr(widget, "_inventory_revision_ref", None)
        if not isinstance(reference, dict):
            return None
        if widget.currentText().strip() != str(
                reference.get("name") or "").strip():
            return None
        return {
            "item_id": reference.get("item_id"),
            "revision": reference.get("revision"),
            "name": reference.get("name"),
        }

    def _fill_inventory_fields(self):
        for key, category in sf.INVENTORY_FIELD_CATEGORIES.items():
            picker = self._fields.get(key)
            if isinstance(picker, _PartPickerEdit):
                picker.setToolTip(
                    f"Click to search {category}, including archived parts")

    def _clear_form(self):
        self._name.clear()
        for edit in self._fields.values():
            self._clear_field(edit)
        for combo in (self._driver, self._chassis_cb, self._track):
            combo.clear()
            combo._inventory_revision_ref = None
        self._load_runs([self._blank_run()])
        self._notes.clear()
        self._update_dirty_styles()

    def _wire_dirty_tracking(self):
        self._name.textChanged.connect(self._update_dirty_styles)
        for edit in self._fields.values():
            signal = (edit.currentTextChanged if isinstance(edit, QComboBox)
                      else edit.textChanged)
            signal.connect(self._update_dirty_styles)
        for edit in self._run_fields.values():
            signal = (edit.currentTextChanged if isinstance(edit, QComboBox)
                      else edit.textChanged)
            signal.connect(self._update_dirty_styles)
        for picker in (self._driver, self._chassis_cb, self._track):
            picker.textChanged.connect(self._update_dirty_styles)
        self._feedback.textChanged.connect(self._update_dirty_styles)
        self._notes.textChanged.connect(self._update_dirty_styles)

    @staticmethod
    def _set_dirty(widget, dirty: bool):
        if bool(widget.property("dirty")) == dirty:
            return
        widget.setProperty("dirty", dirty)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _update_dirty_styles(self, *_args):
        if self._switching_run:
            return
        state = self._initial_state
        if state is None:
            self._set_dirty(self._name, False)
            for edit in self._fields.values():
                self._set_dirty(edit, False)
            for edit in self._run_fields.values():
                self._set_dirty(edit, False)
            for combo in (self._driver, self._chassis_cb, self._track):
                self._set_dirty(combo, False)
            self._set_dirty(self._feedback, False)
            self._set_dirty(self._notes, False)
            self._save_status.hide()
            return

        initial_fields = dict(state["fields"])
        initial_refs = dict(state.get("inventory_refs", ()))
        if self._run_index > 0:
            run_id = self._runs[self._run_index].get("id")
            saved_run = next((run for run in state["runs"] if run.get("id") == run_id), {})
            snapshot = saved_run.get(sf.RUN_SETUP_KEY, {})
            initial_fields = snapshot.get("values", initial_fields)
            initial_refs = snapshot.get("inventory_refs", initial_refs)
        dirty_flags = [
            self._folder_id != state["folder_id"],
            self._folder_path != state["folder_path"],
            self._folder_name != state["folder_name"],
        ]

        name_dirty = self._name.text() != state["name"]
        self._set_dirty(self._name, name_dirty)
        dirty_flags.append(name_dirty)

        for key, edit in self._fields.items():
            dirty = self._field_text(edit) != initial_fields.get(key, "")
            if hasattr(edit, "_inventory_revision_ref"):
                dirty = dirty or (
                    getattr(edit, "_inventory_revision_ref", None)
                    != initial_refs.get(key))
            self._set_dirty(edit, dirty)
            dirty_flags.append(dirty)

        for combo, key in (
                (self._driver, "driver"),
                (self._chassis_cb, "chassis"),
                (self._track, "track")):
            dirty = combo.currentText() != state[key]
            dirty = dirty or self._identity_ref(combo) != dict(
                state.get("identity_refs", ())).get(key)
            self._set_dirty(combo, dirty)
            dirty_flags.append(dirty)

        runs_dirty = self._serialized_runs(include_blank=True) != state["runs"]
        notes_dirty = self._notes.toPlainText() != state["notes"]
        maintenance_dirty = self._maintenance.records() != state.get(maintenance.CHECKS_KEY, {})
        self._set_dirty(self._maintenance, maintenance_dirty)
        self._set_dirty(self._feedback, runs_dirty)
        for edit in self._run_fields.values():
            self._set_dirty(edit, runs_dirty)
        self._set_dirty(self._notes, notes_dirty)
        dirty_flags.extend((runs_dirty, notes_dirty, maintenance_dirty))
        is_dirty = any(dirty_flags)
        self._set_save_status(
            "\u25CF  Unsaved changes", config.COLOR_WARNING, is_dirty)

    def _form_state(self):
        self._store_current_run()
        baseline = self._baseline_run_setup or self._capture_run_setup()
        return {
            "folder_id": self._folder_id,
            "folder_path": self._folder_path,
            "folder_name": self._folder_name,
            "crumb": self._crumb.text(),
            "name": self._name.text(),
            "driver": self._driver.currentText(),
            "chassis": self._chassis_cb.currentText(),
            "track": self._track.currentText(),
            "identity_refs": (
                ("driver", deepcopy(self._identity_ref(self._driver))),
                ("chassis", deepcopy(self._identity_ref(self._chassis_cb))),
                ("track", deepcopy(self._identity_ref(self._track))),
            ),
            "fields": tuple(
                (key, baseline["values"].get(key, ""))
                for key in sorted(self._fields)),
            "inventory_refs": tuple(
                (key, deepcopy(baseline["inventory_refs"].get(key)))
                for key in sorted(sf.INVENTORY_FIELD_CATEGORIES)),
            "runs": self._serialized_runs(include_blank=True),
            "notes": self._notes.toPlainText(),
            maintenance.CHECKS_KEY: self._maintenance.records(),
        }

    def has_unsaved_changes(self) -> bool:
        return (
            self._initial_state is not None
            and self._form_state() != self._initial_state
        )

    def is_saving(self) -> bool:
        return self._saving

    def revert_changes(self):
        """Restore the form exactly to the last loaded/saved state."""
        state = self._initial_state
        if state is None:
            return
        self._folder_id = state["folder_id"]
        self._folder_path = state["folder_path"]
        self._folder_name = state["folder_name"]
        self._crumb.setText(state["crumb"])
        self._name.setText(state["name"])
        self._driver.setCurrentText(state["driver"])
        self._chassis_cb.setCurrentText(state["chassis"])
        self._track.setCurrentText(state["track"])
        identity_refs = dict(state.get("identity_refs", ()))
        self._driver._inventory_revision_ref = deepcopy(
            identity_refs.get("driver"))
        self._chassis_cb._inventory_revision_ref = deepcopy(
            identity_refs.get("chassis"))
        self._track._inventory_revision_ref = deepcopy(
            identity_refs.get("track"))
        refs = dict(state.get("inventory_refs", ()))
        for key, value in state["fields"]:
            self._set_field_text(self._fields[key], value, refs.get(key))
        self._load_runs(state["runs"])
        self._notes.setPlainText(state["notes"])
        self._maintenance.set_records(state.get(maintenance.CHECKS_KEY, {}))
        self.refresh_maintenance()
        self._update_dirty_styles()

    def _populate(self, setup: Setup, only_empty=False, inherit=False):
        revision_refs = dict(
            (setup.data or {}).get("_inventory_part_revisions") or {})
        values = dict(setup.data or {})
        if not values.get("rim_type") and values.get("wheels"):
            values["rim_type"] = values["wheels"]
            if revision_refs.get("wheels"):
                revision_refs["rim_type"] = revision_refs["wheels"]
        # Older setup sheets stored a rear hub independently on both rear
        # corners. Carry the first available choice into the shared rear-axle
        # field so historical setups remain usable without a data migration.
        if not values.get("rear_hub_material"):
            for legacy_key in ("rl_hub_material", "rr_hub_material"):
                if values.get(legacy_key):
                    values["rear_hub_material"] = values[legacy_key]
                    if revision_refs.get(legacy_key):
                        revision_refs["rear_hub_material"] = revision_refs[legacy_key]
                    break
        identity_refs = dict(
            (setup.data or {}).get("_identity_part_revisions") or {})
        if not inherit and not (only_empty and self._name.text()):
            self._name.setText(setup.name or "")
        if setup.track and not (
                only_empty and self._track.currentText() not in ("", NONE)):
            self._set_field_text(
                self._track, setup.track, identity_refs.get("track"))
        if (not inherit and setup.driver_name
                and not (only_empty and self._driver.currentText()
                         not in ("", NONE))):
            self._set_field_text(
                self._driver, setup.driver_name, identity_refs.get("driver"))
        if setup.chassis_name and not (
                only_empty and self._chassis_cb.currentText()
                not in ("", NONE)):
            self._set_field_text(
                self._chassis_cb, setup.chassis_name,
                identity_refs.get("chassis"))
        for key, value in values.items():
            if inherit and key in _NO_PREFILL_KEYS:
                continue
            edit = self._fields.get(key)
            if edit and not (only_empty and self._field_text(edit)):
                self._set_field_text(edit, value, revision_refs.get(key))
        if not inherit and not only_empty:
            self._maintenance.set_records((setup.data or {}).get(maintenance.CHECKS_KEY, {}))
            self._load_runs(self._runs_from_setup(setup), base_values=setup.data)
        if setup.notes and not only_empty:
            self._notes.setPlainText(setup.notes)
        self.refresh_maintenance()

    def _refresh_after_concurrent_merge(self, setup: Setup):
        """Show the complete merged row, including fields removed remotely."""
        values = dict(setup.data or {})
        revision_refs = dict(
            values.get("_inventory_part_revisions") or {})
        identity_refs = dict(
            values.get("_identity_part_revisions") or {})
        self._name.setText(setup.name or "")
        self._set_field_text(
            self._driver, setup.driver_name or NONE,
            identity_refs.get("driver"))
        self._set_field_text(
            self._chassis_cb, setup.chassis_name or NONE,
            identity_refs.get("chassis"))
        self._set_field_text(
            self._track, setup.track or NONE, identity_refs.get("track"))
        for key, edit in self._fields.items():
            self._set_field_text(edit, values.get(key, ""), revision_refs.get(key))
        self._load_runs(self._runs_from_setup(setup), base_values=setup.data)
        self._notes.setPlainText(setup.notes or "")
        self._maintenance.set_records((setup.data or {}).get(maintenance.CHECKS_KEY, {}))
        self.refresh_maintenance()

    # ── Prefill / copy ────────────────────────────────────────────────────────

    def refresh_maintenance(self, *_args, reload_context=False):
        if not hasattr(self, "_maintenance"):
            return
        if self._maintenance_context is None or reload_context:
            sessions, _age = cache.load_sessions()
            self._maintenance_context = (maintenance_store.cached_snapshot().policy,
                                         sessions, offline_store.load_setup_catalog())
        policy, sessions, catalog = self._maintenance_context
        selected = self._chassis_cb.currentText().strip()
        reference = self._identity_ref(self._chassis_cb)
        legacy = next((item for item in self._chassis if item.name.casefold() == selected.casefold()), None)
        legacy_id = legacy.id if legacy else (
            self._setup.chassis_id if self._setup and
            (self._setup.chassis_name or "").casefold() == selected.casefold() else None)
        current = Setup(id=self._setup.id if self._setup else None,
                        drive_folder_id=self._folder_id, drive_folder_path=self._folder_path or self._folder_name,
                        is_baseline=self._is_baseline, chassis_name=selected if selected != NONE else None,
                        chassis_id=legacy_id,
                        data={"_identity_part_revisions": {"chassis": reference} if reference else {},
                              maintenance.CHECKS_KEY: self._maintenance.records()})
        self._maintenance.refresh(policy, current, sessions, catalog)

    def _show_setup_options(self):
        _SetupOptionsDialog(self, self).exec()

    def _set_options_busy(self, busy: bool):
        self._options_btn.setEnabled(not busy)
        self._options_btn.setText("Loading\u2026" if busy else "Setup Settings")

    def _choose_destination(self):
        if self._is_baseline:
            return
        generation = self._load_generation
        self._set_options_busy(True)

        def _done(folders):
            if generation != self._load_generation or not self.isVisible():
                return
            self._set_options_busy(False)
            if not folders:
                QMessageBox.information(
                    self, "No sessions",
                    "No session folders were found in Drive.")
                return
            dialog = _DestinationDialog(folders, self)
            self._dest_dialog = dialog  # strong ref until it closes
            self._dest_dialog_generation = generation
            dialog.accepted.connect(self._destination_chosen)
            dialog.rejected.connect(self._destination_closed)
            dialog.open()

        def _error(message):
            if generation != self._load_generation or not self.isVisible():
                return
            self._set_options_busy(False)
            QMessageBox.critical(self, "Error", message)

        run_async(self, google_drive_client.walk_session_folders,
                  on_done=_done, on_error=_error)

    def _destination_chosen(self):
        dialog = getattr(self, "_dest_dialog", None)
        folder = dialog.selected_folder if dialog else None
        if (folder is not None
                and getattr(self, "_dest_dialog_generation", None)
                == self._load_generation):
            self._folder_id = folder.id
            self._folder_path = folder.path or folder.name or ""
            self._folder_name = folder.name or ""
            self._crumb.setText(self._folder_path or self._folder_name)
            self._save_btn.setEnabled(True)
            self._update_dirty_styles()
        self._destination_closed()

    def _destination_closed(self):
        dialog = getattr(self, "_dest_dialog", None)
        if dialog is not None:
            dialog.deleteLater()
        self._dest_dialog = None
        self._dest_dialog_generation = None

    def _prefill(self):
        generation = self._load_generation
        chassis = next(
            (c for c in self._chassis if c.name == self._chassis_cb.currentText()),
            None)
        track = self._track.currentText()
        if track in ("", NONE):
            track = self._detected_track
        if not chassis and not track:
            QMessageBox.information(
                self, "No baseline", "Pick a Chassis and Track first.")
            return
        scope = (chassis.id if chassis else None, track)
        self._prefill_btn.setEnabled(False)

        def _fetch():
            try:
                base = None
                if chassis and track:
                    base = db.fetch_baseline_for_chassis_track(chassis.id, track)
                if not base and chassis:
                    base = db.fetch_baseline_for_chassis(chassis.id)
                if not base and track:
                    base = db.fetch_baseline_for_track(track)
                return base
            except Exception:
                cached = [
                    setup for setup in offline_store.load_setup_catalog(
                        include_baselines=True) if setup.is_baseline]
                cached.sort(
                    key=lambda setup: setup.updated_at or setup.created_at or "",
                    reverse=True)
                chassis_matches = [
                    setup for setup in cached
                    if not chassis or setup.chassis_id == chassis.id
                    or (setup.chassis_name or "").casefold()
                    == chassis.name.casefold()]
                if chassis and track:
                    exact = next((
                        setup for setup in chassis_matches
                        if (setup.track or "").casefold() == track.casefold()), None)
                    if exact:
                        return exact
                if chassis:
                    global_match = next((
                        setup for setup in chassis_matches if not setup.track), None)
                    if global_match:
                        return global_match
                if track:
                    return next((
                        setup for setup in cached
                        if (setup.track or "").casefold() == track.casefold()), None)
                return None

        def _done(base):
            if generation != self._load_generation or not self.isVisible():
                return
            self._prefill_btn.setEnabled(True)
            current_chassis = next(
                (c for c in self._chassis
                 if c.name == self._chassis_cb.currentText()), None)
            current_track = self._track.currentText()
            if current_track in ("", NONE):
                current_track = self._detected_track
            if scope != (
                    current_chassis.id if current_chassis else None,
                    current_track):
                return
            if not base:
                QMessageBox.information(
                    self, "No baseline",
                    "No matching baseline exists for that chassis / track.")
                return
            self._populate(base, only_empty=True, inherit=True)
            QMessageBox.information(
                self, "Prefilled",
                f"Filled empty fields from \u201c{base.name or 'baseline'}\u201d.")

        def _error(message):
            if generation != self._load_generation or not self.isVisible():
                return
            self._prefill_btn.setEnabled(True)
            QMessageBox.critical(self, "Error", message)

        run_async(self, _fetch, on_done=_done, on_error=_error)

    def _copy_from_setup(self):
        generation = self._load_generation
        self._set_options_busy(True)

        def _fetch():
            try:
                baselines, saved_setups = db.fetch_setup_catalog()
                offline_store.cache_setup_catalog(baselines, saved_setups)
                return baselines + saved_setups
            except Exception:
                return offline_store.load_setup_catalog(
                    include_baselines=True)

        def _done(setups):
            if generation != self._load_generation or not self.isVisible():
                return
            self._set_options_busy(False)
            cur = self._setup.id if self._setup else None
            options = [s for s in setups if s.id and s.id != cur]
            if not options:
                QMessageBox.information(
                    self, "No setups", "There are no other setups to copy from.")
                return
            # Never call exec() here: this callback is delivered from an async
            # worker signal, and a nested modal event loop can deadlock before the
            # worker's thread-quit signal is processed. open() is modal but
            # non-blocking; accepted/rejected signals finish the operation.
            dialog = _CopySetupDialog(options, self._setup_label, self)
            self._copy_dialog = dialog  # strong ref until it closes
            self._copy_dialog_generation = generation
            dialog.accepted.connect(self._copy_dialog_accepted)
            dialog.rejected.connect(self._copy_dialog_closed)
            dialog.open()

        def _error(message):
            if generation != self._load_generation or not self.isVisible():
                return
            self._set_options_busy(False)
            QMessageBox.critical(self, "Error", message)

        run_async(self, _fetch, on_done=_done, on_error=_error)

    def _copy_dialog_accepted(self):
        dialog = getattr(self, "_copy_dialog", None)
        if (dialog is not None and dialog.selected_setup is not None
                and getattr(self, "_copy_dialog_generation", None)
                == self._load_generation):
            self._populate(
                dialog.selected_setup, only_empty=not dialog.overwrite,
                inherit=True)
        self._copy_dialog_closed()

    def _copy_dialog_closed(self):
        dialog = getattr(self, "_copy_dialog", None)
        if dialog is not None:
            dialog.deleteLater()
        self._copy_dialog = None
        self._copy_dialog_generation = None

    @staticmethod
    def _setup_label(setup):
        tags = [t for t in [
            "BASELINE" if setup.is_baseline else None,
            setup.driver_name, setup.chassis_name, setup.track] if t]
        return (setup.name or "(unnamed)") + (
            f"  ({' \u00b7 '.join(tags)})" if tags else "")

    def _set_save_status(self, text, color, visible=True):
        self._save_status.setText(text)
        self._save_status.setStyleSheet(
            f"color:{color}; font-weight:700;")
        self._save_status.setVisible(visible)

    def _show_history(self):
        if self._setup and self._setup.id:
            _SetupHistoryDialog(self._setup, self).exec()

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_via_shortcut(self):
        # Ctrl+S saves only while the builder is the visible page and saving is allowed.
        if self.isVisible() and self._save_btn.isEnabled():
            self._save()

    def _save(self, after_save=None):
        if self._saving:
            return
        if not self._is_baseline and not self._folder_id:
            return
        self._store_current_run()
        from math import isfinite
        snapshots = [self._baseline_run_setup or self._capture_run_setup()] + [
            run.get(sf.RUN_SETUP_KEY, {}) for run in self._runs[1:]]
        for position, snapshot in enumerate(snapshots):
            for field in sf.custom_fields():
                value = snapshot.get("values", {}).get(field.key)
                if field.kind != "num" or value in (None, ""):
                    continue
                try:
                    if not isfinite(float(value)):
                        raise ValueError
                except (TypeError, ValueError):
                    QMessageBox.warning(self, "Invalid parameter value", f"Run {position + 1}: {field.label} must be a finite number.")
                    return
        def _named(items, text):
            key = (text or "").strip().casefold()
            return next((item for item in items
                         if item.name.casefold() == key), None)

        driver_text = self._driver.currentText().strip()
        chassis_text = self._chassis_cb.currentText().strip()
        track_text = self._track.currentText().strip()

        def _selected_part(items, widget, text):
            reference = self._identity_ref(widget)
            if reference:
                match = next((item for item in items
                              if item.id == reference.get("item_id")), None)
                if match is not None:
                    return match
            # Load-compatible fallback for setups saved before identity refs.
            return None if text in ("", NONE) else _named(items, text)

        driver_part = _selected_part(
            self._inventory_options.get("Drivers", []), self._driver, driver_text)
        kart_part = _selected_part(
            self._inventory_options.get("Karts", []), self._chassis_cb, chassis_text)
        track_part = _selected_part(
            self._inventory_options.get("Tracks", []), self._track, track_text)
        driver = None if driver_text in ("", NONE) else _named(
            self._drivers, driver_text)
        chassis = None if chassis_text in ("", NONE) else _named(
            self._chassis, chassis_text)

        def _normalized(value):
            return "".join(ch for ch in (value or "").casefold()
                           if ch.isalnum())

        def _legacy_for_part(part, legacy_items):
            if part is None:
                return None
            part_values = [_normalized(part.name), _normalized(part.manufacturer),
                           _normalized(part.model)]
            return next((legacy for legacy in legacy_items
                         if any(_normalized(legacy.name) in value
                                for value in part_values if value)), None)

        if driver_part is not None:
            driver = _legacy_for_part(driver_part, self._drivers)
        if kart_part is not None:
            chassis = _legacy_for_part(kart_part, self._chassis)
        track = None if track_text in ("", NONE) else next(
            (value for value in self._tracks
             if value.casefold() == track_text.casefold()), None)

        invalid = []
        if (driver_text not in ("", NONE)
                and driver is None and driver_part is None):
            invalid.append("Driver")
        if (chassis_text not in ("", NONE)
                and chassis is None and kart_part is None):
            invalid.append("Kart / Chassis")
        if (track_text not in ("", NONE)
                and track is None and track_part is None):
            invalid.append("Track")
        if invalid:
            QMessageBox.information(
                self, "Choose a listed value",
                "Choose a value from the list for: " + ", ".join(invalid) + ".")
            return
        if (self._is_baseline and chassis is None
                and kart_part is None and track is None and track_part is None):
            QMessageBox.information(
                self, "Choose where this baseline applies",
                "Choose a kart, a track, or both. This lets new setups find "
                "the correct baseline automatically.")
            return
        # Work on a copy so a failed async save cannot mutate the Setup object
        # still cached by the Session/Setups views.
        setup = deepcopy(self._setup) if self._setup else Setup()
        setup.is_baseline = self._is_baseline
        setup.drive_folder_id = None if self._is_baseline else self._folder_id
        setup.drive_folder_path = (
            None if self._is_baseline else (self._folder_path or None))
        setup.track = track_part.name if track_part else track
        setup.name = self._name.text().strip() or (
            _baseline_name(
                kart_part.name if kart_part else (
                    chassis.name if chassis else None),
                setup.track)
            if self._is_baseline else None)
        if self._is_baseline and not self._name.text().strip():
            self._name.setText(setup.name)

        # Preserve packet metadata (e.g. telemetry) not represented in inputs.
        field_keys = {f.key for f in sf.all_value_fields()} | {
            "rl_hub_material", "rr_hub_material", "wheels",
            "driver_feedback", sf.RUNS_KEY, "_inventory_part_revisions",
            "_identity_part_revisions"}
        data = {}
        if self._setup and self._setup.data:
            data.update({k: v for k, v in self._setup.data.items()
                         if k not in field_keys})
        if sf.CUSTOM_FIELD_DEFINITIONS["fields"]:
            data["_custom_field_definitions"] = deepcopy(sf.CUSTOM_FIELD_DEFINITIONS)
        fields_by_key = {f.key: f for f in sf.all_value_fields()}
        self._store_current_run()
        baseline = self._baseline_run_setup or self._capture_run_setup()
        for key, edit in self._fields.items():
            value = str(baseline["values"].get(key) or "").strip()
            if not value:
                continue
            field = fields_by_key[key]
            if field.kind == "num":
                try:
                    if key.startswith("custom_"):
                        parsed = float(value)
                        data[key] = int(parsed) if parsed.is_integer() else parsed
                    else:
                        data[key] = float(value) if "." in value else int(value)
                except ValueError:
                    data[key] = value
            else:
                data[key] = value
        inventory_refs = {
            key: deepcopy(reference)
            for key in sf.INVENTORY_FIELD_CATEGORIES
            if isinstance((reference := baseline["inventory_refs"].get(key)), dict)
            and reference.get("item_id") and reference.get("revision")
        }
        if inventory_refs:
            data["_inventory_part_revisions"] = inventory_refs
        identity_refs = {}
        if driver_part is not None:
            identity_refs["driver"] = {
                "item_id": driver_part.id,
                "revision": int(driver_part.revision or 1),
                "name": driver_part.name,
            }
        if kart_part is not None:
            identity_refs["chassis"] = {
                "item_id": kart_part.id,
                "revision": int(kart_part.revision or 1),
                "name": kart_part.name,
            }
        if track_part is not None:
            identity_refs["track"] = {
                "item_id": track_part.id,
                "revision": int(track_part.revision or 1),
                "name": track_part.name,
            }
        if identity_refs:
            data["_identity_part_revisions"] = identity_refs
        runs = self._serialized_runs()
        if runs:
            data[sf.RUNS_KEY] = runs
        setup.data = data
        checks = self._maintenance.records()
        if checks:
            setup.data[maintenance.CHECKS_KEY] = checks
        setup.notes = self._notes.toPlainText().strip() or None
        setup.driver_id = driver.id if driver else None
        setup.driver_name = (
            driver_part.name if driver_part else (driver.name if driver else None))
        setup.chassis_id = chassis.id if chassis else None
        setup.chassis_name = (
            kart_part.name if kart_part else (chassis.name if chassis else None))
        previous_setup = deepcopy(self._setup)

        self._save_btn.setEnabled(False)
        self._save_btn.setText("Saving\u2026")
        self._saving = True
        self.setEnabled(False)

        def _write():
            # Record the edit before any network operation so an offline save
            # keeps its original editor and timestamp through later sync.
            db.record_setup_change(setup, previous_setup)
            # A first-time offline user may not have a cached Google profile.
            # Keep the setup locally until reconnect can attach the verified
            # identity; never let that lookup escape as a blocking save error.
            if db.setup_identity_pending(setup):
                return offline_store.save_pending_setup(setup)
            if setup.id and str(setup.id).startswith("offline-"):
                resolved_id = offline_store.resolve_setup_id(setup.id)
                if resolved_id:
                    setup.id = resolved_id
                else:
                    return offline_store.save_pending_setup(setup)
            try:
                if setup.is_baseline:
                    existing = db.fetch_conflicting_baseline(
                        setup.chassis_id, setup.track, exclude_id=setup.id)
                    if existing:
                        raise RuntimeError(
                            "A baseline already exists for this kart/track: "
                            f"{existing.name or 'Unnamed baseline'}. Open that "
                            "baseline from Setups to edit it.")
                return (
                    db.update_setup(setup, base=previous_setup) if setup.id
                    else db.create_setup(setup))
            except Exception:
                # Only turn a failed cloud save into an offline save when the
                # service is actually unavailable. Validation/constraint
                # failures must still be shown instead of queued forever.
                try:
                    cloud_available = db.check_connection()
                except Exception:
                    cloud_available = False
                if cloud_available:
                    raise
                return offline_store.save_pending_setup(setup)

        def _done(saved):
            self._saving = False
            self.setEnabled(True)
            saved.driver_name = saved.driver_name or setup.driver_name
            saved.chassis_name = saved.chassis_name or setup.chassis_name
            self._setup = saved
            self._maintenance.set_records((saved.data or {}).get(maintenance.CHECKS_KEY, {}))
            self.refresh_maintenance(reload_context=True)
            merged_concurrently = bool(
                getattr(saved, "_concurrent_merged", False))
            if merged_concurrently:
                # Refresh every displayed value so a later save cannot
                # accidentally reapply stale fields from before the merge.
                self._refresh_after_concurrent_merge(saved)
            self._initial_state = self._form_state()
            self._update_dirty_styles()
            self._save_btn.setEnabled(True)
            self._save_btn.setText(
                "Save Baseline" if self._is_baseline else "Save Setup")
            pending = bool(getattr(saved, "_offline_pending", False))
            self._set_save_status(
                ("\u2713  Saved offline \u00b7 syncs when internet returns"
                 if pending else
                 "\u2713  Saved \u00b7 combined with newer team changes"
                 if merged_concurrently else "\u2713  Saved"),
                config.COLOR_WARNING if pending else config.COLOR_SUCCESS)
            self.saved.emit(saved)
            if after_save:
                after_save()

        def _error(message):
            self._saving = False
            self.setEnabled(True)
            self._save_btn.setEnabled(True)
            self._save_btn.setText(
                "Save Baseline" if self._is_baseline else "Save Setup")
            QMessageBox.critical(self, "Error", message)

        run_async(self, _write, on_done=_done, on_error=_error)
