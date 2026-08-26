from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
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
from .editing import (
    CanvasBackground,
    CanvasSettings,
    EditOutputFormat,
    EditResult,
    EditService,
    EditSettings,
    FilterPreset,
    LineArtAmount,
    LineArtBackground,
    LineArtSettings,
    PaletteSettings,
    PlacementMode,
    StickerSettings,
    TextPosition,
    TextSettings,
    TransparencySettings,
)
from .editing.palette import extract_palette
from .editing.renderer import load_normalized, prepare_palette_source, render_path_preview
from .editing.service import EditProcessingError, SOURCE_FORMATS
from .naming import EXTENSIONS, KNOWN_IMAGE_EXTENSIONS, normalize_filename_stem
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
LINE_ART_LABELS = {
    LineArtAmount.CLEAN: "すっきり",
    LineArtAmount.STANDARD: "標準",
    LineArtAmount.DETAILED: "細密",
    LineArtAmount.COMIC: "コミック",
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


class QuickEditPage(QWidget):
    processing_changed = Signal(bool)
    palette_handoff_requested = Signal(object)

    def __init__(self, service: EditService | None = None) -> None:
        super().__init__()
        self.service = service or EditService()
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
        self._palette_values: tuple[tuple[int, int, int], ...] = ()
        self._palette_replacements: tuple[tuple[int, int, int], ...] = ()
        self._palette_mapping: tuple[int, ...] = ()
        self._palette_mapping_size = (0, 0)
        self._palette_mapping_digest = ""
        self._palette_generation = 0
        self._selected_palette_index = -1
        self._palette_source_signature = None
        self._palette_needs_reextract = False
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

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.settings_scroll.setMinimumWidth(250)
        self.settings_scroll.setMaximumWidth(355)
        left = QWidget()
        left.setMinimumWidth(0)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 12, 4, 12)
        heading = QLabel("何をしますか？")
        heading.setStyleSheet("font-size: 18px; font-weight: 700; color: #182230;")
        ll.addWidget(heading)
        history_row = QGridLayout()
        history_row.setSpacing(4)
        self.undo_button = QPushButton("元に戻す")
        self.redo_button = QPushButton("やり直す")
        self.reset_button = QPushButton("加工をリセット")
        for button in (self.undo_button, self.redo_button, self.reset_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.reset_button.clicked.connect(self.reset_edits)
        history_row.addWidget(self.undo_button, 0, 0)
        history_row.addWidget(self.redo_button, 0, 1)
        history_row.addWidget(self.reset_button, 1, 0, 1, 2)
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
        self.text_enabled = QCheckBox()
        self.text_enabled.setVisible(False)
        self.text_details = QWidget()
        text_form = QFormLayout(self.text_details)
        self._configure_form(text_form)
        self.text_edit = IMEPlainTextEdit()
        self.text_edit.setPlaceholderText("例：おはよう")
        self.text_edit.setFixedHeight(72)
        self.font_combo = FontPickerButton(self.font_catalog)
        self._select_default_font()
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
            "入力すると、そのまま反映されます。空にすると文字を外します",
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

        material_content = QWidget()
        material_layout = QVBoxLayout(material_content)
        material_layout.setContentsMargins(8, 2, 4, 6)
        sticker_group = QGroupBox("ステッカー")
        sticker_layout = QVBoxLayout(sticker_group)
        self.sticker_explanation_label = QLabel("切り抜き画像をふち付きにします。\n背景を透明にしてから使うときれいです。")
        self.sticker_explanation_label.setWordWrap(True)
        self.sticker_explanation_label.setStyleSheet("color: #667085;")
        sticker_layout.addWidget(self.sticker_explanation_label)
        self.sticker_prereq_status = QLabel()
        self.sticker_prereq_status.setWordWrap(True)
        self.sticker_prereq_status.setStyleSheet("color: #9a6700;")
        sticker_layout.addWidget(self.sticker_prereq_status)
        self.sticker_prereq_button = QPushButton("背景を透明にする設定を開く")
        self.sticker_prereq_button.clicked.connect(self.open_transparency_settings)
        sticker_layout.addWidget(self.sticker_prereq_button)
        sticker_form = QFormLayout()
        self._configure_form(sticker_form)
        self.sticker_enabled = QCheckBox("ステッカーにする")
        self.sticker_outline_width_spin = self._spin(1, 50, 8, " px")
        self.sticker_outline_color_button = QPushButton()
        self.sticker_outline_color_button.clicked.connect(self.choose_sticker_color)
        self.sticker_shadow_enabled = QCheckBox("影を付ける")
        sticker_form.addRow("", self.sticker_enabled)
        sticker_form.addRow("フチ色", self.sticker_outline_color_button)
        sticker_form.addRow("フチ太さ", self.sticker_outline_width_spin)
        sticker_form.addRow("", self.sticker_shadow_enabled)
        sticker_layout.addLayout(sticker_form)
        material_layout.addWidget(sticker_group)
        line_group = QGroupBox("線画")
        line_form = QFormLayout(line_group)
        self._configure_form(line_form)
        self.line_art_enabled = QCheckBox("線画にする")
        self.line_art_amount_combo = QComboBox()
        for amount in (LineArtAmount.CLEAN, LineArtAmount.STANDARD, LineArtAmount.DETAILED, LineArtAmount.COMIC):
            self.line_art_amount_combo.addItem(LINE_ART_LABELS[amount], amount.value)
        self.line_art_color_button = QPushButton()
        self.line_art_color_button.clicked.connect(self.choose_line_art_color)
        self.line_art_background_combo = QComboBox()
        for label, value in (("透明", LineArtBackground.TRANSPARENT.value), ("白", LineArtBackground.WHITE.value), ("黒", LineArtBackground.BLACK.value), ("指定色", LineArtBackground.CUSTOM.value)):
            self.line_art_background_combo.addItem(label, value)
        self.line_art_background_color_button = QPushButton()
        self.line_art_background_color_button.clicked.connect(self.choose_line_art_background_color)
        line_form.addRow("", self.line_art_enabled)
        line_form.addRow("仕上がり", self.line_art_amount_combo)
        line_form.addRow("線色", self.line_art_color_button)
        line_form.addRow("背景", self.line_art_background_combo)
        line_form.addRow("指定色", self.line_art_background_color_button)
        material_layout.addWidget(line_group)
        palette_group = QGroupBox("色を整理・変える")
        palette_layout = QVBoxLayout(palette_group)
        self.palette_enabled = QCheckBox("代表色を抽出")
        self.palette_enabled.setVisible(False)
        self.palette_quantize_enabled = QCheckBox("この色数に整理する")
        palette_layout.addWidget(self.palette_quantize_enabled)
        palette_row = QHBoxLayout()
        self.palette_count_combo = QComboBox()
        for count in (5, 6, 8):
            self.palette_count_combo.addItem(f"{count}色", count)
        self.palette_count_combo.setCurrentIndex(self.palette_count_combo.findData(6))
        self.palette_extract_button = QPushButton("代表色を抽出")
        self.palette_extract_button.clicked.connect(self.extract_palette)
        palette_row.addWidget(self.palette_count_combo, 1)
        palette_row.addWidget(self.palette_extract_button, 1)
        palette_layout.addLayout(palette_row)
        self.palette_chips_widget = QWidget()
        self.palette_chips_layout = QGridLayout(self.palette_chips_widget)
        self.palette_chips_layout.setContentsMargins(0, 2, 0, 2)
        self.palette_chips_layout.setHorizontalSpacing(6)
        self.palette_chips_layout.setVerticalSpacing(6)
        palette_layout.addWidget(self.palette_chips_widget)
        self.palette_reset_button = QPushButton("元の配色に戻す")
        self.palette_reset_button.clicked.connect(self.reset_palette)
        palette_layout.addWidget(self.palette_reset_button)
        self.palette_send_button = QPushButton("現在の配色をドット絵で使う")
        self.palette_send_button.clicked.connect(self.send_palette_to_pixel)
        palette_layout.addWidget(self.palette_send_button)
        self.palette_feedback_label = QLabel()
        self.palette_feedback_label.setWordWrap(True)
        self.palette_feedback_label.setStyleSheet("color: #137333; font-weight: 700;")
        palette_layout.addWidget(self.palette_feedback_label)
        material_layout.addWidget(palette_group)
        self.material_section = CollapsibleSection("素材化", "ステッカー、線画、代表色を目的別に作ります", material_content)
        ll.addWidget(self.material_section)
        ll.addStretch()
        self.settings_scroll.setWidget(left)
        splitter.addWidget(self.settings_scroll)

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
            self.line_art_amount_combo,
            self.line_art_background_combo,
            self.palette_count_combo,
        ):
            combo.currentIndexChanged.connect(self._control_changed)
        for spin in (
            self.font_size_spin,
            self.outline_width_spin,
            self.canvas_width_spin,
            self.canvas_height_spin,
            self.sticker_outline_width_spin,
        ):
            spin.valueChanged.connect(self._control_changed)
        self.text_enabled.toggled.connect(self._text_toggled)
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

    @Slot()
    def choose_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "加工する画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if name:
            self.load_image(Path(name))

    @Slot(object)
    def load_image(self, path: Path) -> None:
        if self._thread is not None:
            return
        if self._palette_thread is not None:
            self.cancel_palette_extraction()
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
                alpha_min, _alpha_max = normalized.convert("RGBA").getchannel("A").getextrema()
        except (OSError, UnidentifiedImageError, ValueError):
            QMessageBox.warning(self, "画像を開けません", "PNG / JPEG / WebP画像を選んでください。")
            return
        self.finish_ime(clear_focus=True)
        self.source_path = path.resolve()
        self._palette_generation += 1
        self._source_size = (width, height)
        self._source_format = source_format
        self._source_size_bytes = self.source_path.stat().st_size
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
        self.apply_settings(EditSettings())
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
            line_art=LineArtSettings(self.line_art_enabled.isChecked(), LineArtAmount(self.line_art_amount_combo.currentData()), (self._line_art_color.red(), self._line_art_color.green(), self._line_art_color.blue(), self._line_art_color.alpha()), LineArtBackground(self.line_art_background_combo.currentData()), (self._line_art_background_color.red(), self._line_art_background_color.green(), self._line_art_background_color.blue(), self._line_art_background_color.alpha())),
            palette=PaletteSettings(self.palette_enabled.isChecked(), self.palette_quantize_enabled.isChecked(), int(self.palette_count_combo.currentData()), self._palette_values, self._palette_replacements, self._palette_mapping, self._palette_mapping_size[0], self._palette_mapping_size[1], self._palette_mapping_digest),
        )

    def apply_settings(self, settings: EditSettings) -> None:
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
        self.line_art_amount_combo.setCurrentIndex(self.line_art_amount_combo.findData(settings.line_art.amount.value))
        self._line_art_color = QColor(*settings.line_art.line_color)
        self.line_art_background_combo.setCurrentIndex(self.line_art_background_combo.findData(settings.line_art.background.value))
        self._line_art_background_color = QColor(*settings.line_art.custom_background)
        self.palette_enabled.setChecked(settings.palette.enabled)
        self.palette_quantize_enabled.setChecked(settings.palette.quantize_enabled)
        self.palette_count_combo.setCurrentIndex(self.palette_count_combo.findData(settings.palette.color_count))
        self._palette_values = settings.palette.palette
        self._palette_replacements = settings.palette.replacements or settings.palette.palette
        self._selected_palette_index = -1
        self._palette_mapping = settings.palette.mapping
        self._palette_mapping_size = (settings.palette.mapping_width, settings.palette.mapping_height)
        self._palette_mapping_digest = settings.palette.mapping_digest
        self._palette_source_signature = self._upstream_signature(settings)
        self._palette_needs_reextract = False
        self._rebuild_palette_chips()
        self._update_color_button(self.text_color_button, self._text_color)
        self._update_color_button(self.outline_color_button, self._outline_color)
        self._update_color_button(self.target_color_button, self._target_color)
        self._update_color_button(self.canvas_color_button, self._canvas_color)
        self._update_color_button(self.sticker_outline_color_button, self._sticker_outline_color)
        self._update_color_button(self.line_art_color_button, self._line_art_color)
        self._update_color_button(self.line_art_background_color_button, self._line_art_background_color)
        self._update_visibility()
        self._applying = was_applying
        if not self._applying:
            self._show_original = False
            self.schedule_preview()
            self._update_actions()

    def _palette_chip_style(self, color: tuple[int, int, int], *, clickable: bool) -> str:
        contrast = "#000000" if sum(color) >= 384 else "#FFFFFF"
        border = "2px solid #65768a" if clickable else "1px solid #98a2b3"
        hover = "QPushButton:hover { border: 2px solid #2457b2; }" if clickable else ""
        return (
            f"background: rgb{color}; color: {contrast}; border: {border}; border-radius: 6px; "
            "min-height: 30px; font-weight: 700; padding: 4px 8px;"
            + hover
        )

    def _update_palette_controls(self) -> None:
        has_palette = bool(self._palette_values)
        self.palette_reset_button.setEnabled(has_palette and self._palette_replacements != self._palette_values)
        self.palette_send_button.setEnabled(has_palette)
        self.palette_quantize_enabled.setEnabled(has_palette)
        if not has_palette and not self.palette_feedback_label.text():
            self.palette_feedback_label.setText("")

    def _rebuild_palette_chips(self) -> None:
        while self.palette_chips_layout.count():
            item = self.palette_chips_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not self._palette_values:
            empty = QLabel("代表色を抽出すると、ここで元の色と置き換え先を並べて調整できます。")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: #667085;")
            self.palette_chips_layout.addWidget(empty, 0, 0, 1, 4)
            self._update_palette_controls()
            return
        for index, source_color in enumerate(self._palette_values):
            replacement = self._palette_replacements[index] if index < len(self._palette_replacements) else source_color
            original_chip = QLabel()
            original_chip.setText(f"#{source_color[0]:02X}{source_color[1]:02X}{source_color[2]:02X}")
            original_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            original_chip.setMinimumHeight(30)
            original_chip.setStyleSheet(self._palette_chip_style(source_color, clickable=False))
            arrow = QLabel("→")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setStyleSheet("color: #667085; font-weight: 700;")
            replacement_button = QPushButton(f"#{replacement[0]:02X}{replacement[1]:02X}{replacement[2]:02X}")
            replacement_button.setToolTip(f"代表色 {index + 1} の置き換え先を選ぶ")
            replacement_button.setStyleSheet(self._palette_chip_style(replacement, clickable=True))
            replacement_button.clicked.connect(lambda checked=False, i=index: self._replace_palette_color(i))
            row = index
            self.palette_chips_layout.addWidget(original_chip, row, 0)
            self.palette_chips_layout.addWidget(arrow, row, 1)
            self.palette_chips_layout.addWidget(replacement_button, row, 2)
        self.palette_chips_layout.setColumnStretch(0, 1)
        self.palette_chips_layout.setColumnStretch(2, 1)
        self.palette_chips_layout.setRowStretch(len(self._palette_values), 1)
        self._update_palette_controls()

    @staticmethod
    def _palette_source_identity(path: Path):
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)

    @Slot()
    def extract_palette(self) -> None:
        if not self.source_path or self._thread is not None or self._palette_thread is not None:
            return
        self._palette_generation += 1
        self._palette_request_id += 1
        request_id = self._palette_request_id
        source = self.source_path
        settings = self.settings()
        try:
            identity = self._palette_source_identity(source)
        except OSError:
            self.preview_status.setText("代表色を抽出できませんでした。画像を確認してください。")
            return
        signature = (settings.filter_preset.value, settings.transparency, settings.palette.color_count)
        generation = self._palette_generation
        self._palette_active_request = (generation, request_id, identity, signature)

        self.preview_status.setText("処理中…")
        self._set_processing(True)
        thread = PaletteExtractionThread(source, settings, generation, request_id, identity, signature)
        self._palette_thread = thread
        thread.succeeded.connect(self._on_palette_extracted)
        thread.failed.connect(self._on_palette_extraction_failed)
        thread.finished.connect(lambda t=thread: self._finalize_palette_thread(t))
        thread.start()

    @Slot(object)
    def _on_palette_extracted(self, payload) -> None:
        generation, request_id, identity, signature, mapping = payload
        if self._palette_active_request != (generation, request_id, identity, signature):
            return
        try:
            current_identity = self._palette_source_identity(self.source_path) if self.source_path else None
        except OSError:
            return
        if current_identity != identity:
            return
        current = self.settings()
        current_signature = (current.filter_preset.value, current.transparency, current.palette.color_count)
        if current_signature != signature:
            return
        self._palette_values = mapping.palette
        self._palette_replacements = mapping.palette
        self._palette_mapping = mapping.indices
        self._palette_mapping_size = (mapping.width, mapping.height)
        self._palette_mapping_digest = mapping.digest
        self._palette_needs_reextract = False
        self.palette_enabled.setChecked(True)
        self.palette_feedback_label.setText("代表色を抽出しました。必要なら置き換え先だけ変えられます。")
        self._rebuild_palette_chips()
        self._control_changed()

    @Slot(str, object)
    def _on_palette_extraction_failed(self, message: str, token) -> None:
        if self._palette_active_request == token:
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.preview_status.setText(message)

    def _finalize_palette_thread(self, thread: PaletteExtractionThread) -> None:
        if thread is self._palette_thread:
            self._palette_thread = None
            self._palette_active_request = None
            self._set_processing(False)
        thread.deleteLater()

    def cancel_palette_extraction(self) -> None:
        self._palette_generation += 1
        self._palette_request_id += 1
        self._palette_active_request = None
        if self._palette_thread is not None:
            self._palette_thread.requestInterruption()

    @Slot()
    def reset_palette(self) -> None:
        if self._palette_values:
            self._palette_replacements = self._palette_values
            self._selected_palette_index = -1
            self._palette_needs_reextract = False
            self.palette_feedback_label.setText("置き換え先を元の配色へ戻しました。")
            self._rebuild_palette_chips()
            self._control_changed()

    def _replace_palette_color(self, index: int) -> None:
        self._selected_palette_index = index
        if not 0 <= index < len(self._palette_replacements):
            return
        current = QColor(*self._palette_replacements[index])
        color = QColorDialog.getColor(current, self, "代表色を変更", QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if not color.isValid():
            return
        values = list(self._palette_replacements)
        values[index] = (color.red(), color.green(), color.blue())
        self._palette_replacements = tuple(values)
        self.palette_feedback_label.setText(f"代表色 {index + 1} の置き換え先を更新しました。")
        self._rebuild_palette_chips()
        self._control_changed()

    def _choose_material_color(self, title: str, attribute: str, button: QPushButton) -> None:
        current = getattr(self, attribute)
        color = QColorDialog.getColor(current, self, title, QColorDialog.ColorDialogOption.ShowAlphaChannel)
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
        self._choose_material_color("線画背景色を選ぶ", "_line_art_background_color", self.line_art_background_color_button)

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
            self._palette_values = ()
            self._palette_replacements = ()
            self._palette_mapping = ()
            self._palette_mapping_size = (0, 0)
            self._palette_mapping_digest = ""
            self._selected_palette_index = -1
            previous_enabled = self.palette_enabled.blockSignals(True)
            self.palette_enabled.setChecked(False)
            self.palette_enabled.blockSignals(previous_enabled)
            previous_quantize = self.palette_quantize_enabled.blockSignals(True)
            self.palette_quantize_enabled.setChecked(False)
            self.palette_quantize_enabled.blockSignals(previous_quantize)
            self._palette_needs_reextract = True
            self.palette_feedback_label.setText("")
            self._rebuild_palette_chips()
            self.preview_status.setText("元画像の変更後は、代表色をもう一度抽出してください。")
        finally:
            self._palette_source_signature = signature
            self._invalidating_palette = False

    @Slot()
    def _control_changed(self, *_args) -> None:
        if self._applying:
            return
        self._set_text_enabled_from_content()
        self._invalidate_palette_for_upstream_change(self.settings())
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

    def _update_sticker_prerequisite_ui(self) -> None:
        if not self.source_path:
            self.sticker_prereq_status.setText("画像を読み込むと、ステッカー向けのふち付きを確認できます。")
            self.sticker_prereq_status.setStyleSheet("color: #667085;")
            self.sticker_prereq_button.setVisible(False)
            return
        if self._sticker_ready_for_preview():
            if self._source_alpha_min < 255:
                message = "元画像に透明部分があります。そのままステッカー仕上げを確認できます。"
            else:
                message = "背景を透明にする設定が有効です。ステッカー仕上げをそのまま確認できます。"
            self.sticker_prereq_status.setText(message)
            self.sticker_prereq_status.setStyleSheet("color: #137333;")
            self.sticker_prereq_button.setVisible(False)
            return
        self.sticker_prereq_status.setText("先に背景を透明にすると、ステッカー向けのふちがきれいに付きます。")
        self.sticker_prereq_status.setStyleSheet("color: #9a6700;")
        self.sticker_prereq_button.setVisible(True)

    def _update_visibility(self) -> None:
        self.text_details.setVisible(True)
        self.outline_color_button.setEnabled(self.outline_enabled.isChecked())
        self.outline_width_spin.setEnabled(self.outline_enabled.isChecked())
        self.transparency_details.setVisible(self.transparency_enabled.isChecked())
        self.custom_canvas.setVisible(self.canvas_preset_combo.currentData() == "custom")
        self.canvas_color_button.setVisible(
            self.canvas_background_combo.currentData() == CanvasBackground.CUSTOM.value
        )
        sticker_ready = self._sticker_ready_for_preview()
        self.sticker_outline_width_spin.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.sticker_outline_color_button.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.sticker_shadow_enabled.setEnabled(self.sticker_enabled.isChecked() and sticker_ready)
        self.line_art_amount_combo.setEnabled(self.line_art_enabled.isChecked())
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
                image = render_path_preview(self.source_path, self._effective_settings(), 1400)
            self.drop_zone.preview.set_image(pil_to_qimage(image))
            self.preview_status.setStyleSheet("color: #667085;")
            status = ("元画像" if self._show_original else "加工後") + f" · Preview {image.width} × {image.height}"
            if self._palette_needs_reextract:
                status += " · 代表色を再抽出してください"
            self.preview_status.setText(status)
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
        self._flush_text_history()
        if self._history_index > 0:
            self._history_index -= 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def redo(self) -> None:
        self._flush_text_history()
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self.apply_settings(self._history[self._history_index])

    @Slot()
    def reset_edits(self) -> None:
        self.cancel_palette_extraction()
        self._flush_text_history()
        self.finish_ime(clear_focus=True)
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
            self.palette_feedback_label.setStyleSheet("color: #9a6700; font-weight: 700;")
            self.palette_feedback_label.setText("先に代表色を抽出してください。")
            return
        self.palette_feedback_label.setStyleSheet("color: #137333; font-weight: 700;")
        self.palette_feedback_label.setText("現在の配色をドット絵へ渡しました。")
        self.palette_handoff_requested.emit(colors)

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
        self.jpeg_background_combo.setVisible(jpeg)
        self.quality_spin.setVisible(selected is not EditOutputFormat.PNG)
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
        self.result_label.setText(f"✓ 加工した画像を保存しました\n{result.output_path.name}")
        self.saved_path.set_path(result.output_path)
        self.saved_box.show()
        self._update_save_panel()
        alpha_text = "Alpha保持" if result.has_alpha else "不透明"
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
            self.sticker_enabled,
            self.sticker_outline_width_spin,
            self.sticker_outline_color_button,
            self.sticker_shadow_enabled,
            self.line_art_enabled,
            self.line_art_amount_combo,
            self.line_art_color_button,
            self.line_art_background_combo,
            self.line_art_background_color_button,
            self.palette_enabled,
            self.palette_quantize_enabled,
            self.palette_count_combo,
            self.palette_extract_button,
            self.palette_reset_button,
            self.palette_chips_widget,
            self.undo_button,
            self.redo_button,
            self.reset_button,
            self.format_combo,
            self.quality_spin,
            self.jpeg_background_combo,
            self.filename_edit,
            self.folder_button,
            self.save_button,
            self.open_folder_button,
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
        idle = self._thread is None and self._palette_thread is None
        save_ready = loaded and idle and self._has_valid_output_folder() and bool(self._normalized_output_stem())
        self.undo_button.setEnabled(loaded and idle and self._history_index > 0)
        self.redo_button.setEnabled(loaded and idle and self._history_index + 1 < len(self._history))
        self.reset_button.setEnabled(loaded and idle and self.settings() != EditSettings())
        self.save_button.setEnabled(save_ready)
        self.folder_button.setEnabled(loaded and idle)
        self.open_folder_button.setEnabled(idle and self._last_output is not None and self._last_output.parent.is_dir())
        self.original_button.setEnabled(loaded and not self._show_original)
        self.edited_button.setEnabled(loaded and self._show_original)

    @Slot()
    def open_saved_folder(self) -> None:
        folder = self._last_output.parent if self._last_output else None
        if not folder or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "Windows Explorerで保存先を開けませんでした。")

    def can_close(self) -> bool:
        return self._thread is None and self._palette_thread is None

    def cleanup(self) -> None:
        self.cancel_palette_extraction()

    @staticmethod
    def _human_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
