"""Sidebar navigation rail for the Qt app (collapsible)."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QSize, QRectF
from PySide6.QtGui import QPixmap, QIcon, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QPushButton, QLabel,
                               QButtonGroup, QHBoxLayout)

import config


def _render_svg(path: str, color: str, size: int = 64) -> QPixmap:
    """Render an SVG and recolor it to `color`, keeping anti-aliased edges."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    QSvgRenderer(path).render(painter, QRectF(0, 0, size, size))
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pm.rect(), QColor(color))
    painter.end()
    return pm


def _state_icon(path: str, off: str, on: str, hover: str) -> QIcon:
    """Nav icon that recolors on hover/selection so it tracks the label color."""
    icon = QIcon()
    icon.addPixmap(_render_svg(path, off), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_render_svg(path, hover), QIcon.Active, QIcon.Off)
    icon.addPixmap(_render_svg(path, on), QIcon.Normal, QIcon.On)
    icon.addPixmap(_render_svg(path, on), QIcon.Active, QIcon.On)
    return icon


NAV_ITEMS = [
    ("icon_sessions.svg", "Sessions"),
    ("icon_setups.svg", "Setups"),
    ("icon_parts.svg", "Parts"),
    ("icon_compare.svg", "Compare"),
    ("icon_cheat.svg", "Cheat Sheet"),
    ("icon_settings.svg", "Settings"),
]

WIDTH_EXPANDED = 210
WIDTH_COLLAPSED = 68


