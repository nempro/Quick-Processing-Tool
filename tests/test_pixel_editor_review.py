from __future__ import annotations

import os
import time

import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
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


def _canvas_point(page: PixelEditorPage, x: int, y: int) -> QPointF:
    origin_x, origin_y = page.canvas_view._origin()
    zoom = page.canvas_view.zoom_factor
    return QPointF(origin_x + x * zoom + zoom / 2, origin_y + y * zoom + zoom / 2)


def _mouse_event(page: PixelEditorPage, event_type, point, button, buttons) -> None:
    event = QMouseEvent(event_type, point, point, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(page.canvas_view.viewport(), event)


def test_canvas_hover_before_press_is_noop_and_preserves_history() -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    before = (
        page.canvas.snapshot(),
        page.canvas.history._index,
        len(page.canvas.history._entries),
    )
    point = QPointF(10, 10)
    move = QMouseEvent(
        QEvent.MouseMove,
        point,
        point,
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    release = QMouseEvent(
        QEvent.MouseButtonRelease,
        point,
        point,
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )

    page.canvas_view.mouseMoveEvent(move)
    page.canvas_view.mouseReleaseEvent(release)

    assert page.canvas_view._last_pixel is None
    assert page.canvas_view._stroke_active is False
    assert page.canvas_view._right_erase_active is False
    assert (
        page.canvas.snapshot(),
        page.canvas.history._index,
        len(page.canvas.history._entries),
    ) == before

    click = _canvas_point(page, 1, 1)
    _mouse_event(page, QEvent.MouseButtonPress, click, Qt.LeftButton, Qt.LeftButton)
    _mouse_event(page, QEvent.MouseButtonRelease, click, Qt.LeftButton, Qt.NoButton)
    assert page.canvas.snapshot() != before[0]
    assert page.canvas.history._index == before[1] + 1
    assert len(page.canvas.history._entries) == before[2] + 1
    page.close()
    app.processEvents()


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


def test_real_pointer_gestures_draw_erase_pick_and_commit_once() -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    page.resize(1180, 760)
    page.show()
    app.processEvents()
    zoom_before = page.canvas_view.zoom_factor
    grid_before = page.canvas_view.grid_enabled
    scroll_before = (
        page.canvas_view.horizontalScrollBar().value(),
        page.canvas_view.verticalScrollBar().value(),
    )

    click = _canvas_point(page, 1, 1)
    history = len(page.canvas.history._entries)
    _mouse_event(page, QEvent.MouseButtonPress, click, Qt.LeftButton, Qt.LeftButton)
    _mouse_event(page, QEvent.MouseButtonRelease, click, Qt.LeftButton, Qt.NoButton)
    assert page.canvas.pixel(1, 1)[3] == 255
    assert len(page.canvas.history._entries) == history + 1

    start = _canvas_point(page, 2, 2)
    end = _canvas_point(page, 7, 2)
    history = len(page.canvas.history._entries)
    _mouse_event(page, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    _mouse_event(page, QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton)
    assert page.canvas.pixel(7, 2)[3] == 255
    assert page.preview_label.pixmap().toImage().pixelColor(7, 2).alpha() == 255
    assert len(page.canvas.history._entries) == history
    _mouse_event(page, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    assert len(page.canvas.history._entries) == history + 1
    drawn = page.canvas.snapshot()
    page.undo()
    assert page.canvas.pixel(7, 2)[3] == 0
    page.redo()
    assert page.canvas.snapshot() == drawn

    page._set_tool(PixelTool.ERASER)
    history = len(page.canvas.history._entries)
    point = _canvas_point(page, 7, 2)
    _mouse_event(page, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    _mouse_event(page, QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
    assert page.canvas.pixel(7, 2)[3] == 0
    assert len(page.canvas.history._entries) == history + 1

    page._set_tool(PixelTool.PENCIL)
    page.canvas_view.color = (11, 22, 33, 255)
    point = _canvas_point(page, 6, 2)
    history = len(page.canvas.history._entries)
    _mouse_event(page, QEvent.MouseButtonPress, point, Qt.RightButton, Qt.RightButton)
    _mouse_event(page, QEvent.MouseButtonRelease, point, Qt.RightButton, Qt.NoButton)
    assert page.canvas.pixel(6, 2)[3] == 0
    assert page.canvas_view.tool is PixelTool.PENCIL
    assert len(page.canvas.history._entries) == history + 1

    page._set_tool(PixelTool.EYEDROPPER)
    point = _canvas_point(page, 2, 2)
    history = len(page.canvas.history._entries)
    _mouse_event(page, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    _mouse_event(page, QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
    assert page.canvas_view.color == page.canvas.pixel(2, 2)
    assert len(page.canvas.history._entries) == history
    assert page.canvas_view.zoom_factor == zoom_before
    assert page.canvas_view.grid_enabled == grid_before
    assert (
        page.canvas_view.horizontalScrollBar().value(),
        page.canvas_view.verticalScrollBar().value(),
    ) == scroll_before
    page.close()


def test_page_drop_accepts_image_as_passive_current_source(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    path = tmp_path / "drop.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(path)
    before = page.canvas.snapshot()
    enter, drop = _send_drop(page, _mime(path))
    assert enter.isAccepted() and drop.isAccepted()
    assert page._current_source is not None
    assert page._current_source.path == path.resolve()
    assert page.reference is None
    assert page.canvas.snapshot() == before
    assert page.source_path is None


def test_canvas_view_drop_updates_only_source_and_rejects_invalid_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    path = tmp_path / "drop.jpg"
    Image.new("RGB", (20, 10), "blue").save(path)
    before = page.canvas.snapshot()
    enter, drop = _send_drop(page.canvas_view, _mime(path))
    assert enter.isAccepted() and drop.isAccepted()
    assert page._current_source is not None
    assert page._current_source.path == path.resolve()
    assert page.canvas.snapshot() == before
    assert page.source_path is None
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not an image", encoding="utf-8")
    bad_enter, bad_drop = _send_drop(page, _mime(invalid))
    assert not bad_enter.isAccepted()
    assert not bad_drop.isAccepted()


def test_managed_drop_requests_workspace_source_without_document_mutation(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = PixelEditorPage()
    page.set_workspace_managed(True)
    path = tmp_path / "managed.png"
    Image.new("RGBA", (20, 10), (20, 80, 140, 255)).save(path)
    requested = []
    page.source_change_requested.connect(requested.append)
    before = (page.canvas.snapshot(), page.reference, page.source_path, page.filename_edit.text())
    enter, drop = _send_drop(page.canvas_view, _mime(path))
    app.processEvents()
    assert enter.isAccepted() and drop.isAccepted()
    assert requested == [path]
    assert page._current_source is None
    assert (page.canvas.snapshot(), page.reference, page.source_path, page.filename_edit.text()) == before


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
