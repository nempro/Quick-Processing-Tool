from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from quick_processing_tool.image_splitting import (
    ImageSplitOptions,
    SplitDirection,
    partition_edges,
    split_boxes,
    split_image,
)
from quick_processing_tool.models import OutputFormat, ProcessingOptions
from quick_processing_tool.naming import unique_split_output_paths
from quick_processing_tool.pipeline import process_image_splits
from quick_processing_tool.ui import MainWindow, ProcessingWorker


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("length", "count", "expected_sizes"),
    [
        (1000, 2, [500, 500]),
        (1000, 3, [333, 333, 334]),
        (1001, 3, [333, 334, 334]),
    ],
)
def test_partition_edges_cover_axis_once(
    length: int,
    count: int,
    expected_sizes: list[int],
) -> None:
    edges = partition_edges(length, count)
    sizes = [right - left for left, right in zip(edges, edges[1:])]

    assert edges[0] == 0
    assert edges[-1] == length
    assert sizes == expected_sizes
    assert sum(sizes) == length


@pytest.mark.parametrize("direction", list(SplitDirection))
@pytest.mark.parametrize("count", [2, 3, 4, 5, 6])
def test_split_and_rejoin_is_pixel_exact(
    direction: SplitDirection,
    count: int,
) -> None:
    image = Image.new("RGBA", (1001, 607))
    image.putdata(
        [
            (x % 256, y % 256, (x + y) % 256, (x * 3 + y * 5) % 256)
            for y in range(image.height)
            for x in range(image.width)
        ]
    )
    boxes = split_boxes(image.width, image.height, direction, count)
    panels = split_image(image, direction, count)
    rejoined = Image.new(image.mode, image.size)
    for panel, box in zip(panels, boxes, strict=True):
        rejoined.paste(panel, box[:2])

    assert len(panels) == count
    assert rejoined.tobytes() == image.tobytes()
    if direction is SplitDirection.VERTICAL:
        assert sum(panel.width for panel in panels) == image.width
        assert all(panel.height == image.height for panel in panels)
    else:
        assert sum(panel.height for panel in panels) == image.height
        assert all(panel.width == image.width for panel in panels)


def test_panel_order_is_left_to_right_and_top_to_bottom() -> None:
    vertical = Image.new("RGB", (6, 2))
    for x, color in enumerate(("red", "red", "green", "green", "blue", "blue")):
        for y in range(vertical.height):
            vertical.putpixel((x, y), Image.new("RGB", (1, 1), color).getpixel((0, 0)))
    vertical_panels = split_image(vertical, SplitDirection.VERTICAL, 3)
    assert [panel.getpixel((0, 0)) for panel in vertical_panels] == [
        (255, 0, 0),
        (0, 128, 0),
        (0, 0, 255),
    ]

    horizontal = vertical.transpose(Image.Transpose.TRANSPOSE)
    horizontal_panels = split_image(horizontal, SplitDirection.HORIZONTAL, 3)
    assert [panel.getpixel((0, 0)) for panel in horizontal_panels] == [
        (255, 0, 0),
        (0, 128, 0),
        (0, 0, 255),
    ]


def test_split_output_names_are_ordered_and_collision_safe(tmp_path: Path) -> None:
    source = tmp_path / "illustration.png"
    Image.new("RGB", (12, 4), "blue").save(source)
    output = tmp_path / "out"

    first = unique_split_output_paths(output, source, "PNG", 3)
    assert [path.name for path in first] == [
        "illustration_01.png",
        "illustration_02.png",
        "illustration_03.png",
    ]
    first[0].write_bytes(b"existing")
    second = unique_split_output_paths(output, source, "PNG", 3)
    assert [path.name for path in second] == [
        "illustration_2_01.png",
        "illustration_2_02.png",
        "illustration_2_03.png",
    ]