class Sidebar(QFrame):
    navigate = Signal(str)
    recheck = Signal()
    sync_queue_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(WIDTH_EXPANDED)
        self._buttons: dict[str, QPushButton] = {}
        self._icons: dict[str, QIcon] = {}
        self._collapsed = False
        self._build()

    def _build(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 14, 12, 14)
        self._layout.setSpacing(4)

        # Collapse / expand toggle
        self._menu_icon = QIcon(_render_svg(
            str(config.ASSETS_DIR / "icon_menu.svg"), config.COLOR_TEXT))
        self._expand_icon = QIcon(_render_svg(
            str(config.ASSETS_DIR / "icon_expand.svg"), config.COLOR_TEXT))
        self._toggle = QPushButton()
        self._toggle.setObjectName("NavButton")
        self._toggle.setIcon(self._menu_icon)
        self._toggle.setIconSize(QSize(22, 22))
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setFixedWidth(36)
        self._toggle.clicked.connect(self.toggle)
        self._layout.addWidget(self._toggle, 0, Qt.AlignLeft)

        # Logo — full wordmark when expanded, square mark when collapsed, so the
        # Excalibur Racing logo always stays in the top corner.
        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignLeft)
        self._logo_pix = None
        if config.LOGO_PNG.exists():
            pix = QPixmap(str(config.LOGO_PNG))
            if not pix.isNull():
                self._logo_pix = pix
        if self._logo_pix is not None:
            self._logo.setPixmap(self._logo_pix.scaledToWidth(180, Qt.SmoothTransformation))
        else:
            self._logo.setText("xCAL")
        self._layout.addWidget(self._logo)

        self._ver = QLabel(f"xCAL \u00b7 v{config.APP_VERSION}")
        self._ver.setObjectName("Version")
        self._layout.addWidget(self._ver)
        self._layout.addSpacing(14)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for icon_file, label in NAV_ITEMS:
            icon = _state_icon(
                str(config.ASSETS_DIR / icon_file),
                config.COLOR_TEXT_MUTED, "#ffffff", config.COLOR_TEXT)
            self._icons[label] = icon
            btn = QPushButton(label)
            btn.setObjectName("NavButton")
            btn.setIcon(icon)
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(label)
            btn.clicked.connect(lambda _=False, l=label: self.navigate.emit(l))
            group.addButton(btn)
            btn.setFixedHeight(42)
            self._layout.addWidget(btn)
            self._buttons[label] = btn

        self._layout.addStretch(1)

        # Connection status (click for the full queue)
        self._status_row = QHBoxLayout()
        self._dot = QLabel("\u25CF")
        self._dot.setStyleSheet(f"color: {config.COLOR_TEXT_MUTED};")
        self._status = QLabel(" Connecting\u2026")
        self._status.setObjectName("Muted")
        self._status.setCursor(Qt.PointingHandCursor)
        self._status.setToolTip("Click to open the sync queue")
        self._status.mousePressEvent = (
            lambda _e: self.sync_queue_requested.emit())
        self._dot.mousePressEvent = (
            lambda _e: self.sync_queue_requested.emit())
        self._status_row.addWidget(self._dot)
        self._status_row.addWidget(self._status)
        self._status_row.addStretch(1)
        self._layout.addLayout(self._status_row)

        if "Sessions" in self._buttons:
            self._buttons["Sessions"].setChecked(True)

    # ── Collapse / expand ─────────────────────────────────────────────────────

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setFixedWidth(WIDTH_COLLAPSED if collapsed else WIDTH_EXPANDED)
        self._toggle.setIcon(self._expand_icon if collapsed else self._menu_icon)
        self._toggle.setIconSize(QSize(28 if collapsed else 22, 28 if collapsed else 22))
        self._layout.setContentsMargins(
            8 if collapsed else 12, 12 if collapsed else 14,
            8 if collapsed else 12, 12 if collapsed else 14)
        self._layout.setSpacing(8 if collapsed else 4)
        self._toggle.setFixedSize(48 if collapsed else 36, 42)
        self._toggle.setStyleSheet(
            "font-size:22px; padding:0; text-align:center;" if collapsed else "")
        self._layout.setAlignment(
            self._toggle, Qt.AlignHCenter if collapsed else Qt.AlignLeft)
        if not collapsed and self._logo_pix is not None:
            self._logo.setPixmap(self._logo_pix.scaledToWidth(180, Qt.SmoothTransformation))
            self._logo.setAlignment(Qt.AlignLeft)
        self._logo.setVisible(not collapsed)
        self._ver.setVisible(not collapsed)
        self._status.setVisible(not collapsed)
        dot_font = self._dot.font()
        dot_font.setPointSize(15 if collapsed else 9)
        self._dot.setFont(dot_font)
        self._status_row.setAlignment(
            Qt.AlignHCenter if collapsed else Qt.AlignLeft)
        for label, btn in self._buttons.items():
            if collapsed:
                btn.setText("")
                btn.setIcon(self._icons[label])
                btn.setIconSize(QSize(30, 30))
                btn.setFixedSize(52, 46)
                btn.setStyleSheet("")
                self._layout.setAlignment(btn, Qt.AlignHCenter)
            else:
                btn.setText(label)
                btn.setIcon(self._icons[label])
                btn.setIconSize(QSize(20, 20))
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(16777215)
                btn.setFixedHeight(42)
                btn.setStyleSheet("")
                self._layout.setAlignment(btn, Qt.AlignmentFlag(0))

    # ── Status ────────────────────────────────────────────────────────────────

    def set_active(self, label: str):
        btn = self._buttons.get(label)
        if btn:
            btn.setChecked(True)

    def set_connection(
            self, connected: bool, sync_summary=None,
            supabase_connected=None, drive_connected=None):
        supabase_connected = (
            connected if supabase_connected is None else supabase_connected)
        drive_connected = (
            connected if drive_connected is None else drive_connected)
        any_connected = supabase_connected or drive_connected
        both_connected = supabase_connected and drive_connected

        if sync_summary and sync_summary.total:
            if sync_summary.errors:
                self._dot.setStyleSheet("color: #e74c3c;")
                prefix = "Connected" if both_connected else "Cloud issue"
                self._status.setText(
                    f" {prefix} · {sync_summary.errors} need attention")
            else:
                self._dot.setStyleSheet(f"color: {config.COLOR_WARNING};")
                prefix = "Connected" if both_connected else "Partly connected"
                self._status.setText(
                    f" {prefix} · {sync_summary.total} uploading\u2026")
            details = []
            if sync_summary.files:
                details.append(f"{sync_summary.files} data/video")
            if sync_summary.setups:
                details.append(f"{sync_summary.setups} setup")
            if sync_summary.inventory:
                details.append(f"{sync_summary.inventory} part")
            suffix = (
                f"; {sync_summary.errors} had an error and will retry"
                if sync_summary.errors else "")
            self._status.setToolTip(
                "Cloud access and this device's upload queue are separate. "
                "Waiting locally: " + ", ".join(details) + suffix
                + ". Click to open the sync queue.")
        elif both_connected:
            self._dot.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self._status.setText(" Cloud connected")
            self._status.setToolTip(
                "No uploads are waiting on this device. Files use Google Drive; "
                "setups and Parts also use Google Drive. Click to open the sync queue.")
        elif any_connected:
            self._dot.setStyleSheet(f"color: {config.COLOR_WARNING};")
            service = "Local catalog" if supabase_connected else "Google Drive"
            self._status.setText(f" {service} available")
            self._status.setToolTip(
                f"{service} is available; the other cloud service is offline. "
                "Click to open the sync queue.")
        else:
            self._dot.setStyleSheet("color: #e74c3c;")
            self._status.setText(" Offline \u21BB")
            self._status.setToolTip(
                "Cloud unavailable. Local changes are kept and retry "
                "automatically. Click to open the sync queue.")

    def set_connecting(self):
        self._dot.setStyleSheet(f"color: {config.COLOR_WARNING};")
        self._status.setText(" Connecting\u2026")

    def set_syncing(
            self, sync_summary, supabase_connected=True,
            drive_connected=True):
        """Show queue progress while an upload pass is running."""
        self.set_connection(
            supabase_connected and drive_connected, sync_summary,
            supabase_connected=supabase_connected,
            drive_connected=drive_connected)
