"""Review and export TireLogger data aligned to an AiM CSV timeline."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QProgressBar,
    QVBoxLayout, QWidget,
)

from data_merge.race_studio_build import build_race_studio_package
from data_merge.aim_ibt import read_aim_channel_schema, suggest_channel_mapping
from data_merge.rpm_sync import estimate_rpm_time_map, read_aim_rpm, read_tire_rpm
from qt.workers import run_async


RPM_TOLERANCE = 500.0
MIN_MATCH_FRACTION = 0.60


def _file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact(points, mapper=None, limit=1200):
    stride = max(1, len(points) // limit)
    return [
        ((mapper(point.time_s) if mapper else point.time_s), point.rpm)
        for point in points[::stride]
    ]


def analyze_files(aim_csv: str, tire_csv: str) -> dict:
    """Analyze a pair without writing output; safe to run on a worker thread."""
    aim = read_aim_rpm(aim_csv)
    tires = read_tire_rpm(tire_csv)
    report = estimate_rpm_time_map(
        aim, tires, rpm_tolerance_rpm=RPM_TOLERANCE,
        min_match_fraction=MIN_MATCH_FRACTION)
    mapping = report.time_map
    channels, _units = read_aim_channel_schema(aim_csv)
    return {
        "report": asdict(report),
        "aim_curve": _compact(aim),
        "tire_curve": _compact(tires, mapping.to_aim_time),
        "fingerprints": (
            _file_fingerprint(aim_csv), _file_fingerprint(tire_csv)),
        "source_channels": channels[1:],
        "channel_mapping": suggest_channel_mapping(channels),
    }


class _RpmPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self._aim = []
        self._tires = []

    def set_curves(self, aim, tires):
        self._aim = list(aim or [])
        self._tires = list(tires or [])
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#202226"))
        points = self._aim + self._tires
        if not points:
            painter.setPen(QColor("#aeb4bf"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Analyze both CSVs to review RPM alignment")
            return
        left, top, right, bottom = 48, 16, 16, 32
        width = max(1, self.width() - left - right)
        height = max(1, self.height() - top - bottom)
        x_min = min(point[0] for point in points)
        x_max = max(point[0] for point in points)
        y_min = min(point[1] for point in points)
        y_max = max(point[1] for point in points)
        x_span = max(1.0, x_max - x_min)
        y_span = max(1.0, y_max - y_min)
        painter.setPen(QPen(QColor("#555b65"), 1))
        painter.drawRect(left, top, width, height)
        for curve, color in ((self._aim, "#55baff"),
                             (self._tires, "#55dd99")):
            if len(curve) < 2:
                continue
            mapped = [QPointF(
                left + width * (x - x_min) / x_span,
                top + height * (y_max - y) / y_span,
            ) for x, y in curve]
            painter.setPen(QPen(QColor(color), 2))
            for start, end in zip(mapped, mapped[1:]):
                painter.drawLine(start, end)
        painter.setPen(QColor("#aeb4bf"))
        painter.drawText(left, self.height() - 8,
                         f"{x_min:.1f}s")
        painter.drawText(self.width() - 72, self.height() - 8,
                         f"{x_max:.1f}s")


class TireDataMergeDialog(QDialog):
    """Select, verify, and package one AiM/TireLogger run for a session."""

    completed = Signal(object)

    def __init__(self, session_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tire Data Merge")
        self.resize(900, 720)
        self._analysis = None
        self._output_dir = None
        self._busy = False
        self._input_controls = []

        root = QVBoxLayout(self)
        title = QLabel("Merge TireLogger data into Race Studio")
        title.setObjectName("H2")
        root.addWidget(title)
        hint = QLabel(
            "Choose exports from the same run. xCAL aligns their RPM traces "
            "within ±500 RPM, then creates a Race Studio-compatible IBT.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        conversion_hint = QLabel(
            "Important: before converting the IBT, enable Race Studio "
            "Preferences â†’ Conversion â†’ Settings â†’ iRacing â†’ All channels. "
            "Without it, Race Studio drops Exhaust Temp and other extra sensors. "
            "xCAL carries EGT through its WaterTemp slot; set that imported "
            "channel's Race Studio alias/function to Exhaust Temp once. "
            "After analysis, confirm which source channels should feed xCAL's "
            "longitudinal acceleration, lateral acceleration, and yaw rate.")
        conversion_hint.setObjectName("Warning")
        conversion_hint.setWordWrap(True)
        root.addWidget(conversion_hint)

        form = QFormLayout()
        self._aim = self._file_row(form, "Race Studio CSV", "AiM CSV (*.csv)")
        self._tires = self._file_row(form, "TireLogger CSV", "CSV files (*.csv)")
        self._name = QLineEdit("Tire Data")
        self._input_controls.append(self._name)
        self._name.setPlaceholderText("Tire Data")
        form.addRow("Output name", self._name)
        tolerance = QLabel("±500 RPM (absolute)")
        tolerance.setObjectName("Muted")
        form.addRow("Sync tolerance", tolerance)
        self._motion_channels = {}
        for key, label in (
            ("longitudinal_acceleration", "Long acceleration source"),
            ("lateral_acceleration", "Lateral acceleration source"),
            ("yaw_rate", "Yaw-rate source"),
        ):
            selector = QComboBox()
            selector.setEnabled(False)
            selector.setToolTip(
                "Select the Race Studio channel xCAL should write into the "
                "canonical iRacing motion channel.")
            form.addRow(label, selector)
            self._motion_channels[key] = selector
            self._input_controls.append(selector)
        root.addLayout(form)

        for edit in (self._aim, self._tires):
            edit.textChanged.connect(self._invalidate)

        self._plot = _RpmPlot()
        root.addWidget(self._plot, 1)
        legend = QLabel("Blue: Race Studio RPM    Green: aligned TireLogger RPM")
        legend.setObjectName("Muted")
        root.addWidget(legend)
        self._metrics = QLabel("Select both files, then Analyze.")
        self._metrics.setWordWrap(True)
        root.addWidget(self._metrics)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        root.addWidget(self._progress)

        actions = QHBoxLayout()
        self._analyze = QPushButton("Analyze RPM alignment")
        self._analyze.setObjectName("Primary")
        self._analyze.clicked.connect(self._start_analysis)
        actions.addWidget(self._analyze)
        self._export = QPushButton("Create & Add to Session")
        self._export.setObjectName("Primary")
        self._export.setEnabled(False)
        self._export.clicked.connect(self._start_export)
        actions.addWidget(self._export)
        actions.addStretch(1)
        self._close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._close_buttons.rejected.connect(self.reject)
        actions.addWidget(self._close_buttons)
        root.addLayout(actions)

    def _file_row(self, form, label, file_filter):
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText("Select a CSV file…")
        row.addWidget(edit, 1)
        browse = QPushButton("Browse…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(lambda: self._browse(edit, file_filter))
        row.addWidget(browse)
        form.addRow(label, row)
        self._input_controls.extend((edit, browse))
        return edit

    def _browse(self, edit, file_filter):
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV", "", file_filter)
        if path:
            edit.setText(path)

    def _signature(self):
        return (self._aim.text().strip(), self._tires.text().strip())

    def _invalidate(self, *_args):
        self._analysis = None
        self._export.setEnabled(False)
        for selector in self._motion_channels.values():
            selector.clear()
            selector.setEnabled(False)

    def _populate_motion_channels(self, result):
        channels = result["source_channels"]
        selected = result["channel_mapping"]
        for key, selector in self._motion_channels.items():
            selector.clear()
            selector.addItem("Not mapped", "")
            for channel in channels:
                selector.addItem(channel, channel)
            suggested = selected.get(key)
            index = selector.findData(suggested) if suggested else -1
            if index >= 0:
                selector.setCurrentIndex(index)
            selector.setEnabled(True)

    def _selected_motion_channels(self):
        return {
            key: selector.currentData()
            for key, selector in self._motion_channels.items()
            if selector.currentData()
        }

    def _validate(self):
        aim, tires = map(Path, self._signature())
        if not aim.is_file() or not tires.is_file():
            raise ValueError("Select an existing Race Studio CSV and TireLogger CSV.")
        if aim.resolve() == tires.resolve():
            raise ValueError("Select two different CSV files from the same run.")
        name = self._name.text().strip()
        if not name or any(char in name for char in '<>:"/\\|?*'):
            raise ValueError("Enter an output name without filename punctuation.")
        return str(aim), str(tires), name

    def _set_busy(self, busy, message=""):
        self._busy = busy
        self._progress.setVisible(busy)
        self._analyze.setEnabled(not busy)
        self._export.setEnabled(not busy and self._analysis is not None)
        self._close_buttons.setEnabled(not busy)
        for control in self._input_controls:
            control.setEnabled(not busy)
        if message:
            self._metrics.setText(message)

    def _start_analysis(self):
        try:
            aim, tires, _name = self._validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Check merge files", str(exc))
            return
        signature = self._signature()
        self._set_busy(True, "Matching RPM traces…")

        def done(result):
            if signature != self._signature():
                self._set_busy(False, "Files changed; analyze them again.")
                return
            self._analysis = result
            self._plot.set_curves(result["aim_curve"], result["tire_curve"])
            self._populate_motion_channels(result)
            report = result["report"]
            mapping = report["time_map"]
            self._set_busy(False)
            self._metrics.setText(
                f"Agreement {report['match_fraction']:.1%} within ±500 RPM  ·  "
                f"Correlation {report['coarse_score']:.3f}  ·  "
                f"Ambiguity margin {report['ambiguity_margin']:.3f}  ·  "
                f"Offset {mapping['offset_s']:.3f}s  ·  "
                f"Drift {mapping['drift_ppm']:.1f} ppm  ·  "
                f"{len(report['window_anchors'])} matching windows\n"
                f"{len(result['source_channels'])} Race Studio data channels "
                "detected; xCAL will embed every numeric channel in the IBT.\n"
                "Review several distinctive RPM rises and drops before adding it.")
            self._export.setEnabled(True)

        self._analysis_thread = run_async(
            self, lambda: analyze_files(aim, tires), on_done=done,
            on_error=lambda message: self._analysis_failed(message))

    def _analysis_failed(self, message):
        self._analysis = None
        self._set_busy(False, f"Alignment failed: {message}")
        QMessageBox.critical(self, "Could not align RPM", message)

    def _start_export(self):
        try:
            aim, tires, name = self._validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Check merge files", str(exc))
            return
        if self._analysis is None:
            QMessageBox.information(
                self, "Analyze first", "Analyze and review this file pair first.")
            return
        try:
            current_fingerprints = (
                _file_fingerprint(aim), _file_fingerprint(tires))
        except OSError as exc:
            QMessageBox.critical(self, "Could not read files", str(exc))
            return
        if current_fingerprints != tuple(self._analysis["fingerprints"]):
            self._invalidate()
            QMessageBox.warning(
                self, "Files changed", "One of the CSVs changed. Analyze again.")
            return
        output_dir = Path(tempfile.mkdtemp(prefix="xcal-tire-merge-"))
        channel_mapping = self._selected_motion_channels()
        self._output_dir = output_dir
        self._set_busy(True, "Building merged CSV, IBT, and sync manifest…")

        def work():
            report = build_race_studio_package(
                aim, tires, output_dir, rpm_sync=True,
                rpm_tolerance_rpm=RPM_TOLERANCE,
                min_rpm_match_fraction=MIN_MATCH_FRACTION,
                channel_mapping=channel_mapping, name=name)
            return {
                "files": [aim, tires, str(report.merged_csv),
                          str(report.ibt), str(report.manifest)],
                "temporary_output": str(output_dir),
            }

        def done(result):
            self._set_busy(False, "Package complete; adding it to the session…")
            self.completed.emit(result)
            self.accept()

        self._export_thread = run_async(
            self, work, on_done=done,
            on_error=lambda message: self._export_failed(message))

    def _export_failed(self, message):
        if self._output_dir:
            shutil.rmtree(self._output_dir, ignore_errors=True)
            self._output_dir = None
        self._set_busy(False, f"Export failed: {message}")
        QMessageBox.critical(self, "Tire data export failed", message)

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