def test_png_split_pipeline_preserves_alpha_and_full_region(tmp_path: Path) -> None:
    source = tmp_path / "alpha.png"
    original = Image.new("RGBA", (1001, 9))
    original.putdata(
        [
            (40, 120, 220, (x * 7 + y * 11) % 256)
            for y in range(original.height)
            for x in range(original.width)
        ]
    )
    original.save(source)
    split_options = ImageSplitOptions(True, SplitDirection.VERTICAL, 3)

    results = process_image_splits(
        source,
        ProcessingOptions(output_format=OutputFormat.PNG),
        split_options,
    )
    panels: list[Image.Image] = []
    for result in results:
        with Image.open(BytesIO(result.data)) as opened:
            panels.append(opened.convert("RGBA").copy())
    rejoined = Image.new("RGBA", original.size)
    offset = 0
    for panel in panels:
        rejoined.paste(panel, (offset, 0))
        offset += panel.width

    assert offset == original.width
    assert rejoined.tobytes() == original.tobytes()


def test_batch_worker_saves_every_panel_in_natural_order(tmp_path: Path) -> None:
    sources: list[Path] = []
    for name, colors in (
        ("A", ("red", "green", "blue", "yellow")),
        ("B", ("cyan", "magenta", "black", "white")),
    ):
        source = tmp_path / f"{name}.png"
        image = Image.new("RGB", (12, 4))
        for index, color in enumerate(colors):
            panel = Image.new("RGB", (3, 4), color)
            image.paste(panel, (index * 3, 0))
        image.save(source)
        sources.append(source)
    output = tmp_path / "out"
    summaries: list[tuple[int, int]] = []
    output_counts: list[int] = []
    worker = ProcessingWorker(
        sources,
        ProcessingOptions(output_format=OutputFormat.PNG),
        False,
        "Custom folder",
        output,
        False,
        split_options=ImageSplitOptions(True, SplitDirection.VERTICAL, 4),
    )
    worker.finished.connect(lambda succeeded, failed: summaries.append((succeeded, failed)))
    worker.outputs_saved.connect(output_counts.append)

    worker.run()

    assert summaries == [(2, 0)]
    assert output_counts == [8]
    assert [path.name for path in sorted(output.glob("*.png"))] == [
        "A_01.png",
        "A_02.png",
        "A_03.png",
        "A_04.png",
        "B_01.png",
        "B_02.png",
        "B_03.png",
        "B_04.png",
    ]


def _wait_for_quick_preview(app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and window._quick_preview_thread is not None:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window._quick_preview_thread is None


def test_quick_split_controls_update_guides_immediately(
    app: QApplication,
    tmp_path: Path,
) -> None:
    source = tmp_path / "preview.png"
    Image.new("RGB", (600, 300), "navy").save(source)
    window = MainWindow()
    try:
        window.load_paths([source])
        _wait_for_quick_preview(app, window)
        assert window.split_options() == ImageSplitOptions()
        assert window.preview._guide_items == []

        window.split_enable_check.click()
        app.processEvents()
        assert len(window.preview._guide_items) == 3
        assert not window.copy_action.isEnabled()

        for count in range(2, 7):
            window.split_count_buttons[count].click()
            app.processEvents()
            assert window.split_options().count == count
            assert len(window.preview._guide_items) == count - 1

        window.split_count_buttons[4].click()
        window.split_direction_buttons[SplitDirection.HORIZONTAL].click()
        app.processEvents()
        assert window.split_options() == ImageSplitOptions(
            True,
            SplitDirection.HORIZONTAL,
            4,
        )
        assert all(
            guide.line().y1() == guide.line().y2()
            for guide in window.preview._guide_items
        )
        assert "4枚" in window.info_label.text()
        assert "上から下" in window.info_label.text()
        window._active_split_options = window.split_options()
        window._saved_output_count = 4
        window._on_finished(1, 0)
        assert window.statusBar().currentMessage() == "完了 · 4枚の分割画像を保存しました"


        window.reset_settings()
        assert window.split_options() == ImageSplitOptions()
        assert window.preview._guide_items == []
    finally:
        _wait_for_quick_preview(app, window)
        window.close()
