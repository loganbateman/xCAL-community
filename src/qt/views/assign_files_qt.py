"""Assign a driver / kart to each .xrk in a session.

Telemetry files are not always named correctly. This preview-first tool lets the
user link each file to a roster driver; on Apply it renames the file to the
standard ``Driver_Number_Date.xrk`` form and moves it into that driver's folder
(``Driver Number``) inside the session, creating the folder when needed.
"""
from __future__ import annotations
import re
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QMessageBox,
)

from utils import google_drive_client
from utils.xrk_meta import parse_xrk_filename
from qt.workers import run_async

_DATE_RE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})")
_LEAVE = "\u2014 leave as is \u2014"


def _session_date(session_name: str) -> str:
    m = _DATE_RE.match(session_name or "")
    return m.group(1) if m else date.today().isoformat()


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[\s_]+", (name or "").lower()) if t}


def _best_match(file_name: str, roster: list[dict]) -> int:
    """Pre-select the roster row that best matches a file's parsed driver."""
    meta = parse_xrk_filename(file_name)
    file_driver = (meta.driver if meta else "") or file_name.split("_")[0]
    file_number = meta.number if meta else None
    ftoks = _name_tokens(file_driver)
    best_idx, best_score = -1, 0
    for i, row in enumerate(roster):
        score = len(ftoks & _name_tokens(row.get("driver", "")))
        if file_number and str(row.get("number", "")).strip() == file_number:
            score += 2
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def _folder_name(driver: str, number: str) -> str:
    return f"{driver} {number}".strip()


def build_new_name(driver: str, number: str, session_date: str,
                   original: str, used: set[str]) -> str:
    """Standard ``Driver_Number_Date[_runid].xrk`` name, unique within a batch."""
    stem = re.sub(r"\.xrk$", "", original, flags=re.IGNORECASE)
    parts = stem.split("_")
    tail = parts[-1].strip() if len(parts) > 1 else ""
    # Keep the trailing run id (e.g. 0654) so multiple runs don't collide.
    run_id = tail if tail and tail.lower() not in {number.lower(), driver.lower()} else ""
    base = f"{driver}_{number}_{session_date}"
    candidate = f"{base}_{run_id}.xrk" if run_id else f"{base}.xrk"
    index = 2
    name = candidate
    while name.lower() in used:
        suffix = f"_{run_id}" if run_id else ""
        name = f"{base}{suffix}_{index}.xrk"
        index += 1
    used.add(name.lower())
    return name


class AssignFilesDialog(QDialog):
    def __init__(self, session_id, session_name, xrks, roster, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assign drivers & fix file names")
        self.setMinimumSize(880, 460)
        self._session_id = session_id
        self._session_date = _session_date(session_name)
        self._xrks = list(xrks)
        self._roster = list(roster)
        self._combos: list[QComboBox] = []
        self._build()
        self._recompute_previews()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel(f"Session: {self._session_date}")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "Pick the driver for each file. Nothing changes until you click "
            "Apply. Files are renamed to Driver_Number_Date.xrk and moved into "
            "a \u201cDriver Number\u201d folder in this session.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._table = QTableWidget(len(self._xrks), 4)
        self._table.setHorizontalHeaderLabels(
            ["Current file", "Assign to driver", "New name", "Folder"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        root.addWidget(self._table, 1)

        options = [_LEAVE] + [
            f"{r['driver']}  #{r.get('number', '')}"
            + (f" {r['kart']}" if r.get("kart") else "")
            for r in self._roster
        ]
        for row, node in enumerate(self._xrks):
            current = QTableWidgetItem(node.name)
            current.setFlags(Qt.ItemIsEnabled)
            self._table.setItem(row, 0, current)

            combo = QComboBox()
            combo.addItems(options)
            match = _best_match(node.name, self._roster)
            combo.setCurrentIndex(match + 1 if match >= 0 else 0)
            combo.currentIndexChanged.connect(self._recompute_previews)
            self._combos.append(combo)
            self._table.setCellWidget(row, 1, combo)

            for col in (2, 3):
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsEnabled)
                self._table.setItem(row, col, item)

        self._status = QLabel("")
        self._status.setObjectName("Muted")
        root.addWidget(self._status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._apply_btn = buttons.button(QDialogButtonBox.Ok)
        self._apply_btn.setText("Apply changes")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _row_plan(self):
        """Return per-row (node, driver, number, new_name, folder) for chosen rows."""
        used: set[str] = set()
        plan = []
        for row, node in enumerate(self._xrks):
            idx = self._combos[row].currentIndex() - 1
            if idx < 0:
                plan.append((node, None, None, node.name, ""))
                continue
            entry = self._roster[idx]
            driver = entry["driver"]
            number = str(entry.get("number", "")).strip()
            new_name = build_new_name(
                driver, number, self._session_date, node.name, used)
            folder = _folder_name(driver, number)
            plan.append((node, driver, number, new_name, folder))
        return plan

    def _recompute_previews(self):
        plan = self._row_plan()
        changes = 0
        for row, (node, driver, _num, new_name, folder) in enumerate(plan):
            self._table.item(row, 2).setText(new_name if driver else "")
            self._table.item(row, 3).setText(folder)
            if driver and new_name != node.name:
                changes += 1
        self._status.setText(
            f"{changes} file(s) will be renamed / moved on Apply.")

    def _apply(self):
        plan = [p for p in self._row_plan() if p[1]]
        if not plan:
            QMessageBox.information(
                self, "Nothing to do",
                "Assign at least one file to a driver first.")
            return
        self._apply_btn.setEnabled(False)
        self._status.setText("Applying\u2026")
        session_id = self._session_id

        def _work():
            folder_cache: dict[str, str] = {}
            for node, _driver, _num, new_name, folder in plan:
                target = folder_cache.get(folder)
                if target is None:
                    target = google_drive_client.ensure_child_folder(
                        session_id, folder)
                    folder_cache[folder] = target
                if new_name != node.name:
                    google_drive_client.rename_file(node.id, new_name)
                google_drive_client.move_file(node.id, target)
            return len(plan)

        run_async(
            self, _work,
            on_done=lambda count: self._done(count),
            on_error=self._failed)

    def _done(self, count):
        QMessageBox.information(
            self, "Done", f"Updated {count} file(s).")
        self.accept()

    def _failed(self, message):
        self._apply_btn.setEnabled(True)
        self._status.setText("")
        QMessageBox.critical(self, "Assign Failed", message)
