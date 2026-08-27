from __future__ import annotations

import hashlib
import os
import time
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


def wait_for_import(app: QApplication, page: PixelEditorPage, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while page._import_thread is not None and time.monotonic() < deadline:
        app.processEvents()
    app.processEvents()
    assert page._import_thread is None


def test_usage_frame_hidden_without_source_and_explicit_when_loaded(
    app: QApplication, tmp_path: Path
) -> None:
    source_path = make_image(tmp_path / "current-source.png", (83, 61))
    page = PixelEditorPage()
    try:
        assert page.current_source_usage.isHidden()
        assert not page.current_reference_button.isEnabled()
        assert not page.current_pixels_button.isEnabled()
        assert page.current_source_card.title_label.text() == "元にする画像"
        assert page.current_source_card.name_label.text() == "未選択"
        assert page.current_source_card.change_button.text() == "画像を選ぶ"
        assert page.current_source_card.change_button.toolTip() == "画像を選びます"
        assert page.current_source_card.change_button.accessibleName() == "画像を選ぶ"

        source = read_source_image(source_path, 1)
        page.set_current_source(source)
        page.show()
        app.processEvents()
        assert page.current_source_usage.isVisible()
        assert page.current_source_usage_heading.text() == "この画像を使う"
        assert page.current_source_card.change_button.text() == "別の画像を選ぶ"
        assert page.current_source_card.change_button.toolTip() == "別の画像を選びます"
        assert page.current_source_card.change_button.accessibleName() == "別の画像を選ぶ"
        assert page.current_reference_button.text() == "下絵にする"
        assert page.current_pixels_button.text() == "ドット化"
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


def test_document_summary_tracks_filename_dimensions_and_history_state(
    app: QApplication, tmp_path: Path,
) -> None:
    page = PixelEditorPage()
    try:
        page.resize(900, 760)
        page.show()
        app.processEvents()
        assert page.document_name_label.toolTip() == "新規キャンバス"
        assert page.document_meta_label.text() == "128 × 128"
        page.filename_edit.setText("very-long-" + "x" * 80)
        app.processEvents()
        assert page.document_name_label.toolTip() == "新規キャンバス"
        page.canvas.stroke((1, 1), (4, 1), (10, 20, 30, 255))
        page._refresh()
        assert page.document_name_label.toolTip().endswith(".png")
        assert page.document_name_label.text() != page.document_name_label.toolTip()
        assert page.document_meta_label.text() == "128 × 128"
        page.undo()
        assert page.document_name_label.toolTip() == "新規キャンバス"
        assert page.document_meta_label.text() == "128 × 128"
        page.output_folder = tmp_path
        page.filename_edit.setText("saved-blank")
        page._update_save_ui()
        page.save()
        assert page.document_name_label.toolTip() == "saved-blank.png"
        assert page.document_meta_label.text() == "128 × 128"
        page.preset_combo.setCurrentIndex(0)
        page.new_canvas()
        assert page.document_name_label.toolTip() == "新規キャンバス"
        assert page.document_meta_label.text() == "32 × 32"
    finally:
        page.close()


def test_source_change_notice_is_persistent_and_cleared_by_explicit_use(
    app: QApplication, tmp_path: Path
) -> None:
    first = read_source_image(make_image(tmp_path / "first.png"), 1)
    second = read_source_image(make_image(tmp_path / "second.png"), 2)
    page = PixelEditorPage()
    try:
        page.set_current_source(first)
        assert page.source_change_notice.isHidden()
        page.set_current_source(first)
        assert page.source_change_notice.isHidden()
        page.set_current_source(second)
        assert not page.source_change_notice.isHidden()
        assert page.source_change_notice.text() == (
            "元にする画像を変更しました\n"
            "編集中のドット絵はそのままです"
        )
        page.hide()
        app.processEvents()
        page.show()
        app.processEvents()
        assert not page.source_change_notice.isHidden()
        page.use_current_as_reference()
        assert page.source_change_notice.isHidden()
        wait_for_import(app, page)

        edited = PixelEditorPage()
        edited.canvas.stroke((1, 1), (2, 1), (1, 2, 3, 255))
        edited.set_current_source(first)
        assert not edited.source_change_notice.isHidden()
        edited.close()
    finally:
        page.close()


def test_passive_source_replace_preserves_complete_pixel_state(
    app: QApplication, tmp_path: Path
) -> None:
    first_path = make_image(tmp_path / "first.png", color=(200, 20, 30, 255))
    second_path = make_image(tmp_path / "second.png", color=(20, 200, 30, 255))
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (5, 1), (1, 2, 3, 255))
    page._load_reference_path(first_path)
    wait_for_import(app, page)
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
        assert not page.source_change_notice.isHidden()
        assert page.source_change_notice.text() == (
            "元にする画像を変更しました\n"
            "編集中のドット絵はそのままです"
        )
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
        assert scroll.widget().width() <= scroll.viewport().width()
        assert page.document_summary.width() <= scroll.viewport().width()
        assert page.current_source_card.change_button.height() >= 30
        assert (
            page.current_source_card.change_button.geometry().top()
            > page.current_source_card.meta_label.geometry().bottom()
        )
        assert page.current_source_usage.width() <= scroll.viewport().width()
        assert page.current_reference_button.width() <= page.current_source_usage.width()
        assert page.current_pixels_button.width() <= page.current_source_usage.width()
        assert page.canvas_view.width() == {900: 374, 1180: 654, 1440: 914}[width]
        replacement = make_image(tmp_path / f"layout-replacement-{width}.png")
        window.set_current_source(replacement)
        app.processEvents()
        assert not page.source_change_notice.isHidden()
        assert page.source_change_notice.width() <= scroll.viewport().width()
        assert page.source_change_notice.height() >= page.source_change_notice.heightForWidth(
            page.source_change_notice.width()
        )
        content = scroll.widget()
        assert page.current_source_card.parentWidget() is content
        assert page.current_source_card.geometry().top() < page.document_summary.geometry().top()
        assert page.document_summary.geometry().top() < page.source_change_notice.geometry().top()
        assert page.source_change_notice.geometry().top() < page.current_source_usage.geometry().top()
        assert page.current_source_usage.geometry().top() < page.tools_group.geometry().top()
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        deadline = time.monotonic() + 3.0
        while window.edit_page._preview_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        assert window.edit_page._preview_thread is None
        window.close()
        window.deleteLater()
        app.processEvents()
