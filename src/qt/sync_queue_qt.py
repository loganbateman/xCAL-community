"""Steam-style view of work waiting to reach xCAL's cloud services."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

import offline_store
from utils.format import format_size


class SyncQueueDialog(QDialog):
    retry_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync Queue")
        self.resize(620, 520)
        self._events = {}
        self._rows = {}
        self._syncing = False
        root = QVBoxLayout(self)
        title = QLabel("Sync Queue")
        title.setObjectName("H1")
        root.addWidget(title)
        hint = QLabel(
            "Setups, parts, notes, data, and videos sync to your team's Google Drive. "
            "Conflicting edits stay preserved until you review them in Settings.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        services = QFrame()
        services.setObjectName("Card")
        services_layout = QVBoxLayout(services)
        services_title = QLabel("Connected services")
        services_title.setObjectName("CardTitle")
        services_layout.addWidget(services_title)
        self._supabase_status = QLabel()
        self._supabase_status.setObjectName("CardSub")
        services_layout.addWidget(self._supabase_status)
        self._drive_status = QLabel()
        self._drive_status.setObjectName("CardSub")
        services_layout.addWidget(self._drive_status)
        root.addWidget(services)

        queue_title = QLabel("Uploads from this device")
        queue_title.setObjectName("H2")
        root.addWidget(queue_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._items = QVBoxLayout(body)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self._summary = QLabel()
        self._summary.setObjectName("Muted")
        footer.addWidget(self._summary, 1)
        self._retry = QPushButton("Retry Now")
        self._retry.setObjectName("Primary")
        self._retry.clicked.connect(self.retry_requested.emit)
        footer.addWidget(self._retry)
        close = QPushButton("Close")
        close.setObjectName("Ghost")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)
        self.set_connections(False, False)
        self.refresh()

    def set_connections(self, supabase_connected, drive_connected):
        self._set_service_status(
            self._supabase_status, "Catalog", supabase_connected,
            "Setups and Parts are available locally")
        self._set_service_status(
            self._drive_status, "Google Drive", drive_connected,
            "Live data and videos are available")

    @staticmethod
    def _set_service_status(label, service, connected, detail):
        if connected:
            label.setText(f"✓ {service}: Connected · {detail}")
            label.setStyleSheet("color: #3fb765;")
        else:
            label.setText(f"● {service}: Not connected")
            label.setStyleSheet("color: #e74c3c;")

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_event(self, event):
        self._events[event["key"]] = event
        row = self._rows.get(event["key"])
        if row is not None and event.get("state") == "syncing":
            self._apply_event(*row, event)
        else:
            self.refresh()

    def set_syncing(self, syncing):
        self._syncing = bool(syncing)
        self._update_retry_button()

    def refresh(self):
        self._clear(self._items)
        self._rows = {}
        queued = offline_store.pending_sync_items()
        if not queued:
            empty = QLabel("✓ Everything from this device is synced")
            empty.setObjectName("Muted")
            self._items.addWidget(empty)
            self._summary.setText("Queue empty")
            self._update_retry_button(False)
            self._items.addStretch(1)
            return

        retryable = any(
            item.error or self._events.get(item.key, {}).get("state") == "error"
            for item in queued)
        self._update_retry_button(retryable)
        self._summary.setText(
            f"{len(queued)} item{'s' if len(queued) != 1 else ''} waiting")
        for item in queued:
            self._items.addWidget(self._card(item))
        self._items.addStretch(1)

    def _card(self, item):
        event = self._events.get(item.key, {})
        state = event.get("state", "error" if item.error else "queued")
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        top = QHBoxLayout()
        name = QLabel(item.name)
        name.setObjectName("CardTitle")
        top.addWidget(name, 1)
        destination = QLabel(item.destination)
        destination.setObjectName("Muted")
        top.addWidget(destination)
        layout.addLayout(top)

        details = item.kind
        if item.size is not None:
            details += f" · {format_size(item.size)}"
        if state == "syncing":
            details += " · Uploading…"
        elif state == "error":
            details += " · Needs retry"
        else:
            details += " · Queued"
        sub = QLabel(details)
        sub.setObjectName("CardSub")
        layout.addWidget(sub)

        bar = QProgressBar()
        percent = event.get("percent")
        if state == "syncing" and percent is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            bar.setValue(int(percent or 0))
        bar.setFormat("%p%" if item.size is not None else "")
        layout.addWidget(bar)

        error = event.get("error") or item.error
        message = QLabel(str(error or ""))
        message.setStyleSheet("color: #e74c3c;")
        message.setWordWrap(True)
        message.setVisible(bool(error) and state != "syncing")
        layout.addWidget(message)
        self._rows[item.key] = (item, sub, bar, message)
        return card

    @staticmethod
    def _apply_event(item, sub, bar, message, event):
        details = item.kind
        if item.size is not None:
            details += f" · {format_size(item.size)}"
        details += " · Uploading…"
        sub.setText(details)
        message.hide()
        percent = event.get("percent")
        if percent is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            bar.setValue(int(percent))

    def _update_retry_button(self, retryable=None):
        if retryable is None:
            retryable = any(
                item.error
                or self._events.get(item.key, {}).get("state") == "error"
                for item in offline_store.pending_sync_items())
        self._retry.setText("Syncing…" if self._syncing else "Retry Now")
        self._retry.setEnabled(bool(retryable) and not self._syncing)
