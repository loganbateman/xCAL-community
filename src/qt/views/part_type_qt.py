"""Create team part types with typed inventory attributes."""
from uuid import uuid4

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QVBoxLayout,
)

from utils import team_setup_fields


class PartTypeDialog(QDialog):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.type_name = None
        self.setWindowTitle("New part type")
        self.resize(620, 440)
        root = QVBoxLayout(self)
        hint = QLabel("Name the type of part, then add the attributes you want to record for each one.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText("Part type name, e.g. Seat brackets")
        root.addWidget(self.name)
        self.attributes = QTableWidget(0, 3)
        self.attributes.setHorizontalHeaderLabels(["Attribute", "Value type", "Units (optional)"])
        self.attributes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.attributes.verticalHeader().setDefaultSectionSize(48)
        root.addWidget(self.attributes, 1)
        actions = QHBoxLayout()
        add = QPushButton("+ Add attribute")
        add.clicked.connect(self._add_attribute)
        actions.addWidget(add)
        remove = QPushButton("Remove selected attribute")
        remove.clicked.connect(lambda: self.attributes.removeRow(self.attributes.currentRow()))
        actions.addWidget(remove)
        root.addLayout(actions)
        note = QLabel("Every part also includes name, manufacturer, model, stock and notes.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_attribute(self):
        row = self.attributes.rowCount()
        self.attributes.insertRow(row)
        label, unit = QLineEdit(), QLineEdit()
        label.setMaxLength(80)
        unit.setMaxLength(24)
        kind = QComboBox()
        for title, value in (("Text", "text"), ("Number", "number"), ("Whole number", "integer"), ("Date", "date")):
            kind.addItem(title, value)
        for column, widget in enumerate((label, kind, unit)):
            self.attributes.setCellWidget(row, column, widget)
        label.setFocus()

    def _submit(self):
        attributes = []
        for row in range(self.attributes.rowCount()):
            attributes.append({"key": "spec_" + uuid4().hex,
                               "label": self.attributes.cellWidget(row, 0).text(),
                               "kind": self.attributes.cellWidget(row, 1).currentData(),
                               "unit": self.attributes.cellWidget(row, 2).text()})
        try:
            self.document = team_setup_fields.add_part_type(self.document, self.name.text(), attributes)
        except ValueError as exc:
            QMessageBox.warning(self, "Part type", str(exc))
            return
        self.type_name = self.name.text().strip()
        self.accept()
