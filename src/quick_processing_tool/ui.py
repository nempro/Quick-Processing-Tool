from __future__ import annotations

import copy
import ctypes
import logging
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import ProcessingError
from .models import ImageInfo, OutputFormat, ProcessingOptions, ResizeMode, Transform
from .naming import unique_output_path
from .pipeline import process_image, read_image_info, write_processed
from .processors.resize import output_dimensions
from .processors.transform import normalize_orientation
from .thumbnail_ui import ThumbnailPage


LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def desktop_folder() -> Path:
    """Resolve the visible Windows Desktop, including redirected folders."""
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:
            return Path(buffer.value)
    return Path.home() / "Desktop"


class PreviewCanvas(QGraphicsView):
    """A scene-based preview, ready for future editable overlay layers."""

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._image_item = QGraphicsPixmapItem()
        self._image_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._image_item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage) -> None:
        self._image_item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._image_item.boundingRect())
        self._fit()

    def clear_image(self) -> None:
        self._image_item.setPixmap(QPixmap())
        self.scene().setSceneRect(0, 0, 1, 1)

    def _fit(self) -> None:
        if not self._image_item.pixmap().isNull():
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class ProcessingWorker(QObject):
    progress = Signal(int)
    file_status = Signal(int, str, str)
    copy_ready = Signal(bytes)
    finished = Signal(int, int)

    def __init__(
        self,
        paths: list[Path],
        options: ProcessingOptions,
        copy_mode: bool,
        destination_mode: str,
        custom_folder: Path | None,
        processed_subfolder: bool,
        row_indices: list[int] | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.options = options
        self.copy_mode = copy_mode
        self.destination_mode = destination_mode
        self.custom_folder = custom_folder
        self.processed_subfolder = processed_subfolder
        self.row_indices = row_indices or list(range(len(paths)))

    def _folder_for(self, source: Path) -> Path:
        if self.destination_mode == "Desktop":
            return desktop_folder()
        if self.destination_mode == "Custom folder" and self.custom_folder:
            return self.custom_folder
        base = source.parent
        return base / "Processed" if self.processed_subfolder else base

    @Slot()
    def run(self) -> None:
        succeeded = failed = 0
        total = max(1, len(self.paths))
        for index, path in enumerate(self.paths):
            row = self.row_indices[index]
            self.file_status.emit(row, "Processing", "")
            try:
                result = process_image(path, self.options)
                if self.copy_mode:
                    self.copy_ready.emit(result.data)
                    detail = f"Copied · {result.width} × {result.height} · {human_bytes(result.size_bytes)}"
                else:
                    destination = unique_output_path(self._folder_for(path), path, result.format)
                    write_processed(result, destination, self.options.preserve_timestamp)
                    detail = str(destination)
                succeeded += 1
                self.file_status.emit(row, "Done", detail)
            except Exception as exc:  # Worker must continue after a partial batch failure.
                failed += 1
                LOGGER.exception("Processing failed: %s", path)
                message = str(exc) if isinstance(exc, ProcessingError) else f"処理に失敗しました: {path.name}"
                self.file_status.emit(row, "Error", message)
            self.progress.emit(round((index + 1) * 100 / total))
        self.finished.emit(succeeded, failed)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quick Processing Tool")
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)
        self.files: list[ImageInfo] = []
        self.current_index = -1
        self.custom_folder: Path | None = None
        self.transform_queue: list[Transform] = []
        self._thread: QThread | None = None
        self._worker: ProcessingWorker | None = None

        self._build_toolbar()
        self._build_content()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Drop PNG / JPEG / WebP here, or choose Open")

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Quick actions")
        toolbar.setMovable(False)
        self.open_action = QAction("Open", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_files)
        self.export_action = QAction("Export", self)
        self.export_action.setShortcut("Ctrl+S")
        self.export_action.triggered.connect(self.export_all)
        self.copy_action = QAction("Copy", self)
        self.copy_action.setShortcut("Ctrl+C")
        self.copy_action.triggered.connect(self.copy_current)
        self.reset_action = QAction("Reset", self)
        self.reset_action.triggered.connect(self.reset_settings)
        toolbar.addActions([self.open_action, self.export_action, self.copy_action, self.reset_action])

    def _build_content(self) -> None:
        self.navigation = QTabWidget()
        self.navigation.setDocumentMode(True)
        self.navigation.addTab(self._build_quick_page(), "Quick")
        self.thumbnail_page = ThumbnailPage()
        self.thumbnail_page.processing_changed.connect(self._thumbnail_processing_changed)
        self.navigation.addTab(self.thumbnail_page, "文字サムネ")
        for name in ("Edit", "Enhance", "Video"):
            placeholder = QLabel(f"{name} · Future phase")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            index = self.navigation.addTab(placeholder, name)
            self.navigation.setTabEnabled(index, False)
        self.navigation.currentChanged.connect(self._navigation_changed)
        self.setCentralWidget(self.navigation)
        self._navigation_changed(0)

    def _build_quick_page(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._settings_panel())

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.preview = PreviewCanvas()
        center_layout.addWidget(self.preview, 1)
        self.info_label = QLabel("No image loaded")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("padding: 8px; background: #f2f3f5;")
        center_layout.addWidget(self.info_label)
        splitter.addWidget(center)

        batch_box = QWidget()
        batch_layout = QVBoxLayout(batch_box)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.addWidget(QLabel("Files"))
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["File", "Input", "Status"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.currentItemChanged.connect(self._tree_selection_changed)
        batch_layout.addWidget(self.file_tree, 1)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        batch_layout.addWidget(self.progress)
        splitter.addWidget(batch_box)
        splitter.setSizes([300, 580, 300])
        return splitter

    def _settings_panel(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        resize_group = QGroupBox("Resize")
        form = QFormLayout(resize_group)
        self.resize_mode = QComboBox()
        for mode in ResizeMode:
            self.resize_mode.addItem(mode.value, mode)
        self.resize_mode.currentIndexChanged.connect(self._settings_changed)
        form.addRow("Mode", self.resize_mode)
        self.width_spin = self._spin(1, 30000, 1600)
        self.height_spin = self._spin(1, 30000, 1600)
        self.aspect_check = QCheckBox("Keep aspect ratio")
        self.aspect_check.setChecked(True)
        self.long_edge_spin = self._spin(1, 30000, 1600)
        self.long_edge_spin.setSingleStep(128)
        self.percent_spin = self._spin(1, 1000, 100)
        self.percent_spin.setSuffix(" %")
        for widget in (self.width_spin, self.height_spin, self.aspect_check, self.long_edge_spin, self.percent_spin):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._settings_changed)
            else:
                widget.valueChanged.connect(self._settings_changed)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("", self.aspect_check)
        form.addRow("Long edge", self.long_edge_spin)
        form.addRow("Scale", self.percent_spin)
        layout.addWidget(resize_group)

        output_group = QGroupBox("Output")
        output_form = QFormLayout(output_group)
        self.format_combo = QComboBox()
        for output_format in OutputFormat:
            self.format_combo.addItem(output_format.value, output_format)
        self.format_combo.currentIndexChanged.connect(self._settings_changed)
        output_form.addRow("Format", self.format_combo)
        self.target_combo = QComboBox()
        for label, value in (("No target", None), ("≤ 500 KB", 500 * 1024), ("≤ 1 MB", 1024 * 1024),
                             ("≤ 2 MB", 2 * 1024 * 1024), ("≤ 5 MB", 5 * 1024 * 1024), ("Custom", "custom")):
            self.target_combo.addItem(label, value)
        self.target_combo.currentIndexChanged.connect(self._settings_changed)
        output_form.addRow("Target size", self.target_combo)
        self.target_warning = QLabel("PNGは容量目標を達成できない場合があります。必要ならJPEGまたはWebPを選択してください。")
        self.target_warning.setWordWrap(True)
        self.target_warning.setStyleSheet("color: #b54708;")
        output_form.addRow("", self.target_warning)
        self.custom_kb = self._spin(1, 1024 * 1024, 1024)
        self.custom_kb.setSuffix(" KB")
        self.custom_kb.valueChanged.connect(self._settings_changed)
        output_form.addRow("Custom", self.custom_kb)
        self.quality_spin = self._spin(1, 100, 95)
        self.quality_spin.setToolTip("Maximum quality; target-size mode searches below this value")
        self.quality_spin.valueChanged.connect(self._settings_changed)
        output_form.addRow("Max quality", self.quality_spin)
        self.background_combo = QComboBox()
        self.background_combo.addItems(["White", "Black"])
        self.background_combo.currentIndexChanged.connect(self._settings_changed)
        output_form.addRow("JPEG alpha", self.background_combo)
        self.metadata_check = QCheckBox("Remove metadata (privacy)")
        self.metadata_check.setChecked(True)
        self.timestamp_check = QCheckBox("Preserve modified time")
        self.timestamp_check.setChecked(True)
        output_form.addRow("", self.metadata_check)
        output_form.addRow("", self.timestamp_check)
        layout.addWidget(output_group)

        transform_group = QGroupBox("Rotate / Flip")
        transform_layout = QVBoxLayout(transform_group)
        for transform in Transform:
            button = QPushButton(transform.value)
            button.clicked.connect(lambda checked=False, value=transform: self.add_transform(value))
            transform_layout.addWidget(button)
        self.transform_label = QLabel("No transform")
        self.transform_label.setWordWrap(True)
        transform_layout.addWidget(self.transform_label)
        layout.addWidget(transform_group)

        folder_group = QGroupBox("Destination")
        folder_form = QFormLayout(folder_group)
        self.destination_combo = QComboBox()
        self.destination_combo.addItems(["Same folder", "Desktop", "Custom folder"])
        self.destination_combo.currentTextChanged.connect(self._destination_changed)
        folder_form.addRow("Folder", self.destination_combo)
        self.processed_check = QCheckBox("Use Processed subfolder")
        self.processed_check.setChecked(True)
        folder_form.addRow("", self.processed_check)
        self.folder_button = QPushButton("Choose folder…")
        self.folder_button.clicked.connect(self.choose_folder)
        folder_form.addRow("", self.folder_button)
        layout.addWidget(folder_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setMinimumWidth(285)
        self._destination_changed(self.destination_combo.currentText())
        self._settings_changed()
        return scroll

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    @Slot()
    def open_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Open images", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if names:
            self.load_paths([Path(name) for name in names])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.navigation.currentIndex() != 0:
            event.ignore()
            return
        if self._thread is not None:
            event.ignore()
            return
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_SUFFIXES
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self.navigation.currentIndex() != 0:
            event.ignore()
            return
        if self._thread is not None:
            event.ignore()
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.load_paths(paths)
        event.acceptProposedAction()

    def load_paths(self, paths: list[Path]) -> None:
        if self._thread is not None:
            return
        valid: list[ImageInfo] = []
        errors: list[str] = []
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                errors.append(f"Unsupported format: {path.name}")
                continue
            try:
                info = read_image_info(path)
                valid.append(info)
                LOGGER.info("File open: %s", path)
            except ProcessingError as exc:
                LOGGER.exception("File open failed: %s", path)
                errors.append(str(exc))
        if valid:
            self.files = valid
            self.file_tree.clear()
            for info in valid:
                item = QTreeWidgetItem([info.path.name, human_bytes(info.size_bytes), "Waiting"])
                item.setToolTip(0, str(info.path))
                self.file_tree.addTopLevelItem(item)
            self.file_tree.setCurrentItem(self.file_tree.topLevelItem(0))
            mode = "Single Image Mode" if len(valid) == 1 else f"Batch Mode · {len(valid)} images"
            self.statusBar().showMessage(mode)
            self.export_action.setText("Export" if len(valid) == 1 else "Convert All")
        if errors:
            QMessageBox.warning(self, "Some files could not be opened", "\n".join(errors))

    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def _tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        del previous
        if current is None:
            return
        self.current_index = self.file_tree.indexOfTopLevelItem(current)
        self._show_current()

    def _show_current(self) -> None:
        if not 0 <= self.current_index < len(self.files):
            return
        info = self.files[self.current_index]
        try:
            with Image.open(info.path) as opened:
                image = normalize_orientation(opened).convert("RGBA")
                image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                raw = image.tobytes("raw", "RGBA")
                qimage = QImage(raw, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
            self.preview.set_image(qimage)
        except Exception:
            LOGGER.exception("Preview failed: %s", info.path)
            self.preview.clear_image()
        self._settings_changed()

    def options(self) -> ProcessingOptions:
        target = self.target_combo.currentData()
        if target == "custom":
            target = self.custom_kb.value() * 1024
        background = (255, 255, 255) if self.background_combo.currentText() == "White" else (0, 0, 0)
        return ProcessingOptions(
            resize_mode=ResizeMode(self.resize_mode.currentData()),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            keep_aspect=self.aspect_check.isChecked(),
            long_edge=self.long_edge_spin.value(),
            percentage=float(self.percent_spin.value()),
            output_format=OutputFormat(self.format_combo.currentData()),
            target_bytes=target,
            quality=self.quality_spin.value(),
            remove_metadata=self.metadata_check.isChecked(),
            preserve_timestamp=self.timestamp_check.isChecked(),
            jpeg_background=background,
            transforms=list(self.transform_queue),
        )

    @Slot()
    def _settings_changed(self) -> None:
        mode = ResizeMode(self.resize_mode.currentData())
        dimensions = mode is ResizeMode.DIMENSIONS
        self.width_spin.setVisible(dimensions)
        self.height_spin.setVisible(dimensions)
        self.aspect_check.setVisible(dimensions)
        self.long_edge_spin.setVisible(mode is ResizeMode.LONG_EDGE)
        self.percent_spin.setVisible(mode is ResizeMode.PERCENTAGE)
        self.custom_kb.setVisible(self.target_combo.currentData() == "custom")
        selected = OutputFormat(self.format_combo.currentData())
        current_is_png = any(info.format == "PNG" for info in self.files)
        output_is_png = selected is OutputFormat.PNG or (selected is OutputFormat.SAME and current_is_png)
        self.target_warning.setVisible(self.target_combo.currentData() is not None and output_is_png)
        self._update_info()

    def _update_info(self) -> None:
        if not 0 <= self.current_index < len(self.files):
            return
        info = self.files[self.current_index]
        width, height = info.width, info.height
        for transform in self.transform_queue:
            if transform in (Transform.ROTATE_LEFT, Transform.ROTATE_RIGHT):
                width, height = height, width
        out_width, out_height = output_dimensions(width, height, self.options())
        selected = OutputFormat(self.format_combo.currentData())
        out_format = info.format if selected is OutputFormat.SAME else selected.value.upper()
        target = self.target_combo.currentText()
        size_plan = target if self.options().target_bytes else "Auto"
        self.info_label.setText(
            f"Original  ·  {info.path.name}  ·  {info.width} × {info.height}  ·  {info.format}  ·  {human_bytes(info.size_bytes)}\n"
            f"Output  ·  {out_width} × {out_height}  ·  {out_format}  ·  {size_plan}"
        )

    def add_transform(self, transform: Transform) -> None:
        self.transform_queue.append(transform)
        self.transform_label.setText(" → ".join(item.value for item in self.transform_queue))
        self._update_info()

    @Slot()
    def reset_settings(self) -> None:
        self.resize_mode.setCurrentIndex(0)
        self.format_combo.setCurrentIndex(0)
        self.target_combo.setCurrentIndex(0)
        self.quality_spin.setValue(95)
        self.metadata_check.setChecked(True)
        self.timestamp_check.setChecked(True)
        self.background_combo.setCurrentIndex(0)
        self.transform_queue.clear()
        self.transform_label.setText("No transform")
        self.progress.setValue(0)
        for index in range(self.file_tree.topLevelItemCount()):
            self.file_tree.topLevelItem(index).setText(2, "Waiting")
        self._settings_changed()

    @Slot(str)
    def _destination_changed(self, value: str) -> None:
        self.folder_button.setEnabled(value == "Custom folder")
        self.processed_check.setEnabled(value == "Same folder")

    @Slot()
    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.custom_folder = Path(folder)
            self.folder_button.setText(self.custom_folder.name or str(self.custom_folder))

    @Slot()
    def export_all(self) -> None:
        if not self.files:
            QMessageBox.information(self, "No image", "Open or drop an image first.")
            return
        if self.destination_combo.currentText() == "Custom folder" and not self.custom_folder:
            self.choose_folder()
            if not self.custom_folder:
                return
        self._start_worker(
            [info.path for info in self.files],
            copy_mode=False,
            row_indices=list(range(len(self.files))),
        )

    @Slot()
    def copy_current(self) -> None:
        if not 0 <= self.current_index < len(self.files):
            QMessageBox.information(self, "No image", "Open or drop an image first.")
            return
        self._start_worker(
            [self.files[self.current_index].path],
            copy_mode=True,
            row_indices=[self.current_index],
        )

    def _start_worker(self, paths: list[Path], copy_mode: bool, row_indices: list[int]) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "Processing", "Please wait for the current operation.")
            return
        self.progress.setValue(0)
        self.open_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.copy_action.setEnabled(False)
        self.reset_action.setEnabled(False)
        self.navigation.setTabEnabled(1, False)
        self._thread = QThread(self)
        self._worker = ProcessingWorker(
            paths,
            copy.deepcopy(self.options()),
            copy_mode,
            self.destination_combo.currentText(),
            self.custom_folder,
            self.processed_check.isChecked(),
            row_indices,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.file_status.connect(self._on_file_status)
        self._worker.copy_ready.connect(self._set_clipboard)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker_refs)
        self._thread.start()

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self.navigation.setTabEnabled(1, True)
        self._update_quick_actions()

    @Slot(int)
    def _navigation_changed(self, index: int) -> None:
        del index
        self._update_quick_actions()

    def _update_quick_actions(self) -> None:
        enabled = self.navigation.currentIndex() == 0 and self._thread is None
        self.open_action.setEnabled(enabled)
        self.export_action.setEnabled(enabled)
        self.copy_action.setEnabled(enabled)
        self.reset_action.setEnabled(enabled)

    @Slot(bool)
    def _thumbnail_processing_changed(self, processing: bool) -> None:
        self.navigation.setTabEnabled(0, not processing)

    @Slot(int, str, str)
    def _on_file_status(self, index: int, status: str, detail: str) -> None:
        if 0 <= index < self.file_tree.topLevelItemCount():
            item = self.file_tree.topLevelItem(index)
            item.setText(2, status)
            item.setToolTip(2, detail)
        self.statusBar().showMessage(detail or status)

    @Slot(bytes)
    def _set_clipboard(self, data: bytes) -> None:
        image = QImage()
        if not image.loadFromData(data):
            QMessageBox.warning(self, "Clipboard error", "Clipboard用画像を作成できませんでした。")
            return
        QApplication.clipboard().setImage(image)
        self.statusBar().showMessage("Image copied to Clipboard")

    @Slot(int, int)
    def _on_finished(self, succeeded: int, failed: int) -> None:
        if failed:
            self.statusBar().showMessage(f"Finished · {succeeded} done · {failed} error")
            QMessageBox.warning(self, "Completed with errors", f"Done: {succeeded}\nError: {failed}\n詳細は一覧のTooltipとログを確認してください。")
        else:
            self.statusBar().showMessage(f"Finished · {succeeded} done")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread is not None or not self.thumbnail_page.can_close():
            QMessageBox.information(self, "Processing", "処理の完了後に閉じてください。")
            event.ignore()
            return
        event.accept()
