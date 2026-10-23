"""Sessions page (Qt) — the data-rich home.

Loads instantly from the local SQLite cache, then refreshes the Drive walk in the
background. Qt composites the whole page off-screen, so it appears at once — no
tearing, no staggered "wave".
"""
from __future__ import annotations
import re
from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QComboBox, QPushButton, QScrollArea,
                               QFrame, QProgressBar, QSizePolicy, QMessageBox,
                               QDialog, QFormLayout, QDialogButtonBox)

import config
from qt.responsive import FlowLayout
import cache
import session_types
import database.client as db
import offline_store
from utils import google_drive_client
from utils.xrk_meta import parse_xrk_filename
from qt.workers import run_async
from qt.widgets import DatePickerEdit, make_combo_searchable

_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
_SEASON_RE = re.compile(
    r"(?<!\d)(\d{4})(?:\s*[-\u2013\u2014/]\s*(\d{4}|\d{2}))?(?!\d)")
_NAME_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _parse(name: str):
    m = _DATE_RE.match(name)
    if not m:
        return None, name
    d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    rest = name[m.end():].strip(" -_")
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    return d, rest


def _display(s):
    d, label = _parse(s.name)
    if d:
        return d, (label or s.name), None
    parts = [p for p in s.path.split(" / ") if p]
    parent = parts[-2] if len(parts) >= 2 else s.name
    pd, plabel = _parse(parent)
    if pd:
        tag = f"Kart {s.name}" if s.name.strip().isdigit() else s.name
        return pd, (plabel or parent), tag
    # An undated leaf is still a real session/event. The previous fallback used
    # its parent season as the title and demoted the actual folder name to a tag.
    return None, s.name, None


