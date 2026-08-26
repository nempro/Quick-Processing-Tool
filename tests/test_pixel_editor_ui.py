from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QUrl
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
    assert page.filename_edit.text() == "pixel_art"
    assert not page.save_button.isEnabled()
    labels = {button.text() for button in page.findChildren(type(page.new_button))}
    assert {"鉛筆", "消しゴム", "スポイト", "下絵を読み込む", "ドット化して編集", "PNGで保存", "保存先を選ぶ", "保存先を開く"} <= labels
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


def test_filename_and_folder_drive_live_planned_save_state(tmp_path: Path, qt_app: QApplication) -> None:
    page = PixelEditorPage()
    page.output_folder = tmp_path
    page._update_save_ui()
    assert page.save_button.isEnabled()
    assert page.planned_output_value.toolTip().endswith("pixel_art.png")
    page.filename_edit.setText("  ねこ.png.PNG  ")
    assert page.planned_output_value.toolTip().endswith("ねこ.png")
    page.filename_edit.editingFinished.emit()
    assert page.filename_edit.text() == "ねこ"
    assert page.save_button.isEnabled()
    page.filename_edit.setText("  .png  ")
    assert not page.save_button.isEnabled()
    assert "ファイル名" in page.save_hint_label.text()


def test_save_result_open_folder_and_exact_parent_tracking(
    tmp_path: Path, qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = PixelEditorPage()
    page.output_folder = tmp_path
    page.filename_edit.setText("完成.png")
    page._update_save_ui()
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "quick_processing_tool.pixel_editor_ui.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )
    page.save()
    assert page._last_saved_result is not None
    assert page._last_saved_result.output_path.parent == tmp_path
    assert "PNGを保存しました" in page.saved_label.text()
    assert "完成.png" in page.saved_label.text()
    assert page.open_folder_button.isEnabled()
    page.open_saved_folder()
    assert opened
    assert Path(opened[-1].toLocalFile()) == tmp_path


def test_filename_entry_resets_only_for_new_document_sources_and_not_ordinary_actions(
    tmp_path: Path, qt_app: QApplication
) -> None:
    page = PixelEditorPage()
    page.filename_edit.setText("manual_name")
    page.clear()
    page.undo()
    page.redo()
    page.zoom_combo.setCurrentIndex(1)
    page.grid_check.setChecked(False)
    assert page.filename_edit.text() == "manual_name"
    page.new_canvas()
    assert page.filename_edit.text() == "pixel_art"
    source = tmp_path / "勇者.png"
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(source)
    page.filename_edit.setText("keep_me")
    page._load_reference_path(source)
    assert page.filename_edit.text() == "勇者_pixel"
    page.filename_edit.setText("keep_me_again")
    page._load_pixels_path(source)
    assert page.filename_edit.text() == "勇者_pixel"


def test_pixel_page_geometry_keeps_compact_elided_save_labels(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    page.resize(1180, 760)
    page.show()
    qt_app.processEvents()
    assert page.output_folder_value.sizePolicy().horizontalPolicy() == page.output_folder_value.sizePolicy().Policy.Ignored
    assert page.planned_output_value.sizePolicy().horizontalPolicy() == page.planned_output_value.sizePolicy().Policy.Ignored
    page.close()
