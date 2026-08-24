from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import ProcessingError
from .thumbnail_models import (
    CanvasPreset,
    FixedLabelSettings,
    NumberingSettings,
    OverlayPosition,
    TextAlignment,
    ThumbnailFormat,
    ThumbnailSettings,
    ThumbnailTemplate,
    TitleRecord,
    VerticalAlignment,
    parse_title_records,
)
from .thumbnail_renderer import (
    ThumbnailLayoutError,
    render_record,
    render_thumbnail,
    write_thumbnail_output,
)
from .thumbnail_storage import (
    ThumbnailStorageError,
    ThumbnailTemplateStore,
    settings_to_dict,
)
from .ui_styles import INPUT_CONTROL_STYLE


LOGGER = logging.getLogger(__name__)
CUSTOM_CANVAS_LABEL = "任意サイズ"
ALIGNMENT_LABELS = {
    TextAlignment.LEFT: "左揃え",
    TextAlignment.CENTER: "中央揃え",
    TextAlignment.RIGHT: "右揃え",
}
VERTICAL_ALIGNMENT_LABELS = {
    VerticalAlignment.TOP: "上",
    VerticalAlignment.CENTER: "中央",
    VerticalAlignment.BOTTOM: "下",
}
POSITION_LABELS = {
    OverlayPosition.TOP_LEFT: "左上",
    OverlayPosition.TOP_CENTER: "上中央",
    OverlayPosition.TOP_RIGHT: "右上",
    OverlayPosition.BOTTOM_LEFT: "左下",
    OverlayPosition.BOTTOM_CENTER: "下中央",
    OverlayPosition.BOTTOM_RIGHT: "右下",
}
THUMBNAIL_STYLE = INPUT_CONTROL_STYLE + """
QGroupBox {
    font-size: 14px;
    font-weight: 700;
    border: 1px solid #d7dde5;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    background-color: #fbfcfd;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #182230;
}
QPlainTextEdit {
    background-color: #f4f7fb;
    color: #182230;
    border: 2px solid #6f8094;
    border-radius: 8px;
    padding: 10px;
    font-size: 14px;
    selection-background-color: #315fbd;
    selection-color: #ffffff;
}
QPlainTextEdit:hover {
    background-color: #edf3f9;
    border-color: #405b79;
}
QPlainTextEdit:focus {
    background-color: #ffffff;
    border: 2px solid #2457b2;
}
QPlainTextEdit:disabled {
    background-color: #f3f4f6;
    color: #9aa1aa;
    border-color: #d4d8de;
}
QPushButton {
    min-height: 28px;
    background-color: #f5f7fa;
    color: #182230;
    border: 1px solid #7b899a;
    border-radius: 6px;
    padding: 5px 10px;
}
QPushButton:hover {
    background-color: #e5edf6;
    border-color: #405b79;
}
QPushButton:focus {
    background-color: #ffffff;
    border: 2px solid #2457b2;
    padding: 4px 9px;
}
QPushButton:disabled {
    background-color: #f3f4f6;
    color: #9aa1aa;
    border: 1px solid #d4d8de;
}
QCheckBox {
    color: #182230;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px;
}
QCheckBox:hover {
    background-color: #e5edf6;
}
QCheckBox:focus {
    border-color: #2457b2;
    background-color: #f7faff;
}
QCheckBox:disabled {
    color: #9aa1aa;
    background-color: transparent;
}
QPushButton#generateButton {
    min-height: 44px;
    background-color: #315fbd;
    color: #ffffff;
    border: 1px solid #315fbd;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton#generateButton:hover {
    background-color: #284fa1;
    border-color: #284fa1;
}
QPushButton#generateButton:focus {
    background-color: #284fa1;
    border: 2px solid #173a82;
    padding: 7px 15px;
}
QPushButton#generateButton:pressed {
    background-color: #203f82;
}
QPushButton#generateButton:disabled {
    background-color: #d9dee6;
    color: #8f98a6;
    border-color: #d9dee6;
}
"""


class CollapsibleSection(QWidget):
    def __init__(self, title: str, description: str, content: QWidget) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(5)

        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet(
            "QToolButton { text-align: left; font-weight: 700; padding: 7px;"
            "border: 1px solid #c3ceda; background: #e8eef5;"
            "color: #182230; border-radius: 6px; }"
            "QToolButton:hover { background: #dbe6f1; border-color: #405b79; }"
            "QToolButton:focus { border: 2px solid #2457b2; padding: 6px; }"
            "QToolButton:disabled { background: #f3f4f6; color: #9aa1aa;"
            "border-color: #d4d8de; }"
        )
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: #667085; padding: 0 8px 4px 24px;")
        self.content = content
        self.content.hide()
        self.toggle.toggled.connect(self._set_expanded)

        layout.addWidget(self.toggle)
        layout.addWidget(self.description)
        layout.addWidget(content)

    @Slot(bool)
    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)


class ElidedPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._full_path = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.setStyleSheet("color: #344054;")

    def set_path(self, path: Path | None) -> None:
        self._full_path = str(path) if path is not None else ""
        self.setToolTip(self._full_path)
        self.setAccessibleDescription(self._full_path)
        self._update_elided_text()

    def _update_elided_text(self) -> None:
        available = max(80, self.width() - 4)
        self.setText(
            self.fontMetrics().elidedText(
                self._full_path,
                Qt.TextElideMode.ElideMiddle,
                available,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_elided_text()


class ThumbnailPreview(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image) -> None:
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self._fit()

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())
        self.scene().setSceneRect(0, 0, 1, 1)

    def _fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class ThumbnailWorker(QObject):
    progress = Signal(int)
    item_status = Signal(int, str, str)
    finished = Signal(int, int)

    def __init__(
        self,
        records: list[TitleRecord],
        settings: ThumbnailSettings,
        output_folder: Path,
    ) -> None:
        super().__init__()
        self.records = records
        self.settings = settings
        self.output_folder = output_folder

    @Slot()
    def run(self) -> None:
        succeeded = failed = 0
        total = max(1, len(self.records))
        LOGGER.info(
            "Thumbnail batch start: %s items; output=%s",
            len(self.records),
            self.output_folder,
        )
        for row, record in enumerate(self.records):
            self.item_status.emit(row, "Processing", "")
            try:
                data, rendered = render_record(record, self.settings)
                destination = write_thumbnail_output(
                    self.output_folder,
                    record,
                    len(self.records),
                    self.settings.output_format,
                    data,
                )
                detail = (
                    f"{destination} · 文字 {rendered.font_size}px · "
                    f"{rendered.line_count}行"
                )
                succeeded += 1
                LOGGER.info("Thumbnail generated: %s", destination)
                self.item_status.emit(row, "Done", detail)
            except Exception as exc:  # One title must not stop the remaining batch.
                failed += 1
                LOGGER.exception("Thumbnail generation failed: %s", record.title)
                message = (
                    str(exc)
                    if isinstance(exc, ProcessingError)
                    else "サムネイル生成に失敗しました。"
                )
                self.item_status.emit(row, "Error", message)
            self.progress.emit(round((row + 1) * 100 / total))
        LOGGER.info(
            "Thumbnail batch result: %s generated; %s errors",
            succeeded,
            failed,
        )
        self.finished.emit(succeeded, failed)


class ThumbnailPage(QWidget):
    processing_changed = Signal(bool)

    def __init__(
        self,
        template_store: ThumbnailTemplateStore | None = None,
    ) -> None:
        super().__init__()
        self.template_store = template_store or ThumbnailTemplateStore()
        desktop = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
        default_output = (
            Path(desktop or str(Path.home() / "Desktop"))
            / "Quick Processing Tool Thumbnails"
        )
        stored_output = Path(self.template_store.last_output_folder)
        self.output_folder = (
            stored_output
            if self.template_store.last_output_folder and stored_output.is_dir()
            else default_output
        )
        self.preview_index = 0
        self._applying_template = False
        self._selected_template_id = ""
        self._additional_labels: list[FixedLabelSettings] = []
        self._thread: QThread | None = None
        self._worker: ThumbnailWorker | None = None
        self._processing = False
        self._batch_total = 0
        self._last_generated_folder: Path | None = None
        self._background_color = QColor("#171923")
        self._font_color = QColor("#FFFFFF")
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        self._populate_templates(self.template_store.last_selected_template)
        self._apply_selected_template()
        self._titles_changed()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter = splitter
        splitter.setObjectName("thumbnail_workspace")
        splitter.setStyleSheet(THUMBNAIL_STYLE)

        settings_scroll = QScrollArea()
        self.settings_scroll = settings_scroll
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        settings_scroll.setMinimumWidth(280)
        settings_scroll.setMaximumWidth(560)
        settings_content = QWidget()
        self.settings_content = settings_content
        settings_content.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        settings_layout = QVBoxLayout(settings_content)
        settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        settings_layout.setSpacing(10)

        self.settings_heading = QLabel("サムネイル設定")
        self.settings_heading.setStyleSheet(
            "font-size: 20px; font-weight: 750; color: #182230;"
        )
        settings_intro = QLabel(
            "プレビューを見ながら共通デザインを設定します"
        )
        settings_intro.setWordWrap(True)
        settings_intro.setStyleSheet("color: #667085;")
        settings_layout.addWidget(self.settings_heading)
        settings_layout.addWidget(settings_intro)

        self.template_group = QGroupBox("テンプレート")
        template_layout = QVBoxLayout(self.template_group)
        template_layout.setSpacing(6)
        self.template_combo = QComboBox()
        self._configure_combo(self.template_combo)
        self.template_combo.setAccessibleName("テンプレート")
        template_layout.addWidget(self.template_combo)
        template_actions = QHBoxLayout()
        self.template_save_button = QPushButton("保存・別名…")
        self.template_update_button = QPushButton("上書き")
        self.template_delete_button = QPushButton("削除")
        template_actions.addWidget(self.template_save_button, 1)
        template_actions.addWidget(self.template_update_button)
        template_actions.addWidget(self.template_delete_button)
        template_layout.addLayout(template_actions)
        self.template_status = QLabel("")
        self.template_status.setWordWrap(True)
        self.template_status.setStyleSheet("color: #667085; font-size: 12px;")
        template_layout.addWidget(self.template_status)
        settings_layout.addWidget(self.template_group)

        self.canvas_group = QGroupBox("サイズ")
        self.canvas_form = QFormLayout(self.canvas_group)
        self._configure_form(self.canvas_form)
        self.canvas_preset_combo = QComboBox()
        self._configure_combo(self.canvas_preset_combo)
        self._populate_canvas_presets()
        self.canvas_form.addRow("プリセット", self.canvas_preset_combo)
        self.canvas_size_label = QLabel()
        self.canvas_size_label.setStyleSheet("font-weight: 700; color: #344054;")
        self.canvas_form.addRow("出力サイズ", self.canvas_size_label)
        self.width_spin = self._spin(128, 6000, 1200)
        self.width_spin.setSuffix(" px")
        self.height_spin = self._spin(128, 6000, 1200)
        self.height_spin.setSuffix(" px")
        self.canvas_form.addRow("幅", self.width_spin)
        self.canvas_form.addRow("高さ", self.height_spin)
        preset_actions = QHBoxLayout()
        self.canvas_save_button = QPushButton("このサイズを保存…")
        self.canvas_delete_button = QPushButton("削除")
        preset_actions.addWidget(self.canvas_save_button, 1)
        preset_actions.addWidget(self.canvas_delete_button)
        self.canvas_form.addRow("", preset_actions)
        settings_layout.addWidget(self.canvas_group)

        self.appearance_group = QGroupBox("見た目")
        appearance_layout = QVBoxLayout(self.appearance_group)
        appearance_form = QFormLayout()
        self._configure_form(appearance_form)
        self.background_button = QPushButton()
        self.background_button.clicked.connect(self._choose_background)
        self._update_color_button(self.background_button, self._background_color)
        appearance_form.addRow("背景色", self.background_button)

        self.font_combo = QFontComboBox()
        self._configure_combo(self.font_combo)
        self._select_default_font()
        appearance_form.addRow("フォント", self.font_combo)

        self.font_size_spin = self._spin(8, 500, 72)
        self.font_size_spin.setSuffix(" px")
        appearance_form.addRow("文字サイズ", self.font_size_spin)

        self.font_color_button = QPushButton()
        self.font_color_button.clicked.connect(self._choose_font_color)
        self._update_color_button(self.font_color_button, self._font_color)
        appearance_form.addRow("文字色", self.font_color_button)

        self.alignment_combo = QComboBox()
        self._configure_combo(self.alignment_combo)
        for alignment in TextAlignment:
            self.alignment_combo.addItem(ALIGNMENT_LABELS[alignment], alignment.value)
        self.alignment_combo.setCurrentIndex(
            self.alignment_combo.findData(TextAlignment.CENTER.value)
        )
        appearance_form.addRow("横位置", self.alignment_combo)

        self.vertical_alignment_combo = QComboBox()
        self._configure_combo(self.vertical_alignment_combo)
        for alignment in VerticalAlignment:
            self.vertical_alignment_combo.addItem(
                VERTICAL_ALIGNMENT_LABELS[alignment],
                alignment.value,
            )
        self.vertical_alignment_combo.setCurrentIndex(
            self.vertical_alignment_combo.findData(VerticalAlignment.CENTER.value)
        )
        appearance_form.addRow("縦位置", self.vertical_alignment_combo)

        self.bold_check = QCheckBox("太字にする")
        self.bold_check.setChecked(True)
        appearance_form.addRow("", self.bold_check)
        appearance_layout.addLayout(appearance_form)

        details_content = QWidget()
        details_form = QFormLayout(details_content)
        self._configure_form(details_form)
        self.min_font_size_spin = self._spin(8, 500, 36)
        self.min_font_size_spin.setSuffix(" px")
        self.margin_spin = self._spin(0, 2000, 100)
        self.margin_spin.setSuffix(" px")
        self.max_lines_spin = self._spin(1, 20, 5)
        self.max_lines_spin.setSuffix(" 行")
        details_form.addRow("最小文字サイズ", self.min_font_size_spin)
        details_form.addRow("文字領域の余白", self.margin_spin)
        details_form.addRow("最大行数", self.max_lines_spin)
        self.details_section = CollapsibleSection(
            "詳細設定",
            "自動調整の最小文字サイズと文字領域を設定します",
            details_content,
        )
        appearance_layout.addWidget(self.details_section)
        settings_layout.addWidget(self.appearance_group)

        overlays_content = QWidget()
        overlays_layout = QVBoxLayout(overlays_content)
        overlays_layout.setContentsMargins(0, 0, 0, 0)

        self.label_group = QGroupBox("固定ラベル")
        label_layout = QVBoxLayout(self.label_group)
        self.label_enabled = QCheckBox("固定ラベルを表示する")
        label_layout.addWidget(self.label_enabled)
        self.label_details = QWidget()
        label_form = QFormLayout(self.label_details)
        self._configure_form(label_form)
        self.label_text = QLineEdit()
        self.label_text.setPlaceholderText("例：過去音声")
        self.label_font = QFontComboBox()
        self._configure_combo(self.label_font)
        self.label_font_size = self._spin(8, 500, 28)
        self.label_font_size.setSuffix(" px")
        self._label_color = QColor("#FFFFFF")
        self.label_color_button = QPushButton()
        self._update_color_button(self.label_color_button, self._label_color)
        self.label_position = self._position_combo(OverlayPosition.TOP_LEFT)
        label_form.addRow("文字", self.label_text)
        label_form.addRow("フォント", self.label_font)
        label_form.addRow("文字サイズ", self.label_font_size)
        label_form.addRow("文字色", self.label_color_button)
        label_form.addRow("位置", self.label_position)
        label_layout.addWidget(self.label_details)
        overlays_layout.addWidget(self.label_group)

        self.number_group = QGroupBox("連番")
        number_layout = QVBoxLayout(self.number_group)
        self.number_enabled = QCheckBox("画像内に連番を表示する")
        number_layout.addWidget(self.number_enabled)
        self.number_details = QWidget()
        number_form = QFormLayout(self.number_details)
        self._configure_form(number_form)
        self.number_prefix = QLineEdit("#")
        self.number_prefix.setMaxLength(20)
        self.number_start = self._spin(0, 99999999, 1)
        self.number_digits = self._spin(1, 8, 3)
        self.number_font = QFontComboBox()
        self._configure_combo(self.number_font)
        self.number_font_size = self._spin(8, 500, 28)
        self.number_font_size.setSuffix(" px")
        self._number_color = QColor("#FFFFFF")
        self.number_color_button = QPushButton()
        self._update_color_button(self.number_color_button, self._number_color)
        self.number_position = self._position_combo(OverlayPosition.TOP_RIGHT)
        number_form.addRow("接頭辞", self.number_prefix)
        number_form.addRow("開始番号", self.number_start)
        number_form.addRow("桁数", self.number_digits)
        number_form.addRow("フォント", self.number_font)
        number_form.addRow("文字サイズ", self.number_font_size)
        number_form.addRow("文字色", self.number_color_button)
        number_form.addRow("位置", self.number_position)
        number_layout.addWidget(self.number_details)
        overlays_layout.addWidget(self.number_group)

        self.overlays_section = CollapsibleSection(
            "固定ラベル・連番",
            "必要なときだけ、全画像に共通する文字や番号を追加します",
            overlays_content,
        )
        settings_layout.addWidget(self.overlays_section)

        self.export_group = QGroupBox("保存")
        self.export_form = QFormLayout(self.export_group)
        self._configure_form(self.export_form)
        self.format_combo = QComboBox()
        self._configure_combo(self.format_combo)
        for output_format in ThumbnailFormat:
            self.format_combo.addItem(output_format.value, output_format.value)
        self.format_combo.setCurrentIndex(
            self.format_combo.findData(ThumbnailFormat.JPEG.value)
        )
        self.quality_spin = self._spin(1, 100, 90)
        self.export_form.addRow("保存形式", self.format_combo)
        self.export_form.addRow("JPEG品質", self.quality_spin)
        self.folder_label = QLabel(str(self.output_folder))
        self.folder_label.setWordWrap(True)
        self.folder_label.setMinimumWidth(0)
        self.folder_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.folder_label.setToolTip(str(self.output_folder))
        self.folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.folder_button = QPushButton("保存先を選ぶ…")
        self.folder_button.clicked.connect(self.choose_output_folder)
        self.export_form.addRow("保存先", self.folder_label)
        self.export_form.addRow("", self.folder_button)
        settings_layout.addWidget(self.export_group)
        settings_layout.addStretch(1)

        settings_scroll.setWidget(settings_content)
        splitter.addWidget(settings_scroll)

        center = QWidget()
        center.setMinimumWidth(360)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 8, 8, 8)
        preview_heading = QLabel("プレビュー")
        preview_heading.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #182230;"
        )
        center_layout.addWidget(preview_heading)
        self.preview = ThumbnailPreview()
        center_layout.addWidget(self.preview, 1)

        navigation = QHBoxLayout()
        self.previous_button = QPushButton("‹ 前へ")
        self.previous_button.clicked.connect(lambda: self._move_preview(-1))
        self.preview_position = QLabel("0 / 0")
        self.preview_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_position.setStyleSheet("font-weight: 700; color: #344054;")
        self.next_button = QPushButton("次へ ›")
        self.next_button.clicked.connect(lambda: self._move_preview(1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.preview_position, 1)
        navigation.addWidget(self.next_button)
        center_layout.addLayout(navigation)

        self.preview_title = QLabel("タイトル未入力")
        self.preview_title.setWordWrap(True)
        self.preview_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_title.setStyleSheet(
            "font-weight: 700; padding: 4px; color: #182230;"
        )
        center_layout.addWidget(self.preview_title)

        self.preview_status = QLabel("")
        self.preview_status.setWordWrap(True)
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.preview_status)
        splitter.addWidget(center)

        batch = QWidget()
        batch.setObjectName("thumbnail_titles_panel")
        batch.setMinimumWidth(250)
        batch_layout = QVBoxLayout(batch)
        batch_layout.setContentsMargins(8, 8, 8, 8)
        batch_layout.setSpacing(8)

        self.titles_heading = QLabel("タイトル")
        self.titles_heading.setStyleSheet(
            "font-size: 20px; font-weight: 750; color: #182230;"
        )
        batch_layout.addWidget(self.titles_heading)
        titles_help = QLabel("1行につき1枚のサムネイルを作成します")
        titles_help.setStyleSheet("color: #667085;")
        batch_layout.addWidget(titles_help)

        self.titles_edit = QPlainTextEdit()
        self.titles_edit.setObjectName("thumbnail_titles_input")
        self.titles_edit.setPlaceholderText(
            "タイトルをここへ貼り付け\n\n"
            "夜中寝てたら年下彼氏に急に襲われて\n"
            "嫉妬した彼に問い詰められて\n"
            "眠れない夜にずっと囁かれて"
        )
        self.titles_edit.setMinimumHeight(230)
        self.titles_edit.textChanged.connect(self._titles_changed)
        batch_layout.addWidget(self.titles_edit, 2)

        self.title_count = QLabel("0枚生成予定")
        self.title_count.setObjectName("thumbnail_count")
        self.title_count.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #315fbd;"
        )
        batch_layout.addWidget(self.title_count)

        self.generate_button = QPushButton("タイトルを入力してください")
        self.generate_button.setObjectName("generateButton")
        self.generate_button.clicked.connect(self.generate_all)
        self.generate_button.setEnabled(False)
        batch_layout.addWidget(self.generate_button)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        batch_layout.addWidget(self.result_label)

        self.result_destination = QWidget()
        destination_layout = QVBoxLayout(self.result_destination)
        destination_layout.setContentsMargins(0, 2, 0, 4)
        destination_layout.setSpacing(4)
        destination_heading = QLabel("保存先")
        destination_heading.setStyleSheet("font-weight: 700; color: #344054;")
        self.result_folder_label = ElidedPathLabel()
        self.open_result_folder_button = QPushButton("保存先を開く")
        self.open_result_folder_button.clicked.connect(
            self.open_result_folder
        )
        destination_layout.addWidget(destination_heading)
        destination_layout.addWidget(self.result_folder_label)
        destination_layout.addWidget(self.open_result_folder_button)
        self.result_destination.hide()
        batch_layout.addWidget(self.result_destination)

        results_heading = QLabel("生成結果")
        results_heading.setStyleSheet("font-weight: 700;")
        batch_layout.addWidget(results_heading)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["番号", "タイトル", "状態"])
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setMinimumHeight(150)
        header = self.result_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.result_tree.setColumnWidth(0, 64)
        self.result_tree.setColumnWidth(2, 76)
        batch_layout.addWidget(self.result_tree, 1)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress_count = QLabel("0 / 0")
        self.progress_count.setMinimumWidth(64)
        self.progress_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_count)
        batch_layout.addLayout(progress_row)
        splitter.addWidget(batch)

        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([350, 500, 315])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        self.template_combo.currentIndexChanged.connect(
            self._template_selection_changed
        )
        self.template_save_button.clicked.connect(self.save_template_as)
        self.template_update_button.clicked.connect(self.update_template)
        self.template_delete_button.clicked.connect(self.delete_template)
        self.canvas_save_button.clicked.connect(self.save_canvas_preset)
        self.canvas_delete_button.clicked.connect(self.delete_canvas_preset)
        self.canvas_preset_combo.currentIndexChanged.connect(
            self._canvas_preset_changed
        )
        self.width_spin.valueChanged.connect(self._canvas_dimension_changed)
        self.height_spin.valueChanged.connect(self._canvas_dimension_changed)
        for spin in (
            self.font_size_spin,
            self.min_font_size_spin,
            self.margin_spin,
            self.max_lines_spin,
            self.quality_spin,
            self.label_font_size,
            self.number_start,
            self.number_digits,
            self.number_font_size,
        ):
            spin.valueChanged.connect(self._design_changed)
        self.font_combo.currentFontChanged.connect(self._design_changed)
        self.label_font.currentFontChanged.connect(self._design_changed)
        self.number_font.currentFontChanged.connect(self._design_changed)
        self.bold_check.toggled.connect(self._design_changed)
        self.alignment_combo.currentIndexChanged.connect(self._design_changed)
        self.vertical_alignment_combo.currentIndexChanged.connect(
            self._design_changed
        )
        self.label_enabled.toggled.connect(self._overlay_visibility_changed)
        self.number_enabled.toggled.connect(self._overlay_visibility_changed)
        self.label_text.textChanged.connect(self._design_changed)
        self.number_prefix.textChanged.connect(self._design_changed)
        self.label_position.currentIndexChanged.connect(self._design_changed)
        self.number_position.currentIndexChanged.connect(self._design_changed)
        self.label_color_button.clicked.connect(self._choose_label_color)
        self.number_color_button.clicked.connect(self._choose_number_color)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self._canvas_preset_changed()
        self._format_changed()
        self._overlay_visibility_changed()

    @staticmethod
    def _configure_form(form: QFormLayout) -> None:
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    @staticmethod
    def _configure_combo(combo: QComboBox) -> None:
        combo.setMinimumWidth(0)
        combo.setMinimumContentsLength(10)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    @classmethod
    def _position_combo(
        cls,
        default: OverlayPosition,
    ) -> QComboBox:
        combo = QComboBox()
        cls._configure_combo(combo)
        for position in OverlayPosition:
            combo.addItem(POSITION_LABELS[position], position.value)
        combo.setCurrentIndex(combo.findData(default.value))
        return combo

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        return spin

    def _select_default_font(self) -> None:
        families = QFontDatabase.families()
        by_name = {family.casefold(): family for family in families}
        preferred = next(
            (
                by_name[name.casefold()]
                for name in (
                    "Yu Gothic UI",
                    "Yu Gothic",
                    "Meiryo",
                    "BIZ UDPGothic",
                    "Noto Sans CJK JP",
                )
                if name.casefold() in by_name
            ),
            self.font_combo.currentFont().family(),
        )
        index = self.font_combo.findText(preferred)
        if index >= 0:
            self.font_combo.setCurrentIndex(index)

    @staticmethod
    def _update_color_button(button: QPushButton, color: QColor) -> None:
        contrast = "#000000" if color.lightness() > 150 else "#FFFFFF"
        button.setText(color.name().upper())
        button.setStyleSheet(
            f"QPushButton {{ background: {color.name()}; color: {contrast};"
            "border: 1px solid #65768a; border-radius: 6px;"
            "min-height: 30px; font-weight: 700; }}"
            "QPushButton:hover { border: 2px solid #2457b2; }"
            "QPushButton:focus { border: 2px solid #173a82; }"
            "QPushButton:disabled { background: #f3f4f6; color: #9aa1aa;"
            "border: 1px solid #d4d8de; }"
        )

    def _populate_templates(self, selected_id: str = "") -> None:
        self._applying_template = True
        self.template_combo.clear()
        for template in self.template_store.templates():
            label = (
                f"{template.name}（標準）"
                if template.built_in
                else template.name
            )
            self.template_combo.addItem(label, template.template_id)
        target = selected_id or self.template_store.last_selected_template
        index = self.template_combo.findData(target)
        self.template_combo.setCurrentIndex(max(0, index))
        self._selected_template_id = str(self.template_combo.currentData() or "")
        self._applying_template = False
        self._update_template_actions()

    def _populate_canvas_presets(self, selected_name: str = "") -> None:
        combo = getattr(self, "canvas_preset_combo", None)
        if combo is None:
            return
        with QSignalBlocker(combo):
            combo.clear()
            for preset in self.template_store.canvas_presets():
                combo.addItem(preset.name, preset)
            combo.addItem(CUSTOM_CANVAS_LABEL, None)
            index = combo.findText(selected_name) if selected_name else -1
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _selected_template(self) -> ThumbnailTemplate | None:
        return self.template_store.get_template(
            str(self.template_combo.currentData() or "")
        )

    @Slot()
    def _template_selection_changed(self, *_args) -> None:
        if self._applying_template:
            return
        self._apply_selected_template()

    def _apply_selected_template(self) -> None:
        template = self._selected_template()
        if template is None:
            return
        self._applying_template = True
        settings = template.settings
        self._selected_template_id = template.template_id
        self._populate_canvas_presets(settings.canvas_preset_name)
        preset_index = self.canvas_preset_combo.findText(settings.canvas_preset_name)
        matching = (
            preset_index >= 0
            and isinstance(
                self.canvas_preset_combo.itemData(preset_index),
                CanvasPreset,
            )
            and self.canvas_preset_combo.itemData(preset_index).width == settings.width
            and self.canvas_preset_combo.itemData(preset_index).height == settings.height
        )
        if not matching:
            preset_index = self.canvas_preset_combo.findText(CUSTOM_CANVAS_LABEL)
        self.canvas_preset_combo.setCurrentIndex(preset_index)
        self.width_spin.setValue(settings.width)
        self.height_spin.setValue(settings.height)
        self._background_color = QColor(settings.background_color)
        self._font_color = QColor(settings.font_color)
        self._update_color_button(self.background_button, self._background_color)
        self._update_color_button(self.font_color_button, self._font_color)
        self._set_font_family(self.font_combo, settings.font_family)
        self.font_size_spin.setValue(settings.font_size)
        self.min_font_size_spin.setValue(settings.min_font_size)
        self.bold_check.setChecked(settings.bold)
        self.alignment_combo.setCurrentIndex(
            self.alignment_combo.findData(settings.alignment.value)
        )
        self.vertical_alignment_combo.setCurrentIndex(
            self.vertical_alignment_combo.findData(
                settings.vertical_alignment.value
            )
        )
        self.margin_spin.setValue(settings.margin)
        self.max_lines_spin.setValue(settings.max_lines)

        label = settings.fixed_label
        self._additional_labels = copy.deepcopy(settings.labels[1:])
        self.label_enabled.setChecked(label.enabled)
        self.label_text.setText(label.text)
        self._set_font_family(self.label_font, label.font_family)
        self.label_font_size.setValue(label.font_size)
        self._label_color = QColor(label.color)
        self._update_color_button(self.label_color_button, self._label_color)
        self.label_position.setCurrentIndex(
            self.label_position.findData(label.position.value)
        )

        numbering = settings.numbering
        self.number_enabled.setChecked(numbering.enabled)
        self.number_prefix.setText(numbering.prefix)
        self.number_start.setValue(numbering.start_number)
        self.number_digits.setValue(numbering.digits)
        self._set_font_family(self.number_font, numbering.font_family)
        self.number_font_size.setValue(numbering.font_size)
        self._number_color = QColor(numbering.color)
        self._update_color_button(self.number_color_button, self._number_color)
        self.number_position.setCurrentIndex(
            self.number_position.findData(numbering.position.value)
        )
        self.format_combo.setCurrentIndex(
            self.format_combo.findData(settings.output_format.value)
        )
        self.quality_spin.setValue(settings.quality)
        self._applying_template = False

        self._canvas_preset_changed()
        self._format_changed()
        self._overlay_visibility_changed()
        try:
            self.template_store.set_last_selected(template.template_id)
        except ThumbnailStorageError as exc:
            LOGGER.warning("Template selection state not saved: %s", exc)
        self._update_template_actions()
        self.schedule_preview()

    @staticmethod
    def _set_font_family(combo: QFontComboBox, family: str) -> None:
        index = combo.findText(family)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _update_template_actions(self) -> None:
        template = self._selected_template()
        user_template = bool(template and not template.built_in)
        self.template_update_button.setEnabled(user_template and not self._processing)
        self.template_delete_button.setEnabled(user_template and not self._processing)
        self.template_save_button.setEnabled(not self._processing)
        if template is None:
            self.template_status.setText("")
            return
        current = settings_to_dict(self.settings()) if hasattr(self, "width_spin") else {}
        saved = settings_to_dict(template.settings)
        if current == saved:
            state = "保存済み" if user_template else "標準テンプレート"
        else:
            state = "未保存の変更（切替で破棄）"
        self.template_status.setText(state)

    @Slot()
    def save_template_as(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "テンプレートとして保存",
            "テンプレート名",
            QLineEdit.EchoMode.Normal,
        )
        if not accepted:
            return
        try:
            template = self.template_store.create_template(name, self.settings())
        except ThumbnailStorageError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return
        self._populate_templates(template.template_id)
        self._apply_selected_template()

    @Slot()
    def update_template(self) -> None:
        template = self._selected_template()
        if template is None or template.built_in:
            return
        try:
            self.template_store.update_template(template.template_id, self.settings())
        except ThumbnailStorageError as exc:
            QMessageBox.warning(self, "更新できません", str(exc))
            return
        self._populate_templates(template.template_id)
        self._update_template_actions()

    @Slot()
    def delete_template(self) -> None:
        template = self._selected_template()
        if template is None or template.built_in:
            return
        answer = QMessageBox.question(
            self,
            "テンプレートを削除",
            f"「{template.name}」を削除しますか？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.template_store.delete_template(template.template_id)
        except ThumbnailStorageError as exc:
            QMessageBox.warning(self, "削除できません", str(exc))
            return
        self._populate_templates(self.template_store.last_selected_template)
        self._apply_selected_template()

    @Slot()
    def save_canvas_preset(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "サイズPresetとして保存",
            "Preset名",
            QLineEdit.EchoMode.Normal,
        )
        if not accepted:
            return
        try:
            preset = self.template_store.add_canvas_preset(
                name,
                self.width_spin.value(),
                self.height_spin.value(),
            )
        except ThumbnailStorageError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return
        self._populate_canvas_presets(preset.name)
        self._canvas_preset_changed()

    @Slot()
    def delete_canvas_preset(self) -> None:
        preset = self.canvas_preset_combo.currentData()
        if not isinstance(preset, CanvasPreset):
            return
        try:
            self.template_store.delete_canvas_preset(preset.name)
        except ThumbnailStorageError as exc:
            QMessageBox.warning(self, "削除できません", str(exc))
            return
        self._populate_canvas_presets()
        self._canvas_preset_changed()

    @Slot()
    def _choose_label_color(self) -> None:
        color = QColorDialog.getColor(self._label_color, self, "固定ラベルの色")
        if color.isValid():
            self._label_color = color
            self._update_color_button(self.label_color_button, color)
            self._design_changed()

    @Slot()
    def _choose_number_color(self) -> None:
        color = QColorDialog.getColor(self._number_color, self, "連番の色")
        if color.isValid():
            self._number_color = color
            self._update_color_button(self.number_color_button, color)
            self._design_changed()

    @Slot()
    def _overlay_visibility_changed(self, *_args) -> None:
        self.label_details.setVisible(self.label_enabled.isChecked())
        self.number_details.setVisible(self.number_enabled.isChecked())
        self._design_changed()

    @Slot()
    def _design_changed(self, *_args) -> None:
        if self._applying_template:
            return
        self._update_template_actions()
        self.schedule_preview()

    @Slot()
    def _choose_background(self) -> None:
        color = QColorDialog.getColor(
            self._background_color,
            self,
            "背景色を選ぶ",
        )
        if color.isValid():
            self._background_color = color
            self._update_color_button(self.background_button, color)
            self._design_changed()

    @Slot()
    def _choose_font_color(self) -> None:
        color = QColorDialog.getColor(
            self._font_color,
            self,
            "文字色を選ぶ",
        )
        if color.isValid():
            self._font_color = color
            self._update_color_button(self.font_color_button, color)
            self._design_changed()

    @Slot()
    def _canvas_preset_changed(self, *_args) -> None:
        preset = self.canvas_preset_combo.currentData()
        custom = preset is None
        if isinstance(preset, CanvasPreset):
            self.width_spin.setValue(preset.width)
            self.height_spin.setValue(preset.height)
        self.canvas_form.setRowVisible(self.width_spin, custom)
        self.canvas_form.setRowVisible(self.height_spin, custom)
        self.width_spin.setEnabled(custom and not self._processing)
        self.height_spin.setEnabled(custom and not self._processing)
        self.canvas_delete_button.setEnabled(
            isinstance(preset, CanvasPreset)
            and self.template_store.is_user_preset(preset.name)
            and not self._processing
        )
        self._update_canvas_size_label()
        self._design_changed()

    @Slot()
    def _canvas_dimension_changed(self, *_args) -> None:
        self._update_canvas_size_label()
        self._design_changed()

    def _update_canvas_size_label(self) -> None:
        self.canvas_size_label.setText(
            f"{self.width_spin.value()} × {self.height_spin.value()} px"
        )

    @Slot()
    def _format_changed(self, *_args) -> None:
        jpeg = self.format_combo.currentData() == ThumbnailFormat.JPEG.value
        self.quality_spin.setEnabled(jpeg and not self._processing)
        self._design_changed()

    def records(self) -> list[TitleRecord]:
        return parse_title_records(self.titles_edit.toPlainText())

    def settings(self) -> ThumbnailSettings:
        minimum = min(
            self.font_size_spin.value(),
            self.min_font_size_spin.value(),
        )
        return ThumbnailSettings(
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            background_color=self._background_color.name(),
            font_family=self.font_combo.currentFont().family(),
            font_size=self.font_size_spin.value(),
            min_font_size=minimum,
            font_color=self._font_color.name(),
            bold=self.bold_check.isChecked(),
            canvas_preset_name=self.canvas_preset_combo.currentText(),
            alignment=TextAlignment(self.alignment_combo.currentData()),
            vertical_alignment=VerticalAlignment(
                self.vertical_alignment_combo.currentData()
            ),
            margin=self.margin_spin.value(),
            max_lines=self.max_lines_spin.value(),
            labels=[
                FixedLabelSettings(
                    enabled=self.label_enabled.isChecked(),
                    text=self.label_text.text(),
                    font_family=self.label_font.currentFont().family(),
                    font_size=self.label_font_size.value(),
                    color=self._label_color.name(),
                    position=OverlayPosition(self.label_position.currentData()),
                ),
                *copy.deepcopy(self._additional_labels),
            ],
            numbering=NumberingSettings(
                enabled=self.number_enabled.isChecked(),
                prefix=self.number_prefix.text(),
                start_number=self.number_start.value(),
                digits=self.number_digits.value(),
                font_family=self.number_font.currentFont().family(),
                font_size=self.number_font_size.value(),
                color=self._number_color.name(),
                position=OverlayPosition(self.number_position.currentData()),
            ),
            output_format=ThumbnailFormat(self.format_combo.currentData()),
            quality=self.quality_spin.value(),
        )

    @Slot()
    def _titles_changed(self) -> None:
        records = self.records()
        count = len(records)
        self.title_count.setText(f"{count}枚生成予定")
        self.preview_index = min(self.preview_index, max(0, count - 1))
        self.result_tree.clear()
        for record in records:
            item = QTreeWidgetItem(
                [f"{record.index:03d}", record.title, "待機中"]
            )
            item.setToolTip(1, record.title)
            self.result_tree.addTopLevelItem(item)

        self.generate_button.setText(
            f"{count}枚まとめて生成"
            if records
            else "タイトルを入力してください"
        )
        self.generate_button.setEnabled(bool(records) and not self._processing)
        self.progress.setValue(0)
        self.progress_count.setText(f"0 / {count}")
        self._reset_result_summary()
        self.schedule_preview()

    def _reset_result_summary(self) -> None:
        self.result_label.clear()
        self.result_label.setStyleSheet("")
        self.result_destination.hide()
        self.result_folder_label.set_path(None)
        self.open_result_folder_button.setEnabled(False)
        self._last_generated_folder = None

    @Slot()
    def schedule_preview(self, *_args) -> None:
        self._preview_timer.start()

    def _move_preview(self, offset: int) -> None:
        records = self.records()
        if not records:
            return
        self.preview_index = (self.preview_index + offset) % len(records)
        self.update_preview()

    @Slot()
    def update_preview(self) -> None:
        records = self.records()
        total = len(records)
        if not records:
            self.preview.clear_image()
            self.preview_position.setText("0 / 0")
            self.preview_title.setText("タイトル未入力")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.preview_status.setStyleSheet("color: #667085;")
            self.preview_status.setText(
                "右側へタイトルを入力するとプレビューが表示されます"
            )
            return

        record = records[self.preview_index]
        self.preview_position.setText(f"{self.preview_index + 1} / {total}")
        self.preview_title.setText(record.title)
        self.previous_button.setEnabled(total > 1)
        self.next_button.setEnabled(total > 1)
        try:
            rendered = render_thumbnail(record.title, self.settings(), record.index)
            self.preview.set_image(rendered.image)
            self.preview_status.setStyleSheet("color: #5f6368;")
            self.preview_status.setText(
                f"{rendered.image.width()} × {rendered.image.height()} / "
                f"文字 {rendered.font_size}px / {rendered.line_count}行"
            )
        except ThumbnailLayoutError as exc:
            self.preview.clear_image()
            self.preview_status.setStyleSheet(
                "color: #c62828; font-weight: 700;"
            )
            self.preview_status.setText(str(exc))

    @Slot()
    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "サムネイルの保存先を選ぶ",
            str(self.output_folder),
        )
        if folder:
            self.output_folder = Path(folder)
            self.folder_label.setText(str(self.output_folder))
            self.folder_label.setToolTip(str(self.output_folder))
            try:
                self.template_store.set_last_output_folder(self.output_folder)
            except ThumbnailStorageError as exc:
                LOGGER.warning("Output folder state not saved: %s", exc)

    @Slot()
    def generate_all(self) -> None:
        records = self.records()
        if not records:
            QMessageBox.information(
                self,
                "タイトルがありません",
                "1行に1件ずつタイトルを入力してください。",
            )
            return
        if self._thread is not None:
            return
        if self.output_folder.exists() and not self.output_folder.is_dir():
            QMessageBox.warning(
                self,
                "保存先を使用できません",
                "保存先フォルダーを選び直してください。",
            )
            return

        self._batch_total = len(records)
        self._last_generated_folder = self.output_folder.resolve()
        self.progress.setValue(0)
        self.progress_count.setText(f"0 / {self._batch_total}")
        self.result_label.setStyleSheet("color: #315fbd; font-weight: 700;")
        self.result_label.setText("生成を開始します…")
        self.result_destination.hide()
        self.open_result_folder_button.setEnabled(False)
        for row in range(self.result_tree.topLevelItemCount()):
            item = self.result_tree.topLevelItem(row)
            item.setText(2, "待機中")
            item.setToolTip(2, "")
            item.setForeground(2, QBrush(QColor("#182230")))

        self._set_processing(True)
        self._thread = QThread(self)
        self._worker = ThumbnailWorker(
            records,
            copy.deepcopy(self.settings()),
            self.output_folder,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.item_status.connect(self._on_item_status)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker_refs)
        self._thread.start()

    def _set_processing(self, processing: bool) -> None:
        self._processing = processing
        for widget in (
            self.titles_edit,
            self.generate_button,
            self.template_combo,
            self.template_save_button,
            self.template_update_button,
            self.template_delete_button,
            self.canvas_save_button,
            self.canvas_delete_button,
            self.canvas_preset_combo,
            self.background_button,
            self.font_color_button,
            self.folder_button,
            self.width_spin,
            self.height_spin,
            self.font_combo,
            self.font_size_spin,
            self.min_font_size_spin,
            self.bold_check,
            self.alignment_combo,
            self.vertical_alignment_combo,
            self.label_enabled,
            self.label_text,
            self.label_font,
            self.label_font_size,
            self.label_color_button,
            self.label_position,
            self.number_enabled,
            self.number_prefix,
            self.number_start,
            self.number_digits,
            self.number_font,
            self.number_font_size,
            self.number_color_button,
            self.number_position,
            self.margin_spin,
            self.max_lines_spin,
            self.format_combo,
            self.quality_spin,
        ):
            widget.setEnabled(not processing)
        if not processing:
            self._canvas_preset_changed()
            self._format_changed()
            self._overlay_visibility_changed()
            self.generate_button.setEnabled(bool(self.records()))
        self._update_template_actions()
        self.processing_changed.emit(processing)

    @Slot(int, str, str)
    def _on_item_status(self, row: int, status: str, detail: str) -> None:
        status_label = {
            "Processing": "生成中",
            "Done": "完了",
            "Error": "エラー",
        }.get(status, status)
        if 0 <= row < self.result_tree.topLevelItemCount():
            item = self.result_tree.topLevelItem(row)
            item.setText(2, status_label)
            item.setToolTip(2, detail)
            color = "#c62828" if status == "Error" else "#182230"
            item.setForeground(2, QBrush(QColor(color)))
        if status == "Processing":
            self.progress_count.setText(f"{row + 1} / {self._batch_total}")

    @Slot(int, int)
    def _on_finished(self, succeeded: int, failed: int) -> None:
        self.progress_count.setText(
            f"{self._batch_total} / {self._batch_total}"
        )
        if failed:
            self.result_label.setStyleSheet(
                "color: #9a6700; font-weight: 700;"
            )
            self.result_label.setText(
                f"{succeeded}枚生成 / {failed}件失敗\n"
                "失敗内容は一覧の状態欄へマウスを合わせて確認できます"
            )
        else:
            self.result_label.setStyleSheet(
                "color: #137333; font-weight: 700;"
            )
            self.result_label.setText(f"✓ {succeeded}枚生成しました")

        show_destination = (
            succeeded > 0 and self._last_generated_folder is not None
        )
        if show_destination:
            self.result_folder_label.set_path(self._last_generated_folder)
        self.open_result_folder_button.setEnabled(show_destination)
        self.result_destination.setVisible(show_destination)

    @Slot()
    def open_result_folder(self) -> None:
        folder = self._last_generated_folder
        if folder is None or not folder.is_dir():
            QMessageBox.warning(
                self,
                "保存先を開けません",
                "生成画像の保存先が見つかりません。",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(
                self,
                "保存先を開けません",
                "Windows Explorerで保存先を開けませんでした。",
            )

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self._set_processing(False)

    def save_state(self) -> None:
        try:
            selected = str(self.template_combo.currentData() or "")
            if selected:
                self.template_store.set_last_selected(selected)
            self.template_store.set_last_output_folder(self.output_folder)
        except ThumbnailStorageError as exc:
            LOGGER.warning("Thumbnail page state not saved: %s", exc)

    def can_close(self) -> bool:
        return self._thread is None
