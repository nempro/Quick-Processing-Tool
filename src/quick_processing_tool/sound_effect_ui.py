from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QStandardPaths, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from .color_picker import choose_color
from .edit_ui import ElidedPathLabel, FontPickerButton, IMEPlainTextEdit
from .editing.text import pil_to_qimage
from .font_catalog import FontCatalog
from .naming import normalize_filename_stem
from .sound_effect import SoundEffectSettings, TextDirection, render_sound_effect, save_sound_effect
from .ui_styles import INPUT_CONTROL_STYLE


SOUND_EFFECT_STYLE = INPUT_CONTROL_STYLE + """
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
QPushButton:checked { background: #315fbd; color: white; border-color: #244b99; font-weight: 700; }
QPushButton#soundSave { min-height: 44px; background: #315fbd; color: white;
    border-color: #315fbd; border-radius: 8px; font-size: 14px; font-weight: 700; }
QPushButton#soundSave:hover { background: #284fa1; }
QPushButton#soundSave:focus { border: 2px solid #173a82; padding: 3px 7px; }
QPushButton#soundSave:disabled { background: #d9dee6; color: #8f98a6; border-color: #d9dee6; }
QGroupBox { font-weight: 700; margin-top: 8px; padding-top: 8px; }
"""


def _desktop_folder() -> Path:
    value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(value) if value else Path.home() / "Desktop"


class SoundRenderSignals(QObject):
    finished = Signal(int, object, object, str)


class SoundRenderWorker(QRunnable):
    def __init__(self, generation: int, settings: SoundEffectSettings) -> None:
        super().__init__()
        self.generation = generation
        self.settings = settings
        self.signals = SoundRenderSignals()

    @Slot()
    def run(self) -> None:
        try:
            image = render_sound_effect(self.settings)
        except Exception as exc:
            self.signals.finished.emit(self.generation, self.settings, None, str(exc))
        else:
            self.signals.finished.emit(self.generation, self.settings, image, "")


