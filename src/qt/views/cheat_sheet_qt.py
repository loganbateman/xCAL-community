"""Cheat Sheet page (Qt) — colored tuning-effects table + mechanics notes.

Team-shared via Supabase (app_docs key 'cheat_sheet'). View mode is the colored
read-only grid; Edit mode turns it into an editable, spreadsheet-style table
(add/remove rows and effect columns, click a cell to cycle +/-) plus editable
notes, all saved back to the team.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QFrame, QScrollArea, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QLineEdit, QPlainTextEdit,
                               QInputDialog, QMessageBox)

import config
import cheat_sheet as cs
import database.client as db
from qt.workers import run_async

GREEN = "#1f6b3a"
RED = "#8b2f2f"
DOC_KEY = "cheat_sheet"
_CYCLE = {"": "+", "+": "-", "-": ""}


class CheatSheetView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = cs.default_document()
        self._loaded = False
        self._editing = False
        self._table = None
        self._note_editors = []
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(10)

        header = QHBoxLayout()
        h1 = QLabel("Cheat Sheet")
        h1.setObjectName("H1")
        header.addWidget(h1)
        sub = QLabel("  tuning effects & mechanics")
        sub.setObjectName("Muted")
        header.addWidget(sub)
        header.addStretch(1)
        self._status = QLabel("")
        self._status.setObjectName("Muted")
        header.addWidget(self._status)

        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setObjectName("Ghost")
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.clicked.connect(self._start_edit)
        header.addWidget(self._edit_btn)
        self._save_btn = QPushButton("Save to Team")
        self._save_btn.setObjectName("Primary")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._save)
        self._save_btn.hide()
        header.addWidget(self._save_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("Ghost")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._cancel_edit)
        self._cancel_btn.hide()
        header.addWidget(self._cancel_btn)

        outer.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 4, 8, 8)
        self._body_lay.setSpacing(16)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

        self._render()

    # ── Data ────────────────────────────────────────────────────────────────

    def on_show(self):
        if self._loaded:
            return
        self._loaded = True
        self._status.setText("Syncing\u2026")
        run_async(self, lambda: db.fetch_app_doc(DOC_KEY),
                  on_done=self._on_doc, on_error=lambda _m: self._on_doc(None))

    def _on_doc(self, doc):
        self._doc = cs.normalize_document(doc) if doc else cs.default_document()
        self._status.setText("" if doc else "team copy not created yet")
        if not self._editing:
            self._render()

    def _start_edit(self):
        self._editing = True
        self._edit_btn.hide()
        self._save_btn.show()
        self._cancel_btn.show()
        self._status.setText("editing\u2026")
        self._render()

    def _cancel_edit(self):
        self._editing = False
        self._save_btn.hide()
        self._cancel_btn.hide()
        self._edit_btn.show()
        self._status.setText("")
        self._render()

    # ── Render ───────────────────────────────────────────────

    def _render(self):
        self.setUpdatesEnabled(False)
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self._editing:
            self._body_lay.addWidget(self._effects_editor())
            self._body_lay.addWidget(self._notes_editor())
        else:
            self._body_lay.addWidget(self._effects_card())
            self._body_lay.addWidget(self._notes_card())
        self._body_lay.addStretch(1)
        self.setUpdatesEnabled(True)

    def _effects_card(self) -> QFrame:
        cols = self._doc["effect_columns"]
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        title = QLabel("Adjustment Effects")
        title.setObjectName("H2")
        lay.addWidget(title)
        legend = QLabel("Green + increases the effect \u00b7 Red \u2212 decreases it")
        legend.setObjectName("Muted")
        lay.addWidget(legend)

        grid = QGridLayout()
        grid.setSpacing(2)
        headers = ["Component", "Adjustment"] + cols
        for c, text in enumerate(headers):
            lab = QLabel(text)
            lab.setAlignment(Qt.AlignCenter)
            lab.setWordWrap(True)
            lab.setStyleSheet(
                f"background-color: {config.COLOR_ACCENT}; color: white;"
                "font-weight: 700; font-size: 10px; padding: 5px; border-radius: 4px;")
            grid.addWidget(lab, 0, c)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        for i in range(len(cols)):
            grid.setColumnStretch(2 + i, 2)

        prev = None
        band = False
        for r, row in enumerate(self._doc["tuning_table"], start=1):
            component = row.get("component", "")
            adjustment = row.get("adjustment", "")
            effects = row.get("effects", {})
            new_comp = component != prev
            if new_comp:
                band = not band
            prev = component
            row_bg = config.COLOR_CARD if band else config.COLOR_SIDEBAR

            comp = QLabel(component if new_comp else "")
            comp.setStyleSheet(
                f"background-color: {row_bg}; color: {config.COLOR_ACCENT};"
                "font-weight: 700; padding: 5px 8px;")
            grid.addWidget(comp, r, 0)
            adj = QLabel(adjustment)
            adj.setStyleSheet(f"background-color: {row_bg}; padding: 5px 8px;")
            grid.addWidget(adj, r, 1)
            for i, col in enumerate(cols):
                sign = effects.get(col, "")
                if sign == "+":
                    bg, txt = GREEN, "+"
                elif sign == "-":
                    bg, txt = RED, "\u2212"
                else:
                    bg, txt = row_bg, ""
                cell = QLabel(txt)
                cell.setAlignment(Qt.AlignCenter)
                cell.setStyleSheet(
                    f"background-color: {bg}; color: white; font-weight: 700;"
                    "font-size: 13px; padding: 5px; border-radius: 4px;")
                grid.addWidget(cell, r, 2 + i)

        lay.addLayout(grid)
        return card

    def _notes_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        title = QLabel("Karting Mechanics")
        title.setObjectName("H2")
        lay.addWidget(title)

        for note in self._doc["mechanics_notes"]:
            block = QFrame()
            block.setStyleSheet(
                f"background-color: {config.COLOR_SIDEBAR}; border-radius: 8px;")
            bl = QVBoxLayout(block)
            bl.setContentsMargins(12, 8, 12, 10)
            bl.setSpacing(2)
            nt = QLabel(note.get("title", ""))
            nt.setObjectName("Accent")
            nt.setWordWrap(True)
            bl.addWidget(nt)
            nb = QLabel(note.get("body", ""))
            nb.setWordWrap(True)
            bl.addWidget(nb)
            lay.addWidget(block)
        return card

    # ── Edit mode ───────────────────────────────────────────

    def _effects_editor(self) -> QFrame:
        cols = list(self._doc["effect_columns"])
        rows = self._doc["tuning_table"]
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        title = QLabel("Adjustment Effects")
        title.setObjectName("H2")
        lay.addWidget(title)
        hint = QLabel(
            "Type in Component / Adjustment. Click an effect cell to cycle "
            "blank \u2192 + \u2192 \u2212. Add rows or effect columns below.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        table = QTableWidget(len(rows), 2 + len(cols))
        self._table = table
        table.setHorizontalHeaderLabels(["Component", "Adjustment"] + cols)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(len(cols)):
            table.horizontalHeader().setSectionResizeMode(
                2 + i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        table.setMinimumHeight(360)
        table.cellClicked.connect(self._cell_clicked)
        for r, row in enumerate(rows):
            self._set_text_cell(r, 0, row.get("component", ""))
            self._set_text_cell(r, 1, row.get("adjustment", ""))
            effects = row.get("effects", {})
            for i, col in enumerate(cols):
                self._set_effect_cell(r, 2 + i, effects.get(col, ""))
        lay.addWidget(table)

        controls = QHBoxLayout()
        for text, fn in (("Add Row", self._add_row),
                         ("Remove Row", self._remove_row),
                         ("Add Effect Column", self._add_column),
                         ("Remove Column", self._remove_column)):
            btn = QPushButton(text)
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(fn)
            controls.addWidget(btn)
        controls.addStretch(1)
        lay.addLayout(controls)
        return card

    def _set_text_cell(self, r, c, text):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        self._table.setItem(r, c, item)

    def _set_effect_cell(self, r, c, sign):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignCenter)
        self._style_effect_item(item, sign)
        self._table.setItem(r, c, item)

    @staticmethod
    def _style_effect_item(item, sign):
        if sign == "+":
            item.setText("+")
            item.setBackground(QBrush(QColor(GREEN)))
            item.setForeground(QBrush(QColor("white")))
        elif sign == "-":
            item.setText("\u2212")
            item.setBackground(QBrush(QColor(RED)))
            item.setForeground(QBrush(QColor("white")))
        else:
            item.setText("")
            item.setBackground(QBrush(QColor(config.COLOR_SIDEBAR)))
        item.setData(Qt.UserRole, sign)

    def _cell_clicked(self, r, c):
        if c < 2:
            return
        item = self._table.item(r, c)
        if item is not None:
            self._style_effect_item(
                item, _CYCLE.get(item.data(Qt.UserRole) or "", "+"))

    def _add_row(self):
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._set_text_cell(r, 0, "")
        self._set_text_cell(r, 1, "")
        for c in range(2, self._table.columnCount()):
            self._set_effect_cell(r, c, "")

    def _remove_row(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self._table.removeRow(r)

    def _add_column(self):
        name, ok = QInputDialog.getText(self, "Add Effect Column", "Column name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        c = self._table.columnCount()
        self._table.insertColumn(c)
        self._table.setHorizontalHeaderItem(c, QTableWidgetItem(name))
        self._table.horizontalHeader().setSectionResizeMode(
            c, QHeaderView.ResizeToContents)
        for r in range(self._table.rowCount()):
            self._set_effect_cell(r, c, "")

    def _remove_column(self):
        sel = self._table.selectedIndexes()
        c = sel[0].column() if sel else self._table.columnCount() - 1
        if c < 2:
            QMessageBox.information(
                self, "Remove Column",
                "Select a cell in the effect column you want to remove.")
            return
        self._table.removeColumn(c)

    def _notes_editor(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Karting Mechanics")
        title.setObjectName("H2")
        head.addWidget(title)
        head.addStretch(1)
        add = QPushButton("Add Note")
        add.setObjectName("Ghost")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(lambda: self._add_note_editor(lay, "", ""))
        head.addWidget(add)
        lay.addLayout(head)

        self._note_editors = []
        for note in self._doc["mechanics_notes"]:
            self._add_note_editor(lay, note.get("title", ""), note.get("body", ""))
        return card

    def _add_note_editor(self, lay, title, body):
        block = QFrame()
        block.setStyleSheet(
            f"background-color: {config.COLOR_SIDEBAR}; border-radius: 8px;")
        bl = QVBoxLayout(block)
        bl.setContentsMargins(12, 8, 12, 10)
        bl.setSpacing(4)
        top = QHBoxLayout()
        title_edit = QLineEdit(title)
        title_edit.setPlaceholderText("Title (e.g. Understeer / won't turn in)")
        top.addWidget(title_edit, 1)
        remove = QPushButton("\u2715")
        remove.setObjectName("Danger")
        remove.setFixedWidth(36)
        remove.setCursor(Qt.PointingHandCursor)
        top.addWidget(remove)
        bl.addLayout(top)
        body_edit = QPlainTextEdit(body)
        body_edit.setPlaceholderText("What it means and how to fix it\u2026")
        body_edit.setFixedHeight(72)
        bl.addWidget(body_edit)
        lay.addWidget(block)

        entry = (title_edit, body_edit, block)
        self._note_editors.append(entry)

        def _remove():
            if entry in self._note_editors:
                self._note_editors.remove(entry)
            block.deleteLater()
        remove.clicked.connect(_remove)

    # ── Save ──────────────────────────────────────────────

    def _collect_doc(self) -> dict:
        table = self._table
        cols = []
        for c in range(2, table.columnCount()):
            head = table.horizontalHeaderItem(c)
            cols.append(head.text().strip() if head else f"Column {c - 1}")
        tuning = []
        for r in range(table.rowCount()):
            comp = table.item(r, 0).text().strip() if table.item(r, 0) else ""
            adj = table.item(r, 1).text().strip() if table.item(r, 1) else ""
            if not comp and not adj:
                continue
            effects = {}
            for i, col in enumerate(cols):
                item = table.item(r, 2 + i)
                sign = (item.data(Qt.UserRole) or "") if item else ""
                if sign in ("+", "-"):
                    effects[col] = sign
            tuning.append(
                {"component": comp, "adjustment": adj, "effects": effects})
        notes = []
        for title_edit, body_edit, _ in self._note_editors:
            t = title_edit.text().strip()
            b = body_edit.toPlainText().strip()
            if t or b:
                notes.append({"title": t, "body": b})
        return {"effect_columns": cols, "tuning_table": tuning,
                "mechanics_notes": notes}

    def _save(self):
        doc = self._collect_doc()
        self._save_btn.setEnabled(False)
        self._status.setText("saving to team\u2026")

        def _write():
            db.save_app_doc(DOC_KEY, doc)
            return doc

        def _done(saved):
            self._save_btn.setEnabled(True)
            self._doc = cs.normalize_document(saved)
            self._editing = False
            self._save_btn.hide()
            self._cancel_btn.hide()
            self._edit_btn.show()
            self._status.setText("saved to team")
            self._render()

        def _err(msg):
            self._save_btn.setEnabled(True)
            hint = ("  \u2014 run schema/migration_app_docs.sql in Supabase"
                    if "app_docs" in str(msg) else "")
            self._status.setText(f"\u274C save failed: {msg}{hint}")

        run_async(self, _write, on_done=_done, on_error=_err)