def _season_sort_key(name: str):
    """Put year-labelled seasons newest first, then custom folders by name."""
    match = _SEASON_RE.search(name or "")
    if not match:
        return 1, 0, 0, (name or "").casefold()

    start = int(match.group(1))
    raw_end = match.group(2)
    if not raw_end:
        end = start
    elif len(raw_end) == 4:
        end = int(raw_end)
    else:
        # Expand 2025-26 to 2026 (and handle a century rollover cleanly).
        end = (start // 100) * 100 + int(raw_end)
        if end < start:
            end += 100
    return 0, -start, -end, (name or "").casefold()


def _session_sort_key(session):
    """Put dated/new season sessions first, including undated event-year names."""
    session_date = _display(session)[0]
    if session_date:
        return 0, tuple(-int(part) for part in session_date.split("-")), session.name.casefold()
    years = _NAME_YEAR_RE.findall(session.name or "")
    if years:
        # Event folders such as "Halloween 100 2026" have no exact day. Treat
        # their named year as newer than dated sessions earlier in that year.
        return 0, (-int(years[-1]), -12, -31), session.name.casefold()
    return 1, (0, 0, 0), session.name.casefold()


def _people(xrks):
    drivers, karts = [], []
    for x in xrks:
        m = parse_xrk_filename(x.name)
        if not m:
            continue
        if m.driver and m.driver not in drivers:
            drivers.append(m.driver)
        if m.number:
            tag = f"#{m.number}" + (f" {m.chassis}" if m.chassis else "")
            if tag not in karts:
                karts.append(tag)
    return drivers, karts


def _setup_identity_labels(setups):
    """Compact driver/kart labels taken only from saved setup records."""
    labels = []
    for setup in setups or []:
        if setup.driver_name and setup.driver_name not in labels:
            labels.append(setup.driver_name)
        number = str(setup.get("kart_number") or "").strip()
        kart = " ".join(part for part in (
            f"#{number}" if number else "", setup.chassis_name or "") if part)
        if kart and kart not in labels:
            labels.append(kart)
    return labels


def _merge_offline_sessions(sessions):
    """Keep durable packages reachable when Drive is unavailable."""
    merged = {session.id: session for session in sessions or []}
    for session in offline_store.list_session_folders():
        merged.setdefault(session.id, session)
    return list(merged.values())


class NewSessionDialog(QDialog):
    """Collect a season + name for a new session, prefilled with today's date."""

    def __init__(self, seasons, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Session")
        self.setMinimumWidth(420)
        form = QFormLayout(self)

        self._season = QComboBox()
        self._season.setEditable(True)
        self._season.addItems(seasons)
        if seasons:
            self._season.setCurrentIndex(0)
        else:
            self._season.setCurrentText("")
        form.addRow("Season", self._season)

        self._date = DatePickerEdit()
        self._date.setText(date.today().isoformat())
        form.addRow("Date", self._date)
        self._session_type = QComboBox()
        for kind, label in session_types.TYPES.items():
            self._session_type.addItem(label, kind)
        form.addRow("Session type", self._session_type)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Halloween 100 2026")
        form.addRow("Session / event name", self._name)

        hint = QLabel(
            "The Drive folder is named from the date and event name. "
            "You can edit it later inside the session.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        session_name = " ".join(part for part in (
            self._date.text().strip(), self._name.text().strip()) if part)
        return self._season.currentText().strip(), session_name


class SessionCard(QFrame):
    activated = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, session, n_setups: int, setup_labels=None, parent=None):
        super().__init__(parent)
        self._session = session
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self._build(session, n_setups, setup_labels or [])

    def _build(self, s, n_setups, setup_labels):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(12)

        d, label, kart_tag = _display(s)
        date = QLabel(d or "\u2014")
        date.setObjectName("CardTitle")
        date.setFixedWidth(90)
        lay.addWidget(date)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        title = QLabel(label or s.name)
        title.setWordWrap(True)
        title.setObjectName("CardTitle")
        mid.addWidget(title)

        bits = [session_types.TYPES[session_types.classify(s.id, s.name, s.path)]]
        if kart_tag:
            bits.append(f"{kart_tag}")
        if n_setups:
            # Saved setup identity is authoritative. Do not mix in a stale XRK
            # filename/roster mapping when a session has actual setup records.
            bits += setup_labels[:5]
        else:
            drivers, karts = _people(s.xrks)
            bits += [f"{x}" for x in drivers[:2]]
            bits += [f"{k}" for k in karts[:3]]
        sub = QLabel("   ".join(bits) if bits else s.path)
        sub.setWordWrap(True)
        sub.setObjectName("CardSub")
        mid.addWidget(sub)
        lay.addLayout(mid, 1)

        n_data = len(s.xrks)
        badge = (f"{n_data} files" if n_data else "no data")
        badge += (f"    {n_setups} setups" if n_setups else "    no setups")
        blab = QLabel(badge)
        blab.setObjectName("Success" if (n_data or n_setups) else "Muted")
        lay.addWidget(blab)

        deleteb = QPushButton("Delete")
        deleteb.setObjectName("Danger")
        deleteb.setToolTip("Delete session")
        deleteb.setCursor(Qt.PointingHandCursor)
        deleteb.clicked.connect(
            lambda _checked=False: self.delete_requested.emit(self._session))
        lay.addWidget(deleteb)

        # Let a click anywhere on the card reach the frame, even over text.
        for label_widget in (date, title, sub, blab):
            label_widget.setAttribute(Qt.WA_TransparentForMouseEvents)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self.activated.emit(self._session)
        super().mouseReleaseEvent(event)


