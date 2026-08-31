from __future__ import annotations

import copy
import ctypes
import html
import logging
import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCursor, QDragEnterEvent, QDropEvent, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsLineItem,
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
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedLayout,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import ProcessingError, UnsupportedImageError
from .image_workspace import (
    ImageWorkspace,
    MISSING_SOURCE_MESSAGE,
    MissingSourceError,
    SourceImage,
    require_source_file,
)
from .image_splitting import ImageSplitOptions, SplitDirection, partition_edges, split_boxes
from .models import ImageInfo, OutputFormat, ProcessingOptions, ResizeMode, Transform
from .naming import unique_output_path, unique_split_output_paths
from .pipeline import process_image, process_image_splits, read_image_info, write_processed
from .processors.resize import output_dimensions
from .processors.transform import normalize_orientation
from .thumbnail_ui import ThumbnailPage
from .edit_ui import QuickEditPage
from .upscale_ui import UpscalePage
from .pixel_editor_ui import PixelEditorPage
from .sound_effect_ui import SoundEffectPage
from .speech_bubble_ui import SpeechBubblePage
from .source_ui import CurrentSourceCard
from .ui_styles import (
    INPUT_CONTROL_STYLE,
    PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH,
    PRIMARY_SETTINGS_PANE_MAX_WIDTH,
    PRIMARY_SETTINGS_PANE_MIN_WIDTH,
)
from .preview_activity import PreviewActivityIndicator
from .window_geometry import adaptive_minimum_size, centered_window_geometry


LOGGER = logging.getLogger(__name__)
QUICK_PREVIEW_FORMATS = {"PNG", "JPEG", "WEBP"}
NAVIGATION_TAB_STYLE = """
QTabWidget::pane {
    border: 0;
    border-top: 1px solid #cbd5e1;
}
QTabBar::tab {
    min-height: 24px;
    padding: 7px 16px;
    margin-right: 2px;
    background-color: #edf1f5;
    color: #344054;
    border: 1px solid #c7d0db;
    border-bottom: 1px solid #aeb9c6;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-size: 14px;
    font-weight: 600;
}
QTabBar::tab:hover:!selected:!disabled {
    background-color: #dfe9f4;
    color: #173a63;
    border-color: #8095ab;
}
QTabBar::tab:selected {
    padding: 6px 15px;
    background-color: #ffffff;
    color: #174ea6;
    border: 2px solid #315fbd;
    border-bottom: 3px solid #315fbd;
    font-weight: 700;
}
QTabBar::tab:disabled {
    background-color: #f3f4f6;
    color: #a1a8b2;
    border-color: #dde1e6;
    font-weight: 500;
}
"""

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
RESIZE_LABELS = {
    ResizeMode.NONE: "変更しない",
    ResizeMode.DIMENSIONS: "幅と高さを指定",
    ResizeMode.LONG_EDGE: "長辺を指定",
    ResizeMode.PERCENTAGE: "倍率（%）",
}
FORMAT_LABELS = {
    OutputFormat.SAME: "元の形式",
    OutputFormat.PNG: "PNG",
    OutputFormat.JPEG: "JPEG",
    OutputFormat.WEBP: "WebP",
}
TRANSFORM_LABELS = {
    Transform.ROTATE_LEFT: "左へ90°回転",
    Transform.ROTATE_RIGHT: "右へ90°回転",
    Transform.ROTATE_180: "180°回転",
    Transform.FLIP_HORIZONTAL: "左右反転",
    Transform.FLIP_VERTICAL: "上下反転",
}


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
        self._split_enabled = False
        self._split_direction = SplitDirection.VERTICAL
        self._split_count = 4
        self._guide_items: list[QGraphicsLineItem] = []
        self._zoom_factor: float | None = None
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage) -> None:
        self._image_item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._image_item.boundingRect())
        self._update_split_guides()
        self._apply_zoom()

    def clear_image(self) -> None:
        self._image_item.setPixmap(QPixmap())
        self.scene().setSceneRect(0, 0, 1, 1)
        self._update_split_guides()

    def set_split_guides(
        self,
        enabled: bool,
        direction: SplitDirection,
        count: int,
    ) -> None:
        self._split_enabled = enabled
        self._split_direction = direction
        self._split_count = count
        self._update_split_guides()

    def _clear_split_guides(self) -> None:
        for item in self._guide_items:
            self.scene().removeItem(item)
        self._guide_items.clear()

    def _update_split_guides(self) -> None:
        self._clear_split_guides()
        pixmap = self._image_item.pixmap()
        if pixmap.isNull() or not self._split_enabled:
            return
        axis_length = (
            pixmap.width()
            if self._split_direction is SplitDirection.VERTICAL
            else pixmap.height()
        )
        try:
            positions = partition_edges(axis_length, self._split_count)[1:-1]
        except ValueError:
            return
        pen = QPen(QColor("#ffca3a"))
        pen.setWidthF(2.0)
        pen.setCosmetic(True)
        for position in positions:
            guide = QGraphicsLineItem()
            if self._split_direction is SplitDirection.VERTICAL:
                guide.setLine(float(position), 0.0, float(position), float(pixmap.height()))
            else:
                guide.setLine(0.0, float(position), float(pixmap.width()), float(position))
            guide.setPen(pen)
            guide.setZValue(10)
            self.scene().addItem(guide)
            self._guide_items.append(guide)

    def _fit(self) -> None:
        self.set_zoom_factor(None)

    def set_zoom_factor(self, factor: float | None) -> None:
        if factor is not None and factor <= 0:
            raise ValueError("zoom factor must be positive")
        self._zoom_factor = factor
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        self.resetTransform()
        if not self._image_item.pixmap().isNull():
            if self._zoom_factor is None:
                self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
            else:
                self.scale(self._zoom_factor, self._zoom_factor)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._zoom_factor is None:
            self._apply_zoom()


