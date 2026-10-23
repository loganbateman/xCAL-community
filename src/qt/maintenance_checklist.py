"""Compact setup-bottom checklist; edits save with the setup, never silently."""
from copy import deepcopy
from datetime import datetime
from textwrap import fill

from PySide6.QtCore import Signal, QSize
from PySide6.QtWidgets import QFrame, QVBoxLayout, QGridLayout, QLabel, QCheckBox, QLayout

import maintenance
import config
import session_types
from utils import google_drive_client


def checkbox_style(due=False):
    check_icon = str(config.ASSETS_DIR / "icon_check.svg").replace("\\", "/")
    color = "color: #e6a020; font-weight:600;" if due else ""
    return f"""
        QCheckBox {{ background: transparent; spacing: 8px; {color} }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #858991;
            border-radius: 3px; background: {config.COLOR_SIDEBAR}; }}
        QCheckBox::indicator:checked {{ background: {config.COLOR_SUCCESS};
            border-color: {config.COLOR_SUCCESS}; image: url("{check_icon}"); }}
        QCheckBox::indicator:disabled {{ border-color: #55585f; }}
    """


class MaintenanceChecklist(QFrame):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._records = {}
        self._current = None
        self._state = None
        self._columns = 2
        layout = QVBoxLayout(self)
        # Let resizeEvent switch columns instead of the old two-column minimum
        # preventing the containing scroll area from ever becoming narrower.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        title = QLabel("Pre-session checks & maintenance")
        title.setObjectName("H2")
        layout.addWidget(title)
        self._summary = QLabel("Choose a kart and session to start.")
        self._summary.setObjectName("Muted")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(8)
        layout.addLayout(self._grid)
        self._next = QLabel()
        self._next.setObjectName("Muted")
        self._next.setWordWrap(True)
        layout.addWidget(self._next)
        hint = QLabel("One checklist for this setup sheet, shared across all runs. Checks save with Save Setup, including the time and editor. Intervals are team-configured examples, not manufacturer service limits.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def records(self):
        return deepcopy(self._records)

    def set_records(self, records):
        self._records = deepcopy(records if isinstance(records, dict) else {})

    def refresh(self, policy, current, sessions, setups):
        self._current = current
        self._context = policy, current, sessions, setups
        self._state = maintenance.checklist(policy, current, sessions, setups, self._records)
        self._render(policy)

    def _render(self, policy):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    widget = child.widget()
                    if widget:
                        widget.hide()
                        widget.setParent(None)
                        widget.deleteLater()
                item.layout().deleteLater()
        kind = session_types.classify(self._current.drive_folder_id, path=self._current.drive_folder_path)
        self._summary.setText(session_types.TYPES[kind] + " · " + self._state.message + " Based on cached setup history.")
        rows = self._state.rows if self._state.active else tuple(
            maintenance.ChecklistRow(item) for item in policy["items"] if item["enabled"] and item["kind"] == "check")
        for index, row in enumerate(rows):
            cell = QVBoxLayout()
            checkbox = QCheckBox(fill(row.item["label"] + (" · Due now" if row.due else ""), width=46))
            checkbox.setChecked(row.checked)
            checkbox.setEnabled(self._state.active)
            checkbox.setToolTip(row.item["label"] if self._state.active else self._state.message)
            checkbox.setStyleSheet(checkbox_style(row.due))
            cell.addWidget(checkbox)
            if row.completed_at:
                try:
                    timestamp = datetime.fromisoformat(row.completed_at).astimezone().strftime("%b %d, %Y · %I:%M %p")
                except ValueError:
                    timestamp = row.completed_at
                completed = QLabel(f"✓ {timestamp} · {row.actor}")
                completed.setObjectName("Muted")
                completed.setWordWrap(True)
                cell.addWidget(completed)
            elif row.overdue:
                overdue = QLabel(f"Overdue by {row.overdue} {row.item['unit']}")
                overdue.setObjectName("Muted")
                cell.addWidget(overdue)
            checkbox.toggled.connect(lambda checked, item=row.item: self._toggle(item, checked))
            self._grid.addLayout(cell, index // self._columns, index % self._columns)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._next.setText("Next: " + "; ".join(f"{row.item['label']} in {row.remaining} " +
                                                 ((session_types.TYPES[row.item['session_type']].lower() + " ") if row.item.get("session_type") and row.item['unit'] == 'sessions' else "") + row.item['unit']
                                                 for row in self._state.future[:3]) if self._state.future else "")

    def resizeEvent(self, event):
        columns = 2 if event.size().width() >= 760 else 1
        if columns != self._columns:
            self._columns = columns
            if self._state:
                self._render(self._context[0])
        super().resizeEvent(event)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(360, hint.height())

    def _toggle(self, item, checked):
        if not self._state.active:
            return
        self._records = maintenance.set_checked(self._records, self._state.kart, self._current.drive_folder_id,
                                                item, checked, google_drive_client.cached_user())
        self.refresh(*self._context)
        self.changed.emit()
