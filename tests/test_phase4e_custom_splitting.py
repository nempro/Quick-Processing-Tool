from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quick_processing_tool.image_splitting import (
    MIN_SPLIT_PANEL_PIXELS,
    ImageSplitOptions,
    SplitDirection,
    partition_edges,
    split_boxes,
    split_image,
)
from quick_processing_tool.models import OutputFormat, ProcessingOptions
from quick_processing_tool.pipeline import process_image_splits
from quick_processing_tool.ui import MainWindow, PreviewCanvas


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_for_quick_preview(app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and window._quick_preview_thread is not None:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window._quick_preview_thread is None


def test_custom_partition_edges_cover_axis_and_enforce_minimum() -> None:
    assert partition_edges(1001, 3, (0.2, 0.7), MIN_SPLIT_PANEL_PIXELS) == (
        0,
        200,
        701,
        1001,
    )
    assert partition_edges(100, 3, (0.01, 0.99), MIN_SPLIT_PANEL_PIXELS) == (
        0,
        16,
        84,
        100,
    )


@pytest.mark.parametrize("direction", list(SplitDirection))
def test_custom_split_rejoins_without_gaps_or_overlap(
    direction: SplitDirection,
) -> None:
    image = Image.new("RGBA", (1001, 607))
    image.putdata(
        [
            (x % 256, y % 256, (x + y) % 256, (x * 7 + y * 3) % 256)
            for y in range(image.height)
            for x in range(image.width)
        ]
    )
    boundaries = (0.18, 0.54, 0.83)
    boxes = split_boxes(
        image.width,
        image.height,
        direction,
        4,
        boundaries,
        MIN_SPLIT_PANEL_PIXELS,
    )
    panels = split_image(
        image,
        direction,
        4,
        boundaries,
        MIN_SPLIT_PANEL_PIXELS,
    )
    rejoined = Image.new("RGBA", image.size)
    for panel, box in zip(panels, boxes, strict=True):
        rejoined.paste(panel, box[:2])
    assert rejoined.tobytes() == image.tobytes()


@pytest.mark.parametrize("direction", list(SplitDirection))
@pytest.mark.parametrize("zoom", [None, 1.0, 2.0])
def test_guide_drag_uses_scene_coordinates_at_every_zoom(
    app: QApplication,
    direction: SplitDirection,
    zoom: float | None,
) -> None:
    preview = PreviewCanvas()
    try:
        preview.resize(720, 520)
        preview.show()
        image = QImage(400, 300, QImage.Format.Format_RGBA8888)
        image.fill(0xFF3B82F6)
        preview.set_image(image)
        preview.set_split_guides(True, direction, 3)
        preview.set_zoom_factor(zoom)
        app.processEvents()

        axis_length = image.width() if direction is SplitDirection.VERTICAL else image.height()
        start_axis = partition_edges(axis_length, 3)[1]
        target_axis = round(axis_length * 0.24)
        if direction is SplitDirection.VERTICAL:
            start_scene = QPointF(start_axis, image.height() / 2)
            target_scene = QPointF(target_axis, image.height() / 2)
        else:
            start_scene = QPointF(image.width() / 2, start_axis)
            target_scene = QPointF(image.width() / 2, target_axis)
        start = preview.mapFromScene(start_scene)
        target = preview.mapFromScene(target_scene)
        QTest.mousePress(
            preview.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        QTest.mouseMove(preview.viewport(), target, 20)
        QTest.mouseRelease(
            preview.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        app.processEvents()

        assert preview._split_boundaries is not None
        assert preview._split_boundaries[0] == pytest.approx(target_axis / axis_length)
    finally:
        preview.close()


def test_drag_clamps_between_neighbors_and_minimum_panel_size(
    app: QApplication,
) -> None:
    preview = PreviewCanvas()
    try:
        preview.show()
        image = QImage(300, 120, QImage.Format.Format_RGBA8888)
        image.fill(0xFF111827)
        preview.set_image(image)
        preview.set_split_guides(True, SplitDirection.VERTICAL, 3)
        preview._drag_split_guide(0, QPointF(299, 20))
        preview._drag_split_guide(1, QPointF(1, 20))
        app.processEvents()

        edges = partition_edges(
            image.width(),
            3,
            preview._split_boundaries,
            MIN_SPLIT_PANEL_PIXELS,
        )
        sizes = [right - left for left, right in zip(edges, edges[1:])]
        assert min(sizes) >= MIN_SPLIT_PANEL_PIXELS
        assert list(edges) == sorted(edges)
    finally:
        preview.close()


def test_preview_resize_preserves_custom_boundary_ratios(app: QApplication) -> None:
    preview = PreviewCanvas()
    try:
        preview.show()
        image = QImage(500, 240, QImage.Format.Format_RGBA8888)
        image.fill(0xFF0F766E)
        preview.set_image(image)
        preview.set_split_guides(
            True,
            SplitDirection.VERTICAL,
            3,
            (0.2, 0.76),
        )
        before = tuple(guide.line().x1() for guide in preview._guide_items)
        preview.resize(900, 360)
        app.processEvents()
        after = tuple(guide.line().x1() for guide in preview._guide_items)
        assert preview._split_boundaries == (0.2, 0.76)
        assert after == before
    finally:
        preview.close()


def test_low_scale_fit_hit_area_and_image_refresh_preserve_custom_ratios(
    app: QApplication,
) -> None:
    preview = PreviewCanvas()
    try:
        preview.resize(260, 190)
        preview.show()
        large = QImage(2400, 1600, QImage.Format.Format_RGBA8888)
        large.fill(0xFF334155)
        preview.set_image(large)
        preview.set_split_guides(True, SplitDirection.VERTICAL, 3)
        preview.set_zoom_factor(None)
        app.processEvents()
        assert preview.transform().m11() < 0.2

        start = preview.mapFromScene(QPointF(800, 800))
        target = preview.mapFromScene(QPointF(600, 800))
        QTest.mousePress(
            preview.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        QTest.mouseMove(preview.viewport(), target, 20)
        QTest.mouseRelease(
            preview.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        app.processEvents()
        assert preview._split_boundaries is not None
        dragged_ratio = preview._split_boundaries[0]
        assert dragged_ratio == pytest.approx(0.25, abs=0.005)

        refreshed = QImage(1200, 800, QImage.Format.Format_RGBA8888)
        refreshed.fill(0xFF475569)
        preview.set_image(refreshed)
        app.processEvents()
        assert preview._split_boundaries[0] == dragged_ratio
        expected = partition_edges(
            1200,
            3,
            preview._split_boundaries,
            MIN_SPLIT_PANEL_PIXELS,
        )[1]
        assert preview._guide_items[0].line().x1() == float(expected)
    finally:
        preview.close()


def test_quick_count_direction_and_button_restore_equal_boundaries(
    app: QApplication,
    tmp_path: Path,
) -> None:
    source = tmp_path / "reset.png"
    Image.new("RGB", (600, 300), "navy").save(source)
    window = MainWindow()
    try:
        window.load_paths([source])
        _wait_for_quick_preview(app, window)
        window.split_enable_check.click()
        window.preview._drag_split_guide(0, QPointF(100, 20))
        app.processEvents()
        assert window.split_options().boundaries is not None
        assert window.split_uniform_button.isEnabled()

        window.split_uniform_button.click()
        assert window.split_options().boundaries is None
        assert not window.split_uniform_button.isEnabled()

        window.preview._drag_split_guide(0, QPointF(120, 20))
        window.split_count_buttons[5].click()
        assert window.split_options().boundaries is None

        window.preview._drag_split_guide(0, QPointF(80, 20))
        window.split_direction_buttons[SplitDirection.HORIZONTAL].click()
        assert window.split_options().boundaries is None
    finally:
        _wait_for_quick_preview(app, window)
        window.close()


def test_custom_boundaries_apply_as_ratios_to_different_batch_sizes(
    tmp_path: Path,
) -> None:
    boundaries = (0.2, 0.7)
    widths: list[list[int]] = []
    for width in (100, 250):
        source = tmp_path / f"batch-{width}.png"
        Image.new("RGBA", (width, 40), (20, 40, 80, 128)).save(source)
        results = process_image_splits(
            source,
            ProcessingOptions(output_format=OutputFormat.PNG),
            ImageSplitOptions(
                True,
                SplitDirection.VERTICAL,
                3,
                boundaries,
            ),
        )
        widths.append([result.width for result in results])
        for result in results:
            with Image.open(BytesIO(result.data)) as opened:
                assert opened.mode == "RGBA"
    assert widths == [[20, 50, 30], [50, 125, 75]]
