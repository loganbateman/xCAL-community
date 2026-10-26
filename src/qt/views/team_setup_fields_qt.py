"""Edit team-specific setup parameters without changing original field keys."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QComboBox, QLineEdit, QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
import config

from qt.workers import run_async
from qt.responsive import AdaptiveGrid
from qt.parameter_drag import ParameterDragButton
from utils import team_setup_fields as fields
from inventory_fields import BUILTIN_CATEGORIES


class TeamSetupFieldsDialog(QDialog):
    def __init__(self, document, parent=None, on_saved=None):
        super().__init__(parent)
        self.document = fields.normalize(document)
        self._on_saved = on_saved
        self.setWindowTitle("Team setup parameters")
        self.resize(960, 780)
        self._selected_key = None
        self._preview_cells = {}
        self._preview_labels = {}
        self._editor_section = None
        self._loading_editor = False
        root = QVBoxLayout(self)
        hint = QLabel("Click a box to edit it, or drag it to rearrange this section. Use New field to add a box. Save changes to keep your edits and layout.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self._preview = QScrollArea()
        self._preview.setWidgetResizable(True)
        self._preview.setMinimumHeight(220)
        self._preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._preview.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(self._preview, 1)
        editor_bar = QHBoxLayout()
        self._editor_title = QLabel("Select a field in the preview")
        self._editor_title.setObjectName("H2")
        editor_bar.addWidget(self._editor_title, 1)
        self._new_field = QPushButton("+ New field")
        self._new_field.clicked.connect(self._new_parameter)
        editor_bar.addWidget(self._new_field)
        self._remove = QPushButton()
        self._remove.setIcon(QIcon(str(config.ASSETS_DIR / "icon_trash.svg")))
        self._remove.setIconSize(QSize(20, 20))
        self._remove.setAccessibleName("Remove selected field")
        self._remove.setToolTip("Select a custom field to remove it")
        self._remove.setEnabled(False)
        self._remove.clicked.connect(self._delete_field)
        editor_bar.addWidget(self._remove)
        root.addLayout(editor_bar)
        form = QFormLayout()
        self._section = QComboBox()
        for key, label in fields.SECTIONS.items():
            self._section.addItem(label, key)
        for key, label in self.document.get("sections", {}).items():
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
        self._category.addItems(list(BUILTIN_CATEGORIES) + [row["name"] for row in self.document.get("part_types", [])])
        category_row = QHBoxLayout()
        category_row.addWidget(self._category, 1)
        self._new_type = QPushButton("+ New part type")
        self._new_type.clicked.connect(self._create_part_type)
        category_row.addWidget(self._new_type)
        form.addRow("Section", self._section)
        form.addRow("Parameter name", self._label)
        form.addRow("Units (optional)", self._unit)
        form.addRow("Value type", self._kind)
        form.addRow("Part type", category_row)
        form.setRowVisible(category_row, self._kind.currentData() == "part")
        self._kind.currentIndexChanged.connect(
            lambda _index: form.setRowVisible(category_row, self._kind.currentData() == "part"))
        root.addLayout(form)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self._save = QPushButton("Save changes")
        self._save.setObjectName("Primary")
        self._save.clicked.connect(self._save_parameters)
        buttons.addWidget(self._save)
        root.addLayout(buttons)
        self._section.currentIndexChanged.connect(self._change_section)
        self._label.textChanged.connect(self._preview_caption)
        self._unit.textChanged.connect(self._preview_caption)
        self._render()

    def _builtin(self, key):
        import setup_fields as sf
        return next((field for field in sf.all_value_fields() if field.key == key
                     and not key.startswith("custom_")), None)

    def _preview_caption(self):
        if not self._loading_editor and self._selected_key in self._preview_labels:
            unit = self._unit.text().strip()
            self._preview_labels[self._selected_key].setText(
                self._label.text() + (f" ({unit})" if unit else ""))

    def _commit_editor(self):
        key = self._selected_key
        if not key:
            return True
        try:
            if self._builtin(key):
                candidate = fields.normalize(self.document)
                candidate.setdefault("builtin_overrides", {})[key] = {
                    "label": self._label.text(), "unit": self._unit.text()}
                self.document = fields.normalize(candidate)
            else:
                self.document = fields.edit_parameter(
                    self.document, key, self._editor_section, self._label.text(),
                    self._unit.text(), self._kind.currentData(), self._category.currentText())
        except ValueError as exc:
            QMessageBox.warning(self, "Field", str(exc))
            return False
        return True

    def _change_section(self):
        if not self._commit_editor():
            self._section.blockSignals(True)
            self._section.setCurrentIndex(self._section.findData(self._editor_section))
            self._section.blockSignals(False)
            return
        self._render()

    def _new_parameter(self):
        if not self._commit_editor():
            return
        section = self._section.currentData()
        names = {field["label"].casefold() for field in self.document["fields"]
                 if field["section"] == section and field["active"]}
        label, suffix = "New field", 2
        while label.casefold() in names:
            label = f"New field {suffix}"
            suffix += 1
        try:
            self.document = fields.add_parameter(self.document, section, label)
        except ValueError as exc:
            QMessageBox.warning(self, "Field", str(exc))
            return
        self._render()
        self._select_key(self.document["fields"][-1]["key"])
        self._label.setFocus()
        self._label.selectAll()

    def _create_part_type(self):
        from qt.views.part_type_qt import PartTypeDialog
        dialog = PartTypeDialog(self.document, self)
        if dialog.exec() == QDialog.Accepted:
            self.document = dialog.document
            self._category.addItem(dialog.type_name)
            self._category.setCurrentText(dialog.type_name)
            self._kind.setCurrentIndex(self._kind.findData("part"))

    def _render(self):
        import setup_fields as sf
        self._selected_key = None
        self._preview_cells = {}
        self._preview_labels = {}
        self._visible_order = []
        section = self._section.currentData()
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        heading = QLabel(f"▲ {self._section.currentText()}")
        heading.setObjectName("H2")
        layout.addWidget(heading)
        grid = AdaptiveGrid(4, 190)
        layout.addWidget(grid)
        builtins = {"front": sf.FRONT_FIELDS, "rear": sf.REAR_FIELDS,
                    "engine": sf.ENGINE_FIELDS, "tires": sf.TIRE_FIELDS,
                    "session": sf.RUN_FIELDS}.get(section, [])

        def add_cell(label, category=None, key=None, kind="text"):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            title = QLabel(label)
            title.setObjectName("Muted")
            title.setWordWrap(True)
            if key:
                self._preview_labels[key] = title
            cell.addWidget(title)
            placeholder = "Choose an inventory part…" if category else ("0.00" if kind == "num" else "—")
            if key:
                control = ParameterDragButton(placeholder, key, (id(self), section), self._move_field)
                control.setObjectName("ParameterPreviewInput")
                control.setAccessibleName(f"Edit {label}")
                control.setToolTip("Click to edit · drag to move before or after another box")
                control.setCursor(Qt.OpenHandCursor)
                control.clicked.connect(lambda _checked=False, field_key=key: self._select_key(field_key))
                self._preview_cells[key] = control
            else:
                control = QLineEdit()
                control.setPlaceholderText(placeholder)
                control.setReadOnly(True)
                control.setFocusPolicy(Qt.NoFocus)
                control.setToolTip("Original setup field")
            control.setMinimumHeight(32)
            control.setStyleSheet(
                "background: #202124; color: #b0b6bf; border: 1px solid #45484f; "
                "border-radius: 6px; padding: 5px 9px; text-align: left;")
            cell.addWidget(control)
            grid.add_cell(cell)

        visible = [(sf.display_field(field, self.document), sf.BUILTIN_INVENTORY_FIELD_CATEGORIES.get(field.key))
                   for field in builtins]
        for item in self.document["fields"]:
            if item["section"] != section:
                continue
            if not item["active"]:
                continue
            visible.append((sf.Field(item["key"], item["label"], item["unit"], item["kind"]), item.get("category")))
        categories = {field.key: category for field, category in visible}
        for field in sf.ordered_fields(section, [field for field, _category in visible], self.document):
            self._visible_order.append(field.key)
            add_cell(field.display_label, categories[field.key], field.key, field.kind)
        if not builtins and not self._preview_cells:
            empty = QLabel("Add a parameter below to build this section.")
            empty.setObjectName("Muted")
            layout.addWidget(empty)
        layout.addStretch(1)
        previous = self._preview.takeWidget()
        self._preview.setWidget(card)
        if previous is not None:
            previous.deleteLater()
        self._remove.setEnabled(False)
        self._editor_title.setText("Select a field in the preview")
        self._loading_editor = True
        self._label.clear()
        self._unit.clear()
        for control in (self._label, self._unit, self._kind, self._category, self._new_type):
            control.setEnabled(False)
        self._loading_editor = False

    def _move_field(self, source_key, target_key, after=False):
        if source_key == target_key or source_key not in self._visible_order or target_key not in self._visible_order:
            return
        if not self._commit_editor():
            return
        order = list(self._visible_order)
        order.remove(source_key)
        order.insert(order.index(target_key) + int(after), source_key)
        candidate = fields.normalize(self.document)
        candidate.setdefault("field_order", {})[self._section.currentData()] = order
        self.document = fields.normalize(candidate)
        scroll = self._preview.verticalScrollBar().value()
        self._render()
        self._select_key(source_key)
        self._preview.verticalScrollBar().setValue(scroll)

    def _select_key(self, key):
        if key not in self._preview_cells:
            return
        if key != self._selected_key and not self._commit_editor():
            return
        self._selected_key = key
        self._editor_section = self._section.currentData()
        for field_key, control in self._preview_cells.items():
            selected = field_key == key
            control.setStyleSheet(
                "background: #202124; color: #e7e9ed; border-radius: 6px; padding: 5px 9px; text-align: left; "
                + ("border: 2px solid #a31632;" if selected else "border: 1px solid #45484f;"))
        self._loading_editor = True
        builtin = self._builtin(key)
        if builtin:
            import setup_fields as sf
            display = sf.display_field(builtin, self.document)
            field = {"label": display.label, "unit": display.unit, "kind": display.kind,
                     "category": sf.BUILTIN_INVENTORY_FIELD_CATEGORIES.get(key)}
            self._remove.setEnabled(False)
            self._remove.setToolTip("Original fields are kept for compatibility")
        else:
            field = next(item for item in self.document["fields"] if item["key"] == key)
            self._remove.setEnabled(True)
            self._remove.setToolTip("Delete selected field")
        self._editor_title.setText("Edit field")
        self._label.setEnabled(True)
        self._unit.setEnabled(True)
        for control in (self._kind, self._category, self._new_type):
            control.setEnabled(not bool(builtin))
        self._label.setText(field["label"])
        self._unit.setText(field["unit"])
        while self._kind.count() > 3:
            self._kind.removeItem(self._kind.count() - 1)
        kind = "part" if field.get("category") else field["kind"]
        if self._kind.findData(kind) < 0 and builtin:
            self._kind.addItem(kind.title(), kind)
        self._kind.setCurrentIndex(self._kind.findData(kind))
        if field.get("category"):
            self._category.setCurrentText(field["category"])
        self._loading_editor = False

    def _delete_field(self):
        key = self._selected_key
        if not key:
            return
        if self._builtin(key):
            return
        candidate = fields.normalize(self.document)
        field = next(item for item in candidate["fields"] if item["key"] == key)
        if QMessageBox.question(
                self, "Delete field", f"Delete {field['label']} from the team's setup fields?\n\n"
                "After saving changes, this field cannot be restored here.") != QMessageBox.Yes:
            return
        candidate["fields"] = [item for item in candidate["fields"] if item["key"] != key]
        for order in candidate.get("field_order", {}).values():
            if key in order:
                order.remove(key)
        try:
            self.document = fields.normalize(candidate)
        except ValueError as exc:
            QMessageBox.warning(self, "Parameter", str(exc))
            return
        self._render()

    def _save_parameters(self):
        if not self._commit_editor():
            return
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
