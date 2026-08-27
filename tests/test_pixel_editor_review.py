from __future__ import annotations

import os
import time

import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from quick_processing_tool.pixel_editor.models import PixelTool
from quick_processing_tool.pixel_editor.service import save_png
from quick_processing_tool.pixel_editor_ui import PixelEditorPage


def _mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _send_drop(target, mime: QMimeData):
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, drop)
    return enter, drop


def _wait_for_import(app: QApplication, page: PixelEditorPage, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while page._import_thread is not None and time.monotonic() < deadline:
        app.processEvents()
    app.processEvents()
    assert page._import_thread is None


def test_tools_are_exclusive_and_right_drag_preserves_selection() -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    assert page.pencil_button.isCheckable()
    assert page.pencil_button.isChecked()
    page._set_tool(PixelTool.ERASER)
    assert page.eraser_button.isChecked()
    assert not page.pencil_button.isChecked()
    page._right_erase_active = True
    page.canvas_view.tool = PixelTool.ERASER
    page._set_tool(PixelTool.ERASER)
    assert page.canvas_view.tool == PixelTool.ERASER


def test_page_drop_accepts_image_chooses_reference_and_cancel(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    path = tmp_path / "drop.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(path)
    page.import_choice_provider = lambda dropped: "reference"
    enter, drop = _send_drop(page, _mime(path))
    assert enter.isAccepted() and drop.isAccepted()
    _wait_for_import(app, page)
    assert page.reference is not None
    page.reference = None
    page.import_choice_provider = lambda dropped: None
    _, cancelled = _send_drop(page, _mime(path))
    assert cancelled.isAccepted()
    assert page.reference is None


def test_canvas_view_drop_routes_pixel_mode_and_rejects_invalid_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    path = tmp_path / "drop.jpg"
    Image.new("RGB", (20, 10), "blue").save(path)
    page.import_choice_provider = lambda dropped: "pixels"
    enter, drop = _send_drop(page.canvas_view, _mime(path))
    assert enter.isAccepted() and drop.isAccepted()
    _wait_for_import(app, page)
    assert page.canvas.pixel(64, 64)[3] > 0
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not an image", encoding="utf-8")
    bad_enter, bad_drop = _send_drop(page, _mime(invalid))
    assert not bad_enter.isAccepted()
    assert not bad_drop.isAccepted()


def test_preview_is_native_one_to_one_and_scrolls_large_canvas() -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    page.show()
    app.processEvents()
    assert (page.preview_label.pixmap().width(), page.preview_label.pixmap().height()) == (page.canvas.width, page.canvas.height)
    page.width_spin.setValue(512)
    page.height_spin.setValue(512)
    page.new_canvas()
    app.processEvents()
    assert (page.preview_label.pixmap().width(), page.preview_label.pixmap().height()) == (page.canvas.width, page.canvas.height)
    assert page.preview_scroll.horizontalScrollBar().maximum() > 0
    assert page.preview_scroll.verticalScrollBar().maximum() > 0
    page.close()

@pytest.mark.parametrize(
    ("index", "size"),
    [(0, (32, 32)), (1, (64, 64)), (2, (128, 128)), (3, (37, 19))],
)
def test_preset_selection_keeps_canvas_spins_preview_and_export_in_sync(
    tmp_path: Path, index: int, size: tuple[int, int]
) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    assert page.preset_combo.currentIndex() == 2
    assert (page.canvas.width, page.canvas.height) == (128, 128)
    assert not page.canvas.history.can_undo
    page.preset_combo.setCurrentIndex(index)
    if index == 3:
        page.width_spin.setValue(size[0])
        page.height_spin.setValue(size[1])
    assert (page.width_spin.value(), page.height_spin.value()) == size
    page.new_canvas()
    app.processEvents()
    assert (page.canvas.width, page.canvas.height) == size
    assert (page.preview_label.pixmap().width(), page.preview_label.pixmap().height()) == size
    result = save_png(page.canvas, tmp_path)
    with Image.open(result.output_path) as reopened:
        assert reopened.size == size
