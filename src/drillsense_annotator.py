from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTableView,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "DrillSense Annotator"
APP_SUBTITLE = "Weakly Supervised Drilling-Acoustic Annotation Workstation"

SOUND_TYPES = [
    "左臂钻孔声",
    "右臂钻孔声",
    "钻孔声（无法分辨左右）",
    "双臂均未钻进（确认）",
    "左臂机械臂调整",
    "右臂机械臂调整",
    "状态转换边界",
    "非作业干扰",
]
TRANSITION_TYPES = ["S→L", "S→R", "L→S", "R→S", "L→LR", "R→LR", "LR→L", "LR→R"]
CONFIDENCES = ["高", "中", "低"]
REFERENCE_QUALITIES = ["未评估", "可作参考：单臂清晰", "可作参考：轻微混合", "不可作参考", "不适用"]
SOURCE_DEVICES = ["左臂录音设备", "右臂录音设备"]

UI_TEXT = {
    "左臂钻孔声": "Left-boom drilling",
    "右臂钻孔声": "Right-boom drilling",
    "钻孔声（无法分辨左右）": "Drilling, source uncertain",
    "双臂均未钻进（确认）": "No drilling, confirmed",
    "左臂机械臂调整": "Left-boom repositioning",
    "右臂机械臂调整": "Right-boom repositioning",
    "状态转换边界": "State-transition boundary",
    "非作业干扰": "Non-operational interference",
    "高": "High",
    "中": "Medium",
    "低": "Low",
    "未评估": "Not assessed",
    "可作参考：单臂清晰": "Reference: clean single boom",
    "可作参考：轻微混合": "Reference: mildly overlapped",
    "不可作参考": "Not suitable as reference",
    "不适用": "Not applicable",
    "左臂录音设备": "Left boom",
    "右臂录音设备": "Right boom",
    "全部录音": "All recordings",
    "全部类型": "All classes",
    "全部置信度": "All confidence levels",
}


def ui_text(value: str) -> str:
    return UI_TEXT.get(value, value)


NOTE_REPLACEMENTS = [
    ("\u6807\u6ce8\u7c7b\u522b", "Label class"),
    ("\u6807\u51c6\u7c7b\u522b", "Label class"),
    ("\u8bad\u7ec3\u89d2\u8272", "Training role"),
    ("\u6620\u5c04\u7f16\u53f7", "Mapping ID"),
    ("\u8f6c\u6362\u8fb9\u754c", "state boundary"),
    ("\u53f3\u81c2\u94bb\u8fdb", "right-boom drilling"),
    ("\u5de6\u81c2\u94bb\u8fdb", "left-boom drilling"),
    ("\u5e72\u51c0\u53c2\u8003\u6bb5", "clean reference segment"),
    ("\u5b8c\u6574\u94bb\u5b54", "complete drilling event"),
    ("\u56f0\u96be\u65e0\u94bb\u8fdb", "challenging no-drilling segment"),
]


def ui_note(value: str) -> str:
    text = value
    for source, target in NOTE_REPLACEMENTS:
        text = text.replace(source, target)
    return text.replace("\uff1a", ": ").replace("\uff1b", "; ")


def add_value_items(combo: QComboBox, values: list[str]) -> None:
    for value in values:
        combo.addItem(ui_text(value), value)


def combo_value(combo: QComboBox) -> str:
    value = combo.currentData()
    return str(value if value is not None else combo.currentText())


def set_combo_value(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index < 0:
        index = combo.findText(value)
    if index >= 0:
        combo.setCurrentIndex(index)

FILE_CORRECTIONS = {
    "2026-07-10-22-49-03": -13.263,
    "2026-07-10-23-49-08": -14.420,
}

TYPE_COLORS = {
    "左臂钻孔声": "#1D9BF0",
    "右臂钻孔声": "#8B5CF6",
    "钻孔声（无法分辨左右）": "#64748B",
    "双臂均未钻进（确认）": "#10B981",
    "左臂机械臂调整": "#F59E0B",
    "右臂机械臂调整": "#F97316",
    "状态转换边界": "#EF4444",
    "非作业干扰": "#94A3B8",
}


def app_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[1] / "声纹标定程序_运行数据"
    target = base / "data"
    target.mkdir(parents=True, exist_ok=True)
    return target


AUTOSAVE_PATH = app_data_dir() / "自动保存.json"


def format_elapsed(seconds: float | int | None) -> str:
    value = max(0.0, float(seconds or 0.0))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    secs = value % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def parse_elapsed(text: str) -> float | None:
    raw = str(text or "").strip().replace("：", ":")
    if not raw:
        return None
    try:
        parts = raw.split(":")
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def parse_filename_start(filename: str) -> datetime | None:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})", filename)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()))
    except ValueError:
        return None


def parse_datetime_text(text: str) -> datetime | None:
    raw = str(text or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def recommended_correction(filename: str, source: str = "") -> float:
    for token, correction in FILE_CORRECTIONS.items():
        if token in filename:
            return correction
    if "右臂" in filename or source == "右臂录音设备":
        return -13.263
    return 0.0


def transition_from_text(text: str) -> str:
    match = re.search(r"(LR|L|R|S)\s*(?:→|->|＞|>)\s*(LR|L|R|S)", str(text or "").upper())
    if not match:
        return ""
    value = f"{match.group(1)}→{match.group(2)}"
    return value if value in TRANSITION_TYPES else ""


def category_for_type(sound_type: str) -> str:
    return {
        "左臂钻孔声": "左臂钻进",
        "右臂钻孔声": "右臂钻进",
        "钻孔声（无法分辨左右）": "无法辨臂",
        "双臂均未钻进（确认）": "S 无钻进",
        "左臂机械臂调整": "机械臂调整",
        "右臂机械臂调整": "机械臂调整",
        "状态转换边界": "转换边界",
        "非作业干扰": "排除数据",
    }.get(sound_type, sound_type or "未分类")


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [format_elapsed(value) for value in values]


class AudioAnalysisWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, audio_path: str):
        super().__init__()
        self.audio_path = audio_path

    def run(self):
        try:
            with sf.SoundFile(self.audio_path) as audio:
                sample_rate = int(audio.samplerate)
                frames = int(len(audio))
                channels = int(audio.channels)
                duration = frames / sample_rate if sample_rate else 0.0
                target_columns = int(np.clip(math.ceil(duration * 2.0), 1200, 7200))
                block_frames = max(2048, math.ceil(frames / max(1, target_columns)))
                n_fft = 2048
                freq_bins = 256
                window = np.hanning(n_fft).astype(np.float32)
                rfft_freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
                selected_bins = np.linspace(0, len(rfft_freqs) - 1, freq_bins).astype(int)
                display_freqs = rfft_freqs[selected_bins]
                low_mask = (rfft_freqs >= 200) & (rfft_freqs < 2000)
                high_mask = (rfft_freqs >= 2000) & (rfft_freqs <= min(12000, sample_rate / 2))

                times, energy_db, wave_min, wave_max = [], [], [], []
                clip_ratio, band_ratio, spectral_flux = [], [], []
                spectra = []
                previous_norm = None
                processed = 0
                last_percent = -1

                while processed < frames:
                    block = audio.read(block_frames, dtype="float32", always_2d=True)
                    if block.size == 0:
                        break
                    mono = block.mean(axis=1)
                    count = len(mono)
                    center = (processed + count / 2) / sample_rate
                    rms = float(np.sqrt(np.mean(mono * mono) + 1e-12))
                    energy_db.append(max(-100.0, 20.0 * math.log10(rms + 1e-12)))
                    wave_min.append(float(np.min(mono)))
                    wave_max.append(float(np.max(mono)))
                    clip_ratio.append(float(np.mean(np.abs(mono) >= 0.99)))
                    times.append(center)

                    if count >= n_fft:
                        start = (count - n_fft) // 2
                        segment = mono[start : start + n_fft]
                    else:
                        segment = np.pad(mono, (0, n_fft - count))
                    power = np.abs(np.fft.rfft(segment * window)) ** 2 + 1e-12
                    spectra.append((10.0 * np.log10(power[selected_bins])).astype(np.float32))
                    low = float(np.mean(power[low_mask])) if np.any(low_mask) else 1e-12
                    high = float(np.mean(power[high_mask])) if np.any(high_mask) else 1e-12
                    band_ratio.append(10.0 * math.log10((high + 1e-12) / (low + 1e-12)))
                    norm = power / (np.sum(power) + 1e-12)
                    if previous_norm is None:
                        spectral_flux.append(0.0)
                    else:
                        spectral_flux.append(float(np.sqrt(np.sum((norm - previous_norm) ** 2))))
                    previous_norm = norm

                    processed += count
                    percent = min(100, int(processed * 100 / max(1, frames)))
                    if percent != last_percent:
                        self.progress.emit(percent, f"正在分析整段录音 {percent}%")
                        last_percent = percent

                spec = np.asarray(spectra, dtype=np.float32).T
                if spec.size:
                    spec -= np.nanmax(spec)
                    spec = np.clip(spec, -80.0, 0.0)
                flux = np.asarray(spectral_flux, dtype=np.float32)
                if flux.size and float(np.percentile(flux, 99)) > 0:
                    flux = np.clip(flux / float(np.percentile(flux, 99)), 0, 1)
                band = np.asarray(band_ratio, dtype=np.float32)
                if band.size:
                    lo, hi = np.percentile(band, [5, 95])
                    band_norm = np.clip((band - lo) / max(1e-6, hi - lo), 0, 1)
                else:
                    band_norm = band
                clipping = np.asarray(clip_ratio, dtype=np.float32)
                clip_norm = np.clip(clipping / max(0.005, float(np.percentile(clipping, 99)) if clipping.size else 0.005), 0, 1)

                self.finished.emit(
                    {
                        "path": self.audio_path,
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "duration": duration,
                        "times": np.asarray(times, dtype=np.float64),
                        "energy_db": np.asarray(energy_db, dtype=np.float32),
                        "wave_min": np.asarray(wave_min, dtype=np.float32),
                        "wave_max": np.asarray(wave_max, dtype=np.float32),
                        "spectrogram": spec,
                        "frequencies": display_freqs.astype(np.float32),
                        "spectral_flux": flux,
                        "band_ratio": band,
                        "band_ratio_norm": band_norm.astype(np.float32),
                        "clip_ratio": clipping,
                        "clip_ratio_norm": clip_norm.astype(np.float32),
                    }
                )
        except Exception:
            self.failed.emit(traceback.format_exc())


