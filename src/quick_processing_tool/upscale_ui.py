from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from threading import Event

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QStandardPaths, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QFrame, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QSizePolicy, QSplitter, QStackedLayout,
    QVBoxLayout, QWidget, QComboBox,
)

from .naming import write_unique_bytes
from .ui_styles import INPUT_CONTROL_STYLE
from .upscaler import RealESRGANNCNNBackend, UpscaleMode, UpscaleOptions, UpscaleOutputFormat, UpscaleResult, UpscaleService
from .upscaler.errors import (
    BackendNotFoundError, GPUUnavailableError, InputDecodeError, ModelNotFoundError,
    UpscaleCancelledError, UpscaleError, UpscaleProcessingError, UpscaleSaveError,
)


LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ERROR_MESSAGES = {
    BackendNotFoundError: "高画質化エンジンが準備されていません。",
    ModelNotFoundError: "高画質化モデルが見つかりません。",
    GPUUnavailableError: "この環境では高画質化エンジンを利用できません。",
    InputDecodeError: "画像を読み込めませんでした。",
    UpscaleProcessingError: "高画質化処理に失敗しました。",
    UpscaleSaveError: "高画質化した画像を保存できませんでした。",
}
UPSCALE_STYLE = INPUT_CONTROL_STYLE + """
QGroupBox { font-size: 14px; font-weight: 700; border: 1px solid #d7dde5;
 border-radius: 8px; margin-top: 12px; padding: 12px 8px 8px; background: #fbfcfd; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #182230; }
QPushButton { min-height: 30px; background: #f5f7fa; color: #182230;
 border: 1px solid #7b899a; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #e5edf6; border-color: #405b79; }
QPushButton:focus { background: white; border: 2px solid #2457b2; padding: 4px 9px; }
QPushButton:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }
QPushButton#upscaleStart { min-height: 46px; background: #315fbd; color: white;
 border: 1px solid #315fbd; border-radius: 8px; font-size: 15px; font-weight: 700; }
QPushButton#upscaleStart:hover { background: #284fa1; }
QPushButton#upscaleStart:disabled { background: #d9dee6; color: #8f98a6; border-color: #d9dee6; }
"""


class UpscalePreview(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202124"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fit_mode = True

    def set_image_path(self, path: Path) -> bool:
        image = QImage(str(path))
        if image.isNull():
            return False
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self.apply_zoom()
        return True

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())

    def set_fit(self, fit: bool) -> None:
        self._fit_mode = fit
        self.apply_zoom()

    def apply_zoom(self) -> None:
        self.resetTransform()
        if self._item.pixmap().isNull():
            return
        if self._fit_mode:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self.apply_zoom()


class UpscaleDropZone(QWidget):
    choose_requested = Signal()
    path_dropped = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(380, 340)
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.preview = UpscalePreview()
        self.preview.setAcceptDrops(False)
        self.preview.viewport().setAcceptDrops(False)
        self._stack.addWidget(self.preview)
        self.overlay = QFrame()
        self.overlay.setObjectName("upscaleDropOverlay")
        box = QVBoxLayout(self.overlay)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("▧")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 42px; color: #315fbd;")
        self.title = QLabel("画像をここにドロップ")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 21px; font-weight: 700; color: #182230;")
        formats = QLabel("PNG / JPEG / WebP")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats.setStyleSheet("color: #667085; font-size: 14px;")
        self.choose = QPushButton("画像を選ぶ")
        self.choose.setFixedWidth(150)
        self.choose.clicked.connect(self.choose_requested)
        box.addStretch(); box.addWidget(icon); box.addWidget(self.title); box.addWidget(formats)
        box.addSpacing(8); box.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignCenter); box.addStretch()
        self._stack.addWidget(self.overlay)
        self.set_empty(True)

    def set_empty(self, empty: bool) -> None:
        self.overlay.setVisible(empty)
        self._empty = empty
        self._stack.setCurrentWidget(self.overlay if empty else self.preview)
        self.set_drag_active(False)

    def set_drag_active(self, active: bool) -> None:
        self.overlay.setVisible(active or self._empty)
        self._stack.setCurrentWidget(self.overlay if active or self._empty else self.preview)
        self.title.setText("ここにドロップして画像を読み込み" if active else "画像をここにドロップ")
        self.overlay.setStyleSheet(
            "QFrame#upscaleDropOverlay { background: rgba(226,237,255,245); border: 3px dashed #2457b2; border-radius: 12px; }"
            if active else
            "QFrame#upscaleDropOverlay { background: #f8fafc; border: 2px dashed #8da2b8; border-radius: 12px; }"
        )

    @staticmethod
    def _path(event) -> Path | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile()) if url.isLocalFile() else None
            if path and path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.isEnabled() and self._path(event):
            self.set_drag_active(True); event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and self._path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False); event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._path(event)
        self.set_drag_active(False)
        if path and self.isEnabled():
            self.path_dropped.emit(path); event.acceptProposedAction()
        else:
            event.ignore()


