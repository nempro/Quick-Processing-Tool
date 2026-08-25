from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFontDatabase,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFontComboBox,
)

from .editing import (
    CanvasBackground,
    CanvasSettings,
    EditOutputFormat,
    EditResult,
    EditService,
    EditSettings,
    FilterPreset,
    PlacementMode,
    TextPosition,
    TextSettings,
    TransparencySettings,
)
from .editing.renderer import load_normalized, render_path_preview
from .editing.service import EditProcessingError, SOURCE_FORMATS
from .editing.text import pil_to_qimage
from .ui_styles import INPUT_CONTROL_STYLE


LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FILTER_LABELS = {
    FilterPreset.NONE: "なし",
    FilterPreset.GRAYSCALE: "白黒",
    FilterPreset.SEPIA: "セピア",
    FilterPreset.BRIGHT: "明るめ",
    FilterPreset.DARK: "暗め",
    FilterPreset.WARM: "暖色",
    FilterPreset.COOL: "寒色",
    FilterPreset.SHARP: "くっきり",
    FilterPreset.SOFT: "やわらか",
    FilterPreset.FADED: "色あせ",
}
POSITION_LABELS = {
    TextPosition.TOP_LEFT: "左上",
    TextPosition.TOP_CENTER: "上中央",
    TextPosition.TOP_RIGHT: "右上",
    TextPosition.MIDDLE_LEFT: "左中央",
    TextPosition.CENTER: "中央",
    TextPosition.MIDDLE_RIGHT: "右中央",
    TextPosition.BOTTOM_LEFT: "左下",
    TextPosition.BOTTOM_CENTER: "下中央",
    TextPosition.BOTTOM_RIGHT: "右下",
}
EDIT_STYLE = INPUT_CONTROL_STYLE + """
QPlainTextEdit {
    background: #dce5ef; color: #182230; border: 1px solid #6f8094;
    border-radius: 6px; padding: 7px; selection-background-color: #315fbd;
}
QPlainTextEdit:hover { background: #cfdeec; border-color: #405b79; }
QPlainTextEdit:focus { background: white; border: 2px solid #2457b2; padding: 6px; }
QPlainTextEdit:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton { min-height: 30px; background: #f5f7fa; color: #182230;
    border: 1px solid #7b899a; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #e5edf6; border-color: #405b79; }
QPushButton:focus { background: white; border: 2px solid #2457b2; padding: 4px 9px; }
QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton#editSave { min-height: 46px; background: #315fbd; color: white;
    border-color: #315fbd; border-radius: 8px; font-size: 15px; font-weight: 700; }
QPushButton#editSave:hover { background: #284fa1; }
QPushButton#editSave:disabled { background: #d9dee6; color: #8f98a6; border-color: #d9dee6; }
QSlider::groove:horizontal { height: 7px; border-radius: 3px; background: #c8d3df; }
QSlider::sub-page:horizontal { background: #315fbd; border-radius: 3px; }
QSlider::handle:horizontal { width: 18px; margin: -6px 0; border-radius: 9px;
    background: white; border: 2px solid #315fbd; }
QSlider::handle:horizontal:hover { background: #e5edff; border-color: #173a82; }
QSlider:disabled { color: #9aa1aa; }
"""


class ElidedPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._value = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_path(self, path: Path | None) -> None:
        self._value = str(path or "")
        self.setToolTip(self._value)
        self._update()

    def _update(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._value,
                Qt.TextElideMode.ElideMiddle,
                max(80, self.width() - 4),
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update()


class CollapsibleSection(QWidget):
    expanded = Signal(bool)

    def __init__(self, title: str, description: str, content: QWidget, open_by_default: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(5)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet(
            "QToolButton { text-align: left; font-size: 14px; font-weight: 700; padding: 8px;"
            "border: 1px solid #c3ceda; background: #e8eef5; color: #182230; border-radius: 6px; }"
            "QToolButton:hover { background: #dbe6f1; border-color: #405b79; }"
            "QToolButton:focus { border: 2px solid #2457b2; padding: 7px; }"
            "QToolButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
        )
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: #667085; padding: 0 8px 3px 24px;")
        self.content = content
        self.toggle.toggled.connect(self.set_expanded)
        layout.addWidget(self.toggle)
        layout.addWidget(self.description)
        layout.addWidget(content)
        self.toggle.setChecked(open_by_default)
        self.set_expanded(open_by_default)

    @Slot(bool)
    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        self.expanded.emit(expanded)


class EditPreview(QGraphicsView):
    color_picked = Signal(int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._image = QImage()
        self._picking = False
        tile = QPixmap(24, 24)
        tile.fill(QColor("#d7dbe0"))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 12, 12, QColor("#f4f5f7"))
        painter.fillRect(12, 12, 12, 12, QColor("#f4f5f7"))
        painter.end()
        self.setBackgroundBrush(tile)

    def set_image(self, image: QImage) -> None:
        self._image = image.copy()
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self._fit()

    def clear_image(self) -> None:
        self._image = QImage()
        self._item.setPixmap(QPixmap())
        self.scene().setSceneRect(0, 0, 1, 1)

    def set_picking(self, enabled: bool) -> None:
        self._picking = enabled
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._picking and not self._image.isNull():
            point = self.mapToScene(event.position().toPoint())
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self._image.width() and 0 <= y < self._image.height():
                color = self._image.pixelColor(x, y)
                self.color_picked.emit(color.red(), color.green(), color.blue())
                event.accept()
                return
        super().mousePressEvent(event)

    def _fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class EditDropZone(QWidget):
    choose_requested = Signal()
    path_dropped = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(380, 340)
        self._empty = True
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.preview = EditPreview()
        self.preview.setAcceptDrops(False)
        self.preview.viewport().setAcceptDrops(True)
        self.preview.viewport().installEventFilter(self)
        self._stack.addWidget(self.preview)
        self.overlay = QFrame()
        self.overlay.setObjectName("editDropOverlay")
        box = QVBoxLayout(self.overlay)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("▧")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 42px; color: #315fbd;")
        self.title = QLabel("画像をここにドロップ")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 22px; font-weight: 700; color: #182230;")
        formats = QLabel("PNG / JPEG / WebP")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats.setStyleSheet("font-size: 14px; color: #667085;")
        self.choose = QPushButton("画像を選ぶ")
        self.choose.setFixedWidth(150)
        self.choose.clicked.connect(self.choose_requested)
        box.addStretch()
        box.addWidget(icon)
        box.addWidget(self.title)
        box.addWidget(formats)
        box.addSpacing(8)
        box.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignCenter)
        box.addStretch()
        self._stack.addWidget(self.overlay)
        self.set_empty(True)

    def set_empty(self, empty: bool) -> None:
        self._empty = empty
        self.set_drag_active(False)

    def set_drag_active(self, active: bool) -> None:
        self.overlay.setVisible(active or self._empty)
        self._stack.setCurrentWidget(self.overlay if active or self._empty else self.preview)
        self.title.setText("ここにドロップして画像を読み込み" if active else "画像をここにドロップ")
        self.overlay.setStyleSheet(
            "QFrame#editDropOverlay { background: rgba(226,237,255,245); border: 3px dashed #2457b2; border-radius: 12px; }"
            if active
            else "QFrame#editDropOverlay { background: #f8fafc; border: 2px dashed #8da2b8; border-radius: 12px; }"
        )

    @staticmethod
    def _path(event) -> Path | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.isEnabled() and self._path(event):
            self.set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and self._path(event):
            self.set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._path(event)
        self.set_drag_active(False)
        if path and self.isEnabled():
            self.path_dropped.emit(path)
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



class EditExportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: EditService,
        source: Path,
        folder: Path,
        settings: EditSettings,
        output_format: EditOutputFormat,
        jpeg_background: tuple[int, int, int],
        quality: int,
    ) -> None:
        super().__init__()
        self.service = service
        self.source = source
        self.folder = folder
        self.settings = settings
        self.output_format = output_format
        self.jpeg_background = jpeg_background
        self.quality = quality

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(
                self.service.export(
                    self.source,
                    self.folder,
                    self.settings,
                    self.output_format,
                    self.jpeg_background,
                    self.quality,
                )
            )
        except EditProcessingError as exc:
            LOGGER.exception("Quick edit UI export failed: %s", self.source)
            self.failed.emit(str(exc))
        except Exception:
            LOGGER.exception("Unexpected quick edit UI export failure: %s", self.source)
            self.failed.emit("加工した画像を保存できませんでした。")
        finally:
            self.finished.emit()


