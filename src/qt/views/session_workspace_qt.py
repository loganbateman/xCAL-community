"""Session workspace (Qt) — master-detail layout.

Opening a session keeps a slim, clickable session list on the left (so you can
jump between sessions without going 'back') and shows the session detail on the
right. Concept 1 from the layout mockups.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                               QScrollArea, QFrame, QLineEdit)

import config
from qt.views.session_detail_qt import SessionDetailView
from qt.views.sessions_qt import _display, _session_sort_key


def _workspace_sort_key(session, _active_id=None):
    """Keep the workspace list in the same newest-first order as Sessions."""
    return _session_sort_key(session)


class _MiniRow(QFrame):
    clicked = Signal(object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self._session = session
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(1)
        d, label, _tag = _display(session)
        self._search_text = " ".join(filter(None, (
            d, label, session.name, session.path))).casefold()
        dl = QLabel(label or session.name)
        dl.setObjectName("CardTitle")
        dl.setWordWrap(True)
        lay.addWidget(dl)
        tl = QLabel(d or "\u2014")
        tl.setObjectName("CardSub")
        tl.setWordWrap(True)
        lay.addWidget(tl)

    def mousePressEvent(self, _e):  # noqa: N802
        self.clicked.emit(self._session)

    def matches(self, query: str) -> bool:
        return not query or query in self._search_text

    def set_active(self, active: bool):
        self.setStyleSheet(
            f"#Card {{ background:{config.COLOR_ACCENT}; border-color:{config.COLOR_ACCENT}; }}"
            if active else "")


class _SessionListScroll(QScrollArea):
    """Arrow keys navigate sessions instead of nudging the scrollbar."""

    navigate = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def enterEvent(self, event):  # noqa: N802 (Qt override)
        self.setFocus(Qt.MouseFocusReason)
        super().enterEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt override)
        if event.key() == Qt.Key_Up:
            self.navigate.emit(-1)
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self.navigate.emit(1)
            event.accept()
            return
        super().keyPressEvent(event)


class SessionWorkspace(QWidget):
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[_MiniRow] = []
        self._sessions = []
        self._active_id = None
        self._pending_session = None
        self._navigation_timer = QTimer(self)
        self._navigation_timer.setSingleShot(True)
        self._navigation_timer.setInterval(220)
        self._navigation_timer.timeout.connect(self._load_pending_session)
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Left: slim session list
        left = QWidget()
        left.setObjectName("SessionRail")
        left.setFixedWidth(250)
        # Target only the rail itself. A bare `background` rule cascades to
        # child labels in Qt and creates dark rectangles behind every line.
        left.setStyleSheet(
            "QWidget#SessionRail { background: rgba(0,0,0,0.12); }")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 18, 10, 12)
        lv.setSpacing(6)
        title = QLabel("Sessions")
        title.setObjectName("H2")
        lv.addWidget(title)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sessions…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_rows)
        lv.addWidget(self._search)
        self._list_scroll = _SessionListScroll()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.navigate.connect(self._navigate_rows)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 6, 0)
        self._list_lay.setSpacing(5)
        self._list_lay.addStretch(1)
        self._list_scroll.setWidget(self._list_host)
        lv.addWidget(self._list_scroll, 1)
        lay.addWidget(left)

        # Right: the session detail (its 'back' returns to the home list)
        self.detail = SessionDetailView()
        self.detail.back.connect(self.back.emit)
        lay.addWidget(self.detail, 1)

    def open(self, session, sessions):
        """Show `session` on the right and (re)build the slim list on the left."""
        self._navigation_timer.stop()
        self._pending_session = None
        self._sessions = list(sessions)
        self._build_list(sessions, session)
        self._load_detail(session)

    def _build_list(self, sessions, active):
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows = []
        self._active_id = active.id
        # Keep the rail in normal newest-first order. The accent highlight is
        # enough to identify the active session without moving it in the list.
        ordered = sorted(
            sessions,
            key=lambda item: _workspace_sort_key(item, active.id),
        )
        for s in ordered:
            row = _MiniRow(s)
            row.clicked.connect(self._on_pick)
            row.set_active(s.id == active.id)
            self._list_lay.addWidget(row)
            self._rows.append(row)
        self._list_lay.addStretch(1)
        self._filter_rows(self._search.text())

    def _filter_rows(self, text):
        query = (text or "").strip().casefold()
        for row in self._rows:
            row.setVisible(row.matches(query))

    def _visible_rows(self):
        return [row for row in self._rows if not row.isHidden()]

    def _navigate_rows(self, direction):
        rows = self._visible_rows()
        if not rows:
            return
        current = next(
            (index for index, row in enumerate(rows)
             if row._session.id == self._active_id),
            -1,
        )
        if current < 0:
            target = 0 if direction > 0 else len(rows) - 1
        else:
            target = max(0, min(len(rows) - 1, current + direction))
        row = rows[target]
        if row._session.id == self._active_id:
            return
        self._select(row._session)
        self._pending_session = row._session
        # Keep keyboard navigation fluid without launching a full database and
        # recursive Drive refresh for every auto-repeat event.
        self._navigation_timer.start()
        self._list_scroll.ensureWidgetVisible(row, 8, 12)

    def _on_pick(self, session, focus_list=True):
        self._navigation_timer.stop()
        self._pending_session = None
        if session.id == self._active_id:
            if focus_list:
                self._list_scroll.setFocus(Qt.MouseFocusReason)
            return
        self._select(session)
        self._pending_session = session
        self._navigation_timer.start()
        if focus_list:
            self._list_scroll.setFocus(Qt.MouseFocusReason)

    def _select(self, session):
        self._active_id = session.id
        for row in self._rows:
            row.set_active(row._session.id == session.id)

    def _load_pending_session(self):
        session = self._pending_session
        self._pending_session = None
        if session is not None and session.id == self._active_id:
            self._load_detail(session)

    def _load_detail(self, session):
        seasons = set()
        for item in self._sessions:
            parts = [
                part.strip() for part in item.path.split(" / ")
                if part.strip()]
            if len(parts) > 1:
                seasons.add(parts[0])
        self.detail.load(
            session.id, session.path, session.name,
            getattr(session, "season", ""), sorted(seasons))
