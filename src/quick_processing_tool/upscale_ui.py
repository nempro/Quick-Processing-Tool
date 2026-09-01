from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Event

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QObject, QSize, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSizePolicy, QSplitter, QStackedLayout, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .ui_styles import (
    INPUT_CONTROL_STYLE,
    PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH,
    PRIMARY_SETTINGS_PANE_MAX_WIDTH,
    PRIMARY_SETTINGS_PANE_MIN_WIDTH,
    set_operation_role,
)
from .image_workspace import MISSING_SOURCE_MESSAGE, SourceImage
from .source_ui import CurrentSourceCard
from .upscaler import (
    RealESRGANNCNNBackend, UpscaleMode, UpscaleOptions, UpscaleOutputFormat,
    UpscaleResult, UpscaleService,
)
from .upscaler.batch import (
    BatchCallbacks, BatchJob, BatchOutcome, QueueStatus, UpscaleQueueItem,
    run_sequential_batch,
)
from .upscaler.guard import ProjectedOutput, projected_output

LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
STATUS_TEXT = {
    QueueStatus.WAITING: "待機", QueueStatus.PROCESSING: "処理中",
    QueueStatus.SAVING: "保存中", QueueStatus.DONE: "完了",
    QueueStatus.FAILED: "失敗", QueueStatus.CANCELLED: "キャンセル",
    QueueStatus.MISSING: "元画像なし",
    QueueStatus.WARNING: "大きすぎる可能性", QueueStatus.SKIPPED: "スキップ",
}
UPSCALE_STYLE = INPUT_CONTROL_STYLE + """
QGroupBox { font-size: 14px; font-weight: 700; border: 1px solid #d7dde5;
 border-radius: 8px; margin-top: 9px; padding: 8px 7px 6px; background: #fbfcfd; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #182230; }
QPushButton { min-height: 26px; background: #f5f7fa; color: #182230;
 border: 1px solid #7b899a; border-radius: 6px; padding: 3px 9px; }
QPushButton:hover { background: #e5edf6; border-color: #405b79; }
QPushButton:focus { background: white; border: 2px solid #2457b2; padding: 2px 8px; }
QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QRadioButton#scaleOption { min-height: 36px; min-width: 0; padding: 4px;
 background-color: #e8eef5; color: #243447; border: 2px solid #8da0b5;
 border-radius: 8px; font-size: 16px; font-weight: 600; }
QRadioButton#scaleOption::indicator { width: 0; height: 0; }
QRadioButton#scaleOption:hover:!checked { background-color: #dbe6f1; border-color: #405b79; }
QRadioButton#scaleOption:checked { background-color: #315fbd; color: #ffffff;
 border-color: #174a9c; font-weight: 700; }
QRadioButton#scaleOption:focus { border-color: #102f6b; }
QRadioButton#scaleOption:disabled { background-color: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton#upscaleStart { min-height: 40px; background: #315fbd; color: white;
 border: 1px solid #315fbd; border-radius: 8px; font-size: 15px; font-weight: 700; }
QPushButton#upscaleStart:hover { background: #284fa1; }
QPushButton#upscaleStart:disabled { background: #d9dee6; color: #8f98a6; border-color: #d9dee6; }
QTreeWidget#upscaleQueue { background: white; border: 1px solid #aab4c1; border-radius: 7px; }
QTreeWidget#upscaleQueue::item { min-height: 27px; }
QTreeWidget#upscaleQueue::item:selected { background: #dce9ff; color: #172b4d; }
"""

class UpscalePreview(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fit_mode = True

    def set_image_path(self, path: Path) -> bool:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            reader.setScaledSize(
                source_size.scaled(QSize(1800, 1800), Qt.AspectRatioMode.KeepAspectRatio)
            )
        image = reader.read()
        if image.isNull():
            return False
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self.apply_zoom()
        return True

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())

    def set_fit(self, fit: bool) -> None:
        self._fit_mode = fit
        self.apply_zoom()

    def apply_zoom(self) -> None:
        self.resetTransform()
        if not self._item.pixmap().isNull() and self._fit_mode:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self.apply_zoom()

