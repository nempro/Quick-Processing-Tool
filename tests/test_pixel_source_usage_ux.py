from __future__ import annotations

import hashlib
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QScrollArea

from quick_processing_tool.image_workspace import read_source_image
from quick_processing_tool.pixel_editor_ui import PixelEditorPage
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_image(path: Path, size=(64, 48), color=(30, 80, 130, 255)) -> Path:
    Image.new("RGBA", size, color).save(path)
    return path


def test_usage_frame_hidden_without_source_and_explicit_when_loaded(
    app: QApplication, tmp_path: Path
) -> None:
    source_path = make_image(tmp_path / "current-source.png", (83, 61))
    page = PixelEditorPage()
    try:
        assert page.current_source_usage.isHidden()
        assert not page.current_reference_button.isEnabled()
        assert not page.current_pixels_button.isEnabled()

        source = read_source_image(source_path, 1)
        page.set_current_source(source)
        page.show()
        app.processEvents()
        assert page.current_source_usage.isVisible()
        assert page.current_source_usage_heading.text() == "この画像をどう使いますか？"
        assert page.current_reference_button.text() == "下絵として使う"
        assert page.current_pixels_button.text() == "ドット化して編集"
        assert page.current_reference_button.isEnabled()
        assert page.current_pixels_button.isEnabled()
        assert str(source_path.resolve()) in page.current_source_card.name_label.toolTip()
        assert "83 × 61 / PNG" in page.current_source_card.meta_label.text()
        assert page.current_source_usage_guidance.text() == (
            "現在の画像は読み込まれています。\n"
            "下絵にするか、ドット化して編集できます。"
        )
        assert "#eef5ff" in page.current_source_usage.styleSheet()
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_usage_feedback_tracks_draw_clear_and_undo(
    app: QApplication, tmp_path: Path
) -> None:
    source = read_source_image(make_image(tmp_path / "source.png"), 1)
    page = PixelEditorPage()
    try:
        page.set_current_source(source)
        page.canvas.stroke((1, 1), (4, 1), (10, 20, 30, 255))
        page._refresh()
        assert "自動では変更されません" in page.current_source_usage_guidance.text()
        assert "#f7f8fa" in page.current_source_usage.styleSheet()

        page.clear()
        assert "現在の画像は読み込まれています" in page.current_source_usage_guidance.text()
        page.undo()
        assert "自動では変更されません" in page.current_source_usage_guidance.text()
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_passive_source_replace_preserves_complete_pixel_state(
    app: QApplication, tmp_path: Path
) -> None:
    first_path = make_image(tmp_path / "first.png", color=(200, 20, 30, 255))
    second_path = make_image(tmp_path / "second.png", color=(20, 200, 30, 255))
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (5, 1), (1, 2, 3, 255))
    page._load_reference_path(first_path)
    page.receive_palette(((1, 2, 3), (4, 5, 6)))
    page.filename_edit.setText("keep-name")
    page.output_folder = tmp_path / "keep-folder"
    page.zoom_combo.setCurrentIndex(3)
    page.grid_check.setChecked(False)
    page.set_current_source(read_source_image(first_path, 1))
    before = (
        hashlib.sha256(page.canvas.snapshot()).hexdigest(),
        page.canvas.history.current,
        page.canvas.history._index,
        len(page.canvas.history._entries),
        page.reference,
        page.source_path,
        page._received_palette,
        page.filename_edit.text(),
        page.output_folder,
        page.zoom_combo.currentIndex(),
        page.grid_check.isChecked(),
    )
    try:
        page.set_current_source(read_source_image(second_path, 2))
        after = (
            hashlib.sha256(page.canvas.snapshot()).hexdigest(),
            page.canvas.history.current,
            page.canvas.history._index,
            len(page.canvas.history._entries),
            page.reference,
            page.source_path,
            page._received_palette,
            page.filename_edit.text(),
            page.output_folder,
            page.zoom_combo.currentIndex(),
            page.grid_check.isChecked(),
        )
        assert after == before
        assert "自動では変更されません" in page.current_source_usage_guidance.text()
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_usage_frame_fits_without_horizontal_scroll_or_preview_regression(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = make_image(tmp_path / f"layout-{width}.png")
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.navigation.setCurrentIndex(window.pixel_tab)
        window.show()
        app.processEvents()
        page = window.pixel_page
        scroll = page.findChild(QScrollArea, "pixelSettingsScroll")
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        assert page.current_source_usage.width() <= scroll.viewport().width()
        assert page.current_reference_button.width() <= page.current_source_usage.width()
        assert page.current_pixels_button.width() <= page.current_source_usage.width()
        assert page.canvas_view.width() >= 350
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
