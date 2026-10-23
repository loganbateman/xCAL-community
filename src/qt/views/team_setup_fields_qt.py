"""Add/hide team-specific setup parameters without changing original fields."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QComboBox, QLineEdit, QPushButton, QMessageBox,
)

from qt.workers import run_async
from utils import team_setup_fields as fields
from inventory_fields import CATEGORIES


class TeamSetupFieldsDialog(QDialog):
    def __init__(self, document, parent=None, on_saved=None):
        super().__init__(parent)
        self.document = fields.normalize(document)
        self._on_saved = on_saved
        self.setWindowTitle("Team setup parameters")
        self.resize(620, 540)
        root = QVBoxLayout(self)
        hint = QLabel("Add tuning inputs or Parts pickers by section. Select an added field to edit it, then Update selected. Original kart fields stay unchanged; hiding a field keeps its saved run values.")
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
        self._kind.addItem("Parts picker", "part")
        self._category = QComboBox()
        self._category.addItems(CATEGORIES)
        self._category.setEnabled(False)
        self._kind.currentIndexChanged.connect(lambda: self._category.setEnabled(self._kind.currentData() == "part"))
        form.addRow("Section", self._section)
        form.addRow("Parameter name", self._label)
        form.addRow("Units (optional)", self._unit)
        form.addRow("Value type", self._kind)
        form.addRow("Parts category", self._category)
        root.addLayout(form)
        add = QPushButton("Add parameter")
        add.clicked.connect(self._add)
        root.addWidget(add)
        self._update = QPushButton("Update selected field")
        self._update.setEnabled(False)
        self._update.clicked.connect(self._edit_selected)
        root.addWidget(self._update)
        self._list.currentRowChanged.connect(self._select_field)
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
        self._list.blockSignals(True)
        self._list.clear()
        for item in self.document["fields"]:
            unit = f" ({item['unit']})" if item["unit"] else ""
            hidden = " · Hidden" if not item["active"] else ""
            category = f" · Parts: {item['category']}" if item.get("category") else ""
            self._list.addItem(f"{fields.SECTIONS[item['section']]} · {item['label']}{unit}{category}{hidden}")
        self._visibility.setEnabled(bool(self.document["fields"]))
        self._update.setEnabled(False)
        self._list.blockSignals(False)

    def _select_field(self, row):
        self._update.setEnabled(row >= 0)
        if row < 0:
            return
        field = self.document["fields"][row]
        self._section.setCurrentIndex(self._section.findData(field["section"]))
        self._label.setText(field["label"])
        self._unit.setText(field["unit"])
        self._kind.setCurrentIndex(self._kind.findData("part" if field.get("category") else field["kind"]))
        if field.get("category"):
            self._category.setCurrentText(field["category"])

    def _edit_selected(self):
        row = self._list.currentRow()
        if row < 0:
            return
        try:
            self.document = fields.edit_parameter(self.document, self.document["fields"][row]["key"],
                self._section.currentData(), self._label.text(), self._unit.text(), self._kind.currentData(), self._category.currentText())
        except ValueError as exc:
            QMessageBox.warning(self, "Parameter", str(exc))
            return
        self._render()
        self._list.setCurrentRow(row)

    def _add(self):
        try:
            self.document = fields.add_parameter(self.document, self._section.currentData(),
                                                  self._label.text(), self._unit.text(), self._kind.currentData(), self._category.currentText())
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
        run_async(self, lambda: fields.save(document), on_done=done, on_error=failed)
