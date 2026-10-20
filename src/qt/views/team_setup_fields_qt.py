"""Add/hide team-specific setup parameters without changing original fields."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QComboBox, QLineEdit, QPushButton, QMessageBox,
)

import database.client as db
from qt.workers import run_async
from utils import team_setup_fields as fields


class TeamSetupFieldsDialog(QDialog):
    def __init__(self, document, parent=None, on_saved=None):
        super().__init__(parent)
        self.document = fields.normalize(document)
        self._on_saved = on_saved
        self.setWindowTitle("Team setup parameters")
        self.resize(620, 540)
        root = QVBoxLayout(self)
        hint = QLabel("Keep the original kart fields. Add parameters to Front End, Rear End, Engine, Tires, or Session. Added fields also have independent values in each run. Hiding never deletes old values.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self._list = QListWidget()
        root.addWidget(self._list, 1)
        form = QFormLayout()
        self._section = QComboBox()
        for key, label in fields.SECTIONS.items():
            self._section.addItem(label, key)
        self._label = QLineEdit()
        self._label.setMaxLength(80)
        self._unit = QLineEdit()
        self._unit.setMaxLength(24)
        self._kind = QComboBox()
        self._kind.addItem("Text", "text")
        self._kind.addItem("Number", "num")
        form.addRow("Section", self._section)
        form.addRow("Parameter name", self._label)
        form.addRow("Units (optional)", self._unit)
        form.addRow("Value type", self._kind)
        root.addLayout(form)
        add = QPushButton("Add parameter")
        add.clicked.connect(self._add)
        root.addWidget(add)
        self._visibility = QPushButton("Hide / restore selected parameter")
        self._visibility.clicked.connect(self._toggle_visibility)
        root.addWidget(self._visibility)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self._save = QPushButton("Save team parameters")
        self._save.setObjectName("Primary")
        self._save.clicked.connect(self._save_parameters)
        buttons.addWidget(self._save)
        root.addLayout(buttons)
        self._render()

    def _render(self):
        self._list.clear()
        for item in self.document["fields"]:
            unit = f" ({item['unit']})" if item["unit"] else ""
            hidden = " · Hidden" if not item["active"] else ""
            self._list.addItem(f"{fields.SECTIONS[item['section']]} · {item['label']}{unit}{hidden}")
        self._visibility.setEnabled(bool(self.document["fields"]))

    def _add(self):
        try:
            self.document = fields.add_parameter(self.document, self._section.currentData(),
                                                  self._label.text(), self._unit.text(), self._kind.currentData())
        except ValueError as exc:
            QMessageBox.warning(self, "Parameter", str(exc))
            return
        self._label.clear()
        self._unit.clear()
        self._render()
        self._list.setCurrentRow(self._list.count() - 1)

    def _toggle_visibility(self):
        row = self._list.currentRow()
        if row < 0:
            return
        candidate = fields.normalize(self.document)
        candidate["fields"][row]["active"] = not candidate["fields"][row]["active"]
        try:
            self.document = fields.normalize(candidate)
        except ValueError as exc:
            QMessageBox.warning(self, "Parameter", str(exc))
            return
        self._render()
        self._list.setCurrentRow(row)

    def _save_parameters(self):
        document = fields.normalize(self.document)
        self.setEnabled(False)
        def done(_result):
            self.setEnabled(True)
            if self._on_saved:
                self._on_saved(document)
            self.accept()
        def failed(message):
            self.setEnabled(True)
            QMessageBox.warning(self, "Parameters not saved", message)
        run_async(self, lambda: db.save_app_doc(fields.DOC_KEY, document), on_done=done, on_error=failed)