class DropZone(QWidget):
    """Central, always-active image entry point with explicit interaction states."""

    choose_requested = Signal()
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("preview_drop_zone")
        self.setAcceptDrops(True)
        self._has_image = False
        self.preview = PreviewCanvas()
        self.preview.setAcceptDrops(False)
        self.preview.viewport().setAcceptDrops(True)
        self.preview.viewport().installEventFilter(self)

        stack = QStackedLayout(self)
        self._stack = stack
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self.preview)

        self.overlay = QFrame()
        self.overlay.setObjectName("dropOverlay")
        self.overlay.setAcceptDrops(True)
        self.overlay.installEventFilter(self)
        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.setSpacing(12)
        overlay_layout.setContentsMargins(36, 36, 36, 36)

        self.drop_icon = QLabel("＋")
        self.drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_icon.setStyleSheet(
            "font-size: 42px; font-weight: 300; color: #315fbd;"
            "background: #e8efff; border-radius: 32px; min-width: 64px; min-height: 64px;"
        )
        self.drop_title = QLabel()
        self.drop_title.setObjectName("drop_hint_label")
        self.drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_title.setStyleSheet("font-size: 24px; font-weight: 700; color: #182230;")
        self.drop_subtitle = QLabel()
        self.drop_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_subtitle.setWordWrap(True)
        self.drop_subtitle.setStyleSheet("font-size: 14px; line-height: 1.5; color: #5d6878;")
        self.choose_button = QPushButton("画像を選ぶ")
        self.choose_button.setObjectName("choose_image_button")
        self.choose_button.setMinimumHeight(42)
        self.choose_button.setMinimumWidth(160)
        self.choose_button.setStyleSheet(
            "QPushButton { background: #315fbd; color: white; border: 0; border-radius: 8px;"
            "font-weight: 700; padding: 8px 20px; }"
            "QPushButton:pressed { background: #244b99; }"
        )
        self.choose_button.clicked.connect(self.choose_requested)

        overlay_layout.addStretch(1)
        overlay_layout.addWidget(self.drop_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        overlay_layout.addWidget(self.drop_title)
        overlay_layout.addWidget(self.drop_subtitle)
        overlay_layout.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignHCenter)
        overlay_layout.addStretch(1)
        stack.addWidget(self.overlay)
        self.clear_image()

    @staticmethod
    def _paths_from_mime(mime_data) -> list[Path]:
        if not mime_data.hasUrls():
            return []
        return [
            Path(url.toLocalFile())
            for url in mime_data.urls()
            if url.isLocalFile()
            and Path(url.toLocalFile()).is_file()
            and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_SUFFIXES
        ]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.isEnabled() and self._paths_from_mime(event.mimeData()):
            self.set_drag_active(True)
            event.acceptProposedAction()
        else:
            self.set_drag_active(False)
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and self._paths_from_mime(event.mimeData()):
            self.set_drag_active(True)
            event.acceptProposedAction()
        else:
            self.set_drag_active(False)
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._paths_from_mime(event.mimeData())
        self.set_drag_active(False)
        if paths and self.isEnabled():
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.DragEnter:
            self.dragEnterEvent(event)
            return event.isAccepted()
        if event_type == QEvent.Type.DragMove:
            self.dragMoveEvent(event)
            return event.isAccepted()
        if event_type == QEvent.Type.DragLeave:
            self.dragLeaveEvent(event)
            return True
        if event_type == QEvent.Type.Drop:
            self.dropEvent(event)
            return event.isAccepted()
        return super().eventFilter(watched, event)

    def set_image(self, image: QImage) -> None:
        self.preview.set_image(image)
        self._has_image = True
        self.set_drag_active(False)

    def clear_image(self) -> None:
        self.preview.clear_image()
        self._has_image = False
        self.set_drag_active(False)

    def set_loading(self) -> None:
        """Leave any prior preview in place while making the accepted drop state explicit."""
        self._has_image = True
        self.set_drag_active(False)

    def set_drag_active(self, active: bool) -> None:
        if active:
            self._stack.setCurrentWidget(self.overlay)
            self.drop_title.setText("ここにドロップして画像を読み込み")
            self.drop_subtitle.setText("PNG / JPG / WebP・複数枚まとめて追加できます")
            self.choose_button.hide()
            self.drop_icon.setText("↓")
            self.overlay.setStyleSheet(
                "QFrame#dropOverlay { border: 3px dashed #315fbd; border-radius: 16px;"
                "background: #eaf1ff; }"
            )
            self.overlay.show()
            self.overlay.raise_()
            return

        self.drop_title.setText("画像をここにドロップ")
        self.drop_subtitle.setText("PNG / JPG / WebP\n複数枚まとめて追加できます")
        self.choose_button.show()
        self.drop_icon.setText("＋")
        self.overlay.setStyleSheet(
            "QFrame#dropOverlay { border: 2px dashed #8f9bad; border-radius: 16px;"
            "background: #f7f9fc; }"
        )
        if self._has_image:
            self._stack.setCurrentWidget(self.preview)
            self.overlay.hide()
        else:
            self._stack.setCurrentWidget(self.overlay)
            self.overlay.show()
            self.overlay.raise_()


