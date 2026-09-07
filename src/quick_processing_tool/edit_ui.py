from __future__ import annotations

import logging
import math
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTabletEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
)

from .font_catalog import FontCatalog
from .color_picker import choose_color
from .editing import (
    CanvasBackground,
    CanvasSettings,
    EditOutputFormat,
    EditResult,
    EditService,
    EditSettings,
    FilterPreset,
    HandDrawSettings,
    HandPoint,
    HandStroke,
    HandTool,
    MosaicSettings,
    MosaicStroke,
    MosaicTool,
    LineArtAmount,
    LineArtBackground,
    LineArtSettings,
    PaletteSettings,
    PlacementMode,
    RecolorBlendMode,
    StickerSettings,
    TextPosition,
    TextSettings,
    TransparencySettings,
    render_hand_overlay,
    scale_hand_draw,
)
from .editing.canvas import output_dimensions
from .editing.palette import extract_palette
from .editing.renderer import load_normalized, prepare_palette_source, render_path_preview
from .editing.service import EditProcessingError, SOURCE_FORMATS
from .naming import EXTENSIONS, KNOWN_IMAGE_EXTENSIONS, normalize_filename_stem
from .editing.text import pil_to_qimage
from .image_workspace import MISSING_SOURCE_MESSAGE, MissingSourceError, SourceImage, require_source_file
from .drop_overlay import RoundedDropOverlay
from .source_ui import CurrentSourceCard
from .ui_styles import (
    INPUT_CONTROL_STYLE,
    PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH,
    PRIMARY_SETTINGS_PANE_MAX_WIDTH,
    PRIMARY_SETTINGS_PANE_MIN_WIDTH,
    set_operation_role,
)
from .preview_activity import PreviewActivityIndicator
from .preview_zoom import create_preview_zoom_row, zoom_factor_for_mode


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
RECOLOR_BLEND_LABELS = {
    RecolorBlendMode.SHARP: "くっきり",
    RecolorBlendMode.SMOOTH: "なめらか",
    RecolorBlendMode.PRESERVE_SHADING: "陰影を残す",
}
RECOLOR_BLEND_DESCRIPTIONS = {
    RecolorBlendMode.SHARP: "色面をはっきり分けます",
    RecolorBlendMode.SMOOTH: "色の境目を自然につなぎます",
    RecolorBlendMode.PRESERVE_SHADING: "元画像の明るさを残して色を変えます",
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
    border-radius: 6px; padding: 5px; selection-background-color: #315fbd;
}
QPlainTextEdit:hover { background: #cfdeec; border-color: #405b79; }
QPlainTextEdit:focus { background: white; border: 2px solid #2457b2; padding: 4px; }
QPlainTextEdit:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton { min-height: 26px; background: #f5f7fa; color: #182230;
    border: 1px solid #7b899a; border-radius: 6px; padding: 3px 9px; }
QPushButton:hover { background: #e5edf6; border-color: #405b79; }
QPushButton:focus { background: white; border: 2px solid #2457b2; padding: 2px 8px; }
QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton#editSave { min-height: 40px; background: #315fbd; color: white;
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


class FontPickerDialog(QDialog):
    def __init__(self, catalog: FontCatalog, current_family: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._selected_family = catalog.resolve_family(current_family)
        self.setWindowTitle("フォントを選ぶ")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("フォントを検索")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        layout.addWidget(QLabel("よく使うフォント"))
        self.preferred_list = QListWidget()
        self.preferred_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preferred_list.setMaximumHeight(150)
        layout.addWidget(self.preferred_list)
        layout.addWidget(QLabel("インストール済みフォント"))
        self.all_list = QListWidget()
        self.all_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.all_list, 1)
        self.file_button = QPushButton("フォントファイルを選ぶ…")
        self.file_button.clicked.connect(self._choose_file)
        layout.addWidget(self.file_button)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.search_edit.textChanged.connect(self._filter)
        self.preferred_list.itemClicked.connect(self._item_selected)
        self.all_list.itemClicked.connect(self._item_selected)
        self.preferred_list.currentItemChanged.connect(self._current_item_changed)
        self.all_list.currentItemChanged.connect(self._current_item_changed)
        self.preferred_list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.all_list.itemDoubleClicked.connect(lambda _item: self.accept())
        self._populate()

    @property
    def selected_family(self) -> str:
        return self._selected_family

    def _populate(self) -> None:
        families = self.catalog.families()
        preferred = self.catalog.preferred_families()
        self.preferred_list.clear()
        self.all_list.clear()
        for family in preferred:
            item = QListWidgetItem(family)
            item.setData(Qt.ItemDataRole.UserRole, family)
            self.preferred_list.addItem(item)
        for family in families:
            item = QListWidgetItem(family)
            item.setData(Qt.ItemDataRole.UserRole, family)
            self.all_list.addItem(item)
        self._filter(self.search_edit.text())
        self._select_visible(self._selected_family)

    def _select_visible(self, family: str) -> None:
        for widget in (self.preferred_list, self.all_list):
            for row in range(widget.count()):
                item = widget.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == family:
                    widget.setCurrentItem(item)
                    return

    def _filter(self, query: str) -> None:
        query = query.casefold().strip()
        for widget in (self.preferred_list, self.all_list):
            for row in range(widget.count()):
                item = widget.item(row)
                item.setHidden(bool(query) and query not in item.text().casefold())

    def _current_item_changed(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if item is not None and not item.isHidden():
            self._item_selected(item)

    def _item_selected(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        source = next(
            (widget for widget in (self.preferred_list, self.all_list) if widget.row(item) >= 0),
            None,
        )
        for widget in (self.preferred_list, self.all_list):
            if widget is not source:
                widget.blockSignals(True)
                widget.clearSelection()
                widget.blockSignals(False)
        self._selected_family = str(item.data(Qt.ItemDataRole.UserRole))

    def accept(self) -> None:
        # The last explicit item selection is authoritative; never prefer one list
        # merely because it happens to retain a current row.
        super().accept()

    def _choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "フォントファイルを選ぶ", "", "フォント (*.ttf *.otf)"
        )
        if not filename:
            return
        result = self.catalog.register_file(Path(filename))
        if not result.succeeded:
            QMessageBox.warning(self, "フォントを読み込めません", result.error)
            return
        self._populate()
        self._selected_family = result.families[0]
        self.accept()


class FontPickerButton(QPushButton):
    family_changed = Signal(str)

    def __init__(self, catalog: FontCatalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._family = catalog.default_family()
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._open_picker)
        self._update_display()

    def family(self) -> str:
        return self._family

    def set_family(self, family: str, *, resolve: bool = True) -> None:
        resolved = self.catalog.resolve_family(family) if resolve else family
        if not resolved:
            resolved = self.catalog.default_family()
        changed = resolved != self._family
        self._family = resolved
        self._update_display()
        if changed:
            self.family_changed.emit(self._family)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_display()

    def _update_display(self) -> None:
        width = max(80, self.width() - 24)
        self.setText(self.fontMetrics().elidedText(self._family, Qt.TextElideMode.ElideRight, width))
        self.setToolTip(self._family)

    def _open_picker(self) -> None:
        picker = FontPickerDialog(self.catalog, self._family, self)
        if picker.exec() == QDialog.DialogCode.Accepted:
            self.set_family(picker.selected_family)


class IMEPlainTextEdit(QPlainTextEdit):
    """Plain text editor that finishes native IME composition at focus boundaries."""

    def finish_ime(self) -> None:
        input_method = QGuiApplication.inputMethod()
        if input_method is not None:
            input_method.commit()
            input_method.reset()
            input_method.hide()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.finish_ime()
        super().focusOutEvent(event)


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

    def __init__(
        self,
        title: str,
        description: str = "",
        content: QWidget | None = None,
        open_by_default: bool = False,
    ) -> None:
        super().__init__()
        if content is None:
            raise ValueError("CollapsibleSection content is required")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 0)
        layout.setSpacing(2)
        self.toggle = QToolButton()
        self.toggle.setMinimumWidth(0)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet(
            "QToolButton { text-align: left; font-size: 14px; font-weight: 700; padding: 6px;"
            "border: 1px solid #c3ceda; background: #e8eef5; color: #182230; border-radius: 6px; }"
            "QToolButton:hover { background: #dbe6f1; border-color: #405b79; }"
            "QToolButton:focus { border: 2px solid #2457b2; padding: 5px; }"
            "QToolButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
        )
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: #667085; padding: 0 8px 1px 24px;")
        self.content = content
        self.content.setMinimumWidth(0)
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.content.setProperty("editAccordionContent", True)
        self.content.setStyleSheet(
            "QPushButton { min-height: 22px; padding: 2px 6px; }"
            "QPushButton:focus { padding: 1px 5px; }"
            "QComboBox, QSpinBox { min-height: 22px; padding-top: 2px; padding-bottom: 2px; }"
        )
        self.toggle.toggled.connect(self.set_expanded)
        layout.addWidget(self.toggle)
        layout.addWidget(self.description)
        layout.addWidget(content)
        self.toggle.setChecked(open_by_default)
        self.set_expanded(open_by_default)

    @Slot(bool)
    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.description.setVisible(expanded and bool(self.description.text().strip()))
        self.content.setVisible(expanded)
        self.expanded.emit(expanded)


class ActiveHandOverlayItem(QGraphicsItem):
    """Paint a mutable display-sized QImage without QPixmap conversion per move."""

    def __init__(self) -> None:
        super().__init__()
        self._image = QImage()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._image.width(), self._image.height())

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        if not self._image.isNull():
            painter.drawImage(QPointF(0, 0), self._image)

    def set_image(self, image: QImage) -> None:
        self.prepareGeometryChange()
        self._image = image
        self.update()

    def clear(self) -> None:
        self.set_image(QImage())
        self.hide()

    def update_region(self, rect: QRectF) -> None:
        self.update(rect.adjusted(-2, -2, 2, 2))

    def paint_segment(
        self,
        start: QPointF,
        end: QPointF,
        tool: HandTool,
        color: QColor,
        width: float,
    ) -> None:
        if self._image.isNull():
            return
        painter = QPainter(self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if tool is HandTool.ERASER:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            paint_color = QColor(0, 0, 0, 255)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            paint_color = color
        pen = QPen(paint_color)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if start == end:
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.setBrush(paint_color)
            painter.drawEllipse(start, width / 2, width / 2)
        else:
            painter.drawLine(start, end)
        painter.end()
        radius = width / 2
        dirty = QRectF(start, end).normalized().adjusted(-radius, -radius, radius, radius)
        self.update_region(dirty)


class EditPreview(QGraphicsView):
    color_picked = Signal(int, int, int)
    hand_color_picked = Signal(object, bool)
    hand_pressed = Signal(object)
    hand_moved = Signal(object)
    hand_released = Signal()
    hand_cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._item.setZValue(0)
        self.scene().addItem(self._item)
        self._overlay_item = QGraphicsPixmapItem()
        self._overlay_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._overlay_item.setZValue(1)
        self.scene().addItem(self._overlay_item)
        self._active_overlay_item = ActiveHandOverlayItem()
        self._active_overlay_item.setZValue(1.5)
        self._active_overlay_item.hide()
        self.scene().addItem(self._active_overlay_item)
        self._active_path_item = QGraphicsPathItem()
        self._active_path_item.setZValue(1.5)
        self._active_path_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._active_path_item.hide()
        self.scene().addItem(self._active_path_item)
        self._cursor_item = QGraphicsEllipseItem()
        self._cursor_item.setZValue(2)
        self._cursor_item.setPen(QPen(QColor(35, 55, 85, 220), 1))
        self._cursor_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._cursor_item.hide()
        self.scene().addItem(self._cursor_item)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().installEventFilter(self)
        self._image = QImage()
        self._picking = False
        self._hand_color_picking = False
        self._drawing_enabled = False
        self._pointer_active = False
        self._brush_width = 8.0
        self._full_size = (0, 0)
        self._geometry_generation: int | None = None
        self._zoom_mode = "fit"
        self._pan_start = None
        self._pan_scroll_values: tuple[int, int] | None = None
        self._committed_overlay_key = None
        self._committed_overlay_image = QImage()
        self._active_last_canvas: HandPoint | None = None
        self._active_tool = HandTool.PEN
        self._active_color = QColor("#000000")
        self._active_width = 8.0
        self._active_path = QPainterPath()
        self._active_path_has_segment = False
        tile = QPixmap(24, 24)
        tile.fill(QColor("#d7dbe0"))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 12, 12, QColor("#f4f5f7"))
        painter.fillRect(12, 12, 12, 12, QColor("#f4f5f7"))
        painter.end()
        self.setBackgroundBrush(tile)

    def set_image(
        self,
        image: QImage,
        full_size: tuple[int, int] | None = None,
        geometry_generation: int | None = None,
    ) -> None:
        next_full_size = full_size or (image.width(), image.height())
        same_geometry = (
            self._geometry_generation == geometry_generation
            and self._full_size == next_full_size
            and not self._image.isNull()
            and self._image.size() == image.size()
        )
        if not same_geometry:
            self._cancel_pointer()
        self._image = image.copy()
        self._item.setPixmap(QPixmap.fromImage(image))
        self._overlay_item.setPos(self._item.pos())
        self._active_overlay_item.setPos(self._item.pos())
        self._active_path_item.setPos(self._item.pos())
        self._full_size = next_full_size
        self._geometry_generation = geometry_generation
        if not same_geometry:
            self._committed_overlay_key = None
            self._committed_overlay_image = QImage()
            self._overlay_item.setPixmap(QPixmap())
            self.cancel_active_hand()
        self.scene().setSceneRect(self._item.boundingRect())
        self._apply_zoom()

    def clear_image(self) -> None:
        self._image = QImage()
        self._item.setPixmap(QPixmap())
        self._overlay_item.setPixmap(QPixmap())
        self._committed_overlay_key = None
        self._committed_overlay_image = QImage()
        self.cancel_active_hand()
        self._cursor_item.hide()
        self._full_size = (0, 0)
        self._geometry_generation = None
        self.scene().setSceneRect(0, 0, 1, 1)

    def set_picking(self, enabled: bool) -> None:
        self._picking = enabled
        self._update_viewport_cursor()

    def set_hand_color_picking(self, enabled: bool) -> None:
        self._hand_color_picking = bool(enabled)
        self._update_viewport_cursor()

    def set_drawing_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._drawing_enabled and not enabled and self._pointer_active:
            self._cancel_pointer()
        self._drawing_enabled = enabled
        if not enabled:
            self._cursor_item.hide()
        self._update_viewport_cursor()

    def set_brush_width(self, width: float) -> None:
        self._brush_width = max(1.0, float(width))

    def set_zoom_mode(self, mode: str) -> None:
        zoom_factor_for_mode(mode)
        self._zoom_mode = mode
        self._apply_zoom()

    def set_hand_overlay(
        self,
        settings: HandDrawSettings,
        *,
        visible: bool = True,
    ) -> None:
        display_size = (self._image.width(), self._image.height())
        key = (settings, display_size, bool(visible))
        if key == self._committed_overlay_key:
            return
        self._committed_overlay_key = key
        if self._image.isNull() or not visible or not settings.visible or not settings.strokes:
            self._committed_overlay_image = QImage()
            self._overlay_item.setPixmap(QPixmap())
            return
        overlay = render_hand_overlay(settings, display_size)
        self._committed_overlay_image = pil_to_qimage(overlay)
        self._overlay_item.setPixmap(QPixmap.fromImage(self._committed_overlay_image))

    def _canvas_to_display(self, point: HandPoint) -> QPointF:
        return QPointF(
            point.x * self._image.width() / max(1, self._full_size[0]),
            point.y * self._image.height() / max(1, self._full_size[1]),
        )

    def _active_display_width(self) -> float:
        return max(
            0.25,
            self._active_width
            * min(
                self._image.width() / max(1, self._full_size[0]),
                self._image.height() / max(1, self._full_size[1]),
            ),
        )

    def _paint_active_segment(self, start: HandPoint, end: HandPoint) -> None:
        width = self._active_display_width()
        display_start = self._canvas_to_display(start)
        display_end = self._canvas_to_display(end)
        if self._active_tool is HandTool.PEN:
            if start == end and not self._active_path_has_segment:
                self._active_path = QPainterPath(display_start)
                self._active_path.lineTo(display_start + QPointF(0.001, 0.0))
            elif not self._active_path_has_segment:
                self._active_path = QPainterPath(display_start)
                self._active_path.lineTo(display_end)
                self._active_path_has_segment = True
            else:
                self._active_path.lineTo(display_end)
            self._active_path_item.setPath(self._active_path)
            return
        self._active_overlay_item.paint_segment(
            display_start,
            display_end,
            self._active_tool,
            self._active_color,
            width,
        )

    def begin_active_hand(
        self,
        point: HandPoint,
        tool: HandTool,
        color: QColor,
        width: float,
    ) -> None:
        if self._image.isNull():
            return
        self._active_tool = tool
        self._active_color = QColor(color)
        self._active_width = float(width)
        self._active_last_canvas = point
        self._active_path = QPainterPath()
        self._active_path_has_segment = False
        if tool is HandTool.PEN:
            pen = QPen(self._active_color)
            pen.setWidthF(self._active_display_width())
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self._active_path_item.setPen(pen)
            self._active_path_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._active_overlay_item.clear()
            self._overlay_item.show()
            self._active_path_item.show()
            self._paint_active_segment(point, point)
            return
        if (
            not self._committed_overlay_image.isNull()
            and self._committed_overlay_image.size() == self._image.size()
        ):
            active_overlay_image = self._committed_overlay_image.copy()
        else:
            active_overlay_image = QImage(
                self._image.width(),
                self._image.height(),
                QImage.Format.Format_RGBA8888,
            )
            active_overlay_image.fill(QColor(0, 0, 0, 0))
        self._active_overlay_item.set_image(active_overlay_image)
        self._overlay_item.hide()
        self._active_overlay_item.show()
        self._active_path_item.hide()
        self._paint_active_segment(point, point)

    def append_active_hand(self, point: HandPoint) -> None:
        if self._active_last_canvas is None:
            return
        previous = self._active_last_canvas
        self._active_last_canvas = point
        self._paint_active_segment(previous, point)

    def cancel_active_hand(self) -> None:
        self._active_last_canvas = None
        self._active_path = QPainterPath()
        self._active_path_has_segment = False
        self._active_path_item.setPath(self._active_path)
        self._active_path_item.hide()
        self._active_overlay_item.clear()
        self._overlay_item.show()

    def viewport_to_canvas(self, viewport_point: QPointF) -> HandPoint | None:
        if self._image.isNull() or self._full_size[0] <= 0 or self._full_size[1] <= 0:
            return None
        scene_point = self.mapToScene(viewport_point.toPoint())
        item_point = self._item.mapFromScene(scene_point)
        bounds = self._item.boundingRect()
        if not bounds.contains(item_point):
            return None
        x = min(self._full_size[0] - 1e-6, max(0.0, item_point.x() * self._full_size[0] / bounds.width()))
        y = min(self._full_size[1] - 1e-6, max(0.0, item_point.y() * self._full_size[1] / bounds.height()))
        return HandPoint(x, y)

    @staticmethod
    def _source_over(base: QColor, overlay: QColor) -> QColor:
        overlay_alpha = overlay.alphaF()
        base_alpha = base.alphaF()
        output_alpha = overlay_alpha + base_alpha * (1.0 - overlay_alpha)
        if output_alpha <= 0.0:
            return QColor(0, 0, 0, 0)
        channels = [
            round(
                (
                    overlay_channel * overlay_alpha
                    + base_channel * base_alpha * (1.0 - overlay_alpha)
                )
                / output_alpha
            )
            for base_channel, overlay_channel in zip(
                (base.red(), base.green(), base.blue()),
                (overlay.red(), overlay.green(), overlay.blue()),
            )
        ]
        return QColor(*channels, round(output_alpha * 255))

    def image_color_at(self, viewport_point: QPointF) -> QColor | None:
        if self._image.isNull():
            return None
        scene_point = self.mapToScene(viewport_point.toPoint())
        item_point = self._item.mapFromScene(scene_point)
        bounds = self._item.boundingRect()
        if not bounds.contains(item_point):
            return None
        x = min(self._image.width() - 1, max(0, int(item_point.x())))
        y = min(self._image.height() - 1, max(0, int(item_point.y())))
        return self._image.pixelColor(x, y)

    def visible_color_at(self, viewport_point: QPointF) -> QColor | None:
        color = self.image_color_at(viewport_point)
        if color is None:
            return None
        scene_point = self.mapToScene(viewport_point.toPoint())
        item_point = self._item.mapFromScene(scene_point)
        x = min(self._image.width() - 1, max(0, int(item_point.x())))
        y = min(self._image.height() - 1, max(0, int(item_point.y())))
        if (
            self._overlay_item.isVisible()
            and not self._committed_overlay_image.isNull()
            and self._committed_overlay_image.size() == self._image.size()
        ):
            color = self._source_over(
                color,
                self._committed_overlay_image.pixelColor(x, y),
            )
        return color

    def geometry_generation(self) -> int | None:
        return self._geometry_generation

    def _update_viewport_cursor(self) -> None:
        if self._picking or self._hand_color_picking or self._drawing_enabled:
            cursor = Qt.CursorShape.CrossCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(cursor)

    def _update_brush_cursor(self, position: QPointF) -> None:
        if not self._drawing_enabled:
            self._cursor_item.hide()
            return
        point = self.viewport_to_canvas(position)
        if point is None:
            self._cursor_item.hide()
            return
        scene = self.mapToScene(position.toPoint())
        display_width = self._item.boundingRect().width()
        diameter = self._brush_width * display_width / max(1, self._full_size[0])
        diameter = max(1.0, diameter)
        self._cursor_item.setRect(scene.x() - diameter / 2, scene.y() - diameter / 2, diameter, diameter)
        self._cursor_item.show()

    def _begin_pointer(self, position: QPointF) -> bool:
        if not self._drawing_enabled:
            return False
        point = self.viewport_to_canvas(position)
        if point is None:
            return False
        self._pointer_active = True
        self.hand_pressed.emit(point)
        return True

    def _move_pointer(self, position: QPointF) -> bool:
        self._update_brush_cursor(position)
        if not self._pointer_active:
            return False
        point = self.viewport_to_canvas(position)
        if point is not None:
            self.hand_moved.emit(point)
        return True

    def _end_pointer(self, position: QPointF) -> bool:
        if not self._pointer_active:
            return False
        point = self.viewport_to_canvas(position)
        if point is not None:
            self.hand_moved.emit(point)
        self._pointer_active = False
        self.hand_released.emit()
        return True

    def _cancel_pointer(self) -> bool:
        if not self._pointer_active:
            return False
        self._pointer_active = False
        self.hand_cancelled.emit()
        return True

    def _cancel_pan(self) -> None:
        self._pan_start = None
        self._pan_scroll_values = None
        self._update_viewport_cursor()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.MiddleButton
            and self._zoom_mode != "fit"
            and not self._image.isNull()
        ):
            self._pan_start = event.position().toPoint()
            self._pan_scroll_values = (
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            temporary_hand_pick = bool(
                self._drawing_enabled
                and event.modifiers() & Qt.KeyboardModifier.AltModifier
            )
            if self._hand_color_picking or temporary_hand_pick:
                color = self.visible_color_at(event.position())
                if color is not None:
                    self.hand_color_picked.emit(color, temporary_hand_pick)
                    event.accept()
                    return
            if self._picking and not self._image.isNull():
                color = self.image_color_at(event.position())
                if color is not None:
                    self.color_picked.emit(color.red(), color.green(), color.blue())
                    event.accept()
                    return
            if self._begin_pointer(event.position()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan_start is not None and self._pan_scroll_values is not None:
            delta = event.position().toPoint() - self._pan_start
            horizontal, vertical = self._pan_scroll_values
            self.horizontalScrollBar().setValue(horizontal - delta.x())
            self.verticalScrollBar().setValue(vertical - delta.y())
            event.accept()
            return
        if self._pointer_active and not (event.buttons() & Qt.MouseButton.LeftButton):
            self._cancel_pointer()
            event.accept()
            return
        if self._move_pointer(event.position()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._cancel_pan()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._end_pointer(event.position()):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        handled = False
        if event.type() == QEvent.Type.TabletPress:
            handled = self._begin_pointer(event.position())
        elif event.type() == QEvent.Type.TabletMove:
            handled = self._move_pointer(event.position())
        elif event.type() == QEvent.Type.TabletRelease:
            handled = self._end_pointer(event.position())
        if handled:
            event.accept()
            return
        super().tabletEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.MouseMove
            and self._pointer_active
            and not (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            self._cancel_pointer()
            event.accept()
            return True
        if event.type() in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self._cancel_pointer()
            self._cancel_pan()
        elif (
            event.type() == QEvent.Type.Leave
            and self._pointer_active
            and not (QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        ):
            self._cancel_pointer()
        return super().viewportEvent(event)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802
        if watched is self.viewport():
            if (
                event.type() == QEvent.Type.MouseMove
                and self._pointer_active
                and not (event.buttons() & Qt.MouseButton.LeftButton)
            ):
                self._cancel_pointer()
                event.accept()
                return True
            if event.type() in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
                self._cancel_pointer()
                self._cancel_pan()
            elif (
                event.type() == QEvent.Type.Leave
                and self._pointer_active
                and not (QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
            ):
                self._cancel_pointer()
        return super().eventFilter(watched, event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._pointer_active and not (QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._cancel_pointer()
        if not self._pointer_active:
            self._cursor_item.hide()
        super().leaveEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._cancel_pointer()
        self._cancel_pan()
        super().focusOutEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.WindowDeactivate:
            self._cancel_pointer()
            self._cancel_pan()
        super().changeEvent(event)

    def _scene_center(self) -> QPointF | None:
        if self._item.pixmap().isNull() or self.viewport().rect().isEmpty():
            return None
        return self.mapToScene(self.viewport().rect().center())

    def _bounded_scene_center(self, point: QPointF | None) -> QPointF | None:
        if point is None:
            return None
        bounds = self._item.sceneBoundingRect()
        if bounds.isEmpty():
            return None
        return QPointF(
            min(bounds.right(), max(bounds.left(), point.x())),
            min(bounds.bottom(), max(bounds.top(), point.y())),
        )

    def _fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def _apply_zoom(self) -> None:
        if self._item.pixmap().isNull():
            return
        center = self._scene_center()
        if self._zoom_mode == "fit":
            self._fit()
            return
        self.resetTransform()
        full_width = max(1, self._full_size[0])
        display_width = max(1, self._image.width())
        scale = full_width / display_width * (zoom_factor_for_mode(self._zoom_mode) or 1.0)
        self.scale(scale, scale)
        if bounded_center := self._bounded_scene_center(center):
            self.centerOn(bounded_center)

    def resizeEvent(self, event) -> None:  # noqa: N802
        center = self._scene_center()
        if self._zoom_mode != "fit" and event.oldSize().isValid():
            size_delta = event.oldSize() - event.size()
            old_center_position = self.viewport().rect().center() + QPoint(
                size_delta.width() // 2,
                size_delta.height() // 2,
            )
            center = self.mapToScene(old_center_position)
        super().resizeEvent(event)
        if self._zoom_mode == "fit":
            self._fit()

        elif bounded_center := self._bounded_scene_center(center):
            self.centerOn(bounded_center)


class EditDropZone(QWidget):
    choose_requested = Signal()
    path_dropped = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 340)
        self._empty = True
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.preview = EditPreview()
        self.preview.setAcceptDrops(False)
        self.preview.viewport().setAcceptDrops(True)
        self.preview.viewport().installEventFilter(self)
        self._stack.addWidget(self.preview)
        self.overlay = RoundedDropOverlay()
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
        self.overlay.set_drop_active(active)

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
        custom_stem: str | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.source = source
        self.folder = folder
        self.settings = settings
        self.output_format = output_format
        self.jpeg_background = jpeg_background
        self.quality = quality
        self.custom_stem = custom_stem

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
                    self.custom_stem,
                )
            )
        except MissingSourceError:
            LOGGER.exception("Quick edit UI source missing: %s", self.source)
            self.failed.emit(MISSING_SOURCE_MESSAGE)
        except EditProcessingError as exc:
            LOGGER.exception("Quick edit UI export failed: %s", self.source)
            self.failed.emit(str(exc))
        except Exception:
            LOGGER.exception("Unexpected quick edit UI export failure: %s", self.source)
            self.failed.emit("加工した画像を保存できませんでした。")
        finally:
            self.finished.emit()


class PaletteExtractionThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str, object)

    def __init__(self, source: Path, settings: EditSettings, generation: int, request_id: int, source_identity, settings_signature) -> None:
        super().__init__()
        self.source = source
        self.settings = settings
        self.generation = generation
        self.request_id = request_id
        self.source_identity = source_identity
        self.settings_signature = settings_signature

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            source = load_normalized(self.source)
            source.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            prepared = prepare_palette_source(source, self.settings)
            mapping = extract_palette(prepared, self.settings.palette.color_count)
            if self.isInterruptionRequested():
                return
            self.succeeded.emit((self.generation, self.request_id, self.source_identity, self.settings_signature, mapping))
        except Exception:
            LOGGER.exception("Palette extraction failed: %s", self.source)
            self.failed.emit("代表色を抽出できませんでした。", (self.generation, self.request_id, self.source_identity, self.settings_signature))


class EditPreviewWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, request) -> None:
        super().__init__()
        self.request = request

    @Slot()
    def run(self) -> None:
        generation, request_id, source, identity, settings, show_original = self.request
        try:
            require_source_file(source)
            if show_original:
                image = load_normalized(source)
                image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            else:
                base_settings = replace(settings, hand_draw=HandDrawSettings())
                image = render_path_preview(source, base_settings, 1400)
            payload = (
                generation,
                request_id,
                source,
                identity,
                settings,
                show_original,
                pil_to_qimage(image),
                image.width,
                image.height,
            )
            self.succeeded.emit(payload)
        except Exception as exc:
            LOGGER.exception("Quick edit preview failed: %s", source)
            message = MISSING_SOURCE_MESSAGE if not source.is_file() else f"プレビューを更新できませんでした: {exc}"
            self.failed.emit((generation, request_id, source, identity, settings, show_original, message))
        finally:
            self.finished.emit()


class QuickEditPage(QWidget):
    processing_changed = Signal(bool)
    source_change_requested = Signal(object)
    result_handoff_requested = Signal(object, str)
    palette_handoff_requested = Signal(object)
    palette_open_requested = Signal()

    def __init__(self, service: EditService | None = None) -> None:
        super().__init__()
        self.service = service or EditService()
        self._workspace_managed = False
        self.font_catalog = FontCatalog()
        self.source_path: Path | None = None
        self.output_folder: Path | None = None
        self._output_folder_explicit = False
        self._source_size = (0, 0)
        self._source_format = ""
        self._source_size_bytes = 0
        self._source_has_alpha = False
        self._source_alpha_min = 255
        self._last_output: Path | None = None
        self._thread: QThread | None = None
        self._worker: EditExportWorker | None = None
        self._palette_thread: PaletteExtractionThread | None = None
        self._palette_request_id = 0
        self._palette_active_request = None
        self._palette_pending_request = None
        self._palette_preview_refresh_pending = False
        self._palette_terminal_preview_status: tuple[str, str] | None = None
        self._palette_terminal_activity_pending = False
        self._preview_refresh_without_activity = False
        self._palette_preview_handoff_generation: int | None = None
        self._processing_controls_locked = False
        self._processing_palette_requeue = False
        self._preview_thread: QThread | None = None
        self._preview_worker: EditPreviewWorker | None = None
        self._preview_request_id = 0
        self._preview_generation = 0
        self._preview_active_request = None
        self._preview_pending_request = None
        self._preview_active_result = None
        self._preview_activity_token: int | None = None
        self._palette_activity_token: int | None = None
        self._applying = True
        self._show_original = False
        self._history: list[EditSettings] = []
        self._history_index = -1
        self._text_color = QColor("#FFFFFF")
        self._outline_color = QColor("#000000")
        self._target_color = QColor("#FFFFFF")
        self._canvas_color = QColor("#FFFFFF")
        self._sticker_outline_color = QColor("#FFFFFF")
        self._line_art_color = QColor("#000000")
        self._line_art_background_color = QColor("#FFFFFF")
        self._hand_draw_settings = HandDrawSettings()
        self._mosaic_settings = MosaicSettings()
        self._mosaic_tool = MosaicTool.MOSAIC
        self._mosaic_size = 36
        self._mosaic_block_size = 12
        self._active_mosaic_points: list[HandPoint] = []
        self._active_mosaic_generation: int | None = None
        self._hand_tool = HandTool.PEN
        self._hand_color = QColor("#000000")
        self._hand_size = 8
        self._active_hand_points: list[HandPoint] = []
        self._active_hand_generation: int | None = None
        self._active_hand_tool = HandTool.PEN
        self._active_hand_color = QColor("#000000")
        self._active_hand_width = 8.0
        self._palette_values: tuple[tuple[int, int, int], ...] = ()
        self._palette_replacements: tuple[tuple[int, int, int], ...] = ()
        self._palette_mapping: tuple[int, ...] = ()
        self._palette_mapping_size = (0, 0)
        self._palette_mapping_digest = ""
        self._palette_generation = 0
        self._selected_palette_index = -1
        self._palette_source_signature = None
        self._palette_needs_reextract = False
        self._palette_extracted_color_count: int | None = None
        self._palette_status_message = ""
        self._palette_reset_buttons: list[QToolButton] = []
        self._invalidating_palette = False
        self._text_history_dirty = False
        self._build_ui()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self.update_preview)
        self._text_history_timer = QTimer(self)
        self._text_history_timer.setSingleShot(True)
        self._text_history_timer.setInterval(280)
        self._text_history_timer.timeout.connect(self._commit_text_history)
        self._applying = False
        self._history = [self.settings()]
        self._history_index = 0
        self._palette_source_signature = self._upstream_signature(self.settings())
        self._update_visibility()
        self._update_actions()

    def _build_ui(self) -> None:
        self.setStyleSheet(EDIT_STYLE)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("edit_workspace")

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.settings_scroll.setMinimumWidth(PRIMARY_SETTINGS_PANE_MIN_WIDTH)
        self.settings_scroll.setMaximumWidth(PRIMARY_SETTINGS_PANE_MAX_WIDTH)
        left = QWidget()
        left.setMinimumWidth(0)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 6, 4, 6)
        ll.setSpacing(2)
        heading = QLabel("何をしますか？")
        heading.setStyleSheet("font-size: 18px; font-weight: 700; color: #182230;")
        ll.addWidget(heading)
        self.current_source_card = CurrentSourceCard()
        self.current_source_card.change_requested.connect(self.choose_image)
        ll.addWidget(self.current_source_card)
        history_row = QGridLayout()
        history_row.setSpacing(4)
        self.undo_button = QPushButton("元に戻す")
        self.redo_button = QPushButton("やり直す")
        self.reset_button = QPushButton("加工をリセット")
        self.clear_all_button = QPushButton("作業をリセット")
        self.clear_all_button.setToolTip("元画像を残して、加工設定・履歴・保存結果を初期化します")
        self.clear_image_button = QPushButton("画像をクリア")
        self.clear_image_button.setToolTip("この画像加工タブから画像を外します。他のツールの現在画像は保持されます")
        for button in (self.undo_button, self.redo_button, self.reset_button, self.clear_all_button, self.clear_image_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.reset_button.clicked.connect(self.reset_edits)
        self.clear_all_button.clicked.connect(self.clear_all)
        self.clear_image_button.clicked.connect(self.clear_image)
        history_row.addWidget(self.undo_button, 0, 0)
        history_row.addWidget(self.redo_button, 0, 1)
        history_row.addWidget(self.reset_button, 1, 0)
        history_row.addWidget(self.clear_all_button, 1, 1)
        history_row.addWidget(self.clear_image_button, 2, 0, 1, 2)
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
            "",
            filter_content,
            True,
        )
        self.filter_section.toggle.setToolTip("画像の色合いや雰囲気を選びます")
        ll.addWidget(self.filter_section)

        text_content = QWidget()
        text_layout = QVBoxLayout(text_content)
        text_layout.setContentsMargins(6, 1, 3, 4)
        text_layout.setSpacing(4)
        self.text_enabled = QCheckBox()
        self.text_enabled.setVisible(False)
        self.text_details = QWidget()
        text_form = QFormLayout(self.text_details)
        self._configure_form(text_form)
        self.text_edit = IMEPlainTextEdit()
        self.text_edit.setPlaceholderText("例：おはよう")
        self.text_edit.setFixedHeight(64)
        self.font_combo = FontPickerButton(self.font_catalog)
        self._select_default_font()
        self.font_size_spin = self._spin(8, 500, 64, " px")
        self.bold_check = QCheckBox("太字")
        self.bold_check.setChecked(True)
        self.text_color_button = QPushButton()
        self.text_color_button.setProperty("showAlphaValue", True)
        self.text_color_button.clicked.connect(self.choose_text_color)
        self.outline_enabled = QCheckBox("文字の縁取りを付ける")
        self.outline_enabled.setChecked(True)
        self.outline_color_button = QPushButton()
        self.outline_color_button.setProperty("showAlphaValue", True)
        self.outline_color_button.clicked.connect(self.choose_outline_color)
        self.outline_width_spin = self._spin(0, 40, 4, " px")
        self.position_combo = QComboBox()
        for position in TextPosition:
            self.position_combo.addItem(POSITION_LABELS[position], position.value)
        self.position_combo.setCurrentIndex(self.position_combo.findData(TextPosition.BOTTOM_CENTER.value))
        self.text_clear_button = QPushButton("文字を削除")
        self.text_clear_button.clicked.connect(self.clear_text)
        text_form.addRow("文字", self.text_edit)
        text_form.addRow("フォント", self.font_combo)
        text_form.addRow("文字サイズ", self.font_size_spin)
        text_form.addRow("", self.bold_check)
        text_form.addRow("文字色", self.text_color_button)
        text_form.addRow("", self.outline_enabled)
        text_form.addRow("縁取りの色", self.outline_color_button)
        text_form.addRow("縁取りの太さ", self.outline_width_spin)
        text_form.addRow("位置", self.position_combo)
        text_form.addRow("", self.text_clear_button)
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
            "",
            text_content,
        )
        self.text_section.toggle.setToolTip("文字を入力すると加工結果へ反映します")
        ll.addWidget(self.text_section)

        hand_content = QWidget()
        hand_layout = QVBoxLayout(hand_content)
        hand_layout.setContentsMargins(5, 1, 3, 1)
        hand_layout.setSpacing(3)
        hand_mode_row = QHBoxLayout()
        hand_mode_row.setContentsMargins(0, 0, 0, 0)
        hand_mode_row.setSpacing(6)
        hand_mode_row.addWidget(QLabel("描画モード"))
        hand_mode_row.addStretch()
        self.hand_mode_enabled = QCheckBox("ON")
        self.hand_mode_enabled.setToolTip("プレビュー上へペンや消しゴムで描ける状態にします")
        self.hand_mode_enabled.setAccessibleName("手書きの描画モード")
        hand_mode_row.addWidget(self.hand_mode_enabled)
        self.hand_details = QWidget()
        hand_form = QFormLayout(self.hand_details)
        self._configure_form(hand_form)
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(4)
        self.hand_tool_group = QButtonGroup(self)
        self.hand_tool_group.setExclusive(True)
        self.hand_pen_button = QPushButton("ペン")
        self.hand_eraser_button = QPushButton("消しゴム")
        self.hand_eyedropper_button = QPushButton("スポイト")
        for button, tool, tooltip in (
            (self.hand_pen_button, HandTool.PEN, "選んだ色で描きます"),
            (self.hand_eraser_button, HandTool.ERASER, "手書き部分だけを消します"),
            (self.hand_eyedropper_button, None, "表示中の画像からペン色を選びます"),
        ):
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(tooltip)
            button.setAccessibleName(button.text())
            self.hand_tool_group.addButton(button)
            tool_row.addWidget(button, 1)
            button.clicked.connect(lambda _checked=False, selected=tool: self._select_hand_tool(selected))
        self.hand_pen_button.setChecked(True)
        self.hand_eyedropper_button.setAccessibleName("手描きのスポイト")
        self.hand_color_button = QPushButton()
        self.hand_color_button.setProperty("showAlphaValue", False)
        self.hand_color_button.setProperty("colorPurpose", "手書きの色を選びます")
        self.hand_color_button.clicked.connect(self.choose_hand_color)
        self.hand_size_spin = self._spin(1, 100, 8, " px")
        self.hand_size_spin.setAccessibleName("手書きの太さ")
        self.hand_opacity_spin = self._spin(0, 100, 100, "%")
        self.hand_opacity_spin.setAccessibleName("手書きの不透明度")
        self.hand_opacity_spin.setToolTip("これから描く線の不透明度を設定します")
        self.hand_visible_check = QCheckBox("表示")
        self.hand_visible_check.setChecked(True)
        self.hand_visible_check.setToolTip("手書きレイヤーをプレビューと保存画像へ表示します")
        self.hand_visible_check.setAccessibleName("手書きレイヤーをプレビューと保存画像へ表示")
        self.hand_clear_button = QPushButton("全消去")
        self.hand_clear_button.setToolTip("手書きをすべて消す")
        self.hand_clear_button.setAccessibleName("手書きレイヤーをすべて消す")
        self.hand_clear_button.setMinimumWidth(0)
        self.hand_clear_button.setMinimumHeight(28)
        self.hand_clear_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.hand_clear_button.clicked.connect(self.clear_hand_draw)
        hand_mode_row.addWidget(self.hand_visible_check)
        hand_mode_row.addWidget(self.hand_clear_button, 1)
        hand_layout.addLayout(hand_mode_row)
        hand_form.addRow("道具", tool_row)
        hand_form.addRow("色", self.hand_color_button)
        hand_form.addRow("太さ", self.hand_size_spin)
        hand_form.addRow("不透明度", self.hand_opacity_spin)
        hand_layout.addWidget(self.hand_details)
        hand_content.setStyleSheet(
            "QPushButton:checked { background: #315fbd; color: white; "
            "border: 2px solid #173a82; font-weight: 700; }"
        )
        self.hand_section = CollapsibleSection(
            "手書き",
            "",
            hand_content,
        )
        self.hand_section.toggle.setToolTip("プレビューへ直接描き、加工結果の一番上へ重ねます")
        ll.addWidget(self.hand_section)

        mosaic_content = QWidget()
        mosaic_layout = QVBoxLayout(mosaic_content)
        mosaic_layout.setContentsMargins(5, 1, 3, 1)
        mosaic_layout.setSpacing(3)
        mosaic_mode_row = QHBoxLayout()
        mosaic_mode_row.addWidget(QLabel("描画モード"))
        mosaic_mode_row.addStretch()
        self.mosaic_mode_enabled = QCheckBox("ON")
        self.mosaic_mode_enabled.setToolTip("プレビュー上をなぞった部分にモザイクをかけます")
        self.mosaic_mode_enabled.setAccessibleName("モザイクの描画モード")
        mosaic_mode_row.addWidget(self.mosaic_mode_enabled)
        self.mosaic_details = QWidget()
        mosaic_form = QFormLayout(self.mosaic_details)
        self._configure_form(mosaic_form)
        mosaic_tool_row = QHBoxLayout()
        mosaic_tool_row.setContentsMargins(0, 0, 0, 0)
        self.mosaic_tool_group = QButtonGroup(self)
        self.mosaic_pen_button = QPushButton("モザイク")
        self.mosaic_eraser_button = QPushButton("消しゴム")
        for button, tool in ((self.mosaic_pen_button, MosaicTool.MOSAIC), (self.mosaic_eraser_button, MosaicTool.ERASER)):
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.mosaic_tool_group.addButton(button)
            mosaic_tool_row.addWidget(button, 1)
            button.clicked.connect(lambda _checked=False, selected=tool: self._select_mosaic_tool(selected))
        self.mosaic_pen_button.setChecked(True)
        self.mosaic_size_spin = self._spin(4, 240, 36, " px")
        self.mosaic_size_spin.setAccessibleName("モザイクの大きさ")
        self.mosaic_block_spin = self._spin(2, 128, 12, " px")
        self.mosaic_block_spin.setAccessibleName("モザイクの粗さ")
        self.mosaic_visible_check = QCheckBox("表示")
        self.mosaic_visible_check.setChecked(True)
        self.mosaic_visible_check.setToolTip("モザイクレイヤーをプレビューと保存画像へ表示します")
        self.mosaic_clear_button = QPushButton("全消去")
        self.mosaic_clear_button.setToolTip("モザイクをすべて消します")
        self.mosaic_clear_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mosaic_clear_button.clicked.connect(self.clear_mosaic)
        mosaic_form.addRow("道具", mosaic_tool_row)
        mosaic_form.addRow("範囲", self.mosaic_size_spin)
        mosaic_form.addRow("粗さ", self.mosaic_block_spin)
        mosaic_mode_row.addWidget(self.mosaic_visible_check)
        mosaic_mode_row.addWidget(self.mosaic_clear_button, 1)
        mosaic_layout.addLayout(mosaic_mode_row)
        mosaic_layout.addWidget(self.mosaic_details)
        mosaic_content.setStyleSheet("QPushButton:checked { background: #315fbd; color: white; border: 2px solid #173a82; font-weight: 700; }")
        self.mosaic_section = CollapsibleSection("モザイク", "", mosaic_content)
        self.mosaic_section.toggle.setToolTip("プレビューをなぞった範囲を非破壊でモザイク加工します")
        ll.addWidget(self.mosaic_section)

        transparency_content = QWidget()
        transparency_layout = QVBoxLayout(transparency_content)
        transparency_layout.setContentsMargins(6, 1, 3, 1)
        transparency_layout.setSpacing(4)
        self.transparency_enabled = QCheckBox("背景を透明にする")
        transparency_layout.addWidget(self.transparency_enabled)
        self.transparency_details = QWidget()
        transparency_form = QFormLayout(self.transparency_details)
        self._configure_form(transparency_form)
        self.target_color_button = QPushButton()
        self.target_color_button.clicked.connect(self.choose_target_color)
        self.eyedropper_button = QPushButton("画像から選ぶ")
        self.eyedropper_button.setToolTip("画像から透明にしたい背景色を選びます")
        self.eyedropper_button.setAccessibleName("画像から透明にしたい背景色を選ぶ")
        self.eyedropper_button.setCheckable(True)
        self.eyedropper_button.toggled.connect(self._eyedropper_toggled)
        self.tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self.tolerance_slider.setRange(0, 255)
        self.tolerance_slider.setValue(24)
        self.tolerance_slider.setMinimumHeight(24)
        self.tolerance_spin = self._spin(0, 255, 24)
        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(self.tolerance_slider, 1)
        tolerance_row.addWidget(self.tolerance_spin)
        self.softness_slider = QSlider(Qt.Orientation.Horizontal)
        self.softness_slider.setRange(0, 100)
        self.softness_slider.setValue(8)
        self.softness_slider.setMinimumHeight(24)
        self.softness_spin = self._spin(0, 100, 8)
        softness_row = QHBoxLayout()
        softness_row.addWidget(self.softness_slider, 1)
        softness_row.addWidget(self.softness_spin)
        target_color_row = QHBoxLayout()
        target_color_row.setContentsMargins(0, 0, 0, 0)
        target_color_row.setSpacing(4)
        self.target_color_button.setMinimumWidth(0)
        self.target_color_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.eyedropper_button.setMinimumWidth(0)
        self.eyedropper_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        target_color_row.addWidget(self.target_color_button, 1)
        target_color_row.addWidget(self.eyedropper_button, 1)
        transparency_form.addRow("抜きたい背景色", target_color_row)
        transparency_form.addRow("色の許容範囲", tolerance_row)
        transparency_form.addRow("境界をなじませる", softness_row)
        transparency_layout.addWidget(self.transparency_details)
        self.transparency_section = CollapsibleSection(
            "背景を透明にする",
            "近い色もまとめて透明にします",
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
        self.custom_canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        custom_row = QHBoxLayout(self.custom_canvas)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.canvas_width_spin = self._spin(1, 10000, 320, " px")
        self.canvas_height_spin = self._spin(1, 10000, 320, " px")
        self.canvas_width_spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.canvas_height_spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
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
        self.canvas_color_button.setProperty("showAlphaValue", True)
        self.canvas_color_button.clicked.connect(self.choose_canvas_color)
        canvas_form.addRow("キャンバス", self.canvas_preset_combo)
        canvas_form.addRow("任意サイズ", self.custom_canvas)
        canvas_form.addRow("画像の配置", self.placement_combo)
        canvas_form.addRow("余白", self.padding_combo)
        canvas_form.addRow("背景", self.canvas_background_combo)
        canvas_form.addRow("背景の指定色", self.canvas_color_button)
        self.canvas_section = CollapsibleSection(
            "キャンバスを整える",
            "",
            canvas_content,
        )
        self.canvas_section.toggle.setToolTip("サイズ、配置、余白、背景を設定します")
        ll.addWidget(self.canvas_section)

        material_content = QWidget()
        material_layout = QVBoxLayout(material_content)
        material_layout.setContentsMargins(5, 1, 3, 1)
        material_layout.setSpacing(4)
        sticker_group = QGroupBox("ステッカー")
        sticker_layout = QVBoxLayout(sticker_group)
        sticker_layout.setContentsMargins(5, 4, 5, 4)
        sticker_layout.setSpacing(3)
        sticker_group.setToolTip("透明部分のある画像へフチや影を付けます")
        self.sticker_explanation_label = QLabel()
        self.sticker_explanation_label.hide()
        self.sticker_enabled = QCheckBox("ステッカーにする")
        sticker_layout.addWidget(self.sticker_enabled)
        self.sticker_details = QWidget()
        sticker_details_layout = QVBoxLayout(self.sticker_details)
        sticker_details_layout.setContentsMargins(0, 0, 0, 0)
        sticker_details_layout.setSpacing(3)
        self.sticker_prereq_status = QLabel()
        self.sticker_prereq_status.setWordWrap(True)
        self.sticker_prereq_status.setStyleSheet("color: #9a6700;")
        sticker_details_layout.addWidget(self.sticker_prereq_status)
        self.sticker_prereq_button = QToolButton()
        self.sticker_prereq_button.setText("背景透過を開く →")
        self.sticker_prereq_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sticker_prereq_button.setToolTip("背景を透明にする設定へ移動します")
        self.sticker_prereq_button.setAccessibleName("背景を透明にする設定を開く")
        self.sticker_prereq_button.setMinimumHeight(28)
        self.sticker_prereq_button.setStyleSheet(
            "QToolButton { color: #2457b2; background: transparent; border: 0; "
            "padding: 4px 2px; text-align: left; font-weight: 600; }"
            "QToolButton:hover { color: #173a82; text-decoration: underline; }"
            "QToolButton:focus { border: 1px solid #2457b2; border-radius: 4px; }"
        )
        self.sticker_prereq_button.setMinimumWidth(0)
        self.sticker_prereq_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.sticker_prereq_button.clicked.connect(self.open_transparency_settings)
        sticker_details_layout.addWidget(self.sticker_prereq_button)
        sticker_form = QFormLayout()
        self._configure_form(sticker_form)
        self.sticker_outline_width_spin = self._spin(1, 50, 8, " px")
        self.sticker_outline_color_button = QPushButton()
        self.sticker_outline_color_button.setProperty("showAlphaValue", True)
        self.sticker_outline_color_button.clicked.connect(self.choose_sticker_color)
        self.sticker_shadow_enabled = QCheckBox("影を付ける")
        sticker_form.addRow("フチ色", self.sticker_outline_color_button)
        sticker_form.addRow("フチ太さ", self.sticker_outline_width_spin)
        sticker_form.addRow("", self.sticker_shadow_enabled)
        sticker_details_layout.addLayout(sticker_form)
        sticker_layout.addWidget(self.sticker_details)
        material_layout.addWidget(sticker_group)
        line_group = QGroupBox("輪郭・線表現")
        self.line_art_group = line_group
        line_layout = QVBoxLayout(line_group)
        line_layout.setContentsMargins(5, 4, 5, 4)
        line_layout.setSpacing(4)
        self.line_art_description = QLabel("線主体の表現へ変えます")
        self.line_art_description.setWordWrap(True)
        self.line_art_description.setStyleSheet("color: #667085;")
        line_layout.addWidget(self.line_art_description)
        self.line_art_enabled = QCheckBox("線で表現する")
        self.line_art_enabled.setAccessibleName("輪郭・線表現を有効にする")
        line_layout.addWidget(self.line_art_enabled)
        self.line_art_details = QWidget()
        line_form = QFormLayout(self.line_art_details)
        self._configure_form(line_form)
        line_form.setContentsMargins(0, 1, 0, 0)
        line_form.setVerticalSpacing(3)
        self.line_art_color_button = QPushButton()
        self.line_art_color_button.setProperty("showAlphaValue", True)
        self.line_art_color_button.setProperty(
            "colorPurpose", "輪郭・線表現に使う線の色を選びます"
        )
        self.line_art_color_button.setAccessibleName("線の色")
        self.line_art_color_button.clicked.connect(self.choose_line_art_color)
        self.line_art_background_combo = QComboBox()
        for label, value in (("透明", LineArtBackground.TRANSPARENT.value), ("白", LineArtBackground.WHITE.value), ("黒", LineArtBackground.BLACK.value), ("指定色", LineArtBackground.CUSTOM.value)):
            self.line_art_background_combo.addItem(label, value)
        self.line_art_background_combo.setAccessibleName("輪郭・線表現の背景")
        self.line_art_background_combo.setToolTip("線以外の背景を選びます")
        self.line_art_background_color_button = QPushButton()
        self.line_art_background_color_button.setProperty("showAlphaValue", True)
        self.line_art_background_color_button.setProperty(
            "colorPurpose", "輪郭・線表現の背景色を選びます"
        )
        self.line_art_background_color_button.setAccessibleName("輪郭・線表現の指定背景色")
        self.line_art_background_color_button.clicked.connect(self.choose_line_art_background_color)
        line_form.addRow("線色", self.line_art_color_button)
        line_form.addRow("背景", self.line_art_background_combo)
        line_form.addRow("指定色", self.line_art_background_color_button)
        line_layout.addWidget(self.line_art_details)
        material_layout.addWidget(line_group)
        palette_group = QGroupBox("色を整理・変える")
        palette_group.setMinimumWidth(0)
        palette_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        palette_layout = QVBoxLayout(palette_group)
        palette_layout.setContentsMargins(5, 4, 5, 4)
        palette_layout.setSpacing(3)
        self.palette_enabled = QCheckBox("代表色を抽出")
        self.palette_enabled.setVisible(False)
        palette_row = QHBoxLayout()
        self.palette_count_combo = QComboBox()
        for count in (5, 6, 8, 12):
            self.palette_count_combo.addItem(f"{count}色", count)
        self.palette_count_combo.setCurrentIndex(self.palette_count_combo.findData(6))
        self.palette_extract_button = QPushButton("色を取り出す")
        self.palette_extract_button.clicked.connect(self.extract_palette)
        palette_row.addWidget(self.palette_count_combo, 1)
        palette_row.addWidget(self.palette_extract_button, 1)
        self.palette_intro_label = QLabel(
            "色数を増やすと近い色を細かく分けられます"
        )
        self.palette_intro_label.setWordWrap(True)
        self.palette_intro_label.setStyleSheet("color: #667085;")
        palette_layout.addWidget(self.palette_intro_label)
        palette_layout.addLayout(palette_row)
        self.palette_results_widget = QWidget()
        self.palette_results_widget.setMinimumWidth(0)
        self.palette_results_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        palette_results_layout = QVBoxLayout(self.palette_results_widget)
        palette_results_layout.setContentsMargins(0, 0, 0, 0)
        palette_results_layout.setSpacing(2)
        self.palette_quantize_enabled = QCheckBox()
        self.palette_quantize_guide_label = QLabel()
        self.palette_quantize_guide_label.setWordWrap(True)
        self.palette_quantize_guide_label.setStyleSheet("color: #9a6700;")
        palette_results_layout.addWidget(self.palette_quantize_guide_label)
        blend_row = QHBoxLayout()
        blend_row.setContentsMargins(0, 0, 0, 0)
        blend_row.setSpacing(4)
        self.palette_blend_mode_combo = QComboBox()
        compact_blend_labels = {
            RecolorBlendMode.SHARP: "鮮明",
            RecolorBlendMode.SMOOTH: "滑らか",
            RecolorBlendMode.PRESERVE_SHADING: "陰影",
        }
        for mode in (RecolorBlendMode.SHARP, RecolorBlendMode.SMOOTH, RecolorBlendMode.PRESERVE_SHADING):
            self.palette_blend_mode_combo.addItem(compact_blend_labels[mode], mode.value)
        self.palette_blend_mode_combo.setToolTip("くっきり\n色面をはっきり分けます\n\nなめらか\n色の境目を自然につなぎます\n\n陰影を残す\n元画像の明るさを残して色を変えます")
        self.palette_blend_mode_combo.setAccessibleName("色のなじみ")
        self.palette_quantize_enabled.setMinimumWidth(0)
        self.palette_quantize_enabled.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.palette_blend_mode_combo.setMinimumWidth(0)
        self.palette_blend_mode_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        blend_row.addWidget(self.palette_quantize_enabled, 1)
        blend_row.addWidget(self.palette_blend_mode_combo, 1)
        palette_results_layout.addLayout(blend_row)
        self.palette_blend_description_label = QLabel()
        self.palette_blend_description_label.setWordWrap(True)
        self.palette_blend_description_label.setStyleSheet("color: #667085;")
        palette_results_layout.addWidget(self.palette_blend_description_label)
        self.palette_current_label = QLabel("現在の配色")
        self.palette_current_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #182230;")
        palette_results_layout.addWidget(self.palette_current_label)
        self.palette_instruction_label = QLabel("抽出した色をクリックして、好きな配色へ変更できます。")
        self.palette_instruction_label.setWordWrap(True)
        self.palette_instruction_label.setStyleSheet("color: #667085;")
        palette_results_layout.addWidget(self.palette_instruction_label)
        self.palette_columns_widget = QWidget()
        palette_columns_layout = QGridLayout(self.palette_columns_widget)
        palette_columns_layout.setContentsMargins(0, 0, 0, 0)
        palette_columns_layout.setHorizontalSpacing(6)
        palette_columns_layout.addWidget(QLabel("元の色"), 0, 0)
        palette_columns_layout.addWidget(QLabel(""), 0, 1)
        palette_columns_layout.addWidget(QLabel("変更後"), 0, 2)
        palette_results_layout.addWidget(self.palette_columns_widget)
        self.palette_chips_widget = QWidget()
        self.palette_chips_layout = QGridLayout(self.palette_chips_widget)
        self.palette_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_chips_layout.setHorizontalSpacing(2)
        self.palette_chips_layout.setVerticalSpacing(0)
        palette_results_layout.addWidget(self.palette_chips_widget)
        self.palette_reset_button = QPushButton("元の配色に戻す")
        self.palette_reset_button.setMinimumWidth(0)
        self.palette_reset_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.palette_reset_button.clicked.connect(self.reset_palette)
        palette_results_layout.addWidget(self.palette_reset_button)
        self.palette_other_uses_label = QLabel("ほかで使う")
        self.palette_other_uses_label.setStyleSheet("color: #667085; font-size: 12px; font-weight: 700;")
        palette_results_layout.addWidget(self.palette_other_uses_label)
        palette_actions_row = QHBoxLayout()
        self.palette_send_button = QPushButton("ドット絵へ送る")
        self.palette_send_button.setToolTip("現在の配色をドット絵パレットへ送る")
        self.palette_send_button.setMinimumWidth(0)
        self.palette_send_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.palette_send_button.clicked.connect(self.send_palette_to_pixel)
        palette_actions_row.addWidget(self.palette_send_button, 1)
        self.palette_open_button = QToolButton()
        self.palette_open_button.setText("ドット絵へ")
        self.palette_open_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.palette_open_button.setToolTip("ドット絵画面へ移動します")
        self.palette_open_button.setAccessibleName("ドット絵を開く")
        self.palette_open_button.setMinimumHeight(28)
        self.palette_open_button.setMinimumWidth(0)
        self.palette_open_button.setMaximumWidth(90)
        self.palette_open_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.palette_open_button.clicked.connect(self.open_pixel_tab)
        palette_actions_row.addWidget(self.palette_open_button, 1)
        self.palette_send_button.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 2px; min-height: 24px; }"
            "QPushButton:focus { padding: 1px; }"
        )
        self.palette_open_button.setStyleSheet(
            "QToolButton { color: #2457b2; background: transparent; border: 0; "
            "font-size: 11px; padding: 3px 2px; font-weight: 600; }"
            "QToolButton:hover { color: #173a82; text-decoration: underline; }"
            "QToolButton:focus { border: 1px solid #2457b2; border-radius: 4px; }"
        )
        palette_results_layout.addLayout(palette_actions_row)
        palette_layout.addWidget(self.palette_results_widget)
        self.palette_feedback_label = QLabel()
        self.palette_feedback_label.setWordWrap(True)
        self.palette_feedback_label.setStyleSheet("color: #137333; font-weight: 700;")
        palette_layout.addWidget(self.palette_feedback_label)
        material_layout.addWidget(palette_group)
        self.material_section = CollapsibleSection("素材化", "", material_content)
        self.material_section.toggle.setToolTip("ステッカー、輪郭・線表現、代表色を設定します")
        ll.addWidget(self.material_section)
        ll.addStretch()
        self.settings_scroll.setWidget(left)
        self.settings_panel = QWidget()
        self.settings_panel.setObjectName("edit_settings_panel")
        self.settings_panel.setMinimumWidth(PRIMARY_SETTINGS_PANE_MIN_WIDTH)
        self.settings_panel.setMaximumWidth(PRIMARY_SETTINGS_PANE_MAX_WIDTH)
        settings_panel_layout = QVBoxLayout(self.settings_panel)
        settings_panel_layout.setContentsMargins(0, 0, 0, 0)
        settings_panel_layout.setSpacing(0)
        settings_panel_layout.addWidget(self.settings_scroll, 1)
        self.settings_footer = QFrame()
        self.settings_footer.setObjectName("edit_settings_footer")
        self.settings_footer.setStyleSheet(
            "QFrame#edit_settings_footer { background:#f8fafc; border-top:1px solid #cbd5e1; }"
        )
        settings_footer_layout = QHBoxLayout(self.settings_footer)
        settings_footer_layout.setContentsMargins(8, 6, 8, 8)
        settings_footer_layout.setSpacing(4)
        set_operation_role(self.reset_button, "secondary")
        set_operation_role(self.clear_all_button, "secondary")
        settings_footer_layout.addWidget(self.reset_button, 1)
        settings_footer_layout.addWidget(self.clear_all_button, 1)
        settings_panel_layout.addWidget(self.settings_footer)
        splitter.addWidget(self.settings_panel)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(8, 8, 8, 8)
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
        zoom_row, self.preview_zoom_group, self.preview_zoom_buttons = create_preview_zoom_row(
            self,
            lambda mode: self.drop_zone.preview.set_zoom_mode(mode),
        )
        cl.addLayout(zoom_row)
        self.preview_activity = PreviewActivityIndicator()
        cl.addWidget(self.preview_activity)
        self.drop_zone = EditDropZone()
        self.drop_zone.choose_requested.connect(self.choose_image)
        self.drop_zone.path_dropped.connect(self._request_or_load_image)
        self.drop_zone.preview.color_picked.connect(self._color_picked)
        self.drop_zone.preview.hand_color_picked.connect(self._hand_color_picked)
        self.drop_zone.preview.hand_pressed.connect(self._begin_hand_stroke)
        self.drop_zone.preview.hand_moved.connect(self._append_hand_point)
        self.drop_zone.preview.hand_released.connect(self._finish_hand_stroke)
        self.drop_zone.preview.hand_cancelled.connect(self._cancel_hand_stroke)
        cl.addWidget(self.drop_zone, 1)
        self.preview_history_bar = QWidget()
        self.preview_history_bar.setObjectName("previewHandHistory")
        preview_history_layout = QHBoxLayout(self.preview_history_bar)
        preview_history_layout.setContentsMargins(0, 0, 0, 0)
        preview_history_layout.setSpacing(4)
        preview_history_layout.addStretch()
        self.preview_undo_button = QToolButton()
        self.preview_redo_button = QToolButton()
        for button, text, tooltip, accessible_name in (
            (self.preview_undo_button, "↶", "直前の操作を元に戻します", "プレビュー付近で元に戻す"),
            (self.preview_redo_button, "↷", "元に戻した操作をやり直します", "プレビュー付近でやり直す"),
        ):
            button.setText(text)
            button.setToolTip(tooltip)
            button.setAccessibleName(accessible_name)
            button.setFixedSize(34, 28)
            button.setStyleSheet(
                "QToolButton { background: #e8eef5; color: #182230; border: 1px solid #9aabba; "
                "border-radius: 6px; font-size: 18px; font-weight: 700; padding: 0; }"
                "QToolButton:hover { background: #dbe6f1; border-color: #405b79; }"
                "QToolButton:focus { border: 2px solid #2457b2; }"
                "QToolButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
            )
            preview_history_layout.addWidget(button)
        self.preview_undo_button.setObjectName("previewUndo")
        self.preview_redo_button.setObjectName("previewRedo")
        self.preview_undo_button.clicked.connect(self.undo)
        self.preview_redo_button.clicked.connect(self.redo)
        self.preview_history_bar.hide()
        cl.addWidget(self.preview_history_bar)
        self.preview_status = QLabel("画像を読み込むと、ここへ加工結果を表示します")
        self.preview_status.setWordWrap(True)
        self.preview_status.setStyleSheet("color: #667085;")
        cl.addWidget(self.preview_status)
        splitter.addWidget(center)

        self.save_scroll = QScrollArea()
        self.save_scroll.setObjectName("editSaveScroll")
        self.save_scroll.setWidgetResizable(True)
        self.save_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.save_scroll.setMinimumWidth(220)
        self.save_scroll.setMaximumWidth(360)
        right = QWidget()
        right.setObjectName("editSavePanel")
        self.save_panel = right
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 10, 8)
        info_title = QLabel("画像情報 / 保存")
        info_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        rl.addWidget(info_title)
        self.original_info = QLabel("元画像\n画像を読み込んでください")
        self.output_info = QLabel("加工後\n—")
        info_style = "padding: 8px; background: #f4f6f8; border: 1px solid #d7dde5; border-radius: 8px; color: #273142;"
        for label in (self.original_info, self.output_info):
            label.setWordWrap(True)
            label.setStyleSheet(info_style)
            rl.addWidget(label)
        self.save_options_grid = QGridLayout()
        self.save_options_grid.setContentsMargins(3, 1, 3, 2)
        self.save_options_grid.setHorizontalSpacing(8)
        self.save_options_grid.setVerticalSpacing(4)
        self.save_options_grid.setColumnStretch(1, 1)
        self.format_combo = QComboBox()
        self.format_combo.addItem("元の形式", EditOutputFormat.SAME.value)
        self.format_combo.addItem("PNG", EditOutputFormat.PNG.value)
        self.format_combo.addItem("JPEG", EditOutputFormat.JPEG.value)
        self.format_combo.addItem("WebP", EditOutputFormat.WEBP.value)
        self.quality_spin = self._spin(1, 100, 95, "%")
        self.jpeg_background_combo = QComboBox()
        self.jpeg_background_combo.addItem("白", (255, 255, 255))
        self.jpeg_background_combo.addItem("黒", (0, 0, 0))
        self.format_label = QLabel("保存形式")
        self.quality_label = QLabel("画質")
        self.jpeg_background_label = QLabel("JPEGの\n透明部分")
        self.jpeg_background_label.setAccessibleName("JPEGの透明部分")
        for row, (label, field) in enumerate(
            (
                (self.format_label, self.format_combo),
                (self.quality_label, self.quality_spin),
                (self.jpeg_background_label, self.jpeg_background_combo),
            )
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setMinimumWidth(0)
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.save_options_grid.addWidget(label, row, 0)
            self.save_options_grid.addWidget(field, row, 1)
        rl.addLayout(self.save_options_grid)
        self.alpha_hint = QLabel()
        self.alpha_hint.setWordWrap(True)
        self.alpha_hint.setStyleSheet("color: #9a6700;")
        rl.addWidget(self.alpha_hint)
        rl.addWidget(QLabel("ファイル名"))
        filename_row = QHBoxLayout()
        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("保存する名前")
        self.filename_edit.textChanged.connect(self._update_save_panel)
        self.filename_edit.editingFinished.connect(self._normalize_output_filename_input)
        filename_row.addWidget(self.filename_edit, 1)
        self.filename_suffix_label = QLabel(".png")
        self.filename_suffix_label.setStyleSheet("color: #475467;")
        filename_row.addWidget(self.filename_suffix_label)
        rl.addLayout(filename_row)
        self.filename_hint_label = QLabel("使えない記号は _ に置き換わります")
        self.filename_hint_label.setWordWrap(True)
        self.filename_hint_label.setStyleSheet("color: #667085;")
        rl.addWidget(self.filename_hint_label)
        rl.addWidget(QLabel("保存先"))
        self.folder_label = ElidedPathLabel()
        rl.addWidget(self.folder_label)
        self.folder_button = QPushButton("保存先を選ぶ")
        self.folder_button.clicked.connect(self.choose_output_folder)
        rl.addWidget(self.folder_button)
        rl.addWidget(QLabel("保存予定"))
        self.planned_path_label = ElidedPathLabel()
        rl.addWidget(self.planned_path_label)
        self.save_hint_label = QLabel()
        self.save_hint_label.setWordWrap(True)
        self.save_hint_label.setStyleSheet("color: #667085;")
        rl.addWidget(self.save_hint_label)
        self.save_button = QPushButton("現在の設定で保存")
        self.save_button.setObjectName("editSave")
        self.save_button.clicked.connect(self.save_image)
        set_operation_role(self.save_button, "primary")
        rl.addWidget(self.save_button)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        rl.addWidget(self.result_label)
        self.saved_box = QWidget()
        set_operation_role(self.saved_box, "saveResult")
        saved_layout = QVBoxLayout(self.saved_box)
        saved_layout.setContentsMargins(7, 6, 7, 7)
        saved_layout.setSpacing(4)
        self.saved_filename = ElidedPathLabel()
        self.saved_filename.setObjectName("editSavedFilename")
        self.saved_filename.setAccessibleName("実保存ファイル名")
        self.saved_filename.setStyleSheet("color: #182230; font-weight: 700;")
        saved_layout.addWidget(self.saved_filename)
        self.saved_path = ElidedPathLabel()
        self.saved_path.setObjectName("editSavedPath")
        self.saved_path.setAccessibleName("実保存パス")
        self.saved_path.setStyleSheet("color: #667085; font-size: 11px;")
        saved_layout.addWidget(self.saved_path)
        saved_actions = QHBoxLayout()
        saved_actions.setContentsMargins(0, 0, 0, 0)
        saved_actions.setSpacing(4)
        self.open_image_button = QPushButton("画像を開く")
        self.open_image_button.setMinimumWidth(0)
        self.open_image_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.open_image_button.clicked.connect(self.open_saved_image)
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_folder_button.setMinimumWidth(0)
        self.open_folder_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.open_folder_button.clicked.connect(self.open_saved_folder)
        set_operation_role(self.open_image_button, "secondary")
        set_operation_role(self.open_folder_button, "secondary")
        saved_actions.addWidget(self.open_image_button, 1)
        saved_actions.addWidget(self.open_folder_button, 1)
        saved_layout.addLayout(saved_actions)
        self.edit_upscale_button = QPushButton("高画質化へ")
        self.edit_upscale_button.setToolTip("保存済みの加工結果を高画質化へ渡します")
        self.edit_upscale_button.clicked.connect(self.send_result_to_upscale)
        set_operation_role(self.edit_upscale_button, "secondary")
        saved_layout.addWidget(self.edit_upscale_button)
        self.saved_box.hide()
        rl.addWidget(self.saved_box)
        rl.addStretch()
        self.save_scroll.setWidget(right)
        splitter.addWidget(self.save_scroll)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH, 710, 220])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.sections = [
            self.filter_section,
            self.text_section,
            self.hand_section,
            self.transparency_section,
            self.canvas_section,
            self.material_section,
        ]
        for section in self.sections:
            section.expanded.connect(lambda expanded, active=section: self._section_expanded(active, expanded))
        self._connect_controls()
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._update_color_button(self.target_color_button, self._target_color)
        self._update_color_button(self.canvas_color_button, self._canvas_color)
        self._update_color_button(self.sticker_outline_color_button, self._sticker_outline_color)
        self._update_color_button(self.line_art_color_button, self._line_art_color)
        self._update_color_button(self.line_art_background_color_button, self._line_art_background_color)
        self._update_hand_color_button()

    @staticmethod
    def _configure_form(form: QFormLayout) -> None:
        form.setContentsMargins(3, 1, 3, 2)
        form.setHorizontalSpacing(4)
        form.setVerticalSpacing(2)
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

    def _select_default_font(self) -> None:
        self.font_combo.set_family(self.font_catalog.default_family())

    def _connect_controls(self) -> None:
        for combo in (
            self.filter_combo,
            self.position_combo,
            self.canvas_preset_combo,
            self.placement_combo,
            self.padding_combo,
            self.canvas_background_combo,
            self.line_art_background_combo,
            self.palette_blend_mode_combo,
        ):
            combo.currentIndexChanged.connect(self._control_changed)
        self.palette_count_combo.currentIndexChanged.connect(self._palette_count_changed)
        for spin in (
            self.font_size_spin,
            self.outline_width_spin,
            self.canvas_width_spin,
            self.canvas_height_spin,
            self.sticker_outline_width_spin,
        ):
            spin.valueChanged.connect(self._control_changed)
        self.text_enabled.toggled.connect(self._text_toggled)
        self.hand_mode_enabled.toggled.connect(self._hand_mode_toggled)
        self.mosaic_mode_enabled.toggled.connect(self._mosaic_mode_toggled)
        self.mosaic_visible_check.toggled.connect(self._mosaic_visibility_changed)
        self.mosaic_size_spin.valueChanged.connect(self._mosaic_size_changed)
        self.mosaic_block_spin.valueChanged.connect(self._mosaic_block_changed)
        self.hand_visible_check.toggled.connect(self._hand_visibility_changed)
        self.hand_size_spin.valueChanged.connect(self._hand_size_changed)
        self.hand_opacity_spin.valueChanged.connect(self._hand_opacity_changed)
        for check in (
            self.bold_check,
            self.outline_enabled,
            self.transparency_enabled,
            self.sticker_enabled,
            self.sticker_shadow_enabled,
            self.line_art_enabled,
            self.palette_enabled,
            self.palette_quantize_enabled,
        ):
            check.toggled.connect(self._control_changed)
        self.text_edit.textChanged.connect(self._text_changed)
        self.font_combo.family_changed.connect(self._control_changed)
        self.tolerance_slider.valueChanged.connect(self._tolerance_slider_changed)
        self.tolerance_spin.valueChanged.connect(self._tolerance_spin_changed)
        self.softness_slider.valueChanged.connect(self._softness_slider_changed)
        self.softness_spin.valueChanged.connect(self._softness_spin_changed)
        self.format_combo.currentIndexChanged.connect(self._update_save_options)

    def _section_expanded(self, active: CollapsibleSection, expanded: bool) -> None:
        # Sections are independent so users can keep all settings visible while scrolling.
        del active, expanded

    def finish_ime(self, clear_focus: bool = False) -> None:
        self.text_edit.finish_ime()
        if clear_focus:
            self.text_edit.clearFocus()

    def _set_text_enabled_from_content(self) -> bool:
        enabled = bool(self.text_edit.toPlainText().strip())
        previous = self.text_enabled.blockSignals(True)
        self.text_enabled.setChecked(enabled)
        self.text_enabled.blockSignals(previous)
        return enabled

    @Slot()
    def _text_changed(self) -> None:
        self._set_text_enabled_from_content()
        if self._applying:
            return
        self._text_history_dirty = True
        self._text_history_timer.start()
        self._show_original = False
        self.saved_box.hide()
        self.result_label.clear()
        self.schedule_preview()
        self._update_actions()

    @Slot()
    def _commit_text_history(self) -> None:
        if not self._text_history_dirty:
            return
        self._text_history_dirty = False
        self._control_changed()

    def _flush_text_history(self) -> None:
        if self._text_history_timer.isActive():
            self._text_history_timer.stop()
        self._commit_text_history()

    @Slot(bool)
    def _text_toggled(self, enabled: bool) -> None:
        if not enabled:
            self.finish_ime(clear_focus=True)
        self._update_visibility()
        self._update_actions()

    @Slot(bool)
    def _hand_mode_toggled(self, enabled: bool) -> None:
        if enabled:
            self.mosaic_mode_enabled.setChecked(False)
        else:
            self._cancel_hand_stroke()
        self._update_visibility()
        self._update_hand_drawing_state()

    @Slot(bool)
    def _mosaic_mode_toggled(self, enabled: bool) -> None:
        if enabled:
            self.hand_mode_enabled.setChecked(False)
        else:
            self._cancel_mosaic_stroke()
        self._update_visibility()
        self._update_hand_drawing_state()

    @Slot(bool)
    def _mosaic_visibility_changed(self, visible: bool) -> None:
        if self._applying:
            return
        self._cancel_mosaic_stroke()
        self._commit_mosaic(replace(self._mosaic_settings, visible=visible))
        self._update_visibility()
        self._update_actions()
        self.schedule_preview()

    def _mosaic_size_changed(self, value: int) -> None:
        self._mosaic_size = int(value)
        self.drop_zone.preview.set_brush_width(self._mosaic_size if self.mosaic_mode_enabled.isChecked() else self._hand_size)

    def _mosaic_block_changed(self, value: int) -> None:
        self._mosaic_block_size = int(value)

    def _select_mosaic_tool(self, tool: MosaicTool) -> None:
        self._cancel_mosaic_stroke()
        self._mosaic_tool = tool
        self._update_hand_drawing_state()

    @Slot(bool)
    def _hand_visibility_changed(self, visible: bool) -> None:
        if self._applying:
            return
        self._cancel_hand_stroke()
        self._commit_hand_draw(replace(self._hand_draw_settings, visible=visible))

    @Slot(int)
    def _hand_size_changed(self, size: int) -> None:
        self._hand_size = size
        self.drop_zone.preview.set_brush_width(size)

    @Slot(int)
    def _hand_opacity_changed(self, percent: int) -> None:
        self._hand_color.setAlpha(round(percent * 255 / 100))
        self._update_hand_color_button()

    def _select_hand_tool(self, tool: HandTool | None) -> None:
        self._cancel_hand_stroke()
        if tool is None:
            if self.eyedropper_button.isChecked():
                self.eyedropper_button.setChecked(False)
            if self.source_path and self.hand_mode_enabled.isChecked():
                self.preview_status.setText("画像上をクリックしてペン色を選んでください")
        else:
            self._hand_tool = tool
        self._update_hand_drawing_state()

    @Slot()
    def choose_hand_color(self) -> None:
        color = choose_color(self._hand_color, self, "手書きの色を選ぶ", show_alpha=True)
        if color.isValid():
            self._hand_color = color
            blocked = self.hand_opacity_spin.blockSignals(True)
            self.hand_opacity_spin.setValue(round(color.alpha() * 100 / 255))
            self.hand_opacity_spin.blockSignals(blocked)
            self._update_hand_color_button()

    def _final_canvas_size(self, settings: EditSettings | None = None) -> tuple[int, int]:
        if not self.source_path or self._source_size == (0, 0):
            return 0, 0
        return output_dimensions(self._source_size, (settings or self.settings()).canvas)

    @staticmethod
    def _without_hand_draw(settings: EditSettings) -> EditSettings:
        return replace(settings, hand_draw=HandDrawSettings())

    def _preview_geometry_key(self):
        if not self.source_path:
            return None
        return (
            self._preview_generation,
            self._without_hand_draw(self._effective_settings()),
            False,
        )

    def _update_hand_drawing_state(self) -> None:
        self.preview_history_bar.setVisible(self.hand_mode_enabled.isChecked() or self.mosaic_mode_enabled.isChecked())
        interaction_ready = (
            self.source_path is not None
            and ((self.hand_mode_enabled.isChecked() and self._hand_draw_settings.visible) or (self.mosaic_mode_enabled.isChecked() and self._mosaic_settings.visible))
            and not self._show_original
            and not self.eyedropper_button.isChecked()
            and self._thread is None
            and self._palette_thread is None
            and not self._processing_controls_locked
            and self.drop_zone.preview.geometry_generation() == self._preview_geometry_key()
        )
        hand_color_picking = self.hand_mode_enabled.isChecked() and self.hand_eyedropper_button.isChecked()
        self.drop_zone.preview.set_brush_width(self._mosaic_size if self.mosaic_mode_enabled.isChecked() else self._hand_size)
        self.drop_zone.preview.set_drawing_enabled(
            interaction_ready and not hand_color_picking
        )
        self.drop_zone.preview.set_hand_color_picking(
            interaction_ready and hand_color_picking
        )

    def _transient_hand_stroke(self) -> HandStroke | None:
        if not self._active_hand_points:
            return None
        return HandStroke(
            tool=self._active_hand_tool,
            points=tuple(self._active_hand_points),
            color=(
                self._active_hand_color.red(),
                self._active_hand_color.green(),
                self._active_hand_color.blue(),
                self._active_hand_color.alpha(),
            ),
            width=self._active_hand_width,
        )

    def _refresh_hand_overlay(self) -> None:
        geometry_current = self.drop_zone.preview.geometry_generation() == self._preview_geometry_key()
        self.drop_zone.preview.set_hand_overlay(
            self._hand_draw_settings,
            visible=not self._show_original and geometry_current,
        )
        self._update_hand_drawing_state()

    @Slot(object)
    def _begin_hand_stroke(self, point: HandPoint) -> None:
        if self.mosaic_mode_enabled.isChecked():
            return self._begin_mosaic_stroke(point)
        if self.drop_zone.preview.geometry_generation() != self._preview_geometry_key():
            return
        self._active_hand_tool = self._hand_tool
        self._active_hand_color = QColor(self._hand_color)
        self._active_hand_width = float(self._hand_size)
        self._active_hand_points = [point]
        self._active_hand_generation = self._preview_generation
        self.drop_zone.preview.begin_active_hand(
            point,
            self._active_hand_tool,
            self._active_hand_color,
            self._active_hand_width,
        )

    @Slot(object)
    def _append_hand_point(self, point: HandPoint) -> None:
        if self.mosaic_mode_enabled.isChecked():
            return self._append_mosaic_point(point)
        if not self._active_hand_points or self._active_hand_generation != self._preview_generation:
            return
        previous = self._active_hand_points[-1]
        distance = math.hypot(point.x - previous.x, point.y - previous.y)
        if distance <= 0:
            return
        if distance < max(0.5, self._active_hand_width * 0.08):
            return
        self._active_hand_points.append(point)
        self.drop_zone.preview.append_active_hand(point)

    @Slot()
    def _finish_hand_stroke(self) -> None:
        if self.mosaic_mode_enabled.isChecked():
            return self._finish_mosaic_stroke()
        stroke = self._transient_hand_stroke()
        generation = self._active_hand_generation
        self._active_hand_points = []
        self._active_hand_generation = None
        if stroke is None or generation != self._preview_generation:
            self.drop_zone.preview.cancel_active_hand()
            self._refresh_hand_overlay()
            return
        width, height = self._final_canvas_size()
        if width <= 0 or height <= 0:
            self.drop_zone.preview.cancel_active_hand()
            self._refresh_hand_overlay()
            return
        current = self._hand_draw_settings
        if current.base_width != width or current.base_height != height:
            current = scale_hand_draw(current, width, height)
        self._commit_hand_draw(replace(current, strokes=current.strokes + (stroke,)))
        self.drop_zone.preview.cancel_active_hand()

    @Slot()
    def _cancel_hand_stroke(self) -> None:
        if self.mosaic_mode_enabled.isChecked() and self._active_mosaic_points:
            return self._cancel_mosaic_stroke()
        had_points = bool(self._active_hand_points)
        self._active_hand_points = []
        self._active_hand_generation = None
        self.drop_zone.preview.cancel_active_hand()
        if had_points:
            self._refresh_hand_overlay()

    def _commit_hand_draw(self, updated: HandDrawSettings) -> None:
        if self._applying or updated == self._hand_draw_settings:
            self._refresh_hand_overlay()
            return
        self._flush_text_history()
        self._hand_draw_settings = updated
        current = self.settings()
        self._history = self._history[: self._history_index + 1]
        if not self._history or current != self._history[-1]:
            self._history.append(current)
            if len(self._history) > 60:
                self._history.pop(0)
            self._history_index = len(self._history) - 1
        self._show_original = False
        self.saved_box.hide()
        self.result_label.clear()
        self._refresh_hand_overlay()
        self._update_actions()

    @Slot()
    def clear_hand_draw(self) -> None:
        if not self._hand_draw_settings.strokes:
            return
        self._cancel_hand_stroke()
        self._commit_hand_draw(replace(self._hand_draw_settings, strokes=()))

    def _begin_mosaic_stroke(self, point: HandPoint) -> None:
        if self.drop_zone.preview.geometry_generation() != self._preview_geometry_key():
            return
        self._active_mosaic_points = [point]
        self._active_mosaic_generation = self._preview_generation
        active_color = QColor(49, 95, 189, 120) if self._mosaic_tool is MosaicTool.MOSAIC else QColor(190, 60, 60, 120)
        self.drop_zone.preview.begin_active_hand(point, HandTool.PEN, active_color, float(self._mosaic_size))

    def _append_mosaic_point(self, point: HandPoint) -> None:
        if not self._active_mosaic_points or self._active_mosaic_generation != self._preview_generation:
            return
        previous = self._active_mosaic_points[-1]
        if math.hypot(point.x - previous.x, point.y - previous.y) <= max(0.5, self._mosaic_size * 0.08):
            return
        self._active_mosaic_points.append(point)
        self.drop_zone.preview.append_active_hand(point)

    def _finish_mosaic_stroke(self) -> None:
        if not self._active_mosaic_points:
            self.drop_zone.preview.cancel_active_hand()
            return
        points = tuple(self._active_mosaic_points)
        generation = self._active_mosaic_generation
        self._active_mosaic_points = []
        self._active_mosaic_generation = None
        width, height = self._final_canvas_size()
        if generation != self._preview_generation or width <= 0 or height <= 0:
            self.drop_zone.preview.cancel_active_hand()
            return
        current = self._mosaic_settings
        if current.base_width != width or current.base_height != height:
            from .editing.mosaic import scale_mosaic
            current = scale_mosaic(current, width, height)
        stroke = MosaicStroke(self._mosaic_tool, points, float(self._mosaic_size), int(self._mosaic_block_size))
        self._commit_mosaic(replace(current, strokes=current.strokes + (stroke,)))
        self.drop_zone.preview.cancel_active_hand()
        self._show_original = False
        self.saved_box.hide()
        self.result_label.clear()
        self.schedule_preview()
        self._update_actions()

    def _commit_mosaic(self, updated: MosaicSettings) -> None:
        if self._applying or updated == self._mosaic_settings:
            return
        self._flush_text_history()
        self._mosaic_settings = updated
        current = self.settings()
        self._history = self._history[: self._history_index + 1]
        if not self._history or current != self._history[-1]:
            self._history.append(current)
            if len(self._history) > 60:
                self._history.pop(0)
            self._history_index = len(self._history) - 1
        self._show_original = False
        self.saved_box.hide()
        self.result_label.clear()

    def _cancel_mosaic_stroke(self) -> None:
        self._active_mosaic_points = []
        self._active_mosaic_generation = None
        self.drop_zone.preview.cancel_active_hand()

    @Slot()
    def clear_mosaic(self) -> None:
        if not self._mosaic_settings.strokes:
            return
        self._cancel_mosaic_stroke()
        self._commit_mosaic(replace(self._mosaic_settings, strokes=()))
        self._update_visibility()
        self.schedule_preview()
        self._update_actions()

    @Slot()
    def choose_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "加工する画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if name:
            self._request_or_load_image(Path(name))

    def set_workspace_managed(self, managed: bool = True) -> None:
        self._workspace_managed = managed

    @Slot(object)
    def _request_or_load_image(self, path: Path) -> None:
        if self._workspace_managed:
            self.source_change_requested.emit(Path(path))
        else:
            self.load_image(Path(path))

    @Slot(object)
    def set_current_source(self, source: SourceImage) -> None:
        if self.source_path is not None and self.source_path.resolve() == source.path:
            self.current_source_card.set_source(source)
            return
        if self.load_image(source.path):
            self.current_source_card.set_source(source)

    @Slot(object)
    def load_image(self, path: Path) -> bool:
        if self._thread is not None:
            return False
        if self._palette_thread is not None:
            self.cancel_palette_extraction()
        path = Path(path)
        try:
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise OSError("unsupported suffix")
            resolved_path = path.resolve()
            with Image.open(resolved_path) as opened:
                opened.load()
                normalized = ImageOps.exif_transpose(opened)
                width, height = normalized.size
                source_format = SOURCE_FORMATS.get((opened.format or "").upper())
                if source_format is None:
                    raise ValueError("unsupported decoded format")
                has_alpha = "A" in normalized.getbands() or (
                    normalized.mode == "P" and "transparency" in normalized.info
                )
                alpha_min, _alpha_max = normalized.convert("RGBA").getchannel("A").getextrema()
            source_size_bytes = resolved_path.stat().st_size
        except (OSError, UnidentifiedImageError, ValueError):
            if not path.is_file():
                QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            else:
                QMessageBox.warning(self, "画像を開けません", "PNG / JPEG / WebP画像を選んでください。")
            return False
        self._cancel_hand_stroke()
        self.finish_ime(clear_focus=True)
        self._clear_palette_preview_handoff(clear_refresh=True)
        self._preview_timer.stop()
        self._preview_generation += 1
        self._preview_pending_request = None
        self._preview_active_result = None
        self.preview_activity.invalidate()
        self._preview_activity_token = None
        self.source_path = resolved_path
        self._palette_generation += 1
        self._source_size = (width, height)
        self._source_format = source_format
        self._source_size_bytes = source_size_bytes
        self._source_has_alpha = has_alpha
        self._source_alpha_min = alpha_min
        if not self._output_folder_explicit:
            self.output_folder = self.source_path.parent
        self._set_filename_default(self.source_path)
        self.folder_label.set_path(self.output_folder)
        self.original_info.setText(
            f"元画像\n{width} × {height} / {source_format} / {self._human_bytes(self._source_size_bytes)}\n{self.source_path.name}"
        )
        self.drop_zone.set_empty(False)
        self._applying = True
        self.apply_settings(self._default_settings())
        self._applying = False
        self._history = [self.settings()]
        self._history_index = 0
        self._last_output = None
        self._text_history_dirty = False
        self._text_history_timer.stop()
        self.saved_box.hide()
        self.result_label.clear()
        self._show_original = False
        self._update_actions()
        self.update_preview()
        return True

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
                self.font_combo.family(),
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
            sticker=StickerSettings(self.sticker_enabled.isChecked(), (self._sticker_outline_color.red(), self._sticker_outline_color.green(), self._sticker_outline_color.blue(), self._sticker_outline_color.alpha()), self.sticker_outline_width_spin.value(), self.sticker_shadow_enabled.isChecked()),
            line_art=LineArtSettings(self.line_art_enabled.isChecked(), LineArtAmount.STANDARD, (self._line_art_color.red(), self._line_art_color.green(), self._line_art_color.blue(), self._line_art_color.alpha()), LineArtBackground(self.line_art_background_combo.currentData()), (self._line_art_background_color.red(), self._line_art_background_color.green(), self._line_art_background_color.blue(), self._line_art_background_color.alpha())),
            palette=PaletteSettings(
                enabled=self.palette_enabled.isChecked(),
                quantize_enabled=self.palette_quantize_enabled.isChecked(),
                color_count=int(self.palette_count_combo.currentData()),
                palette=self._palette_values,
                replacements=self._palette_replacements,
                mapping=self._palette_mapping,
                mapping_width=self._palette_mapping_size[0],
                mapping_height=self._palette_mapping_size[1],
                mapping_digest=self._palette_mapping_digest,
                blend_mode=RecolorBlendMode(self.palette_blend_mode_combo.currentData()),
            ),
            hand_draw=self._hand_draw_settings,
            mosaic=self._mosaic_settings,
        )

    def _default_settings(self) -> EditSettings:
        defaults = EditSettings()
        return replace(
            defaults,
            text=replace(
                defaults.text,
                font_family=self.font_catalog.default_family(),
            ),
        )

    @staticmethod
    def _canonical_line_expression_settings(settings: EditSettings) -> EditSettings:
        """Keep retired UI profiles out of active UI state and history."""
        if settings.line_art.amount is LineArtAmount.STANDARD:
            return settings
        return replace(
            settings,
            line_art=replace(settings.line_art, amount=LineArtAmount.STANDARD),
        )

    def _canonicalize_line_expression_history(self) -> None:
        if not self._history:
            return
        normalized: list[EditSettings] = []
        normalized_index = -1
        for index, settings in enumerate(self._history):
            canonical = self._canonical_line_expression_settings(settings)
            if not normalized or canonical != normalized[-1]:
                normalized.append(canonical)
            if index <= self._history_index:
                normalized_index = len(normalized) - 1
        self._history = normalized
        self._history_index = normalized_index

    def apply_settings(self, settings: EditSettings) -> None:
        settings = self._canonical_line_expression_settings(settings)
        previous_settings = self.settings()
        was_applying = self._applying
        self._applying = True
        self.filter_combo.setCurrentIndex(self.filter_combo.findData(settings.filter_preset.value))
        self.transparency_enabled.setChecked(settings.transparency.enabled)
        self._target_color = QColor(*settings.transparency.target_color)
        self.tolerance_slider.setValue(settings.transparency.tolerance)
        self.tolerance_spin.setValue(settings.transparency.tolerance)
        self.softness_slider.setValue(settings.transparency.edge_softness)
        self.softness_spin.setValue(settings.transparency.edge_softness)
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
        self.text_edit.setPlainText(settings.text.text)
        self._set_text_enabled_from_content()
        self.font_combo.set_family(settings.text.font_family)
        self.font_size_spin.setValue(settings.text.font_size)
        self.bold_check.setChecked(settings.text.bold)
        self._text_color = QColor(*settings.text.color)
        self.outline_enabled.setChecked(settings.text.outline_enabled)
        self._outline_color = QColor(*settings.text.outline_color)
        self.outline_width_spin.setValue(settings.text.outline_width)
        self.position_combo.setCurrentIndex(self.position_combo.findData(settings.text.position.value))
        self.sticker_enabled.setChecked(settings.sticker.enabled)
        self._sticker_outline_color = QColor(*settings.sticker.outline_color)
        self.sticker_outline_width_spin.setValue(settings.sticker.outline_width)
        self.sticker_shadow_enabled.setChecked(settings.sticker.shadow_enabled)
        self.line_art_enabled.setChecked(settings.line_art.enabled)
        self._line_art_color = QColor(*settings.line_art.line_color)
        self.line_art_background_combo.setCurrentIndex(self.line_art_background_combo.findData(settings.line_art.background.value))
        self._line_art_background_color = QColor(*settings.line_art.custom_background)
        self._hand_draw_settings = settings.hand_draw
        self._mosaic_settings = settings.mosaic
        self.mosaic_visible_check.setChecked(settings.mosaic.visible)
        self.hand_visible_check.setChecked(settings.hand_draw.visible)
        self.palette_enabled.setChecked(settings.palette.enabled)
        self.palette_quantize_enabled.setChecked(settings.palette.quantize_enabled)
        self.palette_count_combo.setCurrentIndex(self.palette_count_combo.findData(settings.palette.color_count))
        self.palette_blend_mode_combo.setCurrentIndex(self.palette_blend_mode_combo.findData(settings.palette.blend_mode.value))
        self._palette_values = settings.palette.palette
        self._palette_replacements = settings.palette.replacements or settings.palette.palette
        self._selected_palette_index = -1
        self._palette_mapping = settings.palette.mapping
        self._palette_mapping_size = (settings.palette.mapping_width, settings.palette.mapping_height)
        self._palette_mapping_digest = settings.palette.mapping_digest
        self._palette_source_signature = self._upstream_signature(settings)
        self._palette_needs_reextract = False
        self._palette_extracted_color_count = settings.palette.color_count if settings.palette.palette else None
        self._palette_status_message = ""
        self._rebuild_palette_chips()
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._update_color_button(self.target_color_button, self._target_color)
        self._update_color_button(self.canvas_color_button, self._canvas_color)
        self._update_color_button(self.sticker_outline_color_button, self._sticker_outline_color)
        self._update_color_button(self.line_art_color_button, self._line_art_color)
        self._update_color_button(self.line_art_background_color_button, self._line_art_background_color)
        self._update_hand_color_button()
        self._update_visibility()
        self._applying = was_applying
        if not self._applying:
            self._show_original = False
            if self._without_hand_draw(previous_settings) != self._without_hand_draw(settings):
                self.schedule_preview()
            else:
                self._refresh_hand_overlay()
            self._update_actions()

    def _palette_chip_style(self, color: tuple[int, int, int], *, clickable: bool) -> str:
        contrast = "#000000" if sum(color) >= 384 else "#FFFFFF"
        border = "2px solid #65768a" if clickable else "1px solid #98a2b3"
        hover = "QPushButton:hover { border: 2px solid #2457b2; }" if clickable else ""
        return (
            f"background: rgb{color}; color: {contrast}; border: {border}; border-radius: 6px; "
            "min-height: 28px; font-size: 11px; font-weight: 700; padding: 2px;"
            + hover
        )

    def _set_palette_feedback(self, text: str, tone: str = "success") -> None:
        color = {"success": "#137333", "warning": "#9a6700", "info": "#315fbd"}.get(tone, "#137333")
        self.palette_feedback_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.palette_feedback_label.setText(text)

    def _update_palette_quantize_text(self) -> None:
        count = int(self.palette_count_combo.currentData())
        self.palette_quantize_enabled.setText(f"{count}色に整理")

    def _update_palette_blend_description(self) -> None:
        mode = RecolorBlendMode(self.palette_blend_mode_combo.currentData())
        self.palette_blend_description_label.setText(RECOLOR_BLEND_DESCRIPTIONS[mode])

    def _clear_palette_state(self, message: str = "", *, needs_reextract: bool, preview_message: str | None = None) -> None:
        if needs_reextract:
            self._scrub_palette_history()
        self._palette_values = ()
        self._palette_replacements = ()
        self._palette_mapping = ()
        self._palette_mapping_size = (0, 0)
        self._palette_mapping_digest = ""
        self._selected_palette_index = -1
        self._palette_extracted_color_count = None
        previous_enabled = self.palette_enabled.blockSignals(True)
        self.palette_enabled.setChecked(False)
        self.palette_enabled.blockSignals(previous_enabled)
        previous_quantize = self.palette_quantize_enabled.blockSignals(True)
        self.palette_quantize_enabled.setChecked(False)
        self.palette_quantize_enabled.blockSignals(previous_quantize)
        self._palette_needs_reextract = needs_reextract
        self._palette_status_message = message
        self._set_palette_feedback("", "success")
        self._rebuild_palette_chips()
        if preview_message:
            self.preview_status.setText(preview_message)

    @staticmethod
    def _palette_scrubbed_settings(settings: EditSettings) -> EditSettings:
        palette = settings.palette
        return replace(
            settings,
            palette=PaletteSettings(
                enabled=False,
                quantize_enabled=False,
                color_count=palette.color_count,
                blend_mode=palette.blend_mode,
            ),
        )

    def _scrub_palette_history(self) -> None:
        """Remove stale palette mappings from the reachable history branch."""
        if self._history_index < 0:
            return
        scrubbed: list[EditSettings] = []
        for settings in self._history[: self._history_index + 1]:
            candidate = self._palette_scrubbed_settings(settings)
            if not scrubbed or candidate != scrubbed[-1]:
                scrubbed.append(candidate)
        self._history = scrubbed
        self._history_index = len(scrubbed) - 1

    def _update_palette_controls(self) -> None:
        self._update_palette_quantize_text()
        self._update_palette_blend_description()
        has_palette = bool(self._palette_values)
        self.palette_intro_label.setVisible(not has_palette)
        self.palette_results_widget.setVisible(has_palette)
        self.palette_current_label.setVisible(False)
        self.palette_instruction_label.setVisible(False)
        self.palette_columns_widget.setVisible(False)
        self.palette_reset_button.setVisible(has_palette)
        self.palette_reset_button.setEnabled(has_palette and self._palette_replacements != self._palette_values)
        self.palette_other_uses_label.setVisible(False)
        self.palette_send_button.setVisible(has_palette)
        self.palette_send_button.setEnabled(has_palette)
        self.palette_open_button.setVisible(has_palette)
        self.palette_open_button.setEnabled(has_palette)
        self.palette_quantize_enabled.setEnabled(has_palette)
        self.palette_blend_description_label.setVisible(False)
        status_text = self._palette_status_message if not has_palette else ""
        self.palette_quantize_guide_label.setText(status_text)
        self.palette_quantize_guide_label.setVisible(bool(status_text))

    def _rebuild_palette_chips(self) -> None:
        while self.palette_chips_layout.count():
            item = self.palette_chips_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._palette_reset_buttons = []
        if not self._palette_values:
            self._update_palette_controls()
            return
        for index, source_color in enumerate(self._palette_values):
            replacement = self._palette_replacements[index] if index < len(self._palette_replacements) else source_color
            original_chip = QLabel()
            original_chip.setText(f"#{source_color[0]:02X}{source_color[1]:02X}{source_color[2]:02X}")
            original_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            original_chip.setMinimumWidth(0)
            original_chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            original_chip.setMinimumHeight(28)
            original_hex = f"#{source_color[0]:02X}{source_color[1]:02X}{source_color[2]:02X}"
            original_chip.setAccessibleName(f"代表色 {index + 1} の元の色 {original_hex}")
            original_chip.setStyleSheet(self._palette_chip_style(source_color, clickable=False))
            arrow = QLabel("→")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setMaximumWidth(12)
            arrow.setStyleSheet("color: #667085; font-weight: 700;")
            replacement_button = QPushButton(f"#{replacement[0]:02X}{replacement[1]:02X}{replacement[2]:02X}")
            replacement_hex = f"#{replacement[0]:02X}{replacement[1]:02X}{replacement[2]:02X}"
            replacement_button.setToolTip(f"代表色 {index + 1} の置き換え先を選ぶ")
            replacement_button.setMinimumWidth(0)
            replacement_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            replacement_button.setAccessibleName(f"代表色 {index + 1} の変更後の色 {replacement_hex}")
            replacement_button.setMinimumHeight(28)
            replacement_button.setStyleSheet(self._palette_chip_style(replacement, clickable=True))
            replacement_button.clicked.connect(lambda checked=False, i=index: self._replace_palette_color(i))
            reset_button = QToolButton()
            reset_button.setText("↶")
            reset_button.setToolTip("この色だけ元に戻す")
            reset_button.setAccessibleName(f"代表色 {index + 1} を元の色 {original_hex} に戻す")
            reset_button.setEnabled(replacement != source_color)
            reset_button.setMinimumSize(26, 28)
            reset_button.setMaximumWidth(28)
            reset_button.clicked.connect(lambda checked=False, i=index: self._reset_palette_color(i))
            self._palette_reset_buttons.append(reset_button)
            row = index
            self.palette_chips_layout.addWidget(original_chip, row, 0)
            self.palette_chips_layout.addWidget(arrow, row, 1)
            self.palette_chips_layout.addWidget(replacement_button, row, 2)
            self.palette_chips_layout.addWidget(reset_button, row, 3)
        self.palette_chips_layout.setColumnStretch(0, 1)
        self.palette_chips_layout.setColumnStretch(2, 1)
        self._update_palette_controls()

    @staticmethod
    def _palette_source_identity(path: Path):
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)

    @Slot()
    def extract_palette(self) -> None:
        if not self.source_path or self._thread is not None:
            return
        if self._preview_timer.isActive():
            self._palette_preview_refresh_pending = True
            self._palette_preview_handoff_generation = self._preview_generation
        self._preview_timer.stop()
        self._palette_terminal_preview_status = None
        self._palette_terminal_activity_pending = False
        self._palette_preview_handoff_generation = (
            self._preview_generation if self._palette_preview_refresh_pending else None
        )
        if self._palette_thread is None:
            # Palette feedback owns the shared preview status while extraction runs;
            # any older render completion must not overwrite its accepted outcome.
            self._preview_generation += 1
            self._preview_pending_request = None
            self.preview_activity.invalidate()
            self._preview_activity_token = None
        self._palette_generation += 1
        self._palette_request_id += 1
        request_id = self._palette_request_id
        source = self.source_path
        settings = self.settings()
        try:
            identity = self._palette_source_identity(source)
        except OSError:
            had_active_thread = self._palette_thread is not None
            self._palette_pending_request = None
            self._palette_active_request = None
            if self._palette_activity_token is not None:
                self.preview_activity.fail(self._palette_activity_token)
                self._palette_activity_token = None
            if self._palette_thread is not None:
                self._palette_thread.requestInterruption()
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(MISSING_SOURCE_MESSAGE)
            if self._palette_preview_refresh_pending:
                self._palette_terminal_preview_status = (
                    MISSING_SOURCE_MESSAGE,
                    "color: #c62828; font-weight: 700;",
                )
                self._palette_preview_handoff_generation = self._preview_generation
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            if not had_active_thread and self._palette_preview_refresh_pending:
                self._palette_preview_refresh_pending = False
                self._preview_refresh_without_activity = True
                self.schedule_preview()
            return
        signature = (settings.filter_preset.value, settings.transparency, settings.palette.color_count)
        generation = self._palette_generation
        request = (source, settings, generation, request_id, identity, signature)
        if self._palette_thread is not None:
            self._palette_pending_request = request
            return
        self._start_palette_request(request)

    def _start_palette_request(self, request, *, continuing: bool = False) -> None:
        source, settings, generation, request_id, identity, signature = request
        self._palette_active_request = (generation, request_id, identity, signature)

        self.preview_status.setText("処理中…")
        self._palette_activity_token = self.preview_activity.begin("代表色を抽出しています")
        if not continuing:
            self._set_processing(True, palette_requeue=True)
        thread = PaletteExtractionThread(source, settings, generation, request_id, identity, signature)
        self._palette_thread = thread
        thread.succeeded.connect(self._on_palette_extracted)
        thread.failed.connect(self._on_palette_extraction_failed)
        thread.finished.connect(lambda t=thread: self._finalize_palette_thread(t))
        thread.start()

    @Slot(object)
    def _on_palette_extracted(self, payload) -> None:
        generation, request_id, identity, signature, mapping = payload
        if (
            self._palette_active_request != (generation, request_id, identity, signature)
            or self._palette_pending_request is not None
        ):
            return
        try:
            current_identity = self._palette_source_identity(self.source_path) if self.source_path else None
        except OSError:
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(MISSING_SOURCE_MESSAGE)
            if self._palette_activity_token is not None:
                self.preview_activity.fail(self._palette_activity_token)
                self._palette_activity_token = None
            return
        if current_identity != identity:
            message = MISSING_SOURCE_MESSAGE if not self.source_path or not self.source_path.is_file() else "元画像が変更されたため、代表色を適用しませんでした。"
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(message)
            if self._palette_activity_token is not None:
                self.preview_activity.fail(self._palette_activity_token)
                self._palette_activity_token = None
            return
        current = self.settings()
        current_signature = (current.filter_preset.value, current.transparency, current.palette.color_count)
        if current_signature != signature:
            if self._palette_activity_token is not None:
                self.preview_activity.cancel(self._palette_activity_token)
                self._palette_activity_token = None
                self._palette_terminal_activity_pending = self._palette_preview_refresh_pending
                self._palette_preview_handoff_generation = self._preview_generation
            return
        self._palette_values = mapping.palette
        self._palette_replacements = mapping.palette
        self._palette_mapping = mapping.indices
        self._palette_mapping_size = (mapping.width, mapping.height)
        self._palette_mapping_digest = mapping.digest
        self._palette_needs_reextract = False
        self._palette_extracted_color_count = int(signature[2])
        self._palette_status_message = ""
        self.palette_enabled.setChecked(True)
        self._set_palette_feedback("", "success")
        self._rebuild_palette_chips()
        if self._palette_activity_token is not None:
            self.preview_activity.complete(self._palette_activity_token)
            self._palette_activity_token = None
        self._palette_preview_refresh_pending = True
        self._palette_preview_handoff_generation = self._preview_generation
        self._control_changed()

    @Slot(str, object)
    def _on_palette_extraction_failed(self, message: str, token) -> None:
        if self._palette_active_request == token and self._palette_pending_request is None:
            error_style = "color: #c62828; font-weight: 700;"
            self.preview_status.setStyleSheet(error_style)
            self.preview_status.setText(message)
            if self._palette_preview_refresh_pending:
                self._palette_terminal_preview_status = (message, error_style)
                self._palette_preview_handoff_generation = self._preview_generation
            if self._palette_activity_token is not None:
                self.preview_activity.fail(self._palette_activity_token)
                self._palette_activity_token = None
                self._palette_terminal_activity_pending = self._palette_preview_refresh_pending
                self._palette_preview_handoff_generation = self._preview_generation

    def _finalize_palette_thread(self, thread: PaletteExtractionThread) -> None:
        if thread is self._palette_thread:
            if self._palette_activity_token is not None:
                self.preview_activity.cancel(self._palette_activity_token)
                self._palette_activity_token = None
                self._palette_terminal_activity_pending = self._palette_preview_refresh_pending
                self._palette_preview_handoff_generation = self._preview_generation
            self._palette_thread = None
            self._palette_active_request = None
            pending = self._palette_pending_request
            self._palette_pending_request = None
            if pending is None:
                self._set_processing(False)
                if self._palette_preview_refresh_pending:
                    self._palette_preview_refresh_pending = False
                    self._preview_refresh_without_activity = (
                        self._palette_terminal_preview_status is not None
                        or self._palette_terminal_activity_pending
                    )
                    self._palette_terminal_activity_pending = False
                    self.schedule_preview()
            else:
                self._start_palette_request(pending, continuing=True)
        thread.deleteLater()

    def cancel_palette_extraction(self) -> None:
        self._palette_generation += 1
        self._palette_request_id += 1
        self._palette_active_request = None
        self._palette_pending_request = None
        if self._palette_activity_token is not None:
            self.preview_activity.cancel(self._palette_activity_token)
            self._palette_activity_token = None
            self._palette_terminal_activity_pending = self._palette_preview_refresh_pending
            self._palette_preview_handoff_generation = self._preview_generation
        if self._palette_thread is not None:
            self._palette_thread.requestInterruption()

    @Slot()
    def reset_palette(self) -> None:
        self._commit_palette_replacements(
            self._palette_values,
            selected_index=-1,
            feedback="元の配色に戻しました。",
        )

    def _reset_palette_color(self, index: int) -> None:
        if not 0 <= index < len(self._palette_values):
            return
        values = list(self._palette_replacements)
        values[index] = self._palette_values[index]
        self._commit_palette_replacements(
            tuple(values),
            selected_index=index,
            feedback=f"代表色 {index + 1} を元の色に戻しました。",
        )

    def _commit_palette_replacements(
        self,
        replacements: tuple[tuple[int, int, int], ...],
        *,
        selected_index: int,
        feedback: str,
    ) -> bool:
        replacements = tuple(replacements)
        if len(replacements) != len(self._palette_values):
            raise ValueError("Palette replacements must match palette length")
        if replacements == self._palette_replacements:
            return False
        self._palette_replacements = replacements
        self._selected_palette_index = selected_index
        self._palette_needs_reextract = False
        self._palette_status_message = ""
        self._set_palette_feedback(feedback, "info")
        self._rebuild_palette_chips()
        self._control_changed()
        return True

    def _replace_palette_color(self, index: int) -> None:
        if not 0 <= index < len(self._palette_replacements):
            return
        current = QColor(*self._palette_replacements[index])
        color = choose_color(current, self, "代表色を変更", show_alpha=False)
        if not color.isValid():
            return
        values = list(self._palette_replacements)
        values[index] = (color.red(), color.green(), color.blue())
        self._commit_palette_replacements(
            tuple(values),
            selected_index=index,
            feedback=f"代表色 {index + 1} の置き換え先を更新しました。",
        )

    def _choose_material_color(self, title: str, attribute: str, button: QPushButton) -> None:
        current = getattr(self, attribute)
        color = choose_color(current, self, title, show_alpha=True)
        if color.isValid():
            setattr(self, attribute, color)
            self._update_color_button(button, color)
            self._control_changed()

    @Slot()
    def choose_sticker_color(self) -> None:
        self._choose_material_color("フチ色を選ぶ", "_sticker_outline_color", self.sticker_outline_color_button)

    @Slot()
    def choose_line_art_color(self) -> None:
        self._choose_material_color("線色を選ぶ", "_line_art_color", self.line_art_color_button)

    @Slot()
    def choose_line_art_background_color(self) -> None:
        self._choose_material_color("輪郭・線表現の背景色を選ぶ", "_line_art_background_color", self.line_art_background_color_button)

    def _upstream_signature(self, settings: EditSettings):
        return settings.filter_preset, settings.transparency

    def _invalidate_palette_for_upstream_change(self, settings: EditSettings) -> None:
        signature = self._upstream_signature(settings)
        if self._palette_source_signature is None:
            self._palette_source_signature = signature
            return
        if signature == self._palette_source_signature or self._invalidating_palette:
            return
        self._invalidating_palette = True
        try:
            self._clear_palette_state(
                "画像が変更されました。もう一度色を取り出してください。",
                needs_reextract=True,
                preview_message="画像が変更されました。もう一度色を取り出してください。",
            )
        finally:
            self._palette_source_signature = signature
            self._invalidating_palette = False

    @Slot()
    def _palette_count_changed(self, *_args) -> None:
        self._update_palette_quantize_text()
        if self._applying:
            return
        requested = int(self.palette_count_combo.currentData())
        if self._palette_values and self._palette_extracted_color_count != requested:
            self._clear_palette_state(
                f"色数が変更されました。{requested}色で取り直してください。",
                needs_reextract=True,
                preview_message=f"色数が変更されました。{requested}色で取り直してください。",
            )
        self._control_changed()

    @Slot()
    def _control_changed(self, *_args) -> None:
        if self._applying:
            return
        self._canonicalize_line_expression_history()
        self._set_text_enabled_from_content()
        self._invalidate_palette_for_upstream_change(self.settings())
        width, height = self._final_canvas_size()
        if (
            self._hand_draw_settings.strokes
            and width > 0
            and height > 0
            and (self._hand_draw_settings.base_width, self._hand_draw_settings.base_height) != (width, height)
        ):
            self._hand_draw_settings = scale_hand_draw(self._hand_draw_settings, width, height)
        if (self._mosaic_settings.strokes and width > 0 and height > 0 and (self._mosaic_settings.base_width, self._mosaic_settings.base_height) != (width, height)):
            from .editing.mosaic import scale_mosaic
            self._mosaic_settings = scale_mosaic(self._mosaic_settings, width, height)
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

    @Slot(int)
    def _tolerance_slider_changed(self, value: int) -> None:
        if self.tolerance_spin.value() != value:
            previous = self.tolerance_spin.blockSignals(True)
            self.tolerance_spin.setValue(value)
            self.tolerance_spin.blockSignals(previous)
        self._control_changed()

    @Slot(int)
    def _tolerance_spin_changed(self, value: int) -> None:
        if self.tolerance_slider.value() != value:
            previous = self.tolerance_slider.blockSignals(True)
            self.tolerance_slider.setValue(value)
            self.tolerance_slider.blockSignals(previous)
        self._control_changed()

    @Slot(int)
    def _softness_slider_changed(self, value: int) -> None:
        if self.softness_spin.value() != value:
            previous = self.softness_spin.blockSignals(True)
            self.softness_spin.setValue(value)
            self.softness_spin.blockSignals(previous)
        self._control_changed()

    @Slot(int)
    def _softness_spin_changed(self, value: int) -> None:
        if self.softness_slider.value() != value:
            previous = self.softness_slider.blockSignals(True)
            self.softness_slider.setValue(value)
            self.softness_slider.blockSignals(previous)
        self._control_changed()

    def _sticker_ready_for_preview(self) -> bool:
        return self._source_alpha_min < 255 or self.transparency_enabled.isChecked()

    def _effective_settings(self) -> EditSettings:
        settings = self.settings()
        if settings.sticker.enabled and not self._sticker_ready_for_preview():
            settings = replace(settings, sticker=replace(settings.sticker, enabled=False))
        return settings

    def _base_preview_settings(self) -> EditSettings:
        return self._without_hand_draw(self._effective_settings())

    def _update_sticker_prerequisite_ui(self) -> None:
        if not self.source_path:
            self.sticker_prereq_status.setText("画像を読み込むと設定できます")
            self.sticker_prereq_status.setStyleSheet("color: #667085;")
            self.sticker_prereq_status.setVisible(self.sticker_enabled.isChecked())
            self.sticker_prereq_button.setVisible(False)
            return
        if self._sticker_ready_for_preview():
            self.sticker_prereq_status.clear()
            self.sticker_prereq_status.setVisible(False)
            self.sticker_prereq_button.setVisible(False)
            return
        self.sticker_prereq_status.setText("背景透過が必要です")
        self.sticker_prereq_status.setStyleSheet("color: #9a6700;")
        self.sticker_prereq_status.setVisible(self.sticker_enabled.isChecked())
        self.sticker_prereq_button.setVisible(self.sticker_enabled.isChecked())

    def _update_visibility(self) -> None:
        self.text_details.setVisible(True)
        self.outline_color_button.setEnabled(self.outline_enabled.isChecked())
        self.outline_width_spin.setEnabled(self.outline_enabled.isChecked())
        hand_mode = self.hand_mode_enabled.isChecked()
        self.hand_details.setVisible(hand_mode)
        self.hand_pen_button.setEnabled(hand_mode and self._hand_draw_settings.visible)
        self.hand_eraser_button.setEnabled(hand_mode and self._hand_draw_settings.visible)
        self.hand_eyedropper_button.setEnabled(hand_mode and self._hand_draw_settings.visible)
        self.hand_color_button.setEnabled(hand_mode and self._hand_draw_settings.visible)
        self.hand_size_spin.setEnabled(hand_mode and self._hand_draw_settings.visible)
        self.hand_opacity_spin.setEnabled(hand_mode and self._hand_draw_settings.visible)
        has_hand_layer = self.source_path is not None and bool(self._hand_draw_settings.strokes)
        self.hand_visible_check.setEnabled(has_hand_layer)
        self.hand_clear_button.setEnabled(has_hand_layer)
        mosaic_mode = self.mosaic_mode_enabled.isChecked()
        self.mosaic_details.setVisible(mosaic_mode)
        has_mosaic_layer = self.source_path is not None and bool(self._mosaic_settings.strokes)
        self.mosaic_visible_check.setEnabled(has_mosaic_layer)
        self.mosaic_clear_button.setEnabled(has_mosaic_layer)
        self.mosaic_pen_button.setEnabled(mosaic_mode and self._mosaic_settings.visible)
        self.mosaic_eraser_button.setEnabled(mosaic_mode and self._mosaic_settings.visible)
        self.mosaic_size_spin.setEnabled(mosaic_mode and self._mosaic_settings.visible)
        self.mosaic_block_spin.setEnabled(mosaic_mode and self._mosaic_settings.visible)
        self.transparency_details.setVisible(self.transparency_enabled.isChecked())
        self.custom_canvas.setVisible(self.canvas_preset_combo.currentData() == "custom")
        self.canvas_color_button.setVisible(
            self.canvas_background_combo.currentData() == CanvasBackground.CUSTOM.value
        )
        sticker_ready = self._sticker_ready_for_preview()
        self.sticker_details.setVisible(self.sticker_enabled.isChecked())
        self.sticker_outline_width_spin.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.sticker_outline_color_button.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.sticker_shadow_enabled.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.line_art_details.setVisible(self.line_art_enabled.isChecked())
        self.line_art_color_button.setEnabled(self.line_art_enabled.isChecked())
        self.line_art_background_combo.setEnabled(self.line_art_enabled.isChecked())
        self.line_art_background_color_button.setVisible(
            self.line_art_enabled.isChecked()
            and self.line_art_background_combo.currentData() == LineArtBackground.CUSTOM.value
        )
        self.line_art_background_color_button.setEnabled(
            self.line_art_enabled.isChecked()
            and self.line_art_background_combo.currentData() == LineArtBackground.CUSTOM.value
        )
        self._update_sticker_prerequisite_ui()
        self._update_palette_controls()
        self._update_save_options()
        if self._processing_controls_locked:
            self._apply_processing_control_state(
                True,
                palette_requeue=self._processing_palette_requeue,
            )
        self._refresh_hand_overlay()

    def schedule_preview(self) -> None:
        if self.source_path and self._palette_thread is None:
            self._preview_timer.start()
        else:
            self._preview_timer.stop()
            if self.source_path and self._palette_thread is not None:
                self._palette_preview_refresh_pending = True
                self._palette_preview_handoff_generation = self._preview_generation

    def _clear_palette_preview_handoff(
        self,
        *,
        clear_refresh: bool = False,
        generation: int | None = None,
    ) -> None:
        if (
            generation is not None
            and self._palette_preview_handoff_generation is not None
            and generation != self._palette_preview_handoff_generation
        ):
            return
        self._palette_terminal_preview_status = None
        self._palette_terminal_activity_pending = False
        self._preview_refresh_without_activity = False
        if clear_refresh:
            self._palette_preview_refresh_pending = False
        self._palette_preview_handoff_generation = None

    @Slot()
    def update_preview(self) -> None:
        if not self.source_path:
            self.drop_zone.preview.clear_image()
            self._clear_palette_preview_handoff(clear_refresh=True)
            return
        try:
            identity = self._palette_source_identity(self.source_path)
        except OSError:
            self.drop_zone.preview.clear_image()
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(MISSING_SOURCE_MESSAGE)
            self._clear_palette_preview_handoff(clear_refresh=True)
            return
        self._preview_request_id += 1
        request = (
            self._preview_generation,
            self._preview_request_id,
            self.source_path,
            identity,
            self._base_preview_settings(),
            self._show_original,
        )
        suppress_activity = self._preview_refresh_without_activity
        self._preview_refresh_without_activity = False
        if self._preview_activity_token is None and not suppress_activity:
            self._preview_activity_token = self.preview_activity.begin("プレビューを更新しています")
        if self._preview_thread is not None:
            self._preview_pending_request = request
            return
        self._start_preview_request(request)

    def _start_preview_request(self, request) -> None:
        self._preview_active_request = request
        self._preview_active_result = None
        thread = QThread(self)
        worker = EditPreviewWorker(request)
        self._preview_thread = thread
        self._preview_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda payload: self._capture_preview_result("success", payload))
        worker.failed.connect(lambda payload: self._capture_preview_result("failed", payload))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda t=thread, w=worker: self._finalize_preview_request(t, w))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @Slot()
    def _capture_preview_result(self, kind: str, payload) -> None:
        self._preview_active_result = (kind, payload)

    def _preview_request_is_current(self, request) -> bool:
        generation, _request_id, source, identity, settings, show_original = request
        if generation != self._preview_generation or self.source_path != source:
            return False
        if settings != self._base_preview_settings() or show_original != self._show_original:
            return False
        try:
            return identity == self._palette_source_identity(source)
        except OSError:
            return False

    def _preview_request_owns_ui(self, request) -> bool:
        generation, request_id, *_rest = request
        return generation == self._preview_generation and request_id == self._preview_request_id

    def _finalize_preview_request(self, thread: QThread, worker: EditPreviewWorker) -> None:
        if thread is not self._preview_thread:
            return
        request = self._preview_active_request
        result = self._preview_active_result
        self._preview_thread = None
        self._preview_worker = None
        self._preview_active_request = None
        self._preview_active_result = None
        if self._preview_pending_request is not None:
            pending = self._preview_pending_request
            self._preview_pending_request = None
            self._start_preview_request(pending)
            return
        if request is None:
            self.preview_activity.invalidate()
            self._preview_activity_token = None
            self._clear_palette_preview_handoff()
            return
        if not self._preview_request_owns_ui(request):
            self._clear_palette_preview_handoff(generation=request[0])
            return
        if result is None:
            result = ("failed", (*request, "プレビュー処理を完了できませんでした。"))
        kind, payload = result
        if kind == "success" and self._preview_request_is_current(request):
            _generation, _request_id, _source, _identity, _settings, show_original, image, width, height = payload
            full_size = self._source_size if show_original else output_dimensions(self._source_size, _settings.canvas)
            geometry_key = (_generation, _settings, show_original)
            self.drop_zone.preview.set_image(image, full_size, geometry_key)
            self._refresh_hand_overlay()
            self.preview_status.setStyleSheet("color: #667085;")
            status = ("元画像" if show_original else "加工後") + f" · プレビュー {width} × {height}"
            if self._palette_needs_reextract:
                status += " · 代表色を再抽出してください"
            self.preview_status.setText(status)
            if self._preview_activity_token is not None:
                self.preview_activity.complete(self._preview_activity_token)
        else:
            if kind == "failed":
                message = payload[-1]
            else:
                source = request[2]
                message = MISSING_SOURCE_MESSAGE if not source.is_file() else "元画像または設定が変更されたため、プレビューを更新しませんでした。"
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(message)
            if self._preview_activity_token is not None:
                self.preview_activity.fail(self._preview_activity_token)
            self._update_hand_drawing_state()
        if self._palette_terminal_preview_status is not None:
            message, style = self._palette_terminal_preview_status
            self.preview_status.setStyleSheet(style)
            self.preview_status.setText(message)
        self._clear_palette_preview_handoff(generation=request[0])
        self._preview_activity_token = None

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
        if self._active_hand_points or self._active_mosaic_points:
            return
        self._flush_text_history()
        self._canonicalize_line_expression_history()
        if self._history_index > 0:
            self._history_index -= 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def redo(self) -> None:
        if self._active_hand_points or self._active_mosaic_points:
            return
        self._flush_text_history()
        self._canonicalize_line_expression_history()
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def reset_edits(self) -> None:
        self._cancel_hand_stroke()
        self._cancel_mosaic_stroke()
        self.cancel_palette_extraction()
        self._flush_text_history()
        self.finish_ime(clear_focus=True)
        if not self.source_path:
            return
        default = self._default_settings()
        if default != self.settings():
            self._history = self._history[: self._history_index + 1]
            self._history.append(default)
            self._history_index = len(self._history) - 1
            self.apply_settings(default)

    @Slot()
    def clear_all(self) -> None:
        if self.source_path is None or self._thread is not None or self._palette_thread is not None:
            return
        self._cancel_hand_stroke()
        self._cancel_mosaic_stroke()
        self.finish_ime(clear_focus=True)
        self.cancel_palette_extraction()
        self._clear_palette_preview_handoff(clear_refresh=True)
        self._preview_timer.stop()
        self._preview_generation += 1
        self._preview_request_id += 1
        self._preview_pending_request = None
        self._preview_active_result = None
        self.preview_activity.invalidate()
        self._preview_activity_token = None

        self._applying = True
        self.apply_settings(self._default_settings())
        self.canvas_width_spin.setValue(320)
        self.canvas_height_spin.setValue(320)
        self.hand_mode_enabled.setChecked(False)
        self.mosaic_mode_enabled.setChecked(False)
        self._mosaic_tool = MosaicTool.MOSAIC
        self.mosaic_pen_button.setChecked(True)
        self.mosaic_size_spin.setValue(36)
        self.mosaic_block_spin.setValue(12)
        self._hand_tool = HandTool.PEN
        self.hand_pen_button.setChecked(True)
        self._hand_color = QColor("#000000")
        self.hand_size_spin.setValue(8)
        self.hand_opacity_spin.setValue(100)
        self.eyedropper_button.setChecked(False)
        self.format_combo.setCurrentIndex(0)
        self.quality_spin.setValue(95)
        self.jpeg_background_combo.setCurrentIndex(0)
        self._applying = False

        self._text_history_timer.stop()
        self._text_history_dirty = False
        self._history = [self.settings()]
        self._history_index = 0
        self._last_output = None
        self.saved_box.hide()
        self.saved_filename.set_value("")
        self.saved_path.set_value("")
        self.result_label.clear()
        self._set_filename_default(self.source_path)
        self._show_original = False
        self._update_hand_color_button()
        self._update_visibility()
        self._update_save_options()
        self._update_actions()
        self.update_preview()

    @Slot()
    def clear_image(self) -> None:
        """Return this page to its empty state without altering shared Source."""
        if self._thread is not None or self._palette_thread is not None:
            return
        self._cancel_hand_stroke()
        self._cancel_mosaic_stroke()
        self.finish_ime(clear_focus=True)
        self.cancel_palette_extraction()
        self._preview_timer.stop()
        self._preview_generation += 1
        self._preview_request_id += 1
        self._preview_pending_request = None
        self._preview_active_result = None
        self.preview_activity.invalidate()
        self._preview_activity_token = None
        self.source_path = None
        self._source_size = (0, 0)
        self._source_format = ""
        self._source_size_bytes = 0
        self._source_has_alpha = False
        self._source_alpha_min = 255
        self._last_output = None
        self._history = [self.settings()]
        self._history_index = 0
        self.drop_zone.preview.clear_image()
        self.drop_zone.set_empty(True)
        self.current_source_card.set_source(None)
        self.original_info.setText("元画像\n画像を読み込んでください")
        self.output_info.setText("加工後（見込み）\n—")
        self.saved_box.hide()
        self.saved_filename.set_value("")
        self.saved_path.set_value("")
        self.result_label.clear()
        self._update_actions()

    @Slot()
    def show_original(self) -> None:
        if self.source_path:
            self._cancel_hand_stroke()
            self._show_original = True
            self._refresh_hand_overlay()
            self.update_preview()

    @Slot()
    def show_edited(self) -> None:
        if self.source_path:
            self._show_original = False
            self._refresh_hand_overlay()
            self.update_preview()

    def apply_text_preset(self, white_text: bool) -> None:
        self._text_color = QColor("#FFFFFF" if white_text else "#000000")
        self._outline_color = QColor("#000000" if white_text else "#FFFFFF")
        self.outline_enabled.setChecked(True)
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._control_changed()

    @Slot()
    def clear_text(self) -> None:
        self.finish_ime(clear_focus=True)
        if not self.text_edit.toPlainText():
            self._set_text_enabled_from_content()
            self._update_visibility()
            return
        self.text_edit.setPlainText("")
        self._flush_text_history()

    @Slot()
    def choose_text_color(self) -> None:
        self._choose_color("文字色を選ぶ", "_text_color", self.text_color_button, show_alpha=True)

    @Slot()
    def choose_outline_color(self) -> None:
        self._choose_color("縁取りの色を選ぶ", "_outline_color", self.outline_color_button, show_alpha=True)

    @Slot()
    def choose_target_color(self) -> None:
        self._choose_color("透明にする背景色を選ぶ", "_target_color", self.target_color_button, show_alpha=False)

    @Slot()
    def choose_canvas_color(self) -> None:
        self._choose_color("キャンバスの背景色を選ぶ", "_canvas_color", self.canvas_color_button, show_alpha=True)

    def _choose_color(
        self,
        title: str,
        attribute: str,
        button: QPushButton,
        *,
        show_alpha: bool,
    ) -> None:
        current = getattr(self, attribute)
        color = choose_color(current, self, title, show_alpha=show_alpha)
        if color.isValid():
            setattr(self, attribute, color)
            self._update_color_button(button, color)
            self._control_changed()

    @staticmethod
    def _update_color_button(button: QPushButton, color: QColor) -> None:
        show_alpha = bool(button.property("showAlphaValue"))
        if show_alpha:
            alpha = color.alpha() / 255.0
            composite_lightness = (
                ((color.red() + color.green() + color.blue()) / 3.0) * alpha
                + 255 * (1.0 - alpha)
            )
            contrast = "#000000" if composite_lightness > 150 else "#FFFFFF"
            display = color.name(QColor.NameFormat.HexArgb).upper()
            background = f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"
        else:
            contrast = "#000000" if color.lightness() > 150 else "#FFFFFF"
            display = color.name().upper()
            background = color.name()
        button.setText(display)
        button.setMinimumWidth(0)
        button.setMinimumHeight(28)
        button.setMaximumHeight(36)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        purpose = button.property("colorPurpose")
        button.setToolTip(f"{purpose}\n現在: {display}" if purpose else display)
        button.setStyleSheet(
            f"QPushButton {{ background: {background}; color: {contrast}; border: 1px solid #65768a;"
            "border-radius: 6px; min-height: 24px; padding: 2px 4px; font-weight: 700; }"
            "QPushButton:hover { border: 2px solid #2457b2; }"
            "QPushButton:focus { border: 2px solid #173a82; }"
            "QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
        )

    def _update_hand_color_button(self) -> None:
        rgb = QColor(self._hand_color.red(), self._hand_color.green(), self._hand_color.blue())
        self._update_color_button(self.hand_color_button, rgb)
        display = rgb.name().upper()
        opacity = self.hand_opacity_spin.value()
        self.hand_color_button.setToolTip(
            f"手書きの色を選びます\n現在: {display} / 不透明度 {opacity}%"
        )
        self.hand_color_button.setAccessibleName(
            f"手書きの色 {display}、不透明度 {opacity}%"
        )

    @Slot(bool)
    def _eyedropper_toggled(self, enabled: bool) -> None:
        if enabled and self.hand_eyedropper_button.isChecked():
            self.hand_pen_button.setChecked(True)
            self._select_hand_tool(HandTool.PEN)
        self.drop_zone.preview.set_picking(enabled)
        self._update_hand_drawing_state()
        self.eyedropper_button.setText("画像上で選ぶ" if enabled else "画像から選ぶ")
        if enabled:
            self.preview_status.setText("透明にしたい背景色を画像上でクリックしてください")

    @Slot(int, int, int)
    def _color_picked(self, red: int, green: int, blue: int) -> None:
        self._target_color = QColor(red, green, blue)
        self._update_color_button(self.target_color_button, self._target_color)
        self.transparency_enabled.setChecked(True)
        self.eyedropper_button.setChecked(False)
        self._control_changed()

    @Slot(object, bool)
    def _hand_color_picked(self, color: QColor, temporary: bool) -> None:
        opacity = self._hand_color.alpha()
        self._hand_color = QColor(color.red(), color.green(), color.blue(), opacity)
        self._update_hand_color_button()
        display = self._hand_color.name().upper()
        self.preview_status.setText(f"スポイトで {display} を選びました")
        if not temporary:
            self.hand_pen_button.setChecked(True)
            self._select_hand_tool(HandTool.PEN)

    @Slot()
    def open_transparency_settings(self) -> None:
        if not self.transparency_section.toggle.isChecked():
            self.transparency_section.toggle.setChecked(True)
        self.settings_scroll.ensureWidgetVisible(self.transparency_section, 0, 24)

    def _default_output_stem(self) -> str:
        if self.source_path is None:
            return normalize_filename_stem("edited", default="edited", strip_extensions=KNOWN_IMAGE_EXTENSIONS)
        return normalize_filename_stem(
            f"{self.source_path.stem}_edited",
            default="edited",
            strip_extensions=KNOWN_IMAGE_EXTENSIONS,
        )

    def _set_filename_default(self, source_path: Path | None) -> None:
        del source_path
        previous = self.filename_edit.blockSignals(True)
        self.filename_edit.setText(self._default_output_stem() if self.source_path else "")
        self.filename_edit.blockSignals(previous)
        self._update_save_panel()

    def _normalized_output_stem(self) -> str:
        return normalize_filename_stem(
            self.filename_edit.text(),
            default="",
            strip_extensions=KNOWN_IMAGE_EXTENSIONS,
        )

    def _selected_output_extension(self) -> str:
        selected = EditOutputFormat(self.format_combo.currentData())
        if selected is EditOutputFormat.SAME:
            return EXTENSIONS.get(self._source_format or "PNG", ".png")
        return EXTENSIONS[selected.value]

    def _planned_output_path(self) -> Path | None:
        stem = self._normalized_output_stem()
        folder = self.output_folder
        if self.source_path is None or folder is None or not stem:
            return None
        return folder / f"{stem}{self._selected_output_extension()}"

    def _has_valid_output_folder(self) -> bool:
        return self.output_folder is not None and (not self.output_folder.exists() or self.output_folder.is_dir())

    @Slot()
    def _update_save_panel(self, *_args) -> None:
        self.filename_suffix_label.setText(self._selected_output_extension())
        if self.output_folder is None:
            self.folder_label.set_value("未選択")
        else:
            self.folder_label.set_path(self.output_folder)

        if self.source_path is None:
            self.planned_path_label.set_value("画像を読み込むと表示されます")
            self.save_hint_label.setText("画像を読み込むと、保存予定のファイル名を確認できます。")
        elif self.output_folder is None:
            self.planned_path_label.set_value("保存先を選ぶと表示されます")
            self.save_hint_label.setText("保存先を選ぶと書き出しできます。")
        elif self.output_folder.exists() and not self.output_folder.is_dir():
            self.planned_path_label.set_value(str(self.output_folder))
            self.save_hint_label.setText("保存先フォルダーを選び直してください。")
        else:
            stem = self._normalized_output_stem()
            if not stem:
                self.planned_path_label.set_value(str(self.output_folder / self._selected_output_extension().lstrip('.')))
                self.save_hint_label.setText("保存するファイル名を入力してください。")
            else:
                planned = self._planned_output_path()
                self.planned_path_label.set_path(planned)
                self.save_hint_label.setText("同名ファイルがある場合は自動で _2 などの連番を付けます。")
        self.open_folder_button.setEnabled(self._last_output is not None and self._last_output.parent.is_dir())
        if not self._applying:
            self._update_actions()

    @Slot()
    def _normalize_output_filename_input(self) -> None:
        normalized = self._normalized_output_stem()
        if normalized != self.filename_edit.text():
            self.filename_edit.setText(normalized)

    @Slot()
    def send_palette_to_pixel(self) -> None:
        colors = self._palette_replacements or self._palette_values
        if not colors:
            self._set_palette_feedback("先に色を取り出してください。", "warning")
            return
        self._set_palette_feedback(f"✓ {len(colors)}色のパレットをドット絵へ送りました", "success")
        self.palette_handoff_requested.emit(colors)

    @Slot()
    def open_pixel_tab(self) -> None:
        if self._palette_values:
            self.palette_open_requested.emit()

    @Slot()
    def choose_output_folder(self) -> None:
        initial = str(self.output_folder or (self.source_path.parent if self.source_path else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "加工した画像の保存先を選ぶ", initial)
        if folder:
            self.output_folder = Path(folder).resolve()
            self._output_folder_explicit = True
            self.folder_label.set_path(self.output_folder)
            self._update_save_panel()

    def _update_save_options(self, *_args) -> None:
        selected = EditOutputFormat(self.format_combo.currentData())
        source_is_jpeg = self._source_format in {"JPEG", "JPG"}
        jpeg = selected is EditOutputFormat.JPEG or (selected is EditOutputFormat.SAME and source_is_jpeg)
        quality_visible = selected is not EditOutputFormat.PNG
        self.jpeg_background_label.setVisible(jpeg)
        self.jpeg_background_combo.setVisible(jpeg)
        self.quality_label.setVisible(quality_visible)
        self.quality_spin.setVisible(quality_visible)
        transparent = self.transparency_enabled.isChecked() or self._source_has_alpha
        self.alpha_hint.setText("透明部分を保存する場合はPNGがおすすめです" if transparent and jpeg else "")
        self._update_output_info()
        self._update_save_panel()

    @Slot()
    def save_image(self) -> None:
        self._flush_text_history()
        self._normalize_output_filename_input()
        if not self.source_path or self._thread is not None:
            return
        if not self.source_path.is_file():
            QMessageBox.warning(self, "元画像が見つかりません", MISSING_SOURCE_MESSAGE)
            return
        normalized = self._normalized_output_stem()
        if not normalized:
            QMessageBox.warning(self, "ファイル名を入力してください", "保存するファイル名を入力してください。")
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
            self._effective_settings(),
            EditOutputFormat(self.format_combo.currentData()),
            self.jpeg_background_combo.currentData(),
            self.quality_spin.value(),
            normalized,
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
        self.result_label.setText("✓ 保存しました")
        self.saved_filename.set_value(result.output_path.name, str(result.output_path))
        self.saved_path.set_path(result.output_path)
        self.saved_box.show()
        self._update_save_panel()
        alpha_text = "透明部分を保持" if result.has_alpha else "不透明"
        self.output_info.setText(
            f"加工後（保存済み）\n{result.width} × {result.height} / {result.output_format}\n"
            f"{self._human_bytes(result.size_bytes)} / {alpha_text}"
        )

    @Slot(str)
    def _on_save_failed(self, message: str) -> None:
        self.result_label.setStyleSheet("color: #c62828; font-weight: 700;")
        self.result_label.setText(message)
        self._update_save_panel()

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self._set_processing(False)

    def _apply_processing_control_state(self, processing: bool, *, palette_requeue: bool = False) -> None:
        for widget in (
            self.drop_zone,
            self.current_source_card.change_button,
            self.filter_combo,
            self.text_enabled,
            self.text_details,
            self.hand_mode_enabled,
            self.hand_details,
            self.hand_pen_button,
            self.hand_eraser_button,
            self.hand_eyedropper_button,
            self.hand_color_button,
            self.hand_size_spin,
            self.hand_opacity_spin,
            self.hand_visible_check,
            self.hand_clear_button,
            self.mosaic_mode_enabled,
            self.mosaic_details,
            self.mosaic_pen_button,
            self.mosaic_eraser_button,
            self.mosaic_size_spin,
            self.mosaic_block_spin,
            self.mosaic_visible_check,
            self.mosaic_clear_button,
            self.transparency_enabled,
            self.transparency_details,
            self.canvas_preset_combo,
            self.custom_canvas,
            self.placement_combo,
            self.padding_combo,
            self.canvas_background_combo,
            self.canvas_color_button,
            self.sticker_enabled,
            self.sticker_details,
            self.sticker_prereq_button,
            self.sticker_outline_width_spin,
            self.sticker_outline_color_button,
            self.sticker_shadow_enabled,
            self.line_art_enabled,
            self.line_art_color_button,
            self.line_art_background_combo,
            self.line_art_background_color_button,
            self.palette_enabled,
            self.palette_quantize_enabled,
            self.palette_blend_mode_combo,
            self.palette_count_combo,
            self.palette_extract_button,
            self.palette_reset_button,
            self.palette_send_button,
            self.palette_open_button,
            self.palette_chips_widget,
            self.original_button,
            self.edited_button,
            self.undo_button,
            self.redo_button,
            self.preview_undo_button,
            self.preview_redo_button,
            self.reset_button,
            self.clear_all_button,
            self.format_combo,
            self.quality_spin,
            self.jpeg_background_combo,
            self.filename_edit,
            self.folder_button,
            self.save_button,
            self.open_image_button,
            self.open_folder_button,
        ):
            widget.setEnabled(not processing)
        if processing and palette_requeue:
            self.palette_count_combo.setEnabled(True)
            self.palette_extract_button.setEnabled(True)
        for section in self.sections:
            section.toggle.setEnabled(not processing)
        self.mosaic_section.toggle.setEnabled(not processing)
        self._update_hand_drawing_state()

    def _set_processing(self, processing: bool, *, palette_requeue: bool = False) -> None:
        self._processing_controls_locked = processing
        self._processing_palette_requeue = processing and palette_requeue
        self._apply_processing_control_state(processing, palette_requeue=palette_requeue)
        self.processing_changed.emit(processing)
        if not processing:
            self._update_visibility()
            self._update_actions()

    def _update_actions(self) -> None:
        loaded = self.source_path is not None
        idle = self._thread is None and self._palette_thread is None
        save_ready = loaded and idle and self._has_valid_output_folder() and bool(self._normalized_output_stem())
        can_undo = loaded and idle and self._history_index > 0
        can_redo = loaded and idle and self._history_index + 1 < len(self._history)
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        self.preview_undo_button.setEnabled(can_undo)
        self.preview_redo_button.setEnabled(can_redo)
        self.reset_button.setEnabled(
            loaded and idle and self.settings() != self._default_settings()
        )
        self.clear_all_button.setEnabled(loaded and idle)
        self.clear_image_button.setEnabled(loaded and idle)
        self.save_button.setEnabled(save_ready)
        self.folder_button.setEnabled(loaded and idle)
        saved_file_ready = idle and self._last_output is not None and self._last_output.is_file()
        saved_folder_ready = idle and self._last_output is not None and self._last_output.parent.is_dir()
        self.open_image_button.setEnabled(saved_file_ready)
        self.open_folder_button.setEnabled(saved_folder_ready)
        self.edit_upscale_button.setEnabled(saved_file_ready)
        self.original_button.setEnabled(loaded and idle and not self._show_original)
        self.edited_button.setEnabled(loaded and idle and self._show_original)
        self.current_source_card.change_button.setEnabled(idle)

    @Slot()
    def open_saved_image(self) -> None:
        path = self._last_output
        if not path or not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "画像を開けません", "保存した画像を開けませんでした。")

    @Slot()
    def open_saved_folder(self) -> None:
        folder = self._last_output.parent if self._last_output else None
        if not folder or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "Windows Explorerで保存先を開けませんでした。")

    @Slot()
    def send_result_to_upscale(self) -> None:
        path = self._last_output
        if not path or not path.is_file():
            QMessageBox.warning(self, "高画質化へ渡せません", "保存済みの加工結果を選択してください。")
            return
        self.result_handoff_requested.emit(path, "upscale")

    def can_close(self) -> bool:
        return self._thread is None and self._palette_thread is None and self._preview_thread is None

    def can_replace_source(self) -> bool:
        return self._thread is None and self._palette_thread is None

    def cleanup(self) -> None:
        self._cancel_hand_stroke()
        self._preview_generation += 1
        self._preview_pending_request = None
        self.preview_activity.invalidate()
        self._preview_activity_token = None
        self._clear_palette_preview_handoff(clear_refresh=True)
        self.cancel_palette_extraction()

    @staticmethod
    def _human_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
