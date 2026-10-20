"""Compare page (Qt) — one searchable "session · setup" picker per side + diff.

Pick any setup on each side from a single dropdown (type to filter, or use the
caret to browse every setup), and see the two laid out column by column with
every differing field highlighted.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout,
                               QLabel, QComboBox, QFrame, QScrollArea)

import config
import cache
import database.client as db
import offline_store
import setup_fields as sf
from qt.workers import run_async
from qt.widgets import make_combo_searchable

CHANGED = "rgba(230,160,32,0.16)"
ADDED = "rgba(63,183,101,0.14)"
REMOVED = "rgba(139,47,47,0.28)"


def _fmt(v):
    if v in (None, ""):
        return "\u2014"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _norm(v):
    return _fmt(v).strip().lower()


class _ReadableSetupCombo(QComboBox):
    """Compact setup picker with a wide, full-label popup."""

    def _resize_popups(self):
        labels = [self.itemText(index) for index in range(self.count())]
        longest = max(
            (self.fontMetrics().horizontalAdvance(label) for label in labels),
            default=self.width(),
        )
        screen = self.screen()
        screen_limit = (
            screen.availableGeometry().width() - 80 if screen else 900)
        width = max(
            self.width(), min(longest + 48, screen_limit, 900))
        self.view().setMinimumWidth(width)
        completer = self.completer()
        if completer is not None:
            completer.popup().setMinimumWidth(width)

    def showPopup(self):  # noqa: N802 (Qt override)
        self._resize_popups()
        super().showPopup()


class _Slot:
    """One compare column: a single 'session · setup' picker + the chosen setup."""
    def __init__(self, letter, on_change):
        self.letter = letter
        self.on_change = on_change
        self.setup = None

        self.frame = QFrame()
        self.frame.setObjectName("Card")
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        lab = QLabel(f"SETUP {letter}")
        lab.setObjectName("Accent")
        lay.addWidget(lab)

        self.combo = make_combo_searchable(
            _ReadableSetupCombo(), placeholder="Loading setups\u2026")
        self.combo.currentIndexChanged.connect(self._changed)
        lay.addWidget(self.combo)

    def set_entries(self, entries):
        """Repopulate from [(label, setup)]; keeps nothing selected (grey hint)."""
        self.combo.completer().popup().hide()
        self.combo.blockSignals(True)
        self.combo.clear()
        for label, setup in entries:
            self.combo.addItem(label, setup)
            self.combo.setItemData(
                self.combo.count() - 1, label, Qt.ToolTipRole)
        self.combo._resize_popups()
        self.combo.lineEdit().setPlaceholderText(
            "Search or pick a setup\u2026" if entries else "No setups found")
        self.combo.setCurrentIndex(-1)
        self.combo.blockSignals(False)
        self.setup = None

    def _changed(self, _i):
        self.setup = self.combo.currentData()
        self.on_change()

    def select_setup_id(self, setup_id) -> bool:
        for i in range(self.combo.count()):
            st = self.combo.itemData(i)
            if st is not None and getattr(st, "id", None) == setup_id:
                self.combo.setCurrentIndex(i)
                return True
        return False


def _entry_label(prefix, s) -> str:
    label = f"{prefix}  \u00b7  {s.name or 'Setup'}"
    tags = " \u00b7 ".join(t for t in (s.driver_name, s.chassis_name) if t)
    if tags:
        label += f"  ({tags})"
    return label


class CompareView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_map = {}
        self._entries = []
        self._pending_a_id = None
        self._loaded = False
        self._catalog_generation = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        h1 = QLabel("Compare Setups")
        h1.setObjectName("H1")
        root.addWidget(h1)
        hint = QLabel("Pick any two setups \u2014 from any session \u2014 to see what "
                      "changed. Type to filter or use the caret to browse.")
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self._slots_grid = QGridLayout()
        self._slots_grid.setContentsMargins(12, 0, 12, 0)
        self._slots_grid.setHorizontalSpacing(10)
        self._slots_grid.setColumnStretch(0, 3)
        self._slots_grid.setColumnStretch(1, 2)
        self._slots_grid.setColumnStretch(2, 2)
        root.addLayout(self._slots_grid)

        self._diff_scroll = QScrollArea()
        self._diff_scroll.setWidgetResizable(True)
        self._diff_host = QWidget()
        self._diff_lay = QVBoxLayout(self._diff_host)
        self._diff_lay.setContentsMargins(0, 4, 8, 4)
        self._diff_lay.setSpacing(0)
        self._diff_scroll.setWidget(self._diff_host)
        root.addWidget(self._diff_scroll, 1)

        self._placeholder()

    def _placeholder(self):
        self._clear(self._diff_lay)
        msg = QLabel("Choose two setups above to compare them side by side.")
        msg.setObjectName("Muted")
        msg.setAlignment(Qt.AlignCenter)
        self._diff_lay.addWidget(msg)

    @staticmethod
    def _clear(lay):
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def on_show(self):
        if self._loaded:
            return
        self._loaded = True
        self._catalog_generation += 1
        generation = self._catalog_generation
        sessions, _ = cache.load_sessions()
        self._session_map = {s.id: s for s in sessions}
        self._build_slots()
        run_async(
            self,
            lambda: (generation, self._fetch_entries()),
            on_done=self._on_entries,
            on_error=lambda _message: self._on_entries((generation, None)),
        )

    def invalidate(self):
        """Reload catalog entries the next time Compare is shown."""
        self._catalog_generation += 1
        self._loaded = False
        self._entries = []

    def _fetch_entries(self):
        try:
            baselines, setups = db.fetch_setup_catalog()
            offline_store.cache_setup_catalog(baselines, setups)
        except Exception:
            cached = offline_store.load_setup_catalog(include_baselines=True)
            baselines = [setup for setup in cached if setup.is_baseline]
            setups = [setup for setup in cached if not setup.is_baseline]
        return setups, baselines

    def _on_entries(self, payload):
        generation, result = payload
        if generation != self._catalog_generation:
            return
        setups, baselines = result if result else ([], [])
        setup_entries = []
        for s in setups or []:
            sess = self._session_map.get(s.drive_folder_id)
            name = sess.name if sess else (s.drive_folder_path or s.track or "Session")
            setup_entries.append((_entry_label(name, s), s))
        setup_entries.sort(key=lambda e: e[0].lower(), reverse=True)
        baseline_entries = [(_entry_label("Baseline", b), b)
                            for b in (baselines or [])]
        baseline_entries.sort(key=lambda e: e[0].lower())
        self._entries = setup_entries + baseline_entries
        self._a.set_entries(self._entries)
        self._b.set_entries(self._entries)
        if self._pending_a_id is not None:
            self._a.select_setup_id(self._pending_a_id)
            self._pending_a_id = None

    def preload_a(self, setup):
        """Open side A on the given setup (from a session's 'View Setup')."""
        self.on_show()
        if setup is None:
            return
        if self._entries:
            self._a.select_setup_id(getattr(setup, "id", None))
        else:
            self._pending_a_id = getattr(setup, "id", None)

    def _build_slots(self):
        while self._slots_grid.count():
            item = self._slots_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        guide = QFrame()
        guide.setObjectName("Card")
        guide_lay = QVBoxLayout(guide)
        guide_lay.setContentsMargins(12, 10, 12, 12)
        guide_lay.setSpacing(6)
        guide_title = QLabel("SETUP SELECTION")
        guide_title.setObjectName("Accent")
        guide_lay.addWidget(guide_title)
        guide_text = QLabel(
            "Pick a setup for each side \u2014 type to filter or use the caret to "
            "browse every setup. The columns below line up with your picks.")
        guide_text.setObjectName("Muted")
        guide_text.setWordWrap(True)
        guide_lay.addWidget(guide_text)
        guide_lay.addStretch(1)

        self._a = _Slot("A", self._render_diff)
        self._b = _Slot("B", self._render_diff)
        if self._entries:
            self._a.set_entries(self._entries)
            self._b.set_entries(self._entries)
        self._slots_grid.addWidget(guide, 0, 0)
        self._slots_grid.addWidget(self._a.frame, 0, 1)
        self._slots_grid.addWidget(self._b.frame, 0, 2)

    # ── Diff ──────────────────────────────────────────────────────────────────

    def _render_diff(self):
        a, b = getattr(self, "_a", None), getattr(self, "_b", None)
        if not a or not b or a.setup is None or b.setup is None:
            self._placeholder()
            return
        self._clear(self._diff_lay)
        da = a.setup.data or {}
        db_ = b.setup.data or {}

        header = self._diff_row(
            "Field", a.setup.name or "Setup A", b.setup.name or "Setup B",
            bold=True, header=True)
        self._diff_lay.addWidget(header)

        groups = [(title, sf.section_fields(section)) for (title, _fields), section in
                  zip(sf.MAIN_GROUPS, ("front", "engine", "rear", "tires"))]
        groups.append(("Session", list(sf.SESSION_FIELDS) + sf.custom_fields("session")))
        groups.append(("Axle Detail", sf.DETAIL_AXLE_FIELDS))
        for code in sf.CORNERS:
            name, _, _ = sf.CORNERS[code]
            groups.append((name, sf.corner_fields(code)))

        diff_count = 0
        for title, fields in groups:
            shown = [(f, da.get(f.key), db_.get(f.key)) for f in fields
                     if da.get(f.key) not in (None, "") or db_.get(f.key) not in (None, "")]
            if not shown:
                continue
            grp = QLabel(title)
            grp.setMinimumHeight(30)
            grp.setStyleSheet(
                f"background:{config.COLOR_SIDEBAR}; color:{config.COLOR_ACCENT};"
                "font-weight:700; padding:6px 12px;")
            self._diff_lay.addWidget(grp)
            for f, av, bv in shown:
                a_empty = av in (None, "")
                b_empty = bv in (None, "")
                row_bg = None
                if not a_empty and not b_empty and _norm(av) != _norm(bv):
                    row_bg = CHANGED
                    diff_count += 1
                elif a_empty and not b_empty:
                    row_bg = ADDED
                    diff_count += 1
                elif not a_empty and b_empty:
                    row_bg = REMOVED
                    diff_count += 1
                self._diff_lay.addWidget(
                    self._diff_row(f.label, _fmt(av), _fmt(bv), row_bg=row_bg))

        summary = QLabel(f"\u0394 {diff_count} field(s) differ")
        summary.setStyleSheet(
            f"color:{config.COLOR_WARNING if diff_count else config.COLOR_SUCCESS};"
            "font-weight:700; padding:10px 4px;")
        self._diff_lay.addWidget(summary)
        self._diff_lay.addStretch(1)

    def _diff_row(self, field, a_val, b_val, bold=False, header=False,
                  row_bg=None):
        row = QFrame()
        row.setMinimumHeight(34)
        row.setStyleSheet("border-bottom:1px solid " + config.COLOR_BORDER + ";"
                          + (f"background:{config.COLOR_SIDEBAR};" if header
                             else f"background:{row_bg};" if row_bg else ""))
        grid = QGridLayout(row)
        grid.setContentsMargins(12, 7, 12, 7)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)

        fl = QLabel(field)
        fl.setStyleSheet("font-weight:700;" if (bold or header)
                         else f"color:{config.COLOR_TEXT_MUTED};")
        grid.addWidget(fl, 0, 0)

        a_lbl = QLabel(a_val)
        a_lbl.setWordWrap(True)
        a_lbl.setStyleSheet("font-weight:700;" if (bold or header or row_bg) else "")
        grid.addWidget(a_lbl, 0, 1)

        b_lbl = QLabel(b_val)
        b_lbl.setWordWrap(True)
        if bold or header or row_bg:
            b_lbl.setStyleSheet("font-weight:700;")
        grid.addWidget(b_lbl, 0, 2)
        return row