class UpscaleDropZone(QWidget):
    choose_requested = Signal()
    paths_dropped = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 320)
        self._empty = True
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.preview = UpscalePreview()
        self.preview.setAcceptDrops(False)
        self.preview.viewport().setAcceptDrops(False)
        self._stack.addWidget(self.preview)
        self.overlay = QFrame()
        self.overlay.setObjectName("upscaleDropOverlay")
        box = QVBoxLayout(self.overlay)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("▧")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 42px; color: #315fbd;")
        self.title = QLabel("画像をここにドロップ")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 21px; font-weight: 700; color: #182230;")
        formats = QLabel("PNG / JPEG / WebP\n複数枚まとめて追加できます")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats.setStyleSheet("color: #667085; font-size: 14px;")
        self.choose = QPushButton("画像を選ぶ")
        self.choose.setFixedWidth(150)
        self.choose.clicked.connect(self.choose_requested)
        box.addStretch(); box.addWidget(icon); box.addWidget(self.title); box.addWidget(formats)
        box.addSpacing(8); box.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignCenter); box.addStretch()
        self._stack.addWidget(self.overlay)
        self.set_empty(True)

    def set_empty(self, empty: bool) -> None:
        self._empty = empty
        self.overlay.setVisible(empty)
        self._stack.setCurrentWidget(self.overlay if empty else self.preview)
        self.set_drag_active(False)

    def set_drag_active(self, active: bool) -> None:
        self.overlay.setVisible(active or self._empty)
        self._stack.setCurrentWidget(self.overlay if active or self._empty else self.preview)
        self.title.setText("ここにドロップして画像を読み込み" if active else "画像をここにドロップ")
        style = (
            "QFrame#upscaleDropOverlay { background: rgba(226,237,255,245); border: 3px dashed #2457b2; border-radius: 12px; }"
            if active else
            "QFrame#upscaleDropOverlay { background: #f8fafc; border: 2px dashed #8da2b8; border-radius: 12px; }"
        )
        self.overlay.setStyleSheet(style)

    @staticmethod
    def _paths(event) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in event.mimeData().urls()
                if url.isLocalFile() and Path(url.toLocalFile()).is_file()
                and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_SUFFIXES]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.isEnabled() and self._paths(event):
            self.set_drag_active(True); event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and self._paths(event):
            self.set_drag_active(True); event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False); event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._paths(event)
        self.set_drag_active(False)
        if paths and self.isEnabled():
            self.paths_dropped.emit(paths); event.acceptProposedAction()
        else:
            event.ignore()

class UpscaleBatchWorker(QObject):
    item_status = Signal(int, str, str)
    current_item = Signal(int, int, int, str)
    item_result = Signal(int, object)
    batch_progress = Signal(int, int)
    completed = Signal(object)
    finished = Signal()

    def __init__(self, service: UpscaleService, jobs: list[BatchJob], options: UpscaleOptions, cancel_event: Event, skipped_jobs: list[BatchJob] | None = None) -> None:
        super().__init__()
        self.service = service
        self.jobs = jobs
        self.options = options
        self.cancel_event = cancel_event
        self.skipped_jobs = skipped_jobs or []

    @Slot()
    def run(self) -> None:
        callbacks = BatchCallbacks(
            status=lambda index, status, detail: self.item_status.emit(index, status.value, detail),
            current=self.current_item.emit,
            result=self.item_result.emit,
            progress=self.batch_progress.emit,
        )
        try:
            outcome = run_sequential_batch(self.service, self.jobs, self.options, self.cancel_event, callbacks, self.skipped_jobs)
            self.completed.emit(outcome)
        except Exception:
            LOGGER.exception("Unexpected upscale batch worker failure")
            for job in self.jobs:
                self.item_status.emit(
                    job.queue_index,
                    QueueStatus.FAILED.value,
                    "バッチ処理を完了できませんでした",
                )
            self.completed.emit(BatchOutcome(len(self.jobs) + len(self.skipped_jobs), 0, len(self.jobs), 0, 0.0, len(self.skipped_jobs)))
        finally:
            self.finished.emit()

class ElidedPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._value = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_value(self, value: str, tooltip: str | None = None) -> None:
        self._value = value
        self.setToolTip(tooltip if tooltip is not None else value)
        self._update()

    def set_path(self, path: Path | None) -> None:
        self.set_value(str(path or ""))

    def _update(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._value, Qt.TextElideMode.ElideMiddle, max(80, self.width() - 4)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update()

class UpscalePage(QWidget):
    processing_changed = Signal(bool)
    source_change_requested = Signal(object)
    result_handoff_requested = Signal(object, str)

    def __init__(self, service: UpscaleService | None = None) -> None:
        super().__init__()
        self.service = service or UpscaleService(RealESRGANNCNNBackend())
        self._workspace_managed = False
        self._current_source: SourceImage | None = None
        self.items: list[UpscaleQueueItem] = []
        self.current_index = -1
        self.output_folder: Path | None = None
        self._output_folder_explicit = False
        self._view_after = False
        self._thread: QThread | None = None
        self._worker: UpscaleBatchWorker | None = None
        self._cancel_event: Event | None = None
        self._last_outcome: BatchOutcome | None = None
        self._saved_folder_target: Path | None = None
        self._saved_output: Path | None = None
        self._building = True
        self._build_ui()
        self._building = False
        self._update_availability()

    @property
    def source_path(self) -> Path | None:
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index].source_path
        return None

    @property
    def result(self) -> UpscaleResult | None:
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index].result
        return None
    @result.setter
    def result(self, value: UpscaleResult | None) -> None:
        if 0 <= self.current_index < len(self.items):
            self.items[self.current_index].result = value

    def _build_ui(self) -> None:
        self.setStyleSheet(UPSCALE_STYLE)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("upscaleWorkspace")

        left = QWidget()
        left.setMinimumWidth(PRIMARY_SETTINGS_PANE_MIN_WIDTH)
        left.setMaximumWidth(PRIMARY_SETTINGS_PANE_MAX_WIDTH)
        ll = QVBoxLayout(left); ll.setContentsMargins(8, 8, 6, 8); ll.setSpacing(5)
        heading = QLabel("高画質化設定")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        ll.addWidget(heading)
        help_text = QLabel("すべての画像に同じ設定を適用します")
        help_text.setStyleSheet("color: #667085;"); help_text.setWordWrap(True)
        ll.addWidget(help_text)
        self.current_source_card = CurrentSourceCard()
        self.current_source_card.change_requested.connect(self.choose_current_source)
        ll.addWidget(self.current_source_card)
        self.add_current_source_button = QPushButton("現在の画像を高画質化に追加")
        self.add_current_source_button.clicked.connect(self.add_current_source)
        self.add_current_source_button.setEnabled(False)
        ll.addWidget(self.add_current_source_button)

        scale_box = QGroupBox("拡大倍率")
        scale_box.setMinimumWidth(0)
        scale_layout = QHBoxLayout(scale_box)
        scale_layout.setContentsMargins(4, 4, 4, 4)
        scale_layout.setSpacing(4)
        self.scale_2 = QRadioButton("2倍"); self.scale_4 = QRadioButton("4倍")
        for scale_button in (self.scale_2, self.scale_4):
            scale_button.setObjectName("scaleOption")
            scale_button.setMinimumWidth(0)
            scale_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            scale_layout.addWidget(scale_button, 1)
        self.scale_2.setChecked(True); ll.addWidget(scale_box)

        mode_box = QGroupBox("画像タイプ"); mode_layout = QHBoxLayout(mode_box)
        self.illustration = QRadioButton("イラスト"); self.photo = QRadioButton("写真")
        self.illustration.setChecked(True)
        mode_layout.addWidget(self.illustration); mode_layout.addWidget(self.photo); ll.addWidget(mode_box)

        output_box = QGroupBox("保存"); form = QFormLayout(output_box)
        self.format_combo = QComboBox()
        for label, value in (("元の形式", "same"), ("PNG", "PNG"), ("JPEG", "JPEG"), ("WebP", "WEBP")):
            self.format_combo.addItem(label, value)
        self.folder_label = ElidedPathLabel(); self.folder_label.set_value("元画像と同じフォルダー")
        self.folder_button = QPushButton("共通の保存先を選ぶ"); self.folder_button.clicked.connect(self.choose_folder)
        set_operation_role(self.folder_button, "secondary")
        form.addRow("保存形式", self.format_combo); form.addRow("保存先", self.folder_label); form.addRow("", self.folder_button)
        ll.addWidget(output_box)

        self.start_button = QPushButton("画像を選んでください")
        self.start_button.setObjectName("upscaleStart"); self.start_button.setEnabled(False); self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("キャンセル"); self.cancel_button.hide(); self.cancel_button.clicked.connect(self.cancel)
        set_operation_role(self.start_button, "primary")
        set_operation_role(self.cancel_button, "secondary")
        ll.addWidget(self.start_button); ll.addWidget(self.cancel_button); ll.addStretch(); splitter.addWidget(left)

        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(8, 8, 8, 8)
        preview_head = QHBoxLayout(); preview_head.addWidget(QLabel("プレビュー")); preview_head.addStretch()
        self.before_button = QPushButton("元画像"); self.after_button = QPushButton("高画質化後"); self.after_button.setEnabled(False)
        self.fit_button = QPushButton("全体表示"); self.actual_button = QPushButton("100%")
        for button in (self.before_button, self.after_button, self.fit_button, self.actual_button):
            preview_head.addWidget(button)
        self.before_button.clicked.connect(self.show_before); self.after_button.clicked.connect(self.show_after)
        self.fit_button.clicked.connect(lambda: self.drop_zone.preview.set_fit(True))
        self.actual_button.clicked.connect(lambda: self.drop_zone.preview.set_fit(False))
        cl.addLayout(preview_head)
        self.drop_zone = UpscaleDropZone()
        self.drop_zone.choose_requested.connect(self.choose_images); self.drop_zone.paths_dropped.connect(self.load_paths)
        cl.addWidget(self.drop_zone, 1); splitter.addWidget(center)
        right = QWidget(); right.setMinimumWidth(220); right.setMaximumWidth(430)
        rl = QVBoxLayout(right); rl.setContentsMargins(8, 8, 10, 8); rl.setSpacing(5)
        queue_head = QHBoxLayout()
        self.queue_title = QLabel("高画質化する画像　0枚")
        self.queue_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.queue_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.queue_title.setMinimumWidth(0)
        queue_head.addWidget(self.queue_title)
        queue_head.addStretch()
        rl.addLayout(queue_head)
        queue_actions = QHBoxLayout()
        self.add_button = QPushButton("画像を追加"); self.clear_button = QPushButton("一覧をクリア")
        self.add_button.clicked.connect(self.choose_images); self.clear_button.clicked.connect(self.clear_queue)
        set_operation_role(self.add_button, "secondary")
        set_operation_role(self.clear_button, "secondary")
        queue_actions.addStretch()
        queue_actions.addWidget(self.add_button); queue_actions.addWidget(self.clear_button)
        rl.addLayout(queue_actions)

        self.queue = QTreeWidget(); self.queue.setObjectName("upscaleQueue")
        self.queue.setHeaderLabels(["#", "画像", "状態"]); self.queue.setRootIsDecorated(False)
        self.queue.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.queue.header()
        header.setMinimumSectionSize(30)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.queue.setColumnWidth(0, 34)
        self.queue.setColumnWidth(2, 72)
        self.queue.currentItemChanged.connect(self._queue_selection_changed)
        rl.addWidget(self.queue, 1)
        self.queue_feedback = QLabel("画像を追加すると、ここで順番と状態を確認できます")
        self.queue_feedback.setWordWrap(True); self.queue_feedback.setStyleSheet("color: #667085;")
        rl.addWidget(self.queue_feedback)

        self.original_info = QLabel("元画像\n画像を読み込んでください")
        self.output_info = QLabel("高画質化後\n—")
        info_style = "padding: 7px; background: #f4f6f8; border: 1px solid #d7dde5; border-radius: 8px; color: #273142;"
        for label in (self.original_info, self.output_info):
            label.setWordWrap(True); label.setStyleSheet(info_style); rl.addWidget(label)
        self.engine_label = QLabel(); self.engine_label.setWordWrap(True); rl.addWidget(self.engine_label)
        self.progress_label = QLabel(); self.progress_label.setWordWrap(True); self.progress_label.hide()
        self.progress = QProgressBar(); self.progress.hide()
        rl.addWidget(self.progress_label); rl.addWidget(self.progress)
        self.result_label = QLabel(); self.result_label.setWordWrap(True); rl.addWidget(self.result_label)

        self.saved_box = QWidget(); saved = QVBoxLayout(self.saved_box); saved.setContentsMargins(7, 6, 7, 7)
        saved.setSpacing(4)
        set_operation_role(self.saved_box, "saveResult")
        saved.addWidget(QLabel("保存先")); self.saved_path = ElidedPathLabel(); saved.addWidget(self.saved_path)
        self.open_folder_button = QPushButton("保存先を開く"); self.open_folder_button.clicked.connect(self.open_saved_folder)
        self.split_result_button = QPushButton("画像分割へ")
        self.split_result_button.setToolTip("選択中の高画質化済み画像を、かんたん変換の画像分割へ渡します")
        self.split_result_button.clicked.connect(self.send_result_to_split)
        set_operation_role(self.open_folder_button, "secondary")
        set_operation_role(self.split_result_button, "secondary")
        saved.addWidget(self.open_folder_button)
        saved.addWidget(self.split_result_button)
        self.saved_box.hide(); rl.addWidget(self.saved_box)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 24); splitter.setStretchFactor(1, 47); splitter.setStretchFactor(2, 29)
        splitter.setSizes([PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH, 570, 340])
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(splitter)
        for button in (self.scale_2, self.scale_4, self.illustration, self.photo):
            button.toggled.connect(self._settings_changed)
        self.format_combo.currentIndexChanged.connect(self._settings_changed)

    def _update_availability(self) -> None:
        available = self.service.backend.check_availability()
        self.engine_label.setText(("● " if available.available else "⚠ ") + available.user_message)
        self.engine_label.setStyleSheet("color: #137333;" if available.available else "color: #9a6700;")
        self.engine_label.setToolTip(available.detail)
        self._engine_available = available.available
        self._update_actions()

    @Slot()
    def choose_images(self) -> None:
        if self._thread is not None:
            return
        names, _ = QFileDialog.getOpenFileNames(self, "高画質化する画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if names:
            self.load_paths([Path(name) for name in names])

    @Slot()
    def choose_current_source(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "現在の画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)"
        )
        if not name:
            return
        if self._workspace_managed:
            self.source_change_requested.emit(Path(name))
        else:
            self.load_paths([Path(name)])

    def set_workspace_managed(self, managed: bool = True) -> None:
        self._workspace_managed = managed

    @Slot(object)
    def set_current_source(self, source: SourceImage) -> None:
        self._current_source = source
        self.current_source_card.set_source(source)
        self.add_current_source_button.setEnabled(self._thread is None)
        if not self.items:
            self.load_paths([source.path], update_workspace=False)

    @Slot()
    def add_current_source(self) -> None:
        if self._current_source is not None:
            if not self._current_source.path.is_file():
                QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
                return
            self.load_paths([self._current_source.path], update_workspace=False)

    def load_image(self, path: Path) -> None:
        self.load_paths([path])

    @Slot(object)
    def load_paths(self, paths: list[Path], *, update_workspace: bool = True) -> None:
        if self._thread is not None:
            return
        existing = {os.path.normcase(str(item.source_path.resolve())) for item in self.items}
        added: list[UpscaleQueueItem] = []
        duplicates = 0
        errors: list[str] = []
        selected_source: Path | None = None
        for candidate in paths:
            path = Path(candidate)
            if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
                errors.append(f"{path.name}: 対応する画像ではありません")
                continue
            try:
                resolved = path.resolve()
                key = os.path.normcase(str(resolved))
                with Image.open(resolved) as opened:
                    opened.load()
                    image = ImageOps.exif_transpose(opened)
                    width, height = image.size
                    source_format = (opened.format or resolved.suffix[1:]).upper()
                    if source_format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError("unsupported decoded format")
                    has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
                size_bytes = resolved.stat().st_size
                if key in existing:
                    duplicates += 1
                    if selected_source is None:
                        selected_source = resolved
                    continue
                added.append(UpscaleQueueItem(resolved, width, height, source_format, size_bytes, has_alpha))
                if selected_source is None:
                    selected_source = resolved
                existing.add(key)
            except (OSError, UnidentifiedImageError, ValueError) as exc:
                LOGGER.exception("Upscale queue input decode failed: %s", path)
                errors.append(f"{path.name}: {exc}")

        first_new_row = len(self.items)
        for item in added:
            self.items.append(item)
            row = QTreeWidgetItem([str(len(self.items)), item.source_path.name, STATUS_TEXT[item.status]])
            row.setToolTip(1, str(item.source_path)); self.queue.addTopLevelItem(row)
        if added and self.current_index < 0:
            self.queue.setCurrentItem(self.queue.topLevelItem(first_new_row))
        if added and not self._output_folder_explicit and self.output_folder is None:
            self.output_folder = added[0].source_path.parent
        self.drop_zone.set_empty(not self.items)
        self.queue_title.setText(f"高画質化する画像　{len(self.items)}枚")
        feedback = f"{len(added)}枚追加しました"
        if duplicates:
            feedback += f" / 重複{duplicates}枚は追加していません"
        if added or duplicates:
            self.queue_feedback.setText(feedback)
        if errors:
            QMessageBox.warning(self, "一部の画像を追加できませんでした", "\n".join(errors))
        if update_workspace and self._workspace_managed and selected_source is not None:
            self.source_change_requested.emit(selected_source)
        self._refresh_large_warnings()
        self._clear_summary(); self._update_actions()

    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def _queue_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        del previous
        if current is None:
            self.current_index = -1
            return
        self.current_index = self.queue.indexOfTopLevelItem(current)
        self._view_after = bool(self.items[self.current_index].result)
        self._show_selected()

    def _show_selected(self) -> None:
        if not 0 <= self.current_index < len(self.items):
            return
        item = self.items[self.current_index]
        result = item.result
        self.split_result_button.setEnabled(
            result is not None and result.output_path.is_file()
        )
        preview_path = result.output_path if self._view_after and result else item.source_path
        if not self.drop_zone.preview.set_image_path(preview_path):
            self.drop_zone.preview.clear_image()
        self.before_button.setEnabled(self._view_after)
        self.after_button.setEnabled(result is not None and not self._view_after)
        self.original_info.setText(
            f"元画像\n{item.width} × {item.height} / {item.source_format} / {self._human_bytes(item.size_bytes)}\n{item.source_path.name}"
        )
        self._update_output_info()

    @Slot()
    def _settings_changed(self) -> None:
        if self._building or self._thread is not None:
            return
        for index, item in enumerate(self.items):
            item.status = QueueStatus.WAITING; item.detail = ""; item.result = None
            self._update_queue_row(index)
        self._refresh_large_warnings()
        self._view_after = False; self._clear_summary()
        if self.items:
            self._show_selected()
        self._update_actions()

    def _projected(self, item: UpscaleQueueItem) -> ProjectedOutput:
        return projected_output(item.width, item.height, self._scale())

    def _large_indices(self) -> list[int]:
        return [index for index, item in enumerate(self.items) if self._projected(item).is_large]

    def _refresh_large_warnings(self) -> None:
        for index, item in enumerate(self.items):
            if item.result is not None or item.status in {QueueStatus.PROCESSING, QueueStatus.SAVING}:
                continue
            projected = self._projected(item)
            if projected.is_large:
                item.status = QueueStatus.WARNING
                item.detail = f"{projected.width} × {projected.height} / 約{projected.megapixels:.1f}MP。GPUメモリ不足や処理失敗の可能性があります。"
            else:
                item.status = QueueStatus.WAITING
                item.detail = ""
            self._update_queue_row(index)

    def _large_output_choice(self, indices: list[int]) -> str:
        lines = []
        for index in indices:
            projected = self._projected(self.items[index])
            lines.append(f"{self.items[index].source_path.name}: {projected.width} × {projected.height} / 約{projected.megapixels:.1f}メガピクセル")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("大きな画像を処理します")
        box.setText("この画像は高画質化後に非常に大きくなります。\n\n" + "\n".join(lines))
        box.setInformativeText("GPUメモリ不足や処理失敗の可能性があります。")
        skip = box.addButton("大きい画像をスキップ", QMessageBox.ButtonRole.AcceptRole)
        proceed = box.addButton("それでも処理", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is skip:
            return "skip"
        if box.clickedButton() is proceed:
            return "continue"
        return "cancel"

    def _update_output_info(self) -> None:
        if not 0 <= self.current_index < len(self.items):
            self.output_info.setText("高画質化後\n—")
            return
        item = self.items[self.current_index]
        scale = self._scale(); mode = "イラスト" if self.illustration.isChecked() else "写真"
        if item.result:
            self.output_info.setText(
                f"高画質化後（保存済み）\n{item.result.width} × {item.result.height} / {self._human_bytes(item.result.size_bytes)}\n{item.result.output_path.name}"
            )
        else:
            projected = self._projected(item)
            warning = "\n大きすぎる可能性" if projected.is_large else ""
            self.output_info.setText(f"高画質化後（見込み）\n{projected.width} × {projected.height}\n約{projected.megapixels:.1f}MP / {scale}倍 / {mode}{warning}")
    def _options(self) -> UpscaleOptions:
        return UpscaleOptions(
            scale=self._scale(),
            mode=UpscaleMode.ILLUSTRATION if self.illustration.isChecked() else UpscaleMode.PHOTO,
            output_format=UpscaleOutputFormat(self.format_combo.currentData()),
        )

    def _scale(self) -> int:
        return 2 if self.scale_2.isChecked() else 4

    @Slot()
    def choose_folder(self) -> None:
        initial = str(self.output_folder or (self.source_path.parent if self.source_path else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "高画質化画像の共通保存先を選ぶ", initial)
        if folder:
            self.output_folder = Path(folder).resolve()
            self._output_folder_explicit = True
            self.folder_label.set_path(self.output_folder)

    @Slot()
    def start(self) -> None:
        if not self.items or self._thread is not None:
            return
        if len(self.items) == 1 and not self.items[0].source_path.is_file():
            self.items[0].status = QueueStatus.MISSING
            self.items[0].detail = MISSING_SOURCE_MESSAGE
            self._update_queue_row(0)
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return
        if self._output_folder_explicit and self.output_folder is not None:
            if self.output_folder.exists() and not self.output_folder.is_dir():
                QMessageBox.warning(self, "保存先を使用できません", "保存先フォルダーを選び直してください。")
                return

        large_indices = self._large_indices()
        choice = self._large_output_choice(large_indices) if large_indices else "continue"
        if choice == "cancel":
            return
        skipped_indices = set(large_indices if choice == "skip" else [])

        options = self._options()
        jobs: list[BatchJob] = []
        skipped_jobs: list[BatchJob] = []
        for index, item in enumerate(self.items):
            folder = self.output_folder if self._output_folder_explicit and self.output_folder else item.source_path.parent
            job = BatchJob(index, item.source_path, folder)
            (skipped_jobs if index in skipped_indices else jobs).append(job)
        if jobs:
            available = self.service.backend.check_availability()
            if not available.available:
                QMessageBox.warning(self, "高画質化を開始できません", available.user_message + "\n\nREADMEのRuntime設定を確認してください。")
                return
        for index, item in enumerate(self.items):
            item.status = QueueStatus.WAITING; item.detail = ""; item.result = None
            self._update_queue_row(index)
            if index in skipped_indices:
                item.status = QueueStatus.SKIPPED
                item.detail = "大きすぎる可能性があるためスキップ"
                self._update_queue_row(index)
        self._view_after = False
        self._show_selected()
        self._clear_summary()
        self._cancel_event = Event()
        total_jobs = len(jobs) + len(skipped_jobs)
        self.progress.setRange(0, total_jobs); self.progress.setValue(0); self.progress.show()
        self.progress_label.setText(f"0 / {total_jobs}枚 完了"); self.progress_label.show()
        if not jobs:
            self._on_completed(BatchOutcome(total_jobs, 0, 0, 0, 0.0, len(skipped_jobs)))
            return
        self._set_processing(True)
        self._thread = QThread(self)
        self._worker = UpscaleBatchWorker(self.service, jobs, options, self._cancel_event, skipped_jobs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.item_status.connect(self._on_item_status)
        self._worker.current_item.connect(self._on_current_item)
        self._worker.item_result.connect(self._on_item_result)
        self._worker.batch_progress.connect(self._on_batch_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker_refs)
        self._thread.start()

    @Slot()
    def cancel(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
            self.progress_label.setText(self.progress_label.text() + "\nキャンセルしています…")
            self.cancel_button.setEnabled(False)

    @Slot(int)
    def _on_progress(self, value: int) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(value)

    @Slot(int, str, str)
    def _on_item_status(self, index: int, status_value: str, detail: str) -> None:
        if 0 <= index < len(self.items):
            item = self.items[index]
            item.status = QueueStatus(status_value); item.detail = detail
            self._update_queue_row(index)

    @Slot(int, int, int, str)
    def _on_current_item(self, index: int, position: int, total: int, name: str) -> None:
        del index
        self.progress_label.setText(f"{position} / {total}枚目を処理中\n{name}")

    @Slot(int, object)
    def _on_item_result(self, index: int, result: UpscaleResult) -> None:
        if not 0 <= index < len(self.items):
            return
        self.items[index].result = result
        self._saved_output = result.output_path
        if index == self.current_index:
            self._view_after = True
            self._show_selected()

    @Slot(int, int)
    def _on_batch_progress(self, completed: int, total: int) -> None:
        self.progress.setRange(0, total); self.progress.setValue(completed)
        self.progress_label.setText(f"{completed} / {total}枚 完了")

    @Slot(object)
    def _on_completed(self, outcome: BatchOutcome) -> None:
        self._last_outcome = outcome
        completed = outcome.succeeded + outcome.failed + outcome.skipped
        self.progress.setRange(0, outcome.total)
        self.progress.setValue(completed)
        self.progress_label.setText(f"{completed} / {outcome.total}枚 処理済み")
        if outcome.cancelled:
            headline = "処理をキャンセルしました"
            detail = f"{outcome.succeeded}枚保存 / {outcome.failed}件失敗 / {outcome.cancelled}枚未完了"
            color = "#9a6700"
        elif outcome.failed:
            headline = "一部の画像を生成できませんでした"
            detail = f"{outcome.succeeded}枚保存 / {outcome.failed}件失敗"
            if outcome.skipped:
                detail += f" / {outcome.skipped}枚スキップ"
            color = "#c62828"
        elif outcome.skipped and not outcome.succeeded:
            headline = "大きい画像をスキップしました"
            detail = f"{outcome.skipped}枚スキップ"
            color = "#9a6700"
        elif outcome.skipped:
            headline = f"✓ {outcome.succeeded}枚の画像を保存しました"
            detail = f"{outcome.skipped}枚スキップ"
            color = "#137333"
        else:
            if outcome.total == 1:
                headline = f"✓ {self._scale()}倍の画像を保存しました"
                only = next((item.result for item in self.items if item.result), None)
                detail = only.output_path.name if only else "保存を確認しました"
            else:
                headline = f"✓ {outcome.succeeded}枚の画像を保存しました"
                detail = "すべての画像を正常に確認しました"
            color = "#137333"
        processed = outcome.succeeded + outcome.failed
        self.result_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.result_label.setText(
            f"{headline}\n{detail}\n合計 {outcome.duration_seconds:.1f}秒 / 平均 {outcome.duration_seconds / max(1, processed):.1f}秒"
        )
        successful = [item.result for item in self.items if item.result is not None]
        folders = {result.output_path.parent for result in successful}
        if successful:
            if len(folders) == 1:
                self._saved_folder_target = next(iter(folders))
                self.saved_path.set_path(self._saved_folder_target)
                self.open_folder_button.setText("保存先を開く")
            else:
                self._saved_folder_target = None
                self.saved_path.set_value("各元画像と同じフォルダー", "\n".join(sorted(str(folder) for folder in folders)))
                self.open_folder_button.setText("選択中の画像の保存先を開く")
            self.saved_box.show()
        else:
            self.saved_box.hide()

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None; self._thread = None; self._cancel_event = None
        self._set_processing(False)

    def _set_processing(self, active: bool) -> None:
        for widget in (
            self.scale_2, self.scale_4, self.illustration, self.photo,
            self.format_combo, self.folder_button, self.start_button,
            self.drop_zone, self.add_button, self.clear_button,
            self.add_current_source_button,
            self.current_source_card.change_button,
        ):
            widget.setEnabled(not active)
        self.cancel_button.setVisible(active); self.cancel_button.setEnabled(active)
        self.processing_changed.emit(active)
        if not active:
            self._update_actions()

    def _update_actions(self) -> None:
        idle = self._thread is None
        count = len(self.items)
        self.start_button.setEnabled(bool(count) and self._engine_available and idle)
        self.add_button.setEnabled(idle); self.clear_button.setEnabled(bool(count) and idle)
        self.add_current_source_button.setEnabled(self._current_source is not None and idle)
        self.current_source_card.change_button.setEnabled(idle)
        if not count:
            self.start_button.setText("画像を選んでください")
        elif count == 1:
            self.start_button.setText(f"{self._scale()}倍で高画質化を開始")
        else:
            self.start_button.setText(f"{count}枚まとめて{self._scale()}倍で高画質化")
    def _update_queue_row(self, index: int) -> None:
        row = self.queue.topLevelItem(index)
        if row is None:
            return
        item = self.items[index]
        row.setText(2, STATUS_TEXT[item.status]); row.setToolTip(2, item.detail)
        color = {
            QueueStatus.DONE: QColor("#137333"), QueueStatus.FAILED: QColor("#c62828"),
            QueueStatus.PROCESSING: QColor("#2457b2"), QueueStatus.SAVING: QColor("#2457b2"),
            QueueStatus.CANCELLED: QColor("#9a6700"), QueueStatus.WARNING: QColor("#9a6700"),
            QueueStatus.SKIPPED: QColor("#9a6700"),
            QueueStatus.MISSING: QColor("#c62828"),
        }.get(item.status, QColor("#273142"))
        row.setForeground(2, color)

    @Slot()
    def show_before(self) -> None:
        if self.source_path:
            self._view_after = False
            self._show_selected()

    @Slot()
    def show_after(self) -> None:
        if self.result and self.result.output_path.is_file():
            self._view_after = True
            self._show_selected()

    @Slot()
    def open_saved_folder(self) -> None:
        folder = self._saved_folder_target
        if folder is None and 0 <= self.current_index < len(self.items):
            result = self.items[self.current_index].result
            folder = result.output_path.parent if result else None
        if not folder or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "完了した画像を選択してから、もう一度お試しください。")

    @Slot()
    def send_result_to_split(self) -> None:
        result = self.result
        if result is None or not result.output_path.is_file():
            QMessageBox.warning(self, "画像分割へ渡せません", "完了した高画質化画像を選択してください。")
            return
        self.result_handoff_requested.emit(result.output_path, "quick_split")

    @Slot()
    def clear_queue(self) -> None:
        if self._thread is not None:
            return
        self.items.clear(); self.queue.clear(); self.current_index = -1; self._view_after = False
        if not self._output_folder_explicit:
            self.output_folder = None
        self.drop_zone.preview.clear_image(); self.drop_zone.set_empty(True)
        self.queue_title.setText("高画質化する画像　0枚")
        self.queue_feedback.setText("画像を追加すると、ここで順番と状態を確認できます")
        self.original_info.setText("元画像\n画像を読み込んでください")
        self.output_info.setText("高画質化後\n—")
        self._clear_summary(); self._update_actions()

    def _clear_summary(self) -> None:
        self._last_outcome = None; self._saved_folder_target = None; self._saved_output = None
        self.result_label.clear(); self.saved_box.hide()
        if self._thread is None:
            self.progress.hide(); self.progress_label.hide()

    def can_close(self) -> bool:
        return self._thread is None

    def cleanup(self) -> None:
        pass

    @staticmethod
    def _human_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
