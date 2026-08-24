from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFontDatabase, QPainter, QPixmap
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
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
    CANVAS_PRESETS,
    CanvasPreset,
    TextAlignment,
    ThumbnailFormat,
    ThumbnailSettings,
    TitleRecord,
    parse_title_records,
)
from .thumbnail_renderer import (
    ThumbnailLayoutError,
    render_record,
    render_thumbnail,
    write_thumbnail_output,
)


LOGGER = logging.getLogger(__name__)
CUSTOM_CANVAS_LABEL = "任意サイズ"
ALIGNMENT_LABELS = {
    TextAlignment.LEFT: "左揃え",
    TextAlignment.CENTER: "中央揃え",
    TextAlignment.RIGHT: "右揃え",
}
THUMBNAIL_STYLE = """
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
QComboBox, QSpinBox {
    background-color: #e9eef4;
    color: #182230;
    border: 1px solid #8d99a8;
    border-radius: 6px;
    padding: 5px 28px 5px 8px;
    min-height: 28px;
    selection-background-color: #315fbd;
    selection-color: #ffffff;
}
QSpinBox {
    padding-right: 22px;
}
QComboBox:hover, QSpinBox:hover {
    background-color: #e2eaf3;
    border-color: #617389;
}
QComboBox:focus, QSpinBox:focus {
    background-color: #ffffff;
    border: 2px solid #315fbd;
    padding: 4px 27px 4px 7px;
}
QSpinBox:focus {
    padding-right: 21px;
}
QComboBox:disabled, QSpinBox:disabled {
    background-color: #f3f4f6;
    color: #9aa1aa;
    border: 1px solid #cfd4da;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #182230;
    border: 1px solid #7a8796;
    selection-background-color: #dce7ff;
    selection-color: #182230;
    outline: 0;
}
QPlainTextEdit {
    background-color: #f7f9fc;
    color: #182230;
    border: 2px solid #7e8da0;
    border-radius: 8px;
    padding: 10px;
    font-size: 14px;
    selection-background-color: #315fbd;
    selection-color: #ffffff;
}
QPlainTextEdit:hover {
    background-color: #f2f6fb;
    border-color: #5f7187;
}
QPlainTextEdit:focus {
    background-color: #ffffff;
    border: 2px solid #315fbd;
}
QPlainTextEdit:disabled {
    background-color: #f3f4f6;
    color: #9aa1aa;
    border-color: #cfd4da;
}
QPushButton#generateButton {
    min-height: 44px;
    background-color: #315fbd;
    color: #ffffff;
    border: 0;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton#generateButton:hover {
    background-color: #284fa1;
}
QPushButton#generateButton:pressed {
    background-color: #203f82;
}
QPushButton#generateButton:disabled {
    background-color: #d9dee6;
    color: #8f98a6;
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
            "border: 0; background: #eef2f7; border-radius: 6px; }"
            "QToolButton:hover { background: #e3eaf2; }"
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

    def __init__(self) -> None:
        super().__init__()
        desktop = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
        self.output_folder = (
            Path(desktop or str(Path.home() / "Desktop"))
            / "Quick Processing Tool Thumbnails"
        )
        self.preview_index = 0
        self._thread: QThread | None = None
        self._worker: ThumbnailWorker | None = None
        self._processing = False
        self._batch_total = 0
        self._background_color = QColor("#171923")
        self._font_color = QColor("#FFFFFF")
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        self._titles_changed()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("thumbnail_workspace")
        splitter.setStyleSheet(THUMBNAIL_STYLE)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setMinimumWidth(300)
        settings_scroll.setMaximumWidth(360)
        settings_content = QWidget()
        settings_layout = QVBoxLayout(settings_content)
        settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        settings_layout.setSpacing(10)

        settings_heading = QLabel("サイズと見た目")
        settings_heading.setStyleSheet(
            "font-size: 20px; font-weight: 750; color: #182230;"
        )
        settings_intro = QLabel(
            "代表1枚のプレビューを見ながら共通デザインを設定します"
        )
        settings_intro.setWordWrap(True)
        settings_intro.setStyleSheet("color: #667085;")
        settings_layout.addWidget(settings_heading)
        settings_layout.addWidget(settings_intro)

        canvas_group = QGroupBox("2. サイズ")
        self.canvas_form = QFormLayout(canvas_group)
        self.canvas_preset_combo = QComboBox()
        for preset in CANVAS_PRESETS:
            self.canvas_preset_combo.addItem(
                f"{preset.name}　{preset.width} × {preset.height}",
                preset,
            )
        self.canvas_preset_combo.addItem(CUSTOM_CANVAS_LABEL, None)
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
        settings_layout.addWidget(canvas_group)

        appearance_group = QGroupBox("3. 見た目")
        appearance_layout = QVBoxLayout(appearance_group)
        appearance_form = QFormLayout()
        self.background_button = QPushButton()
        self.background_button.clicked.connect(self._choose_background)
        self._update_color_button(self.background_button, self._background_color)
        appearance_form.addRow("背景色", self.background_button)

        self.font_combo = QFontComboBox()
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
        for alignment in TextAlignment:
            self.alignment_combo.addItem(ALIGNMENT_LABELS[alignment], alignment.value)
        self.alignment_combo.setCurrentIndex(
            self.alignment_combo.findData(TextAlignment.CENTER.value)
        )
        appearance_form.addRow("横位置", self.alignment_combo)

        self.bold_check = QCheckBox("太字にする")
        self.bold_check.setChecked(True)
        appearance_form.addRow("", self.bold_check)
        appearance_layout.addLayout(appearance_form)

        details_content = QWidget()
        details_form = QFormLayout(details_content)
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
        settings_layout.addWidget(appearance_group)

        export_group = QGroupBox("4. 保存")
        self.export_form = QFormLayout(export_group)
        self.format_combo = QComboBox()
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
        self.folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.folder_button = QPushButton("保存先を選ぶ…")
        self.folder_button.clicked.connect(self.choose_output_folder)
        self.export_form.addRow("保存先", self.folder_label)
        self.export_form.addRow("", self.folder_button)
        settings_layout.addWidget(export_group)
        settings_layout.addStretch(1)

        settings_scroll.setWidget(settings_content)
        splitter.addWidget(settings_scroll)

        center = QWidget()
        center.setMinimumWidth(420)
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
        batch.setMinimumWidth(350)
        batch_layout = QVBoxLayout(batch)
        batch_layout.setContentsMargins(8, 8, 8, 8)
        batch_layout.setSpacing(8)

        titles_heading = QLabel("1. タイトル")
        titles_heading.setStyleSheet(
            "font-size: 20px; font-weight: 750; color: #182230;"
        )
        batch_layout.addWidget(titles_heading)
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
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([330, 600, 390])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

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
        ):
            spin.valueChanged.connect(self.schedule_preview)
        self.font_combo.currentFontChanged.connect(self.schedule_preview)
        self.bold_check.toggled.connect(self.schedule_preview)
        self.alignment_combo.currentIndexChanged.connect(self.schedule_preview)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self._canvas_preset_changed()
        self._format_changed()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
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
            f"background: {color.name()}; color: {contrast};"
            "border: 1px solid #7d8998; border-radius: 6px;"
            "min-height: 30px; font-weight: 700;"
        )

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
            self.schedule_preview()

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
            self.schedule_preview()

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
        self._update_canvas_size_label()
        self.schedule_preview()

    @Slot()
    def _canvas_dimension_changed(self, *_args) -> None:
        self._update_canvas_size_label()
        self.schedule_preview()

    def _update_canvas_size_label(self) -> None:
        self.canvas_size_label.setText(
            f"{self.width_spin.value()} × {self.height_spin.value()} px"
        )

    @Slot()
    def _format_changed(self, *_args) -> None:
        jpeg = self.format_combo.currentData() == ThumbnailFormat.JPEG.value
        self.quality_spin.setEnabled(jpeg and not self._processing)
        self.schedule_preview()

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
            alignment=TextAlignment(self.alignment_combo.currentData()),
            margin=self.margin_spin.value(),
            max_lines=self.max_lines_spin.value(),
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
        self.result_label.clear()
        self.schedule_preview()

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
            rendered = render_thumbnail(record.title, self.settings())
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
        self.progress.setValue(0)
        self.progress_count.setText(f"0 / {self._batch_total}")
        self.result_label.setText("生成を開始します…")
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
            self.margin_spin,
            self.max_lines_spin,
            self.format_combo,
            self.quality_spin,
        ):
            widget.setEnabled(not processing)
        if not processing:
            self._canvas_preset_changed()
            self._format_changed()
            self.generate_button.setEnabled(bool(self.records()))
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
        self.result_label.setText(
            f"{succeeded}枚生成 / {failed}件失敗"
            + (
                "\n失敗内容は一覧の状態欄へ"
                "マウスを合わせて確認できます"
                if failed
                else ""
            )
        )

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self._set_processing(False)

    def can_close(self) -> bool:
        return self._thread is None
