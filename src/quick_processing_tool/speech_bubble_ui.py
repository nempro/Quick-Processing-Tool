from __future__ import annotations

import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsEllipseItem,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from .color_picker import choose_color
from .edit_ui import ElidedPathLabel, FontPickerButton, IMEPlainTextEdit
from .editing.text import pil_to_qimage
from .font_catalog import FontCatalog
from .naming import normalize_filename_stem
from .speech_bubble import (
    BubbleGeometry, BubbleShape, SpeechBubbleSettings, TailPreset,
    render_speech_bubble, save_speech_bubble,
)
from .ui_styles import INPUT_CONTROL_STYLE


BUBBLE_STYLE = INPUT_CONTROL_STYLE + """
QPlainTextEdit { background: #dce5ef; color: #182230; border: 1px solid #6f8094;
    border-radius: 6px; padding: 7px; selection-background-color: #315fbd; }
QPlainTextEdit:hover { background: #cfdeec; border-color: #405b79; }
QPlainTextEdit:focus { background: white; border: 2px solid #2457b2; padding: 6px; }
QPlainTextEdit:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton { min-height: 28px; background: #f5f7fa; color: #182230;
    border: 1px solid #7b899a; border-radius: 6px; padding: 4px 8px; }
QPushButton:hover { background: #e5edf6; border-color: #405b79; }
QPushButton:focus { background: white; border: 2px solid #2457b2; padding: 3px 7px; }
QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton#bubbleSave { min-height: 44px; background: #315fbd; color: white;
    border-color: #315fbd; border-radius: 8px; font-size: 14px; font-weight: 700; }
QPushButton#bubbleSave:hover { background: #284fa1; }
QPushButton#bubbleSave:focus { border: 2px solid #173a82; padding: 3px 7px; }
QPushButton#bubbleSave:disabled { background: #d9dee6; color: #8f98a6; border-color: #d9dee6; }
QGroupBox { font-weight: 700; margin-top: 8px; padding-top: 8px; }
"""


SHAPE_LABELS = {BubbleShape.ELLIPSE: "楕円", BubbleShape.ROUNDED: "角丸", BubbleShape.BURST: "ギザギザ"}
TAIL_LABELS = {TailPreset.LEFT_TOP: "左上", TailPreset.RIGHT_TOP: "右上", TailPreset.LEFT_BOTTOM: "左下", TailPreset.RIGHT_BOTTOM: "右下"}


def _desktop_folder() -> Path:
    value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(value) if value else Path.home() / "Desktop"


class BubblePreview(QGraphicsView):
    tip_dragged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self._handle = QGraphicsEllipseItem(-7, -7, 14, 14)
        self._handle.setPen(QPen(QColor("#ffffff"), 2))
        self._handle.setBrush(QBrush(QColor("#315fbd")))
        self._handle.setZValue(5)
        self._handle.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._handle.hide()
        self.scene().addItem(self._handle)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setBackgroundBrush(self._checkerboard())
        self._zoom = "Fit"
        self._dragging = False
        self._body_center_scene: QPointF | None = None

    @staticmethod
    def _checkerboard() -> QBrush:
        tile = QPixmap(24, 24)
        tile.fill(QColor("#f5f5f5"))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 12, 12, QColor("#d8dde3"))
        painter.fillRect(12, 12, 12, 12, QColor("#d8dde3"))
        painter.end()
        return QBrush(tile)

    def set_result(
        self,
        image: Image.Image | None,
        tip: tuple[float, float] | None,
        *,
        body_center: tuple[float, float] | None = None,
        preserve_view: bool = False,
    ) -> None:
        previous_scene_rect = self.scene().sceneRect()
        previous_body_center = self._body_center_scene
        pixmap = QPixmap() if image is None else QPixmap.fromImage(pil_to_qimage(image))
        self._item.setPixmap(pixmap)
        if preserve_view and previous_body_center is not None and body_center is not None and not pixmap.isNull():
            self._item.setPos(previous_body_center - QPointF(*body_center))
            self.scene().setSceneRect(previous_scene_rect)
        else:
            self._item.setPos(QPointF())
            self.scene().setSceneRect(self._item.sceneBoundingRect() if not pixmap.isNull() else self.rect())
        self._body_center_scene = (
            self._item.mapToScene(QPointF(*body_center))
            if body_center is not None and not pixmap.isNull()
            else None
        )
        if tip is None or pixmap.isNull():
            self._handle.hide()
        else:
            self._handle.setPos(self._item.mapToScene(QPointF(*tip)))
            self._handle.show()
        if not preserve_view:
            self.apply_zoom(self._zoom)

    def apply_zoom(self, mode: str) -> None:
        self._zoom = mode
        self.resetTransform()
        if self._item.pixmap().isNull():
            return
        if mode == "Fit":
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            factor = 1.0 if mode == "100%" else 2.0
            self.scale(factor, factor)

    def viewport_to_image(self, point: QPointF) -> QPointF:
        scene_point = self.mapToScene(point.toPoint())
        return self._item.mapFromScene(scene_point)

    def image_to_viewport(self, point: QPointF) -> QPointF:
        return QPointF(self.mapFromScene(self._item.mapToScene(point)))

    def _over_handle(self, position: QPointF) -> bool:
        if not self._handle.isVisible():
            return False
        handle = self.image_to_viewport(self._handle.pos())
        return math.hypot(position.x() - handle.x(), position.y() - handle.y()) <= 12.0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._over_handle(event.position()):
            self._dragging = True
            self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            point = self.viewport_to_image(event.position())
            self._handle.setPos(point)
            self.tip_dragged.emit(point)
            event.accept()
            return
        self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor if self._over_handle(event.position()) else Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.tip_dragged.emit(self.viewport_to_image(event.position()))
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._zoom == "Fit":
            self.apply_zoom("Fit")


class SpeechBubblePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("speech_bubble_page")
        self.setStyleSheet(BUBBLE_STYLE)
        self.font_catalog = FontCatalog()
        self.output_folder = _desktop_folder()
        self._text_color = QColor(0, 0, 0, 255)
        self._fill_color = QColor(255, 255, 255, 255)
        self._stroke_color = QColor(0, 0, 0, 255)
        self._tail_tip: tuple[float, float] | None = None
        self.preview_result = None
        self.last_saved_path: Path | None = None
        self._auto_filename = "speech_bubble"
        self._resetting = False
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update_preview)
        self._connect()
        self.reset_settings()

    @staticmethod
    def _spin(minimum: int, maximum: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        return spin

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("speech_bubble_workspace")
        root.addWidget(splitter)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setMinimumWidth(210)
        self.settings_scroll.setMaximumWidth(340)
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left = QVBoxLayout(host)
        left.setContentsMargins(8, 4, 8, 8)
        left.addWidget(self._text_group())
        left.addWidget(self._bubble_group())
        left.addWidget(self._tail_group())
        reset_actions = QWidget()
        reset_layout = QHBoxLayout(reset_actions)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        self.reset_button = QPushButton("設定をリセット")
        self.clear_all_button = QPushButton("すべてクリア")
        for button in (self.reset_button, self.clear_all_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            reset_layout.addWidget(button)
        left.addWidget(reset_actions)
        left.addStretch(1)
        self.settings_scroll.setWidget(host)
        splitter.addWidget(self.settings_scroll)

        center = QWidget()
        center.setMinimumWidth(220)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 4, 8, 8)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("プレビュー · しっぽの青い●をドラッグ"))
        toolbar.addStretch(1)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItem("全体表示", "Fit")
        self.zoom_combo.addItem("100%", "100%")
        self.zoom_combo.addItem("200%", "200%")
        toolbar.addWidget(self.zoom_combo)
        center_layout.addLayout(toolbar)
        self.preview = BubblePreview()
        self.preview.setMinimumHeight(320)
        center_layout.addWidget(self.preview, 1)
        self.preview_status = QLabel("セリフを入力するとプレビューされます")
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.preview_status)
        splitter.addWidget(center)

        save_panel = QFrame()
        save_panel.setMinimumWidth(180)
        save_panel.setMaximumWidth(270)
        save = QVBoxLayout(save_panel)
        save.addWidget(QLabel("保存"))
        save.addWidget(QLabel("ファイル名（PNG）"))
        self.filename_edit = QLineEdit()
        save.addWidget(self.filename_edit)
        self.folder_button = QPushButton("保存先を選ぶ…")
        save.addWidget(self.folder_button)
        self.folder_label = ElidedPathLabel()
        save.addWidget(self.folder_label)
        self.output_info = QLabel("透明背景（RGBA）\n—")
        self.output_info.setWordWrap(True)
        save.addWidget(self.output_info)
        save.addStretch(1)
        self.save_button = QPushButton("透明PNGで保存")
        self.save_button.setObjectName("bubbleSave")
        save.addWidget(self.save_button)
        self.save_result = QLabel()
        self.save_result.setWordWrap(True)
        save.addWidget(self.save_result)
        self.open_image_button = QPushButton("画像を開く")
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_image_button.hide()
        self.open_folder_button.hide()
        save.addWidget(self.open_image_button)
        save.addWidget(self.open_folder_button)
        splitter.addWidget(save_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 600, 240])

    def _text_group(self) -> QGroupBox:
        group = QGroupBox("文字")
        form = QFormLayout(group)
        self.text_edit = IMEPlainTextEdit()
        self.text_edit.setPlaceholderText("えっ！？\nそれ本当に\n言ってる？")
        self.text_edit.setMaximumHeight(90)
        self.font_button = FontPickerButton(self.font_catalog)
        self.font_size_spin = self._spin(8, 256, " px")
        self.text_color_button = QPushButton()
        form.addRow("セリフ", self.text_edit)
        form.addRow("フォント", self.font_button)
        form.addRow("サイズ", self.font_size_spin)
        form.addRow("文字色", self.text_color_button)
        return group

    def _bubble_group(self) -> QGroupBox:
        group = QGroupBox("吹き出し")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        self.bubble_enabled = QCheckBox("吹き出しを描く")
        layout.addWidget(self.bubble_enabled)
        self.bubble_details = QWidget()
        form = QFormLayout(self.bubble_details)
        form.setContentsMargins(0, 0, 0, 0)
        self.shape_combo = QComboBox()
        for shape, label in SHAPE_LABELS.items():
            self.shape_combo.addItem(label, shape)
        self.fill_color_button = QPushButton()
        self.fill_opacity_spin = self._spin(0, 100, "%")
        self.fill_opacity_spin.setAccessibleName("吹き出し塗りの不透明度")
        self.stroke_color_button = QPushButton()
        self.stroke_width_spin = self._spin(1, 20, " px")
        self.padding_spin = self._spin(4, 100, " px")
        form.addRow("形", self.shape_combo)
        form.addRow("塗り", self.fill_color_button)
        form.addRow("不透明度", self.fill_opacity_spin)
        form.addRow("枠線", self.stroke_color_button)
        form.addRow("太さ", self.stroke_width_spin)
        form.addRow("内側余白", self.padding_spin)
        layout.addWidget(self.bubble_details)
        return group

    def _tail_group(self) -> QGroupBox:
        self.tail_group = QGroupBox("しっぽ")
        form = QFormLayout(self.tail_group)
        self.tail_enabled = QCheckBox("しっぽを付ける")
        self.tail_preset_combo = QComboBox()
        for preset, label in TAIL_LABELS.items():
            self.tail_preset_combo.addItem(label, preset)
        form.addRow(self.tail_enabled)
        form.addRow("方向", self.tail_preset_combo)
        self.tail_preset_label = form.labelForField(self.tail_preset_combo)
        self.tail_note = QLabel("プレビューの青い●をドラッグして先端を調整できます。")
        self.tail_note.setWordWrap(True)
        form.addRow(self.tail_note)
        return self.tail_group

    def _connect(self) -> None:
        self.text_edit.textChanged.connect(self._text_changed)
        self.font_button.family_changed.connect(self._schedule)
        self.font_size_spin.valueChanged.connect(self._schedule)
        self.bubble_enabled.toggled.connect(self._bubble_enabled_changed)
        self.shape_combo.currentIndexChanged.connect(self._schedule)
        for spin in (self.stroke_width_spin, self.padding_spin):
            spin.valueChanged.connect(self._schedule)
        self.fill_opacity_spin.valueChanged.connect(self._fill_opacity_changed)
        self.tail_enabled.toggled.connect(self._tail_enabled_changed)
        self.tail_preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.text_color_button.clicked.connect(lambda: self._choose_color("文字色", "_text_color"))
        self.fill_color_button.clicked.connect(lambda: self._choose_color("塗り色", "_fill_color"))
        self.stroke_color_button.clicked.connect(lambda: self._choose_color("枠線色", "_stroke_color"))
        self.zoom_combo.currentIndexChanged.connect(
            lambda _index: self.preview.apply_zoom(self.zoom_combo.currentData())
        )
        self.preview.tip_dragged.connect(self._tail_dragged)
        self.reset_button.clicked.connect(self.reset_settings)
        self.clear_all_button.clicked.connect(self.clear_all)
        self.folder_button.clicked.connect(self.choose_output_folder)
        self.filename_edit.textChanged.connect(self._update_save_state)
        self.save_button.clicked.connect(self.save_png)
        self.open_image_button.clicked.connect(self.open_saved_image)
        self.open_folder_button.clicked.connect(self.open_saved_folder)

    @staticmethod
    def _rgba(color: QColor) -> tuple[int, int, int, int]:
        return color.red(), color.green(), color.blue(), color.alpha()

    def settings(self) -> SpeechBubbleSettings:
        return SpeechBubbleSettings(
            text=self.text_edit.toPlainText(), font_family=self.font_button.family(), font_size=self.font_size_spin.value(),
            text_color=self._rgba(self._text_color), bubble_enabled=self.bubble_enabled.isChecked(),
            shape=BubbleShape(self.shape_combo.currentData()), fill_color=self._rgba(self._fill_color),
            stroke_color=self._rgba(self._stroke_color), stroke_width=self.stroke_width_spin.value(), padding=self.padding_spin.value(),
            tail_enabled=self.tail_enabled.isChecked(), tail_preset=TailPreset(self.tail_preset_combo.currentData()), tail_tip=self._tail_tip,
        )

    def _schedule(self, *_args) -> None:
        if not self._resetting:
            self._timer.start()

    def _text_changed(self) -> None:
        new = self._default_filename(self.text_edit.toPlainText())
        if not self.filename_edit.text().strip() or self.filename_edit.text() == self._auto_filename:
            self.filename_edit.setText(new)
        self._auto_filename = new
        self._schedule()

    @staticmethod
    def _default_filename(text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return f"{normalize_filename_stem(first, default='speech_bubble', max_length=48)}_bubble" if first else "speech_bubble"

    def update_preview(self) -> None:
        try:
            result = render_speech_bubble(self.settings())
        except Exception as exc:
            self.preview_result = None
            self.preview.set_result(None, None)
            self.preview_status.setText(f"プレビューを作成できませんでした: {exc}")
        else:
            self.preview_result = result
            if result is None:
                self.preview.set_result(None, None)
                self.preview_status.setText("セリフを入力するとプレビューされます")
                self.output_info.setText("透明背景（RGBA）\n—")
            else:
                self.preview.set_result(
                    result.image,
                    result.geometry.tail_tip,
                    body_center=result.geometry.body_center,
                )
                if result.geometry.tail_tip is not None:
                    self.preview_status.setText("青い●は操作用です。PNGには含まれません。")
                elif self.bubble_enabled.isChecked():
                    self.preview_status.setText("しっぽなしの吹き出しを表示しています。")
                else:
                    self.preview_status.setText("文字だけを透明キャンバスへ表示しています。")
                self.output_info.setText(f"透明背景（RGBA）\n{result.image.width} × {result.image.height} px")
        self._update_save_state()

    def _tail_dragged(self, point: QPointF) -> None:
        if self.preview_result is None or not self.bubble_enabled.isChecked() or not self.tail_enabled.isChecked():
            return
        geometry: BubbleGeometry = self.preview_result.geometry
        _x, _y, width, height = geometry.body_rect
        cx, cy = geometry.body_center
        nx = max(-3.0, min(3.0, (point.x() - cx) * 2 / max(1.0, width)))
        ny = max(-3.0, min(3.0, (point.y() - cy) * 2 / max(1.0, height)))
        distance = math.hypot(nx, ny)
        if distance < 1.05:
            if distance < 0.001:
                nx, ny = (0.72, 1.25)
            else:
                nx, ny = nx * 1.05 / distance, ny * 1.05 / distance
        self._tail_tip = (nx, ny)
        if self.preview._dragging:
            try:
                result = render_speech_bubble(self.settings())
            except Exception:
                return
            if result is not None:
                self.preview_result = result
                self.preview.set_result(
                    result.image,
                    result.geometry.tail_tip,
                    body_center=result.geometry.body_center,
                    preserve_view=True,
                )
                self.output_info.setText(f"透明背景（RGBA）\n{result.image.width} × {result.image.height} px")
                self._update_save_state()
        else:
            self.update_preview()

    def _preset_changed(self, *_args) -> None:
        if not self._resetting:
            self._tail_tip = None
            self._schedule()

    def _tail_enabled_changed(self, enabled: bool) -> None:
        self.tail_preset_combo.setVisible(enabled)
        if self.tail_preset_label is not None:
            self.tail_preset_label.setVisible(enabled)
        self.tail_note.setVisible(enabled)
        self._schedule()

    def _bubble_enabled_changed(self, enabled: bool) -> None:
        self.bubble_details.setVisible(enabled)
        self.tail_group.setVisible(enabled)
        self._schedule()

    def _fill_opacity_changed(self, percent: int) -> None:
        self._fill_color.setAlpha(round(percent * 255 / 100))
        self._update_color_buttons()
        self._schedule()

    def _choose_color(self, title: str, attribute: str) -> None:
        color = choose_color(getattr(self, attribute), self, title, show_alpha=True)
        if color.isValid():
            setattr(self, attribute, color)
            if attribute == "_fill_color":
                blocked = self.fill_opacity_spin.blockSignals(True)
                self.fill_opacity_spin.setValue(round(color.alpha() * 100 / 255))
                self.fill_opacity_spin.blockSignals(blocked)
            self._update_color_buttons()
            self._schedule()

    def _update_color_buttons(self) -> None:
        for button, color in ((self.text_color_button, self._text_color), (self.fill_color_button, self._fill_color), (self.stroke_color_button, self._stroke_color)):
            alpha = "" if button is self.fill_color_button else f"  A{color.alpha()}"
            button.setText(f"#{color.red():02X}{color.green():02X}{color.blue():02X}{alpha}")
            button.setStyleSheet(f"background: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()});")

    def reset_settings(self) -> None:
        text = self.text_edit.toPlainText()
        self._resetting = True
        defaults = SpeechBubbleSettings(font_family=self.font_catalog.default_family())
        self.font_button.set_family(defaults.font_family)
        self.font_size_spin.setValue(defaults.font_size)
        self._text_color = QColor(*defaults.text_color)
        self.bubble_enabled.setChecked(True)
        self.shape_combo.setCurrentIndex(self.shape_combo.findData(defaults.shape.value))
        self._fill_color = QColor(*defaults.fill_color)
        self.fill_opacity_spin.setValue(100)
        self._stroke_color = QColor(*defaults.stroke_color)
        self.stroke_width_spin.setValue(defaults.stroke_width)
        self.padding_spin.setValue(defaults.padding)
        self.tail_enabled.setChecked(True)
        self.tail_preset_combo.setCurrentIndex(self.tail_preset_combo.findData(TailPreset.RIGHT_BOTTOM.value))
        self._tail_tip = None
        self.text_edit.setPlainText(text)
        self._resetting = False
        self.bubble_details.show()
        self.tail_group.show()
        self.tail_preset_combo.show()
        if self.tail_preset_label is not None:
            self.tail_preset_label.show()
        self.tail_note.show()
        self._update_color_buttons()
        self.update_preview()

    def clear_all(self) -> None:
        """Return this material editor to a fresh state without touching saved files."""
        self.reset_settings()
        self.text_edit.clear()
        self._auto_filename = self._default_filename("")
        self.filename_edit.setText(self._auto_filename)
        self._timer.stop()
        self.update_preview()
        self._clear_saved_result()

    def _clear_saved_result(self) -> None:
        self.last_saved_path = None
        self.save_result.clear()
        self.save_result.setToolTip("")
        self.open_image_button.hide()
        self.open_folder_button.hide()

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "透明PNGの保存先を選ぶ", str(self.output_folder))
        if folder:
            self.output_folder = Path(folder)
            self._update_save_state()

    def _update_save_state(self, *_args) -> None:
        self.folder_label.set_path(self.output_folder)
        stem = normalize_filename_stem(self.filename_edit.text(), strip_extensions=(".png",))
        self.save_button.setEnabled(self.preview_result is not None and bool(stem) and self.output_folder.is_dir())

    def save_png(self) -> None:
        self._timer.stop()
        self.update_preview()
        if self.preview_result is None:
            return
        stem = normalize_filename_stem(self.filename_edit.text(), strip_extensions=(".png",))
        if not stem or not self.output_folder.is_dir():
            return
        try:
            path = save_speech_bubble(self.preview_result.image, self.output_folder, stem)
        except OSError as exc:
            QMessageBox.warning(self, "保存できませんでした", str(exc))
            return
        self.last_saved_path = path
        self.save_result.setText(f"✓ 保存しました\n{path.name}")
        self.save_result.setToolTip(str(path))
        self.open_image_button.show()
        self.open_folder_button.show()

    def open_saved_image(self) -> None:
        path = self.last_saved_path
        if path is None or not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "画像を開けません", "PNGを保存してから、もう一度お試しください。")

    def open_saved_folder(self) -> None:
        folder = self.last_saved_path.parent if self.last_saved_path else None
        if folder is None or not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "PNGを保存してから、もう一度お試しください。")

    def can_close(self) -> bool:
        return True
