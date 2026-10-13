"""Shared Driver / Kart / Number naming configuration dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
)

import database.client as db
from database.models import Driver, Chassis
from utils import team_naming
from qt.workers import run_async


class TeamNamingEditor(QWidget):
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drivers = []
        self._chassis = []
        self._build()
        self._load()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("Roster \u2014 Drivers, Karts & Numbers")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "One row per driver. The Number ties a driver to a chassis "
            "(e.g. 11 \u2192 Birel, 24/42 \u2192 Redspeed) and controls how XRK "
            "filenames such as Alexander Davis_11_... are matched to a driver "
            "and kart. Click any cell to edit it; changes sync to everyone.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Driver", "Kart / Chassis", "Number"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        # One click edits a cell and rows are taller, so it's easier to click/type.
        self._table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        root.addWidget(self._table, 1)

        actions = QHBoxLayout()
        add = QPushButton("Add Row")
        add.setObjectName("Primary")
        add.clicked.connect(lambda: self._add_row("", "", "", None))
        remove = QPushButton("Remove Selected")
        remove.setObjectName("Danger")
        remove.clicked.connect(self._remove_selected)
        save = QPushButton("Save Roster")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch(1)
        actions.addWidget(save)
        for button in (add, remove, save):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(36)
        root.addLayout(actions)

        self._status = QLabel("Loading\u2026")
        self._status.setObjectName("Muted")
        root.addWidget(self._status)

    def _load(self):
        def _fetch():
            # The shared doc is optional; a failure here (e.g. app_docs not yet
            # created) must not stop the roster loading from the Drivers table.
            try:
                doc = db.fetch_app_doc(team_naming.DOC_KEY)
            except Exception:
                doc = None
            return (doc, db.fetch_drivers(), db.fetch_chassis())

        def _done(result):
            doc, self._drivers, self._chassis = result
            rows = (
                team_naming.normalize(doc)["assignments"]
                if doc else team_naming.from_drivers(self._drivers)["assignments"])
            self._table.setRowCount(0)
            driver_ids = {d.name.lower(): d.id for d in self._drivers}
            for row in rows:
                self._add_row(
                    row["driver"], row["kart"], row["number"],
                    driver_ids.get(row["driver"].lower()))
            self._status.setText(
                f"{len(rows)} shared naming assignment(s)")

        run_async(
            self, _fetch, on_done=_done,
            on_error=lambda message: self._status.setText(
                f"Could not load naming: {message}"))

    def _add_row(self, driver, kart, number, driver_id):
        row = self._table.rowCount()
        self._table.insertRow(row)
        driver_item = QTableWidgetItem(driver)
        driver_item.setData(Qt.UserRole, driver_id)
        self._table.setItem(row, 0, driver_item)
        self._table.setItem(row, 1, QTableWidgetItem(kart))
        self._table.setItem(row, 2, QTableWidgetItem(number))

    def _remove_selected(self):
        rows = sorted(
            {index.row() for index in self._table.selectedIndexes()},
            reverse=True)
        for row in rows:
            self._table.removeRow(row)

    def _table_rows(self):
        result = []
        for row in range(self._table.rowCount()):
            driver_item = self._table.item(row, 0)
            driver = (driver_item.text() if driver_item else "").strip()
            kart = (
                self._table.item(row, 1).text()
                if self._table.item(row, 1) else "").strip()
            number = (
                self._table.item(row, 2).text()
                if self._table.item(row, 2) else "").strip()
            if not (driver or kart or number):
                continue
            result.append({
                "driver": driver, "kart": kart, "number": number,
                "driver_id": (
                    driver_item.data(Qt.UserRole) if driver_item else None),
            })
        return result

    def _save(self):
        rows = self._table_rows()
        if any(not row["driver"] for row in rows):
            QMessageBox.warning(
                self, "Incomplete Naming",
                "Every row needs a Driver name.")
            return
        if any(bool(row["kart"]) != bool(row["number"]) for row in rows):
            QMessageBox.warning(
                self, "Incomplete Kart Assignment",
                "Kart / Chassis and Number must either both be filled or both "
                "left blank.")
            return
        numbers = {}
        for row in rows:
            if not row["number"]:
                continue
            prior = numbers.get(row["number"])
            if prior and prior != row["kart"]:
                QMessageBox.warning(
                    self, "Conflicting Number",
                    f"Number {row['number']} is assigned to both {prior} and "
                    f"{row['kart']}.")
                return
            numbers[row["number"]] = row["kart"]
        self._status.setText("Saving\u2026")

        def _write():
            assignments = [{
                "driver": row["driver"],
                "kart": row["kart"],
                "number": row["number"],
            } for row in rows]
            doc = {"assignments": assignments}
            # New schemas store the document directly. Older deployed schemas
            # may not have app_docs yet; Drivers remains the shared fallback.
            try:
                db.save_app_doc(team_naming.DOC_KEY, doc)
            except Exception:
                pass

            existing_drivers = {d.name.lower(): d for d in db.fetch_drivers()}
            for row in rows:
                driver = next(
                    (d for d in existing_drivers.values()
                     if d.id == row["driver_id"]), None)
                if driver is None:
                    driver = existing_drivers.get(row["driver"].lower())
                if driver:
                    driver.name = row["driver"]
                    # Empty string is intentional: update_driver must clear a
                    # previously assigned number rather than omitting the field.
                    driver.number = row["number"]
                    driver.notes = team_naming.notes_with_kart(
                        driver.notes, row["kart"])
                    db.update_driver(driver)
                else:
                    db.create_driver(Driver(
                        name=row["driver"], number=row["number"],
                        notes=team_naming.notes_with_kart(None, row["kart"])))

            existing_karts = {
                c.name.lower() for c in db.fetch_chassis()}
            for kart in sorted({row["kart"] for row in rows if row["kart"]}):
                if kart.lower() not in existing_karts:
                    db.create_chassis(Chassis(name=kart))
            return doc

        def _done(doc):
            team_naming.apply(doc)
            self._status.setText(
                f"Saved {len(rows)} assignment(s) for the team")
            self.saved.emit()

        run_async(
            self, _write, on_done=_done,
            on_error=lambda message: self._status.setText(
                f"\u274C Save failed: {message}"))


class TeamNamingDialog(QDialog):
    """Optional standalone wrapper; Settings embeds TeamNamingEditor directly."""
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Driver / Kart / Number Naming")
        self.resize(800, 560)
        lay = QVBoxLayout(self)
        self.editor = TeamNamingEditor(self)
        self.editor.saved.connect(self.saved.emit)
        lay.addWidget(self.editor)
        close = QPushButton("Close")
        close.setObjectName("Ghost")
        close.clicked.connect(self.close)
        lay.addWidget(close, 0, Qt.AlignRight)
