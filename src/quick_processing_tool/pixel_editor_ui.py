from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QToolButton,
    QButtonGroup, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .pixel_editor.canvas import MAX_SIZE, MIN_SIZE, PixelCanvas, pixel_from_display
from .pixel_editor.importers import import_as_pixels, load_reference
from .pixel_editor.models import PixelTool, ReferenceImage
from .pixel_editor.service import PixelExportError, save_png


def _qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()


SUPPORTED_DROP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


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


class PixelImportChoiceDialog(QDialog):
    """Small Japanese chooser kept separate so drop behavior is testable."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.choice: str | None = None
        self.setWindowTitle("画像を読み込む")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{path.name} をどのように読み込みますか？"))
        reference = QPushButton("下絵として使う")
        pixels = QPushButton("ドット化して編集")
        cancel = QPushButton("キャンセル")
        reference.clicked.connect(lambda: self._finish("reference"))
        pixels.clicked.connect(lambda: self._finish("pixels"))
        cancel.clicked.connect(self.reject)
        layout.addWidget(reference)
        layout.addWidget(pixels)
        layout.addWidget(cancel)

    def _finish(self, choice: str):
        self.choice = choice
        self.accept()

    @classmethod
    def choose(cls, path: Path, parent=None) -> str | None:
        dialog = cls(path, parent)
        return dialog.choice if dialog.exec() == QDialog.Accepted else None

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
        self.canvas.stroke(self._last_pixel, px, self.color, self.pencil_size, erase=erase, commit=False)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source_path: Path | None = None
        self.reference: ReferenceImage | None = None
        self.import_choice_provider = lambda path: PixelImportChoiceDialog.choose(path, self)
        self.canvas = PixelCanvas()
        self.canvas_view = PixelCanvasView(self.canvas)
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
        tools = QGroupBox("ツール")
        tl = QVBoxLayout(tools)
        self.pencil_button = QPushButton("鉛筆")
        self.eraser_button = QPushButton("消しゴム")
        self.eyedropper_button = QPushButton("スポイト")
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self._tool_buttons = {PixelTool.PENCIL: self.pencil_button, PixelTool.ERASER: self.eraser_button, PixelTool.EYEDROPPER: self.eyedropper_button}
        for tool, button in self._tool_buttons.items():
            button.setCheckable(True)
            self.tool_group.addButton(button)
            button.clicked.connect(lambda checked=False, t=tool: self._set_tool(t))
            tl.addWidget(button)
        self.pencil_button.setChecked(True)
        size_row = QHBoxLayout(); size_row.addWidget(QLabel("太さ")); self.size_combo = QComboBox(); self.size_combo.addItems(["1", "2", "3"]); size_row.addWidget(self.size_combo); tl.addLayout(size_row)
        left_layout.addWidget(tools)
        canvas_group = QGroupBox("キャンバス")
        cl = QFormLayout(canvas_group)
        self.preset_combo = QComboBox(); self.preset_combo.addItems(["32 × 32", "64 × 64", "128 × 128", "カスタム"]); self.preset_combo.setCurrentIndex(2); self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        cl.addRow("サイズ", self.preset_combo)
        self.width_spin = QSpinBox(); self.width_spin.setRange(MIN_SIZE, MAX_SIZE); self.width_spin.setValue(128)
        self.height_spin = QSpinBox(); self.height_spin.setRange(MIN_SIZE, MAX_SIZE); self.height_spin.setValue(128)
        cl.addRow("幅", self.width_spin); cl.addRow("高さ", self.height_spin)
        self.new_button = QPushButton("新規キャンバス"); self.new_button.clicked.connect(self.new_canvas); cl.addRow(self.new_button)
        left_layout.addWidget(canvas_group)
        io_group = QGroupBox("画像")
        il = QVBoxLayout(io_group)
        self.reference_button = QPushButton("下絵を読み込む"); self.reference_button.clicked.connect(self.load_reference)
        self.pixelize_button = QPushButton("ドット化して編集"); self.pixelize_button.clicked.connect(self.load_pixels)
        il.addWidget(self.reference_button); il.addWidget(self.pixelize_button); left_layout.addWidget(io_group)
        view_group = QGroupBox("表示")
        vl = QVBoxLayout(view_group)
        self.zoom_combo = QComboBox(); self.zoom_combo.addItems(["Fit", "2x", "4x", "8x", "16x"]); self.zoom_combo.setCurrentIndex(2); self.zoom_combo.currentIndexChanged.connect(self._zoom_changed); vl.addWidget(self.zoom_combo)
        self.grid_check = QCheckBox("グリッド"); self.grid_check.setChecked(True); self.grid_check.toggled.connect(lambda x: setattr(self.canvas_view, "grid_enabled", x) or self.canvas_view.viewport().update()); vl.addWidget(self.grid_check)
        self.undo_button = QPushButton("元に戻す"); self.undo_button.clicked.connect(self.undo); self.redo_button = QPushButton("やり直す"); self.redo_button.clicked.connect(self.redo); self.clear_button = QPushButton("全消去"); self.clear_button.clicked.connect(self.clear)
        vl.addWidget(self.undo_button); vl.addWidget(self.redo_button); vl.addWidget(self.clear_button); left_layout.addWidget(view_group); left_layout.addStretch(1); left.setWidget(left_widget)
        center = QWidget(); center_layout = QVBoxLayout(center); self.coord_label = QLabel("- "); center_layout.addWidget(self.coord_label); center_layout.addWidget(self.canvas_view, 1)
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
        right = QWidget(); rl = QVBoxLayout(right); rl.addWidget(QLabel("実寸プレビュー")); self.preview_size_label = QLabel(); rl.addWidget(self.preview_size_label)
        self.preview_hint_label = QLabel("透明なキャンバスです。左のツールで描けます。")
        self.preview_hint_label.setWordWrap(True)
        rl.addWidget(self.preview_hint_label)
        rl.addWidget(self.preview_scroll, 1)
        self.reference_check = QCheckBox("下絵を表示"); self.reference_check.setChecked(True); self.reference_check.toggled.connect(self._reference_visibility); rl.addWidget(self.reference_check)
        self.opacity_slider = QSlider(Qt.Horizontal); self.opacity_slider.setRange(10, 100); self.opacity_slider.setValue(50); self.opacity_slider.valueChanged.connect(self._reference_opacity); rl.addWidget(QLabel("下絵の不透明度")); rl.addWidget(self.opacity_slider)
        self.save_button = QPushButton("PNGで保存"); self.save_button.clicked.connect(self.save); rl.addWidget(self.save_button); self.saved_label = QLabel(); self.saved_label.setWordWrap(True); rl.addWidget(self.saved_label)
        splitter.addWidget(left); splitter.addWidget(center); splitter.addWidget(right); splitter.setStretchFactor(1, 1); splitter.setSizes([250, 650, 250])
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(splitter)
        self.canvas_view.changed.connect(self._refresh); self.canvas_view.coordinates_changed.connect(self.coord_label.setText); self.canvas_view.drop_requested.connect(self._handle_dropped_path); self.size_combo.currentTextChanged.connect(lambda x: setattr(self.canvas_view, "pencil_size", int(x)))
        self._refresh()

    def _set_tool(self, tool):
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
        if path.suffix.lower() not in SUPPORTED_DROP_SUFFIXES or not path.is_file():
            return
        choice = self.import_choice_provider(path)
        if choice == "reference":
            self._load_reference_path(path)
        elif choice == "pixels":
            self._load_pixels_path(path)

    def _preset_changed(self, index):
        if index < 3: self.width_spin.setValue((32, 64, 128)[index]); self.height_spin.setValue((32, 64, 128)[index])
    def _zoom_changed(self, index):
        if index == 0: self.canvas_view.set_zoom(max(1, min(self.canvas_view.viewport().width() // self.canvas.width, self.canvas_view.viewport().height() // self.canvas.height)))
        else: self.canvas_view.set_zoom((2, 4, 8, 16)[index - 1])
    def new_canvas(self):
        self.canvas = PixelCanvas(self.width_spin.value(), self.height_spin.value()); self.canvas_view.set_canvas(self.canvas); self.reference = None; self.canvas_view.reference = None; self.source_path = None; self._refresh()
    def clear(self): self.canvas.clear(); self._refresh()
    def undo(self): self.canvas.undo(); self._refresh()
    def redo(self): self.canvas.redo(); self._refresh()
    def _load_reference_path(self, path: Path):
        self.reference = load_reference(path, (self.canvas.width, self.canvas.height), self.opacity_slider.value())
        self.canvas_view.reference = self.reference
        self.source_path = path
        self._refresh()

    def load_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "下絵を読み込む", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._load_reference_path(Path(path))
    def _load_pixels_path(self, path: Path):
        import_as_pixels(path, self.canvas)
        self.source_path = path
        self.reference = None
        self.canvas_view.reference = None
        self._refresh()

    def load_pixels(self):
        path, _ = QFileDialog.getOpenFileName(self, "ドット化して編集", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._load_pixels_path(Path(path))
    def _reference_visibility(self, value): self.canvas_view.reference_visible = value; self.canvas_view.viewport().update(); self._refresh()
    def _reference_opacity(self, value):
        if self.reference is not None: self.reference = ReferenceImage(self.reference.image, value); self.canvas_view.reference = self.reference; self.canvas_view.viewport().update(); self._refresh()
    def save(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先を選ぶ")
        if not folder: return
        try: result = save_png(self.canvas, folder, self.source_path); self.saved_label.setText(str(result.output_path))
        except PixelExportError as exc: QMessageBox.warning(self, "保存エラー", str(exc))
    def _refresh(self):
        self.preview_size_label.setText(f"{self.canvas.width} × {self.canvas.height}")
        image = _qimage(self.canvas.image)
        self.preview_label.setPixmap(QPixmap.fromImage(image))
        self.preview_label.setFixedSize(image.size())
        self.canvas_view.viewport().update()
    def resizeEvent(self, event): self._refresh(); super().resizeEvent(event)
    def can_close(self): return True
    def cleanup(self): return None