class AnnotationTableModel(QAbstractTableModel):
    columns = [
        ("#", "index"),
        ("Recording", "filename"),
        ("Start", "start_elapsed"),
        ("End", "end_elapsed"),
        ("Duration", "duration"),
        ("Acoustic class", "type"),
        ("Transition", "transition_type"),
        ("Confidence", "confidence"),
        ("Event ID", "event_id"),
        ("Reference quality", "reference_quality"),
        ("Notes", "note"),
    ]

    def __init__(self, annotations: list[dict]):
        super().__init__()
        self.annotations = annotations

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.annotations)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.columns[section][0]
        return section + 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.annotations)):
            return None
        item = self.annotations[index.row()]
        key = self.columns[index.column()][1]
        if role == Qt.DisplayRole:
            if key == "index":
                return str(index.row() + 1)
            if key == "start_elapsed":
                return format_elapsed(item.get(key, 0))
            if key == "end_elapsed":
                return format_elapsed(item.get(key, 0))
            if key == "duration":
                return format_elapsed(float(item.get("end_elapsed", 0)) - float(item.get("start_elapsed", 0)))
            if key == "filename":
                filename = Path(str(item.get(key, ""))).name
                return filename.replace("左臂", "Left").replace("右臂", "Right")
            if key == "note":
                return ui_note(str(item.get(key, "")))
            if key in {"type", "confidence", "reference_quality", "source"}:
                return ui_text(str(item.get(key, "") or "—"))
            return str(item.get(key, "") or "—")
        if role == Qt.UserRole:
            if key in {"start_elapsed", "end_elapsed"}:
                return float(item.get(key, 0))
            if key == "duration":
                return float(item.get("end_elapsed", 0)) - float(item.get("start_elapsed", 0))
            return item.get(key, "")
        if role == Qt.BackgroundRole and index.column() == 5:
            color = QColor(TYPE_COLORS.get(item.get("type", ""), "#CBD5E1"))
            color.setAlpha(38)
            return color
        if role == Qt.ForegroundRole and index.column() == 7 and item.get("confidence") == "低":
            return QColor("#DC2626")
        if role == Qt.TextAlignmentRole and index.column() in {0, 2, 3, 4, 6, 7}:
            return int(Qt.AlignCenter)
        return None

    def refresh(self):
        self.beginResetModel()
        self.endResetModel()


class AnnotationFilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.filename_filter = "全部录音"
        self.type_filter = "全部类型"
        self.confidence_filter = "全部置信度"
        self.search_text = ""
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        item = model.annotations[source_row]
        if self.filename_filter != "全部录音" and item.get("filename") != self.filename_filter:
            return False
        if self.type_filter != "全部类型" and item.get("type") != self.type_filter:
            return False
        if self.confidence_filter != "全部置信度" and item.get("confidence") != self.confidence_filter:
            return False
        if self.search_text:
            haystack = " ".join(str(item.get(key, "")) for key in ("filename", "type", "transition_type", "event_id", "reference_quality", "note"))
            if self.search_text.lower() not in haystack.lower():
                return False
        return True


class Card(QFrame):
    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(13, 11, 13, 11)
        self.layout_box.setSpacing(8)
        if title:
            label = QLabel(title)
            label.setObjectName("CardTitle")
            self.layout_box.addWidget(label)


