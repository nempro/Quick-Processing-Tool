from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter

from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.ui import MainWindow
from quick_processing_tool.ui_styles import PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _bounds(widget, parent) -> tuple[int, int, int, int]:
    top_left = widget.mapTo(parent, QPoint(0, 0))
    return top_left.x(), top_left.y(), widget.width(), widget.height()


@pytest.mark.parametrize(
    "output_format,quality_visible,jpeg_visible",
    (
        ("PNG", False, False),
        ("JPEG", True, True),
        ("WEBP", True, False),
    ),
)
@pytest.mark.parametrize("width", (720, 900, 1180, 1440))
def test_edit_save_options_keep_stable_label_and_field_columns(
    app: QApplication,
    output_format: str,
    quality_visible: bool,
    jpeg_visible: bool,
    width: int,
) -> None:
    page = QuickEditPage()
    try:
        page.resize(width, 720)
        page.show()
        page.format_combo.setCurrentIndex(page.format_combo.findData(output_format))
        app.processEvents()

        expected_visibility = (
            (page.format_label, page.format_combo, True),
            (page.quality_label, page.quality_spin, quality_visible),
            (page.jpeg_background_label, page.jpeg_background_combo, jpeg_visible),
        )
        for label, field, visible in expected_visibility:
            assert label.isVisibleTo(page.save_panel) is visible
            assert field.isVisibleTo(page.save_panel) is visible
            if not visible:
                continue
            label_bounds = _bounds(label, page.save_panel)
            field_bounds = _bounds(field, page.save_panel)
            assert label_bounds[0] + label_bounds[2] < field_bounds[0]
            assert field_bounds[0] + field_bounds[2] <= page.save_panel.width()
            assert field_bounds[2] >= 70
        assert page.save_scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.close()


@pytest.mark.parametrize("width", (720, 900, 1180, 1440))
def test_quick_and_edit_share_left_pane_width_and_preview_start(
    app: QApplication, width: int
) -> None:
    window = MainWindow()
    try:
        window.setMinimumSize(0, 0)
        window.resize(width, 620)
        window.show()
        app.processEvents()

        window.navigation.setCurrentIndex(window.quick_tab)
        app.processEvents()
        quick_splitter = window.navigation.currentWidget()
        assert isinstance(quick_splitter, QSplitter)
        quick_left = quick_splitter.widget(0)
        quick_center = quick_splitter.widget(1)
        quick_width = quick_left.width()
        quick_start = quick_center.mapTo(window, QPoint(0, 0)).x()

        window.navigation.setCurrentIndex(window.image_edit_tab)
        app.processEvents()
        edit_splitter = window.edit_page.findChild(QSplitter, "edit_workspace")
        assert edit_splitter is not None
        edit_left = edit_splitter.widget(0)
        edit_center = edit_splitter.widget(1)
        edit_width = edit_left.width()
        edit_start = edit_center.mapTo(window, QPoint(0, 0)).x()

        assert quick_width == edit_width == PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH
        assert abs(quick_start - edit_start) <= 1
        assert window.quick_settings_scroll.horizontalScrollBar().maximum() == 0
        assert window.edit_page.settings_scroll.horizontalScrollBar().maximum() == 0
        for scroll in window.edit_page.findChildren(QScrollArea):
            assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()


@pytest.mark.parametrize("width", (900, 1180))
def test_loaded_jpeg_save_panel_never_needs_horizontal_scroll(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = tmp_path / f"save-layout-{width}.png"
    Image.new("RGBA", (96, 64), (20, 80, 180, 160)).save(source)
    page = QuickEditPage()
    try:
        page.resize(width, 620)
        page.show()
        assert page.load_image(source)
        deadline = time.monotonic() + 4.0
        while page._preview_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert page._preview_thread is None
        page.format_combo.setCurrentIndex(page.format_combo.findData("JPEG"))
        app.processEvents()
        assert page.save_scroll.verticalScrollBar().maximum() > 0
        assert page.save_scroll.horizontalScrollBar().maximum() == 0
        assert page.save_panel.width() <= page.save_scroll.viewport().width()
    finally:
        page.close()