class CollapsibleSection(QWidget):
    """Purpose-first settings section with immediate, predictable disclosure."""

    def __init__(self, title: str, description: str, content: QWidget, expanded: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(3)

        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.toggle.setStyleSheet(
            "QToolButton { text-align: left; font-size: 15px; font-weight: 700;"
            "padding: 7px 7px; border: 1px solid transparent;"
            "background: #eef2f7; border-radius: 8px; color: #182230; }"
            "QToolButton:hover { background: #e4eaf2; }"
            "QToolButton[expanded=\"true\"] { background: #e8f1ff; color: #174ea6;"
            "border: 1px solid #b8cdf8; border-bottom: 2px solid #315fbd; }"
        )

        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: #667085; padding: 0 8px 2px 26px;")
        self.content = content
        self.content.setVisible(expanded)
        self.toggle.toggled.connect(self._set_expanded)

        layout.addWidget(self.toggle)
        layout.addWidget(self.description)
        layout.addWidget(self.content)
        self._apply_header_state(expanded)

    def _apply_header_state(self, expanded: bool) -> None:
        self.toggle.setProperty("expanded", expanded)
        self.toggle.style().unpolish(self.toggle)
        self.toggle.style().polish(self.toggle)
        self.toggle.update()

    @Slot(bool)
    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._apply_header_state(expanded)
        self.content.setVisible(expanded)


class ProcessingWorker(QObject):
    progress = Signal(int)
    file_status = Signal(int, str, str)
    copy_ready = Signal(bytes)
    outputs_saved = Signal(int)
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
        split_options: ImageSplitOptions | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.options = options
        self.copy_mode = copy_mode
        self.destination_mode = destination_mode
        self.custom_folder = custom_folder
        self.processed_subfolder = processed_subfolder
        self.row_indices = row_indices or list(range(len(paths)))
        self.split_options = split_options or ImageSplitOptions()

    def _folder_for(self, source: Path) -> Path:
        if self.destination_mode == "Desktop":
            return desktop_folder()
        if self.destination_mode == "Custom folder" and self.custom_folder:
            return self.custom_folder
        base = source.parent
        return base / "Processed" if self.processed_subfolder else base

    @Slot()
    def run(self) -> None:
        succeeded = failed = saved_outputs = 0
        total = max(1, len(self.paths))
        for index, path in enumerate(self.paths):
            row = self.row_indices[index]
            self.file_status.emit(row, "Processing", "")
            try:
                require_source_file(path)
                if self.copy_mode:
                    result = process_image(path, self.options)
                    self.copy_ready.emit(result.data)
                    detail = f"コピー完了 · {result.width} × {result.height} · {human_bytes(result.size_bytes)}"
                elif self.split_options.enabled:
                    results = process_image_splits(path, self.options, self.split_options)
                    folder = self._folder_for(path)
                    destinations = unique_split_output_paths(
                        folder,
                        path,
                        results[0].format,
                        len(results),
                    )
                    written: list[Path] = []
                    try:
                        for result, destination in zip(results, destinations, strict=True):
                            write_processed(
                                result,
                                destination,
                                self.options.preserve_timestamp,
                            )
                            written.append(destination)
                    except Exception:
                        for destination in written:
                            destination.unlink(missing_ok=True)
                        raise
                    saved_outputs += len(destinations)
                    detail = f"{len(destinations)}枚保存 · {folder}"
                else:
                    result = process_image(path, self.options)
                    destination = unique_output_path(
                        self._folder_for(path),
                        path,
                        result.format,
                    )
                    write_processed(result, destination, self.options.preserve_timestamp)
                    saved_outputs += 1
                    detail = str(destination)
                succeeded += 1
                self.file_status.emit(row, "Done", detail)
            except MissingSourceError:
                failed += 1
                LOGGER.warning("Processing source missing: %s", path)
                self.file_status.emit(row, "Missing", MISSING_SOURCE_MESSAGE)
            except Exception as exc:  # Worker must continue after a partial batch failure.
                failed += 1
                LOGGER.exception("Processing failed: %s", path)
                if not path.is_file():
                    self.file_status.emit(row, "Missing", MISSING_SOURCE_MESSAGE)
                else:
                    message = (
                        str(exc)
                        if isinstance(exc, ProcessingError)
                        else f"処理に失敗しました: {path.name}"
                    )
                    self.file_status.emit(row, "Error", message)
            self.progress.emit(round((index + 1) * 100 / total))
        self.outputs_saved.emit(saved_outputs)
        self.finished.emit(succeeded, failed)


def decode_quick_preview(path: Path) -> tuple[QImage, ImageInfo]:
    """Validate metadata and build the preview with one image-file open."""
    require_source_file(path)
    try:
        size_bytes = path.stat().st_size
        with Image.open(path) as opened:
            image_format = (opened.format or "").upper()
            if image_format not in QUICK_PREVIEW_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP のみ開けます。")
            opened.load()
            image = normalize_orientation(opened).convert("RGBA")
            info = ImageInfo(path, image.width, image.height, image_format, size_bytes)
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        raw = image.tobytes("raw", "RGBA")
        qimage = QImage(
            raw,
            image.width,
            image.height,
            image.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        return qimage, info
    except UnsupportedImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedImageError(f"画像を開けません: {path.name}") from exc


class QuickPreviewWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, request) -> None:
        super().__init__()
        self.request = request

    @Slot()
    def run(self) -> None:
        generation, request_id, path, identity = self.request
        try:
            qimage, _info = decode_quick_preview(path)
            self.succeeded.emit((generation, request_id, path, identity, qimage))
        except Exception as exc:
            LOGGER.exception("Preview failed: %s", path)
            message = MISSING_SOURCE_MESSAGE if not path.is_file() else f"プレビューを表示できませんでした: {exc}"
            self.failed.emit((generation, request_id, path, identity, message))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quick Processing Tool")
        startup_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if startup_screen is not None:
            available = startup_screen.availableGeometry()
            self.setMinimumSize(adaptive_minimum_size(available))
            self.setGeometry(centered_window_geometry(available))
        else:
            self.resize(1180, 760)
            self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)
        self.workspace = ImageWorkspace(self)
        self.files: list[ImageInfo] = []
        self.current_index = -1
        self.custom_folder: Path | None = None
        self.transform_queue: list[Transform] = []
        self._thread: QThread | None = None
        self._worker: ProcessingWorker | None = None
        self._active_split_options = ImageSplitOptions()
        self._saved_output_count = 0
        self._quick_preview_thread: QThread | None = None
        self._quick_preview_worker: QuickPreviewWorker | None = None
        self._quick_preview_generation = 0
        self._quick_preview_request_id = 0
        self._quick_preview_active_request = None
        self._quick_preview_pending_request = None
        self._quick_preview_active_result = None
        self._quick_preview_activity_token: int | None = None
        self._last_navigation_index = -1

        self._build_toolbar()
        self._build_content()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("中央へ画像をドロップするか、「画像を開く」を選んでください")

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("かんたん変換")
        toolbar.setMovable(False)
        self.open_action = QAction("画像を開く", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_source_image)
        self.export_action = QAction("画像を保存", self)
        self.export_action.setShortcut("Ctrl+S")
        self.export_action.triggered.connect(self.export_all)
        self.copy_action = QAction("クリップボードにコピー", self)
        self.copy_action.setShortcut("Ctrl+C")
        self.copy_action.triggered.connect(self.copy_current)
        self.reset_action = QAction("設定をリセット", self)
        self.reset_action.triggered.connect(self.reset_settings)
        toolbar.addActions([self.open_action, self.export_action, self.copy_action, self.reset_action])

    def _build_content(self) -> None:
        self.navigation = QTabWidget()
        self.navigation.setDocumentMode(True)
        self.navigation.setStyleSheet(NAVIGATION_TAB_STYLE)
        self.navigation.tabBar().setExpanding(False)
        self.navigation.tabBar().setUsesScrollButtons(True)
        self.quick_tab = self.navigation.addTab(self._build_quick_page(), "かんたん変換")
        self.edit_page = QuickEditPage()
        self.edit_page.set_workspace_managed(True)
        self.edit_page.source_change_requested.connect(self.set_current_source)
        self.edit_page.processing_changed.connect(self._edit_processing_changed)
        self.edit_page.palette_handoff_requested.connect(self._handoff_palette_to_pixel)
        self.edit_page.palette_open_requested.connect(self._open_pixel_tab_from_edit)
        self.image_edit_tab = self.navigation.addTab(self.edit_page, "画像加工")
        self.sound_effect_page = SoundEffectPage()
        self.sound_effect_tab = self.navigation.addTab(self.sound_effect_page, "擬音素材")
        self.speech_bubble_page = SpeechBubblePage()
        self.speech_bubble_tab = self.navigation.addTab(self.speech_bubble_page, "吹き出し素材")
        self.upscale_page = UpscalePage()
        self.upscale_page.set_workspace_managed(True)
        self.upscale_page.source_change_requested.connect(self.set_current_source)
        self.upscale_page.processing_changed.connect(self._upscale_processing_changed)
        self.upscale_tab = self.navigation.addTab(self.upscale_page, "高画質化")
        self.pixel_page = PixelEditorPage()
        self.pixel_page.set_workspace_managed(True)
        self.pixel_page.source_change_requested.connect(self.set_current_source)
        self.pixel_page.processing_changed.connect(self._pixel_processing_changed)
        self.pixel_tab = self.navigation.addTab(self.pixel_page, "ドット絵")
        self.thumbnail_page = ThumbnailPage()
        self.thumbnail_page.processing_changed.connect(self._thumbnail_processing_changed)
        self.thumbnail_tab = self.navigation.addTab(self.thumbnail_page, "文字サムネ")
        self.navigation.currentChanged.connect(self._navigation_changed)
        self.workspace.source_changed.connect(self._workspace_source_changed)
        self.setCentralWidget(self.navigation)
        self._navigation_changed(0)

    def _build_quick_page(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("quick_workspace")
        splitter.setStyleSheet(INPUT_CONTROL_STYLE)
        splitter.addWidget(self._settings_panel())

        center = QWidget()
        center.setMinimumWidth(240)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 8, 8, 8)
        self.quick_preview_activity = PreviewActivityIndicator()
        center_layout.addWidget(self.quick_preview_activity)
        self.drop_zone = DropZone()
        self.drop_zone.choose_requested.connect(self.open_files)
        self.drop_zone.paths_dropped.connect(self.load_paths)
        self.preview = self.drop_zone.preview
        self._update_split_preview_guides()
        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        zoom_row.setSpacing(4)
        zoom_label = QLabel("表示")
        zoom_label.setStyleSheet("color: #667085; font-weight: 600;")
        zoom_row.addWidget(zoom_label)
        self.preview_zoom_group = QButtonGroup(self)
        self.preview_zoom_buttons: dict[str, QPushButton] = {}
        for label, factor in (("全体表示", None), ("100%", 1.0), ("200%", 2.0)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QPushButton { padding: 4px 6px; }"
                "QPushButton:checked { background: #dbeafe; color: #174ea6;"
                "border: 2px solid #315fbd; font-weight: 700; }"
            )
            button.clicked.connect(
                lambda _checked=False, current=factor: self.preview.set_zoom_factor(current)
            )
            self.preview_zoom_group.addButton(button)
            self.preview_zoom_buttons[label] = button
            zoom_row.addWidget(button, 1)
        self.preview_zoom_buttons["全体表示"].setChecked(True)
        center_layout.addLayout(zoom_row)
        center_layout.addWidget(self.drop_zone, 1)
        self.info_label = QLabel("")
        self.info_label.setObjectName("preview_info_label")
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        self.info_label.setStyleSheet(
            "padding: 12px 14px; background: #f4f6f8; border-radius: 8px; color: #273142;"
        )
        self.info_label.hide()
        center_layout.addWidget(self.info_label)
        splitter.addWidget(center)

        batch_box = QWidget()
        batch_box.setObjectName("loaded_images_panel")
        batch_box.setMinimumWidth(180)
        batch_layout = QVBoxLayout(batch_box)
        batch_layout.setContentsMargins(8, 8, 8, 8)
        self.files_heading = QLabel("読み込んだ画像　0枚")
        self.files_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        batch_layout.addWidget(self.files_heading)
        files_help = QLabel("ここではプレビューする画像を選択できます")
        files_help.setWordWrap(True)
        files_help.setStyleSheet("color: #667085;")
        batch_layout.addWidget(files_help)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["ファイル名", "容量", "状態"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.currentItemChanged.connect(self._tree_selection_changed)
        batch_layout.addWidget(self.file_tree, 1)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        batch_layout.addWidget(self.progress)
        splitter.addWidget(batch_box)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH, 580, 240])
        return splitter

    def _settings_panel(self) -> QWidget:
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(6)

        heading = QLabel("何をしたいですか？")
        heading.setStyleSheet("font-size: 19px; font-weight: 750; color: #182230;")
        guidance = QLabel("必要な項目だけ開いて設定できます")
        guidance.setStyleSheet("color: #667085; padding-bottom: 2px;")
        layout.addWidget(heading)
        layout.addWidget(guidance)

        self.quick_source_card = CurrentSourceCard()
        self.quick_source_card.change_requested.connect(self.open_source_image)
        layout.addWidget(self.quick_source_card)
        self.quick_add_source_button = QPushButton("現在の画像を一覧へ追加")
        self.quick_add_source_button.clicked.connect(self.add_current_source_to_quick)
        self.quick_add_source_button.setEnabled(False)
        layout.addWidget(self.quick_add_source_button)

        capacity_content = QWidget()
        self.capacity_form = QFormLayout(capacity_content)
        self.target_combo = QComboBox()
        for label, value in (
            ("指定しない", None),
            ("500 KB以下", 500 * 1024),
            ("1 MB以下", 1024 * 1024),
            ("2 MB以下", 2 * 1024 * 1024),
            ("5 MB以下", 5 * 1024 * 1024),
            ("自由入力", "custom"),
        ):
            self.target_combo.addItem(label, value)
        self.target_combo.currentIndexChanged.connect(self._settings_changed)
        self.capacity_form.addRow("目標容量", self.target_combo)
        self.custom_kb = self._spin(1, 1024 * 1024, 1024)
        self.custom_kb.setSuffix(" KB")
        self.custom_kb.valueChanged.connect(self._settings_changed)
        self.capacity_form.addRow("自由入力", self.custom_kb)
        self.quality_spin = self._spin(1, 100, 95)
        self.quality_spin.setToolTip("目標容量以下になる最大画質を探索します")
        self.quality_spin.valueChanged.connect(self._settings_changed)
        self.capacity_form.addRow("画質の上限", self.quality_spin)
        self.target_warning = QLabel(
            "PNGのままでは容量目標を達成できない場合があります。"
            "必要なら「画像形式を変える」でJPEGまたはWebPを選択してください。"
        )
        self.target_warning.setWordWrap(True)
        self.target_warning.setStyleSheet("color: #b54708;")
        self.capacity_form.addRow("", self.target_warning)
        capacity_section = CollapsibleSection(
            "容量を小さくする",
            "1MB以下など、ファイル容量を減らします",
            capacity_content,
        )
        capacity_content.setObjectName("capacity_settings")
        layout.addWidget(capacity_section)

        resize_content = QWidget()
        self.resize_form = QFormLayout(resize_content)
        self.resize_mode = QComboBox()
        for mode in ResizeMode:
            self.resize_mode.addItem(RESIZE_LABELS[mode], mode)
        self.resize_mode.currentIndexChanged.connect(self._settings_changed)
        self.resize_form.addRow("変更方法", self.resize_mode)
        self.width_spin = self._spin(1, 30000, 1600)
        self.height_spin = self._spin(1, 30000, 1600)
        self.aspect_check = QCheckBox("縦横比を維持")
        self.aspect_check.setChecked(True)
        self.long_edge_spin = self._spin(1, 30000, 1600)
        self.long_edge_spin.setSingleStep(128)
        self.percent_spin = self._spin(1, 1000, 100)
        self.percent_spin.setSuffix(" %")
        for widget in (
            self.width_spin,
            self.height_spin,
            self.aspect_check,
            self.long_edge_spin,
            self.percent_spin,
        ):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._settings_changed)
            else:
                widget.valueChanged.connect(self._settings_changed)
        self.resize_form.addRow("幅", self.width_spin)
        self.resize_form.addRow("高さ", self.height_spin)
        self.resize_form.addRow("", self.aspect_check)
        self.resize_form.addRow("長辺", self.long_edge_spin)
        self.resize_form.addRow("倍率", self.percent_spin)
        resize_section = CollapsibleSection(
            "画像サイズを変更する",
            "長辺1600px、50%などに変更します",
            resize_content,
        )
        resize_content.setObjectName("resize_settings")
        layout.addWidget(resize_section)

        format_content = QWidget()
        self.format_form = QFormLayout(format_content)
        self.format_combo = QComboBox()
        for output_format in OutputFormat:
            self.format_combo.addItem(FORMAT_LABELS[output_format], output_format)
        self.format_combo.currentIndexChanged.connect(self._settings_changed)
        self.format_form.addRow("保存形式", self.format_combo)
        self.background_combo = QComboBox()
        self.background_combo.addItem("白", (255, 255, 255))
        self.background_combo.addItem("黒", (0, 0, 0))
        self.background_combo.currentIndexChanged.connect(self._settings_changed)
        self.format_form.addRow("透明部分の背景", self.background_combo)
        format_section = CollapsibleSection(
            "画像形式を変える",
            "PNG / JPEG / WebPへ変換します",
            format_content,
        )
        format_content.setObjectName("format_settings")
        layout.addWidget(format_section)

        transform_content = QWidget()
        transform_layout = QVBoxLayout(transform_content)
        transform_layout.setContentsMargins(0, 2, 0, 0)
        for transform in Transform:
            button = QPushButton(TRANSFORM_LABELS[transform])
            button.clicked.connect(
                lambda checked=False, value=transform: self.add_transform(value)
            )
            transform_layout.addWidget(button)
        self.transform_label = QLabel("変更なし")
        self.transform_label.setWordWrap(True)
        self.transform_label.setStyleSheet("color: #667085; padding: 4px;")
        transform_layout.addWidget(self.transform_label)
        transform_section = CollapsibleSection(
            "回転・反転する",
            "画像の向きを変更します",
            transform_content,
        )
        transform_content.setObjectName("transform_settings")
        layout.addWidget(transform_section)

        split_content = QWidget()
        split_layout = QVBoxLayout(split_content)
        split_layout.setContentsMargins(0, 2, 0, 0)
        split_layout.setSpacing(5)
        self.split_enable_check = QCheckBox("画像を分割して保存")
        self.split_enable_check.setToolTip(
            "元画像全体を欠けなく均等に分け、連番ファイルとして保存します"
        )
        self.split_enable_check.toggled.connect(self._split_settings_changed)
        split_layout.addWidget(self.split_enable_check)

        split_direction_label = QLabel("分割方向")
        split_direction_label.setStyleSheet("font-weight: 600; color: #344054;")
        split_layout.addWidget(split_direction_label)
        direction_row = QHBoxLayout()
        direction_row.setSpacing(4)
        self.split_direction_group = QButtonGroup(self)
        self.split_direction_buttons: dict[SplitDirection, QPushButton] = {}
        for direction, label, tooltip in (
            (SplitDirection.VERTICAL, "縦に分割", "左から右の順に保存します"),
            (SplitDirection.HORIZONTAL, "横に分割", "上から下の順に保存します"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(tooltip)
            button.setStyleSheet(
                "QPushButton { padding: 5px 4px; }"
                "QPushButton:checked { background: #dbeafe; color: #174ea6;"
                "border: 2px solid #315fbd; font-weight: 700; }"
            )
            self.split_direction_group.addButton(button)
            self.split_direction_buttons[direction] = button
            direction_row.addWidget(button, 1)
        self.split_direction_buttons[SplitDirection.VERTICAL].setChecked(True)
        self.split_direction_group.buttonClicked.connect(self._split_settings_changed)
        split_layout.addLayout(direction_row)

        split_count_label = QLabel("分割数")
        split_count_label.setStyleSheet("font-weight: 600; color: #344054;")
        split_layout.addWidget(split_count_label)
        count_row = QHBoxLayout()
        count_row.setSpacing(3)
        self.split_count_group = QButtonGroup(self)
        self.split_count_buttons: dict[int, QPushButton] = {}
        for count in range(2, 7):
            button = QPushButton(str(count))
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QPushButton { padding: 5px 2px; }"
                "QPushButton:checked { background: #dbeafe; color: #174ea6;"
                "border: 2px solid #315fbd; font-weight: 700; }"
            )
            self.split_count_group.addButton(button, count)
            self.split_count_buttons[count] = button
            count_row.addWidget(button, 1)
        self.split_count_buttons[4].setChecked(True)
        self.split_count_group.idClicked.connect(self._split_settings_changed)
        split_layout.addLayout(count_row)

        self.split_summary = QLabel("4分割 · 左から右")
        self.split_summary.setWordWrap(True)
        self.split_summary.setStyleSheet("color: #667085; padding: 2px;")
        split_layout.addWidget(self.split_summary)
        split_section = CollapsibleSection(
            "画像分割",
            "画像全体を2〜6枚へ均等に分けます",
            split_content,
        )
        split_content.setObjectName("split_settings")
        layout.addWidget(split_section)
        destination_content = QWidget()
        self.destination_form = QFormLayout(destination_content)
        self.destination_combo = QComboBox()
        self.destination_combo.addItem("元画像と同じ場所", "Same folder")
        self.destination_combo.addItem("デスクトップ", "Desktop")
        self.destination_combo.addItem("指定したフォルダー", "Custom folder")
        self.destination_combo.currentIndexChanged.connect(self._destination_changed)
        self.destination_form.addRow("保存先", self.destination_combo)
        self.processed_check = QCheckBox("処理済みサブフォルダーを\n使う")
        self.processed_check.setAccessibleName("処理済みサブフォルダーを使う")
        self.processed_check.setToolTip("処理済みサブフォルダーを使う")
        self.processed_check.setChecked(True)
        self.processed_check.toggled.connect(self._update_quick_clear_state)
        self.destination_form.addRow("", self.processed_check)
        self.folder_button = QPushButton("保存先を選ぶ…")
        self.folder_button.clicked.connect(self.choose_folder)
        self.destination_form.addRow("", self.folder_button)
        self.metadata_check = QCheckBox("メタ情報を削除")
        self.metadata_check.setChecked(True)
        self.metadata_check.setToolTip(
            "EXIFなどの画像情報を保存時に削除します"
        )
        self.metadata_check.setAccessibleDescription(
            "EXIFなどの画像情報を保存時に削除します"
        )
        self.timestamp_check = QCheckBox("元画像の更新日時を\n引き継ぐ")
        self.timestamp_check.setAccessibleName("元画像の更新日時を引き継ぐ")
        self.timestamp_check.setToolTip("元画像の更新日時を引き継ぐ")
        self.timestamp_check.setChecked(True)
        self.destination_form.addRow("", self.metadata_check)
        self.destination_form.addRow("", self.timestamp_check)
        destination_section = CollapsibleSection(
            "保存先とプライバシー",
            "メタ情報（EXIFなど）を保存時に削除できます",
            destination_content,
        )
        destination_section.description.setObjectName("metadata_privacy_summary")
        destination_content.setObjectName("destination_settings")
        layout.addWidget(destination_section)
        layout.addStretch(1)

        self.quick_sections = [
            capacity_section,
            resize_section,
            format_section,
            transform_section,
            split_section,
            destination_section,
        ]

        self.quick_settings_scroll = QScrollArea()
        self.quick_settings_scroll.setObjectName("quick_settings_scroll")
        self.quick_settings_scroll.setWidgetResizable(True)
        self.quick_settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.quick_settings_scroll.setWidget(content)

        panel = QWidget()
        panel.setObjectName("quick_settings_panel")
        panel.setMinimumWidth(PRIMARY_SETTINGS_PANE_MIN_WIDTH)
        panel.setMaximumWidth(PRIMARY_SETTINGS_PANE_MAX_WIDTH)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self.quick_settings_scroll, 1)

        self.quick_settings_footer = QFrame()
        self.quick_settings_footer.setObjectName("quick_settings_footer")
        self.quick_settings_footer.setStyleSheet(
            "QFrame#quick_settings_footer { background: #f8fafc;"
            "border-top: 1px solid #cbd5e1; padding-top: 2px; }"
            "QPushButton#quick_save_button { background: #315fbd; color: white;"
            "border: 1px solid #244b99; border-radius: 8px; font-weight: 700;"
            "padding: 8px 12px; min-height: 22px; }"
            "QPushButton#quick_save_button:hover { background: #284fa1; }"
            "QPushButton#quick_save_button:pressed { background: #1f3f82; }"
            "QPushButton#quick_save_button:disabled { background: #e5e7eb;"
            "color: #8a94a3; border-color: #d1d5db; }"
        )
        footer_layout = QVBoxLayout(self.quick_settings_footer)
        footer_layout.setContentsMargins(8, 6, 8, 8)
        footer_layout.setSpacing(4)
        self.quick_save_hint = QLabel("画像を開くと保存できます")
        self.quick_save_hint.setObjectName("quick_save_hint")
        self.quick_save_hint.setWordWrap(True)
        self.quick_save_hint.setStyleSheet("color: #667085; font-size: 12px;")
        self.quick_save_button = QPushButton("現在の設定で保存")
        self.quick_save_button.setObjectName("quick_save_button")
        self.quick_save_button.setEnabled(False)
        self.quick_save_button.setToolTip("画像を開くと保存できます")
        self.quick_save_button.clicked.connect(self.export_all)
        self.quick_clear_button = QPushButton("すべてクリア")
        self.quick_clear_button.setObjectName("quick_clear_button")
        self.quick_clear_button.setToolTip("現在の画像を残して、このタブの作業状態を初期化します")
        self.quick_clear_button.clicked.connect(self.clear_quick_all)
        footer_layout.addWidget(self.quick_save_hint)
        footer_layout.addWidget(self.quick_save_button)
        footer_layout.addWidget(self.quick_clear_button)
        panel_layout.addWidget(self.quick_settings_footer, 0)

        for section in self.quick_sections:
            section.toggle.toggled.connect(
                lambda expanded, current=section: self._quick_section_toggled(
                    current, expanded
                )
            )
        self._destination_changed()
        self._split_settings_changed()
        return panel

    def _quick_section_toggled(
        self, section: CollapsibleSection, expanded: bool
    ) -> None:
        if expanded:
            QTimer.singleShot(0, lambda: self._ensure_quick_section_visible(section))

    def _ensure_quick_section_visible(self, section: CollapsibleSection) -> None:
        if not section.toggle.isChecked() or section.content.isHidden():
            return
        viewport = self.quick_settings_scroll.viewport()
        content_top = section.content.mapTo(viewport, QPoint(0, 0)).y()
        desired_height = min(section.content.height(), 76)
        content_bottom = content_top + desired_height
        margin = 8
        delta = 0
        if content_top < margin:
            delta = content_top - margin
        elif content_bottom > viewport.height() - margin:
            delta = content_bottom - (viewport.height() - margin)
        if delta:
            bar = self.quick_settings_scroll.verticalScrollBar()
            bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + delta)))
    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    @Slot()
    def open_source_image(self) -> None:
        if not self._source_change_available():
            return
        name, _ = QFileDialog.getOpenFileName(
            self,
            "画像を開く",
            "",
            "画像ファイル (*.png *.jpg *.jpeg *.webp)",
        )
        if name:
            self.set_current_source(Path(name))

    @Slot(object)
    def set_current_source(self, path: Path) -> None:
        if not self._source_change_available():
            return
        try:
            self.workspace.set_source(Path(path))
        except MissingSourceError:
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
        except ProcessingError as exc:
            QMessageBox.warning(self, "画像を開けません", str(exc))

    @Slot(object)
    def _workspace_source_changed(self, source: SourceImage) -> None:
        self.quick_source_card.set_source(source)
        self.quick_add_source_button.setEnabled(True)
        if not self.files:
            self.load_paths([source.path], update_workspace=False)
        self.edit_page.set_current_source(source)
        self.upscale_page.set_current_source(source)
        self.pixel_page.set_current_source(source)
        self.statusBar().showMessage(f"現在の画像を {source.filename} に変更しました")

    @Slot()
    def add_current_source_to_quick(self) -> None:
        source = self.workspace.current
        if source is not None:
            if not source.path.is_file():
                QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
                return
            self.load_paths([source.path], update_workspace=False)

    @Slot()
    def open_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を開く",
            "",
            "画像ファイル (*.png *.jpg *.jpeg *.webp)",
        )
        if names:
            self.load_paths([Path(name) for name in names])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.navigation.currentIndex() != self.quick_tab or self._thread is not None:
            self.drop_zone.set_drag_active(False)
            event.ignore()
            return
        if DropZone._paths_from_mime(event.mimeData()):
            self.drop_zone.set_drag_active(True)
            event.acceptProposedAction()
        else:
            self.drop_zone.set_drag_active(False)
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_zone.set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self.navigation.currentIndex() != self.quick_tab or self._thread is not None:
            event.ignore()
            return
        paths = DropZone._paths_from_mime(event.mimeData())
        self.drop_zone.set_drag_active(False)
        if paths:
            self.load_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def load_paths(self, paths: list[Path], *, update_workspace: bool = True) -> None:
        if self._thread is not None:
            return
        valid: list[ImageInfo] = []
        errors: list[str] = []
        known_paths = {str(info.path.resolve()).casefold() for info in self.files}
        duplicate_count = 0
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                errors.append(f"対応していない形式です: {path.name}")
                continue
            try:
                info = read_image_info(path)
                key = str(info.path.resolve()).casefold()
                if key in known_paths:
                    duplicate_count += 1
                    continue
                known_paths.add(key)
                valid.append(info)
                LOGGER.info("File open: %s", path)
            except ProcessingError as exc:
                LOGGER.exception("File open failed: %s", path)
                errors.append(str(exc))
        if valid:
            was_empty = not self.files
            first_new_row = len(self.files)
            self.files.extend(valid)
            for info in valid:
                item = QTreeWidgetItem([info.path.name, human_bytes(info.size_bytes), "待機中"])
                item.setToolTip(0, str(info.path))
                self.file_tree.addTopLevelItem(item)
            if was_empty:
                self.file_tree.setCurrentItem(self.file_tree.topLevelItem(first_new_row))
            count = len(self.files)
            self.files_heading.setText(f"読み込んだ画像　{count}枚")
            self.statusBar().showMessage(
                "画像を1枚読み込みました"
                if len(valid) == 1
                else f"画像を{len(valid)}枚追加しました"
            )
            self.export_action.setText(
                "画像を保存" if count == 1 else f"{count}枚をまとめて保存"
            )
            self._update_quick_actions()
            if update_workspace:
                self.set_current_source(valid[0].path)
        elif duplicate_count:
            self.statusBar().showMessage("すでに読み込まれている画像です")
        if errors:
            QMessageBox.warning(self, "開けなかった画像があります", "\n".join(errors))
    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def _tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        del previous
        if current is None:
            return
        self.current_index = self.file_tree.indexOfTopLevelItem(current)
        self._show_current()
        if 0 <= self.current_index < len(self.files):
            self.set_current_source(self.files[self.current_index].path)

    def _show_current(self) -> None:
        if not 0 <= self.current_index < len(self.files):
            return
        info = self.files[self.current_index]
        # Selection owns preview feedback immediately, even if its source
        # disappears before identity inspection.  Older workers must not
        # overwrite the concrete missing-source state below.
        self._quick_preview_request_id += 1
        self._quick_preview_pending_request = None
        self.quick_preview_activity.invalidate()
        self._quick_preview_activity_token = None
        try:
            identity = self._quick_preview_source_identity(info.path)
        except OSError:
            self.drop_zone.clear_image()
            self.statusBar().showMessage(MISSING_SOURCE_MESSAGE)
            self._settings_changed()
            return
        request = (
            self._quick_preview_generation,
            self._quick_preview_request_id,
            info.path,
            identity,
        )
        self._quick_preview_activity_token = self.quick_preview_activity.begin("プレビューを準備しています")
        if self._quick_preview_thread is not None:
            self._quick_preview_pending_request = request
        else:
            self._start_quick_preview_request(request)
        self.drop_zone.set_loading()
        self._settings_changed()

    @staticmethod
    def _quick_preview_source_identity(path: Path):
        stat = path.stat()
        return str(path.resolve()), stat.st_mtime_ns, stat.st_size

    def _start_quick_preview_request(self, request) -> None:
        self._quick_preview_active_request = request
        self._quick_preview_active_result = None
        thread = QThread(self)
        worker = QuickPreviewWorker(request)
        self._quick_preview_thread = thread
        self._quick_preview_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda payload: self._capture_quick_preview_result("success", payload))
        worker.failed.connect(lambda payload: self._capture_quick_preview_result("failed", payload))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda t=thread, w=worker: self._finalize_quick_preview_request(t, w))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _capture_quick_preview_result(self, kind: str, payload) -> None:
        self._quick_preview_active_result = (kind, payload)

    def _quick_preview_request_is_current(self, request) -> bool:
        generation, _request_id, path, identity = request
        if generation != self._quick_preview_generation:
            return False
        if not 0 <= self.current_index < len(self.files) or self.files[self.current_index].path != path:
            return False
        try:
            return identity == self._quick_preview_source_identity(path)
        except OSError:
            return False

    def _quick_preview_request_owns_ui(self, request) -> bool:
        generation, request_id, *_rest = request
        return generation == self._quick_preview_generation and request_id == self._quick_preview_request_id

    def _finalize_quick_preview_request(self, thread: QThread, worker: QuickPreviewWorker) -> None:
        if thread is not self._quick_preview_thread:
            return
        request = self._quick_preview_active_request
        result = self._quick_preview_active_result
        self._quick_preview_thread = None
        self._quick_preview_worker = None
        self._quick_preview_active_request = None
        self._quick_preview_active_result = None
        if self._quick_preview_pending_request is not None:
            pending = self._quick_preview_pending_request
            self._quick_preview_pending_request = None
            self._start_quick_preview_request(pending)
            return
        if request is None:
            self.quick_preview_activity.invalidate()
            self._quick_preview_activity_token = None
            return
        if not self._quick_preview_request_owns_ui(request):
            return
        if result is None:
            result = ("failed", (*request, "プレビュー処理を完了できませんでした。"))
        kind, payload = result
        if kind == "success" and self._quick_preview_request_is_current(request):
            self.drop_zone.set_image(payload[-1])
            if self._quick_preview_activity_token is not None:
                self.quick_preview_activity.complete(self._quick_preview_activity_token)
        else:
            if kind == "success":
                path = request[2]
                message = MISSING_SOURCE_MESSAGE if not path.is_file() else "元画像が変更されたため、プレビューを更新しませんでした。"
            else:
                message = payload[-1]
            self.drop_zone.clear_image()
            self.statusBar().showMessage(message)
            if self._quick_preview_activity_token is not None:
                self.quick_preview_activity.fail(self._quick_preview_activity_token)
        self._quick_preview_activity_token = None

    def options(self) -> ProcessingOptions:
        target = self.target_combo.currentData()
        if target == "custom":
            target = self.custom_kb.value() * 1024
        background = tuple(self.background_combo.currentData())
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

    def split_options(self) -> ImageSplitOptions:
        direction = next(
            (
                direction
                for direction, button in self.split_direction_buttons.items()
                if button.isChecked()
            ),
            SplitDirection.VERTICAL,
        )
        count = self.split_count_group.checkedId()
        if count not in self.split_count_buttons:
            count = 4
        return ImageSplitOptions(
            enabled=self.split_enable_check.isChecked(),
            direction=direction,
            count=count,
        )

    @Slot()
    def _split_settings_changed(self, *_args) -> None:
        options = self.split_options()
        for button in self.split_direction_buttons.values():
            button.setEnabled(options.enabled)
        for button in self.split_count_buttons.values():
            button.setEnabled(options.enabled)
        order = (
            "左から右"
            if options.direction is SplitDirection.VERTICAL
            else "上から下"
        )
        self.split_summary.setText(f"{options.count}分割 · {order}")
        self._update_split_preview_guides()
        self._settings_changed()
        if hasattr(self, "quick_tab"):
            self._update_quick_actions()

    def _update_split_preview_guides(self) -> None:
        if not hasattr(self, "preview"):
            return
        options = self.split_options()
        self.preview.set_split_guides(
            options.enabled,
            options.direction,
            options.count,
        )

    @Slot()
    def _settings_changed(self) -> None:
        mode = ResizeMode(self.resize_mode.currentData())
        dimensions = mode is ResizeMode.DIMENSIONS
        self.resize_form.setRowVisible(self.width_spin, dimensions)
        self.resize_form.setRowVisible(self.height_spin, dimensions)
        self.resize_form.setRowVisible(self.aspect_check, dimensions)
        self.resize_form.setRowVisible(
            self.long_edge_spin, mode is ResizeMode.LONG_EDGE
        )
        self.resize_form.setRowVisible(
            self.percent_spin, mode is ResizeMode.PERCENTAGE
        )
        self.capacity_form.setRowVisible(
            self.custom_kb, self.target_combo.currentData() == "custom"
        )
        selected = OutputFormat(self.format_combo.currentData())
        self.format_form.setRowVisible(
            self.background_combo, selected is OutputFormat.JPEG
        )
        current_is_png = any(info.format == "PNG" for info in self.files)
        output_is_png = selected is OutputFormat.PNG or (
            selected is OutputFormat.SAME and current_is_png
        )
        self.capacity_form.setRowVisible(
            self.target_warning,
            self.target_combo.currentData() is not None and output_is_png,
        )
        self._update_info()
        self._update_quick_clear_state()

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
        out_format = (
            info.format if selected is OutputFormat.SAME else FORMAT_LABELS[selected]
        )
        unchanged = (
            ResizeMode(self.resize_mode.currentData()) is ResizeMode.NONE
            and selected is OutputFormat.SAME
            and not self.transform_queue
        )
        if self.options().target_bytes:
            size_plan = self.target_combo.currentText()
        elif unchanged:
            size_plan = f"約{human_bytes(info.size_bytes)}"
        else:
            size_plan = "容量は保存時に確定"
        split_info = ""
        split_options = self.split_options()
        if split_options.enabled:
            order = (
                "左から右"
                if split_options.direction is SplitDirection.VERTICAL
                else "上から下"
            )
            try:
                boxes = split_boxes(
                    out_width,
                    out_height,
                    split_options.direction,
                    split_options.count,
                )
            except ValueError:
                split_info = "<br><br><b>画像分割</b><br>画像サイズが分割数に足りません"
            else:
                panel_widths = [right - left for left, _top, right, _bottom in boxes]
                panel_heights = [bottom - top for _left, top, _right, bottom in boxes]
                width_label = (
                    str(panel_widths[0])
                    if min(panel_widths) == max(panel_widths)
                    else f"{min(panel_widths)}〜{max(panel_widths)}"
                )
                height_label = (
                    str(panel_heights[0])
                    if min(panel_heights) == max(panel_heights)
                    else f"{min(panel_heights)}〜{max(panel_heights)}"
                )
                split_info = (
                    f"<br><br><b>画像分割</b><br>"
                    f"{split_options.count}枚 / {order} / "
                    f"各 {width_label} × {height_label} px"
                )
        self.info_label.setText(
            f"<b>元画像</b><br>"
            f"{html.escape(info.path.name)}<br>"
            f"{info.width} × {info.height} / {info.format} / "
            f"{human_bytes(info.size_bytes)}"
            f"<br><br><b>保存後（見込み）</b><br>"
            f"{out_width} × {out_height} / {out_format} / {size_plan}"
            f"{split_info}"
        )
        self.info_label.show()

    def add_transform(self, transform: Transform) -> None:
        self.transform_queue.append(transform)
        self.transform_label.setText(
            " → ".join(TRANSFORM_LABELS[item] for item in self.transform_queue)
        )
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
        self.split_enable_check.setChecked(False)
        self.split_direction_buttons[SplitDirection.VERTICAL].setChecked(True)
        self.split_count_buttons[4].setChecked(True)
        self.transform_queue.clear()
        self.transform_label.setText("変更なし")
        self.progress.setValue(0)
        for index in range(self.file_tree.topLevelItemCount()):
            self.file_tree.topLevelItem(index).setText(2, "待機中")
        self._split_settings_changed()

    @Slot()
    def clear_quick_all(self) -> None:
        if self._thread is not None:
            return
        self._quick_preview_generation += 1
        self._quick_preview_request_id += 1
        self._quick_preview_pending_request = None
        self._quick_preview_active_result = None
        self.quick_preview_activity.invalidate()
        self._quick_preview_activity_token = None

        self.files.clear()
        self.current_index = -1
        self.file_tree.clear()
        self.files_heading.setText("読み込んだ画像　0枚")
        self.drop_zone.clear_image()
        self.preview_zoom_buttons["全体表示"].setChecked(True)
        self.preview.set_zoom_factor(None)
        self.info_label.clear()
        self.info_label.hide()
        self.progress.setValue(0)
        self.export_action.setText("画像を保存")

        self.reset_settings()
        self.custom_kb.setValue(1024)
        self.width_spin.setValue(1600)
        self.height_spin.setValue(1600)
        self.aspect_check.setChecked(True)
        self.long_edge_spin.setValue(1600)
        self.percent_spin.setValue(100)
        destination_index = self.destination_combo.findData("Same folder")
        self.destination_combo.setCurrentIndex(destination_index)
        self.processed_check.setChecked(True)
        self.custom_folder = None
        self.folder_button.setText("保存先を選ぶ…")
        self.folder_button.setToolTip("")
        self._destination_changed()
        self._settings_changed()
        self.statusBar().showMessage("かんたん変換の作業をクリアしました。現在の画像は保持されています")
        self._update_quick_actions()

    def _destination_changed(self, *_args) -> None:
        value = self.destination_combo.currentData()
        self.folder_button.setEnabled(value == "Custom folder")
        self.processed_check.setEnabled(value == "Same folder")
        self._update_quick_clear_state()

    @Slot()
    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先を選ぶ")
        if folder:
            self.custom_folder = Path(folder)
            self.folder_button.setText(
                self.custom_folder.name or str(self.custom_folder)
            )
            self.folder_button.setToolTip(str(self.custom_folder))
            self._update_quick_clear_state()

    @Slot()
    def export_all(self) -> None:
        if not self.files:
            QMessageBox.information(
                self, "画像がありません", "先に画像を開くかドロップしてください。"
            )
            return
        if (
            self.destination_combo.currentData() == "Custom folder"
            and not self.custom_folder
        ):
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
            QMessageBox.information(
                self, "画像がありません", "先に画像を開くかドロップしてください。"
            )
            return
        self._start_worker(
            [self.files[self.current_index].path],
            copy_mode=True,
            row_indices=[self.current_index],
        )

    def _start_worker(
        self, paths: list[Path], copy_mode: bool, row_indices: list[int]
    ) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self, "処理中", "現在の処理が終わるまでお待ちください。"
            )
            return
        missing = [path for path in paths if not Path(path).is_file()]
        if len(paths) == 1 and missing:
            row = row_indices[0]
            self._on_file_status(row, "Missing", MISSING_SOURCE_MESSAGE)
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return
        self.progress.setValue(0)
        self.open_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.copy_action.setEnabled(False)
        self.reset_action.setEnabled(False)
        self.drop_zone.set_drag_active(False)
        self.drop_zone.setEnabled(False)
        self.navigation.setTabEnabled(self.thumbnail_tab, False)
        self.navigation.setTabEnabled(self.sound_effect_tab, False)
        self.navigation.setTabEnabled(self.speech_bubble_tab, False)
        self.navigation.setTabEnabled(self.upscale_tab, False)
        self.navigation.setTabEnabled(self.image_edit_tab, False)
        self.navigation.setTabEnabled(self.pixel_tab, False)
        self._saved_output_count = 0
        self._active_split_options = (
            ImageSplitOptions() if copy_mode else copy.deepcopy(self.split_options())
        )
        self._thread = QThread(self)
        self._update_quick_actions()
        self._worker = ProcessingWorker(
            paths,
            copy.deepcopy(self.options()),
            copy_mode,
            self.destination_combo.currentData(),
            self.custom_folder,
            self.processed_check.isChecked(),
            row_indices,
            self._active_split_options,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.file_status.connect(self._on_file_status)
        self._worker.copy_ready.connect(self._set_clipboard)
        self._worker.outputs_saved.connect(self._set_saved_output_count)
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
        self._active_split_options = ImageSplitOptions()
        self.drop_zone.setEnabled(True)
        self.navigation.setTabEnabled(self.thumbnail_tab, True)
        self.navigation.setTabEnabled(self.sound_effect_tab, True)
        self.navigation.setTabEnabled(self.speech_bubble_tab, True)
        self.navigation.setTabEnabled(self.upscale_tab, True)
        self.navigation.setTabEnabled(self.image_edit_tab, True)
        self.navigation.setTabEnabled(self.pixel_tab, True)
        self._update_quick_actions()

    @Slot(int)
    def _navigation_changed(self, index: int) -> None:
        previous = self._last_navigation_index
        if previous == getattr(self, "image_edit_tab", -1) and index != previous:
            self.edit_page.finish_ime(clear_focus=True)
            self.edit_page.cancel_palette_extraction()
        self._last_navigation_index = index
        self._update_quick_actions()
        if index == self.sound_effect_tab:
            self.statusBar().showMessage("文字を入力するとプレビューされます")
        elif index == self.speech_bubble_tab:
            self.statusBar().showMessage("セリフを入力するとプレビューされます")
        elif index == self.quick_tab and not self.files:
            self.statusBar().showMessage("中央へ画像をドロップするか、「画像を開く」を選んでください")
        elif index != self.quick_tab:
            self.statusBar().clearMessage()

    def _update_quick_actions(self) -> None:
        quick_enabled = self.navigation.currentIndex() == self.quick_tab and self._thread is None
        global_open_enabled = self._source_change_available()
        split_options = self.split_options()
        self.open_action.setEnabled(global_open_enabled)
        self.export_action.setEnabled(quick_enabled and bool(self.files))
        self.copy_action.setEnabled(
            quick_enabled and bool(self.files) and not split_options.enabled
        )
        self.reset_action.setEnabled(quick_enabled)
        if split_options.enabled and self.files:
            output_count = len(self.files) * split_options.count
            self.export_action.setText(f"{output_count}枚に分割して保存")
        elif self.files:
            self.export_action.setText(
                "画像を保存"
                if len(self.files) == 1
                else f"{len(self.files)}枚をまとめて保存"
            )
        else:
            self.export_action.setText("画像を保存")
        if not quick_enabled:
            self.drop_zone.set_drag_active(False)
        self.drop_zone.setEnabled(quick_enabled)
        self.quick_source_card.change_button.setEnabled(global_open_enabled)
        self.quick_add_source_button.setEnabled(
            global_open_enabled and self.workspace.current is not None
        )
        save_enabled = quick_enabled and bool(self.files)
        self.quick_save_button.setEnabled(save_enabled)
        if self._thread is not None:
            save_hint = "保存中です…"
        elif not self.files:
            save_hint = "画像を開くと保存できます"
        elif not quick_enabled:
            save_hint = "かんたん変換タブで保存できます"
        elif split_options.enabled:
            output_count = len(self.files) * split_options.count
            save_hint = (
                f"各画像を{split_options.count}分割し、"
                f"合計{output_count}枚を保存します"
            )
        else:
            save_hint = "すべての設定をまとめて適用します"
        self.quick_save_hint.setText(save_hint)
        self.quick_save_button.setToolTip(save_hint)
        self.quick_save_button.setAccessibleDescription(save_hint)
        self._update_quick_clear_state()

    def _update_quick_clear_state(self, *_args) -> None:
        if not hasattr(self, "quick_clear_button"):
            return
        quick_enabled = (
            self.navigation.currentIndex() == getattr(self, "quick_tab", -1)
            and self._thread is None
        )
        quick_dirty = (
            bool(self.files)
            or self.options() != ProcessingOptions()
            or self.split_options() != ImageSplitOptions()
            or self.destination_combo.currentData() != "Same folder"
            or not self.processed_check.isChecked()
            or self.custom_folder is not None
            or (
                hasattr(self, "progress")
                and self.progress.value() != 0
            )
        )
        self.quick_clear_button.setEnabled(quick_enabled and quick_dirty)

    def _source_change_available(self) -> bool:
        return (
            self._thread is None
            and self.thumbnail_page.can_close()
            and self.edit_page.can_replace_source()
            and self.upscale_page.can_close()
            and self.pixel_page.can_replace_source()
        )

    @Slot(bool)
    def _thumbnail_processing_changed(self, processing: bool) -> None:
        self.navigation.setTabEnabled(self.quick_tab, not processing)
        self.navigation.setTabEnabled(self.sound_effect_tab, not processing)
        self.navigation.setTabEnabled(self.speech_bubble_tab, not processing)
        self.navigation.setTabEnabled(self.upscale_tab, not processing)
        self.navigation.setTabEnabled(self.image_edit_tab, not processing)
        self.navigation.setTabEnabled(self.pixel_tab, not processing)
        self._update_quick_actions()

    @Slot(bool)
    def _upscale_processing_changed(self, processing: bool) -> None:
        self.navigation.setTabEnabled(self.quick_tab, not processing)
        self.navigation.setTabEnabled(self.thumbnail_tab, not processing)
        self.navigation.setTabEnabled(self.sound_effect_tab, not processing)
        self.navigation.setTabEnabled(self.speech_bubble_tab, not processing)
        self.navigation.setTabEnabled(self.image_edit_tab, not processing)
        self.navigation.setTabEnabled(self.pixel_tab, not processing)
        self._update_quick_actions()

    @Slot(object)
    def _handoff_palette_to_pixel(self, colors) -> None:
        self.pixel_page.receive_palette(colors)

    @Slot()
    def _open_pixel_tab_from_edit(self) -> None:
        self.navigation.setCurrentIndex(self.pixel_tab)

    @Slot(bool)
    def _edit_processing_changed(self, processing: bool) -> None:
        self.navigation.setTabEnabled(self.quick_tab, not processing)
        self.navigation.setTabEnabled(self.thumbnail_tab, not processing)
        self.navigation.setTabEnabled(self.sound_effect_tab, not processing)
        self.navigation.setTabEnabled(self.speech_bubble_tab, not processing)
        self.navigation.setTabEnabled(self.upscale_tab, not processing)
        self.navigation.setTabEnabled(self.image_edit_tab, True)
        self.navigation.setTabEnabled(self.pixel_tab, not processing)
        self._update_quick_actions()

    @Slot(bool)
    def _pixel_processing_changed(self, processing: bool) -> None:
        self.navigation.setTabEnabled(self.quick_tab, not processing)
        self.navigation.setTabEnabled(self.thumbnail_tab, not processing)
        self.navigation.setTabEnabled(self.sound_effect_tab, not processing)
        self.navigation.setTabEnabled(self.speech_bubble_tab, not processing)
        self.navigation.setTabEnabled(self.upscale_tab, not processing)
        self.navigation.setTabEnabled(self.image_edit_tab, not processing)
        self.navigation.setTabEnabled(self.pixel_tab, True)
        self._update_quick_actions()

    @Slot(int, str, str)
    def _on_file_status(self, index: int, status: str, detail: str) -> None:
        status_label = {
            "Processing": "処理中",
            "Done": "完了",
            "Error": "エラー",
            "Missing": "元画像なし",
        }.get(status, status)
        if 0 <= index < self.file_tree.topLevelItemCount():
            item = self.file_tree.topLevelItem(index)
            item.setText(2, status_label)
            item.setToolTip(2, detail)
        self.statusBar().showMessage(detail or status_label)

    @Slot(int)
    def _set_saved_output_count(self, count: int) -> None:
        self._saved_output_count = count


    @Slot(bytes)
    def _set_clipboard(self, data: bytes) -> None:
        image = QImage()
        if not image.loadFromData(data):
            QMessageBox.warning(
                self,
                "コピーできませんでした",
                "クリップボード用画像を作成できませんでした。",
            )
            return
        QApplication.clipboard().setImage(image)
        self.statusBar().showMessage("画像をクリップボードにコピーしました")

    @Slot(int, int)
    def _on_finished(self, succeeded: int, failed: int) -> None:
        split_active = self._active_split_options.enabled
        if failed:
            if split_active:
                status = f"完了 · {self._saved_output_count}枚保存 · エラー {failed}件"
                detail = (
                    f"保存した分割画像: {self._saved_output_count}枚\n"
                    f"エラー: {failed}件\n"
                )
            else:
                status = f"完了 · 成功 {succeeded}件 · エラー {failed}件"
                detail = f"成功: {succeeded}件\nエラー: {failed}件\n"
            self.statusBar().showMessage(status)
            QMessageBox.warning(
                self,
                "一部の処理でエラーが発生しました",
                detail + "詳細は一覧のツールチップとログを確認してください。",
            )
        elif split_active:
            self.statusBar().showMessage(
                f"完了 · {self._saved_output_count}枚の分割画像を保存しました"
            )
        else:
            self.statusBar().showMessage(f"完了 · {succeeded}件を保存しました")

    def closeEvent(self, event) -> None:  # noqa: N802
        if (
            self._thread is not None
            or self._quick_preview_thread is not None
            or not self.thumbnail_page.can_close()
            or not self.sound_effect_page.can_close()
            or not self.speech_bubble_page.can_close()
            or not self.edit_page.can_close()
            or not self.upscale_page.can_close()
            or not self.pixel_page.can_close()
        ):
            QMessageBox.information(self, "処理中", "処理の完了後に閉じてください。")
            event.ignore()
            return
        self.thumbnail_page.save_state()
        self._quick_preview_generation += 1
        self._quick_preview_pending_request = None
        self.quick_preview_activity.invalidate()
        self.edit_page.cleanup()
        self.upscale_page.cleanup()
        self.pixel_page.cleanup()
        event.accept()
