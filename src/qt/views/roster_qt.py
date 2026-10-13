"""Roster editor (Qt) — manage the team's drivers and chassis.

Drivers and chassis are the shared lookup tables that setups link to: two
columns with add / edit / delete, backed by the database.client CRUD calls.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QMessageBox,
)

import database.client as db
from database.models import Driver, Chassis
from qt.workers import run_async


class _EntryDialog(QDialog):
    """Modal add/edit form. `fields` is a list of (label, key); first is required."""

    def __init__(self, title, fields, values=None, submit_text="Add", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        self.values: dict | None = None
        self._fields = fields
        self._edits: dict[str, QLineEdit] = {}
        values = values or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("H2")
        lay.addWidget(head)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        for i, (label, key) in enumerate(fields):
            lab = QLabel(label)
            lab.setObjectName("Muted")
            form.addWidget(lab, i, 0)
            edit = QLineEdit(str(values.get(key, "") or ""))
            self._edits[key] = edit
            form.addWidget(edit, i, 1)
        lay.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(submit_text)
        ok.setObjectName("Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self._submit)
        ok.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self._edits[fields[0][1]].setFocus()

    def _submit(self):
        vals = {k: e.text().strip() for k, e in self._edits.items()}
        if not vals[self._fields[0][1]]:
            return  # first field (the name) is required
        self.values = vals
        self.accept()


class RosterView(QWidget):
    """Two-column drivers / chassis manager, lazily loaded on first show."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(10)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        outer.addLayout(cols, 1)

        self._driver_col, self._driver_host = self._make_column(
            "Drivers", self._add_driver)
        self._chassis_col, self._chassis_host = self._make_column(
            "Chassis", self._add_chassis)
        cols.addWidget(self._driver_col)
        cols.addWidget(self._chassis_col)

        self._status = QLabel("")
        self._status.setObjectName("Muted")
        outer.addWidget(self._status)

    def _make_column(self, title, on_add):
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        header = QHBoxLayout()
        lab = QLabel(title)
        lab.setObjectName("H2")
        header.addWidget(lab)
        header.addStretch(1)
        add = QPushButton("Add")
        add.setObjectName("Primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(on_add)
        header.addWidget(add)
        lay.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 6, 0)
        host_lay.setSpacing(6)
        host_lay.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        return col, host_lay

    # ── Loading ──────────────────────────────────────────────────────────────

    def showEvent(self, event):  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._load()

    def _load(self):
        self._status.setText("Loading roster\u2026")

        def _fetch():
            return db.fetch_drivers(), db.fetch_chassis()

        def _done(result):
            drivers, chassis = result
            self._fill_drivers(drivers)
            self._fill_chassis(chassis)
            self._status.setText(
                f"{len(drivers)} driver(s) \u00b7 {len(chassis)} chassis")

        run_async(self, _fetch, on_done=_done,
                  on_error=lambda m: self._status.setText(f"\u274C {m}"))

    @staticmethod
    def _clear(lay):
        while lay.count() > 1:  # keep the trailing stretch
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _fill_drivers(self, drivers):
        self._clear(self._driver_host)
        if not drivers:
            self._driver_host.insertWidget(0, self._empty("No drivers yet."))
            return
        for d in drivers:
            row = self._row(
                d.name, d.number or "",
                lambda _=False, drv=d: self._edit_driver(drv),
                lambda _=False, drv=d: self._delete_driver(drv))
            self._driver_host.insertWidget(self._driver_host.count() - 1, row)

    def _fill_chassis(self, chassis):
        self._clear(self._chassis_host)
        if not chassis:
            self._chassis_host.insertWidget(0, self._empty("No chassis yet."))
            return
        for c in chassis:
            sub = " ".join(str(x) for x in (c.manufacturer, c.model_year) if x)
            row = self._row(
                c.name, sub,
                lambda _=False, ch=c: self._edit_chassis(ch),
                lambda _=False, ch=c: self._delete_chassis(ch))
            self._chassis_host.insertWidget(self._chassis_host.count() - 1, row)

    def _empty(self, text):
        lab = QLabel(text)
        lab.setObjectName("Muted")
        lab.setAlignment(Qt.AlignCenter)
        return lab

    def _row(self, name, sub, on_edit, on_delete):
        card = QFrame()
        card.setObjectName("Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)

        text = QVBoxLayout()
        text.setSpacing(1)
        n = QLabel(name)
        n.setObjectName("CardTitle")
        text.addWidget(n)
        if sub:
            s = QLabel(sub)
            s.setObjectName("CardSub")
            text.addWidget(s)
        lay.addLayout(text, 1)

        edit = QPushButton("Edit")
        edit.setObjectName("Ghost")
        edit.setFixedWidth(38)
        edit.setToolTip("Edit")
        edit.setCursor(Qt.PointingHandCursor)
        edit.clicked.connect(on_edit)
        delete = QPushButton("\u2715")
        delete.setObjectName("Danger")
        delete.setFixedWidth(38)
        delete.setToolTip("Delete")
        delete.setCursor(Qt.PointingHandCursor)
        delete.clicked.connect(on_delete)
        lay.addWidget(edit)
        lay.addWidget(delete)
        return card

    # ── Driver CRUD ──────────────────────────────────────────────────────────

    def _add_driver(self):
        dlg = _EntryDialog(
            "Add Driver", [("Name", "name"), ("Number (optional)", "number")],
            parent=self)
        if dlg.exec() and dlg.values:
            v = dlg.values
            self._write(lambda: db.create_driver(
                Driver(name=v["name"], number=v["number"] or None)))

    def _edit_driver(self, d: Driver):
        dlg = _EntryDialog(
            "Edit Driver", [("Name", "name"), ("Number (optional)", "number")],
            values={"name": d.name, "number": d.number or ""},
            submit_text="Save", parent=self)
        if dlg.exec() and dlg.values:
            v = dlg.values

            def _apply():
                d.name = v["name"]
                d.number = v["number"] or None
                return db.update_driver(d)
            self._write(_apply)

    def _delete_driver(self, d: Driver):
        if self._confirm(f"Remove driver '{d.name}'?"):
            self._write(lambda: db.delete_driver(d.id))

    # ── Chassis CRUD ─────────────────────────────────────────────────────────

    def _add_chassis(self):
        dlg = _EntryDialog(
            "Add Chassis",
            [("Name", "name"), ("Manufacturer (optional)", "manufacturer"),
             ("Year (optional)", "model_year")], parent=self)
        if dlg.exec() and dlg.values:
            v = dlg.values
            self._write(lambda: db.create_chassis(Chassis(
                name=v["name"], manufacturer=v["manufacturer"] or None,
                model_year=_as_year(v["model_year"]))))

    def _edit_chassis(self, c: Chassis):
        dlg = _EntryDialog(
            "Edit Chassis",
            [("Name", "name"), ("Manufacturer (optional)", "manufacturer"),
             ("Year (optional)", "model_year")],
            values={"name": c.name, "manufacturer": c.manufacturer or "",
                    "model_year": c.model_year or ""},
            submit_text="Save", parent=self)
        if dlg.exec() and dlg.values:
            v = dlg.values

            def _apply():
                c.name = v["name"]
                c.manufacturer = v["manufacturer"] or None
                c.model_year = _as_year(v["model_year"])
                return db.update_chassis(c)
            self._write(_apply)

    def _delete_chassis(self, c: Chassis):
        if self._confirm(f"Remove chassis '{c.name}'?"):
            self._write(lambda: db.delete_chassis(c.id))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _confirm(self, text) -> bool:
        return QMessageBox.question(
            self, "Confirm", text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _write(self, fn):
        self._status.setText("Saving\u2026")
        run_async(self, fn, on_done=lambda _r: self._load(),
                  on_error=lambda m: self._status.setText(f"\u274C {m}"))


def _as_year(text):
    text = (text or "").strip()
    return int(text) if text.isdigit() else None