class SoundPreview(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(self._checkerboard())
        self._zoom = "Fit"

    @staticmethod
    def _checkerboard() -> QBrush:
        tile = QPixmap(24, 24)
        tile.fill(QColor("#f5f5f5"))
        painter = QPainter(tile)
        try:
            painter.fillRect(0, 0, 12, 12, QColor("#d8dde3"))
            painter.fillRect(12, 12, 12, 12, QColor("#d8dde3"))
        finally:
            painter.end()
        return QBrush(tile)

    def set_image(self, image: Image.Image | None) -> None:
        pixmap = QPixmap() if image is None else QPixmap.fromImage(pil_to_qimage(image))
        self._item.setPixmap(pixmap)
        self.scene().setSceneRect(self._item.boundingRect() if not pixmap.isNull() else self.rect())
        self.apply_zoom(self._zoom)

    def apply_zoom(self, mode: str) -> None:
        self._zoom = mode
        self.resetTransform()
        if self._item.pixmap().isNull():
            return
        if mode == "Fit":
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.scale(1.0 if mode == "100%" else 2.0, 1.0 if mode == "100%" else 2.0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._zoom == "Fit":
            self.apply_zoom("Fit")


class SoundEffectPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sound_effect_page")
        self.setStyleSheet(SOUND_EFFECT_STYLE)
        self.font_catalog = FontCatalog()
        self.output_folder = _desktop_folder()
        self._text_color = QColor(0, 0, 0, 255)
        self._outline_color = QColor(255, 255, 255, 255)
        self._shadow_color = QColor(0, 0, 0, 160)
        self.preview_image: Image.Image | None = None
        self.last_saved_path: Path | None = None
        self._auto_filename = "sound_effect"
        self._resetting = False
        self._render_generation = 0
        self._render_active = False
        self._pending_render: tuple[int, SoundEffectSettings] | None = None
        self._render_worker: SoundRenderWorker | None = None
        self._preview_current = False
        self._build_ui()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._connect_controls()
        self.reset_settings()

    @staticmethod
    def _spin(minimum: int, maximum: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        spin.setMinimumWidth(0)
        return spin

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("sound_effect_workspace")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(splitter)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setMinimumWidth(210)
        self.settings_scroll.setMaximumWidth(340)
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        settings_layout = QVBoxLayout(host)
        settings_layout.setContentsMargins(8, 4, 8, 8)
        settings_layout.addWidget(self._text_group())
        settings_layout.addWidget(self._appearance_group())
        settings_layout.addWidget(self._transform_group())
        reset_actions = QWidget()
        reset_layout = QHBoxLayout(reset_actions)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        self.reset_button = QPushButton("設定をリセット")
        self.clear_all_button = QPushButton("すべてクリア")
        for button in (self.reset_button, self.clear_all_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            reset_layout.addWidget(button)
        settings_layout.addWidget(reset_actions)
        settings_layout.addStretch(1)
        self.settings_scroll.setWidget(host)
        splitter.addWidget(self.settings_scroll)

        center = QWidget()
        center.setMinimumWidth(220)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 4, 8, 8)
        toolbar = QHBoxLayout()
        title = QLabel("プレビュー · 透明PNG")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #182230;")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("表示倍率"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItem("全体表示", "Fit")
        self.zoom_combo.addItem("100%", "100%")
        self.zoom_combo.addItem("200%", "200%")
        toolbar.addWidget(self.zoom_combo)
        center_layout.addLayout(toolbar)
        self.preview = SoundPreview()
        self.preview.setMinimumHeight(320)
        center_layout.addWidget(self.preview, 1)
        self.preview_status = QLabel("文字を入力するとプレビューされます")
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_status.setWordWrap(True)
        self.preview_status.setStyleSheet("color: #667085; padding: 4px;")
        center_layout.addWidget(self.preview_status)
        splitter.addWidget(center)

        save_panel = QFrame()
        save_panel.setObjectName("sound_save_panel")
        save_panel.setMinimumWidth(180)
        save_panel.setMaximumWidth(270)
        save_layout = QVBoxLayout(save_panel)
        save_layout.setContentsMargins(10, 4, 10, 8)
        heading = QLabel("保存")
        heading.setStyleSheet("font-size: 15px; font-weight: 700; color: #182230;")
        save_layout.addWidget(heading)
        save_layout.addWidget(QLabel("ファイル名（PNG）"))
        self.filename_edit = QLineEdit()
        self.filename_edit.setClearButtonEnabled(True)
        save_layout.addWidget(self.filename_edit)
        self.folder_button = QPushButton("保存先を選ぶ…")
        save_layout.addWidget(self.folder_button)
        self.folder_label = ElidedPathLabel()
        self.folder_label.setStyleSheet("color: #667085;")
        save_layout.addWidget(self.folder_label)
        self.output_info = QLabel("透明背景（RGBA）\n—")
        self.output_info.setWordWrap(True)
        self.output_info.setStyleSheet("padding: 9px; background: #eef2f7; border-radius: 7px;")
        save_layout.addWidget(self.output_info)
        save_layout.addStretch(1)
        self.save_button = QPushButton("透明PNGで保存")
        self.save_button.setObjectName("soundSave")
        save_layout.addWidget(self.save_button)
        self.save_result = QLabel()
        self.save_result.setWordWrap(True)
        save_layout.addWidget(self.save_result)
        self.open_image_button = QPushButton("画像を開く")
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_image_button.hide()
        self.open_folder_button.hide()
        save_layout.addWidget(self.open_image_button)
        save_layout.addWidget(self.open_folder_button)
        splitter.addWidget(save_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 600, 240])

    def _text_group(self) -> QGroupBox:
        group = QGroupBox("文字")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.text_edit = IMEPlainTextEdit()
        self.text_edit.setPlaceholderText("ドン！\nざわ…\nぎゅっ")
        self.text_edit.setMaximumHeight(88)
        form.addRow("文字", self.text_edit)
        self.font_button = FontPickerButton(self.font_catalog)
        form.addRow("フォント", self.font_button)
        self.font_size_spin = self._spin(8, 512, " px")
        form.addRow("サイズ", self.font_size_spin)
        return group

    def _appearance_group(self) -> QGroupBox:
        group = QGroupBox("見た目")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.text_color_button = QPushButton()
        form.addRow("文字色", self.text_color_button)
        self.outline_enabled = QCheckBox("縁取り")
        form.addRow(self.outline_enabled)
        self.outline_color_button = QPushButton()
        form.addRow("縁色", self.outline_color_button)
        self.outline_width_spin = self._spin(0, 40, " px")
        form.addRow("太さ", self.outline_width_spin)
        self.shadow_enabled = QCheckBox("影")
        form.addRow(self.shadow_enabled)
        self.shadow_color_button = QPushButton()
        form.addRow("影色", self.shadow_color_button)
        self.shadow_distance_spin = self._spin(0, 100, " px")
        form.addRow("影の距離", self.shadow_distance_spin)
        self.shadow_blur_spin = self._spin(0, 50, " px")
        form.addRow("ぼかし", self.shadow_blur_spin)
        return group

    def _transform_group(self) -> QGroupBox:
        group = QGroupBox("変形")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        direction = QWidget()
        direction_layout = QHBoxLayout(direction)
        direction_layout.setContentsMargins(0, 0, 0, 0)
        self.horizontal_button = QPushButton("横書き")
        self.vertical_button = QPushButton("縦書き")
        self.direction_group = QButtonGroup(self)
        self.direction_group.setExclusive(True)
        for button in (self.horizontal_button, self.vertical_button):
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.direction_group.addButton(button)
            direction_layout.addWidget(button)
        form.addRow("方向", direction)
        self.letter_spacing_spin = self._spin(-20, 100, " px")
        self.line_spacing_spin = self._spin(0, 100, " px")
        self.rotation_spin = self._spin(-180, 180, "°")
        self.scale_x_spin = self._spin(50, 200, "%")
        self.scale_y_spin = self._spin(50, 200, "%")
        self.padding_spin = self._spin(0, 100, " px")
        for label, control in (("文字間隔", self.letter_spacing_spin), ("行／列間", self.line_spacing_spin), ("回転", self.rotation_spin), ("横幅", self.scale_x_spin), ("高さ", self.scale_y_spin), ("余白", self.padding_spin)):
            form.addRow(label, control)
        return group

    def _connect_controls(self) -> None:
        self.text_edit.textChanged.connect(self._text_changed)
        self.font_button.family_changed.connect(self._schedule_preview)
        for spin in (self.font_size_spin, self.outline_width_spin, self.shadow_distance_spin, self.shadow_blur_spin, self.letter_spacing_spin, self.line_spacing_spin, self.rotation_spin, self.scale_x_spin, self.scale_y_spin, self.padding_spin):
            spin.valueChanged.connect(self._schedule_preview)
        for control in (self.outline_enabled, self.shadow_enabled, self.horizontal_button, self.vertical_button):
            control.toggled.connect(self._schedule_preview)
        self.outline_enabled.toggled.connect(self._update_enabled_controls)
        self.shadow_enabled.toggled.connect(self._update_enabled_controls)
        self.text_color_button.clicked.connect(lambda: self._choose_color("文字色", "_text_color"))
        self.outline_color_button.clicked.connect(lambda: self._choose_color("縁取り色", "_outline_color"))
        self.shadow_color_button.clicked.connect(lambda: self._choose_color("影色", "_shadow_color"))
        self.zoom_combo.currentIndexChanged.connect(
            lambda _index: self.preview.apply_zoom(self.zoom_combo.currentData())
        )
        self.reset_button.clicked.connect(self.reset_settings)
        self.clear_all_button.clicked.connect(self.clear_all)
        self.folder_button.clicked.connect(self.choose_output_folder)
        self.filename_edit.textChanged.connect(self._update_save_state)
        self.save_button.clicked.connect(self.save_png)
        self.open_image_button.clicked.connect(self.open_saved_image)
        self.open_folder_button.clicked.connect(self.open_saved_folder)
        self._preview_timer.timeout.connect(self._request_preview)

    @staticmethod
    def _rgba(color: QColor) -> tuple[int, int, int, int]:
        return color.red(), color.green(), color.blue(), color.alpha()

    def settings(self) -> SoundEffectSettings:
        return SoundEffectSettings(
            text=self.text_edit.toPlainText(), font_family=self.font_button.family(), font_size=self.font_size_spin.value(), color=self._rgba(self._text_color),
            outline_enabled=self.outline_enabled.isChecked(), outline_color=self._rgba(self._outline_color), outline_width=self.outline_width_spin.value(),
            shadow_enabled=self.shadow_enabled.isChecked(), shadow_color=self._rgba(self._shadow_color), shadow_distance=self.shadow_distance_spin.value(), shadow_blur=self.shadow_blur_spin.value(),
            direction=TextDirection.VERTICAL if self.vertical_button.isChecked() else TextDirection.HORIZONTAL,
            letter_spacing=self.letter_spacing_spin.value(), line_spacing=self.line_spacing_spin.value(), rotation=self.rotation_spin.value(),
            scale_x=self.scale_x_spin.value(), scale_y=self.scale_y_spin.value(), padding=self.padding_spin.value(),
        )

    def _text_changed(self) -> None:
        new_default = self._default_filename(self.text_edit.toPlainText())
        if not self.filename_edit.text().strip() or self.filename_edit.text() == self._auto_filename:
            self.filename_edit.setText(new_default)
        self._auto_filename = new_default
        self._schedule_preview()

    @staticmethod
    def _default_filename(text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if not first:
            return "sound_effect"
        return f"{normalize_filename_stem(first, default='sound_effect', max_length=48)}_sound"

    def _schedule_preview(self, *_args) -> None:
        if not self._resetting:
            self._render_generation += 1
            self._preview_current = False
            self._update_save_state()
            self._preview_timer.start()

    def _request_preview(self) -> None:
        self._render_generation += 1
        request = (self._render_generation, self.settings())
        self._preview_current = False
        if not request[1].text.strip():
            self._pending_render = None
            self._apply_render_result(None, "")
            return
        if self._render_active:
            self._pending_render = request
            self.preview_status.setText("プレビューを更新しています…")
            self._update_save_state()
            return
        self._start_render(request)

    def _start_render(self, request: tuple[int, SoundEffectSettings]) -> None:
        generation, settings = request
        self._render_active = True
        worker = SoundRenderWorker(generation, settings)
        self._render_worker = worker
        worker.signals.finished.connect(self._render_finished)
        self.preview_status.setText("プレビューを更新しています…")
        QThreadPool.globalInstance().start(worker)

    @Slot(int, object, object, str)
    def _render_finished(self, generation: int, _settings: SoundEffectSettings, image: Image.Image | None, error: str) -> None:
        self._render_active = False
        self._render_worker = None
        if generation == self._render_generation:
            self._apply_render_result(image, error)
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            self._start_render(pending)

    def update_preview(self) -> None:
        """Synchronously refresh for explicit Save and deterministic test paths."""
        self._preview_timer.stop()
        self._render_generation += 1
        self._pending_render = None
        try:
            image = render_sound_effect(self.settings())
        except Exception as exc:
            self._apply_render_result(None, str(exc))
        else:
            self._apply_render_result(image, "")

    def _apply_render_result(self, image: Image.Image | None, error: str) -> None:
        self.preview_image = image
        self.preview.set_image(image)
        self._preview_current = not error
        if error:
            self.preview_status.setText(f"プレビューを作成できませんでした: {error}")
            self.preview_status.setStyleSheet("color: #c62828; font-weight: 700;")
            self.output_info.setText("透明背景（RGBA）\n—")
        elif image is None:
            self.preview_status.setStyleSheet("color: #667085; padding: 4px;")
            self.preview_status.setText("文字を入力するとプレビューされます")
            self.output_info.setText("透明背景（RGBA）\n—")
        else:
            self.preview_status.setStyleSheet("color: #667085; padding: 4px;")
            self.preview_status.setText("市松模様は透明部分です。")
            self.output_info.setText(f"透明背景（RGBA）\n{image.width} × {image.height} px")
        self._update_save_state()

    def _choose_color(self, title: str, attribute: str) -> None:
        selected = choose_color(getattr(self, attribute), self, title, show_alpha=True)
        if selected.isValid():
            setattr(self, attribute, selected)
            self._update_color_buttons()
            self._schedule_preview()

    def _update_color_buttons(self) -> None:
        for button, color in ((self.text_color_button, self._text_color), (self.outline_color_button, self._outline_color), (self.shadow_color_button, self._shadow_color)):
            button.setText(f"#{color.red():02X}{color.green():02X}{color.blue():02X}  A{color.alpha()}")
            foreground = "#ffffff" if color.lightness() < 128 and color.alpha() > 100 else "#182230"
            button.setStyleSheet(f"background: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()}); color: {foreground}; font-weight: 700;")

    def _update_enabled_controls(self, *_args) -> None:
        for control in (self.outline_color_button, self.outline_width_spin):
            control.setEnabled(self.outline_enabled.isChecked())
        for control in (self.shadow_color_button, self.shadow_distance_spin, self.shadow_blur_spin):
            control.setEnabled(self.shadow_enabled.isChecked())

    def reset_settings(self) -> None:
        text = self.text_edit.toPlainText()
        self._resetting = True
        defaults = SoundEffectSettings(font_family=self.font_catalog.default_family())
        self.font_button.set_family(defaults.font_family)
        self.font_size_spin.setValue(defaults.font_size)
        self._text_color = QColor(*defaults.color)
        self.outline_enabled.setChecked(defaults.outline_enabled)
        self._outline_color = QColor(*defaults.outline_color)
        self.outline_width_spin.setValue(defaults.outline_width)
        self.shadow_enabled.setChecked(defaults.shadow_enabled)
        self._shadow_color = QColor(*defaults.shadow_color)
        self.shadow_distance_spin.setValue(defaults.shadow_distance)
        self.shadow_blur_spin.setValue(defaults.shadow_blur)
        self.horizontal_button.setChecked(True)
        self.letter_spacing_spin.setValue(defaults.letter_spacing)
        self.line_spacing_spin.setValue(defaults.line_spacing)
        self.rotation_spin.setValue(defaults.rotation)
        self.scale_x_spin.setValue(defaults.scale_x)
        self.scale_y_spin.setValue(defaults.scale_y)
        self.padding_spin.setValue(defaults.padding)
        self.text_edit.setPlainText(text)
        self._resetting = False
        self._update_color_buttons()
        self._update_enabled_controls()
        self._schedule_preview()

    def clear_all(self) -> None:
        """Return this material editor to a fresh state without touching saved files."""
        self.reset_settings()
        self.text_edit.clear()
        self._auto_filename = self._default_filename("")
        self.filename_edit.setText(self._auto_filename)
        self._preview_timer.stop()
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
        self.save_button.setEnabled(self._preview_current and self.preview_image is not None and bool(stem) and self.output_folder.is_dir())

    def can_close(self) -> bool:
        return not self._render_active

    def save_png(self) -> None:
        self.update_preview()
        if self.preview_image is None:
            return
        if not self.output_folder.is_dir():
            QMessageBox.warning(self, "保存先を使用できません", "保存先フォルダーを選び直してください。")
            return
        stem = normalize_filename_stem(self.filename_edit.text(), strip_extensions=(".png",))
        if not stem:
            QMessageBox.warning(self, "ファイル名を入力してください", "保存するファイル名を入力してください。")
            return
        try:
            path = save_sound_effect(self.preview_image, self.output_folder, stem)
        except OSError as exc:
            QMessageBox.warning(self, "保存できませんでした", str(exc))
            return
        self.save_result.setStyleSheet("color: #137333; font-weight: 700;")
        self.save_result.setText(f"✓ 保存しました\n{path.name}")
        self.save_result.setToolTip(str(path))
        self.last_saved_path = path
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
