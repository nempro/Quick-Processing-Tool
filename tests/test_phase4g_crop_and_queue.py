import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from quick_processing_tool.models import ImageInfo, OutputFormat, ProcessingOptions
from quick_processing_tool.pipeline import process_image, process_image_splits
from quick_processing_tool.image_splitting import ImageSplitOptions, SplitDirection
from quick_processing_tool.processors.crop import crop_box_for_image, crop_image
from quick_processing_tool.ui import MainWindow, PreviewCanvas
from quick_processing_tool.upscale_ui import UpscalePage


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _rgba(width: int = 100, height: int = 80) -> Image.Image:
    return Image.new("RGBA", (width, height), (40, 80, 120, 90))


def _wait_for_quick_preview(app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 5.0
    while window._quick_preview_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window._quick_preview_thread is None


def test_crop_box_and_alpha_are_exact() -> None:
    image = _rgba()
    rect = (0.1, 0.25, 0.5, 0.5)
    assert crop_box_for_image(image.size, rect) == (10, 20, 60, 60)
    cropped = crop_image(image, rect)
    assert cropped.size == (50, 40)
    assert cropped.getchannel("A").getextrema() == (90, 90)


def test_crop_is_applied_before_split(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _rgba().save(source)
    options = ProcessingOptions(output_format=OutputFormat.PNG, crop_rect=(0.1, 0.25, 0.5, 0.5))
    single = process_image(source, options)
    assert (single.width, single.height) == (50, 40)
    panels = process_image_splits(
        source,
        options,
        ImageSplitOptions(enabled=True, direction=SplitDirection.VERTICAL, count=2),
    )
    assert [(panel.width, panel.height) for panel in panels] == [(25, 40), (25, 40)]


def test_crop_overlay_drag_preserves_normalized_rect_and_leaves_split_guides_frontmost(
    qt_app: QApplication,
) -> None:
    preview = PreviewCanvas()
    try:
        preview.resize(640, 400)
        preview.show()
        image = QImage(240, 120, QImage.Format.Format_RGBA8888)
        image.fill(0xFF3B82F6)
        preview.set_image(image)
        preview.set_crop_overlay(True, None, None)
        qt_app.processEvents()

        item = preview._crop_item
        assert item is not None
        start = preview.mapFromScene(QPointF(238, 60))
        target = preview.mapFromScene(QPointF(180, 60))
        QTest.mousePress(
            preview.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, start,
        )
        QTest.mouseMove(preview.viewport(), target, 20)
        QTest.mouseRelease(
            preview.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, target,
        )
        qt_app.processEvents()

        assert preview._crop_rect is not None
        assert preview._crop_rect[0] == pytest.approx(0)
        assert preview._crop_rect[2] == pytest.approx(0.75, abs=0.02)
        rect_before_resize = preview._crop_rect
        preview.resize(500, 300)
        qt_app.processEvents()
        assert preview._crop_rect == rect_before_resize

        preview.set_split_guides(True, SplitDirection.VERTICAL, 3)
        assert preview._crop_item is not None
        assert all(
            guide.zValue() > preview._crop_item.zValue()
            for guide in preview._guide_items
        )
    finally:
        preview.close()


def test_crop_ratio_selection_updates_preview_and_exact_split_result(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    original = _rgba(120, 80)
    original.save(source)
    window = MainWindow()
    try:
        window.load_paths([source])
        _wait_for_quick_preview(qt_app, window)

        window.crop_section.toggle.setChecked(True)
        window.crop_ratio_combo.setCurrentIndex(
            window.crop_ratio_combo.findData(1.0)
        )
        assert window._crop_rect is not None
        assert window._crop_rect[2] / window._crop_rect[3] == pytest.approx(2 / 3)
        assert window.crop_summary.text() == "80 × 80 px"

        results = process_image_splits(
            source,
            window.options(),
            ImageSplitOptions(True, SplitDirection.VERTICAL, 2),
        )
        assert [(result.width, result.height) for result in results] == [(40, 80), (40, 80)]
    finally:
        window.close()


def test_quick_removes_selected_queue_item_without_deleting_source(
    qt_app: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "first.png"
    _rgba().save(first)
    window = MainWindow()
    try:
        window.files = [ImageInfo(first.resolve(), 100, 80, "PNG", first.stat().st_size)]
        window.current_index = 0
        window.remove_selected_quick_file()
        assert window.files == []
        assert first.is_file()
    finally:
        window.close()


def test_quick_queue_removes_only_selected_item_and_updates_preview(
    qt_app: QApplication, tmp_path: Path
) -> None:
    sources = [tmp_path / f"source-{index}.png" for index in range(3)]
    for index, source in enumerate(sources):
        Image.new("RGBA", (60 + index, 40), (index * 70, 40, 120, 255)).save(source)
    window = MainWindow()
    try:
        window.load_paths(sources)
        _wait_for_quick_preview(qt_app, window)
        assert window.file_tree.topLevelItemCount() == 3

        window.file_tree.setCurrentItem(window.file_tree.topLevelItem(1))
        qt_app.processEvents()
        window.remove_selected_quick_file()

        assert [info.path for info in window.files] == [
            source.resolve() for source in (sources[0], sources[2])
        ]
        assert window.current_index == 1
        assert all(source.is_file() for source in sources)
        assert window.file_tree.topLevelItemCount() == 2
        assert window.file_tree.currentItem().text(0) == sources[2].name
    finally:
        _wait_for_quick_preview(qt_app, window)
        window.close()


def test_upscale_removes_selected_queue_item_without_deleting_source(
    qt_app: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _rgba().save(first)
    _rgba().save(second)
    page = UpscalePage()
    try:
        page.load_paths([first, second], update_workspace=False)
        page.queue.setCurrentItem(page.queue.topLevelItem(0))
        page.remove_selected()
        assert [item.source_path for item in page.items] == [second.resolve()]
        assert first.is_file()
    finally:
        page.close()
