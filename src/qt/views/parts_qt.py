"""Sortable parts inventory with category-specific specifications."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from math import isfinite

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QInputDialog,
)

import database.client as db
import offline_store
from database.models import InventoryItem, InventoryRevision, Setup
from inventory_fields import (
    BASELINE_SETUP_CATEGORY, CATEGORIES, ENTITY_CATEGORIES,
    MAX_INVENTORY_COST, PARTS_CATEGORIES,
    MAX_INVENTORY_QUANTITY,
    fields_for_category,
)
from qt.workers import run_async
from qt.widgets import (
    DatePickerEdit, ScrollSafeComboBox, ScrollSafeDoubleSpinBox,
    make_combo_searchable,
)


ITEM_ID_ROLE = Qt.UserRole + 1
ALL_PARTS_CATEGORY = "All Parts"

TRACKING_OPTIONS = (
    ("Serialized", "serialized"), ("Set", "set"), ("Bulk", "bulk"))
STATUS_OPTIONS = (
    ("Available", "available"), ("Installed", "installed"),
    ("Reserved", "reserved"), ("Needs Service", "service"),
    ("Retired", "retired"),
)
CONDITION_OPTIONS = (
    ("New", "new"), ("Good", "good"), ("Worn", "worn"),
    ("Needs Service", "needs_service"), ("Damaged", "damaged"),
    ("Retired", "retired"),
)
DISPLAY_VALUES = {
    value: label for label, value in (
        STATUS_OPTIONS + CONDITION_OPTIONS + TRACKING_OPTIONS)}


def _save_inventory_changes(
        current: InventoryItem, target: InventoryItem) -> InventoryItem:
    """Save all Parts changes online, or durably queue them when offline."""
    if (getattr(current, "_offline_pending", False)
            or str(current.id or "").startswith("offline-part-")):
        return offline_store.queue_inventory_item(target, current)
    definition_changed = (
        PartMetadataDialog._definition_signature(target)
        != PartMetadataDialog._definition_signature(current))
    quantity_delta = round(
        float(target.quantity) - float(current.quantity), 3)
    archive_changed = bool(target.archived_at) != bool(current.archived_at)
    saved = current
    try:
        current_entity = current.category in ENTITY_CATEGORIES
        target_entity = target.category in ENTITY_CATEGORIES
        quantity_first = bool(
            quantity_delta and target_entity and not current_entity)
        if quantity_first:
            saved = db.adjust_inventory_stock(
                saved.id, quantity_delta, "Quantity changed from Part Metadata")
        if definition_changed:
            saved = db.update_inventory_item(target)
        if quantity_delta and not quantity_first:
            saved = db.adjust_inventory_stock(
                saved.id, quantity_delta, "Quantity changed from Part Metadata")
        if archive_changed:
            saved = db.archive_inventory_item(
                saved.id, archived=bool(target.archived_at))
        offline_store.cache_inventory_item(saved)
        return saved
    except Exception:
        if db.check_connection():
            raise
        return offline_store.queue_inventory_item(target, saved)


def _create_inventory_item(item: InventoryItem) -> InventoryItem:
    try:
        saved = db.create_inventory_item(item)
        offline_store.cache_inventory_item(saved)
        return saved
    except Exception:
        if db.check_connection():
            raise
        return offline_store.queue_inventory_item(item)


TABLE_COLUMNS = (
    ("Name", "name"),
    ("Manufacturer", "manufacturer"),
    ("Model", "model"),
    ("Part #", "part_number"),
    ("Tracking", "tracking_mode"),
    ("Quantity", "quantity"),
    ("Unit", "unit"),
    ("Status", "status"),
    ("Condition", "condition"),
    ("Supplier", "supplier"),
    ("Cost", "cost"),
)


def _part_type(item) -> str:
    """Return the inventory category used to group a Parts-list row."""

    if isinstance(item, Setup):
        return BASELINE_SETUP_CATEGORY
    return item.category or "Uncategorized"


def _spec_editor(field):
    if field.kind == "date":
        return DatePickerEdit()
    if field.options:
        editor = ScrollSafeComboBox()
        editor.addItem("— Select —", "")
        for option in field.options:
            editor.addItem(option, option)
        return editor
    return QLineEdit()


def _spec_value(editor):
    if isinstance(editor, QComboBox):
        return str(editor.currentData() or "").strip()
    return editor.text().strip()


def _set_spec_value(editor, value):
    text = "" if value is None else str(value)
    if isinstance(editor, QComboBox):
        index = editor.findData(text)
        if index < 0 and text:
            editor.addItem(text, text)
            index = editor.count() - 1
        editor.setCurrentIndex(max(0, index))
    else:
        editor.setText(text)


class _SortItem(QTableWidgetItem):
    """Table item that sorts numbers numerically and text case-insensitively."""

    def __init__(self, text="", sort_value=None):
        super().__init__(text)
        self._sort_key = self._key(sort_value)

    @staticmethod
    def _key(value):
        if value is None or value == "":
            return 1, 0, ""
        if isinstance(value, (int, float)):
            return 0, 0, float(value)
        return 0, 1, str(value).casefold()

    def __lt__(self, other):
        return self._sort_key < other._sort_key


class _PartCard(QFrame):
    """Quiet summary card for the current revision of one part."""

    def __init__(self, item: InventoryItem, parent=None, show_category=False):
        super().__init__(parent)
        self._item = item
        self.setObjectName("PartCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(14)

        identity = QVBoxLayout()
        identity.setSpacing(3)
        name = QLabel(item.name or "Unnamed part")
        name.setObjectName("H2")
        identity.addWidget(name)
        details = " · ".join(value for value in (
            item.manufacturer, item.model, item.part_number) if value)
        if details:
            sub = QLabel(details)
            sub.setObjectName("Muted")
            identity.addWidget(sub)
        row.addLayout(identity, 1)

        if show_category:
            row.addLayout(self._metric("Part Type", _part_type(item)))
        created = (item.created_at or "")[:10] or "—"
        row.addLayout(self._metric("Created", created))
        row.addLayout(self._metric("Revision", f"Rev {item.revision}"))
        quantity = f"{float(item.quantity):g}"
        row.addLayout(self._metric("Quantity", quantity))
        if item.archived_at:
            archived = QLabel("Archived")
            archived.setStyleSheet(
                "color:#ffb0b7; border:1px solid #7a3038; border-radius:8px;"
                "padding:5px 9px; font-weight:700;")
            row.addWidget(archived)
        self.set_selected(False)

    @staticmethod
    def _metric(label, value):
        layout = QVBoxLayout()
        layout.setSpacing(2)
        heading = QLabel(label)
        heading.setObjectName("Muted")
        content = QLabel(value)
        content.setStyleSheet("font-weight:700; font-size:14px;")
        layout.addWidget(heading)
        layout.addWidget(content)
        return layout

    def set_selected(self, selected):
        self.setStyleSheet(
            "QFrame#PartCard {"
            + ("background:#463037; border:2px solid #a01729;"
               if selected else
               "background:#34363b; border:1px solid #45474d;")
            + "border-radius:10px;}"
        )


class _BaselineCard(QFrame):
    """Parts-style summary for one reusable baseline setup definition."""

    def __init__(self, setup: Setup, parent=None, show_category=False):
        super().__init__(parent)
        self._setup = setup
        self.setObjectName("PartCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(14)

        identity = QVBoxLayout()
        identity.setSpacing(3)
        name = QLabel(setup.name or "Unnamed baseline")
        name.setObjectName("H2")
        identity.addWidget(name)
        details = " · ".join(value for value in (
            setup.chassis_name, setup.track) if value)
        sub = QLabel(details or "Choose a Kart and Track")
        sub.setObjectName("Muted")
        identity.addWidget(sub)
        row.addLayout(identity, 1)

        if show_category:
            row.addLayout(_PartCard._metric(
                "Part Type", BASELINE_SETUP_CATEGORY))
        updated = (setup.updated_at or setup.created_at or "")[:10] or "—"
        row.addLayout(_PartCard._metric("Updated", updated))
        row.addLayout(_PartCard._metric("Type", "Baseline"))
        self.set_selected(False)

    def set_selected(self, selected):
        self.setStyleSheet(
            "QFrame#PartCard {"
            + ("background:#463037; border:2px solid #a01729;"
               if selected else
               "background:#34363b; border:1px solid #45474d;")
            + "border-radius:10px;}"
        )


class PartDialog(QDialog):
    """Create/edit form for common inventory data plus category specs."""

    def __init__(self, category: str | None, item: InventoryItem | None = None,
                 source: InventoryItem | None = None, parent=None):
        super().__init__(parent)
        if item is not None and source is not None:
            raise ValueError("A part dialog cannot edit and copy at the same time")
        self._item = item
        self._source = source
        template = item or source
        self.result_item: InventoryItem | None = None
        self._common: dict[str, QWidget] = {}
        self._spec_edits: dict[str, QWidget] = {}
        self._spec_values: dict[str, dict[str, str]] = {}
        self._spec_category: str | None = None
        self._tracking_category: str | None = None
        self._non_entity_quantity = 1.0
        self._non_entity_tracking = "serialized"
        mode_title = (
            "Edit Part" if item else
            "Create Part from Copy" if source else
            "Add Part")
        self.setWindowTitle(mode_title)
        self.setModal(True)
        self.resize(620, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        title = QLabel(mode_title)
        title.setObjectName("H2")
        root.addWidget(title)
        if source:
            copy_hint = QLabel(
                f"Starting from {source.name}. Give this new part its own "
                "name; it will begin at Revision 1.")
            copy_hint.setObjectName("Muted")
            copy_hint.setWordWrap(True)
            root.addWidget(copy_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._form = QFormLayout(body)
        self._form.setContentsMargins(4, 4, 10, 8)
        self._form.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._category = ScrollSafeComboBox()
        for option in CATEGORIES:
            self._category.addItem(option, option)
        make_combo_searchable(
            self._category, "Choose a category…", allow_custom=False)
        self._category.enable_reselect_search()
        selected_category = template.category if template else category
        if selected_category in CATEGORIES:
            self._category.setCurrentIndex(
                self._category.findData(selected_category))
        else:
            self._category.setCurrentIndex(-1)
        self._form.addRow("Category", self._category)

        for label, key in (
            ("Name *", "name"),
            ("Manufacturer", "manufacturer"),
            ("Model", "model"),
            ("Part Number", "part_number"),
            ("Unit", "unit"),
            ("Supplier", "supplier"),
            ("Cost", "cost"),
        ):
            edit = DatePickerEdit() if key.endswith("_date") else QLineEdit()
            self._common[key] = edit
            self._form.addRow(label, edit)

        quantity = ScrollSafeDoubleSpinBox()
        quantity.setDecimals(3)
        quantity.setRange(0, MAX_INVENTORY_QUANTITY)
        quantity.setValue(1)
        if item:
            quantity.setEnabled(False)
            quantity.setToolTip("Use Adjust Stock to preserve quantity history")
        self._common["quantity"] = quantity
        self._form.addRow("Quantity", quantity)

        tracking = ScrollSafeComboBox()
        for label, value in TRACKING_OPTIONS:
            tracking.addItem(label, value)
        self._common["tracking_mode"] = tracking
        self._form.addRow("Tracking", tracking)

        status = ScrollSafeComboBox()
        for label, value in STATUS_OPTIONS:
            status.addItem(label, value)
        self._common["status"] = status
        self._form.addRow("Status", status)

        condition = ScrollSafeComboBox()
        for label, value in CONDITION_OPTIONS:
            condition.addItem(label, value)
        self._common["condition"] = condition
        self._form.addRow("Condition", condition)
        condition.setCurrentIndex(condition.findData("good"))

        self._spec_title = QLabel("Category Details")
        self._spec_title.setObjectName("H2")
        self._form.addRow(self._spec_title)
        self._spec_host = QWidget()
        self._spec_form = QFormLayout(self._spec_host)
        self._spec_form.setContentsMargins(0, 0, 0, 0)
        self._form.addRow(self._spec_host)
        self._category.currentIndexChanged.connect(self._category_selected)

        self._notes = QPlainTextEdit()
        self._notes.setFixedHeight(90)
        self._form.addRow("Notes", self._notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate(template)
        self._rebuild_specs(self._selected_category())
        if template:
            self._populate_specs(template)
        self._update_tracking_controls(self._selected_category())
        if source:
            self._common["name"].clear()
            self._common["name"].setPlaceholderText(
                f"New name based on {source.name}")
            self._common["name"].setFocus()

    def _populate(self, item):
        if not item:
            self._common["unit"].setText("each")
            return
        for key, widget in self._common.items():
            value = getattr(item, key, None)
            if isinstance(widget, QLineEdit):
                widget.setText("" if value is None else str(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value or 0))
            elif isinstance(widget, QComboBox):
                text = str(value or "")
                index = widget.findData(text)
                if index >= 0:
                    widget.setCurrentIndex(index)
                elif text:
                    widget.setCurrentText(text)
        self._notes.setPlainText(item.notes or "")

    def _clear_specs(self):
        while self._spec_form.count():
            child = self._spec_form.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._spec_edits.clear()

    def _selected_category(self) -> str:
        category = self._category.currentData()
        return category if category in CATEGORIES else ""

    def _category_selected(self, _index):
        category = self._selected_category()
        self._rebuild_specs(category)
        self._update_tracking_controls(category)

    def _rebuild_specs(self, category):
        if self._spec_category is not None:
            self._spec_values[self._spec_category] = {
                key: _spec_value(edit)
                for key, edit in self._spec_edits.items()}
        values = self._spec_values.get(category, {})
        self._clear_specs()
        for field in fields_for_category(category):
            edit = _spec_editor(field)
            _set_spec_value(edit, values.get(field.key, ""))
            self._spec_edits[field.key] = edit
            label = field.label + (f" ({field.unit})" if field.unit else "")
            self._spec_form.addRow(label, edit)
        self._spec_title.setVisible(bool(self._spec_edits))
        self._spec_host.setVisible(bool(self._spec_edits))
        self._spec_category = category

    def _update_tracking_controls(self, category):
        previous = self._tracking_category
        entity = category in ENTITY_CATEGORIES
        quantity = self._common["quantity"]
        tracking = self._common["tracking_mode"]
        if previous is not None and previous not in ENTITY_CATEGORIES:
            self._non_entity_quantity = quantity.value()
            self._non_entity_tracking = (
                tracking.currentData() or "serialized")
        if entity:
            quantity.setValue(1)
            tracking.setCurrentIndex(tracking.findData("serialized"))
        elif previous in ENTITY_CATEGORIES:
            quantity.setValue(self._non_entity_quantity)
            index = tracking.findData(self._non_entity_tracking)
            tracking.setCurrentIndex(max(0, index))
        elif previous is None:
            self._non_entity_quantity = quantity.value()
            self._non_entity_tracking = (
                tracking.currentData() or "serialized")
        quantity.setEnabled(not entity and self._item is None)
        tracking.setEnabled(not entity)
        if self._item is not None and not entity:
            quantity.setToolTip("Use Adjust Stock to preserve quantity history")
        self._tracking_category = category

    def _populate_specs(self, item):
        for key, edit in self._spec_edits.items():
            value = (item.specs or {}).get(key)
            _set_spec_value(edit, value)
        self._spec_values[self._selected_category()] = {
            key: _spec_value(edit) for key, edit in self._spec_edits.items()}

    @staticmethod
    def _optional_float(text):
        text = (text or "").strip().replace("$", "").replace(",", "")
        if not text:
            return None
        try:
            value = float(text)
            return value if isfinite(value) else None
        except ValueError:
            return None

    @staticmethod
    def _is_iso_date(value):
        if not value:
            return True
        try:
            parsed = date.fromisoformat(value)
            return parsed.isoformat() == value
        except ValueError:
            return False

    def _submit(self):
        category = self._selected_category()
        if not category:
            QMessageBox.information(
                self, "Category required", "Choose a category for this part.")
            self._category.setFocus()
            self._category.showPopup()
            return
        name = self._common["name"].text().strip()
        if not name:
            QMessageBox.information(self, "Name required", "Enter a part name.")
            self._common["name"].setFocus()
            return
        if (self._source is not None
                and name.casefold() == (self._source.name or "").casefold()):
            QMessageBox.information(
                self, "Use a new name",
                "Give the copied part a different name so it is easy to "
                "distinguish from its source.")
            self._common["name"].setFocus()
            self._common["name"].selectAll()
            return

        item = deepcopy(self._item) if self._item else InventoryItem()
        item.category = category
        for key in (
                "name", "manufacturer", "model", "part_number", "unit",
                "supplier"):
            value = self._common[key].text().strip()
            setattr(item, key, value or None)
        item.name = name
        item.unit = item.unit or "each"
        item.quantity = self._common["quantity"].value()
        item.tracking_mode = (
            self._common["tracking_mode"].currentData() or "serialized")
        item.status = self._common["status"].currentData() or "available"
        item.condition = self._common["condition"].currentData() or None
        if item.category in ENTITY_CATEGORIES:
            if (self._item is not None
                    and float(self._item.quantity) != 1):
                QMessageBox.information(
                    self, "Quantity must be 1",
                    "Adjust this record's stock to 1 before changing it to a "
                    "Kart or Driver.")
                return
            item.quantity = 1
            item.tracking_mode = "serialized"
        for key, label in (("cost", "Cost"),):
            raw_value = self._common[key].text().strip()
            parsed = self._optional_float(raw_value)
            if raw_value and parsed is None:
                QMessageBox.information(
                    self, "Enter a number", f"{label} must be a number.")
                self._common[key].setFocus()
                return
            maximum = MAX_INVENTORY_COST
            if parsed is not None and not 0 <= parsed <= maximum:
                QMessageBox.information(
                    self, "Number out of range",
                    f"{label} must be between 0 and {maximum:g}.")
                self._common[key].setFocus()
                return
            setattr(item, key, parsed)
        item.notes = self._notes.toPlainText().strip() or None

        all_spec_keys = {
            field.key for category in CATEGORIES
            for field in fields_for_category(category)}
        specs = {
            key: value for key, value in (item.specs or {}).items()
            if key not in all_spec_keys}
        fields = {field.key: field for field in fields_for_category(item.category)}
        for key, edit in self._spec_edits.items():
            value = _spec_value(edit)
            if not value:
                continue
            if fields[key].kind == "date" and not self._is_iso_date(value):
                QMessageBox.information(
                    self, "Use YYYY-MM-DD",
                    f"{fields[key].label} must use YYYY-MM-DD.")
                edit.setFocus()
                return
            if fields[key].kind in ("number", "integer"):
                parsed = self._optional_float(value)
                if parsed is None or (
                        fields[key].kind == "integer" and not parsed.is_integer()):
                    QMessageBox.information(
                        self, "Enter a number",
                        f"{fields[key].label} must be "
                        + ("a whole number." if fields[key].kind == "integer"
                           else "a number."))
                    edit.setFocus()
                    return
                specs[key] = (
                    int(parsed) if fields[key].kind == "integer" else parsed)
            else:
                specs[key] = value
        item.specs = specs
        self.result_item = item
        self.accept()


class MovementHistoryDialog(QDialog):
    """Read-only stock audit trail for one inventory item."""

    def __init__(self, item: InventoryItem, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Stock History - {item.name}")
        self.resize(820, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)

        title = QLabel(f"Stock History - {item.name}")
        title.setObjectName("H2")
        root.addWidget(title)
        self._status = QLabel("Loading history\u2026")
        self._status.setObjectName("Muted")
        root.addWidget(self._status)

        columns = ("Date", "Change", "Before", "After", "Reason", "By", "Notes")
        self._table = QTableWidget(0, len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch)
        root.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._close_btn = buttons.button(QDialogButtonBox.Close)
        self._close_btn.setEnabled(False)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._loading = True
        self._request_complete = False
        self._thread_complete = False
        thread = run_async(
            self,
            lambda: db.fetch_inventory_movements(item.id),
            on_done=self._loaded,
            on_error=self._load_error,
        )
        thread.finished.connect(self._request_finished)

    def closeEvent(self, event):
        if self._loading:
            event.ignore()
            self._status.setText("Finishing the history request…")
            return
        super().closeEvent(event)

    def reject(self):
        if self._loading:
            return
        super().reject()

    def _load_error(self, message):
        self._status.setText(f"Could not load history: {message}")
        self._request_complete = True
        self._update_close_state()

    def _request_finished(self):
        self._thread_complete = True
        self._update_close_state()

    def _update_close_state(self):
        if not (self._request_complete and self._thread_complete):
            return
        self._loading = False
        self._close_btn.setEnabled(True)

    def _loaded(self, movements):
        movements = list(movements or [])
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(movements))
        for row, movement in enumerate(movements):
            values = (
                (movement.created_at or "", movement.created_at),
                (f"{movement.quantity_delta:+g}", movement.quantity_delta),
                (f"{movement.quantity_before:g}", movement.quantity_before),
                (f"{movement.quantity_after:g}", movement.quantity_after),
                (movement.reason or "", movement.reason),
                (movement.created_by or "", movement.created_by),
                (movement.notes or "", movement.notes),
            )
            for column, (text, sort_value) in enumerate(values):
                self._table.setItem(
                    row, column, _SortItem(str(text), sort_value))
        self._table.setSortingEnabled(True)
        self._table.sortItems(0, Qt.DescendingOrder)
        self._status.setText(
            f"{len(movements)} movement(s)" if movements
            else "No stock movements recorded yet.")
        self._request_complete = True
        self._update_close_state()


class PartMetadataDialog(QDialog):
    """Editable current metadata with compact immutable revision comparison."""

    part_saved = Signal(object)

    def __init__(self, item: InventoryItem, parent=None):
        super().__init__(parent)
        self._current = deepcopy(item)
        self._editing = True
        self._busy = False
        self._initial_loading = False
        self._fields: dict[str, QWidget] = {}
        self._spec_edits: dict[str, QWidget] = {}
        self._spec_cache: dict[str, dict] = {}
        self._spec_category: str | None = None
        self.setWindowTitle(f"Part Metadata - {item.name}")
        self.resize(1120, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        self._title = QLabel(f"Part Metadata - {item.name}")
        self._title.setObjectName("H1")
        root.addWidget(self._title)
        self._mode = QLabel(
            "Edit the values below. Metadata creates a revision; stock changes keep their own history.")
        self._mode.setObjectName("Muted")
        root.addWidget(self._mode)

        splitter = QSplitter(Qt.Horizontal)
        metadata = QScrollArea()
        metadata.setWidgetResizable(True)
        metadata_body = QWidget()
        metadata_layout = QVBoxLayout(metadata_body)
        metadata_layout.setContentsMargins(14, 4, 14, 12)
        metadata_layout.setSpacing(10)
        self._revision_heading = QLabel("")
        self._revision_heading.setObjectName("H2")
        metadata_layout.addWidget(self._revision_heading)
        self._audit = QLabel("")
        self._audit.setObjectName("Muted")
        self._audit.setWordWrap(True)
        metadata_layout.addWidget(self._audit)

        form = QFormLayout()
        form.setSpacing(8)
        self._common_form = form
        self._category = ScrollSafeComboBox()
        self._category.addItems(CATEGORIES)
        self._category.currentTextChanged.connect(self._category_changed)
        self._fields["category"] = self._category
        form.addRow("Category", self._category)
        for label, key in (
            ("Name", "name"),
            ("Manufacturer", "manufacturer"),
            ("Model", "model"),
            ("Part Number", "part_number"),
            ("Unit", "unit"),
            ("Supplier", "supplier"),
            ("Cost", "cost"),
        ):
            edit = QLineEdit()
            self._fields[key] = edit
            form.addRow(label, edit)
        quantity = ScrollSafeDoubleSpinBox()
        quantity.setDecimals(3)
        quantity.setRange(0, MAX_INVENTORY_QUANTITY)
        quantity.setToolTip(
            "Set the quantity in stock. Saving records the change in Stock History.")
        self._fields["quantity"] = quantity
        form.addRow("Quantity in Stock", quantity)

        for label, key, options in (
            ("Tracking", "tracking_mode", TRACKING_OPTIONS),
            ("Status", "status", STATUS_OPTIONS),
            ("Condition", "condition", CONDITION_OPTIONS),
        ):
            combo = ScrollSafeComboBox()
            for option_label, value in options:
                combo.addItem(option_label, value)
            self._fields[key] = combo
            form.addRow(label, combo)
        metadata_layout.addLayout(form)

        self._spec_title = QLabel("Category Details")
        self._spec_title.setObjectName("H2")
        metadata_layout.addWidget(self._spec_title)
        self._spec_host = QWidget()
        self._spec_form = QFormLayout(self._spec_host)
        self._spec_form.setContentsMargins(0, 0, 0, 0)
        self._spec_form.setSpacing(8)
        metadata_layout.addWidget(self._spec_host)
        self._notes_label = QLabel("Notes")
        metadata_layout.addWidget(self._notes_label)
        self._notes = QPlainTextEdit()
        self._notes.setFixedHeight(90)
        metadata_layout.addWidget(self._notes)
        metadata_layout.addStretch(1)
        metadata.setWidget(metadata_body)
        splitter.addWidget(metadata)

        comparison = QFrame()
        comparison.setObjectName("Card")
        comparison_layout = QVBoxLayout(comparison)
        comparison_layout.setContentsMargins(14, 14, 14, 14)
        comparison_layout.setSpacing(9)
        comparison_layout.addWidget(QLabel("Revision Comparison"))
        self._revisions = ScrollSafeComboBox()
        self._revisions.currentIndexChanged.connect(self._revision_selected)
        comparison_layout.addWidget(self._revisions)
        self._selection_badge = QLabel("Current revision selected")
        self._selection_badge.setObjectName("RevisionSelected")
        comparison_layout.addWidget(self._selection_badge)
        self._restore_btn = QPushButton("Restore Selected as New Revision")
        self._restore_btn.setObjectName("Primary")
        self._restore_btn.clicked.connect(self._restore_revision)
        self._restore_btn.setToolTip(
            "Copy the selected revision's metadata into a new latest revision; "
            "current stock quantity and all history stay unchanged")
        comparison_layout.addWidget(self._restore_btn)
        self._revision_status = QLabel("Loading revisions\u2026")
        self._revision_status.setObjectName("Muted")
        self._revision_status.setWordWrap(True)
        comparison_layout.addWidget(self._revision_status)
        self._comparison_scroll = QScrollArea()
        self._comparison_scroll.setWidgetResizable(True)
        self._comparison_body = QWidget()
        self._comparison_layout = QVBoxLayout(self._comparison_body)
        self._comparison_layout.setContentsMargins(0, 4, 4, 4)
        self._comparison_layout.setSpacing(7)
        self._comparison_scroll.setWidget(self._comparison_body)
        comparison_layout.addWidget(self._comparison_scroll, 1)
        splitter.addWidget(comparison)
        splitter.setSizes([720, 380])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_btn = QPushButton("Reset Changes")
        self._cancel_btn.setObjectName("Ghost")
        self._cancel_btn.clicked.connect(self._cancel_edit)
        actions.addWidget(self._cancel_btn)
        self._save_btn = QPushButton("Save Changes")
        self._save_btn.setObjectName("Primary")
        self._save_btn.clicked.connect(self._save_revision)
        actions.addWidget(self._save_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        actions.addWidget(self._close_btn)
        root.addLayout(actions)

        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._save_revision)
        self._save_btn.setToolTip(
            "Save metadata as a revision and record quantity changes in Stock History (Ctrl+S)")

        self._populate(self._current, current_quantity=True)
        self._set_editing(True)
        self._load_revisions()

    def closeEvent(self, event):
        if self._busy or self._initial_loading or self._has_running_requests():
            event.ignore()
            return
        if not self._confirm_discard_changes():
            event.ignore()
            return
        super().closeEvent(event)

    def accept(self):
        if (not self._busy and not self._initial_loading
                and not self._has_running_requests()
                and self._confirm_discard_changes()):
            super().accept()

    def reject(self):
        self.accept()

    def _has_running_requests(self):
        return any(
            thread.isRunning()
            for thread, _worker, _proxy in getattr(self, "_async_reg", set()))

    def _confirm_discard_changes(self):
        if not self._editing:
            return True
        try:
            edited = self._build_edited_item()
            changed = (
                self._definition_signature(edited)
                != self._definition_signature(self._current)
                or float(edited.quantity) != float(self._current.quantity))
        except ValueError:
            changed = True
        if not changed:
            return True
        return QMessageBox.question(
            self, "Discard changes?",
            "These part changes have not been saved. Discard them?",
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel) == QMessageBox.Discard

    def _category_changed(self, category):
        if self._spec_category is not None:
            self._spec_cache[self._spec_category] = {
                key: _spec_value(edit)
                for key, edit in self._spec_edits.items()}
        self._rebuild_specs(category, self._spec_cache.get(category, {}))
        self._update_edit_controls()

    def _rebuild_specs(self, category, values=None):
        while self._spec_form.count():
            child = self._spec_form.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._spec_edits.clear()
        values = values or {}
        for field in fields_for_category(category):
            edit = _spec_editor(field)
            _set_spec_value(edit, values.get(field.key))
            self._spec_edits[field.key] = edit
            label = field.label + (f" ({field.unit})" if field.unit else "")
            self._spec_form.addRow(label, edit)
        self._spec_category = category
        self._set_spec_read_only(not self._editing)

    def _populate(self, item: InventoryItem, current_quantity=False):
        self._spec_cache = {item.category: dict(item.specs or {})}
        self._category.blockSignals(True)
        self._category.setCurrentText(item.category)
        self._category.blockSignals(False)
        self._rebuild_specs(item.category, item.specs or {})
        for key in ("name", "manufacturer", "model", "part_number",
                    "unit", "supplier"):
            value = getattr(item, key, None)
            self._fields[key].setText("" if value is None else str(value))
        self._fields["cost"].setText(
            "" if item.cost is None else f"{float(item.cost):g}")
        quantity = self._current.quantity if current_quantity else item.quantity
        self._fields["quantity"].setValue(float(quantity))
        for key in ("tracking_mode", "status", "condition"):
            combo = self._fields[key]
            index = combo.findData(getattr(item, key, None))
            combo.setCurrentIndex(max(0, index))
        self._notes.setPlainText(item.notes or "")
        self._revision_heading.setText(
            f"Current · Revision {self._current.revision}")
        self._audit.setText(
            f"Created {self._format_timestamp(item.created_at)}"
            + (f" by {item.created_by}" if item.created_by else "")
            + (f"  ·  Last changed {self._format_timestamp(item.updated_at)}"
               if item.updated_at else "")
            + (f" by {item.updated_by}" if item.updated_by else ""))

    @staticmethod
    def _format_timestamp(value):
        if not value:
            return "Unknown"
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%b %d, %Y · %I:%M %p")
        except (TypeError, ValueError):
            return str(value)

    def _set_spec_read_only(self, read_only):
        for edit in self._spec_edits.values():
            if isinstance(edit, QComboBox):
                edit.setFocusPolicy(Qt.NoFocus if read_only else Qt.StrongFocus)
            else:
                edit.setReadOnly(read_only)
            edit.setAttribute(Qt.WA_TransparentForMouseEvents, read_only)
            self._set_metadata_style(edit, read_only)

    @staticmethod
    def _set_metadata_style(widget, read_only):
        widget.setProperty("metadataReadOnly", read_only)
        if isinstance(widget, DatePickerEdit):
            widget.setClearButtonEnabled(not read_only)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _set_editing(self, editing):
        # The current revision is always editable. Access roles can layer on
        # top of this later without making today's workflow feel locked down.
        self._editing = True
        for key, widget in self._fields.items():
            if isinstance(widget, QComboBox):
                widget.setEnabled(True)
                widget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                widget.setFocusPolicy(Qt.StrongFocus)
            else:
                widget.setReadOnly(False)
                widget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._set_metadata_style(widget, False)
        self._notes.setReadOnly(False)
        self._notes.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._set_metadata_style(self._notes, False)
        self._set_spec_read_only(False)
        for key, widget in self._fields.items():
            widget.setVisible(True)
            label = self._common_form.labelForField(widget)
            if label:
                label.setVisible(True)
        for edit in self._spec_edits.values():
            edit.setVisible(True)
            label = self._spec_form.labelForField(edit)
            if label:
                label.setVisible(True)
        self._spec_title.setVisible(bool(self._spec_edits))
        self._spec_host.setVisible(bool(self._spec_edits))
        self._notes_label.setVisible(True)
        self._notes.setVisible(True)
        self._revisions.setEnabled(not self._busy)
        self._cancel_btn.setVisible(True)
        self._save_btn.setVisible(True)
        self._close_btn.setEnabled(not self._busy)
        self._restore_btn.setVisible(True)
        self._mode.setText(
            "Edit the current values below. Metadata creates a revision; "
            "quantity updates Stock History. Older setup links remain unchanged.")
        self._update_edit_controls()

    def _update_edit_controls(self):
        entity = self._category.currentText() in ENTITY_CATEGORIES
        quantity = self._fields.get("quantity")
        if isinstance(quantity, QDoubleSpinBox):
            if entity:
                quantity.setValue(1)
            quantity.setEnabled(not self._busy and not entity)
            quantity.setToolTip(
                "Drivers, Karts, and Tracks always have quantity 1."
                if entity else
                "Set the quantity in stock. Saving records the change in Stock History.")
        tracking = self._fields.get("tracking_mode")
        if isinstance(tracking, QComboBox):
            tracking.setAttribute(
                Qt.WA_TransparentForMouseEvents,
                not self._editing or entity)
            tracking.setFocusPolicy(
                Qt.StrongFocus if self._editing and not entity else Qt.NoFocus)
            if self._editing and entity:
                tracking.setCurrentIndex(tracking.findData("serialized"))
        revision = self._revisions.currentData()
        self._restore_btn.setEnabled(
            not self._busy and revision is not None
            and revision.revision != self._current.revision)

    def _begin_edit(self):
        if self._initial_loading:
            return
        self._populate(self._current, current_quantity=True)
        self._set_editing(True)
        self._fields["name"].setFocus()

    def _cancel_edit(self):
        self._populate(self._current, current_quantity=True)
        self._set_editing(True)

    def _build_edited_item(self):
        name = self._fields["name"].text().strip()
        if not name:
            raise ValueError("Enter a part name.")
        item = deepcopy(self._current)
        item.category = self._category.currentText()
        for key in ("name", "manufacturer", "model", "part_number",
                    "unit", "supplier"):
            value = self._fields[key].text().strip()
            setattr(item, key, value or None)
        item.name = name
        item.unit = item.unit or "each"
        item.quantity = self._fields["quantity"].value()
        if item.category in ENTITY_CATEGORIES and float(item.quantity) != 1:
            raise ValueError(
                "Karts, Drivers, and Tracks must have quantity 1 before changing "
                "this part to that category.")
        item.tracking_mode = (
            "serialized" if item.category in ENTITY_CATEGORIES
            else self._fields["tracking_mode"].currentData() or "serialized")
        item.status = self._fields["status"].currentData() or "available"
        item.condition = self._fields["condition"].currentData() or "good"
        raw_cost = self._fields["cost"].text().strip()
        parsed_cost = PartDialog._optional_float(raw_cost)
        if raw_cost and parsed_cost is None:
            raise ValueError("Cost must be a number.")
        if parsed_cost is not None and not 0 <= parsed_cost <= MAX_INVENTORY_COST:
            raise ValueError(
                f"Cost must be between 0 and {MAX_INVENTORY_COST:g}.")
        item.cost = parsed_cost
        item.notes = self._notes.toPlainText().strip() or None
        all_spec_keys = {
            field.key for category in CATEGORIES
            for field in fields_for_category(category)}
        specs = {
            key: value for key, value in (item.specs or {}).items()
            if key not in all_spec_keys}
        definitions = {
            field.key: field for field in fields_for_category(item.category)}
        for key, edit in self._spec_edits.items():
            value = _spec_value(edit)
            if not value:
                continue
            definition = definitions[key]
            if (definition.kind == "date"
                    and not PartDialog._is_iso_date(value)):
                raise ValueError(
                    f"{definition.label} must use YYYY-MM-DD.")
            if definition.kind in ("number", "integer"):
                parsed = PartDialog._optional_float(value)
                if parsed is None or (
                        definition.kind == "integer" and not parsed.is_integer()):
                    raise ValueError(
                        f"{definition.label} must be "
                        + ("a whole number." if definition.kind == "integer"
                           else "a number."))
                specs[key] = int(parsed) if definition.kind == "integer" else parsed
            else:
                specs[key] = value
        item.specs = specs
        return item

    def _save_revision(self):
        try:
            target = self._build_edited_item()
        except ValueError as exc:
            QMessageBox.information(self, "Check part metadata", str(exc))
            return
        self._write_revision(target, "Saving changes\u2026")

    def _restore_revision(self):
        revision = self._revisions.currentData()
        if revision is None or revision.revision == self._current.revision:
            return
        if QMessageBox.question(
                self, "Restore as New Revision",
                f"Create Revision {self._current.revision + 1} from "
                f"Revision {revision.revision}?\n\n"
                "The selected revision's metadata will become the new current "
                "definition. Current stock quantity will stay unchanged.\n\n"
                "No history will be deleted, and existing setups will remain "
                "linked to their original revisions.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        target = InventoryItem.from_dict(revision.snapshot or {})
        target.id = self._current.id
        target.revision = self._current.revision
        target.quantity = self._current.quantity
        target.archived_at = self._current.archived_at
        target.created_at = self._current.created_at
        target.created_by = self._current.created_by
        self._write_revision(
            target, f"Restoring Revision {revision.revision}\u2026")

    def _write_revision(self, target, status):
        if self._busy:
            return
        definition_changed = (
            self._definition_signature(target)
            != self._definition_signature(self._current))
        quantity_delta = round(
            float(target.quantity) - float(self._current.quantity), 3)
        quantity_changed = quantity_delta != 0
        if not definition_changed and not quantity_changed:
            QMessageBox.information(
                self, "No changes", "The part already matches these values.")
            return
        self._busy = True
        self._revision_status.setText(status)
        self.setEnabled(False)

        def _write():
            saved = _save_inventory_changes(self._current, target)
            if getattr(saved, "_offline_pending", False):
                revisions = [InventoryRevision(
                    item_id=saved.id,
                    revision=saved.revision,
                    snapshot=offline_store._inventory_payload(saved),
                    created_by=saved.updated_by or saved.created_by,
                    created_at=saved.updated_at,
                )]
            else:
                revisions = db.fetch_inventory_revisions(saved.id)
            return saved, revisions, definition_changed, quantity_changed

        run_async(
            self, _write,
            on_done=self._revision_saved,
            on_error=self._save_error,
        )

    def _revision_saved(self, result):
        saved, revisions, definition_changed, quantity_changed = result
        self._busy = False
        self.setEnabled(True)
        self._current = saved
        self.setWindowTitle(f"Part Metadata - {saved.name}")
        self._title.setText(f"Part Metadata - {saved.name}")
        self.part_saved.emit(saved)
        self._populate_revision_list(revisions)
        self._populate(saved, current_quantity=True)
        self._set_editing(False)
        if getattr(saved, "_offline_pending", False):
            self._revision_status.setText(
                "Saved offline. This Parts update will sync when internet returns.")
            return
        if definition_changed and quantity_changed:
            message = (
                f"Saved Revision {saved.revision} and updated stock. "
                "Earlier revisions and stock history remain available.")
        elif definition_changed:
            message = (
                f"Saved Revision {saved.revision}. "
                "All earlier revisions remain available.")
        else:
            message = "Quantity updated. The change is recorded in Stock History."
        self._revision_status.setText(message)

    def _save_error(self, message):
        self._busy = False
        self.setEnabled(True)
        self._revision_status.setText("The changes were not saved.")
        QMessageBox.critical(self, "Could not save changes", message)
        self._set_editing(self._editing)

    @staticmethod
    def _definition_signature(item):
        definition = item.to_dict(include_nulls=True)
        for key in (
                "quantity", "archived_at", "created_by", "updated_by"):
            definition.pop(key, None)
        return definition

    def _load_revisions(self):
        self._initial_loading = True
        self._close_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._restore_btn.setEnabled(False)
        run_async(
            self,
            lambda: db.fetch_inventory_revisions(self._current.id),
            on_done=self._initial_revisions_loaded,
            on_error=self._initial_revisions_error,
        )

    def _initial_revisions_loaded(self, revisions):
        self._populate_revision_list(revisions)
        self._finish_initial_load()

    def _initial_revisions_error(self, message):
        self._populate_revision_list([InventoryRevision(
            item_id=self._current.id,
            revision=self._current.revision,
            snapshot=offline_store._inventory_payload(self._current),
            created_by=self._current.updated_by or self._current.created_by,
            created_at=self._current.updated_at or self._current.created_at,
        )])
        self._revision_status.setText(
            "Offline Parts copy — current values are editable; full revision "
            "history returns after reconnection.")
        self._finish_initial_load()

    def _finish_initial_load(self):
        self._initial_loading = False
        self._close_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._revisions.setEnabled(True)
        self._update_edit_controls()

    def _populate_revision_list(self, revisions):
        revisions = list(revisions or [])
        self._revisions.blockSignals(True)
        self._revisions.clear()
        current_row = -1
        for row, revision in enumerate(revisions):
            created = self._format_timestamp(revision.created_at)
            label = (
                f"Current · Revision {revision.revision}"
                if revision.revision == self._current.revision
                else f"Revision {revision.revision} · {created}")
            self._revisions.addItem(label, revision)
            if revision.revision == self._current.revision:
                current_row = row
        self._revisions.blockSignals(False)
        if current_row >= 0:
            self._revisions.setCurrentIndex(current_row)
        elif revisions:
            self._revisions.setCurrentIndex(0)
        self._revision_status.setText(
            (f"{len(revisions)} permanent revision(s). Choose an older one "
             "to compare it with the editable current values.") if revisions
            else "No revision snapshots found. Run the latest Parts migration.")
        self._revision_selected(self._revisions.currentIndex())
        self._update_edit_controls()

    def _revision_selected(self, _index):
        revision = self._revisions.currentData()
        if revision is None:
            self._render_comparison(None)
            return
        if revision.revision == self._current.revision:
            self._selection_badge.setText(
                f"Current selected · Revision {revision.revision}")
            self._render_comparison(None)
        else:
            part = InventoryItem.from_dict(revision.snapshot or {})
            self._selection_badge.setText(
                f"Selected for comparison · Revision {revision.revision}")
            self._render_comparison(part, revision)
        self._update_edit_controls()

    def _render_comparison(self, part, revision=None):
        while self._comparison_layout.count():
            child = self._comparison_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if part is None:
            hint = QLabel(
                "The newest revision is always editable on the left. Select "
                "an older revision above to see what changed before restoring it.")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            self._comparison_layout.addWidget(hint)
            self._comparison_layout.addStretch(1)
            return

        heading = QLabel(f"Revision {revision.revision}")
        heading.setObjectName("H2")
        self._comparison_layout.addWidget(heading)
        saved = QLabel(
            f"Saved {self._format_timestamp(revision.created_at)}"
            + (f" by {revision.created_by}" if revision.created_by else ""))
        saved.setObjectName("Muted")
        saved.setWordWrap(True)
        self._comparison_layout.addWidget(saved)

        common = (
            ("Category", part.category, self._current.category),
            ("Name", part.name, self._current.name),
            ("Manufacturer", part.manufacturer, self._current.manufacturer),
            ("Model", part.model, self._current.model),
            ("Part Number", part.part_number, self._current.part_number),
            ("Unit", part.unit, self._current.unit),
            ("Tracking", DISPLAY_VALUES.get(part.tracking_mode, part.tracking_mode),
             DISPLAY_VALUES.get(self._current.tracking_mode,
                                self._current.tracking_mode)),
            ("Status", DISPLAY_VALUES.get(part.status, part.status),
             DISPLAY_VALUES.get(self._current.status, self._current.status)),
            ("Condition", DISPLAY_VALUES.get(part.condition, part.condition),
             DISPLAY_VALUES.get(self._current.condition, self._current.condition)),
            ("Supplier", part.supplier, self._current.supplier),
            ("Cost", part.cost, self._current.cost),
            ("Notes", part.notes, self._current.notes),
        )
        for label, old_value, current_value in common:
            self._add_comparison_value(label, old_value, current_value)

        spec_keys = list(dict.fromkeys(
            list((part.specs or {}).keys())
            + list((self._current.specs or {}).keys())))
        if spec_keys:
            specs_heading = QLabel("Category Details")
            specs_heading.setObjectName("H2")
            self._comparison_layout.addWidget(specs_heading)
        definitions = {
            field.key: field.display_label
            for field in fields_for_category(part.category)}
        for key in spec_keys:
            self._add_comparison_value(
                definitions.get(key, key.replace("_", " ").title()),
                (part.specs or {}).get(key),
                (self._current.specs or {}).get(key))
        self._comparison_layout.addStretch(1)

    def _add_comparison_value(self, label, old_value, current_value):
        if old_value in (None, "") and current_value in (None, ""):
            return
        caption = QLabel(label)
        caption.setObjectName("Muted")
        self._comparison_layout.addWidget(caption)
        value = QLabel("—" if old_value in (None, "") else str(old_value))
        value.setWordWrap(True)
        value.setObjectName(
            "ComparisonChanged" if old_value != current_value
            else "ComparisonValue")
        value.setToolTip(
            "Different from the current revision" if old_value != current_value
            else "Same as the current revision")
        self._comparison_layout.addWidget(value)


class PartsView(QWidget):
    """Left-nav inventory page with lazy category loading and local filtering."""

    new_baseline = Signal()
    open_baseline = Signal(object)
    copy_baseline = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._loading = False
        self._writing = False
        self._load_generation = 0
        self._refresh_pending = False
        self._items: list[InventoryItem] = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Parts Inventory")
        title.setObjectName("H1")
        header.addWidget(title)
        self._count = QLabel("")
        self._count.setObjectName("Muted")
        header.addWidget(self._count)
        header.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("Ghost")
        self._refresh_btn.clicked.connect(self.refresh)
        self._add_btn = QPushButton("+ Add Part")
        self._add_btn.setObjectName("Primary")
        self._add_btn.clicked.connect(self._add_part)
        for button in (self._refresh_btn, self._add_btn):
            button.setCursor(Qt.PointingHandCursor)
            header.addWidget(button)
        root.addLayout(header)

        filters = QHBoxLayout()
        self._category_filter = ScrollSafeComboBox()
        self._category_filter.setMinimumWidth(220)
        self._category_filter.addItem(ALL_PARTS_CATEGORY, ALL_PARTS_CATEGORY)
        for category in PARTS_CATEGORIES:
            self._category_filter.addItem(category, category)
        make_combo_searchable(
            self._category_filter, "All Parts — search categories…",
            allow_custom=False)
        self._category_filter.enable_reselect_search()
        # An empty editable field is the All Parts state, which means the user
        # can immediately type without first deleting the words "All Parts".
        self._category_filter.setCurrentIndex(-1)
        self._category_filter.setToolTip(
            "Type to find a category, or leave All Parts selected")
        self._category_filter.currentIndexChanged.connect(
            self._category_changed)
        filters.addWidget(self._category_filter)
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search name, manufacturer, model, part number\u2026")
        self._search.textChanged.connect(self._render)
        filters.addWidget(self._search, 1)
        self._status_filter = ScrollSafeComboBox()
        self._status_filter.addItem("All statuses", None)
        for label, value in STATUS_OPTIONS:
            self._status_filter.addItem(label, value)
        self._status_filter.currentTextChanged.connect(self._render)
        filters.addWidget(self._status_filter)
        self._sort = ScrollSafeComboBox()
        self._sort.addItem("Part type A–Z", "category_name")
        self._sort.addItem("Newest first", "newest")
        self._sort.addItem("Name A–Z", "name")
        self._sort.addItem("Name Z–A", "name_desc")
        self._sort.addItem("Highest revision", "revision")
        self._sort.addItem("Quantity high–low", "quantity")
        self._sort.currentTextChanged.connect(self._render)
        filters.addWidget(self._sort)
        self._show_archived = QCheckBox("Show archived")
        self._show_archived.toggled.connect(self.refresh)
        filters.addWidget(self._show_archived)
        root.addLayout(filters)

        list_host = QWidget()
        table_layout = QVBoxLayout(list_host)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(8)
        self._parts_list = QListWidget()
        self._parts_list.setObjectName("PartList")
        self._parts_list.setSpacing(5)
        self._parts_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._parts_list.itemDoubleClicked.connect(self._open_metadata)
        table_layout.addWidget(self._parts_list, 1)

        actions = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("Muted")
        actions.addWidget(self._status, 1)
        self._history_btn = QPushButton("Stock History")
        self._history_btn.setObjectName("Ghost")
        self._history_btn.clicked.connect(self._show_history)
        self._metadata_btn = QPushButton("Open Metadata")
        self._metadata_btn.setObjectName("Primary")
        self._metadata_btn.clicked.connect(self._open_metadata)
        self._copy_btn = QPushButton("Create Copy")
        self._copy_btn.setObjectName("Ghost")
        self._copy_btn.setToolTip(
            "Create a separate Revision 1 part using the selected part's data")
        self._copy_btn.clicked.connect(self._copy_selected)
        self._stock_btn = QPushButton("Adjust Stock")
        self._stock_btn.setObjectName("Ghost")
        self._stock_btn.clicked.connect(self._adjust_stock)
        self._archive_btn = QPushButton("Archive Selected")
        self._archive_btn.setObjectName("Danger")
        self._archive_btn.clicked.connect(self._archive_selected)
        for button in (
                self._history_btn, self._metadata_btn, self._copy_btn,
                self._stock_btn, self._archive_btn):
            button.setCursor(Qt.PointingHandCursor)
            actions.addWidget(button)
        self._parts_list.itemSelectionChanged.connect(self._selection_changed)
        self._selection_changed()
        table_layout.addLayout(actions)
        root.addWidget(list_host, 1)

    def on_show(self):
        if not self._loaded:
            self.refresh()

    def _category_changed(self, _index):
        baseline_mode = self.current_category() == BASELINE_SETUP_CATEGORY
        self._status_filter.setEnabled(not baseline_mode)
        self._show_archived.setEnabled(not baseline_mode)
        self._add_btn.setText(
            "+ New Baseline" if baseline_mode else "+ Add Part")
        if self._loaded:
            self.refresh()

    def current_category(self) -> str:
        category = self._category_filter.currentData()
        return category if category in PARTS_CATEGORIES else ALL_PARTS_CATEGORY

    def refresh(self, *_args):
        self._load_generation += 1
        generation = self._load_generation
        if self._loading or self._writing:
            self._refresh_pending = True
            return
        self._start_refresh(generation)

    def _start_refresh(self, generation):
        self._loading = True
        self._set_busy(True)
        category = self.current_category()
        include_archived = self._show_archived.isChecked()
        self._status.setText(f"Loading {category.lower()}\u2026")

        def _fetch():
            if category == BASELINE_SETUP_CATEGORY:
                items = db.fetch_baselines()
            elif category == ALL_PARTS_CATEGORY:
                items = db.fetch_inventory_items(None, include_archived)
                offline_store.cache_inventory(items)
                items = offline_store.merge_pending_inventory(items)
                if not include_archived:
                    items = [item for item in items if not item.archived_at]
                items.extend(db.fetch_baselines())
            else:
                items = db.fetch_inventory_items(category, include_archived)
                items = [item for item in offline_store.merge_pending_inventory(
                    items) if item.category == category
                    and (include_archived or not item.archived_at)]
            return generation, category, include_archived, items

        run_async(
            self,
            _fetch,
            on_done=self._loaded_items,
            on_error=lambda message: self._load_error(generation, message),
        )

    def _loaded_items(self, result):
        generation, category, include_archived, items = result
        self._loading = False
        if (generation != self._load_generation
                or category != self.current_category()
                or include_archived != self._show_archived.isChecked()):
            self._set_busy(False)
            self._refresh_pending = True
            self._run_pending_refresh()
            return
        self._loaded = True
        self._items = list(items or [])
        self._set_busy(False)
        self._status.setText("")
        self._render()
        self._run_pending_refresh()

    def _load_error(self, generation, message):
        self._loading = False
        self._set_busy(False)
        if generation == self._load_generation:
            category = self.current_category()
            baselines = offline_store.load_setups("__baselines__")
            if category == BASELINE_SETUP_CATEGORY:
                cached = baselines
            elif category == ALL_PARTS_CATEGORY:
                cached = offline_store.load_inventory() + baselines
            else:
                cached = offline_store.load_inventory(category)
            if not self._show_archived.isChecked():
                cached = [
                    item for item in cached
                    if isinstance(item, Setup) or not item.archived_at]
            if cached:
                self._loaded = True
                self._items = cached
                self._status.setText(
                    "Offline Parts copy — changes from other devices appear "
                    "after reconnection")
                self._render()
            elif category == BASELINE_SETUP_CATEGORY:
                self._status.setText(
                    f"Could not load baseline setups: {message}")
            else:
                self._status.setText(
                    f"Could not load Parts: {message}. Run the Parts inventory "
                    "migration if this is the first local build.")
            if not cached:
                self._parts_list.clear()
                self._count.setText("Unavailable")
        self._run_pending_refresh()

    def _run_pending_refresh(self):
        if (not self._refresh_pending or self._loading or self._writing):
            return
        self._refresh_pending = False
        self._start_refresh(self._load_generation)

    def _set_busy(self, busy):
        self._refresh_btn.setEnabled(not busy)
        self._add_btn.setEnabled(not busy)
        self._category_filter.setEnabled(not busy)
        self._show_archived.setEnabled(
            not busy and self.current_category() != BASELINE_SETUP_CATEGORY)
        self._parts_list.setEnabled(not busy)
        self._refresh_btn.setText("Working\u2026" if busy else "Refresh")
        self._selection_changed()

    def _matches(self, item):
        if isinstance(item, Setup):
            terms = self._search.text().strip().casefold().split()
            blob = " ".join(filter(None, (
                item.name, item.chassis_name, item.track,
                item.driver_name, item.notes))).casefold()
            return all(term in blob for term in terms)
        status = self._status_filter.currentData()
        if status is not None and (item.status or "") != status:
            return False
        terms = self._search.text().strip().casefold().split()
        if not terms:
            return True
        common = [
            item.name, item.manufacturer, item.model,
            item.part_number, item.tracking_mode,
            DISPLAY_VALUES.get(item.tracking_mode), item.quantity, item.unit,
            item.status, DISPLAY_VALUES.get(item.status), item.condition,
            DISPLAY_VALUES.get(item.condition), item.supplier, item.cost,
            item.notes,
        ]
        blob = " ".join(str(value) for value in common if value)
        blob += " " + " ".join(
            f"{key} {value}" for key, value in (item.specs or {}).items())
        blob = blob.casefold()
        return all(term in blob for term in terms)

    def _render(self):
        rows = [item for item in self._items if self._matches(item)]
        sort_mode = self._sort.currentData()
        if sort_mode == "category_name":
            rows.sort(key=lambda item: (
                _part_type(item).casefold(),
                (item.name or "").casefold()))
        elif sort_mode == "name":
            rows.sort(key=lambda item: (item.name or "").casefold())
        elif sort_mode == "name_desc":
            rows.sort(
                key=lambda item: (item.name or "").casefold(), reverse=True)
        elif sort_mode == "revision":
            rows.sort(key=lambda item: (
                -int(getattr(item, "revision", 1) or 1),
                (item.name or "").casefold()))
        elif sort_mode == "quantity":
            rows.sort(key=lambda item: (
                -float(getattr(item, "quantity", 1) or 0),
                (item.name or "").casefold()))
        else:
            rows.sort(key=lambda item: (
                getattr(item, "updated_at", None)
                or item.created_at or "", item.name or ""), reverse=True)

        current = self._parts_list.currentItem()
        selected_id = current.data(ITEM_ID_ROLE) if current else None
        self._parts_list.clear()
        selected_row = -1
        for row, item in enumerate(rows):
            entry = QListWidgetItem()
            entry.setData(ITEM_ID_ROLE, item.id)
            entry.setSizeHint(QSize(620, 62))
            self._parts_list.addItem(entry)
            show_category = self.current_category() == ALL_PARTS_CATEGORY
            card = (_BaselineCard(
                        item, show_category=show_category)
                    if isinstance(item, Setup)
                    else _PartCard(
                        item, show_category=show_category))
            self._parts_list.setItemWidget(entry, card)
            if item.id == selected_id:
                selected_row = row
        if selected_row >= 0:
            self._parts_list.setCurrentRow(selected_row)
        elif rows:
            self._parts_list.setCurrentRow(0)
        self._count.setText(
            f"{len(rows)} shown \u00b7 {len(self._items)} in {self.current_category()}")
        self._selection_changed()

    def _selected_item(self):
        entry = self._parts_list.currentItem()
        item_id = entry.data(ITEM_ID_ROLE) if entry else None
        return next((item for item in self._items if item.id == item_id), None)

    def _selection_changed(self):
        item = self._selected_item()
        busy = self._loading or self._writing
        current = self._parts_list.currentItem()
        for row in range(self._parts_list.count()):
            entry = self._parts_list.item(row)
            card = self._parts_list.itemWidget(entry)
            if isinstance(card, (_PartCard, _BaselineCard)):
                card.set_selected(entry is current)
        baseline_mode = isinstance(item, Setup)
        self._history_btn.setEnabled(
            item is not None and not baseline_mode and not busy
            and getattr(self, "_history_dialog", None) is None)
        self._metadata_btn.setEnabled(item is not None and not busy)
        self._copy_btn.setEnabled(item is not None and not busy)
        self._stock_btn.setEnabled(
            item is not None and not baseline_mode and not busy
            and not item.archived_at
            and item.category not in ENTITY_CATEGORIES)
        self._archive_btn.setEnabled(
            item is not None and not baseline_mode and not busy)
        self._history_btn.setVisible(not baseline_mode)
        self._stock_btn.setVisible(not baseline_mode)
        self._archive_btn.setVisible(not baseline_mode)
        self._metadata_btn.setText(
            "Open Setup" if baseline_mode else "Open Metadata")
        self._archive_btn.setText(
            "Restore Selected" if item and not baseline_mode and item.archived_at
            else "Archive Selected")

    def _add_part(self):
        category = self.current_category()
        if category == BASELINE_SETUP_CATEGORY:
            self.new_baseline.emit()
            return
        dialog = PartDialog(
            None if category == ALL_PARTS_CATEGORY else category,
            parent=self)
        if dialog.exec() == QDialog.Accepted and dialog.result_item:
            self._write(
                lambda: _create_inventory_item(dialog.result_item),
                "Part added.")

    def _show_history(self):
        existing = getattr(self, "_history_dialog", None)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        item = self._selected_item()
        if not item:
            return
        dialog = MovementHistoryDialog(item, self)
        self._history_dialog = dialog
        self._history_btn.setEnabled(False)
        dialog.finished.connect(self._history_closed)
        # Keep the inventory page usable if the network request is slow. The
        # dialog protects its own lifetime until its worker has finished.
        dialog.show()

    def _history_closed(self, _result):
        dialog = getattr(self, "_history_dialog", None)
        if dialog is not None:
            dialog.deleteLater()
        self._history_dialog = None
        self._selection_changed()

    def _open_metadata(self, *_args):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "Select a part", "Select a part first.")
            return
        if isinstance(item, Setup):
            self.open_baseline.emit(item)
            return
        PartMetadataDialog(item, self).exec()
        self.refresh()

    def _copy_selected(self):
        source = self._selected_item()
        if not source:
            QMessageBox.information(self, "Select a part", "Select a part first.")
            return
        if isinstance(source, Setup):
            copied = deepcopy(source)
            copied.id = None
            copied.name = f"{source.name or 'Baseline'} Copy"
            copied.created_at = None
            copied.updated_at = None
            self.copy_baseline.emit(copied)
            return
        dialog = PartDialog(
            source.category, source=source, parent=self)
        if dialog.exec() == QDialog.Accepted and dialog.result_item:
            self._write(
                lambda: _create_inventory_item(dialog.result_item),
                f"New part created from {source.name}.")

    def _adjust_stock(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "Select a part", "Select a row first.")
            return
        delta, ok = QInputDialog.getDouble(
            self, "Adjust Stock",
            f"Quantity change for {item.name}\n"
            "Use a negative number to remove stock:",
            0, -float(item.quantity),
            max(0.0, MAX_INVENTORY_QUANTITY - float(item.quantity)), 3)
        if not ok or delta == 0:
            return
        reason, ok = QInputDialog.getText(
            self, "Adjustment Reason", "Reason (optional):")
        if not ok:
            return
        target = deepcopy(item)
        target.quantity = round(float(item.quantity) + delta, 3)
        self._write(
            lambda: _save_inventory_changes(item, target),
            "Stock adjusted.")

    def _archive_selected(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "Select a part", "Select a row first.")
            return
        restoring = bool(item.archived_at)
        verb = "Restore" if restoring else "Archive"
        if QMessageBox.question(
                self, verb + " Part",
                f"{verb} '{item.name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        target = deepcopy(item)
        target.archived_at = None if restoring else datetime.now().astimezone().isoformat()
        self._write(
            lambda: _save_inventory_changes(item, target),
            "Part restored." if restoring else "Part archived.")

    def _write(self, operation, success_message):
        if self._loading or self._writing:
            return
        self._writing = True
        self._set_busy(True)

        def _done(result):
            self._writing = False
            self._set_busy(False)
            self._status.setText(
                "Saved offline · syncs when internet returns"
                if isinstance(result, InventoryItem)
                and getattr(result, "_offline_pending", False)
                else success_message)
            if isinstance(result, InventoryItem):
                self._upsert(result)
            else:
                self.refresh()
            self._run_pending_refresh()

        def _error(message):
            self._writing = False
            self._set_busy(False)
            self._status.setText(f"Save failed: {message}")
            self._run_pending_refresh()

        run_async(self, operation, on_done=_done, on_error=_error)

    def _upsert(self, saved):
        self._items = [item for item in self._items if item.id != saved.id]
        if ((self.current_category() == ALL_PARTS_CATEGORY
             or saved.category == self.current_category())
                and (self._show_archived.isChecked() or not saved.archived_at)):
            self._items.append(saved)
        self._render()
