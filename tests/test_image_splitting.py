from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QSplitter

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
import quick_processing_tool.ui as ui_module
from quick_processing_tool.ui import MainWindow, PreviewCanvas, ProcessingWorker


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("length", "count", "expected_sizes"),
    [
        (1000, 2, [500, 500]),
        (1000, 3, [333, 333, 334]),
        (1001, 3, [333, 334, 334]),
        (1003, 6, [167, 167, 167, 167, 167, 168]),
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


@pytest.mark.parametrize(
    ("output_format", "pil_format", "keeps_alpha"),
    [
        (OutputFormat.PNG, "PNG", True),
        (OutputFormat.JPEG, "JPEG", False),
        (OutputFormat.WEBP, "WEBP", True),
    ],
)
def test_split_pipeline_reuses_supported_export_formats(
    tmp_path: Path,
    output_format: OutputFormat,
    pil_format: str,
    keeps_alpha: bool,
) -> None:
    source = tmp_path / f"formats-{output_format.value}.png"
    image = Image.new("RGBA", (1003, 24), (40, 120, 220, 96))
    image.save(source)

    results = process_image_splits(
        source,
        ProcessingOptions(
            output_format=output_format,
            jpeg_background=(12, 34, 56),
        ),
        ImageSplitOptions(True, SplitDirection.VERTICAL, 6),
    )

    assert len(results) == 6
    assert sum(result.width for result in results) == image.width
    for result in results:
        with Image.open(BytesIO(result.data)) as opened:
            assert opened.format == pil_format
            assert ("A" in opened.getbands()) is keeps_alpha
            opened.load()


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


def test_split_batch_mixed_resolution_missing_source_continues_with_custom_ratios(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "moved-source.png"
    Image.new("RGBA", (401, 83), (30, 80, 200, 120)).save(missing)
    source = tmp_path / "upscaled-result.png"
    original = Image.new("RGBA", (1003, 127), (120, 40, 220, 0))
    original.putdata(
        [
            (x % 256, y % 256, (x + y) % 256, (x * 5 + y * 3) % 256)
            for y in range(original.height)
            for x in range(original.width)
        ]
    )
    original.save(source)
    missing.unlink()

    output = tmp_path / "out"
    statuses: list[tuple[int, str, str]] = []
    summaries: list[tuple[int, int]] = []
    output_counts: list[int] = []
    worker = ProcessingWorker(
        [missing, source],
        ProcessingOptions(output_format=OutputFormat.PNG),
        False,
        "Custom folder",
        output,
        False,
        split_options=ImageSplitOptions(
            True,
            SplitDirection.VERTICAL,
            3,
            (0.23, 0.61),
        ),
    )
    worker.file_status.connect(lambda *args: statuses.append(args))
    worker.finished.connect(lambda *args: summaries.append(args))
    worker.outputs_saved.connect(output_counts.append)

    worker.run()

    assert summaries == [(1, 1)]
    assert output_counts == [3]
    assert any(row == 0 and status == "Missing" for row, status, _ in statuses)
    assert [path.name for path in sorted(output.glob("*.png"))] == [
        "upscaled-result_01.png",
        "upscaled-result_02.png",
        "upscaled-result_03.png",
    ]
    panels = [Image.open(path).convert("RGBA") for path in sorted(output.glob("*.png"))]
    try:
        rejoined = Image.new("RGBA", original.size)
        offset = 0
        for panel in panels:
            rejoined.paste(panel, (offset, 0))
            offset += panel.width
        assert offset == original.width
        assert rejoined.tobytes() == original.tobytes()
    finally:
        for panel in panels:
            panel.close()


def test_split_batch_mixed_resolution_custom_ratios_saves_all_sources(
    tmp_path: Path,
) -> None:
    originals: dict[str, Image.Image] = {}
    sources: list[Path] = []
    for name, size in (("original", (401, 83)), ("upscaled-result", (1003, 127))):
        image = Image.new("RGBA", size)
        image.putdata(
            [
                (x % 256, y % 256, (x + y) % 256, (x * 5 + y * 3) % 256)
                for y in range(image.height)
                for x in range(image.width)
            ]
        )
        path = tmp_path / f"{name}.png"
        image.save(path)
        originals[name] = image
        sources.append(path)

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
        split_options=ImageSplitOptions(
            True,
            SplitDirection.VERTICAL,
            3,
            (0.23, 0.61),
        ),
    )
    worker.finished.connect(lambda *args: summaries.append(args))
    worker.outputs_saved.connect(output_counts.append)

    worker.run()

    assert summaries == [(2, 0)]
    assert output_counts == [6]
    assert [path.name for path in sorted(output.glob("*.png"))] == [
        "original_01.png",
        "original_02.png",
        "original_03.png",
        "upscaled-result_01.png",
        "upscaled-result_02.png",
        "upscaled-result_03.png",
    ]
    for name, original in originals.items():
        panels = [
            Image.open(output / f"{name}_{index:02d}.png").convert("RGBA")
            for index in range(1, 4)
        ]
        try:
            rejoined = Image.new("RGBA", original.size)
            offset = 0
            for panel in panels:
                rejoined.paste(panel, (offset, 0))
                offset += panel.width
            assert offset == original.width
            assert rejoined.tobytes() == original.tobytes()
        finally:
            for panel in panels:
                panel.close()


def test_split_batch_rolls_back_failed_source_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (120, 40), (10, 20, 30, 140)).save(first)
    Image.new("RGBA", (120, 40), (40, 50, 60, 200)).save(second)
    output = tmp_path / "out"
    original_write = ui_module.write_processed
    writes = 0

    def fail_once(result, destination, preserve_timestamp) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated write failure")
        original_write(result, destination, preserve_timestamp)

    monkeypatch.setattr(ui_module, "write_processed", fail_once)
    summaries: list[tuple[int, int]] = []
    worker = ProcessingWorker(
        [first, second],
        ProcessingOptions(output_format=OutputFormat.PNG),
        False,
        "Custom folder",
        output,
        False,
        split_options=ImageSplitOptions(True, SplitDirection.VERTICAL, 3, (0.3, 0.7)),
    )
    worker.finished.connect(lambda *args: summaries.append(args))

    worker.run()

    assert summaries == [(1, 1)]
    assert not list(output.glob("first_*.png"))
    assert [path.name for path in sorted(output.glob("second_*.png"))] == [
        "second_01.png",
        "second_02.png",
        "second_03.png",
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


@pytest.mark.parametrize("direction", list(SplitDirection))
def test_split_guides_stay_on_exact_boundaries_at_fit_and_zoom(
    app: QApplication,
    direction: SplitDirection,
) -> None:
    preview = PreviewCanvas()
    try:
        preview.resize(720, 420)
        preview.show()
        image = QImage(1003, 603, QImage.Format.Format_RGBA8888)
        image.fill(0xFF1E6091)
        preview.set_image(image)
        preview.set_split_guides(True, direction, 4)
        app.processEvents()

        axis_length = image.width() if direction is SplitDirection.VERTICAL else image.height()
        expected_positions = partition_edges(axis_length, 4)[1:-1]

        for view_mode in ("fit", "100%", "200%", "400%"):
            if view_mode == "fit":
                preview.set_zoom_factor(None)
                expected_scale = None
            else:
                expected_scale = {"100%": 1.0, "200%": 2.0, "400%": 4.0}[view_mode]
                preview.set_zoom_factor(expected_scale)
            app.processEvents()

            scene_positions: list[float] = []
            viewport_positions: list[int] = []
            for guide in preview._guide_items:
                line = guide.line()
                assert guide.pen().isCosmetic()
                if direction is SplitDirection.VERTICAL:
                    assert line.x1() == line.x2()
                    scene_positions.append(line.x1())
                    viewport_positions.append(
                        preview.mapFromScene(QPointF(line.x1(), 0.0)).x()
                    )
                else:
                    assert line.y1() == line.y2()
                    scene_positions.append(line.y1())
                    viewport_positions.append(
                        preview.mapFromScene(QPointF(0.0, line.y1())).y()
                    )

            assert scene_positions == [float(value) for value in expected_positions]
            scale = (
                preview.transform().m11()
                if direction is SplitDirection.VERTICAL
                else preview.transform().m22()
            )
            if expected_scale is not None:
                assert scale == pytest.approx(expected_scale)
            for index in range(len(expected_positions) - 1):
                expected_delta = (expected_positions[index + 1] - expected_positions[index]) * scale
                actual_delta = viewport_positions[index + 1] - viewport_positions[index]
                assert actual_delta == pytest.approx(expected_delta, abs=1.5)
    finally:
        preview.close()


@pytest.mark.parametrize(
    ("width", "height", "minimum_preview_width"),
    [(900, 620, 360), (1180, 720, 400)],
)
def test_quick_split_controls_keep_compact_layout_without_horizontal_scroll(
    app: QApplication,
    tmp_path: Path,
    width: int,
    height: int,
    minimum_preview_width: int,
) -> None:
    source = tmp_path / f"layout-{width}.png"
    Image.new("RGB", (1003, 603), "navy").save(source)
    window = MainWindow()
    try:
        window.setMinimumSize(0, 0)
        window.resize(width, height)
        window.show()
        window.navigation.setCurrentIndex(window.quick_tab)
        window.load_paths([source])
        _wait_for_quick_preview(app, window)

        split_section = next(
            section for section in window.quick_sections
            if section.toggle.text() == "画像分割"
        )
        split_section.toggle.setChecked(True)
        window.split_enable_check.setChecked(True)
        app.processEvents()

        workspace = window.navigation.currentWidget()
        assert isinstance(workspace, QSplitter)
        assert workspace.widget(0).width() == 250
        assert workspace.widget(1).width() >= minimum_preview_width
        assert workspace.widget(2).width() >= 180
        assert window.quick_settings_scroll.horizontalScrollBar().maximum() == 0
        assert split_section.content.width() <= window.quick_settings_scroll.viewport().width()
        assert len(window.preview._guide_items) == 3

        for label, expected_scale in (("100%", 1.0), ("200%", 2.0), ("400%", 4.0)):
            window.preview_zoom_buttons[label].click()
            app.processEvents()
            assert window.preview.transform().m11() == pytest.approx(expected_scale)
            assert len(window.preview._guide_items) == 3
        window.preview_zoom_buttons["全体表示"].click()
        app.processEvents()
        assert window.preview._zoom_factor is None

        last_count_button = window.split_count_buttons[6]
        button_right = last_count_button.mapTo(
            window.quick_settings_scroll.viewport(),
            last_count_button.rect().bottomRight(),
        ).x()
        assert button_right <= window.quick_settings_scroll.viewport().width()
    finally:
        _wait_for_quick_preview(app, window)
        window.close()