class QuickEditPage(QWidget):
    processing_changed = Signal(bool)

    def __init__(self, service: EditService | None = None) -> None:
        super().__init__()
        self.service = service or EditService()
        self.source_path: Path | None = None
        self.output_folder: Path | None = None
        self._output_folder_explicit = False
        self._source_size = (0, 0)
        self._source_format = ""
        self._source_size_bytes = 0
        self._source_has_alpha = False
        self._last_output: Path | None = None
        self._thread: QThread | None = None
        self._worker: EditExportWorker | None = None
        self._applying = True
        self._show_original = False
        self._history: list[EditSettings] = []
        self._history_index = -1
        self._text_color = QColor("#FFFFFF")
        self._outline_color = QColor("#000000")
        self._target_color = QColor("#FFFFFF")
        self._canvas_color = QColor("#FFFFFF")
        self._build_ui()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self.update_preview)
        self._applying = False
        self._history = [self.settings()]
        self._history_index = 0
        self._update_visibility()
        self._update_actions()

    def _build_ui(self) -> None:
        self.setStyleSheet(EDIT_STYLE)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(280)
        left_scroll.setMaximumWidth(355)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 8, 12)
        heading = QLabel("何をしますか？")
        heading.setStyleSheet("font-size: 18px; font-weight: 700; color: #182230;")
        ll.addWidget(heading)
        history_row = QHBoxLayout()
        self.undo_button = QPushButton("元に戻す")
        self.redo_button = QPushButton("やり直す")
        self.reset_button = QPushButton("加工をリセット")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.reset_button.clicked.connect(self.reset_edits)
        history_row.addWidget(self.undo_button)
        history_row.addWidget(self.redo_button)
        history_row.addWidget(self.reset_button)
        ll.addLayout(history_row)

        filter_content = QWidget()
        filter_form = QFormLayout(filter_content)
        self._configure_form(filter_form)
        self.filter_combo = QComboBox()
        for preset in FilterPreset:
            self.filter_combo.addItem(FILTER_LABELS[preset], preset.value)
        filter_form.addRow("雰囲気", self.filter_combo)
        self.filter_section = CollapsibleSection(
            "フィルター",
            "選ぶだけで画像の雰囲気を変えます",
            filter_content,
            True,
        )
        ll.addWidget(self.filter_section)

        text_content = QWidget()
        text_layout = QVBoxLayout(text_content)
        text_layout.setContentsMargins(8, 2, 4, 6)
        self.text_enabled = QCheckBox("文字を入れる")
        text_layout.addWidget(self.text_enabled)
        self.text_details = QWidget()
        text_form = QFormLayout(self.text_details)
        self._configure_form(text_form)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("例：おはよう")
        self.text_edit.setFixedHeight(72)
        self.font_combo = QFontComboBox()
        self._select_default_font(self.font_combo)
        self.font_size_spin = self._spin(8, 500, 64, " px")
        self.bold_check = QCheckBox("太字")
        self.bold_check.setChecked(True)
        self.text_color_button = QPushButton()
        self.text_color_button.clicked.connect(self.choose_text_color)
        self.outline_enabled = QCheckBox("文字の縁取りを付ける")
        self.outline_enabled.setChecked(True)
        self.outline_color_button = QPushButton()
        self.outline_color_button.clicked.connect(self.choose_outline_color)
        self.outline_width_spin = self._spin(0, 40, 4, " px")
        self.position_combo = QComboBox()
        for position in TextPosition:
            self.position_combo.addItem(POSITION_LABELS[position], position.value)
        self.position_combo.setCurrentIndex(self.position_combo.findData(TextPosition.BOTTOM_CENTER.value))
        text_form.addRow("文字", self.text_edit)
        text_form.addRow("フォント", self.font_combo)
        text_form.addRow("文字サイズ", self.font_size_spin)
        text_form.addRow("", self.bold_check)
        text_form.addRow("文字色", self.text_color_button)
        text_form.addRow("", self.outline_enabled)
        text_form.addRow("縁取りの色", self.outline_color_button)
        text_form.addRow("縁取りの太さ", self.outline_width_spin)
        text_form.addRow("位置", self.position_combo)
        preset_row = QHBoxLayout()
        white_outline = QPushButton("白文字＋黒縁")
        black_outline = QPushButton("黒文字＋白縁")
        white_outline.clicked.connect(lambda: self.apply_text_preset(True))
        black_outline.clicked.connect(lambda: self.apply_text_preset(False))
        preset_row.addWidget(white_outline)
        preset_row.addWidget(black_outline)
        text_form.addRow("組み合わせ", preset_row)
        text_layout.addWidget(self.text_details)
        self.text_section = CollapsibleSection(
            "文字を入れる",
            "日本語や改行、白文字＋黒縁に対応します",
            text_content,
        )
        ll.addWidget(self.text_section)

        transparency_content = QWidget()
        transparency_layout = QVBoxLayout(transparency_content)
        transparency_layout.setContentsMargins(8, 2, 4, 6)
        self.transparency_enabled = QCheckBox("背景を透明にする")
        transparency_layout.addWidget(self.transparency_enabled)
        self.transparency_details = QWidget()
        transparency_form = QFormLayout(self.transparency_details)
        self._configure_form(transparency_form)
        self.target_color_button = QPushButton()
        self.target_color_button.clicked.connect(self.choose_target_color)
        self.eyedropper_button = QPushButton("画像から色を選ぶ")
        self.eyedropper_button.setCheckable(True)
        self.eyedropper_button.toggled.connect(self._eyedropper_toggled)
        self.tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self.tolerance_slider.setRange(0, 255)
        self.tolerance_slider.setValue(24)
        self.tolerance_value = QLabel("24 / 255")
        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(self.tolerance_slider, 1)
        tolerance_row.addWidget(self.tolerance_value)
        self.softness_slider = QSlider(Qt.Orientation.Horizontal)
        self.softness_slider.setRange(0, 100)
        self.softness_slider.setValue(8)
        self.softness_value = QLabel("8 / 100")
        softness_row = QHBoxLayout()
        softness_row.addWidget(self.softness_slider, 1)
        softness_row.addWidget(self.softness_value)
        transparency_form.addRow("抜きたい背景色", self.target_color_button)
        transparency_form.addRow("", self.eyedropper_button)
        transparency_form.addRow("色の許容範囲", tolerance_row)
        transparency_form.addRow("境界をなじませる", softness_row)
        transparency_layout.addWidget(self.transparency_details)
        self.transparency_section = CollapsibleSection(
            "背景を透明にする",
            "単色背景を選び、近い色まで透明にします",
            transparency_content,
        )
        ll.addWidget(self.transparency_section)

        canvas_content = QWidget()
        canvas_form = QFormLayout(canvas_content)
        self._configure_form(canvas_form)
        self.canvas_preset_combo = QComboBox()
        for label, value in (
            ("元サイズ", (0, 0)),
            ("128 × 128", (128, 128)),
            ("256 × 256", (256, 256)),
            ("320 × 320", (320, 320)),
            ("512 × 512", (512, 512)),
            ("任意サイズ", "custom"),
        ):
            self.canvas_preset_combo.addItem(label, value)
        self.custom_canvas = QWidget()
        custom_row = QHBoxLayout(self.custom_canvas)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.canvas_width_spin = self._spin(1, 10000, 320, " px")
        self.canvas_height_spin = self._spin(1, 10000, 320, " px")
        custom_row.addWidget(self.canvas_width_spin)
        custom_row.addWidget(QLabel("×"))
        custom_row.addWidget(self.canvas_height_spin)
        self.placement_combo = QComboBox()
        self.placement_combo.addItem("収める", PlacementMode.FIT.value)
        self.placement_combo.addItem("いっぱいに広げる", PlacementMode.FILL.value)
        self.padding_combo = QComboBox()
        for padding in (0, 5, 10, 15, 20):
            self.padding_combo.addItem(f"{padding}%", padding)
        self.canvas_background_combo = QComboBox()
        self.canvas_background_combo.addItem("透明", CanvasBackground.TRANSPARENT.value)
        self.canvas_background_combo.addItem("白", CanvasBackground.WHITE.value)
        self.canvas_background_combo.addItem("黒", CanvasBackground.BLACK.value)
        self.canvas_background_combo.addItem("指定色", CanvasBackground.CUSTOM.value)
        self.canvas_color_button = QPushButton()
        self.canvas_color_button.clicked.connect(self.choose_canvas_color)
        canvas_form.addRow("キャンバス", self.canvas_preset_combo)
        canvas_form.addRow("任意サイズ", self.custom_canvas)
        canvas_form.addRow("画像の配置", self.placement_combo)
        canvas_form.addRow("余白", self.padding_combo)
        canvas_form.addRow("背景", self.canvas_background_combo)
        canvas_form.addRow("背景の指定色", self.canvas_color_button)
        self.canvas_section = CollapsibleSection(
            "キャンバスを整える",
            "縦横比を保ったまま中央へ配置します",
            canvas_content,
        )
        ll.addWidget(self.canvas_section)
        ll.addStretch()
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(8, 12, 8, 12)
        preview_head = QHBoxLayout()
        title = QLabel("プレビュー")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        preview_head.addWidget(title)
        preview_head.addStretch()
        self.original_button = QPushButton("元画像")
        self.edited_button = QPushButton("加工後")
        self.original_button.clicked.connect(self.show_original)
        self.edited_button.clicked.connect(self.show_edited)
        preview_head.addWidget(self.original_button)
        preview_head.addWidget(self.edited_button)
        cl.addLayout(preview_head)
        self.drop_zone = EditDropZone()
        self.drop_zone.choose_requested.connect(self.choose_image)
        self.drop_zone.path_dropped.connect(self.load_image)
        self.drop_zone.preview.color_picked.connect(self._color_picked)
        cl.addWidget(self.drop_zone, 1)
        self.preview_status = QLabel("画像を読み込むと、ここへ加工結果を表示します")
        self.preview_status.setWordWrap(True)
        self.preview_status.setStyleSheet("color: #667085;")
        cl.addWidget(self.preview_status)
        splitter.addWidget(center)

        right = QWidget()
        right.setMinimumWidth(270)
        right.setMaximumWidth(360)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 12, 12, 12)
        info_title = QLabel("画像情報 / 保存")
        info_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        rl.addWidget(info_title)
        self.original_info = QLabel("元画像\n画像を読み込んでください")
        self.output_info = QLabel("加工後\n—")
        info_style = "padding: 10px; background: #f4f6f8; border: 1px solid #d7dde5; border-radius: 8px; color: #273142;"
        for label in (self.original_info, self.output_info):
            label.setWordWrap(True)
            label.setStyleSheet(info_style)
            rl.addWidget(label)
        save_form = QFormLayout()
        self._configure_form(save_form)
        self.format_combo = QComboBox()
        self.format_combo.addItem("元の形式", EditOutputFormat.SAME.value)
        self.format_combo.addItem("PNG", EditOutputFormat.PNG.value)
        self.format_combo.addItem("JPEG", EditOutputFormat.JPEG.value)
        self.format_combo.addItem("WebP", EditOutputFormat.WEBP.value)
        self.quality_spin = self._spin(1, 100, 95, "%")
        self.jpeg_background_combo = QComboBox()
        self.jpeg_background_combo.addItem("白", (255, 255, 255))
        self.jpeg_background_combo.addItem("黒", (0, 0, 0))
        save_form.addRow("保存形式", self.format_combo)
        save_form.addRow("画質", self.quality_spin)
        save_form.addRow("JPEGの透明部分", self.jpeg_background_combo)
        rl.addLayout(save_form)
        self.alpha_hint = QLabel()
        self.alpha_hint.setWordWrap(True)
        self.alpha_hint.setStyleSheet("color: #9a6700;")
        rl.addWidget(self.alpha_hint)
        rl.addWidget(QLabel("保存先"))
        self.folder_label = ElidedPathLabel()
        rl.addWidget(self.folder_label)
        self.folder_button = QPushButton("保存先を選ぶ")
        self.folder_button.clicked.connect(self.choose_output_folder)
        rl.addWidget(self.folder_button)
        self.save_button = QPushButton("加工した画像を保存")
        self.save_button.setObjectName("editSave")
        self.save_button.clicked.connect(self.save_image)
        rl.addWidget(self.save_button)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        rl.addWidget(self.result_label)
        self.saved_box = QWidget()
        saved_layout = QVBoxLayout(self.saved_box)
        saved_layout.setContentsMargins(0, 4, 0, 0)
        saved_layout.addWidget(QLabel("実際の保存先"))
        self.saved_path = ElidedPathLabel()
        saved_layout.addWidget(self.saved_path)
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_folder_button.clicked.connect(self.open_saved_folder)
        saved_layout.addWidget(self.open_folder_button)
        self.saved_box.hide()
        rl.addWidget(self.saved_box)
        rl.addStretch()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 25)
        splitter.setStretchFactor(1, 50)
        splitter.setStretchFactor(2, 25)
        splitter.setSizes([300, 580, 300])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.sections = [
            self.filter_section,
            self.text_section,
            self.transparency_section,
            self.canvas_section,
        ]
        for section in self.sections:
            section.expanded.connect(lambda expanded, active=section: self._section_expanded(active, expanded))
        self._connect_controls()
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._update_color_button(self.target_color_button, self._target_color)
        self._update_color_button(self.canvas_color_button, self._canvas_color)

    @staticmethod
    def _configure_form(form: QFormLayout) -> None:
        form.setContentsMargins(4, 3, 4, 4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    @staticmethod
    def _select_default_font(combo: QFontComboBox) -> None:
        families = QFontDatabase.families()
        by_name = {family.casefold(): family for family in families}
        preferred = next(
            (
                by_name[name.casefold()]
                for name in ("Yu Gothic UI", "Yu Gothic", "Meiryo", "BIZ UDPGothic", "Noto Sans JP")
                if name.casefold() in by_name
            ),
            combo.currentFont().family(),
        )
        index = combo.findText(preferred)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _connect_controls(self) -> None:
        for combo in (
            self.filter_combo,
            self.position_combo,
            self.canvas_preset_combo,
            self.placement_combo,
            self.padding_combo,
            self.canvas_background_combo,
        ):
            combo.currentIndexChanged.connect(self._control_changed)
        for spin in (
            self.font_size_spin,
            self.outline_width_spin,
            self.canvas_width_spin,
            self.canvas_height_spin,
        ):
            spin.valueChanged.connect(self._control_changed)
        for check in (self.text_enabled, self.bold_check, self.outline_enabled, self.transparency_enabled):
            check.toggled.connect(self._control_changed)
        self.text_edit.textChanged.connect(self._control_changed)
        self.font_combo.currentFontChanged.connect(self._control_changed)
        self.tolerance_slider.valueChanged.connect(self._slider_changed)
        self.softness_slider.valueChanged.connect(self._slider_changed)
        self.format_combo.currentIndexChanged.connect(self._update_save_options)

    def _section_expanded(self, active: CollapsibleSection, expanded: bool) -> None:
        if not expanded:
            return
        for section in self.sections:
            if section is not active and section.toggle.isChecked():
                section.toggle.setChecked(False)

    @Slot()
    def choose_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "加工する画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if name:
            self.load_image(Path(name))

    @Slot(object)
    def load_image(self, path: Path) -> None:
        if self._thread is not None:
            return
        path = Path(path)
        try:
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise OSError("unsupported suffix")
            with Image.open(path) as opened:
                opened.load()
                normalized = ImageOps.exif_transpose(opened)
                width, height = normalized.size
                source_format = SOURCE_FORMATS.get((opened.format or "").upper())
                if source_format is None:
                    raise ValueError("unsupported decoded format")
                has_alpha = "A" in normalized.getbands() or (
                    normalized.mode == "P" and "transparency" in normalized.info
                )
        except (OSError, UnidentifiedImageError, ValueError):
            QMessageBox.warning(self, "画像を開けません", "PNG / JPEG / WebP画像を選んでください。")
            return
        self.source_path = path.resolve()
        self._source_size = (width, height)
        self._source_format = source_format
        self._source_size_bytes = self.source_path.stat().st_size
        self._source_has_alpha = has_alpha
        if not self._output_folder_explicit:
            self.output_folder = self.source_path.parent
        self.folder_label.set_path(self.output_folder)
        self.original_info.setText(
            f"元画像\n{width} × {height} / {source_format} / {self._human_bytes(self._source_size_bytes)}\n{self.source_path.name}"
        )
        self.drop_zone.set_empty(False)
        self._applying = True
        self.apply_settings(EditSettings())
        self._applying = False
        self._history = [self.settings()]
        self._history_index = 0
        self._last_output = None
        self.saved_box.hide()
        self.result_label.clear()
        self._show_original = False
        self._update_actions()
        self.update_preview()

    def settings(self) -> EditSettings:
        canvas_data = self.canvas_preset_combo.currentData()
        if canvas_data == "custom":
            canvas_width = self.canvas_width_spin.value()
            canvas_height = self.canvas_height_spin.value()
        else:
            canvas_width, canvas_height = canvas_data
        return EditSettings(
            filter_preset=FilterPreset(self.filter_combo.currentData()),
            transparency=TransparencySettings(
                self.transparency_enabled.isChecked(),
                (self._target_color.red(), self._target_color.green(), self._target_color.blue()),
                self.tolerance_slider.value(),
                self.softness_slider.value(),
            ),
            canvas=CanvasSettings(
                canvas_width,
                canvas_height,
                PlacementMode(self.placement_combo.currentData()),
                self.padding_combo.currentData(),
                CanvasBackground(self.canvas_background_combo.currentData()),
                (
                    self._canvas_color.red(),
                    self._canvas_color.green(),
                    self._canvas_color.blue(),
                    self._canvas_color.alpha(),
                ),
            ),
            text=TextSettings(
                self.text_enabled.isChecked(),
                self.text_edit.toPlainText(),
                self.font_combo.currentFont().family(),
                self.font_size_spin.value(),
                self.bold_check.isChecked(),
                (self._text_color.red(), self._text_color.green(), self._text_color.blue(), self._text_color.alpha()),
                self.outline_enabled.isChecked(),
                (
                    self._outline_color.red(),
                    self._outline_color.green(),
                    self._outline_color.blue(),
                    self._outline_color.alpha(),
                ),
                self.outline_width_spin.value(),
                TextPosition(self.position_combo.currentData()),
                24,
            ),
        )

    def apply_settings(self, settings: EditSettings) -> None:
        was_applying = self._applying
        self._applying = True
        self.filter_combo.setCurrentIndex(self.filter_combo.findData(settings.filter_preset.value))
        self.transparency_enabled.setChecked(settings.transparency.enabled)
        self._target_color = QColor(*settings.transparency.target_color)
        self.tolerance_slider.setValue(settings.transparency.tolerance)
        self.softness_slider.setValue(settings.transparency.edge_softness)
        canvas_index = self.canvas_preset_combo.findData((settings.canvas.width, settings.canvas.height))
        if settings.canvas.width == 0 and settings.canvas.height == 0:
            canvas_index = 0
        elif canvas_index < 0:
            canvas_index = self.canvas_preset_combo.findData("custom")
        self.canvas_preset_combo.setCurrentIndex(canvas_index)
        if settings.canvas.width:
            self.canvas_width_spin.setValue(settings.canvas.width)
        if settings.canvas.height:
            self.canvas_height_spin.setValue(settings.canvas.height)
        self.placement_combo.setCurrentIndex(self.placement_combo.findData(settings.canvas.placement.value))
        self.padding_combo.setCurrentIndex(self.padding_combo.findData(settings.canvas.padding_percent))
        self.canvas_background_combo.setCurrentIndex(
            self.canvas_background_combo.findData(settings.canvas.background.value)
        )
        self._canvas_color = QColor(*settings.canvas.custom_color)
        self.text_enabled.setChecked(settings.text.enabled)
        self.text_edit.setPlainText(settings.text.text)
        self.font_combo.setCurrentFont(self.font_combo.currentFont())
        font_index = self.font_combo.findText(settings.text.font_family)
        if font_index >= 0:
            self.font_combo.setCurrentIndex(font_index)
        self.font_size_spin.setValue(settings.text.font_size)
        self.bold_check.setChecked(settings.text.bold)
        self._text_color = QColor(*settings.text.color)
        self.outline_enabled.setChecked(settings.text.outline_enabled)
        self._outline_color = QColor(*settings.text.outline_color)
        self.outline_width_spin.setValue(settings.text.outline_width)
        self.position_combo.setCurrentIndex(self.position_combo.findData(settings.text.position.value))
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._update_color_button(self.target_color_button, self._target_color)
        self._update_color_button(self.canvas_color_button, self._canvas_color)
        self._update_visibility()
        self._applying = was_applying
        if not self._applying:
            self._show_original = False
            self.schedule_preview()
            self._update_actions()

    @Slot()
    def _control_changed(self, *_args) -> None:
        if self._applying:
            return
        self._update_visibility()
        current = self.settings()
        if self._history_index < 0 or current != self._history[self._history_index]:
            self._history = self._history[: self._history_index + 1]
            self._history.append(current)
            if len(self._history) > 60:
                self._history.pop(0)
            self._history_index = len(self._history) - 1
        self._show_original = False
        self.saved_box.hide()
        self.result_label.clear()
        self.schedule_preview()
        self._update_actions()

    @Slot()
    def _slider_changed(self, *_args) -> None:
        self.tolerance_value.setText(f"{self.tolerance_slider.value()} / 255")
        self.softness_value.setText(f"{self.softness_slider.value()} / 100")
        self._control_changed()

    def _update_visibility(self) -> None:
        self.text_details.setVisible(self.text_enabled.isChecked())
        self.outline_color_button.setEnabled(self.outline_enabled.isChecked())
        self.outline_width_spin.setEnabled(self.outline_enabled.isChecked())
        self.transparency_details.setVisible(self.transparency_enabled.isChecked())
        self.custom_canvas.setVisible(self.canvas_preset_combo.currentData() == "custom")
        self.canvas_color_button.setVisible(
            self.canvas_background_combo.currentData() == CanvasBackground.CUSTOM.value
        )
        self._update_save_options()

    def schedule_preview(self) -> None:
        if self.source_path:
            self._preview_timer.start()

    @Slot()
    def update_preview(self) -> None:
        if not self.source_path:
            self.drop_zone.preview.clear_image()
            return
        try:
            if self._show_original:
                image = load_normalized(self.source_path)
                image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            else:
                image = render_path_preview(self.source_path, self.settings(), 1400)
            self.drop_zone.preview.set_image(pil_to_qimage(image))
            self.preview_status.setStyleSheet("color: #667085;")
            self.preview_status.setText(
                ("元画像" if self._show_original else "加工後")
                + f" · Preview {image.width} × {image.height}"
            )
            self._update_output_info()
        except Exception as exc:
            LOGGER.exception("Quick edit preview failed: %s", self.source_path)
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(f"プレビューを更新できませんでした: {exc}")

    def _update_output_info(self) -> None:
        if not self.source_path:
            self.output_info.setText("加工後\n—")
            return
        canvas = self.settings().canvas
        width = canvas.width or self._source_size[0]
        height = canvas.height or self._source_size[1]
        selected = EditOutputFormat(self.format_combo.currentData())
        output_format = self._source_format if selected is EditOutputFormat.SAME else selected.value
        alpha_text = "透明部分あり" if self.settings().transparency.enabled or self._source_has_alpha else "不透明"
        self.output_info.setText(f"加工後（見込み）\n{width} × {height} / {output_format}\n{alpha_text}")

    @Slot()
    def undo(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def redo(self) -> None:
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def reset_edits(self) -> None:
        if not self.source_path:
            return
        default = EditSettings()
        if default != self.settings():
            self._history = self._history[: self._history_index + 1]
            self._history.append(default)
            self._history_index = len(self._history) - 1
            self.apply_settings(default)

    @Slot()
    def show_original(self) -> None:
        if self.source_path:
            self._show_original = True
            self.update_preview()

    @Slot()
    def show_edited(self) -> None:
        if self.source_path:
            self._show_original = False
            self.update_preview()

    def apply_text_preset(self, white_text: bool) -> None:
        self._text_color = QColor("#FFFFFF" if white_text else "#000000")
        self._outline_color = QColor("#000000" if white_text else "#FFFFFF")
        self.outline_enabled.setChecked(True)
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._control_changed()

    @Slot()
    def choose_text_color(self) -> None:
        self._choose_color("文字色を選ぶ", "_text_color", self.text_color_button)

    @Slot()
    def choose_outline_color(self) -> None:
        self._choose_color("縁取りの色を選ぶ", "_outline_color", self.outline_color_button)

    @Slot()
    def choose_target_color(self) -> None:
        self._choose_color("透明にする背景色を選ぶ", "_target_color", self.target_color_button)

    @Slot()
    def choose_canvas_color(self) -> None:
        self._choose_color("キャンバスの背景色を選ぶ", "_canvas_color", self.canvas_color_button)

    def _choose_color(self, title: str, attribute: str, button: QPushButton) -> None:
        current = getattr(self, attribute)
        color = QColorDialog.getColor(current, self, title, QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            setattr(self, attribute, color)
            self._update_color_button(button, color)
            self._control_changed()

    @staticmethod
    def _update_color_button(button: QPushButton, color: QColor) -> None:
        contrast = "#000000" if color.lightness() > 150 else "#FFFFFF"
        button.setText(color.name().upper())
        button.setStyleSheet(
            f"QPushButton {{ background: {color.name()}; color: {contrast}; border: 1px solid #65768a;"
            "border-radius: 6px; min-height: 30px; font-weight: 700; }"
            "QPushButton:hover { border: 2px solid #2457b2; }"
            "QPushButton:focus { border: 2px solid #173a82; }"
            "QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
        )

    @Slot(bool)
    def _eyedropper_toggled(self, enabled: bool) -> None:
        self.drop_zone.preview.set_picking(enabled)
        self.eyedropper_button.setText("画像上の色をクリック" if enabled else "画像から色を選ぶ")
        if enabled:
            self.preview_status.setText("透明にしたい背景色を画像上でクリックしてください")

    @Slot(int, int, int)
    def _color_picked(self, red: int, green: int, blue: int) -> None:
        self._target_color = QColor(red, green, blue)
        self._update_color_button(self.target_color_button, self._target_color)
        self.transparency_enabled.setChecked(True)
        self.eyedropper_button.setChecked(False)
        self._control_changed()

    @Slot()
    def choose_output_folder(self) -> None:
        initial = str(self.output_folder or (self.source_path.parent if self.source_path else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "加工した画像の保存先を選ぶ", initial)
        if folder:
            self.output_folder = Path(folder).resolve()
            self._output_folder_explicit = True
            self.folder_label.set_path(self.output_folder)

    def _update_save_options(self, *_args) -> None:
        selected = EditOutputFormat(self.format_combo.currentData())
        source_is_jpeg = self._source_format in {"JPEG", "JPG"}
        jpeg = selected is EditOutputFormat.JPEG or (selected is EditOutputFormat.SAME and source_is_jpeg)
        self.jpeg_background_combo.setVisible(jpeg)
        self.quality_spin.setVisible(selected is not EditOutputFormat.PNG)
        transparent = self.transparency_enabled.isChecked() or self._source_has_alpha
        self.alpha_hint.setText("透明部分を保存する場合はPNGがおすすめです" if transparent and jpeg else "")
        self._update_output_info()

    @Slot()
    def save_image(self) -> None:
        if not self.source_path or self._thread is not None:
            return
        folder = self.output_folder or self.source_path.parent
        if folder.exists() and not folder.is_dir():
            QMessageBox.warning(self, "保存先を使用できません", "保存先フォルダーを選び直してください。")
            return
        self.result_label.setStyleSheet("color: #315fbd; font-weight: 700;")
        self.result_label.setText("加工した画像を保存しています…")
        self.saved_box.hide()
        self._set_processing(True)
        self._thread = QThread(self)
        self._worker = EditExportWorker(
            self.service,
            self.source_path,
            folder,
            self.settings(),
            EditOutputFormat(self.format_combo.currentData()),
            self.jpeg_background_combo.currentData(),
            self.quality_spin.value(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_saved)
        self._worker.failed.connect(self._on_save_failed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker_refs)
        self._thread.start()

    @Slot(object)
    def _on_saved(self, result: EditResult) -> None:
        self._last_output = result.output_path
        self.result_label.setStyleSheet("color: #137333; font-weight: 700;")
        self.result_label.setText(f"✓ 加工した画像を保存しました\n{result.output_path.name}")
        self.saved_path.set_path(result.output_path.parent)
        self.saved_box.show()
        alpha_text = "Alpha保持" if result.has_alpha else "不透明"
        self.output_info.setText(
            f"加工後（保存済み）\n{result.width} × {result.height} / {result.output_format}\n"
            f"{self._human_bytes(result.size_bytes)} / {alpha_text}"
        )

    @Slot(str)
    def _on_save_failed(self, message: str) -> None:
        self.result_label.setStyleSheet("color: #c62828; font-weight: 700;")
        self.result_label.setText(message)

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self._set_processing(False)

    def _set_processing(self, processing: bool) -> None:
        for widget in (
            self.drop_zone,
            self.filter_combo,
            self.text_enabled,
            self.text_details,
            self.transparency_enabled,
            self.transparency_details,
            self.canvas_preset_combo,
            self.custom_canvas,
            self.placement_combo,
            self.padding_combo,
            self.canvas_background_combo,
            self.canvas_color_button,
            self.undo_button,
            self.redo_button,
            self.reset_button,
            self.format_combo,
            self.quality_spin,
            self.jpeg_background_combo,
            self.folder_button,
            self.save_button,
        ):
            widget.setEnabled(not processing)
        for section in self.sections:
            section.toggle.setEnabled(not processing)
        self.processing_changed.emit(processing)
        if not processing:
            self._update_visibility()
            self._update_actions()

    def _update_actions(self) -> None:
        loaded = self.source_path is not None
        idle = self._thread is None
        self.undo_button.setEnabled(loaded and idle and self._history_index > 0)
        self.redo_button.setEnabled(loaded and idle and self._history_index + 1 < len(self._history))
        self.reset_button.setEnabled(loaded and idle and self.settings() != EditSettings())
        self.save_button.setEnabled(loaded and idle)
        self.folder_button.setEnabled(loaded and idle)
        self.original_button.setEnabled(loaded and not self._show_original)
        self.edited_button.setEnabled(loaded and self._show_original)

    @Slot()
    def open_saved_folder(self) -> None:
        folder = self._last_output.parent if self._last_output else None
        if not folder or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "Windows Explorerで保存先を開けませんでした。")

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
