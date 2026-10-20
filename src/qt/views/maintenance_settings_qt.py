"""Edit team pre-session checks and timestamped session/day maintenance rules."""
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QLabel, QTableWidget,
    QTableWidgetItem, QCheckBox, QSpinBox, QPushButton, QHBoxLayout,
    QMessageBox, QHeaderView, QWidget,
)

import maintenance
import maintenance_store
import session_types
from qt.widgets import DatePickerEdit
from qt.workers import run_async
from qt.maintenance_checklist import checkbox_style


class _OverrideSpin(QSpinBox):
    def textFromValue(self, value):
        return "Hidden" if value == -1 else "Default" if value == 0 else str(value)


class _TypeRule(QWidget):
    def __init__(self, rule, default_unit, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        self.every = _OverrideSpin()
        self.every.setRange(-1, 10000)
        self.every.setValue(0 if rule is None else rule["every"] if rule.get("enabled", True) else -1)
        self.every.setToolTip("Default: use main rule. Hidden: skip this type. Number: interval for this type.")
        self.unit = QComboBox()
        self.unit.addItem("Sessions", "sessions")
        self.unit.addItem("Days", "days")
        self.unit.setCurrentIndex(1 if (rule or {}).get("unit", default_unit) == "days" else 0)
        self.every.valueChanged.connect(lambda value: self.unit.setVisible(value > 0))
        layout.addWidget(self.every)
        layout.addWidget(self.unit)
        self.unit.setVisible(self.value() > 0)

    def value(self):
        return self.every.value()

    def setValue(self, value):
        self.every.setValue(value)


class MaintenanceSettingsDialog(QDialog):
    saved = Signal(object)

    def __init__(self, snapshot, sessions, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.setWindowTitle("Checklist & maintenance schedule")
        self.resize(1280, 740)
        root = QVBoxLayout(self)
        hint = QLabel("Basic checks appear on each dated setup. Interval work appears when due for that kart. Counts use distinct sessions with that kart's setups, not runs. Starter periods are editable examples; use your team's hardware-specific requirements.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        self._start_session = QComboBox()
        self._start_session.addItem("Use start date only", None)
        selected = 0
        for session in sorted(sessions, key=lambda node: node.name):
            if maintenance.session_date(session.name):
                self._start_session.addItem(session.name, session)
                if (session.id == snapshot.policy["start_session_id"]
                        or session.name == snapshot.policy["start_session_name"]):
                    selected = self._start_session.count() - 1
        if not selected and (snapshot.policy["start_session_id"] or snapshot.policy["start_session_name"]):
            anchor = SimpleNamespace(id=snapshot.policy["start_session_id"], name=snapshot.policy["start_session_name"])
            self._start_session.addItem((anchor.name or "Configured start session") + " (not in cached list)", anchor)
            selected = self._start_session.count() - 1
        self._start_date = DatePickerEdit()
        self._start_date.setText(snapshot.policy["start_date"])
        self._start_session.currentIndexChanged.connect(self._select_anchor)
        self._start_session.setCurrentIndex(selected)
        self._select_anchor()
        form.addRow("Start at session", self._start_session)
        form.addRow("Start date", self._start_date)
        root.addLayout(form)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["Enabled", "Checklist item", "Default rule", "Every", "Count", "Practice", "Qualifying", "Race"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(70)
        for column, width in ((0, 72), (2, 140), (3, 85), (4, 125), (5, 135), (6, 135), (7, 135)):
            self._table.setColumnWidth(column, width)
        root.addWidget(self._table, 1)
        for item in snapshot.policy["items"]:
            self._add_row(item)
        add = QPushButton("+ Add checklist / interval item")
        add.clicked.connect(lambda: self._add_row({"id": uuid4().hex, "label": "New check", "kind": "check", "enabled": True}))
        root.addWidget(add)
        overrides_hint = QLabel("Practice / Qualifying / Race: Default uses the main rule; Hidden skips that type; a number sets its own interval in sessions or days. Session overrides count only sessions of that type. Completing service resets the task across all types.")
        overrides_hint.setWordWrap(True)
        root.addWidget(overrides_hint)
        note = QLabel("Disable an item to hide it without deleting history. Renaming or changing task type starts a new task; interval changes retain prior service. Schedule changes and checklist ticks save locally and sync through your team Drive journal.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)
        self._status = QLabel("" if snapshot.available else "Offline: cached settings shown. Reconnect to save the shared schedule.")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self._save = QPushButton("Save team schedule")
        self._save.setObjectName("Primary")
        self._save.setEnabled(snapshot.available)
        self._save.clicked.connect(self._save_policy)
        buttons.addWidget(self._save)
        root.addLayout(buttons)

    def _select_anchor(self, *_args):
        session = self._start_session.currentData()
        self._start_date.setEnabled(session is None)
        if session:
            day = maintenance.session_date(session.name)
            if day:
                self._start_date.setText(day.isoformat())

    def _add_row(self, item):
        row = self._table.rowCount()
        self._table.insertRow(row)
        enabled = QCheckBox()
        enabled.setStyleSheet(checkbox_style())
        enabled.setChecked(item["enabled"])
        self._table.setCellWidget(row, 0, enabled)
        label = QTableWidgetItem(item["label"])
        label.setData(Qt.UserRole, deepcopy(item))
        self._table.setItem(row, 1, label)
        kind = QComboBox()
        kind.addItem("Every setup", "check")
        kind.addItem("Interval", "interval")
        kind.setCurrentIndex(1 if item["kind"] == "interval" else 0)
        self._table.setCellWidget(row, 2, kind)
        every = QSpinBox()
        every.setStyleSheet("QSpinBox { padding:4px; }")
        every.setRange(1, 10000)
        every.setValue(item.get("every", 5))
        self._table.setCellWidget(row, 3, every)
        unit = QComboBox()
        unit.addItem("Sessions", "sessions")
        unit.addItem("Days", "days")
        unit.setCurrentIndex(1 if item.get("unit") == "days" else 0)
        self._table.setCellWidget(row, 4, unit)
        for column, session_type in enumerate(session_types.TYPES, 5):
            rule = item.get("by_session_type", {}).get(session_type)
            override = _TypeRule(rule, item.get("unit", "sessions"))
            self._table.setCellWidget(row, column, override)
        def changed(*_args):
            is_interval = kind.currentData() == "interval"
            every.setEnabled(is_interval)
            unit.setEnabled(is_interval)
            every.setVisible(is_interval)
            unit.setVisible(is_interval)
        kind.currentIndexChanged.connect(changed)
        changed()
        # QTableWidget can show newly installed cell widgets during its first
        # layout; restore the rule's visibility once that layout finishes.
        QTimer.singleShot(0, kind, changed)

    def policy(self):
        items = []
        for row in range(self._table.rowCount()):
            label_item = self._table.item(row, 1)
            previous = label_item.data(Qt.UserRole)
            label = label_item.text().strip()
            kind = self._table.cellWidget(row, 2).currentData()
            identity = previous["id"] if label == previous["label"] and kind == previous["kind"] else uuid4().hex
            item = {"id": identity, "label": label, "kind": kind,
                    "enabled": self._table.cellWidget(row, 0).isChecked()}
            if kind == "interval":
                item.update(every=self._table.cellWidget(row, 3).value(), unit=self._table.cellWidget(row, 4).currentData())
            rules = {}
            for column, session_type in enumerate(session_types.TYPES, 5):
                value = self._table.cellWidget(row, column).value()
                if value == -1:
                    rules[session_type] = {"enabled": False}
                elif value > 0:
                    rules[session_type] = {"enabled": True, "every": value,
                                           "unit": self._table.cellWidget(row, column).unit.currentData()}
            if rules:
                item["by_session_type"] = rules
            items.append(item)
        session = self._start_session.currentData()
        return maintenance.normalize_policy({"schema": 1, "start_date": self._start_date.text(),
                                             "start_session_id": session.id if session else "",
                                             "start_session_name": session.name if session else "", "items": items})

    def _save_policy(self):
        try:
            policy = self.policy()
        except ValueError as exc:
            QMessageBox.warning(self, "Schedule", str(exc))
            return
        self._save.setEnabled(False)
        self._table.setEnabled(False)
        self._status.setText("Saving schedule…")
        def done(snapshot):
            self.saved.emit(snapshot.policy)
            self.accept()
        def failed(message):
            self._save.setEnabled(True)
            self._table.setEnabled(True)
            self._status.setText(message)
        run_async(self, lambda: maintenance_store.save_policy(policy, self.snapshot), on_done=done, on_error=failed)
