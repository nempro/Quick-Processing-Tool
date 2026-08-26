from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea

from quick_processing_tool.pixel_editor.models import PixelTool
from quick_processing_tool.pixel_editor_ui import PixelEditorPage
from quick_processing_tool.ui import MainWindow


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _palette_tooltips(page: PixelEditorPage) -> list[str]:
    return [button.toolTip() for button in page._palette_chip_buttons]


def _checked_palette_indices(page: PixelEditorPage) -> list[int]:
    return [index for index, button in enumerate(page._palette_chip_buttons) if button.isChecked()]


def test_pixel_page_japanese_controls_defaults_and_state(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    assert page.canvas.size == (128, 128) if hasattr(page.canvas, "size") else (page.canvas.width, page.canvas.height) == (128, 128)
    assert page.canvas_view.zoom_factor == 4
    assert page.grid_check.isChecked()
    assert page.reference_check.isChecked()
    assert page.filename_edit.text() == "pixel_art"
    assert not page.save_button.isEnabled()
    assert page.palette_contract_label.text() == "パレットの色だけを受け取ります。元画像は移動しません。"
    labels = {button.text() for button in page.findChildren(type(page.new_button))}
    assert {"鉛筆", "消しゴム", "スポイト", "下絵を読み込む", "ドット化して編集", "PNGで保存", "保存先を選ぶ", "保存先を開く", "パレットをクリア"} <= labels
    assert any(group.title() == "パレット" for group in page.findChildren(QGroupBox))
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
    assert window.pixel_page.preview_scroll.width() >= 180
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


def test_receive_palette_shows_order_count_and_blank_guidance(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    palette = (
        (0x17, 0x19, 0x1B),
        (0xE4, 0xAA, 0x23),
        (0xF6, 0xD9, 0x9C),
        (0x80, 0x40, 0xC0),
        (0x10, 0x90, 0x50),
        (0xFA, 0xFA, 0xFA),
    )
    before_color = page.canvas_view.color
    page.receive_palette(palette)
    assert page.palette_status_label.text() == "✓ 6色のパレットを受け取りました"
    assert page.palette_count_label.text() == "6色"
    assert page.palette_contract_label.text() == "パレットの色だけを受け取ります。元画像は移動しません。"
    assert page.palette_guidance_label.text() == (
        "パレットだけを受け取りました。\n"
        "画像は読み込まれていません。\n\n"
        "「下絵を読み込む」または\n"
        "「ドット化して編集」から画像を追加できます。"
    )
    assert _palette_tooltips(page) == ["#17191B", "#E4AA23", "#F6D99C", "#8040C0", "#109050", "#FAFAFA"]
    assert _checked_palette_indices(page) == []
    assert page.canvas_view.color == before_color


def test_receive_palette_replaces_values_and_resets_selection_without_changing_current_color(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    first = tuple((index, index + 1, index + 2) for index in range(6))
    second = tuple((index + 20, index + 21, index + 22) for index in range(8))
    page.receive_palette(first)
    page._palette_chip_buttons[2].click()
    qt_app.processEvents()
    current_before = page.canvas_view.color
    page.receive_palette(second)
    assert page.palette_status_label.text() == "✓ 8色のパレットを受け取りました"
    assert page.palette_count_label.text() == "8色"
    assert len(page._palette_chip_buttons) == 8
    assert _palette_tooltips(page) == [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in second]
    assert _checked_palette_indices(page) == []
    assert page.canvas_view.color == current_before


def test_palette_click_updates_current_color_selection_and_draw(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    palette = ((10, 20, 30), (200, 100, 50), (80, 90, 100))
    page.receive_palette(palette)
    page._palette_chip_buttons[1].click()
    qt_app.processEvents()
    assert page._palette_selected_index == 1
    assert _checked_palette_indices(page) == [1]
    assert page.current_color_button.text() == "#C86432"
    assert page.canvas_view.color == (200, 100, 50, 255)
    page.canvas.stroke((0, 0), (0, 0), page.canvas_view.color)
    assert page.canvas.pixel(0, 0) == (200, 100, 50, 255)
    page._palette_chip_buttons[2].click()
    qt_app.processEvents()
    assert _checked_palette_indices(page) == [2]


def test_palette_eyedropper_sync_does_not_mutate_palette(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    palette = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
    page.receive_palette(palette)
    page._palette_chip_buttons[0].click()
    page.canvas.set_pixel(1, 1, (9, 8, 7, 255))
    page.canvas_view.color_picked.emit(QColor(9, 8, 7, 255))
    qt_app.processEvents()
    assert page.canvas_view.color == (9, 8, 7, 255)
    assert page.current_color_button.text() == "#090807"
    assert _checked_palette_indices(page) == []
    assert page._received_palette == palette


def test_clear_palette_keeps_canvas_history_and_current_color(qt_app: QApplication) -> None:
    page = PixelEditorPage()
    palette = ((1, 2, 3), (4, 5, 6), (7, 8, 9))
    page.receive_palette(palette)
    page._palette_chip_buttons[1].click()
    page.canvas.stroke((0, 0), (1, 0), page.canvas_view.color)
    snapshot = page.canvas.snapshot()
    history_index = page.canvas.history._index
    history_len = len(page.canvas.history._entries)
    current_color = page.canvas_view.color
    page.clear_palette()
    assert page._received_palette == ()
    assert len(page._palette_chip_buttons) == 0
    assert page.palette_count_label.text() == "0色"
    assert page.canvas.snapshot() == snapshot
    assert page.canvas.history._index == history_index
    assert len(page.canvas.history._entries) == history_len
    assert page.canvas_view.color == current_color


def test_receive_palette_preserves_existing_document_state(qt_app: QApplication, tmp_path: Path) -> None:
    page = PixelEditorPage()
    source = tmp_path / "source.png"
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(source)
    page._load_reference_path(source)
    page.output_folder = tmp_path
    page._update_save_ui()
    page.filename_edit.setText("keep_name")
    page.zoom_combo.setCurrentIndex(4)
    page.grid_check.setChecked(False)
    page.canvas.stroke((0, 0), (2, 0), (12, 34, 56, 255))
    snapshot = page.canvas.snapshot()
    history_index = page.canvas.history._index
    history_len = len(page.canvas.history._entries)
    reference = page.reference
    page.receive_palette(((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18)))
    assert "そのまま" in page.palette_guidance_label.text()
    assert page.source_path == source
    assert page.reference == reference
    assert page.filename_edit.text() == "keep_name"
    assert page.output_folder == tmp_path
    assert page.zoom_combo.currentIndex() == 4
    assert page.canvas_view.zoom_factor == 16
    assert not page.grid_check.isChecked()
    assert not page.canvas_view.grid_enabled
    assert page.canvas.snapshot() == snapshot
    assert page.canvas.history._index == history_index
    assert len(page.canvas.history._entries) == history_len
