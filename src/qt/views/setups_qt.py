"""Dedicated catalog of saved session setups."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QMessageBox,
)

import database.client as db
import offline_store
import setup_fields as sf
from database.models import Setup
from qt.workers import run_async


class SetupsView(QWidget):
    open_setup = Signal(object)
    setups_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setups = []
        self._setup_cards = {}
        self._loaded = False
        self._refreshing = False
        self._refresh_pending = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Setups")
        title.setObjectName("H1")
        header.addWidget(title)
        self._count = QLabel("")
        self._count.setObjectName("Muted")
        header.addWidget(self._count)
        header.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("Ghost")
        self._refresh_btn.setMinimumWidth(92)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setToolTip("Refresh saved setups")
        self._refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self._refresh_btn)
        root.addLayout(header)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search setup, driver, chassis, track\u2026")
        self._search.textChanged.connect(self._render)
        root.addWidget(self._search)

        self._all_scroll, self._all_lay = self._scroll_area()
        root.addWidget(self._all_scroll, 1)

    @staticmethod
    def _scroll_area():
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 6, 10, 8)
        lay.setSpacing(7)
        lay.addStretch(1)
        scroll.setWidget(host)
        return scroll, lay

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def on_show(self):
        if not self._loaded:
            self.refresh()

    def refresh(self):
        if self._refreshing:
            self._refresh_pending = True
            return
        self._loaded = True
        self._refreshing = True
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Refreshing…")
        self._refresh_btn.setToolTip("Refreshing saved setups")

        # Match Sessions, Parts, and Session Detail: show durable local data
        # immediately, then replace it only if the cloud refresh succeeds.
        cached = self._offline_setups()
        if cached:
            self._setups = cached
            self._render()

        def _fetch():
            return db.fetch_setup_catalog()

        def _done(result):
            self._finish_refresh()
            self._refresh_btn.setToolTip("Refresh saved setups")
            baselines, self._setups = result
            offline_store.cache_setup_catalog(baselines, self._setups)
            self._render()

        def _error(message):
            self._finish_refresh()
            self._refresh_btn.setToolTip(
                "Showing the automatic offline copy; cloud refresh will retry")
            if not self._setups:
                self._setups = self._offline_setups()
            self._render()

        run_async(self, _fetch, on_done=_done,
                  on_error=_error)

    @staticmethod
    def _offline_setups():
        return offline_store.load_setup_catalog()

    def _finish_refresh(self):
        self._refreshing = False
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("Refresh")
        if self._refresh_pending:
            self._refresh_pending = False
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.refresh)

    def _render(self):
        setups = self._matching_setups()
        self._count.setText(f"{len(setups)} saved setups")
        self._setup_cards = {}
        self._render_list(self._all_lay, setups, False)

    def _matching_setups(self):
        query = self._search.text().strip().lower()

        def matches(setup):
            blob = " ".join(filter(None, [
                setup.name, setup.driver_name, setup.chassis_name, setup.track,
                str(setup.get("kart_number") or ""),
            ])).lower()
            return not query or query in blob

        return [s for s in self._setups if matches(s)]

    def _remove_setup_card(self, setup: Setup):
        scrollbar = self._all_scroll.verticalScrollBar()
        position = scrollbar.value()
        self._search.setFocus(Qt.OtherFocusReason)
        card = self._setup_cards.pop(str(setup.id), None)
        matching = self._matching_setups()
        self._count.setText(f"{len(matching)} saved setups")
        if card is None or not matching:
            self._render()
            return
        self._all_lay.removeWidget(card)
        card.hide()
        card.deleteLater()

        def _restore():
            scrollbar.setValue(min(position, scrollbar.maximum()))

        QTimer.singleShot(0, _restore)

    def _render_list(self, layout, setups, baseline):
        self._clear(layout)
        if not setups:
            empty = QLabel(
                "No matching baselines." if baseline
                else "No matching saved setups.")
            empty.setObjectName("Muted")
            layout.addWidget(empty)
        else:
            ordered = sorted(
                setups,
                key=lambda s: (
                    s.track or "", s.chassis_name or "", s.name or ""),
            )
            for setup in ordered:
                layout.addWidget(self._setup_card(setup, baseline))
        layout.addStretch(1)

    def _setup_card(self, setup: Setup, baseline: bool):
        card = QFrame()
        card.setObjectName("Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 11, 12, 11)
        lay.setSpacing(12)

        mid = QVBoxLayout()
        title = QLabel(setup.name or "Setup")
        title.setObjectName("CardTitle")
        mid.addWidget(title)
        bits = []
        if baseline:
            bits.append("BASELINE")
        if setup.track:
            bits.append(setup.track)
        if setup.driver_name:
            bits.append(setup.driver_name)
        num = setup.get("kart_number") or sf.kart_number_for(
            setup.chassis_name)
        if setup.chassis_name:
            bits.append(
                setup.chassis_name + (f" #{num}" if num else ""))
        elif num:
            bits.append(f"Kart #{num}")
        latest_change = db.latest_setup_change(setup)
        if latest_change:
            bits.append(
                f"Last changed by "
                f"{latest_change.get('actor_name') or 'Unknown'}")
        elif setup.created_by:
            bits.append(f"Created by {setup.created_by}")
        bits.append(
            "Waiting to sync" if getattr(setup, "_offline_pending", False)
            else "\u2713 Synced to Supabase")
        sub = QLabel("  \u00b7  ".join(bits) if bits else "No details")
        sub.setObjectName("CardSub")
        mid.addWidget(sub)
        lay.addLayout(mid, 1)

        openb = QPushButton("Open \u2192")
        openb.setObjectName("Primary")
        openb.setCursor(Qt.PointingHandCursor)
        openb.clicked.connect(lambda: self.open_setup.emit(setup))
        lay.addWidget(openb)

        deleteb = QPushButton("Delete")
        deleteb.setObjectName("Danger")
        deleteb.setCursor(Qt.PointingHandCursor)
        deleteb.clicked.connect(lambda: self._delete_setup(setup))
        lay.addWidget(deleteb)
        if setup.id:
            self._setup_cards[str(setup.id)] = card
        return card

    def _delete_setup(self, setup: Setup):
        answer = QMessageBox.warning(
            self,
            "Delete Setup?",
            f"Permanently delete “{setup.name or 'this setup'}”?\n\n"
            "The session and its telemetry files will not be deleted.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _delete():
            remote_id = offline_store.resolve_setup_id(setup.id)
            if not remote_id:
                offline_store.delete_cached_setup(setup.id)
                return False
            offline_store.queue_setup_deletion(
                remote_id, setup.drive_folder_id or setup.session_id)
            try:
                db.delete_setup(remote_id)
            except Exception:
                return True
            offline_store.complete_setup_deletion(remote_id)
            return False

        def _deleted(_result):
            self._setups = [
                item for item in self._setups if item.id != setup.id]
            self._remove_setup_card(setup)
            self.setups_changed.emit()

        run_async(
            self,
            _delete,
            on_done=_deleted,
            on_error=lambda message: QMessageBox.critical(
                self, "Delete Setup Failed", message),
        )