class AnalysisPanel(QWidget):
    seekRequested = Signal(float)

    def __init__(self):
        super().__init__()
        self.visible_annotation_type: str | None = None
        self.legend_buttons: dict[str, QToolButton] = {}
        self._annotations: list[dict] = []
        self._annotation_filename = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Acoustic Analysis")
        title.setObjectName("SectionTitle")
        subtitle = QLabel("Full-record offline analysis · original recording time is retained after zooming")
        subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        toolbar.addLayout(title_box)
        toolbar.addStretch()
        self.feature_readout = QLabel("Spectral flux —  ·  High/low ratio —  ·  Clipping —")
        self.feature_readout.setObjectName("MetricBadge")
        toolbar.addWidget(self.feature_readout)
        layout.addLayout(toolbar)

        legend = QGridLayout()
        legend.setHorizontalSpacing(16)
        legend.setVerticalSpacing(4)
        legend_title = QLabel("Annotation classes")
        legend_title.setObjectName("Muted")
        legend.addWidget(legend_title, 0, 0, 2, 1)
        legend_items = [
            ("左臂钻孔声", "Left drilling", TYPE_COLORS["左臂钻孔声"]),
            ("右臂钻孔声", "Right drilling", TYPE_COLORS["右臂钻孔声"]),
            ("钻孔声（无法分辨左右）", "Source uncertain", TYPE_COLORS["钻孔声（无法分辨左右）"]),
            ("双臂均未钻进（确认）", "No drilling", TYPE_COLORS["双臂均未钻进（确认）"]),
            ("左臂机械臂调整", "Left repositioning", TYPE_COLORS["左臂机械臂调整"]),
            ("右臂机械臂调整", "Right repositioning", TYPE_COLORS["右臂机械臂调整"]),
            ("状态转换边界", "State boundary", TYPE_COLORS["状态转换边界"]),
            ("非作业干扰", "Interference", TYPE_COLORS["非作业干扰"]),
        ]
        for index, (sound_type, label, color) in enumerate(legend_items):
            row = index // 4
            column = index % 4 + 1
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(f"Show only {label}; click again to restore all classes")
            button.setStyleSheet(
                f"""
                QToolButton {{
                    min-height: 27px;
                    padding: 0 9px;
                    color: #334155;
                    background: #FFFFFF;
                    border: 1px solid #D7E2EA;
                    border-left: 6px solid {color};
                    border-radius: 3px;
                    font-weight: 600;
                }}
                QToolButton:hover {{
                    background: #F1F7FA;
                    border-color: {color};
                }}
                QToolButton:checked {{
                    color: #FFFFFF;
                    background: {color};
                    border-color: {color};
                    font-weight: 750;
                }}
                """
            )
            button.clicked.connect(
                lambda checked, selected_type=sound_type: self._legend_filter_changed(
                    selected_type, checked
                )
            )
            self.legend_buttons[sound_type] = button
            legend.addWidget(button, row, column)
        legend.setColumnStretch(5, 1)
        layout.addLayout(legend)
        legend_hint = QLabel("Click a color chip to isolate one annotation class; click it again to restore all classes.")
        legend_hint.setObjectName("Muted")
        layout.addWidget(legend_hint)

        self.plot_splitter = QSplitter(Qt.Vertical)
        self.plot_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.plot_splitter, 1)

        axis_bottom_1 = TimeAxis(orientation="bottom")
        axis_bottom_2 = TimeAxis(orientation="bottom")
        axis_bottom_3 = TimeAxis(orientation="bottom")
        self.spec_plot = pg.PlotWidget(axisItems={"bottom": axis_bottom_1})
        self.energy_plot = pg.PlotWidget(axisItems={"bottom": axis_bottom_2})
        self.feature_plot = pg.PlotWidget(axisItems={"bottom": axis_bottom_3})
        for plot in (self.spec_plot, self.energy_plot, self.feature_plot):
            plot.setBackground("#08111F")
            plot.showGrid(x=True, y=True, alpha=0.12)
            plot.getAxis("left").setTextPen("#94A3B8")
            plot.getAxis("bottom").setTextPen("#94A3B8")
            plot.getAxis("left").setPen("#334155")
            plot.getAxis("bottom").setPen("#334155")
            plot.setMouseEnabled(x=True, y=False)
            plot.setMenuEnabled(False)
            plot.getAxis("left").setWidth(58)
            plot.getAxis("bottom").setHeight(42)

        self.spec_plot.setLabel("left", "Frequency", units="Hz")
        self.spec_plot.setLabel("bottom", "Recording time")
        self.energy_plot.setLabel("left", "Energy", units="dBFS")
        self.energy_plot.setLabel("bottom", "Recording time")
        self.feature_plot.setLabel("left", "Change features", units="normalized")
        self.feature_plot.setLabel("bottom", "Recording time")
        self.energy_plot.setYRange(-40, 0, padding=0)
        self.feature_plot.setYRange(0, 1, padding=0.04)
        self.spec_plot.setYRange(0, 24000, padding=0)
        self.spec_plot.setXRange(0, 60, padding=0)
        self.energy_plot.setXLink(self.spec_plot)
        self.feature_plot.setXLink(self.spec_plot)

        self.image_item = pg.ImageItem()
        self.spec_plot.addItem(self.image_item)
        try:
            self.image_item.setLookupTable(pg.colormap.get("inferno").getLookupTable(0, 1, 256))
        except Exception:
            pass
        self.energy_curve = self.energy_plot.plot([], [], pen=pg.mkPen("#38BDF8", width=2))
        self.wave_max_curve = self.energy_plot.plot([], [], pen=pg.mkPen("#64748B", width=1))
        self.flux_curve = self.feature_plot.plot([], [], pen=pg.mkPen("#FBBF24", width=2), name="Spectral flux")
        self.band_curve = self.feature_plot.plot([], [], pen=pg.mkPen("#22D3EE", width=2), name="High/low ratio")
        self.clip_curve = self.feature_plot.plot([], [], pen=pg.mkPen("#F472B6", width=1.5), name="Clipping ratio")
        self.feature_plot.addLegend(offset=(12, 8), brush=pg.mkBrush(8, 17, 31, 180), labelTextColor="#CBD5E1")

        self.cursor_lines = []
        for plot in (self.spec_plot, self.energy_plot, self.feature_plot):
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#FCD34D", width=1.5))
            plot.addItem(line)
            self.cursor_lines.append(line)
            plot.scene().sigMouseClicked.connect(lambda event, p=plot: self._plot_clicked(event, p))

        self.draft_regions = []
        for plot in (self.spec_plot, self.energy_plot, self.feature_plot):
            region = pg.LinearRegionItem(values=(0, 0), movable=False, brush=pg.mkBrush(251, 191, 36, 32), pen=pg.mkPen("#F59E0B", width=1))
            region.hide()
            plot.addItem(region)
            self.draft_regions.append(region)

        self.annotation_regions = []
        self.plot_splitter.addWidget(self.spec_plot)
        self.plot_splitter.addWidget(self.energy_plot)
        self.plot_splitter.addWidget(self.feature_plot)
        self.plot_splitter.setSizes([270, 155, 140])
        self.analysis = None

    def _legend_filter_changed(self, sound_type: str, checked: bool):
        if checked:
            self.visible_annotation_type = sound_type
            for other_type, button in self.legend_buttons.items():
                if other_type == sound_type:
                    continue
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
        else:
            self.visible_annotation_type = None
        self.set_annotations(self._annotations, self._annotation_filename)

    def _plot_clicked(self, event, plot):
        if event.button() != Qt.LeftButton:
            return
        point = plot.getViewBox().mapSceneToView(event.scenePos())
        self.seekRequested.emit(max(0.0, float(point.x())))

    def set_analysis(self, analysis: dict):
        self.analysis = analysis
        times = analysis["times"]
        duration = float(analysis["duration"])
        nyquist = float(analysis["sample_rate"]) / 2.0
        spec = analysis["spectrogram"]
        if spec.size:
            self.image_item.setImage(spec.T, autoLevels=False, levels=(-80, 0))
            self.image_item.setRect(QRectF(0, 0, duration, nyquist))
        self.spec_plot.setYRange(0, nyquist, padding=0)
        self.spec_plot.setXRange(0, max(1, duration), padding=0)
        energy = np.clip(analysis["energy_db"], -40, 0)
        self.energy_curve.setData(times, energy)
        peak_db = 20 * np.log10(np.maximum(1e-6, np.abs(analysis["wave_max"])))
        self.wave_max_curve.setData(times, np.clip(peak_db, -40, 0))
        self.flux_curve.setData(times, analysis["spectral_flux"])
        self.band_curve.setData(times, analysis["band_ratio_norm"])
        self.clip_curve.setData(times, analysis["clip_ratio_norm"])

    def set_cursor(self, seconds: float):
        for line in self.cursor_lines:
            line.setValue(seconds)
        if self.analysis and len(self.analysis["times"]):
            idx = int(np.clip(np.searchsorted(self.analysis["times"], seconds), 0, len(self.analysis["times"]) - 1))
            flux = float(self.analysis["spectral_flux"][idx])
            band = float(self.analysis["band_ratio"][idx])
            clip = float(self.analysis["clip_ratio"][idx]) * 100
            self.feature_readout.setText(f"Spectral flux {flux:.2f}  ·  High/low ratio {band:+.1f} dB  ·  Clipping {clip:.1f}%")

    def set_draft(self, start: float | None, end: float | None):
        if start is None and end is None:
            for region in self.draft_regions:
                region.hide()
            return
        a = float(start if start is not None else end)
        b = float(end if end is not None else start)
        if b < a:
            a, b = b, a
        if abs(b - a) < 0.05:
            b = a + 0.05
        for region in self.draft_regions:
            region.setRegion((a, b))
            region.show()

    def set_annotations(self, annotations: list[dict], filename: str):
        self._annotations = annotations
        self._annotation_filename = filename
        for region in self.annotation_regions:
            self.energy_plot.removeItem(region)
        self.annotation_regions.clear()
        if not filename:
            return
        for item in annotations:
            if item.get("filename") != filename:
                continue
            if self.visible_annotation_type and item.get("type") != self.visible_annotation_type:
                continue
            color = QColor(TYPE_COLORS.get(item.get("type", ""), "#94A3B8"))
            region = pg.LinearRegionItem(
                values=(float(item.get("start_elapsed", 0)), float(item.get("end_elapsed", 0))),
                movable=False,
                brush=pg.mkBrush(color.red(), color.green(), color.blue(), 22),
                pen=pg.mkPen(color.name(), width=1),
            )
            region.setZValue(-2)
            self.energy_plot.addItem(region)
            self.annotation_regions.append(region)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · {APP_SUBTITLE}")
        self.resize(1660, 980)
        self.setMinimumSize(1280, 760)

        self.annotations: list[dict] = []
        self.current_audio_path = ""
        self.current_filename = ""
        self.audio_duration = 0.0
        self.analysis_data = None
        self.analysis_thread = None
        self.analysis_worker = None
        self.draft_start: float | None = None
        self.draft_end: float | None = None
        self.editing_index: int | None = None
        self.slider_dragging = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)

        self._build_ui()
        self._connect_signals()
        self._install_shortcuts()
        self._apply_styles()
        self._restore_autosave()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(lambda: self._save_autosave("定时自动保存"))
        self.autosave_timer.start(60_000)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = self._build_sidebar()
        shell.addWidget(sidebar)
        content = QWidget()
        content.setObjectName("ContentArea")
        shell.addWidget(content, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 20, 28, 22)
        root.setSpacing(14)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        title_line = QHBoxLayout()
        title = QLabel("Workspace")
        title.setObjectName("AppTitle")
        title_line.addWidget(title)
        title_line.addStretch()
        subtitle = QLabel("Review recordings and build interval annotations")
        subtitle.setObjectName("AppSubtitle")
        brand.addLayout(title_line)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()

        for text, callback, primary in (
            ("Open audio", self.open_audio_dialog, True),
            ("Import annotations", self.import_csv_dialog, False),
            ("Export annotations", self.export_csv_dialog, False),
        ):
            button = QPushButton(text)
            button.setObjectName("PrimaryButton" if primary else "ToolbarButton")
            button.clicked.connect(callback)
            header.addWidget(button)

        project_button = QToolButton()
        project_button.setText("Project")
        project_button.setObjectName("ToolbarButton")
        project_button.setPopupMode(QToolButton.InstantPopup)
        project_menu = QMenu(project_button)
        for text, callback in (
            ("New annotation set", self.new_blank_annotation_set),
            ("Open project", self.load_project_dialog),
            ("Save project", self.save_project_dialog),
        ):
            action = project_menu.addAction(text)
            action.triggered.connect(callback)
        project_button.setMenu(project_menu)
        header.addWidget(project_button)
        root.addLayout(header)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self.summary_annotations = self._summary_card("Annotations", "0", "saved intervals")
        self.summary_confidence = self._summary_card("High confidence", "0", "quality checked")
        self.summary_recording = self._summary_card("Current recording", "—", "open a WAV file")
        summary_row.addWidget(self.summary_annotations)
        summary_row.addWidget(self.summary_confidence)
        summary_row.addWidget(self.summary_recording, 2)
        root.addLayout(summary_row)

        self.main_vertical = QSplitter(Qt.Vertical)
        self.main_vertical.setChildrenCollapsible(False)
        root.addWidget(self.main_vertical, 1)

        top = QSplitter(Qt.Horizontal)
        top.setChildrenCollapsible(False)
        self.main_vertical.addWidget(top)

        left_card = self._build_left_panel()
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setWidget(left_card)
        self.analysis_panel = AnalysisPanel()
        analysis_card = Card()
        analysis_card.layout_box.addWidget(self.analysis_panel)
        right_panel = self._build_editor_panel()
        top.addWidget(left_scroll)
        top.addWidget(analysis_card)
        top.addWidget(right_panel)
        top.setSizes([330, 1000, 390])

        history_card = self._build_history_panel()
        self.main_vertical.addWidget(history_card)
        self.main_vertical.setSizes([600, 330])

        self.status_label = QLabel("Ready. Open a WAV recording or import an existing annotation CSV.")
        self.status_label.setObjectName("StatusBar")
        root.addWidget(self.status_label)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(226)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 24, 18, 22)
        layout.setSpacing(8)

        brand = QLabel("●  DrillSense")
        brand.setObjectName("Brand")
        layout.addWidget(brand)
        brand_hint = QLabel("Acoustic annotation")
        brand_hint.setObjectName("BrandHint")
        layout.addWidget(brand_hint)

        profile = QFrame()
        profile.setObjectName("ProfileCard")
        profile_layout = QVBoxLayout(profile)
        profile_layout.setContentsMargins(12, 12, 12, 12)
        profile_layout.setSpacing(2)
        profile_name = QLabel("Field review")
        profile_name.setObjectName("ProfileName")
        profile_role = QLabel("Tunnel drilling project")
        profile_role.setObjectName("ProfileRole")
        profile_layout.addWidget(profile_name)
        profile_layout.addWidget(profile_role)
        layout.addSpacing(24)
        layout.addWidget(profile)
        layout.addSpacing(18)

        nav_title = QLabel("WORKSPACE")
        nav_title.setObjectName("NavTitle")
        layout.addWidget(nav_title)
        self.nav_buttons = {}
        for key, text, active in (("desk", "◉  Annotation desk", True), ("recordings", "◷  Recordings", False), ("sets", "▤  Annotation sets", False), ("quality", "◌  Quality review", False)):
            button = QPushButton(text)
            button.setObjectName("NavButtonActive" if active else "NavButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, target=key: self._navigate_to(target))
            self.nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        upload = QPushButton("＋  New recording")
        upload.setObjectName("SidePrimaryButton")
        upload.setMinimumHeight(46)
        upload.clicked.connect(self.open_audio_dialog)
        layout.addWidget(upload)
        footer = QLabel("WAV  ·  CSV  ·  Auto-save")
        footer.setObjectName("SidebarFooter")
        layout.addWidget(footer)
        return sidebar

    def _summary_card(self, title: str, value: str, hint: str) -> QFrame:
        card = QFrame()
        card.setObjectName("SummaryCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("SummaryTitle")
        value_label = QLabel(value)
        value_label.setObjectName("SummaryValue")
        hint_label = QLabel(hint)
        hint_label.setObjectName("SummaryHint")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(hint_label)
        card.value_label = value_label
        card.hint_label = hint_label
        return card

    def _navigate_to(self, target: str):
        """Use the sidebar as lightweight workspace navigation within the single-page annotator."""
        labels = {
            "desk": "Annotation desk",
            "recordings": "Recordings",
            "sets": "Annotation sets",
            "quality": "Quality review",
        }
        for key, button in getattr(self, "nav_buttons", {}).items():
            button.setObjectName("NavButtonActive" if key == target else "NavButton")
            button.style().unpolish(button)
            button.style().polish(button)
        if target == "recordings":
            self.file_name_label.setFocus()
            self._set_status("Recordings: open a WAV file from the top toolbar or New recording.")
        elif target == "sets":
            self.table.setFocus()
            self._set_status("Annotation sets: browse, filter, import, or export records in the table below.")
        elif target == "quality":
            set_combo_value(self.confidence_filter, "高")
            self.table.setFocus()
            self._set_status("Quality review: showing high-confidence records for review.")
        else:
            self.analysis_panel.setFocus()
            self._set_status("Annotation desk: analyze the recording and set interval boundaries.")

    def _build_left_panel(self) -> QWidget:
        card = Card("Recording and Playback")
        card.setMinimumWidth(300)
        card.layout_box.setContentsMargins(14, 13, 14, 13)
        card.layout_box.setSpacing(6)
        file_label = QLabel("Current recording")
        file_label.setObjectName("FieldLabel")
        self.file_name_label = QLabel("No recording loaded")
        self.file_name_label.setObjectName("FileName")
        self.file_name_label.setWordWrap(False)
        card.layout_box.addWidget(file_label)
        card.layout_box.addWidget(self.file_name_label)

        self.file_meta_label = QLabel("Sample rate, channels, and duration are shown after loading")
        self.file_meta_label.setObjectName("Muted")
        self.file_meta_label.setWordWrap(False)
        card.layout_box.addWidget(self.file_meta_label)

        self.analysis_progress = QProgressBar()
        self.analysis_progress.setRange(0, 100)
        self.analysis_progress.setValue(0)
        self.analysis_progress.setTextVisible(True)
        self.analysis_progress.hide()
        card.layout_box.addWidget(self.analysis_progress)

        card.layout_box.addWidget(self._separator())
        source_label = QLabel("Recording source")
        source_label.setObjectName("FieldLabel")
        self.source_combo = QComboBox()
        add_value_items(self.source_combo, SOURCE_DEVICES)
        correction_label = QLabel("Beijing-time correction")
        correction_label.setObjectName("FieldLabel")
        self.correction_spin = QDoubleSpinBox()
        self.correction_spin.setRange(-120, 120)
        self.correction_spin.setDecimals(3)
        self.correction_spin.setSuffix(" s")
        self.correction_spin.setSingleStep(0.001)
        source_grid = QGridLayout()
        source_grid.setContentsMargins(0, 0, 0, 0)
        source_grid.setHorizontalSpacing(8)
        source_grid.setVerticalSpacing(4)
        source_grid.addWidget(source_label, 0, 0)
        source_grid.addWidget(correction_label, 0, 1)
        source_grid.addWidget(self.source_combo, 1, 0)
        source_grid.addWidget(self.correction_spin, 1, 1)
        source_grid.setColumnStretch(0, 1)
        source_grid.setColumnStretch(1, 1)
        card.layout_box.addLayout(source_grid)

        self.alignment_hint = QLabel("Auto correction from filename · editable")
        self.alignment_hint.setObjectName("Hint")
        self.alignment_hint.setWordWrap(False)
        card.layout_box.addWidget(self.alignment_hint)

        card.layout_box.addWidget(self._separator())
        time_title = QLabel("Cursor time")
        time_title.setObjectName("FieldLabel")
        self.beijing_time_label = QLabel("—")
        self.beijing_time_label.setObjectName("HeroTime")
        self.beijing_time_label.setWordWrap(False)
        self.elapsed_time_label = QLabel("Recording  00:00:00.000")
        self.elapsed_time_label.setObjectName("Muted")
        card.layout_box.addWidget(time_title)
        card.layout_box.addWidget(self.beijing_time_label)
        card.layout_box.addWidget(self.elapsed_time_label)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 10000)
        card.layout_box.addWidget(self.position_slider)

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶  Play")
        self.play_button.setObjectName("PrimaryButton")
        self.speed_combo = QComboBox()
        for speed in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0):
            self.speed_combo.addItem(f"{speed:g}×", speed)
        self.speed_combo.setCurrentIndex(2)
        controls.addWidget(self.play_button, 1)
        controls.addWidget(self.speed_combo)
        card.layout_box.addLayout(controls)

        seek_grid = QHBoxLayout()
        seek_grid.setSpacing(6)
        for i, seconds in enumerate((-10, -1, 1, 10)):
            button = QToolButton()
            button.setText(f"{seconds:+d} s")
            button.clicked.connect(lambda checked=False, delta=seconds: self.seek_relative(delta))
            seek_grid.addWidget(button, 1)
        card.layout_box.addLayout(seek_grid)
        card.layout_box.addStretch()

        shortcut = QLabel("Space Play  ·  A Start  ·  D End  ·  Ctrl+S Save")
        shortcut.setObjectName("Hint")
        shortcut.setWordWrap(False)
        card.layout_box.addWidget(shortcut)
        return card

    def _build_editor_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        scroll.setWidget(container)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        card = Card("Interval Annotation Editor")
        outer.addWidget(card)

        self.edit_mode_badge = QLabel("New annotation")
        self.edit_mode_badge.setObjectName("SuccessBadge")
        card.layout_box.addWidget(self.edit_mode_badge, 0, Qt.AlignLeft)

        stamps = QHBoxLayout()
        self.start_stamp = self._stamp("Draft start", "Not set")
        self.end_stamp = self._stamp("Draft end", "Not set")
        stamps.addWidget(self.start_stamp[0])
        stamps.addWidget(self.end_stamp[0])
        card.layout_box.addLayout(stamps)

        set_buttons = QHBoxLayout()
        self.set_start_button = QPushButton("Set start  A")
        self.set_end_button = QPushButton("Set end  D")
        self.set_start_button.setObjectName("ToolbarButton")
        self.set_end_button.setObjectName("ToolbarButton")
        set_buttons.addWidget(self.set_start_button)
        set_buttons.addWidget(self.set_end_button)
        card.layout_box.addLayout(set_buttons)

        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("HH:MM:SS.mmm")
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("HH:MM:SS.mmm")
        card.layout_box.addWidget(self._field("Start time", self.start_edit))
        card.layout_box.addWidget(self._field("End time", self.end_edit))

        self.type_combo = QComboBox()
        add_value_items(self.type_combo, SOUND_TYPES)
        card.layout_box.addWidget(self._field("Acoustic class", self.type_combo))

        self.transition_container = QWidget()
        transition_layout = QVBoxLayout(self.transition_container)
        transition_layout.setContentsMargins(0, 0, 0, 0)
        transition_layout.setSpacing(6)
        transition_label = QLabel("State transition")
        transition_label.setObjectName("FieldLabel")
        self.transition_combo = QComboBox()
        add_value_items(self.transition_combo, TRANSITION_TYPES)
        transition_help = QLabel("S: inactive; L: left boom; R: right boom; LR: concurrent drilling.")
        transition_help.setObjectName("Hint")
        transition_help.setWordWrap(True)
        transition_layout.addWidget(transition_label)
        transition_layout.addWidget(self.transition_combo)
        transition_layout.addWidget(transition_help)
        self.transition_container.hide()
        card.layout_box.addWidget(self.transition_container)

        self.confidence_combo = QComboBox()
        add_value_items(self.confidence_combo, CONFIDENCES)
        card.layout_box.addWidget(self._field("Confidence", self.confidence_combo))

        self.event_id_edit = QLineEdit()
        self.event_id_edit.setPlaceholderText("e.g., E-012; use one ID for the same drilling event")
        card.layout_box.addWidget(self._field("Anonymous event ID", self.event_id_edit))

        self.reference_combo = QComboBox()
        add_value_items(self.reference_combo, REFERENCE_QUALITIES)
        card.layout_box.addWidget(self._field("Reference quality", self.reference_combo))

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Record short idling, interval completeness, or annotation evidence")
        self.note_edit.setFixedHeight(74)
        card.layout_box.addWidget(self._field("Notes", self.note_edit))

        actions = QHBoxLayout()
        self.save_annotation_button = QPushButton("Save annotation  Ctrl+S")
        self.save_annotation_button.setObjectName("PrimaryButton")
        self.cancel_edit_button = QPushButton("Clear draft")
        self.cancel_edit_button.setObjectName("ToolbarButton")
        actions.addWidget(self.save_annotation_button, 1)
        actions.addWidget(self.cancel_edit_button)
        card.layout_box.addLayout(actions)
        outer.addStretch()
        return scroll

    def _build_history_panel(self) -> QWidget:
        card = Card()
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Annotation Records")
        title.setObjectName("SectionTitle")
        self.history_summary = QLabel("Browse all records; select a row to edit it in the right panel")
        self.history_summary.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(self.history_summary)
        head.addLayout(title_box)
        head.addStretch()

        self.file_filter = QComboBox()
        self.file_filter.addItem(ui_text("全部录音"), "全部录音")
        self.type_filter = QComboBox()
        add_value_items(self.type_filter, ["全部类型", *SOUND_TYPES])
        self.confidence_filter = QComboBox()
        add_value_items(self.confidence_filter, ["全部置信度", *CONFIDENCES])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search event ID or notes")
        self.search_edit.setMaximumWidth(190)
        for widget in (self.file_filter, self.type_filter, self.confidence_filter, self.search_edit):
            head.addWidget(widget)

        self.locate_button = QPushButton("Locate")
        self.delete_button = QPushButton("Delete selected")
        self.delete_filtered_button = QPushButton("Delete filtered")
        self.locate_button.setObjectName("ToolbarButton")
        self.delete_button.setObjectName("DangerButton")
        self.delete_filtered_button.setObjectName("DangerButton")
        head.addWidget(self.locate_button)
        head.addWidget(self.delete_button)
        head.addWidget(self.delete_filtered_button)
        card.layout_box.addLayout(head)

        self.model = AnnotationTableModel(self.annotations)
        self.proxy = AnnotationFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        widths = [48, 210, 105, 105, 105, 145, 85, 96, 100, 170, 260]
        for i, width in enumerate(widths):
            self.table.setColumnWidth(i, width)
        card.layout_box.addWidget(self.table, 1)
        return card

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("Separator")
        return line

    def _stamp(self, title: str, value: str):
        frame = QFrame()
        frame.setObjectName("Stamp")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("StampLabel")
        strong = QLabel(value)
        strong.setObjectName("StampValue")
        strong.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(strong)
        return frame, strong

    def _field(self, label_text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return container

    def _connect_signals(self):
        self.play_button.clicked.connect(self.toggle_playback)
        self.speed_combo.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(float(self.speed_combo.currentData())))
        self.player.positionChanged.connect(self._player_position_changed)
        self.player.durationChanged.connect(self._player_duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.errorOccurred.connect(lambda *args: self._set_status(f"Playback error: {self.player.errorString()}", error=True))
        self.position_slider.sliderPressed.connect(lambda: setattr(self, "slider_dragging", True))
        self.position_slider.sliderReleased.connect(self._slider_released)
        self.position_slider.sliderMoved.connect(self._slider_preview)
        self.analysis_panel.seekRequested.connect(self.seek_to_seconds)
        self.correction_spin.valueChanged.connect(lambda: self._update_time_display(self.current_position_seconds()))
        self.source_combo.currentIndexChanged.connect(lambda: self._source_changed(combo_value(self.source_combo)))
        self.set_start_button.clicked.connect(self.set_draft_start)
        self.set_end_button.clicked.connect(self.set_draft_end)
        self.start_edit.editingFinished.connect(self._draft_fields_changed)
        self.end_edit.editingFinished.connect(self._draft_fields_changed)
        self.type_combo.currentIndexChanged.connect(lambda: self._type_changed(combo_value(self.type_combo)))
        self.save_annotation_button.clicked.connect(self.save_annotation)
        self.cancel_edit_button.clicked.connect(self.clear_editor)
        self.file_filter.currentTextChanged.connect(self._filters_changed)
        self.type_filter.currentTextChanged.connect(self._filters_changed)
        self.confidence_filter.currentTextChanged.connect(self._filters_changed)
        self.search_edit.textChanged.connect(self._filters_changed)
        self.table.selectionModel().selectionChanged.connect(self._table_selection_changed)
        self.table.doubleClicked.connect(lambda index: self.locate_selected())
        self.locate_button.clicked.connect(self.locate_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_filtered_button.clicked.connect(self.delete_filtered)

    def _install_shortcuts(self):
        for sequence, callback in (
            ("Space", self.toggle_playback),
            ("A", self.set_draft_start),
            ("D", self.set_draft_end),
            ("Ctrl+S", self.save_annotation),
            ("Ctrl+O", self.open_audio_dialog),
            ("Delete", self.delete_selected),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            self.addAction(action)

    def _apply_styles(self):
        QApplication.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(
            """
            #AppRoot { background: #F7F8FA; }
            #ContentArea { background: #F7F8FA; }
            #Sidebar { background: #FFFFFF; border-right: 1px solid #E8EBF0; }
            #Brand { font-size: 19px; font-weight: 750; color: #1F2937; }
            #BrandHint { color: #98A2B3; font-size: 10px; padding-left: 18px; }
            #ProfileCard { background: #F7F9FC; border: 1px solid #EEF1F5; border-radius: 10px; }
            #ProfileName { font-weight: 700; color: #1F2937; }
            #ProfileRole { color: #98A2B3; font-size: 10px; }
            #NavTitle { color: #A1A9B6; font-size: 9px; font-weight: 700; letter-spacing: 1px; padding-left: 10px; }
            #NavButton, #NavButtonActive { text-align: left; min-height: 38px; border: 0; border-radius: 8px; padding: 0 12px; font-weight: 600; }
            #NavButton { background: transparent; color: #667085; }
            #NavButton:hover { background: #F2F6FC; color: #1F7AE8; }
            #NavButtonActive { background: #EAF3FF; color: #1F7AE8; }
            #SidebarFooter { color: #A1A9B6; font-size: 9px; padding-top: 8px; }
            QLabel { color: #1F2937; }
            #AppTitle { font-size: 24px; font-weight: 750; color: #182230; }
            #AppSubtitle { font-size: 11px; color: #98A2B3; }
            #Card { background: #FFFFFF; border: 1px solid #E7EAF0; border-radius: 10px; }
            #CardTitle, #SectionTitle { font-size: 14px; font-weight: 700; color: #182230; }
            #FieldLabel { font-size: 9px; font-weight: 700; color: #667085; }
            #Muted { color: #98A2B3; }
            #Hint { color: #667085; background: #F8FAFC; border-radius: 6px; padding: 6px; }
            #FileName { font-size: 12px; font-weight: 700; color: #1F7AE8; background: #F0F6FF; border-radius: 6px; padding: 8px; }
            #HeroTime { font-family: Consolas; font-size: 17px; font-weight: 700; color: #1F7AE8; background: #F0F6FF; border: 1px solid #D9E9FF; border-radius: 6px; padding: 8px; }
            #SuccessBadge { color: #168A6A; background: #ECFDF3; border: 1px solid #C7F0D9; border-radius: 6px; padding: 4px 8px; font-weight: 700; }
            #MetricBadge { color: #667085; background: #F8FAFC; border: 1px solid #EAECF0; border-radius: 6px; padding: 5px 8px; font-weight: 600; }
            #SummaryCard { background: #FFFFFF; border: 1px solid #E7EAF0; border-radius: 10px; }
            #SummaryTitle { color: #667085; font-size: 10px; }
            #SummaryValue { color: #182230; font-size: 22px; font-weight: 750; }
            #SummaryHint { color: #98A2B3; font-size: 9px; }
            #StatusBar { color: #667085; background: #EEF4FB; border-radius: 6px; padding: 6px 9px; }
            #Separator { color: #EEF1F5; background: #EEF1F5; max-height: 1px; }
            #Stamp { background: #FAFBFC; border: 1px solid #EAECF0; border-radius: 7px; }
            #StampLabel { color: #98A2B3; font-size: 10px; }
            #StampValue { color: #344054; font-family: Consolas; font-weight: 700; }
            QPushButton, QToolButton { min-height: 31px; border-radius: 7px; padding: 0 12px; border: 1px solid #D0D5DD; background: #FFFFFF; color: #344054; font-weight: 600; }
            QPushButton:hover, QToolButton:hover { background: #F5F9FF; border-color: #A9C9F5; }
            #PrimaryButton, #SidePrimaryButton { background: #1F7AE8; border-color: #1F7AE8; color: white; }
            #PrimaryButton:hover, #SidePrimaryButton:hover { background: #1769D0; }
            #SidePrimaryButton { min-height: 45px; border-radius: 9px; font-size: 11px; }
            #ToolbarButton { background: #FFFFFF; }
            #DangerButton { color: #B42318; background: #FFF8F7; border-color: #F4C7C2; }
            #DangerButton:hover { background: #FDECEA; }
            QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit { background: white; border: 1px solid #D0D5DD; border-radius: 7px; padding: 6px 8px; color: #344054; selection-background-color: #BBD7FF; }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QTextEdit:focus { border: 1px solid #1F7AE8; }
            QComboBox::drop-down { border: 0; width: 24px; }
            QSlider::groove:horizontal { height: 6px; background: #E4E7EC; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #1F7AE8; border-radius: 3px; }
            QSlider::handle:horizontal { width: 16px; margin: -5px 0; border-radius: 8px; background: #1F7AE8; }
            QProgressBar { border: 0; border-radius: 6px; background: #EAECF0; text-align: center; color: #475467; min-height: 18px; }
            QProgressBar::chunk { border-radius: 6px; background: #40C9B0; }
            QTableView { background: white; alternate-background-color: #FAFBFC; border: 1px solid #EAECF0; border-radius: 7px; color: #344054; selection-background-color: #EAF3FF; selection-color: #182230; }
            QTableView::item { padding: 6px; border-bottom: 1px solid #F0F2F5; }
            QHeaderView::section { background: #F8FAFC; color: #667085; border: 0; border-bottom: 1px solid #EAECF0; padding: 8px; font-weight: 700; }
            QScrollArea { background: transparent; }
            QSplitter::handle { background: transparent; width: 8px; height: 8px; }
            QToolTip { background: #102C42; color: white; border: 0; padding: 5px; }
            """
        )

    def _set_status(self, text: str, error: bool = False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color:#B42318;background:#FFF0EE;" if error else "")

    def current_position_seconds(self) -> float:
        return max(0.0, self.player.position() / 1000.0)

    def current_start_datetime(self) -> datetime | None:
        return parse_filename_start(self.current_filename)

    def current_beijing(self, elapsed: float) -> datetime | None:
        base = self.current_start_datetime()
        if base is None:
            return None
        return base + timedelta(seconds=elapsed + self.correction_spin.value())

    def _update_time_display(self, elapsed: float):
        self.elapsed_time_label.setText(f"Recording  {format_elapsed(elapsed)}")
        self.beijing_time_label.setText(format_datetime(self.current_beijing(elapsed)))
        self.analysis_panel.set_cursor(elapsed)

    def open_audio_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择录音文件", str(Path.cwd()), "WAV 录音 (*.wav *.WAV);;所有文件 (*.*)")
        if path:
            self.open_audio(path)

    def open_audio(self, path: str):
        audio_path = Path(path)
        if not audio_path.exists():
            QMessageBox.warning(self, "文件不存在", str(audio_path))
            return
        self.current_audio_path = str(audio_path)
        self.current_filename = audio_path.name
        self.file_name_label.setText(self.current_filename)
        self.summary_recording.value_label.setText(self.current_filename)
        self.summary_recording.hint_label.setText("loaded recording")
        is_right = "右臂" in self.current_filename
        set_combo_value(self.source_combo, "右臂录音设备" if is_right else "左臂录音设备")
        correction = recommended_correction(self.current_filename, combo_value(self.source_combo))
        self.correction_spin.setValue(correction)
        self.alignment_hint.setText(self._alignment_description())
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self.clear_editor()
        self.analysis_panel.set_annotations(self.annotations, self.current_filename)
        self._start_analysis(str(audio_path))
        self._update_time_display(0.0)
        self._set_status(f"Loaded {self.current_filename}; generating full-record acoustic views.")
        self._refresh_filters()

    def _alignment_description(self) -> str:
        value = self.correction_spin.value()
        if "22-49-03" in self.current_filename:
            return f"Session 1  ·  auto {value:+.3f} s"
        if "23-49-08" in self.current_filename:
            return f"Session 2  ·  auto {value:+.3f} s"
        return f"Current correction  ·  {value:+.3f} s"

    def _source_changed(self, source: str):
        if self.current_filename:
            self.correction_spin.setValue(recommended_correction(self.current_filename, source))
            self.alignment_hint.setText(self._alignment_description())

    def _start_analysis(self, path: str):
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.quit()
            self.analysis_thread.wait(1000)
        self.analysis_progress.show()
        self.analysis_progress.setValue(0)
        self.analysis_thread = QThread(self)
        self.analysis_worker = AudioAnalysisWorker(path)
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.progress.connect(self._analysis_progress)
        self.analysis_worker.finished.connect(self._analysis_finished)
        self.analysis_worker.failed.connect(self._analysis_failed)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_thread.start()

    def _analysis_progress(self, value: int, text: str):
        self.analysis_progress.setValue(value)
        self.analysis_progress.setFormat(f"{value}%")
        self._set_status(text)

    def _analysis_finished(self, result: dict):
        if result.get("path") != self.current_audio_path:
            return
        self.analysis_data = result
        self.audio_duration = float(result["duration"])
        self.file_meta_label.setText(
            f"{result['sample_rate'] / 1000:g} kHz  ·  {result['channels']} channel(s)  ·  {format_elapsed(result['duration'])}"
        )
        self.analysis_panel.set_analysis(result)
        self.analysis_panel.set_annotations(self.annotations, self.current_filename)
        self.analysis_progress.hide()
        self._set_status("Spectrogram, energy, and clipping-robust change features are ready.")

    def _analysis_failed(self, details: str):
        self.analysis_progress.hide()
        self._set_status("Audio analysis failed. Check the WAV format.", error=True)
        QMessageBox.critical(self, "Audio analysis failed", details[-2000:])

    def toggle_playback(self):
        if not self.current_audio_path:
            self.open_audio_dialog()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_state_changed(self, state):
        self.play_button.setText("Ⅱ  Pause" if state == QMediaPlayer.PlayingState else "▶  Play")

    def _player_duration_changed(self, milliseconds: int):
        if milliseconds > 0:
            self.audio_duration = milliseconds / 1000.0

    def _player_position_changed(self, milliseconds: int):
        seconds = milliseconds / 1000.0
        if not self.slider_dragging and self.audio_duration > 0:
            self.position_slider.setValue(int(np.clip(seconds / self.audio_duration * 10000, 0, 10000)))
        self._update_time_display(seconds)

    def _slider_released(self):
        self.slider_dragging = False
        self.seek_to_seconds(self.position_slider.value() / 10000 * self.audio_duration)

    def _slider_preview(self, value: int):
        if self.audio_duration > 0:
            self._update_time_display(value / 10000 * self.audio_duration)

    def seek_to_seconds(self, seconds: float):
        seconds = float(np.clip(seconds, 0, self.audio_duration if self.audio_duration > 0 else seconds))
        self.player.setPosition(int(seconds * 1000))

    def seek_relative(self, delta: float):
        self.seek_to_seconds(self.current_position_seconds() + delta)

    def set_draft_start(self):
        if not self.current_audio_path:
            self._set_status("Load a recording first.", error=True)
            return
        self.draft_start = self.current_position_seconds()
        if self.draft_end is not None and self.draft_end < self.draft_start:
            self.draft_end = None
        self._refresh_draft_display()

    def set_draft_end(self):
        if not self.current_audio_path:
            self._set_status("Load a recording first.", error=True)
            return
        self.draft_end = self.current_position_seconds()
        self._refresh_draft_display()

    def _draft_fields_changed(self):
        start = parse_elapsed(self.start_edit.text())
        end = parse_elapsed(self.end_edit.text())
        self.draft_start = start
        self.draft_end = end
        self._refresh_draft_display(update_fields=False)

    def _refresh_draft_display(self, update_fields: bool = True):
        self.start_stamp[1].setText(format_elapsed(self.draft_start) if self.draft_start is not None else "Not set")
        self.end_stamp[1].setText(format_elapsed(self.draft_end) if self.draft_end is not None else "Not set")
        if update_fields:
            self.start_edit.setText(format_elapsed(self.draft_start) if self.draft_start is not None else "")
            self.end_edit.setText(format_elapsed(self.draft_end) if self.draft_end is not None else "")
        self.analysis_panel.set_draft(self.draft_start, self.draft_end)

    def _type_changed(self, sound_type: str):
        self.transition_container.setVisible(sound_type == "状态转换边界")
        if "钻孔声" not in sound_type:
            set_combo_value(self.reference_combo, "不适用")
        elif combo_value(self.reference_combo) == "不适用":
            set_combo_value(self.reference_combo, "未评估")

    def clear_editor(self):
        self.draft_start = None
        self.draft_end = None
        self.editing_index = None
        self.edit_mode_badge.setText("New annotation")
        self.save_annotation_button.setText("Save annotation  Ctrl+S")
        self.event_id_edit.clear()
        self.note_edit.clear()
        set_combo_value(self.reference_combo, "未评估" if "钻孔声" in combo_value(self.type_combo) else "不适用")
        self._refresh_draft_display()
        self.table.clearSelection()

    def save_annotation(self):
        if not self.current_filename:
            self._set_status("Load a recording first.", error=True)
            return
        self._draft_fields_changed()
        if self.draft_start is None or self.draft_end is None or self.draft_end < self.draft_start:
            self._set_status("Set valid start and end times.", error=True)
            return
        sound_type = combo_value(self.type_combo)
        transition = combo_value(self.transition_combo) if sound_type == "状态转换边界" else ""
        item = {
            "filename": self.current_filename,
            "source": combo_value(self.source_combo),
            "correction_seconds": float(self.correction_spin.value()),
            "start_elapsed": float(self.draft_start),
            "end_elapsed": float(self.draft_end),
            "start_bjt": format_datetime(self.current_beijing(self.draft_start)),
            "end_bjt": format_datetime(self.current_beijing(self.draft_end)),
            "type": sound_type,
            "transition_type": transition,
            "confidence": combo_value(self.confidence_combo),
            "event_id": self.event_id_edit.text().strip(),
            "reference_quality": combo_value(self.reference_combo),
            "note": self.note_edit.toPlainText().strip(),
        }
        if self.editing_index is None:
            self.annotations.append(item)
            message = f"Annotation {len(self.annotations)} saved."
        else:
            self.annotations[self.editing_index] = item
            message = f"Annotation {self.editing_index + 1} updated."
        self._annotations_changed(message)
        self.clear_editor()

    def _annotations_changed(self, message: str):
        self.model.refresh()
        self.proxy.invalidate()
        self._refresh_filters()
        high = sum(item.get("confidence") == "高" for item in self.annotations)
        boundaries = sum(item.get("type") == "状态转换边界" for item in self.annotations)
        self.history_summary.setText(f"{len(self.annotations)} total · {high} high-confidence · {boundaries} boundaries · select a row to edit")
        self.summary_annotations.value_label.setText(str(len(self.annotations)))
        self.summary_confidence.value_label.setText(str(high))
        self.summary_confidence.hint_label.setText(f"{boundaries} state boundaries")
        self.analysis_panel.set_annotations(self.annotations, self.current_filename)
        self._save_autosave(message)
        self._set_status(message)

    def _source_index_from_proxy(self, proxy_index: QModelIndex) -> int | None:
        if not proxy_index.isValid():
            return None
        return self.proxy.mapToSource(proxy_index).row()

    def _selected_source_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self._source_index_from_proxy(rows[0]) if rows else None

    def _table_selection_changed(self):
        index = self._selected_source_index()
        if index is None:
            return
        item = self.annotations[index]
        self.editing_index = index
        self.edit_mode_badge.setText(f"Editing annotation {index + 1}")
        self.save_annotation_button.setText("Update selected  Ctrl+S")
        self.draft_start = float(item.get("start_elapsed", 0))
        self.draft_end = float(item.get("end_elapsed", 0))
        set_combo_value(self.type_combo, item.get("type", SOUND_TYPES[0]))
        transition = item.get("transition_type", "")
        if transition in TRANSITION_TYPES:
            set_combo_value(self.transition_combo, transition)
        set_combo_value(self.confidence_combo, item.get("confidence", "高"))
        self.event_id_edit.setText(item.get("event_id", ""))
        set_combo_value(self.reference_combo, item.get("reference_quality", "未评估"))
        self.note_edit.setPlainText(item.get("note", ""))
        self._refresh_draft_display()

    def locate_selected(self):
        index = self._selected_source_index()
        if index is None:
            self._set_status("Select an annotation first.", error=True)
            return
        item = self.annotations[index]
        if item.get("filename") != self.current_filename:
            self._set_status(f"This annotation belongs to {item.get('filename')}; load that recording first.", error=True)
            return
        self.player.pause()
        self.seek_to_seconds(float(item.get("start_elapsed", 0)))
        duration = max(0.1, float(item.get("end_elapsed", 0)) - float(item.get("start_elapsed", 0)))
        start = max(0, float(item.get("start_elapsed", 0)) - 6)
        self.analysis_panel.spec_plot.setXRange(start, start + max(20, duration + 12), padding=0)
        self._set_status(f"Located {ui_text(item.get('type', ''))}: {format_elapsed(item.get('start_elapsed'))} to {format_elapsed(item.get('end_elapsed'))}.")

    def delete_selected(self):
        index = self._selected_source_index()
        if index is None:
            return
        item = self.annotations[index]
        if QMessageBox.question(self, "Delete annotation", f"Delete annotation {index + 1}: {ui_text(item.get('type', ''))}?") != QMessageBox.Yes:
            return
        self.annotations.pop(index)
        self.editing_index = None
        self._annotations_changed("Selected annotation deleted.")
        self.clear_editor()

    def _backup_annotations(self, reason: str) -> Path | None:
        if not self.annotations:
            return None
        backup_dir = app_data_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"标定备份_{timestamp}.json"
        payload = self._project_payload()
        payload["backup_reason"] = reason
        backup_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return backup_path

    def new_blank_annotation_set(self):
        count = len(self.annotations)
        if count:
            reply = QMessageBox.question(
                self,
                "New annotation set",
                f"The current {count} annotations will be backed up before the workspace is cleared.\n\n"
                "Audio files will not be deleted. Continue?",
            )
            if reply != QMessageBox.Yes:
                return
        backup_path = self._backup_annotations("新建空白标定集")
        self.annotations.clear()
        self.editing_index = None
        self.clear_editor()
        message = "A new blank annotation set has been created."
        if backup_path:
            message += f" The previous {count} records were backed up to {backup_path}"
        self._annotations_changed(message)

    def delete_filtered(self):
        source_rows = sorted(
            {
                self._source_index_from_proxy(self.proxy.index(row, 0))
                for row in range(self.proxy.rowCount())
            }
            - {None},
            reverse=True,
        )
        if not source_rows:
            self._set_status("No annotations match the current filters.", error=True)
            return
        filter_name = combo_value(self.file_filter)
        reply = QMessageBox.question(
            self,
            "Delete filtered annotations",
            f"Delete {len(source_rows)} annotations in the current filtered view?\n"
            f"Recording filter: {filter_name}\n\nA backup will be created first.",
        )
        if reply != QMessageBox.Yes:
            return
        backup_path = self._backup_annotations(f"删除当前筛选：{filter_name}")
        for index in source_rows:
            self.annotations.pop(index)
        self.editing_index = None
        self.clear_editor()
        message = f"Deleted {len(source_rows)} annotations from the filtered view."
        if backup_path:
            message += f" Backup: {backup_path}"
        self._annotations_changed(message)

    def _filters_changed(self):
        self.proxy.filename_filter = combo_value(self.file_filter)
        self.proxy.type_filter = combo_value(self.type_filter)
        self.proxy.confidence_filter = combo_value(self.confidence_filter)
        self.proxy.search_text = self.search_edit.text().strip()
        self.proxy.invalidate()

    def _refresh_filters(self):
        current = combo_value(self.file_filter)
        filenames = sorted({item.get("filename", "") for item in self.annotations if item.get("filename")})
        if self.current_filename and self.current_filename not in filenames:
            filenames.append(self.current_filename)
            filenames.sort()
        self.file_filter.blockSignals(True)
        self.file_filter.clear()
        self.file_filter.addItem(ui_text("全部录音"), "全部录音")
        for filename in filenames:
            self.file_filter.addItem(filename, filename)
        set_combo_value(self.file_filter, current if current in ["全部录音", *filenames] else "全部录音")
        self.file_filter.blockSignals(False)
        self._filters_changed()

    def _read_csv(self, path: str) -> list[dict]:
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = Path(path).read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别 CSV 编码")
        rows = []
        for raw in csv.DictReader(text.splitlines()):
            filename = (raw.get("录音文件") or "未标明录音文件").strip()
            source = (raw.get("录音来源（设备）") or "").strip()
            correction = float(raw.get("北京时间校正量秒") or recommended_correction(filename, source))
            start = parse_elapsed(raw.get("文件内开始时间", ""))
            end = parse_elapsed(raw.get("文件内结束时间", ""))
            try:
                legacy_duration = float(raw.get("时长秒") or 0)
            except ValueError:
                legacy_duration = 0.0
            if start is not None and (end is None or end < start) and legacy_duration > 0:
                # 兼容旧网页在整点处将 01:00:00 错写成 00:00:00 的历史记录。
                end = start + legacy_duration
            if start is None or end is None or end < start:
                continue
            note = (raw.get("备注") or "").strip()
            transition = (raw.get("状态转换类型") or "").strip() or transition_from_text(note)
            sound_type = (raw.get("声音类型") or "钻孔声（无法分辨左右）").strip()
            if transition and sound_type == "非作业干扰":
                sound_type = "状态转换边界"
            base = parse_filename_start(filename)
            start_bjt = format_datetime(base + timedelta(seconds=start + correction)) if base else (raw.get("校正后北京时间开始") or raw.get("北京时间开始") or "—")
            end_bjt = format_datetime(base + timedelta(seconds=end + correction)) if base else (raw.get("校正后北京时间结束") or raw.get("北京时间结束") or "—")
            rows.append(
                {
                    "filename": filename,
                    "source": source or ("右臂录音设备" if "右臂" in filename else "左臂录音设备"),
                    "correction_seconds": correction,
                    "start_elapsed": start,
                    "end_elapsed": end,
                    "start_bjt": start_bjt,
                    "end_bjt": end_bjt,
                    "type": sound_type,
                    "transition_type": transition if transition in TRANSITION_TYPES else "",
                    "confidence": (raw.get("置信度") or "低").strip(),
                    "event_id": (raw.get("孔位／连续作业编号") or raw.get("孔位/连续作业编号") or "").strip(),
                    "reference_quality": (raw.get("自注册参考质量") or ("未评估" if "钻孔声" in sound_type else "不适用")).strip(),
                    "note": note,
                }
            )
        return rows

    def import_csv_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "导入声音区间标定 CSV", str(Path.cwd()), "CSV 文件 (*.csv)")
        if paths:
            self.import_csv_paths(paths)

    def import_csv_paths(self, paths: list[str]):
        added = 0
        existing = {
            (item.get("filename"), round(float(item.get("start_elapsed", 0)), 3), round(float(item.get("end_elapsed", 0)), 3), item.get("type"), item.get("transition_type", ""))
            for item in self.annotations
        }
        for path in paths:
            for item in self._read_csv(path):
                key = (item.get("filename"), round(float(item.get("start_elapsed", 0)), 3), round(float(item.get("end_elapsed", 0)), 3), item.get("type"), item.get("transition_type", ""))
                if key in existing:
                    continue
                existing.add(key)
                self.annotations.append(item)
                added += 1
        self._annotations_changed(f"Imported {added} new annotations; duplicate records were skipped.")

    def export_csv_dialog(self):
        if not self.annotations:
            self._set_status("There are no annotations to export.", error=True)
            return
        default = f"声音区间标定_{Path(self.current_filename).stem if self.current_filename else '汇总'}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出声音标定 CSV", str(Path.cwd() / default), "CSV 文件 (*.csv)")
        if path:
            self.export_csv(path)

    def export_csv(self, path: str):
        fieldnames = [
            "标注编号", "录音来源（设备）", "录音文件", "北京时间校正量秒", "声音类型", "状态转换类型",
            "置信度", "孔位／连续作业编号", "自注册参考质量", "文件内开始时间", "文件内结束时间",
            "校正后北京时间开始", "校正后北京时间结束", "时长秒", "备注",
        ]
        ordered = sorted(self.annotations, key=lambda item: (item.get("start_bjt", ""), item.get("filename", ""), item.get("start_elapsed", 0)))
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for index, item in enumerate(ordered, 1):
                writer.writerow(
                    {
                        "标注编号": index,
                        "录音来源（设备）": item.get("source", ""),
                        "录音文件": item.get("filename", ""),
                        "北京时间校正量秒": f"{float(item.get('correction_seconds', 0)):.3f}",
                        "声音类型": item.get("type", ""),
                        "状态转换类型": item.get("transition_type", "") if item.get("type") == "状态转换边界" else "",
                        "置信度": item.get("confidence", ""),
                        "孔位／连续作业编号": item.get("event_id", ""),
                        "自注册参考质量": item.get("reference_quality", ""),
                        "文件内开始时间": format_elapsed(item.get("start_elapsed", 0)),
                        "文件内结束时间": format_elapsed(item.get("end_elapsed", 0)),
                        "校正后北京时间开始": item.get("start_bjt", ""),
                        "校正后北京时间结束": item.get("end_bjt", ""),
                        "时长秒": f"{float(item.get('end_elapsed', 0)) - float(item.get('start_elapsed', 0)):.3f}",
                        "备注": item.get("note", ""),
                    }
                )
        self._set_status(f"Exported {len(ordered)} annotations to {path}")

    def _project_payload(self) -> dict:
        return {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "current_audio_path": self.current_audio_path,
            "annotations": self.annotations,
        }

    def _save_autosave(self, reason: str):
        try:
            AUTOSAVE_PATH.write_text(json.dumps(self._project_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            self._set_status("Autosave failed. Export the annotations to CSV.", error=True)

    def _restore_autosave(self):
        if not AUTOSAVE_PATH.exists():
            return
        try:
            payload = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
            restored = payload.get("annotations", [])
            if isinstance(restored, list):
                self.annotations.extend(restored)
                self._annotations_changed(f"Restored {len(restored)} locally autosaved annotations.")
        except Exception:
            self._set_status("Could not read the local autosave. Import a CSV manually.", error=True)

    def save_project_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存标定项目", str(Path.cwd() / "声纹标定项目.json"), "标定项目 (*.json)")
        if path:
            Path(path).write_text(json.dumps(self._project_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._set_status(f"Project saved: {path}")

    def load_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开标定项目", str(Path.cwd()), "标定项目 (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            annotations = payload.get("annotations", [])
            if not isinstance(annotations, list):
                raise ValueError("项目文件缺少标定列表")
            self.annotations[:] = annotations
            self._annotations_changed(f"Project opened with {len(annotations)} annotations.")
            audio_path = payload.get("current_audio_path", "")
            if audio_path and Path(audio_path).exists():
                self.open_audio(audio_path)
        except Exception as exc:
            QMessageBox.critical(self, "项目打开失败", str(exc))

    def closeEvent(self, event):
        self._save_autosave("退出前保存")
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.quit()
            self.analysis_thread.wait(1500)
        event.accept()


def main():
    pg.setConfigOptions(antialias=True, imageAxisOrder="col-major")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("DrillSense Research")
    window = MainWindow()
    window.show()

    args = sys.argv[1:]
    if "--audio" in args:
        audio_position = args.index("--audio")
        if audio_position + 1 < len(args):
            window.open_audio(args[audio_position + 1])
    if "--csv" in args:
        csv_position = args.index("--csv")
        csv_paths = [item for item in args[csv_position + 1:] if not item.startswith("--")]
        if csv_paths:
            window.import_csv_paths(csv_paths)
    # ``--demo`` is retained as a compatibility flag, but no private path is
    # embedded in the public release. Use ``--csv path/to/file.csv`` instead.
    if "--screenshot" in args:
        position = args.index("--screenshot")
        target = Path(args[position + 1]) if position + 1 < len(args) else app_data_dir() / "界面预览.png"
        def capture():
            target.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(target))
            app.quit()
        QTimer.singleShot(4500, capture)
    elif "--smoke-test" in args:
        QTimer.singleShot(1800, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