class SessionsView(QWidget):
    open_session = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: list = []
        self._counts: dict = {}
        self._setup_labels: dict = {}
        self._loaded = False
        self._refreshing = False
        self._refresh_pending = False
        self._counts_refreshing = False
        self._counts_refresh_pending = False
        self._counts_generation = 0
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._filter_timer.timeout.connect(self._render)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        # Header
        header = FlowLayout()
        h1 = QLabel("Sessions")
        h1.setObjectName("H1")
        header.addWidget(h1)
        self._count = QLabel("")
        self._count.setObjectName("Muted")
        header.addWidget(self._count)
        hint = QLabel("\u00b7 click a session to open")
        hint.setObjectName("Muted")
        header.addWidget(hint)
        header.addStretch(1)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedWidth(130)
        self._progress.hide()
        header.addWidget(self._progress)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("Ghost")
        self._refresh_btn.setMinimumWidth(92)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setToolTip("Refresh sessions from Google Drive")
        self._refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        header.addWidget(self._refresh_btn)
        root.addLayout(header)

        # Hero
        self._hero = QFrame()
        self._hero.setObjectName("Hero")
        self._hero_lay = QHBoxLayout(self._hero)
        self._hero_lay.setContentsMargins(18, 16, 18, 16)
        self._hero.hide()
        root.addWidget(self._hero)

        # Toolbar
        bar = FlowLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sessions, drivers, tracks\u2026")
        self._search.setMinimumWidth(160)
        self._search.textChanged.connect(self._schedule_filter)
        bar.addWidget(self._search)
        self._season = make_combo_searchable(QComboBox())
        self._season.addItem("All seasons")
        self._season.setMinimumWidth(140)
        self._season.currentIndexChanged.connect(self._render)
        bar.addWidget(self._season)
        self._type_filter = QComboBox()
        self._type_filter.addItem("All session types", None)
        for kind, label in session_types.TYPES.items():
            self._type_filter.addItem(label, kind)
        self._type_filter.currentIndexChanged.connect(self._render)
        bar.addWidget(self._type_filter)
        bar.addStretch(1)
        newb = QPushButton("New Session")
        newb.setObjectName("Primary")
        newb.setCursor(Qt.PointingHandCursor)
        newb.clicked.connect(self._new_session)
        bar.addWidget(newb)
        root.addLayout(bar)

        # Scrollable card list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list = QWidget()
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(0, 4, 8, 8)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch(1)
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll, 1)

    def _schedule_filter(self, *_args):
        """Coalesce rapid keystrokes so large session lists rebuild once."""
        self._filter_timer.start()

    # ── Data ────────────────────────────────────────────────────────────────

    def refresh(self, force: bool = False):
        if not config.GOOGLE_DRIVE_FOLDER_ID:
            self._loaded = True
            self._sessions = _merge_offline_sessions([])
            self._populate_seasons()
            self._render_hero()
            self._render()
            self._refresh_btn.setToolTip("Connect a team Google Drive in Settings to load shared sessions")
            return
        sessions_cache_is_fresh = False
        counts_cache_is_fresh = False
        if not self._loaded:
            cached, age = cache.load_sessions()
            offline_sessions = offline_store.list_session_folders()
            if age is not None or offline_sessions:
                self._sessions = _merge_offline_sessions(cached)
                cached_counts, counts_age = cache.load_setup_counts()
                self._counts = dict(cached_counts or {})
                self._setup_labels = (
                    cache.load_setup_summaries() if cached_counts is not None
                    else {})
                for session in offline_sessions:
                    setups = offline_store.load_setups(session.id)
                    self._counts.setdefault(session.id, len(setups))
                    self._setup_labels.setdefault(
                        session.id, _setup_identity_labels(setups))
                # Render the disk cache immediately. Setup counts come back with
                # the background refresh; a Supabase call here used to block the
                # Sessions page before its cached cards could appear.
                self._last_signature = self._signature(
                    self._sessions, self._counts, self._setup_labels)
                self._loaded = True
                self._populate_seasons()
                self._render_hero()
                self._render()
                sessions_cache_is_fresh = age is not None and age < 300
                counts_cache_is_fresh = (
                    cached_counts is not None
                    and counts_age is not None and counts_age < 300)
        # A reload moments after the last sync should not immediately walk Drive
        # again and visibly refresh the page. The refresh button passes force=True.
        if sessions_cache_is_fresh and not force:
            if not counts_cache_is_fresh:
                self.refresh_setup_counts()
            return
        if self._refreshing:
            if force:
                self._refresh_pending = True
            return
        self._refreshing = True
        # Supersede any older counts-only request. If it came from a setup
        # mutation, a failed full refresh still needs one final count query.
        self._counts_generation += 1
        refresh_generation = self._counts_generation
        if self._counts_refreshing:
            self._counts_refresh_pending = True
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Refreshing\u2026")
        self._refresh_btn.setToolTip("Refreshing sessions from Google Drive")
        self._progress.show()

        run_async(self, lambda: self._drive_refresh(refresh_generation),
                  on_done=self._on_refreshed, on_error=self._on_refresh_error)

    def _drive_refresh(self, counts_generation):
        try:
            session_types.refresh()
        except Exception:
            pass  # Keep cached classifications available while offline.
        # Drive traversal and Supabase badge counts are independent. Fetch them
        # together so refresh duration is the slower call, not both added up.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            sessions_future = pool.submit(
                google_drive_client.walk_session_folders)
            counts_future = pool.submit(db.fetch_setup_folder_overview)
            sessions = sessions_future.result()
            try:
                counts, setup_summaries = counts_future.result()
                setup_labels = {
                    fid: _setup_identity_labels(setups)
                    for fid, setups in setup_summaries.items()
                }
            except Exception:
                counts = None
                setup_labels = None
        cache.save_sessions(sessions)
        return sessions, counts, setup_labels, counts_generation

    def _on_refreshed(self, result):
        self._refreshing = False
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("Refresh")
        self._refresh_btn.setToolTip("Refresh sessions from Google Drive")
        self._progress.hide()
        sessions, counts, setup_labels, counts_generation = result
        sessions = _merge_offline_sessions(sessions)
        # None means the count query failed; an empty dict is a valid response
        # and must be allowed to clear stale setup badges.
        counts_are_current = (
            counts is not None
            and counts_generation == self._counts_generation)
        if counts_are_current:
            effective_counts = counts
            effective_labels = setup_labels or {}
            cache.save_setup_counts(counts)
            cache.save_setup_summaries(effective_labels)
            # This full refresh supersedes any older counts-only request.
            self._counts_refresh_pending = False
        else:
            effective_counts = self._counts
            effective_labels = self._setup_labels
        signature = self._signature(
            sessions, effective_counts, effective_labels)
        if signature == getattr(self, "_last_signature", None):
            # Cache already matches Drive: update references, but do not rebuild
            # a page the user is currently looking at.
            self._sessions = sessions
            self._counts = effective_counts
            self._setup_labels = effective_labels
            self._loaded = True
            self._run_pending_counts_refresh()
            self._run_pending_drive_refresh()
            return
        self._last_signature = signature
        self._sessions = sessions
        self._counts = effective_counts
        self._setup_labels = effective_labels
        self._loaded = True
        self._populate_seasons()
        self._render_hero()
        self._render()
        self._run_pending_counts_refresh()
        self._run_pending_drive_refresh()

    def _on_refresh_error(self, message):
        self._refreshing = False
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("Refresh")
        self._refresh_btn.setToolTip(
            f"Refresh failed: {message}\nCached sessions are still available.")
        self._progress.hide()
        self._run_pending_counts_refresh()
        self._run_pending_drive_refresh()

    def _run_pending_drive_refresh(self):
        if not self._refresh_pending or self._refreshing:
            return
        self._refresh_pending = False
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def refresh_setup_counts(self):
        """Update setup badges without performing an unrelated Drive walk."""
        self._counts_generation += 1
        cache.invalidate_setup_counts()
        if not self._loaded:
            self._counts_refresh_pending = True
            return
        if self._refreshing or self._counts_refreshing:
            self._counts_refresh_pending = True
            return
        self._start_setup_counts_refresh()

    def _start_setup_counts_refresh(self):
        self._counts_refreshing = True
        generation = self._counts_generation

        def _done(result):
            self._counts_refreshing = False
            result_generation, counts, setup_summaries = result
            if result_generation == self._counts_generation:
                counts = counts or {}
                setup_labels = {
                    fid: _setup_identity_labels(setups)
                    for fid, setups in (setup_summaries or {}).items()
                }
                cache.save_setup_counts(counts)
                cache.save_setup_summaries(setup_labels)
                if (counts != self._counts
                        or setup_labels != self._setup_labels):
                    self._counts = counts
                    self._setup_labels = setup_labels
                    self._last_signature = self._signature(
                        self._sessions, self._counts, self._setup_labels)
                    self._render()
            self._run_pending_counts_refresh()

        def _error(_message):
            self._counts_refreshing = False
            if generation == self._counts_generation:
                cache.invalidate_setup_counts()
            self._run_pending_counts_refresh()

        run_async(
            self,
            lambda: (generation, *db.fetch_setup_folder_overview()),
            on_done=_done, on_error=_error)

    def _run_pending_counts_refresh(self):
        if (not self._counts_refresh_pending
                or self._refreshing or self._counts_refreshing):
            return
        self._counts_refresh_pending = False
        cache.invalidate_setup_counts()
        self._start_setup_counts_refresh()

    @staticmethod
    def _signature(sessions, counts, setup_labels=None):
        return (
            tuple(sorted((
                s.id,
                s.name,
                s.path,
                s.season,
                session_types.classify(s.id, s.name, s.path),
                tuple(sorted(
                    (node.id, node.name, node.size) for node in s.xrks)),
            ) for s in sessions)),
            tuple(sorted((counts or {}).items())),
            tuple(sorted(
                (folder_id, tuple(labels))
                for folder_id, labels in (setup_labels or {}).items())),
        )

    def _populate_seasons(self):
        seasons = sorted(
            {s.season for s in self._sessions if s.season},
            key=_season_sort_key,
        )
        cur = self._season.currentText()
        self._season.blockSignals(True)
        self._season.clear()
        self._season.addItem("All seasons")
        self._season.addItems(seasons)
        idx = self._season.findText(cur)
        self._season.setCurrentIndex(idx if idx >= 0 else 0)
        self._season.blockSignals(False)

    # ── Hero ────────────────────────────────────────────────────────────────

    def _render_hero(self):
        self._hero.setUpdatesEnabled(False)
        self._clear_layout(self._hero_lay)
        if not self._sessions:
            self._hero.hide()
            self._hero.setUpdatesEnabled(True)
            return
        latest = min(self._sessions, key=_session_sort_key)
        d, label, _tag = _display(latest)
        n_setups = self._counts.get(latest.id, 0)
        if n_setups:
            identity_labels = self._setup_labels.get(latest.id, [])
        else:
            drivers, karts = _people(latest.xrks)
            identity_labels = ([f"{x}" for x in drivers[:2]]
                               + [f"{k}" for k in karts[:2]])

        left = QVBoxLayout()
        tag = QLabel("JUMP BACK IN \u00b7 LATEST SESSION")
        tag.setObjectName("Accent")
        left.addWidget(tag)
        title = QLabel(f"{d}  \u2014  {label or latest.name}")
        title.setWordWrap(True)
        title.setObjectName("H2")
        left.addWidget(title)
        bits = identity_labels[:4] + [f"{len(latest.xrks)} files"]
        sub = QLabel("   ".join(bits))
        sub.setWordWrap(True)
        sub.setObjectName("Muted")
        left.addWidget(sub)
        self._hero_lay.addLayout(left, 1)

        open_hint = QLabel("Click to open  \u2192")
        open_hint.setObjectName("Accent")
        self._hero_lay.addWidget(open_hint, 0, Qt.AlignVCenter)
        for label_widget in (tag, title, sub, open_hint):
            label_widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hero.setCursor(Qt.PointingHandCursor)
        self._hero.mouseReleaseEvent = (
            lambda event, s=latest:
            self.open_session.emit(s) if event.button() == Qt.LeftButton else None)
        self._hero.show()
        self._hero.setUpdatesEnabled(True)
        self._hero.update()

    @classmethod
    def _clear_layout(cls, layout):
        """Delete widgets and nested layouts (the hero contains a QVBoxLayout)."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
            elif child_layout is not None:
                cls._clear_layout(child_layout)
                child_layout.deleteLater()

    # ── List ────────────────────────────────────────────────────────────────

    def _render(self):
        # Rebuild the list. Qt buffers this off-screen, so it appears at once.
        self.setUpdatesEnabled(False)
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        q = self._search.text().strip().lower()
        season_f = self._season.currentText()

        def match(s):
            kind = self._type_filter.currentData()
            if kind and session_types.classify(s.id, s.name, s.path) != kind:
                return False
            if season_f != "All seasons" and s.season != season_f:
                return False
            if q:
                drivers, karts = _people(s.xrks)
                blob = " ".join(
                    [s.name, s.path] + drivers + karts
                    + self._setup_labels.get(s.id, [])).lower()
                if q not in blob:
                    return False
            return True

        rows = [s for s in self._sessions if match(s)]
        self._count.setText(
            f"{len(rows)} sessions \u00b7 {sum(len(s.xrks) for s in rows)} files")

        groups: dict[str, list] = {}
        for s in rows:
            groups.setdefault(s.season or "Other", []).append(s)

        ordered = sorted(groups, key=_season_sort_key)

        if not rows:
            empty = QLabel("No sessions match your search.")
            empty.setObjectName("Muted")
            self._list_lay.addWidget(empty)
        else:
            for season in ordered:
                items = groups[season]
                files = sum(len(s.xrks) for s in items)
                hdr = QHBoxLayout()
                sh = QLabel(f"{season}")
                sh.setObjectName("SeasonHdr")
                hdr.addWidget(sh)
                hdr.addStretch(1)
                meta = QLabel(f"{len(items)} sessions \u00b7 {files} files")
                meta.setObjectName("Muted")
                hdr.addWidget(meta)
                wrap = QWidget()
                wrap.setLayout(hdr)
                self._list_lay.addWidget(wrap)

                for s in sorted(items, key=_session_sort_key):
                    card = SessionCard(
                        s,
                        self._counts.get(s.id, 0),
                        self._setup_labels.get(s.id, []),
                    )
                    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                    card.activated.connect(self.open_session.emit)
                    card.delete_requested.connect(self.request_delete_session)
                    self._list_lay.addWidget(card)

        self._list_lay.addStretch(1)
        self.setUpdatesEnabled(True)
        self.update()

    # Called by the main window when this page becomes visible the first time.
    def on_show(self):
        if not self._loaded:
            self.refresh()

    # ── Delete ──────────────────────────────────────────────────────────────

    # ── New session ─────────────────────────────────────────────────────────

    def _new_session(self):
        seasons = sorted(
            {s.season for s in self._sessions if s.season},
            key=_season_sort_key,
        )
        dialog = NewSessionDialog(seasons, self)
        if dialog.exec() != QDialog.Accepted:
            return
        season, name = dialog.values()
        if not name:
            QMessageBox.warning(
                self, "Name Required", "Please enter a session name.")
            return

        new_type = dialog._session_type.currentData()

        def _create():
            folder_id = google_drive_client.create_session_folder(
                name, season or None)
            try:
                session_types.save(folder_id, new_type)
            except Exception as exc:
                return folder_id, str(exc)
            return folder_id, None

        def _created(result):
            self.refresh(force=True)
            if result[1]:
                QMessageBox.warning(self, "Session created", "The session was created, but its type could not be saved. Open the session to set its type.\n\n" + result[1])

        run_async(
            self, _create,
            on_done=_created,
            on_error=lambda message: QMessageBox.critical(
                self, "Create Session Failed", message))

    def request_delete_session(self, session, on_done=None):
        setup_count = self._counts.get(session.id, 0)
        answer = QMessageBox.warning(
            self,
            "Delete Session?",
            f"Delete “{session.name}”?\n\n"
            f"• The Google Drive folder (and {len(session.xrks)} .xrk file(s)) "
            "will be moved to Drive trash.\n"
            f"• {setup_count} linked setup record(s) will be permanently deleted."
            "\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _delete():
            google_drive_client.trash_file(session.id)
            db.delete_setups_by_folder(session.id)
            return session.id

        def _deleted(_folder_id):
            self._sessions = [s for s in self._sessions if s.id != session.id]
            self._counts.pop(session.id, None)
            cache.save_sessions(self._sessions)
            cache.save_setup_counts(self._counts)
            if self._refreshing:
                self._refresh_pending = True
            self._populate_seasons()
            self._render_hero()
            self._render()
            if on_done:
                on_done()

        run_async(
            self,
            _delete,
            on_done=_deleted,
            on_error=lambda message: QMessageBox.critical(
                self, "Delete Session Failed", message),
        )
