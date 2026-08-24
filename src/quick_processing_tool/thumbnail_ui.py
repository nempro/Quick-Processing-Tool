from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import ProcessingError
from .thumbnail_models import (
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


class ThumbnailPreview(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))

    def set_image(self, image) -> None:
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self._fit()

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
        LOGGER.info("Thumbnail batch start: %s items; output=%s", len(self.records), self.output_folder)
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
                    f"{destination} · font {rendered.font_size}px · "
                    f"{rendered.line_count} lines"
                )
                succeeded += 1
                LOGGER.info("Thumbnail generated: %s", destination)
                self.item_status.emit(row, "Done", detail)
            except Exception as exc:  # One title must not stop the remaining batch.
                failed += 1
                LOGGER.exception("Thumbnail generation failed: %s", record.title)
                message = str(exc) if isinstance(exc, ProcessingError) else "サムネイル生成に失敗しました。"
                self.item_status.emit(row, "Error", message)
            self.progress.emit(round((row + 1) * 100 / total))
        LOGGER.info("Thumbnail batch result: %s generated; %s errors", succeeded, failed)
        self.finished.emit(succeeded, failed)


class ThumbnailPage(QWidget):
    processing_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        self.output_folder = Path(desktop or str(Path.home() / "Desktop")) / "Quick Processing Tool Thumbnails"
        self.preview_index = 0
        self._thread: QThread | None = None
        self._worker: ThumbnailWorker | None = None
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

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setMinimumWidth(285)
        settings_content = QWidget()
        settings_layout = QVBoxLayout(settings_content)
        settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        template_group = QGroupBox("Design")
        template_form = QFormLayout(template_group)
        self.template_combo = QComboBox()
        self.template_combo.addItem("Simple Dark")
        self.template_combo.setToolTip("Template Save / Load is planned for Phase 2B")
        template_form.addRow("Template", self.template_combo)
        settings_layout.addWidget(template_group)

        canvas_group = QGroupBox("Canvas / Solid Background")
        canvas_form = QFormLayout(canvas_group)
        self.width_spin = self._spin(128, 6000, 1080)
        self.height_spin = self._spin(128, 6000, 1080)
        self.background_button = QPushButton()
        self.background_button.clicked.connect(self._choose_background)
        self._update_color_button(self.background_button, self._background_color)
        canvas_form.addRow("Width", self.width_spin)
        canvas_form.addRow("Height", self.height_spin)
        canvas_form.addRow("Background", self.background_button)
        settings_layout.addWidget(canvas_group)

        text_group = QGroupBox("Main Title")
        text_form = QFormLayout(text_group)
        self.font_combo = QFontComboBox()
        families = QFontDatabase.families()
        preferred = next(
            (name for name in ("Yu Gothic UI", "Yu Gothic", "Meiryo", "Noto Sans CJK JP") if name in families),
            self.font_combo.currentFont().family(),
        )
        self.font_combo.setCurrentFont(self.font_combo.currentFont())
        index = self.font_combo.findText(preferred)
        if index >= 0:
            self.font_combo.setCurrentIndex(index)
        self.font_size_spin = self._spin(8, 500, 72)
        self.min_font_size_spin = self._spin(8, 500, 36)
        self.bold_check = QPushButton("Bold: On")
        self.bold_check.setCheckable(True)
        self.bold_check.setChecked(True)
        self.bold_check.toggled.connect(lambda value: self.bold_check.setText(f"Bold: {'On' if value else 'Off'}"))
        self.font_color_button = QPushButton()
        self.font_color_button.clicked.connect(self._choose_font_color)
        self._update_color_button(self.font_color_button, self._font_color)
        self.alignment_combo = QComboBox()
        for alignment in TextAlignment:
            self.alignment_combo.addItem(alignment.value, alignment.value)
        self.alignment_combo.setCurrentText(TextAlignment.CENTER.value)
        self.margin_spin = self._spin(0, 2000, 100)
        self.max_lines_spin = self._spin(1, 20, 5)
        text_form.addRow("Installed font", self.font_combo)
        text_form.addRow("Base size", self.font_size_spin)
        text_form.addRow("Minimum size", self.min_font_size_spin)
        text_form.addRow("", self.bold_check)
        text_form.addRow("Color", self.font_color_button)
        text_form.addRow("Alignment", self.alignment_combo)
        text_form.addRow("Text margin", self.margin_spin)
        text_form.addRow("Maximum lines", self.max_lines_spin)
        settings_layout.addWidget(text_group)

        export_group = QGroupBox("Batch Export")
        export_form = QFormLayout(export_group)
        self.format_combo = QComboBox()
        for output_format in ThumbnailFormat:
            self.format_combo.addItem(output_format.value, output_format.value)
        self.format_combo.setCurrentText(ThumbnailFormat.JPEG.value)
        self.quality_spin = self._spin(1, 100, 90)
        self.folder_label = QLabel(str(self.output_folder))
        self.folder_label.setWordWrap(True)
        self.folder_button = QPushButton("Choose folder…")
        self.folder_button.clicked.connect(self.choose_output_folder)
        export_form.addRow("Format", self.format_combo)
        export_form.addRow("JPEG quality", self.quality_spin)
        export_form.addRow("Output", self.folder_label)
        export_form.addRow("", self.folder_button)
        settings_layout.addWidget(export_group)
        settings_scroll.setWidget(settings_content)
        splitter.addWidget(settings_scroll)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.preview = ThumbnailPreview()
        center_layout.addWidget(self.preview, 1)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("‹")
        self.previous_button.clicked.connect(lambda: self._move_preview(-1))
        self.preview_position = QLabel("Preview")
        self.preview_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = QPushButton("›")
        self.next_button.clicked.connect(lambda: self._move_preview(1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.preview_position, 1)
        navigation.addWidget(self.next_button)
        center_layout.addLayout(navigation)
        self.preview_status = QLabel("")
        self.preview_status.setWordWrap(True)
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.preview_status)
        splitter.addWidget(center)

        batch = QWidget()
        batch_layout = QVBoxLayout(batch)
        batch_layout.addWidget(QLabel("Titles · 1 line = 1 image"))
        self.titles_edit = QPlainTextEdit()
        self.titles_edit.setPlaceholderText(
            "夜中寝てたら年下彼氏に急に襲われて\n"
            "嫉妬した彼に問い詰められて\n"
            "眠れない夜にずっと囁かれて"
        )
        self.titles_edit.textChanged.connect(self._titles_changed)
        batch_layout.addWidget(self.titles_edit, 1)
        self.title_count = QLabel("0 titles")
        batch_layout.addWidget(self.title_count)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["#", "Title", "Status"])
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setMinimumHeight(180)
        batch_layout.addWidget(self.result_tree, 1)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        batch_layout.addWidget(self.progress)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        batch_layout.addWidget(self.result_label)
        self.generate_button = QPushButton("Generate All")
        self.generate_button.clicked.connect(self.generate_all)
        batch_layout.addWidget(self.generate_button)
        splitter.addWidget(batch)

        splitter.setSizes([300, 560, 380])
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        for spin in (
            self.width_spin,
            self.height_spin,
            self.font_size_spin,
            self.min_font_size_spin,
            self.margin_spin,
            self.max_lines_spin,
            self.quality_spin,
        ):
            spin.valueChanged.connect(self.schedule_preview)
        self.font_combo.currentFontChanged.connect(self.schedule_preview)
        self.bold_check.toggled.connect(self.schedule_preview)
        self.alignment_combo.currentTextChanged.connect(self.schedule_preview)
        self.format_combo.currentTextChanged.connect(self._format_changed)
        self._format_changed(self.format_combo.currentText())

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    @staticmethod
    def _update_color_button(button: QPushButton, color: QColor) -> None:
        contrast = "#000000" if color.lightness() > 150 else "#FFFFFF"
        button.setText(color.name().upper())
        button.setStyleSheet(f"background: {color.name()}; color: {contrast};")

    def _choose_background(self) -> None:
        color = QColorDialog.getColor(self._background_color, self, "Solid background")
        if color.isValid():
            self._background_color = color
            self._update_color_button(self.background_button, color)
            self.schedule_preview()

    def _choose_font_color(self) -> None:
        color = QColorDialog.getColor(self._font_color, self, "Font color")
        if color.isValid():
            self._font_color = color
            self._update_color_button(self.font_color_button, color)
            self.schedule_preview()

    @Slot(str)
    def _format_changed(self, value: str) -> None:
        self.quality_spin.setEnabled(value == ThumbnailFormat.JPEG.value)
        self.schedule_preview()

    def records(self) -> list[TitleRecord]:
        return parse_title_records(self.titles_edit.toPlainText())

    def settings(self) -> ThumbnailSettings:
        minimum = min(self.font_size_spin.value(), self.min_font_size_spin.value())
        return ThumbnailSettings(
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            background_color=self._background_color.name(),
            font_family=self.font_combo.currentFont().family(),
            font_size=self.font_size_spin.value(),
            min_font_size=minimum,
            font_color=self._font_color.name(),
            bold=self.bold_check.isChecked(),
            alignment=TextAlignment(self.alignment_combo.currentText()),
            margin=self.margin_spin.value(),
            max_lines=self.max_lines_spin.value(),
            output_format=ThumbnailFormat(self.format_combo.currentText()),
            quality=self.quality_spin.value(),
        )

    @Slot()
    def _titles_changed(self) -> None:
        records = self.records()
        self.title_count.setText(f"{len(records)} titles")
        self.preview_index = min(self.preview_index, max(0, len(records) - 1))
        self.result_tree.clear()
        for record in records:
            item = QTreeWidgetItem([str(record.index), record.title, "Waiting"])
            self.result_tree.addTopLevelItem(item)
        self.generate_button.setText(f"Generate {len(records)} images" if records else "Generate All")
        self.schedule_preview()

    @Slot()
    def schedule_preview(self) -> None:
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
        title = records[self.preview_index].title if records else "タイトルプレビュー"
        total = len(records)
        self.preview_position.setText(
            f"{self.preview_index + 1} / {total}" if total else "Preview"
        )
        self.previous_button.setEnabled(total > 1)
        self.next_button.setEnabled(total > 1)
        try:
            rendered = render_thumbnail(title, self.settings())
            self.preview.set_image(rendered.image)
            self.preview_status.setStyleSheet("color: #5f6368;")
            self.preview_status.setText(
                f"{rendered.image.width()} × {rendered.image.height()} · "
                f"font {rendered.font_size}px · {rendered.line_count} lines"
            )
        except ThumbnailLayoutError as exc:
            self.preview_status.setStyleSheet("color: #c62828;")
            self.preview_status.setText(str(exc))

    @Slot()
    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose thumbnail output folder", str(self.output_folder))
        if folder:
            self.output_folder = Path(folder)
            self.folder_label.setText(str(self.output_folder))

    @Slot()
    def generate_all(self) -> None:
        records = self.records()
        if not records:
            QMessageBox.information(self, "No titles", "1行に1件ずつタイトルを入力してください。")
            return
        if self._thread is not None:
            return
        self.progress.setValue(0)
        self.result_label.setText("")
        self._set_processing(True)
        self._thread = QThread(self)
        self._worker = ThumbnailWorker(records, copy.deepcopy(self.settings()), self.output_folder)
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
        for widget in (
            self.titles_edit,
            self.generate_button,
            self.template_combo,
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
        self.processing_changed.emit(processing)

    @Slot(int, str, str)
    def _on_item_status(self, row: int, status: str, detail: str) -> None:
        if 0 <= row < self.result_tree.topLevelItemCount():
            item = self.result_tree.topLevelItem(row)
            item.setText(2, status)
            item.setToolTip(2, detail)

    @Slot(int, int)
    def _on_finished(self, succeeded: int, failed: int) -> None:
        self.result_label.setText(f"{succeeded} generated · {failed} errors")

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None
        self._thread = None
        self._set_processing(False)

    def can_close(self) -> bool:
        return self._thread is None