from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea, QCheckBox, QComboBox, QFileDialog, QFrame,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QToolButton,
    QButtonGroup, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .pixel_editor.canvas import MAX_SIZE, MIN_SIZE, PixelCanvas, pixel_from_display
from .pixel_editor.importers import SUPPORTED_IMAGE_FORMATS, load_reference, load_rgba
from .pixel_editor.models import PixelExportResult, PixelTool, ReferenceImage
from .pixel_editor.service import PixelExportError, save_png
from .naming import normalize_filename_stem
from .ui_styles import INPUT_CONTROL_STYLE
from .color_picker import choose_color
from .errors import ProcessingError
from .image_workspace import MISSING_SOURCE_MESSAGE, MissingSourceError, SourceImage, read_source_image
from .source_ui import CurrentSourceCard
from .preview_activity import PreviewActivityIndicator


def _qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()


SUPPORTED_DROP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
INVALID_IMAGE_MESSAGE = "PNG / JPEG / WebP画像を選んでください。"


class PixelImportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, request) -> None:
        super().__init__()
        self.request = request

    @Slot()
    def run(self) -> None:
        generation, request_id, mode, path, identity, canvas_size, opacity, activity_token = self.request
        try:
            if mode == "reference":
                reference = load_reference(path, canvas_size, opacity)
                pixels = reference.image.tobytes()
            else:
                canvas = PixelCanvas(*canvas_size)
                canvas.import_image(load_rgba(path), commit=False)
                pixels = canvas.snapshot()
            self.succeeded.emit((generation, request_id, mode, path, identity, canvas_size, opacity, activity_token, pixels))
        except Exception as exc:
            message = MISSING_SOURCE_MESSAGE if not path.is_file() else f"画像を読み込めませんでした: {exc}"
            self.failed.emit((generation, request_id, mode, path, identity, canvas_size, opacity, activity_token, message))
        finally:
            self.finished.emit()


def local_image_paths(mime_data) -> list[Path]:
    if not mime_data.hasUrls():
        return []
    return [
        Path(url.toLocalFile())
        for url in mime_data.urls()
        if url.isLocalFile()
        and Path(url.toLocalFile()).is_file()
        and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_DROP_SUFFIXES
    ]


class ElidedValueLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._value = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)

    def set_value(self, value: str, tooltip: str | None = None) -> None:
        self._value = value
        self.setToolTip(tooltip if tooltip is not None else value)
        self._update_text()

    def set_path(self, path: Path | None) -> None:
        self.set_value(str(path or ""))

    def _update_text(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._value,
                Qt.TextElideMode.ElideMiddle,
                max(80, self.width() - 4),
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_text()


class PixelCanvasView(QAbstractScrollArea):
    changed = Signal()
    color_picked = Signal(QColor)
    coordinates_changed = Signal(str)
    drop_requested = Signal(str)

    def __init__(self, canvas: PixelCanvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.zoom_factor = 4
        self.tool = PixelTool.PENCIL
        self.color = (0, 0, 0, 255)
        self.pencil_size = 1
        self.grid_enabled = True
        self.reference: ReferenceImage | None = None
        self.reference_visible = True
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(240, 240)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)

    def eventFilter(self, watched, event):
        if watched is self.viewport():
            if event.type() == QEvent.DragEnter:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.DragLeave:
                self.dragLeaveEvent(event)
                return True
            if event.type() == QEvent.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(watched, event)

    def set_canvas(self, canvas: PixelCanvas):
        self.canvas = canvas
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._update_scrollbars()
        self.viewport().update()

    def set_zoom(self, zoom: int):
        self.zoom_factor = max(1, int(zoom))
        self._update_scrollbars()
        self.viewport().update()

    def _content_size(self):
        return self.canvas.width * self.zoom_factor, self.canvas.height * self.zoom_factor

    def _update_scrollbars(self):
        cw, ch = self._content_size()
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.horizontalScrollBar().setRange(0, max(0, cw - self.viewport().width()))
        self.verticalScrollBar().setRange(0, max(0, ch - self.viewport().height()))

    def resizeEvent(self, event):
        self._update_scrollbars()
        super().resizeEvent(event)

    def _origin(self):
        cw, ch = self._content_size()
        vw, vh = self.viewport().width(), self.viewport().height()
        ox = (vw - cw) // 2 if cw <= vw else -self.horizontalScrollBar().value()
        oy = (vh - ch) // 2 if ch <= vh else -self.verticalScrollBar().value()
        return ox, oy

    def pixel_at(self, position: QPoint):
        ox, oy = self._origin()
        return pixel_from_display(position.x(), position.y(), origin_x=ox, origin_y=oy, zoom=self.zoom_factor, width=self.canvas.width, height=self.canvas.height)

    def dragEnterEvent(self, event):
        if local_image_paths(event.mimeData()):
            self.setProperty("dragActive", True)
            self.viewport().setProperty("dragActive", True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", False)
        self.viewport().setProperty("dragActive", False)
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):
        paths = local_image_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.setProperty("dragActive", False)
        self.viewport().setProperty("dragActive", False)
        self.drop_requested.emit(str(paths[0]))
        event.acceptProposedAction()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#d9d9d9"))
        ox, oy = self._origin()
        rect = QRect(ox, oy, self.canvas.width * self.zoom_factor, self.canvas.height * self.zoom_factor)
        tile = max(4, self.zoom_factor * 2)
        for y in range(rect.top(), rect.bottom() + 1, tile):
            for x in range(rect.left(), rect.right() + 1, tile):
                painter.fillRect(QRect(x, y, tile, tile), QColor("#eeeeee" if ((x // tile + y // tile) & 1) else "#cccccc"))
        if self.reference is not None and self.reference_visible:
            painter.setOpacity(self.reference.opacity / 100.0)
            painter.drawImage(rect, _qimage(self.reference.image), QRect(0, 0, self.reference.image.width, self.reference.image.height))
            painter.setOpacity(1.0)
        painter.drawImage(rect, _qimage(self.canvas.image), QRect(0, 0, self.canvas.width, self.canvas.height))
        if self.grid_enabled and self.zoom_factor >= 4:
            pen = QPen(QColor(0, 0, 0, 50))
            pen.setWidth(1)
            painter.setPen(pen)
            for x in range(ox, ox + rect.width() + 1, self.zoom_factor):
                painter.drawLine(x, oy, x, oy + rect.height())
            for y in range(oy, oy + rect.height() + 1, self.zoom_factor):
                painter.drawLine(ox, y, ox + rect.width(), y)

    def _apply_point(self, point):
        px = self.pixel_at(point)
        if px is None:
            return False
        self.coordinates_changed.emit(f"{px[0]}, {px[1]}")
        if self.tool == PixelTool.EYEDROPPER:
            rgba = self.canvas.pixel(*px)
            self.color = rgba
            self.color_picked.emit(QColor(*rgba))
            return False
        erase = self.tool == PixelTool.ERASER
        if self._last_pixel is None:
            self._last_pixel = px
        self.canvas.stroke(
            self._last_pixel,
            px,
            self.color,
            size=self.pencil_size,
            erase=erase,
            commit=False,
        )
        self._last_pixel = px
        self.changed.emit()
        self.viewport().update()
        return True

    def mousePressEvent(self, event):
        self._last_pixel = None
        self._stroke_active = False
        self._right_erase_active = False
        if event.button() == Qt.RightButton:
            old = self.tool
            self.tool = PixelTool.ERASER
            self._right_erase_active = True
            self._stroke_active = self._apply_point(event.position().toPoint())
            self.tool = old
        elif event.button() == Qt.LeftButton:
            self._stroke_active = self._apply_point(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._stroke_active and event.buttons() & (Qt.LeftButton | Qt.RightButton):
            old = self.tool
            if self._right_erase_active: self.tool = PixelTool.ERASER
            self._apply_point(event.position().toPoint())
            self.tool = old
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._stroke_active and event.button() in (Qt.LeftButton, Qt.RightButton):
            self.canvas.history.commit(self.canvas.snapshot())
            self._stroke_active = False
            self._right_erase_active = False
            self.changed.emit()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            choices = [1, 2, 4, 8, 16]
            current = min(range(len(choices)), key=lambda i: abs(choices[i] - self.zoom_factor))
            direction = 1 if event.angleDelta().y() > 0 else -1
            self.set_zoom(choices[max(0, min(len(choices) - 1, current + direction))])
            event.accept()
            return
        super().wheelEvent(event)


class PixelEditorPage(QWidget):
    processing_changed = Signal(bool)
    source_change_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workspace_managed = False
        self._current_source: SourceImage | None = None
        self.source_path: Path | None = None
        self.output_folder: Path | None = None
        self._last_saved_result: PixelExportResult | None = None
        self._received_palette: tuple[tuple[int, int, int], ...] = ()
        self._palette_selected_index = -1
        self._palette_chip_buttons: list[QToolButton] = []
        self.reference: ReferenceImage | None = None
        self._import_thread: QThread | None = None
        self._import_worker: PixelImportWorker | None = None
        self._import_generation = 0
        self._import_request_id = 0
        self._import_active_request = None
        self._import_pending_request = None
        self._import_active_result = None
        self._import_activity_token: int | None = None
        self.canvas = PixelCanvas()
        self.canvas_view = PixelCanvasView(self.canvas)
        self._palette_button_group = QButtonGroup(self)
        self._palette_button_group.setExclusive(True)
        self._build_ui()

    def _build_ui(self):
        self.setAcceptDrops(True)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        left = QScrollArea()
        left.setObjectName("pixelSettingsScroll")
        left.setWidgetResizable(True)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)

        self.current_source_card = CurrentSourceCard(
            title_text="元にする画像",
            empty_button_text="画像を選ぶ",
            source_button_text="別の画像を選ぶ",
            button_on_separate_row=True,
        )
        self.current_source_card.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum
        )
        self.current_source_card.change_requested.connect(self.choose_current_source)
        left_layout.addWidget(self.current_source_card)

        self.document_summary = QFrame()
        self.document_summary.setObjectName("pixelDocumentSummary")
        self.document_summary.setMinimumWidth(0)
        self.document_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum
        )
        document_layout = QVBoxLayout(self.document_summary)
        document_layout.setContentsMargins(7, 5, 7, 5)
        document_layout.setSpacing(1)
        document_first_row = QHBoxLayout()
        document_first_row.setContentsMargins(0, 0, 0, 0)
        document_first_row.setSpacing(4)
        document_heading = QLabel("編集中のドット絵")
        document_heading.setMinimumWidth(0)
        document_heading.setStyleSheet("font-weight: 700; color: #182230; border: 0;")
        document_first_row.addWidget(document_heading)
        self.document_name_label = ElidedValueLabel()
        self.document_name_label.setStyleSheet("font-weight: 650; color: #273142; border: 0;")
        document_first_row.addWidget(self.document_name_label, 1)
        document_layout.addLayout(document_first_row)
        self.document_meta_label = QLabel()
        self.document_meta_label.setStyleSheet("color: #667085; border: 0;")
        document_layout.addWidget(self.document_meta_label)
        self.document_summary.setStyleSheet(
            "QFrame#pixelDocumentSummary { background: #fafbfc; border: 1px solid #d7dde5; "
            "border-radius: 8px; }"
        )
        left_layout.addWidget(self.document_summary)

        self.source_change_notice = QLabel()
        self.source_change_notice.setObjectName("pixelSourceChangeNotice")
        self.source_change_notice.setMinimumWidth(0)
        notice_policy = QSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        notice_policy.setHeightForWidth(True)
        self.source_change_notice.setSizePolicy(notice_policy)
        self.source_change_notice.setWordWrap(True)
        self.source_change_notice.setStyleSheet(
            "QLabel#pixelSourceChangeNotice { color: #174a9c; background: #eef5ff; "
            "border: 1px solid #6b94d6; border-radius: 6px; padding: 5px 7px; }"
        )
        self.source_change_notice.hide()
        left_layout.addWidget(self.source_change_notice)
        self.current_source_usage = QFrame()
        self.current_source_usage.setObjectName("pixelCurrentSourceUsage")
        current_source_actions = QVBoxLayout(self.current_source_usage)
        current_source_actions.setContentsMargins(7, 6, 7, 7)
        current_source_actions.setSpacing(4)
        self.current_source_usage_heading = QLabel("この画像を使う")
        self.current_source_usage_heading.setStyleSheet("font-weight: 700; color: #182230;")
        self.current_source_usage_guidance = QLabel()
        self.current_source_usage_guidance.setWordWrap(True)
        self.current_reference_button = QPushButton("下絵にする")
        self.current_pixels_button = QPushButton("ドット化")
        for button in (self.current_reference_button, self.current_pixels_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(30)
        self.current_reference_button.clicked.connect(self.use_current_as_reference)
        self.current_pixels_button.clicked.connect(self.use_current_as_pixels)
        self.current_reference_button.setAccessibleName("現在の画像を下絵として使う")
        self.current_pixels_button.setAccessibleName("現在の画像をドット化して編集")
        self.current_reference_button.setEnabled(False)
        self.current_pixels_button.setEnabled(False)
        current_source_actions.addWidget(self.current_source_usage_heading)
        current_source_actions.addWidget(self.current_source_usage_guidance)
        source_use_row = QHBoxLayout()
        source_use_row.setSpacing(4)
        for button in (self.current_reference_button, self.current_pixels_button):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setStyleSheet("font-size: 11px; padding: 3px 2px; min-height: 30px;")
            source_use_row.addWidget(button, 1)
        current_source_actions.addLayout(source_use_row)
        self.current_source_usage.hide()
        left_layout.addWidget(self.current_source_usage)

        tools = QGroupBox("ツール")
        self.tools_group = tools
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(7, 7, 7, 7)
        tl.setSpacing(5)
        self.pencil_button = QPushButton("鉛筆")
        self.eraser_button = QPushButton("消しゴム")
        self.eyedropper_button = QPushButton("スポイト")
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self._tool_buttons = {
            PixelTool.PENCIL: self.pencil_button,
            PixelTool.ERASER: self.eraser_button,
            PixelTool.EYEDROPPER: self.eyedropper_button,
        }
        tool_row = QHBoxLayout()
        tool_row.setSpacing(0)
        tool_help = {
            PixelTool.PENCIL: "鉛筆で描画します",
            PixelTool.ERASER: "描いたドットを消します",
            PixelTool.EYEDROPPER: "キャンバスから色を選びます",
        }
        for tool, button in self._tool_buttons.items():
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setToolTip(tool_help[tool])
            button.setAccessibleName(f"描画ツール: {button.text()}")
            self.tool_group.addButton(button)
            button.clicked.connect(lambda checked=False, t=tool: self._set_tool(t))
            tool_row.addWidget(button, 1)
        tools.setStyleSheet(
            "QPushButton { min-height: 30px; padding: 3px 2px; border-radius: 3px; }"
            "QPushButton:checked { background: #315fbd; color: white; border: 2px solid #173a82; font-weight: 700; }"
        )
        tl.addLayout(tool_row)
        self.pencil_button.setChecked(True)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("太さ"))
        self.size_combo = QComboBox()
        self.size_combo.addItems(["1", "2", "3"])
        size_row.addWidget(self.size_combo, 1)
        tl.addLayout(size_row)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("現在色"))
        self.current_color_button = QPushButton()
        self.current_color_button.clicked.connect(self.choose_current_color)
        color_row.addWidget(self.current_color_button, 1)
        tl.addLayout(color_row)

        canvas_group = QGroupBox("キャンバス")
        self.canvas_group = canvas_group
        cl = QVBoxLayout(canvas_group)
        cl.setContentsMargins(7, 7, 7, 7)
        cl.setSpacing(5)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(0)
        self.preset_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.preset_combo.addItems(["32 × 32", "64 × 64", "128 × 128", "カスタム"])
        self.preset_combo.setCurrentIndex(2)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset_combo, 1)
        self.width_spin = QSpinBox()
        self.width_spin.setMinimumWidth(0)
        self.width_spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.width_spin.setRange(MIN_SIZE, MAX_SIZE)
        self.width_spin.setValue(128)
        self.height_spin = QSpinBox()
        self.height_spin.setMinimumWidth(0)
        self.height_spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.height_spin.setRange(MIN_SIZE, MAX_SIZE)
        self.height_spin.setValue(128)
        self.new_button = QPushButton("新規キャンバス")
        self.new_button.clicked.connect(self.new_canvas)
        self.new_button.setMinimumWidth(110)
        self.new_button.setMinimumHeight(30)
        self.new_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.new_button.setStyleSheet("font-size: 11px; padding: 3px 2px;")
        preset_row.addWidget(self.new_button)
        cl.addLayout(preset_row)
        self.custom_size_widget = QWidget()
        custom_size_row = QHBoxLayout(self.custom_size_widget)
        custom_size_row.setContentsMargins(0, 0, 0, 0)
        custom_size_row.addWidget(QLabel("幅"))
        custom_size_row.addWidget(self.width_spin, 1)
        custom_size_row.addWidget(QLabel("高さ"))
        custom_size_row.addWidget(self.height_spin, 1)
        self.custom_size_widget.hide()
        cl.addWidget(self.custom_size_widget)

        palette_group = QGroupBox("パレット")
        self.palette_group = palette_group
        pl = QVBoxLayout(palette_group)
        self.palette_status_label = QLabel("画像加工からパレットを受け取ると、ここに並びます。")
        self.palette_status_label.setWordWrap(True)
        pl.addWidget(self.palette_status_label)
        self.palette_contract_label = QLabel("パレットの色だけを受け取ります。元画像は移動しません。")
        self.palette_contract_label.setWordWrap(True)
        self.palette_contract_label.setStyleSheet("color: #667085;")
        pl.addWidget(self.palette_contract_label)
        self.palette_guidance_label = QLabel("")
        self.palette_guidance_label.setWordWrap(True)
        self.palette_guidance_label.setStyleSheet("color: #667085;")
        pl.addWidget(self.palette_guidance_label)
        self.palette_chips_widget = QWidget()
        self.palette_chips_layout = QGridLayout(self.palette_chips_widget)
        self.palette_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_chips_layout.setHorizontalSpacing(4)
        self.palette_chips_layout.setVerticalSpacing(6)
        pl.addWidget(self.palette_chips_widget)
        palette_footer = QHBoxLayout()
        self.palette_count_label = QLabel("0色")
        palette_footer.addWidget(self.palette_count_label)
        palette_footer.addStretch(1)
        self.palette_clear_button = QPushButton("パレットをクリア")
        self.palette_clear_button.clicked.connect(self.clear_palette)
        palette_footer.addWidget(self.palette_clear_button)
        pl.addLayout(palette_footer)

        view_group = QGroupBox("表示")
        self.view_group = view_group
        vl = QVBoxLayout(view_group)
        vl.setContentsMargins(7, 7, 7, 7)
        vl.setSpacing(5)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["Fit", "2x", "4x", "8x", "16x"])
        self.zoom_combo.setCurrentIndex(2)
        self.zoom_combo.currentIndexChanged.connect(self._zoom_changed)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("倍率"))
        view_row.addWidget(self.zoom_combo, 1)
        self.grid_check = QCheckBox("グリッド")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(lambda value: setattr(self.canvas_view, "grid_enabled", value) or self.canvas_view.viewport().update())
        view_row.addWidget(self.grid_check)
        vl.addLayout(view_row)
        self.undo_button = QPushButton("元に戻す")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = QPushButton("やり直す")
        self.redo_button.clicked.connect(self.redo)
        self.clear_button = QPushButton("全消去")
        self.clear_button.clicked.connect(self.clear)
        history_row = QHBoxLayout()
        for button in (self.undo_button, self.redo_button):
            button.setMinimumWidth(0)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            history_row.addWidget(button, 1)
        self.undo_button.setToolTip("直前の操作を元に戻します")
        self.undo_button.setAccessibleName("元に戻す")
        self.redo_button.setToolTip("元に戻した操作をやり直します")
        self.redo_button.setAccessibleName("やり直す")
        vl.addLayout(history_row)

        # Keep the high-frequency controls in the first viewport and destructive
        # canvas clearing separate at the bottom.
        left_layout.addWidget(tools)
        left_layout.addWidget(view_group)
        left_layout.addWidget(canvas_group)
        left_layout.addWidget(palette_group)
        clear_frame = QFrame()
        clear_frame.setObjectName("pixelClearActions")
        clear_layout = QVBoxLayout(clear_frame)
        clear_layout.setContentsMargins(5, 7, 5, 5)
        clear_layout.addWidget(self.clear_button)
        clear_frame.setStyleSheet(
            "QFrame#pixelClearActions { border-top: 1px solid #d7dde5; background: transparent; }"
            "QPushButton { min-height: 26px; padding: 4px 8px; color: #8a2930; "
            "background: #faf6f6; border: 1px solid #c9a8ab; }"
            "QPushButton:hover { background: #f6e8e9; border-color: #a85d63; }"
        )
        left_layout.addWidget(clear_frame)
        left_layout.addStretch(1)
        left.setWidget(left_widget)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.coord_label = QLabel("- ")
        center_layout.addWidget(self.coord_label)
        center_layout.addWidget(self.canvas_view, 1)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setObjectName("pixelPreview")
        self.preview_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("pixelPreviewScroll")
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setAlignment(Qt.AlignCenter)
        self.preview_scroll.setMinimumSize(190, 210)
        self.preview_scroll.setWidget(self.preview_label)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("実寸プレビュー"))
        self.preview_activity = PreviewActivityIndicator()
        rl.addWidget(self.preview_activity)
        self.preview_size_label = QLabel()
        rl.addWidget(self.preview_size_label)
        self.preview_hint_label = QLabel("現在色: #000000")
        self.preview_hint_label.setWordWrap(True)
        rl.addWidget(self.preview_hint_label)
        rl.addWidget(self.preview_scroll, 1)
        self.reference_check = QCheckBox("下絵を表示")
        self.reference_check.setChecked(True)
        self.reference_check.toggled.connect(self._reference_visibility)
        rl.addWidget(self.reference_check)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(50)
        self.opacity_slider.valueChanged.connect(self._reference_opacity)
        rl.addWidget(QLabel("下絵の不透明度"))
        rl.addWidget(self.opacity_slider)
        rl.addWidget(QLabel("ファイル名"))
        name_row = QHBoxLayout()
        self.filename_edit = QLineEdit("pixel_art")
        self.filename_edit.setStyleSheet(INPUT_CONTROL_STYLE)
        self.filename_edit.setPlaceholderText("保存する名前")
        self.filename_edit.textChanged.connect(self._update_save_ui)
        self.filename_edit.textChanged.connect(self._update_document_summary)
        self.filename_edit.editingFinished.connect(self._normalize_filename_input)
        name_row.addWidget(self.filename_edit, 1)
        self.filename_suffix_label = QLabel(".png")
        self.filename_suffix_label.setStyleSheet("color: #475467;")
        name_row.addWidget(self.filename_suffix_label)
        rl.addLayout(name_row)
        self.filename_hint_label = QLabel("使えない記号は _ に置き換わります")
        self.filename_hint_label.setWordWrap(True)
        self.filename_hint_label.setStyleSheet("color: #667085;")
        rl.addWidget(self.filename_hint_label)
        rl.addWidget(QLabel("保存先"))
        self.output_folder_value = ElidedValueLabel()
        self.output_folder_value.set_value("未選択")
        rl.addWidget(self.output_folder_value)
        self.choose_folder_button = QPushButton("保存先を選ぶ")
        self.choose_folder_button.clicked.connect(self.choose_output_folder)
        rl.addWidget(self.choose_folder_button)
        rl.addWidget(QLabel("保存予定"))
        self.planned_output_value = ElidedValueLabel()
        self.planned_output_value.set_value("保存先とファイル名を指定してください")
        rl.addWidget(self.planned_output_value)
        self.save_hint_label = QLabel()
        self.save_hint_label.setWordWrap(True)
        self.save_hint_label.setStyleSheet("color: #667085;")
        rl.addWidget(self.save_hint_label)
        self.save_button = QPushButton("PNGで保存")
        self.save_button.clicked.connect(self.save)
        rl.addWidget(self.save_button)
        self.saved_label = QLabel()
        self.saved_label.setWordWrap(True)
        rl.addWidget(self.saved_label)
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_folder_button.clicked.connect(self.open_saved_folder)
        rl.addWidget(self.open_folder_button)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 650, 250])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.canvas_view.changed.connect(self._refresh)
        self.canvas_view.coordinates_changed.connect(self.coord_label.setText)
        self.canvas_view.drop_requested.connect(self._handle_dropped_path)
        self.canvas_view.color_picked.connect(self._on_canvas_color_picked)
        self.size_combo.currentTextChanged.connect(lambda value: setattr(self.canvas_view, "pencil_size", int(value)))
        self._set_current_color(QColor(*self.canvas_view.color))
        self._update_save_ui()
        self._update_palette_guidance()
        self._refresh()

    def _set_tool(self, tool):
        if self._import_thread is not None:
            return
        tool = PixelTool(tool)
        self.canvas_view.tool = tool
        button = self._tool_buttons.get(tool)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def dragEnterEvent(self, event):
        if local_image_paths(event.mimeData()):
            self.setProperty("dragActive", True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", False)
        event.accept()

    def dropEvent(self, event):
        paths = local_image_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.setProperty("dragActive", False)
        self._handle_dropped_path(str(paths[0]))
        event.acceptProposedAction()

    def _handle_dropped_path(self, path):
        path = Path(path)
        source = self._read_valid_import_source(path)
        if source is None:
            return
        if self._workspace_managed:
            self.source_change_requested.emit(path)
        else:
            self.set_current_source(source)

    def _preset_changed(self, index):
        self.custom_size_widget.setVisible(index == 3)
        if index < 3:
            self.width_spin.setValue((32, 64, 128)[index])
            self.height_spin.setValue((32, 64, 128)[index])

    def _zoom_changed(self, index):
        if index == 0:
            self.canvas_view.set_zoom(max(1, min(self.canvas_view.viewport().width() // self.canvas.width, self.canvas_view.viewport().height() // self.canvas.height)))
        else:
            self.canvas_view.set_zoom((2, 4, 8, 16)[index - 1])

    def _current_color_hex(self, color: QColor) -> str:
        return color.name(QColor.NameFormat.HexArgb).upper() if color.alpha() < 255 else color.name().upper()

    def _update_current_color_button(self, color: QColor) -> None:
        contrast = "#000000" if color.lightness() > 150 and color.alpha() > 127 else "#FFFFFF"
        text = self._current_color_hex(color)
        self.current_color_button.setText(text)
        self.current_color_button.setToolTip(text)
        self.current_color_button.setStyleSheet(
            f"QPushButton {{ background: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()}); color: {contrast}; border: 1px solid #65768a;"
            " border-radius: 6px; min-height: 30px; font-weight: 700; padding: 4px 8px; }}"
            "QPushButton:hover { border: 2px solid #2457b2; padding: 3px 7px; }"
        )

    def _set_palette_selection(self, index: int) -> None:
        self._palette_selected_index = index
        previous = self._palette_button_group.exclusive()
        self._palette_button_group.setExclusive(False)
        for button_index, button in enumerate(self._palette_chip_buttons):
            button.setChecked(button_index == index)
            button.setText("✓" if button_index == index else "")
            state = "選択中" if button_index == index else "未選択"
            button.setAccessibleName(f"パレット {button_index + 1}: {button.toolTip()}・{state}")
        self._palette_button_group.setExclusive(previous)

    def _set_current_color(self, color: QColor, *, palette_index: int = -1, message: str | None = None) -> None:
        self.canvas_view.color = (color.red(), color.green(), color.blue(), color.alpha())
        self._set_palette_selection(palette_index)
        self._update_current_color_button(color)
        self.preview_hint_label.setText(message or f"現在色: {self._current_color_hex(color)}")

    def _canvas_is_blank(self) -> bool:
        return self.canvas.image.getchannel("A").getbbox() is None

    def _update_palette_guidance(self) -> None:
        if not self._received_palette:
            self.palette_guidance_label.setText("")
            self.palette_clear_button.setEnabled(False)
            return
        self.palette_clear_button.setEnabled(True)
        if self.source_path is None and self.reference is None and self._canvas_is_blank():
            self.palette_guidance_label.setText(
                "パレットだけを受け取りました。\n"
                "画像は読み込まれていません。\n\n"
                "上の「現在の画像」から画像を選び、\n"
                "下絵またはドット化を選んでください。"
            )
        else:
            self.palette_guidance_label.setText("現在のキャンバスや下絵はそのままです。パレットだけを更新しました。")

    def new_canvas(self):
        if self._import_thread is not None:
            return
        self._invalidate_import_requests()
        self.canvas = PixelCanvas(self.width_spin.value(), self.height_spin.value())
        self.canvas_view.set_canvas(self.canvas)
        self.reference = None
        self.canvas_view.reference = None
        self.source_path = None
        self._set_filename_default(None)
        self._update_palette_guidance()
        self._refresh()

    def _document_is_edited(self) -> bool:
        return self.canvas.history.can_undo or not self._canvas_is_blank()

    @Slot()
    def _update_document_summary(self) -> None:
        filename = self._normalized_filename_stem()
        if self._document_is_edited() or self._last_saved_result is not None:
            display_name = f"{filename}.png" if filename else "ファイル名未設定"
        else:
            display_name = "新規キャンバス"
        self.document_name_label.set_value(display_name, display_name)
        self.document_meta_label.setText(f"{self.canvas.width} × {self.canvas.height}")
        self.document_meta_label.setToolTip(
            f"{display_name}\n{self.canvas.width} × {self.canvas.height}"
        )

    def _hide_source_change_notice(self) -> None:
        self.source_change_notice.clear()
        self.source_change_notice.hide()

    def clear(self):
        if self._import_thread is not None:
            return
        self.canvas.clear()
        self._update_palette_guidance()
        self._refresh()

    def undo(self):
        if self._import_thread is not None:
            return
        self.canvas.undo()
        self._refresh()

    def redo(self):
        if self._import_thread is not None:
            return
        self.canvas.redo()
        self._refresh()

    def _load_reference_path(self, path: Path):
        return self._queue_import("reference", Path(path))

    def load_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "下絵を読み込む", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._load_reference_path(Path(path))

    def _load_pixels_path(self, path: Path):
        return self._queue_import("pixels", Path(path))

    def load_pixels(self):
        path, _ = QFileDialog.getOpenFileName(self, "ドット化して編集", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._load_pixels_path(Path(path))

    def set_workspace_managed(self, managed: bool = True) -> None:
        self._workspace_managed = managed

    @Slot(object)
    def set_current_source(self, source: SourceImage) -> None:
        """Update only passive source UI; never mutate canvas/reference/history."""
        previous = self._current_source
        changed = previous is None or previous.document_id != source.document_id
        if changed:
            self._invalidate_import_requests()
        self._current_source = source
        self.current_source_card.set_source(source)
        if changed and (previous is not None or self._document_is_edited()):
            self.source_change_notice.setText(
                "元にする画像を変更しました\n"
                "編集中のドット絵はそのままです"
            )
            self.source_change_notice.show()
            self.source_change_notice.updateGeometry()
        self._update_current_source_usage()

    def _update_current_source_usage(self) -> None:
        has_source = self._current_source is not None
        self.current_source_usage.setVisible(has_source)
        self.current_reference_button.setEnabled(has_source)
        self.current_pixels_button.setEnabled(has_source)
        if not has_source:
            return
        if self._canvas_is_blank():
            self.current_source_usage_guidance.setText(
                "現在の画像は読み込まれています。\n"
                "下絵にするか、ドット化して編集できます。"
            )
            self.current_source_usage_guidance.setStyleSheet(
                "color: #174a9c; font-weight: 650;"
            )
            self.current_source_usage.setStyleSheet(
                "QFrame#pixelCurrentSourceUsage { background: #eef5ff; "
                "border: 2px solid #6b94d6; border-radius: 8px; }"
            )
        else:
            self.current_source_usage_guidance.setText(
                "現在のキャンバスは自動では変更されません。\n"
                "使う場合は、下の操作を選んでください。"
            )
            self.current_source_usage_guidance.setStyleSheet(
                "color: #667085; font-weight: 400;"
            )
            self.current_source_usage.setStyleSheet(
                "QFrame#pixelCurrentSourceUsage { background: #f7f8fa; "
                "border: 1px solid #cfd6df; border-radius: 8px; }"
            )

    @Slot()
    def choose_current_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "現在の画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)"
        )
        if not path:
            return
        source = self._read_valid_import_source(Path(path))
        if source is None:
            return
        if self._workspace_managed:
            self.source_change_requested.emit(Path(path))
        else:
            self.set_current_source(source)

    def _read_valid_import_source(self, path: Path) -> SourceImage | None:
        try:
            return read_source_image(path, 0)
        except MissingSourceError:
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
        except ProcessingError as exc:
            QMessageBox.warning(self, "画像を開けません", str(exc))
        return None

    @Slot()
    def use_current_as_reference(self) -> None:
        self._hide_source_change_notice()
        if self._current_source is None:
            return
        if not self._current_source.path.is_file():
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return
        self._load_reference_path(self._current_source.path)

    @Slot()
    def use_current_as_pixels(self) -> None:
        self._hide_source_change_notice()
        if self._current_source is None:
            return
        if not self._current_source.path.is_file():
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return
        self._load_pixels_path(self._current_source.path)

    @staticmethod
    def _import_source_identity(path: Path):
        stat = path.stat()
        return str(path.resolve()), stat.st_mtime_ns, stat.st_size

    def _preflight_import(self, path: Path) -> bool:
        try:
            if path.suffix.lower() not in SUPPORTED_DROP_SUFFIXES:
                raise ValueError(INVALID_IMAGE_MESSAGE)
            if not path.is_file():
                raise MissingSourceError(MISSING_SOURCE_MESSAGE)
            with Image.open(path) as opened:
                if (opened.format or "").upper() not in SUPPORTED_IMAGE_FORMATS:
                    raise ValueError(INVALID_IMAGE_MESSAGE)
                opened.verify()
            return True
        except (MissingSourceError, FileNotFoundError, OSError):
            if not path.is_file():
                QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            else:
                QMessageBox.warning(self, "画像を開けません", INVALID_IMAGE_MESSAGE)
            return False
        except Exception:
            QMessageBox.warning(self, "画像を開けません", INVALID_IMAGE_MESSAGE)
            return False

    def _queue_import(self, mode: str, path: Path, *, preflight: bool = True) -> bool:
        self._hide_source_change_notice()
        if preflight and not self._preflight_import(path):
            return False
        try:
            identity = self._import_source_identity(path)
        except OSError:
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return False
        self._import_request_id += 1
        activity_token = self.preview_activity.begin(
            "下絵を準備しています" if mode == "reference" else "画像をドット化しています"
        )
        self._import_activity_token = activity_token
        request = (
            self._import_generation,
            self._import_request_id,
            mode,
            path.resolve(),
            identity,
            (self.canvas.width, self.canvas.height),
            self.opacity_slider.value(),
            activity_token,
        )
        if self._import_thread is not None:
            self._import_pending_request = request
            return True
        self._start_import_request(request)
        return True

    def _start_import_request(self, request) -> None:
        self._import_active_request = request
        self._import_active_result = None
        thread = QThread(self)
        worker = PixelImportWorker(request)
        self._import_thread = thread
        self._import_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda payload: self._capture_import_result("success", payload))
        worker.failed.connect(lambda payload: self._capture_import_result("failed", payload))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda t=thread, w=worker: self._finalize_import_request(t, w))
        thread.finished.connect(thread.deleteLater)
        self._set_import_processing(True)
        thread.start()

    def _capture_import_result(self, kind: str, payload) -> None:
        self._import_active_result = (kind, payload)

    def _import_request_is_current(self, request) -> bool:
        generation, _request_id, _mode, path, identity, canvas_size, _opacity, _token = request
        if generation != self._import_generation or canvas_size != (self.canvas.width, self.canvas.height):
            return False
        try:
            return identity == self._import_source_identity(path)
        except OSError:
            return False

    def _import_request_owns_ui(self, request) -> bool:
        generation, request_id, *_rest = request
        return generation == self._import_generation and request_id == self._import_request_id

    def _finalize_import_request(self, thread: QThread, worker: PixelImportWorker) -> None:
        if thread is not self._import_thread:
            return
        request = self._import_active_request
        result = self._import_active_result
        self._import_thread = None
        self._import_worker = None
        self._import_active_request = None
        self._import_active_result = None
        if self._import_pending_request is not None:
            pending = self._import_pending_request
            self._import_pending_request = None
            self._start_import_request(pending)
            return
        owns_ui = request is not None and self._import_request_owns_ui(request)
        if owns_ui:
            activity_token = request[-1]
            if result is None:
                result = ("failed", (*request, "画像の読み込みを完了できませんでした。"))
            kind, payload = result
            if kind == "success" and self._import_request_is_current(request):
                _generation, _request_id, mode, path, _identity, canvas_size, opacity, _token, pixels = payload
                image = Image.frombytes("RGBA", canvas_size, pixels)
                if mode == "reference":
                    self.reference = ReferenceImage(image, opacity)
                    self.canvas_view.reference = self.reference
                else:
                    self.canvas.restore(pixels)
                    self.canvas.history.commit(pixels)
                    self.reference = None
                    self.canvas_view.reference = None
                    self.source_path = path
                    self._set_filename_default(path)
                self._update_palette_guidance()
                self._refresh()
                self.preview_hint_label.setStyleSheet("color: #667085;")
                self.preview_hint_label.setText(f"現在色: {self._current_color_hex(QColor(*self.canvas_view.color))}")
                self.preview_activity.complete(activity_token)
            else:
                if kind == "success":
                    path = request[3]
                    message = MISSING_SOURCE_MESSAGE if not path.is_file() else "元画像が変更されたため、読み込み結果を適用しませんでした。"
                else:
                    message = payload[-1]
                self.preview_hint_label.setStyleSheet("color: #c62828; font-weight: 700;")
                self.preview_hint_label.setText(message)
                if message == MISSING_SOURCE_MESSAGE:
                    QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
                else:
                    QMessageBox.warning(self, "画像を開けません", message)
                self.preview_activity.fail(activity_token)
            if self._import_activity_token == activity_token:
                self._import_activity_token = None
        self._set_import_processing(False)

    def _set_import_processing(self, processing: bool) -> None:
        for widget in (
            self.canvas_view,
            self.tools_group,
            self.view_group,
            self.canvas_group,
            self.palette_group,
            self.clear_button,
            self.reference_check,
            self.opacity_slider,
            self.filename_edit,
            self.choose_folder_button,
            self.save_button,
            self.new_button,
        ):
            widget.setEnabled(not processing)
        self.processing_changed.emit(processing)
        if not processing:
            self._update_current_source_usage()
            self._update_save_ui()

    def _invalidate_import_requests(self) -> None:
        self._import_generation += 1
        self._import_pending_request = None
        self.preview_activity.invalidate()
        self._import_activity_token = None

    def _reference_visibility(self, value):
        self.canvas_view.reference_visible = value
        self.canvas_view.viewport().update()
        self._refresh()

    def _reference_opacity(self, value):
        if self.reference is not None:
            self.reference = ReferenceImage(self.reference.image, value)
            self.canvas_view.reference = self.reference
            self.canvas_view.viewport().update()
            self._refresh()

    def choose_current_color(self) -> None:
        if self._import_thread is not None:
            return
        color = choose_color(
            QColor(*self.canvas_view.color),
            self,
            "描画色を選ぶ",
            show_alpha=True,
        )
        if color.isValid():
            self._set_current_color(color)

    def _on_canvas_color_picked(self, color: QColor) -> None:
        if self._import_thread is not None:
            return
        self._set_current_color(color)

    def set_palette(self, colors) -> None:
        self.receive_palette(colors)

    def receive_palette(self, colors) -> None:
        if self._import_thread is not None:
            return
        self._received_palette = tuple(tuple(color) for color in colors or ())
        self._set_palette_selection(-1)
        self._rebuild_palette_chips()
        self.palette_count_label.setText(f"{len(self._received_palette)}色")
        if self._received_palette:
            self.palette_status_label.setText(f"✓ {len(self._received_palette)}色のパレットを受け取りました")
        else:
            self.palette_status_label.setText("画像加工からパレットを受け取ると、ここに並びます。")
        self._update_palette_guidance()

    def _rebuild_palette_chips(self) -> None:
        self._palette_button_group.setExclusive(False)
        for button in self._palette_chip_buttons:
            self._palette_button_group.removeButton(button)
        self._palette_button_group.setExclusive(True)
        self._palette_chip_buttons.clear()
        while self.palette_chips_layout.count():
            item = self.palette_chips_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not self._received_palette:
            return
        for index, color in enumerate(self._received_palette):
            button = QToolButton()
            contrast = "#000000" if sum(color) >= 384 else "#FFFFFF"
            button.setCheckable(True)
            button.setText("")
            button.setToolTip(f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}")
            button.setAccessibleName(
                f"パレット {index + 1}: {button.toolTip()}・"
                f"{'選択中' if index == self._palette_selected_index else '未選択'}"
            )
            button.setFixedSize(28, 28)
            button.setStyleSheet(
                f"QToolButton {{ background: rgb{color}; color: {contrast}; border: 1px solid #65768a; border-radius: 6px; font-size: 14px; font-weight: 800; }}"
                "QToolButton:hover { border: 2px solid #2457b2; }"
                "QToolButton:checked { border: 3px solid #173a82; }"
            )
            button.clicked.connect(lambda checked=False, rgb=color, i=index: self._apply_palette_color(rgb, i))
            self._palette_button_group.addButton(button, index)
            self._palette_chip_buttons.append(button)
            self.palette_chips_layout.addWidget(button, index // 6, index % 6)

    def _apply_palette_color(self, color: tuple[int, int, int], index: int) -> None:
        self._set_current_color(QColor(color[0], color[1], color[2], 255), palette_index=index)

    def clear_palette(self) -> None:
        if self._import_thread is not None:
            return
        self._received_palette = ()
        self._set_palette_selection(-1)
        self._rebuild_palette_chips()
        self.palette_count_label.setText("0色")
        self.palette_status_label.setText("パレットをクリアしました。")
        self._update_palette_guidance()

    def _default_filename_stem(self, source_path: Path | None) -> str:
        raw = f"{source_path.stem}_pixel" if source_path else "pixel_art"
        return normalize_filename_stem(raw, default="pixel_art")

    def _set_filename_default(self, source_path: Path | None) -> None:
        self.filename_edit.setText(self._default_filename_stem(source_path))
        self.saved_label.clear()
        self._last_saved_result = None
        self._update_save_ui()

    def _normalized_filename_stem(self) -> str:
        return normalize_filename_stem(self.filename_edit.text(), default="")

    def _normalize_filename_input(self) -> None:
        normalized = self._normalized_filename_stem()
        if self.filename_edit.text() != normalized:
            self.filename_edit.setText(normalized)

    def _planned_output_path(self) -> Path | None:
        normalized = self._normalized_filename_stem()
        if self.output_folder is None or not normalized:
            return None
        return self.output_folder / f"{normalized}.png"

    def _update_save_ui(self) -> None:
        normalized = self._normalized_filename_stem()
        if self.output_folder is None:
            self.output_folder_value.set_value("未選択")
            self.planned_output_value.set_value("保存先を選ぶと表示されます")
            self.save_hint_label.setText("保存先を選ぶとPNG保存できます。")
            self.save_button.setEnabled(False)
        elif not normalized:
            self.output_folder_value.set_path(self.output_folder)
            self.planned_output_value.set_value(str(self.output_folder / ".png"))
            self.save_hint_label.setText("保存するファイル名を入力してください。")
            self.save_button.setEnabled(False)
        else:
            planned = self._planned_output_path()
            self.output_folder_value.set_path(self.output_folder)
            self.planned_output_value.set_path(planned)
            self.save_hint_label.setText("同名ファイルがある場合は自動で連番を付けます。")
            self.save_button.setEnabled(True)
        self.open_folder_button.setEnabled(self._last_saved_result is not None and self._last_saved_result.output_path.parent.is_dir())

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先を選ぶ", str(self.output_folder or ""))
        if not folder:
            return
        self.output_folder = Path(folder)
        self._update_save_ui()

    def save(self):
        if self.output_folder is None:
            QMessageBox.warning(self, "保存先を選んでください", "保存先を選ぶを押して、保存先フォルダーを指定してください。")
            return
        normalized = self._normalized_filename_stem()
        if not normalized:
            QMessageBox.warning(self, "ファイル名を入力してください", "保存するファイル名を入力してください。")
            return
        try:
            result = save_png(self.canvas, self.output_folder, self.source_path, custom_stem=normalized)
            self._last_saved_result = result
            self.saved_label.setStyleSheet("color: #137333; font-weight: 700;")
            self.saved_label.setText(f"✓ PNGを保存しました\n{result.output_path.name}\n{result.width} × {result.height} / {result.size_bytes:,} bytes")
            self._update_save_ui()
            self._update_document_summary()
        except PixelExportError as exc:
            QMessageBox.warning(self, "保存エラー", str(exc))

    def open_saved_folder(self):
        folder = self._last_saved_result.output_path.parent if self._last_saved_result is not None else None
        if not folder or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "PNGを保存してから、もう一度お試しください。")

    def _refresh(self):
        self.preview_size_label.setText(f"{self.canvas.width} × {self.canvas.height}")
        image = _qimage(self.canvas.image)
        self.preview_label.setPixmap(QPixmap.fromImage(image))
        self.preview_label.setFixedSize(image.size())
        self.canvas_view.viewport().update()
        self._update_document_summary()
        self._update_current_source_usage()

    def resizeEvent(self, event):
        self._refresh()
        super().resizeEvent(event)

    def can_close(self):
        return self._import_thread is None

    def can_replace_source(self):
        return True

    def cleanup(self):
        self._invalidate_import_requests()
