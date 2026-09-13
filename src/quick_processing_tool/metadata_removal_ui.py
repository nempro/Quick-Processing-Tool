from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import ProcessingError
from .metadata_removal import (
    collect_supported_image_paths,
    metadata_output_folder,
    remove_image_metadata,
)
from .ui_styles import set_operation_role


LOGGER = logging.getLogger(__name__)


class MetadataDropArea(QFrame):
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("metadata_drop_area")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(72)
        self.setStyleSheet(
            "QFrame#metadata_drop_area { background: #f6f9ff; border: 2px dashed #7b9bd1;"
            "border-radius: 9px; }"
            "QFrame#metadata_drop_area:hover { background: #eef4ff; border-color: #315fbd; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel("画像またはフォルダーをここにドロップ")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight: 700; color: #244b99;")
        layout.addWidget(label)
        note = QLabel("PNG / JPEG / WebP · フォルダーは直下の画像だけ追加します")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setStyleSheet("color: #667085; font-size: 12px;")
        layout.addWidget(note)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()


class MetadataRemovalWorker(QObject):
    progress = Signal(int)
    file_status = Signal(int, str, str)
    folder_saved = Signal(object)
    finished = Signal(int, int, bool)

    def __init__(
        self,
        paths: list[Path],
        destination_mode: str,
        custom_folder: Path | None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.destination_mode = destination_mode
        self.custom_folder = custom_folder
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        succeeded = failed = 0
        cancelled = False
        total = max(1, len(self.paths))
        for index, source in enumerate(self.paths):
            if self._cancel_requested:
                cancelled = True
                for remaining in range(index, len(self.paths)):
                    self.file_status.emit(remaining, "キャンセル", "処理されませんでした")
                break
            self.file_status.emit(index, "処理中", "")
            try:
                folder = metadata_output_folder(
                    source,
                    self.destination_mode,
                    self.custom_folder,
                )
                result = remove_image_metadata(source, folder)
                self.folder_saved.emit(result.output.parent)
                self.file_status.emit(index, "完了", str(result.output))
                succeeded += 1
            except Exception as exc:  # Isolate each failed file in a batch.
                failed += 1
                LOGGER.exception("Metadata removal failed: %s", source)
                detail = str(exc) if isinstance(exc, ProcessingError) else "画像を処理できませんでした"
                self.file_status.emit(index, "失敗", detail)
            self.progress.emit(round((index + 1) * 100 / total))
        self.finished.emit(succeeded, failed, cancelled)


class MetadataRemovalDialog(QDialog):
    """A focused batch workflow for safe, metadata-free image copies."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("メタ情報を一括削除")
        self.setModal(False)
        self.setMinimumSize(680, 440)
        self.resize(840, 560)
        self.setStyleSheet("QDialog { background: #ffffff; } QTreeWidget { border: 1px solid #cbd5e1; border-radius: 7px; }")
        self.paths: list[Path] = []
        self.custom_folder: Path | None = None
        self._thread: QThread | None = None
        self._worker: MetadataRemovalWorker | None = None
        self._last_output_folders: set[Path] = set()
        self._build_ui()
        self._refresh_queue()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        heading = QLabel("メタ情報を一括削除")
        heading.setStyleSheet("font-size: 21px; font-weight: 750; color: #182230;")
        layout.addWidget(heading)
        intro = QLabel("EXIF・GPS・生成パラメーター・コメントなどを削除し、元画像を残したまま別ファイルとして保存します。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #5d6878;")
        layout.addWidget(intro)
        self.drop_area = MetadataDropArea()
        self.drop_area.paths_dropped.connect(self.add_paths)
        layout.addWidget(self.drop_area)

        queue_row = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setStyleSheet("font-weight: 700; color: #344054;")
        queue_row.addWidget(self.count_label, 1)
        self.add_button = QPushButton("画像を追加")
        self.add_button.clicked.connect(self.choose_images)
        self.remove_button = QPushButton("選択を削除")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("一覧をクリア")
        self.clear_button.clicked.connect(self.clear_queue)
        for button in (self.add_button, self.remove_button, self.clear_button):
            set_operation_role(button, "secondary")
            queue_row.addWidget(button)
        layout.addLayout(queue_row)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["ファイル名", "形式", "状態"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        header = self.file_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.file_tree.setColumnWidth(1, 72)
        self.file_tree.setColumnWidth(2, 116)
        layout.addWidget(self.file_tree, 1)

        destination = QGroupBox("保存先")
        destination_layout = QHBoxLayout(destination)
        self.destination_group = QButtonGroup(self)
        self.same_folder_radio = QRadioButton("元画像と同じ場所")
        self.processed_radio = QRadioButton("Processedフォルダー")
        self.custom_folder_radio = QRadioButton("指定フォルダー")
        self.processed_radio.setChecked(True)
        for button in (self.same_folder_radio, self.processed_radio, self.custom_folder_radio):
            self.destination_group.addButton(button)
            destination_layout.addWidget(button)
        self.choose_folder_button = QPushButton("選ぶ…")
        self.choose_folder_button.clicked.connect(self.choose_folder)
        set_operation_role(self.choose_folder_button, "secondary")
        destination_layout.addWidget(self.choose_folder_button)
        self.source_protection = QLabel("✓ 元画像は変更しません")
        self.source_protection.setStyleSheet("color: #185c2b; font-weight: 700;")
        destination_layout.addWidget(self.source_protection)
        layout.addWidget(destination)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        result_row = QHBoxLayout()
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        result_row.addWidget(self.result_label, 1)
        self.open_folder_button = QPushButton("保存先を開く")
        self.open_folder_button.clicked.connect(self.open_saved_folder)
        self.open_folder_button.setEnabled(False)
        set_operation_role(self.open_folder_button, "secondary")
        result_row.addWidget(self.open_folder_button)
        layout.addLayout(result_row)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self.cancel_or_close)
        set_operation_role(self.cancel_button, "secondary")
        self.run_button = QPushButton()
        self.run_button.clicked.connect(self.start_processing)
        set_operation_role(self.run_button, "primary")
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.run_button)
        layout.addLayout(bottom)

    def _destination_mode(self) -> str:
        if self.same_folder_radio.isChecked():
            return "Same folder"
        if self.custom_folder_radio.isChecked():
            return "Custom folder"
        return "Processed"

    @Slot(list)
    def add_paths(self, raw_paths: list[Path]) -> None:
        if self._thread is not None:
            return
        existing = {str(path).casefold() for path in self.paths}
        for path in collect_supported_image_paths(raw_paths):
            if str(path).casefold() not in existing:
                self.paths.append(path)
                existing.add(str(path).casefold())
        self._refresh_queue()

    @Slot()
    def choose_images(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "メタ情報を削除する画像を選ぶ",
            "",
            "画像 (*.png *.jpg *.jpeg *.webp)",
        )
        if names:
            self.add_paths([Path(name) for name in names])

    @Slot()
    def choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "保存先フォルダーを選ぶ",
            str(self.custom_folder or Path.home()),
        )
        if selected:
            self.custom_folder = Path(selected)
            self.custom_folder_radio.setChecked(True)
            self.result_label.setText(f"保存先: {self.custom_folder}")

    @Slot()
    def remove_selected(self) -> None:
        rows = sorted({self.file_tree.indexOfTopLevelItem(item) for item in self.file_tree.selectedItems()}, reverse=True)
        for row in rows:
            if row >= 0:
                del self.paths[row]
        self._refresh_queue()

    @Slot()
    def clear_queue(self) -> None:
        if self._thread is None:
            self.paths.clear()
            self._refresh_queue()

    def _refresh_queue(self) -> None:
        self.file_tree.clear()
        for path in self.paths:
            image_format = "JPEG" if path.suffix.lower() in {".jpg", ".jpeg"} else path.suffix[1:].upper()
            self.file_tree.addTopLevelItem(QTreeWidgetItem([path.name, image_format, "待機中"]))
        count = len(self.paths)
        self.count_label.setText(f"読み込み画像: {count}枚")
        self.run_button.setText(f"{count}枚のメタ情報を削除" if count else "画像を追加してください")
        self.run_button.setEnabled(bool(count) and self._thread is None)
        self.remove_button.setEnabled(bool(count) and self._thread is None)
        self.clear_button.setEnabled(bool(count) and self._thread is None)
        self.add_button.setEnabled(self._thread is None)
        self.progress.setValue(0)
        if self._thread is None:
            self.result_label.clear()
            self.open_folder_button.setEnabled(False)

    @Slot()
    def start_processing(self) -> None:
        if not self.paths or self._thread is not None:
            return
        if self._destination_mode() == "Custom folder" and self.custom_folder is None:
            QMessageBox.warning(self, "保存先を選んでください", "指定フォルダーを選ぶか、別の保存先を選択してください。")
            return
        self._last_output_folders.clear()
        self.progress.setValue(0)
        self.result_label.setStyleSheet("color: #315fbd; font-weight: 700;")
        self.result_label.setText("メタ情報を削除しています…")
        self.open_folder_button.setEnabled(False)
        for row in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(row)
            item.setText(2, "待機中")
            item.setToolTip(2, "")
        self._set_processing(True)
        self._thread = QThread(self)
        self._worker = MetadataRemovalWorker(
            list(self.paths), self._destination_mode(), self.custom_folder
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.file_status.connect(self._on_file_status)
        self._worker.folder_saved.connect(self._on_folder_saved)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker_references)
        self._thread.start()

    @Slot(int, str, str)
    def _on_file_status(self, row: int, status: str, detail: str) -> None:
        item = self.file_tree.topLevelItem(row)
        if item is None:
            return
        item.setText(2, status)
        item.setToolTip(2, detail)
        color = "#185c2b" if status == "完了" else "#b42318" if status == "失敗" else "#667085"
        item.setForeground(2, QBrush(QColor(color)))

    @Slot(int, int, bool)
    def _on_finished(self, succeeded: int, failed: int, cancelled: bool) -> None:
        message = f"処理完了: {succeeded} / {len(self.paths)} 成功"
        if failed:
            message += f" · {failed}件失敗"
        if cancelled:
            message += " · キャンセルしました"
        self.result_label.setText(message)
        self.result_label.setStyleSheet(
            "color: #b42318; font-weight: 700;" if failed else "color: #185c2b; font-weight: 700;"
        )
        self.open_folder_button.setEnabled(bool(self._last_output_folders))
        self._set_processing(False)

    @Slot(object)
    def _on_folder_saved(self, folder: Path) -> None:
        self._last_output_folders.add(Path(folder))

    def _set_processing(self, processing: bool) -> None:
        for widget in (
            self.add_button, self.remove_button, self.clear_button,
            self.same_folder_radio, self.processed_radio, self.custom_folder_radio,
            self.choose_folder_button, self.run_button,
        ):
            widget.setEnabled(not processing)
        self.cancel_button.setText("中止" if processing else "閉じる")

    @Slot()
    def cancel_or_close(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.result_label.setText("現在の画像の処理後に中止します…")
            return
        self.close()

    @Slot()
    def open_saved_folder(self) -> None:
        if not self._last_output_folders:
            return
        folder = sorted(self._last_output_folders, key=str)[0]
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "保存先を開けません", "保存先フォルダーを開けませんでした。")

    @Slot()
    def _clear_worker_references(self) -> None:
        self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread is not None:
            event.ignore()
            self.cancel_or_close()
            return
        super().closeEvent(event)
