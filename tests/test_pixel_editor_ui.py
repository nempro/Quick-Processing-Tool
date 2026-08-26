from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from quick_processing_tool.pixel_editor.models import PixelTool
from quick_processing_tool.pixel_editor_ui import PixelEditorPage
from quick_processing_tool.ui import MainWindow


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_pixel_page_japanese_controls_defaults_and_state(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    assert page.canvas.size == (128, 128) if hasattr(page.canvas, "size") else (page.canvas.width, page.canvas.height) == (128, 128)
    assert page.canvas_view.zoom_factor == 4
    assert page.grid_check.isChecked()
    assert page.reference_check.isChecked()
    labels = {button.text() for button in page.findChildren(type(page.new_button))}
    assert {"鉛筆", "消しゴム", "スポイト", "下絵を読み込む", "ドット化して編集", "PNGで保存"} <= labels
    page._set_tool(PixelTool.ERASER)
    assert page.canvas_view.tool == PixelTool.ERASER
    page.grid_check.setChecked(False)
    assert not page.canvas_view.grid_enabled


@pytest.mark.parametrize("size", [(900, 620), (1180, 760), (1440, 900)])
def test_main_window_pixel_tab_and_three_column_geometry(qt_app: QApplication, size: tuple[int, int]) -> None:
    window = MainWindow()
    window.resize(*size)
    window.navigation.setCurrentWidget(window.pixel_page)
    window.show()
    qt_app.processEvents()
    assert window.navigation.tabText(window.pixel_tab) == "ドット絵"
    assert window.navigation.isTabEnabled(window.pixel_tab)
    assert window.pixel_page.canvas_view.width() > 0
    left = window.pixel_page.findChild(QScrollArea, "pixelSettingsScroll")
    assert left is not None
    assert left.horizontalScrollBarPolicy().name == "ScrollBarAlwaysOff"
    assert left.horizontalScrollBar().maximum() == 0
    window.close()


def test_processing_locks_pixel_tab_and_close_contract(qt_app: QApplication) -> None:
    window = MainWindow()
    window._thumbnail_processing_changed(True)
    assert not window.navigation.isTabEnabled(window.pixel_tab)
    window._thumbnail_processing_changed(False)
    assert window.navigation.isTabEnabled(window.pixel_tab)
    assert window.pixel_page.can_close()
    window.close()