class UpscaleWorker(QObject):
    progress = Signal(int)
    succeeded = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, service: UpscaleService, source: Path, folder: Path, options: UpscaleOptions, cancel_event: Event) -> None:
        super().__init__()
        self.service, self.source, self.folder, self.options = service, source, folder, options
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.run(self.source, self.folder, self.options, self.progress.emit, self.cancel_event)
            self.succeeded.emit(result)
        except UpscaleCancelledError:
            self.cancelled.emit()
        except UpscaleError as exc:
            LOGGER.exception("Upscale failed: %s", self.source)
            message = next((text for kind, text in ERROR_MESSAGES.items() if isinstance(exc, kind)), str(exc))
            self.failed.emit(type(exc).__name__, message)
        except Exception:
            LOGGER.exception("Unexpected upscale failure: %s", self.source)
            self.failed.emit("UnexpectedError", "高画質化処理に失敗しました。")
        finally:
            self.finished.emit()


class ElidedPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__(); self._path = ""; self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_path(self, path: Path | None) -> None:
        self._path = str(path or ""); self.setToolTip(self._path); self._update()

    def _update(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._path, Qt.TextElideMode.ElideMiddle, max(80, self.width() - 4)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event); self._update()


class UpscalePage(QWidget):
    processing_changed = Signal(bool)

    def __init__(self, service: UpscaleService | None = None) -> None:
        super().__init__()
        self.service = service or UpscaleService(RealESRGANNCNNBackend())
        self.source_path: Path | None = None
        self.output_folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation))
        self.result: UpscaleResult | None = None
        self._result_temp: tempfile.TemporaryDirectory[str] | None = None
        self._thread: QThread | None = None
        self._worker: UpscaleWorker | None = None
        self._cancel_event: Event | None = None
        self._build_ui()
        self._update_availability()

    def _build_ui(self) -> None:
        self.setStyleSheet(UPSCALE_STYLE)
        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setObjectName("upscaleWorkspace")
        left = QWidget(); left.setMinimumWidth(250); left.setMaximumWidth(330)
        ll = QVBoxLayout(left); ll.setContentsMargins(12, 12, 8, 12)
        heading = QLabel("高画質化設定"); heading.setStyleSheet("font-size: 18px; font-weight: 700;"); ll.addWidget(heading)
        scale_box = QGroupBox("拡大倍率"); scale_layout = QHBoxLayout(scale_box)
        self.scale_2 = QRadioButton("2倍"); self.scale_4 = QRadioButton("4倍"); self.scale_2.setChecked(True)
        scale_layout.addWidget(self.scale_2); scale_layout.addWidget(self.scale_4); ll.addWidget(scale_box)
        mode_box = QGroupBox("画像タイプ"); mode_layout = QHBoxLayout(mode_box)
        self.illustration = QRadioButton("イラスト"); self.photo = QRadioButton("写真"); self.illustration.setChecked(True)
        mode_layout.addWidget(self.illustration); mode_layout.addWidget(self.photo); ll.addWidget(mode_box)
        output_box = QGroupBox("保存"); form = QFormLayout(output_box)
        self.format_combo = QComboBox()
        for label, value in (("元の形式", "same"), ("PNG", "PNG"), ("JPEG", "JPEG"), ("WebP", "WEBP")):
            self.format_combo.addItem(label, value)
        self.folder_label = ElidedPathLabel(); self.folder_label.set_path(self.output_folder)
        self.folder_button = QPushButton("保存先を選ぶ"); self.folder_button.clicked.connect(self.choose_folder)
        form.addRow("保存形式", self.format_combo); form.addRow("保存先", self.folder_label); form.addRow("", self.folder_button); ll.addWidget(output_box)
        self.start_button = QPushButton("画像を選んでください"); self.start_button.setObjectName("upscaleStart"); self.start_button.setEnabled(False); self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("キャンセル"); self.cancel_button.hide(); self.cancel_button.clicked.connect(self.cancel)
        ll.addWidget(self.start_button); ll.addWidget(self.cancel_button); ll.addStretch(); splitter.addWidget(left)

        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(8, 12, 8, 12)
        preview_head = QHBoxLayout(); preview_head.addWidget(QLabel("プレビュー")); preview_head.addStretch()
        self.before_button = QPushButton("元画像"); self.after_button = QPushButton("高画質化後"); self.after_button.setEnabled(False)
        self.fit_button = QPushButton("全体表示"); self.actual_button = QPushButton("100%")
        for button in (self.before_button, self.after_button, self.fit_button, self.actual_button): preview_head.addWidget(button)
        self.before_button.clicked.connect(self.show_before); self.after_button.clicked.connect(self.show_after)
        self.fit_button.clicked.connect(lambda: self.drop_zone.preview.set_fit(True)); self.actual_button.clicked.connect(lambda: self.drop_zone.preview.set_fit(False))
        cl.addLayout(preview_head)
        self.drop_zone = UpscaleDropZone(); self.drop_zone.choose_requested.connect(self.choose_image); self.drop_zone.path_dropped.connect(self.load_image)
        cl.addWidget(self.drop_zone, 1); splitter.addWidget(center)

        right = QWidget(); right.setMinimumWidth(260); right.setMaximumWidth(360)
        rl = QVBoxLayout(right); rl.setContentsMargins(8, 12, 12, 12)
        title = QLabel("画像情報 / 処理結果"); title.setStyleSheet("font-size: 18px; font-weight: 700;"); rl.addWidget(title)
        self.original_info = QLabel("元画像\n画像を読み込んでください"); self.original_info.setWordWrap(True)
        self.output_info = QLabel("高画質化後\n—"); self.output_info.setWordWrap(True)
        info_style = "padding: 12px; background: #f4f6f8; border: 1px solid #d7dde5; border-radius: 8px; color: #273142;"
        self.original_info.setStyleSheet(info_style); self.output_info.setStyleSheet(info_style)
        rl.addWidget(self.original_info); rl.addWidget(self.output_info)
        self.engine_label = QLabel(); self.engine_label.setWordWrap(True); rl.addWidget(self.engine_label)
        self.progress_label = QLabel(""); self.progress = QProgressBar(); self.progress.hide(); self.progress_label.hide()
        rl.addWidget(self.progress_label); rl.addWidget(self.progress)
        self.result_label = QLabel(""); self.result_label.setWordWrap(True); rl.addWidget(self.result_label)
        self.save_button = QPushButton("画像を保存"); self.save_button.setEnabled(False); self.save_button.clicked.connect(self.save_result); rl.addWidget(self.save_button)
        self.saved_box = QWidget(); saved = QVBoxLayout(self.saved_box); saved.setContentsMargins(0, 6, 0, 0)
        saved.addWidget(QLabel("保存先")); self.saved_path = ElidedPathLabel(); saved.addWidget(self.saved_path)
        self.open_folder_button = QPushButton("保存先を開く"); self.open_folder_button.clicked.connect(self.open_saved_folder); saved.addWidget(self.open_folder_button)
        self.saved_box.hide(); rl.addWidget(self.saved_box); rl.addStretch(); splitter.addWidget(right)
        splitter.setStretchFactor(0, 25); splitter.setStretchFactor(1, 48); splitter.setStretchFactor(2, 27); splitter.setSizes([280, 540, 300])
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(splitter)
        for button in (self.scale_2, self.scale_4, self.illustration, self.photo):
            button.toggled.connect(self._settings_changed)
        self.format_combo.currentIndexChanged.connect(self._settings_changed)

    def _update_availability(self) -> None:
        available = self.service.backend.check_availability()
        self.engine_label.setText(("● " if available.available else "⚠ ") + available.user_message)
        self.engine_label.setStyleSheet("color: #137333;" if available.available else "color: #9a6700;")
        self.engine_label.setToolTip(available.detail)
        self._engine_available = available.available
        self._update_actions()

    @Slot()
    def choose_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "高画質化する画像を選ぶ", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if name: self.load_image(Path(name))

    @Slot(Path)
    def load_image(self, path: Path) -> None:
        if self._thread is not None: return
        try:
            with Image.open(path) as opened:
                opened.load(); image = ImageOps.exif_transpose(opened); width, height = image.size; fmt = (opened.format or path.suffix[1:]).upper(); alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
        except OSError:
            QMessageBox.warning(self, "画像を開けません", "PNG / JPEG / WebP画像を選んでください。"); return
        self._clear_result(); self.source_path = path.resolve(); self._source_size = (width, height); self._source_format = fmt; self._source_alpha = alpha
        self.output_folder = self.source_path.parent; self.folder_label.set_path(self.output_folder)
        self.drop_zone.preview.set_image_path(self.source_path); self.drop_zone.set_empty(False)
        self.original_info.setText(f"元画像\n{width} × {height}\n{fmt} / {self._human_bytes(self.source_path.stat().st_size)}\n{self.source_path.name}")
        self._settings_changed(); self._update_actions()

    @Slot()
    def _settings_changed(self) -> None:
        if not self.source_path: return
        if self.result is not None:
            self._clear_result()
            self.drop_zone.preview.set_image_path(self.source_path)
            self.before_button.setEnabled(False)
        scale = 2 if self.scale_2.isChecked() else 4; w, h = self._source_size
        self.output_info.setText(f"高画質化後\n{w * scale} × {h * scale}\n{scale}倍 / {'イラスト' if self.illustration.isChecked() else '写真'}")
        self.start_button.setText(f"{scale}倍で高画質化を開始")

    def _options(self) -> UpscaleOptions:
        return UpscaleOptions(scale=2 if self.scale_2.isChecked() else 4, mode=UpscaleMode.ILLUSTRATION if self.illustration.isChecked() else UpscaleMode.PHOTO, output_format=UpscaleOutputFormat(self.format_combo.currentData()))

    @Slot()
    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "高画質化画像の保存先を選ぶ", str(self.output_folder))
        if folder: self.output_folder = Path(folder); self.folder_label.set_path(self.output_folder)

    @Slot()
    def start(self) -> None:
        if not self.source_path or self._thread is not None: return
        available = self.service.backend.check_availability()
        if not available.available:
            QMessageBox.warning(self, "高画質化を開始できません", available.user_message + "\n\nREADMEのRuntime設定を確認してください。"); return
        self._clear_result(); self._result_temp = tempfile.TemporaryDirectory(prefix="quick-processing-upscale-result-")
        self._cancel_event = Event(); options = self._options()
        self.progress.setRange(0, 0); self.progress.show(); self.progress_label.setText("高画質化しています…"); self.progress_label.show()
        self._set_processing(True)
        self._thread = QThread(self); self._worker = UpscaleWorker(self.service, self.source_path, Path(self._result_temp.name), options, self._cancel_event)
        self._worker.moveToThread(self._thread); self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress); self._worker.succeeded.connect(self._on_succeeded); self._worker.failed.connect(self._on_failed); self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit); self._thread.finished.connect(self._worker.deleteLater); self._thread.finished.connect(self._thread.deleteLater); self._thread.finished.connect(self._clear_worker_refs)
        self._thread.start()

    @Slot()
    def cancel(self) -> None:
        if self._cancel_event: self._cancel_event.set(); self.progress_label.setText("キャンセルしています…"); self.cancel_button.setEnabled(False)

    @Slot(int)
    def _on_progress(self, value: int) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(value)

    @Slot(object)
    def _on_succeeded(self, result: UpscaleResult) -> None:
        self.result = result; self.progress.setRange(0, 100); self.progress.setValue(100); self.progress_label.setText("高画質化が完了しました")
        self.result_label.setStyleSheet("color: #137333; font-weight: 700;"); self.result_label.setText(f"✓ {result.scale}倍の高画質化が完了しました\n{result.duration_seconds:.1f}秒")
        self.after_button.setEnabled(True); self.save_button.setEnabled(True); self.show_after()

    @Slot(str, str)
    def _on_failed(self, code: str, message: str) -> None:
        self.progress.hide(); self.progress_label.setText(message); self.result_label.setStyleSheet("color: #c62828; font-weight: 700;"); self.result_label.setText("処理を完了できませんでした")
        LOGGER.error("Upscale UI failure: code=%s message=%s", code, message)
        self._discard_temporary_result()

    @Slot()
    def _on_cancelled(self) -> None:
        self.progress.hide(); self.progress_label.setText("高画質化をキャンセルしました"); self.result_label.clear()
        self._discard_temporary_result()

    @Slot()
    def _clear_worker_refs(self) -> None:
        self._worker = None; self._thread = None; self._cancel_event = None; self._set_processing(False)

    def _set_processing(self, active: bool) -> None:
        for widget in (self.scale_2, self.scale_4, self.illustration, self.photo, self.format_combo, self.folder_button, self.start_button, self.drop_zone): widget.setEnabled(not active)
        self.cancel_button.setVisible(active); self.cancel_button.setEnabled(active); self.processing_changed.emit(active)
        if not active: self._update_actions()

    def _update_actions(self) -> None:
        self.start_button.setEnabled(bool(self.source_path) and self._engine_available and self._thread is None)
        if not self.source_path: self.start_button.setText("画像を選んでください")

    @Slot()
    def show_before(self) -> None:
        if self.source_path: self.drop_zone.preview.set_image_path(self.source_path); self.before_button.setEnabled(False); self.after_button.setEnabled(bool(self.result))

    @Slot()
    def show_after(self) -> None:
        if self.result and self.result.output_path.is_file(): self.drop_zone.preview.set_image_path(self.result.output_path); self.before_button.setEnabled(True); self.after_button.setEnabled(False)

    @Slot()
    def save_result(self) -> None:
        if not self.result or not self.result.output_path.is_file(): return
        try:
            output = write_unique_bytes(self.output_folder, self.result.output_path.stem, self.result.output_path.suffix, self.result.output_path.read_bytes())
            with Image.open(output) as checked: checked.load()
        except OSError:
            QMessageBox.warning(self, "保存できません", "保存先へ画像を書き込めませんでした。"); return
        self._saved_output = output; self.saved_path.set_path(output.parent); self.saved_box.show(); self.result_label.setText(f"✓ {self.result.scale}倍の画像を保存しました\n{output.name}")

    @Slot()
    def open_saved_folder(self) -> None:
        output = getattr(self, "_saved_output", None)
        if not output or not output.parent.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent))):
            QMessageBox.warning(self, "保存先を開けません", "Windows Explorerで保存先を開けませんでした。")

    def _clear_result(self) -> None:
        self.result = None; self.after_button.setEnabled(False); self.save_button.setEnabled(False); self.result_label.clear(); self.progress.hide(); self.progress_label.hide(); self.saved_box.hide(); self._saved_output = None
        if self._result_temp: self._result_temp.cleanup(); self._result_temp = None

    def _discard_temporary_result(self) -> None:
        if self._result_temp:
            self._result_temp.cleanup()
            self._result_temp = None

    def can_close(self) -> bool:
        return self._thread is None

    def cleanup(self) -> None:
        if self._result_temp: self._result_temp.cleanup(); self._result_temp = None

    @staticmethod
    def _human_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB": return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
