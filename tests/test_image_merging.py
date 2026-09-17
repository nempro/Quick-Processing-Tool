from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from quick_processing_tool.image_merging import (
    ImageMergeOptions,
    MergeAlignment,
    MergeDirection,
    MergeSizeMode,
    merge_images,
    merged_dimensions,
    process_image_merge,
)
from quick_processing_tool.models import OutputFormat, ProcessingOptions


def solid(color: str, size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, color)


def test_equal_size_horizontal_and_vertical_are_pixel_exact() -> None:
    red = solid("red", (500, 500))
    blue = solid("blue", (500, 500))
    horizontal = merge_images(
        [red, blue], ImageMergeOptions(direction=MergeDirection.HORIZONTAL)
    )
    vertical = merge_images(
        [red, blue], ImageMergeOptions(direction=MergeDirection.VERTICAL)
    )
    assert horizontal.size == (1000, 500)
    assert horizontal.crop((0, 0, 500, 500)).tobytes() == red.tobytes()
    assert horizontal.crop((500, 0, 1000, 500)).tobytes() == blue.tobytes()
    assert vertical.size == (500, 1000)
    assert vertical.crop((0, 0, 500, 500)).tobytes() == red.tobytes()
    assert vertical.crop((0, 500, 500, 1000)).tobytes() == blue.tobytes()


def test_order_gap_background_and_original_alignment() -> None:
    images = [solid("red", (2, 2)), solid("green", (2, 4)), solid("blue", (2, 2))]
    result = merge_images(
        images,
        ImageMergeOptions(
            direction=MergeDirection.HORIZONTAL,
            alignment=MergeAlignment.END,
            gap=1,
            background=(0, 0, 0),
        ),
    )
    assert result.size == (8, 4)
    assert result.getpixel((0, 3)) == (255, 0, 0)
    assert result.getpixel((3, 0)) == (0, 128, 0)
    assert result.getpixel((6, 3)) == (0, 0, 255)
    assert result.getpixel((2, 0)) == (0, 0, 0)
    assert result.getpixel((5, 1)) == (0, 0, 0)


def test_match_height_and_width_preserve_aspect_ratio() -> None:
    wide = solid("red", (80, 20))
    tall = solid("blue", (10, 40))
    horizontal = merge_images(
        [wide, tall],
        ImageMergeOptions(
            direction=MergeDirection.HORIZONTAL, size_mode=MergeSizeMode.MATCH_HEIGHT
        ),
    )
    vertical = merge_images(
        [wide, tall],
        ImageMergeOptions(
            direction=MergeDirection.VERTICAL, size_mode=MergeSizeMode.MATCH_WIDTH
        ),
    )
    assert horizontal.size == (170, 40)  # 160x40 + 10x40
    assert vertical.size == (80, 340)  # 80x20 + 80x320
    assert merged_dimensions([(160, 40), (10, 40)], ImageMergeOptions()) == (170, 40)


@pytest.mark.parametrize(
    ("output_format", "expected_format", "keeps_alpha"),
    [
        (OutputFormat.PNG, "PNG", True),
        (OutputFormat.WEBP, "WEBP", True),
        (OutputFormat.JPEG, "JPEG", False),
    ],
)
def test_merge_export_reuses_supported_formats_and_alpha_contract(
    tmp_path: Path,
    output_format: OutputFormat,
    expected_format: str,
    keeps_alpha: bool,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (12, 8), (200, 30, 20, 0)).save(first)
    Image.new("RGBA", (12, 8), (20, 30, 200, 140)).save(second)
    result = process_image_merge(
        [first, second],
        ProcessingOptions(output_format=output_format, jpeg_background=(12, 34, 56)),
        ImageMergeOptions(background=None),
    )
    assert result.size_bytes > 0
    assert (result.width, result.height) == (24, 8)
    with Image.open(BytesIO(result.data)) as opened:
        opened.load()
        assert opened.format == expected_format
        rgba = opened.convert("RGBA")
        assert rgba.size == (24, 8)
        if keeps_alpha:
            assert rgba.getpixel((0, 0))[3] == 0
            assert rgba.getpixel((20, 3))[3] == 140
        else:
            assert max(abs(actual - expected) for actual, expected in zip(rgba.getpixel((0, 0))[:3], (12, 34, 56))) <= 2
            assert rgba.getpixel((0, 0))[3] == 255


def test_merge_requires_two_to_six_inputs() -> None:
    image = solid("red", (1, 1))
    with pytest.raises(ValueError):
        merge_images([image], ImageMergeOptions())
    with pytest.raises(ValueError):
        merge_images([image] * 7, ImageMergeOptions())


def test_quick_merge_order_preview_and_export(tmp_path: Path) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for name, color in (("red", "red"), ("green", "green"), ("blue", "blue")):
        path = tmp_path / f"{name}.png"
        solid(color, (20, 10)).save(path)
        paths.append(path)
    output = tmp_path / "out"
    output.mkdir()
    window = MainWindow()
    try:
        window.load_paths(paths)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._quick_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)
        window.merge_enable_check.setChecked(True)
        while time.monotonic() < deadline and window._merge_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)
        assert window.merge_paths == paths
        assert "60 × 10" in window.merge_summary.text()
        window.merge_list.setCurrentItem(window.merge_list.topLevelItem(2))
        window.move_merge_item(-1)
        window.move_merge_item(-1)
        while time.monotonic() < deadline and window._merge_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)
        assert [path.name for path in window.merge_paths] == ["blue.png", "red.png", "green.png"]
        window.destination_combo.setCurrentIndex(window.destination_combo.findData("Custom folder"))
        window.custom_folder = output
        window._update_current_destination_display()
        window.export_all()
        while time.monotonic() < deadline and window._thread is not None:
            app.processEvents()
            time.sleep(0.005)
        saved = output / "blue.png"
        assert saved.is_file()
        with Image.open(saved) as opened:
            assert opened.size == (60, 10)
            assert opened.convert("RGB").getpixel((0, 0)) == (0, 0, 255)
            assert opened.convert("RGB").getpixel((20, 0)) == (255, 0, 0)
    finally:
        window.close()


@pytest.mark.parametrize("gap", [0, 2, 20, 100])
def test_vertical_merge_export_keeps_gap_dimensions_and_pixels(tmp_path: Path, gap: int) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    solid("red", (832, 832)).save(first)
    solid("blue", (832, 832)).save(second)
    result = process_image_merge(
        [first, second],
        ProcessingOptions(output_format=OutputFormat.PNG),
        ImageMergeOptions(direction=MergeDirection.VERTICAL, gap=gap),
    )
    with Image.open(BytesIO(result.data)) as opened:
        image = opened.convert("RGB")
        assert image.size == (832, 1664 + gap)
        assert image.getpixel((100, 100)) == (255, 0, 0)
        assert image.getpixel((100, 832 + gap + 100)) == (0, 0, 255)
        if gap:
            assert image.getpixel((100, 832)) == (255, 255, 255)


def test_quick_merge_gap_keyboard_and_rapid_updates_are_safe(tmp_path: Path) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for name, color in (("red", "red"), ("blue", "blue")):
        path = tmp_path / f"{name}.png"
        solid(color, (832, 832)).save(path)
        paths.append(path)

    def settle(window: MainWindow) -> None:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window._merge_preview_thread is None
                and not window._merge_gap_refresh_timer.isActive()
            ):
                return
            time.sleep(0.005)
        raise AssertionError("merge preview did not settle")

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.load_paths(paths)
        window.merge_enable_check.setChecked(True)
        window.merge_direction_buttons[MergeDirection.VERTICAL].click()
        settle(window)

        line_edit = window.merge_gap_spin.lineEdit()
        line_edit.setFocus()
        line_edit.selectAll()
        QTest.keyClick(line_edit, "2")
        settle(window)
        assert window.merge_gap_spin.value() == 2
        assert "832 × 1666" in window.merge_summary.text()
        assert window.preview._image_item.pixmap().size().width() == 832
        assert window.preview._image_item.pixmap().size().height() == 1666

        QTest.keyClick(line_edit, "0")
        settle(window)
        assert window.merge_gap_spin.value() == 20
        assert "832 × 1684" in window.merge_summary.text()
        assert window.preview._image_item.pixmap().size().width() == 832
        assert window.preview._image_item.pixmap().size().height() == 1684
        assert line_edit.hasFocus()

        QTest.mouseMove(
            window.merge_gap_spin,
            QPoint(window.merge_gap_spin.width() - 3, window.merge_gap_spin.height() // 2),
        )
        assert line_edit.hasFocus()

        QTest.keyClick(line_edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(line_edit, "100")
        QTest.keyClick(line_edit, Qt.Key.Key_Return)
        settle(window)
        assert window.merge_gap_spin.value() == 100

        QTest.keyClick(line_edit, Qt.Key.Key_Down)
        settle(window)
        assert window.merge_gap_spin.value() == 99

        QTest.keyClick(line_edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(line_edit, Qt.Key.Key_Backspace)
        QTest.qWait(40)
        app.processEvents()
        assert window.merge_gap_spin.value() == 99
        assert not window._merge_gap_refresh_timer.isActive()
        QTest.keyClick(line_edit, "0")
        QTest.keyClick(line_edit, Qt.Key.Key_Return)
        settle(window)
        assert window.merge_gap_spin.value() == 0

        QTest.keyClick(line_edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(line_edit, "20")
        window.merge_list.setFocus()
        app.processEvents()
        settle(window)
        assert window.merge_gap_spin.value() == 20

        QTest.mouseClick(
            window.merge_gap_spin,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(window.merge_gap_spin.width() - 3, window.merge_gap_spin.height() // 4),
        )
        settle(window)
        assert window.merge_gap_spin.value() == 21
        assert line_edit.hasFocus()

        window.merge_list.setFocus()
        gap_before_wheel = window.merge_gap_spin.value()
        wheel = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(window.merge_gap_spin, wheel)
        assert window.merge_gap_spin.value() == gap_before_wheel

        for value in (0, 1, 2, 3, 10, 20, 99, 100, 1):
            window.merge_gap_spin.setValue(value)
        settle(window)
        assert window.merge_gap_spin.value() == 1
        assert "832 × 1665" in window.merge_summary.text()

        for value, expected_height in (("-2", 1662), ("-20", 1644), ("20", 1684)):
            line_edit.setFocus()
            line_edit.selectAll()
            QTest.keyClicks(line_edit, value)
            QTest.keyClick(line_edit, Qt.Key.Key_Return)
            settle(window)
            assert window.merge_gap_spin.value() == int(value)
            assert f"832 × {expected_height}" in window.merge_summary.text()
        window.merge_direction_buttons[MergeDirection.HORIZONTAL].click()
        window.merge_gap_spin.setValue(2)
        settle(window)
        assert "1666 × 832" in window.merge_summary.text()
        assert window.preview._image_item.pixmap().size().width() == 1666
        assert window.preview._image_item.pixmap().size().height() == 832
    finally:
        window.close()



def test_grid_layouts_keep_order_gap_and_empty_cells() -> None:
    colors = ["red", "green", "blue", "yellow", "magenta", "cyan"]
    images = [solid(color, (10, 10)) for color in colors]
    grid_3x2 = merge_images(
        images,
        ImageMergeOptions(direction=MergeDirection.GRID, columns=3, background=(0, 0, 0)),
    )
    assert grid_3x2.size == (30, 20)
    assert grid_3x2.getpixel((1, 1)) == (255, 0, 0)
    assert grid_3x2.getpixel((11, 1)) == (0, 128, 0)
    assert grid_3x2.getpixel((21, 1)) == (0, 0, 255)
    assert grid_3x2.getpixel((1, 11)) == (255, 255, 0)

    grid_2x3 = merge_images(
        images,
        ImageMergeOptions(direction=MergeDirection.GRID, columns=2, background=(0, 0, 0)),
    )
    assert grid_2x3.size == (20, 30)
    assert grid_2x3.getpixel((11, 21)) == (0, 255, 255)

    incomplete = merge_images(
        images[:5],
        ImageMergeOptions(direction=MergeDirection.GRID, columns=3, background=(0, 0, 0)),
    )
    assert incomplete.size == (30, 20)
    assert incomplete.getpixel((21, 11)) == (0, 0, 0)

    nine = merge_images(
        [solid("red", (10, 10)) for _ in range(9)],
        ImageMergeOptions(direction=MergeDirection.GRID, columns=3),
    )
    assert nine.size == (30, 30)
    assert nine.getpixel((21, 21)) == (255, 0, 0)


@pytest.mark.parametrize("gap", [0, 2, 20, 100])
def test_500_square_grid_dimensions_and_gap(gap: int) -> None:
    images = [solid(color, (500, 500)) for color in ("red", "green", "blue", "yellow")]
    result = merge_images(
        images,
        ImageMergeOptions(direction=MergeDirection.GRID, columns=2, gap=gap),
    )
    assert result.size == (1000 + gap, 1000 + gap)
    assert result.getpixel((1, 1)) == (255, 0, 0)
    assert result.getpixel((500 + gap + 1, 500 + gap + 1)) == (255, 255, 0)


def test_grid_size_modes_preserve_aspect_and_alpha() -> None:
    wide = Image.new("RGBA", (80, 20), (255, 0, 0, 120))
    tall = Image.new("RGBA", (20, 80), (0, 0, 255, 180))
    matched_width = merge_images(
        [wide, tall, wide, tall],
        ImageMergeOptions(direction=MergeDirection.GRID, columns=2, size_mode=MergeSizeMode.MATCH_WIDTH, background=None),
    )
    assert matched_width.size == (160, 640)
    assert matched_width.getpixel((1, 151))[3] == 120
    cell_fit = merge_images(
        [wide, tall, wide, tall],
        ImageMergeOptions(direction=MergeDirection.GRID, columns=2, size_mode=MergeSizeMode.CELL_FIT, background=None),
    )
    assert cell_fit.size == (160, 160)
    assert cell_fit.getpixel((1, 31))[3] == 120


@pytest.mark.parametrize(
    ("output_format", "expected_format", "keeps_alpha"),
    [
        (OutputFormat.PNG, "PNG", True),
        (OutputFormat.WEBP, "WEBP", True),
        (OutputFormat.JPEG, "JPEG", False),
    ],
)
def test_grid_export_reuses_format_and_alpha_contract(
    tmp_path: Path, output_format: OutputFormat, expected_format: str, keeps_alpha: bool
) -> None:
    paths: list[Path] = []
    for index, alpha in enumerate((0, 80, 160, 255)):
        path = tmp_path / f"grid-{index}.png"
        Image.new("RGBA", (12, 8), (20 * index, 30, 200, alpha)).save(path)
        paths.append(path)
    result = process_image_merge(
        paths,
        ProcessingOptions(output_format=output_format, jpeg_background=(12, 34, 56)),
        ImageMergeOptions(direction=MergeDirection.GRID, columns=2, background=None),
    )
    with Image.open(BytesIO(result.data)) as opened:
        opened.load()
        assert opened.format == expected_format
        assert opened.size == (24, 16)
        if keeps_alpha:
            assert opened.convert("RGBA").getpixel((1, 1))[3] == 0
        else:
            assert opened.convert("RGBA").getpixel((1, 1))[3] == 255


def test_quick_grid_preview_and_export_follow_order(tmp_path: Path) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for name, color in (("red", "red"), ("green", "green"), ("blue", "blue"), ("yellow", "yellow")):
        path = tmp_path / f"{name}.png"
        solid(color, (20, 10)).save(path)
        paths.append(path)
    output = tmp_path / "out"
    output.mkdir()
    window = MainWindow()
    try:
        window.load_paths(paths)
        window.merge_enable_check.setChecked(True)
        window.merge_direction_buttons[MergeDirection.GRID].click()
        window.merge_grid_columns_buttons[2].click()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._merge_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)
        assert "40 × 20" in window.merge_summary.text()
        window.destination_combo.setCurrentIndex(window.destination_combo.findData("Custom folder"))
        window.custom_folder = output
        window.export_all()
        while time.monotonic() < deadline and window._thread is not None:
            app.processEvents()
            time.sleep(0.005)
        saved = output / "red.png"
        with Image.open(saved) as opened:
            image = opened.convert("RGB")
            assert image.size == (40, 20)
            assert image.getpixel((1, 1)) == (255, 0, 0)
            assert image.getpixel((21, 11)) == (255, 255, 0)
    finally:
        window.close()



def test_quick_merge_drop_reorder_updates_grid_preview_and_export(tmp_path: Path) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    colors = (
        ("red", "red"),
        ("green", "green"),
        ("blue", "blue"),
        ("yellow", "yellow"),
        ("magenta", "magenta"),
        ("cyan", "cyan"),
    )
    paths: list[Path] = []
    for name, color in colors:
        path = tmp_path / f"{name}.png"
        solid(color, (10, 10)).save(path)
        paths.append(path)
    output = tmp_path / "out"
    output.mkdir()

    def settle(window: MainWindow) -> None:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.processEvents()
            if window._merge_preview_thread is None:
                return
            time.sleep(0.005)
        raise AssertionError("merge preview did not settle")

    window = MainWindow()
    try:
        window.load_paths(paths)
        window.merge_enable_check.setChecked(True)
        window.merge_direction_buttons[MergeDirection.GRID].click()
        window.merge_grid_columns_buttons[3].click()
        settle(window)
        assert window.merge_list.dragDropMode().name == "NoDragDrop"  # direct mouse-drag implementation

        # This is the post-Drop tree state for dragging item 6 to position 2.
        moved = window.merge_list.takeTopLevelItem(5)
        window.merge_list.insertTopLevelItem(1, moved)
        window.merge_list.setCurrentItem(moved)
        window._merge_items_reordered()
        settle(window)

        expected = ["red.png", "cyan.png", "green.png", "blue.png", "yellow.png", "magenta.png"]
        assert [path.name for path in window.merge_paths] == expected
        assert [window.merge_list.topLevelItem(index).text(0) for index in range(6)] == [
            f"{index}. {name}" for index, name in enumerate(expected, start=1)
        ]
        assert "グリッド 3列" in window.merge_summary.text()
        preview = window.preview._image_item.pixmap().toImage()
        assert preview.pixelColor(1, 1).name() == "#ff0000"
        assert preview.pixelColor(11, 1).name() == "#00ffff"
        assert preview.pixelColor(21, 1).name() == "#008000"
        assert preview.pixelColor(1, 11).name() == "#0000ff"

        window.destination_combo.setCurrentIndex(window.destination_combo.findData("Custom folder"))
        window.custom_folder = output
        window.export_all()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._thread is not None:
            app.processEvents()
            time.sleep(0.005)
        with Image.open(output / "red.png") as opened:
            image = opened.convert("RGB")
            assert image.size == (30, 20)
            assert image.getpixel((1, 1)) == (255, 0, 0)
            assert image.getpixel((11, 1)) == (0, 255, 255)
            assert image.getpixel((21, 1)) == (0, 128, 0)
            assert image.getpixel((1, 11)) == (0, 0, 255)
    finally:
        window.close()

@pytest.mark.parametrize(
    ("count", "source", "target"),
    [
        (2, 0, 2),   # first -> last
        (6, 5, 0),   # last -> first
        (6, 2, 2),   # same-position drop
        (9, 4, 1),   # middle -> middle in a grid-sized queue
    ],
)
def test_merge_queue_drop_reorder_renumbers_two_six_and_nine_items(
    tmp_path: Path, count: int, source: int, target: int
) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"{index + 1}.png"
        solid("red", (2, 2)).save(path)
        paths.append(path)
    window = MainWindow()
    try:
        window.load_paths(paths)
        if count == 9:
            window.merge_direction_buttons[MergeDirection.GRID].click()
        window.merge_enable_check.setChecked(True)
        app.processEvents()

        if source != target:
            moved = window.merge_list.takeTopLevelItem(source)
            if target >= window.merge_list.topLevelItemCount():
                window.merge_list.addTopLevelItem(moved)
            else:
                window.merge_list.insertTopLevelItem(target, moved)
        window._merge_items_reordered()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._merge_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)

        expected = list(paths)
        if source != target:
            expected.insert(target, expected.pop(source))
        assert window.merge_paths == expected
        assert [window.merge_list.topLevelItem(index).text(0) for index in range(count)] == [
            f"{index}. {path.name}" for index, path in enumerate(expected, start=1)
        ]
    finally:
        window.close()

@pytest.mark.parametrize("direction", [MergeDirection.HORIZONTAL, MergeDirection.VERTICAL])
@pytest.mark.parametrize("gap", [-1, -20, -100, 0, 20])
def test_linear_negative_gap_overlap_preserves_z_order_and_dimensions(
    direction: MergeDirection, gap: int
) -> None:
    first = solid("red", (40, 30))
    later = solid("blue", (40, 30))
    options = ImageMergeOptions(direction=direction, gap=gap)
    result = merge_images([first, later], options)
    primary = 40 if direction is MergeDirection.HORIZONTAL else 30
    effective_gap = max(gap, 16 - primary) if gap < 0 else gap
    assert result.size == (
        (primary * 2 + effective_gap, 30)
        if direction is MergeDirection.HORIZONTAL
        else (40, primary * 2 + effective_gap)
    )
    if gap < 0:
        overlap_start = primary + effective_gap
        pixel = (
            result.getpixel((overlap_start, 1))
            if direction is MergeDirection.HORIZONTAL
            else result.getpixel((1, overlap_start))
        )
        assert pixel == (0, 0, 255)


def test_negative_gap_clamps_for_different_sizes_and_retains_visible_regions() -> None:
    images = [solid("red", (40, 10)), solid("green", (20, 10)), solid("blue", (60, 10))]
    result = merge_images(images, ImageMergeOptions(gap=-100))
    # The 20 px input sets the safe overlap clamp to -4 px, leaving 16 px visible.
    assert result.size == (112, 10)
    assert result.getpixel((35, 1)) == (255, 0, 0)
    assert result.getpixel((36, 1)) == (0, 128, 0)
    assert result.getpixel((52, 1)) == (0, 0, 255)


def test_negative_gap_alpha_composites_later_queue_images_on_top() -> None:
    first = Image.new("RGBA", (40, 20), (255, 0, 0, 255))
    later = Image.new("RGBA", (40, 20), (0, 0, 255, 128))
    result = merge_images(
        [first, later],
        ImageMergeOptions(gap=-20, background=None),
    )
    red, green, blue, alpha = result.getpixel((20, 1))
    assert alpha == 255
    assert blue > red > 0
    assert green == 0


@pytest.mark.parametrize("gap", [-100, -20, -1, 0, 20, 100])
def test_grid_gap_keeps_dimensions_and_queue_z_order(gap: int) -> None:
    images = [solid(color, (40, 40)) for color in ("red", "green", "blue", "yellow")]
    result = merge_images(images, ImageMergeOptions(direction=MergeDirection.GRID, columns=2, gap=gap))
    effective_gap = max(gap, 16 - 40) if gap < 0 else gap
    advance = 40 + effective_gap
    assert result.size == (80 + effective_gap, 80 + effective_gap)
    assert result.getpixel((1, 1)) == (255, 0, 0)
    assert result.getpixel((advance + 1, 1)) == (0, 128, 0)
    assert result.getpixel((1, advance + 1)) == (0, 0, 255)
    assert result.getpixel((advance + 1, advance + 1)) == (255, 255, 0)


def test_grid_negative_gap_keeps_alpha_and_incomplete_cells() -> None:
    images = [
        Image.new("RGBA", (40, 40), (255, 0, 0, 255)),
        Image.new("RGBA", (40, 40), (0, 255, 0, 160)),
        Image.new("RGBA", (40, 40), (0, 0, 255, 128)),
        Image.new("RGBA", (40, 40), (255, 255, 0, 255)),
        Image.new("RGBA", (40, 40), (255, 0, 255, 255)),
    ]
    result = merge_images(
        images,
        ImageMergeOptions(direction=MergeDirection.GRID, columns=3, gap=-20, background=None),
    )
    assert result.size == (80, 60)
    assert result.getpixel((1, 1))[3] == 255
    # Queue order 1 → 2 → 3 → 4 → 5 determines the overlap z-order.
    assert result.getpixel((1, 21)) == (255, 255, 0, 255)
    assert result.getpixel((21, 21)) == (255, 0, 255, 255)


def test_quick_negative_gap_reorder_preview_and_export_keep_the_same_z_order(tmp_path: Path) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for name, color in (("red", "red"), ("green", "green"), ("blue", "blue")):
        path = tmp_path / f"{name}.png"
        solid(color, (40, 20)).save(path)
        paths.append(path)
    output = tmp_path / "out"
    output.mkdir()

    def settle(window: MainWindow) -> None:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.processEvents()
            if window._merge_preview_thread is None:
                return
            time.sleep(0.005)
        raise AssertionError("merge preview did not settle")

    window = MainWindow()
    try:
        window.load_paths(paths)
        window.merge_enable_check.setChecked(True)
        window.merge_gap_spin.setValue(-20)
        assert "マイナス値で画像を重ねます" in window.merge_gap_hint.text()
        settle(window)

        moved = window.merge_list.takeTopLevelItem(2)
        window.merge_list.insertTopLevelItem(0, moved)
        window.merge_list.setCurrentItem(moved)
        window._merge_items_reordered()
        settle(window)
        assert [path.name for path in window.merge_paths] == ["blue.png", "red.png", "green.png"]
        assert "80 × 20" in window.merge_summary.text()
        preview = window.preview._image_item.pixmap().toImage()
        assert preview.pixelColor(20, 1).name() == "#ff0000"

        window.destination_combo.setCurrentIndex(window.destination_combo.findData("Custom folder"))
        window.custom_folder = output
        window.export_all()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._thread is not None:
            app.processEvents()
            time.sleep(0.005)
        with Image.open(output / "blue.png") as opened:
            image = opened.convert("RGB")
            assert image.size == (80, 20)
            assert image.getpixel((20, 1)) == (255, 0, 0)

        window.merge_direction_buttons[MergeDirection.GRID].click()
        settle(window)
        assert window.merge_gap_spin.minimum() == -100
        assert window.merge_gap_spin.value() == -20
        assert window.merge_gap_hint.text() == "マイナス値で画像を重ねます"
    finally:
        window.close()

def test_merge_addition_and_membership_changes_refresh_the_latest_grid_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import quick_processing_tool.ui as ui_module
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for name, color in (("red", "red"), ("green", "green"), ("blue", "blue"), ("yellow", "yellow")):
        path = tmp_path / f"{name}.png"
        solid(color, (10, 10)).save(path)
        paths.append(path)

    original_preview = ui_module.build_merge_preview

    def delayed_preview(paths, processing, options):
        if len(paths) == 3:
            time.sleep(0.05)
        return original_preview(paths, processing, options)

    monkeypatch.setattr(ui_module, "build_merge_preview", delayed_preview)

    def settle(window: MainWindow) -> None:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.processEvents()
            if window._merge_preview_thread is None:
                return
            time.sleep(0.005)
        raise AssertionError("merge preview did not settle")

    window = MainWindow()
    try:
        window.load_paths(paths[:3])
        window.merge_enable_check.setChecked(True)
        window.merge_direction_buttons[MergeDirection.GRID].click()
        window.merge_grid_columns_buttons[2].click()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and window._merge_preview_thread is None:
            app.processEvents()
            time.sleep(0.005)
        assert window._merge_preview_thread is not None

        window.load_paths([paths[3]])
        settle(window)
        assert window.merge_paths == paths
        assert "4枚 / グリッド 2列" in window.merge_summary.text()
        preview = window.preview._image_item.pixmap().toImage()
        assert preview.size().width() == 20
        assert preview.size().height() == 20
        assert preview.pixelColor(11, 11).name() == "#ffff00"
        assert [window.file_tree.topLevelItem(index).text(2) for index in range(4)] == ["結合対象"] * 4

        window.merge_list.setCurrentItem(window.merge_list.topLevelItem(3))
        window.remove_selected_from_merge()
        settle(window)
        assert [window.file_tree.topLevelItem(index).text(2) for index in range(4)] == [
            "結合対象", "結合対象", "結合対象", "対象外",
        ]
        assert "3枚 / グリッド 2列" in window.merge_summary.text()

        window.file_tree.setCurrentItem(window.file_tree.topLevelItem(3))
        app.processEvents()
        window.add_selected_to_merge()
        settle(window)
        assert [window.file_tree.topLevelItem(index).text(2) for index in range(4)] == ["結合対象"] * 4
        assert "4枚 / グリッド 2列" in window.merge_summary.text()
    finally:
        window.close()


@pytest.mark.parametrize(
    ("count", "source", "target", "expected"),
    [
        (4, 3, 1, ["1.png", "4.png", "2.png", "3.png"]),
        (4, 0, 4, ["2.png", "3.png", "4.png", "1.png"]),
        (6, 2, 2, ["1.png", "2.png", "3.png", "4.png", "5.png", "6.png"]),
        (9, 8, 0, ["9.png", "1.png", "2.png", "3.png", "4.png", "5.png", "6.png", "7.png", "8.png"]),
    ],
)
def test_merge_order_drop_commit_updates_model_once(
    tmp_path: Path, count: int, source: int, target: int, expected: list[str]
) -> None:
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"{index + 1}.png"
        solid("red", (20, 20)).save(path)
        paths.append(path)
    window = MainWindow()
    try:
        scheduled: list[bool] = []
        window._schedule_merge_preview = lambda: scheduled.append(True)
        window.load_paths(paths, update_workspace=False)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and window._quick_preview_thread is not None:
            app.processEvents()
            time.sleep(0.005)
        assert window._quick_preview_thread is None
        if count == 9:
            window.merge_direction_buttons[MergeDirection.GRID].click()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and window._quick_preview_thread is not None:
                app.processEvents()
                time.sleep(0.005)
            assert window._quick_preview_thread is None
        window.merge_enable_check.setChecked(True)
        tree = window.merge_list
        emitted: list[bool] = []
        tree.order_dropped.connect(lambda: emitted.append(True))
        scheduled.clear()
        tree.setCurrentItem(tree.topLevelItem(source))
        changed = tree.move_current_item_to_index(target)
        app.processEvents()
        assert len(scheduled) == int(source != target)
        assert changed is (source != target)
        assert len(emitted) == int(source != target)
        assert [tree.topLevelItem(index).text(0) for index in range(count)] == [
            f"{index}. {name}" for index, name in enumerate(expected, start=1)
        ]
        assert [path.name for path in window.merge_paths] == expected
        if count == 4 and source == 3:
            window.merge_direction_buttons[MergeDirection.GRID].click()
            window.merge_grid_columns_buttons[2].click()
            assert "グリッド 2列" in window.merge_summary.text()
    finally:
        window.close()

def test_merge_preview_rapid_add_stress_keeps_latest_grid_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real Qt worker overlap while queue membership changes rapidly."""
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import quick_processing_tool.ui as ui_module
    from PySide6.QtWidgets import QApplication
    from quick_processing_tool.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    paths: list[Path] = []
    for index in range(9):
        path = tmp_path / f"stress-{index}.png"
        solid(("red", "green", "blue", "yellow", "magenta", "cyan")[index % 6], (16, 16)).save(path)
        paths.append(path)

    original_preview = ui_module.build_merge_preview

    def delayed_preview(paths, processing, options):
        time.sleep(0.01)
        return original_preview(paths, processing, options)

    monkeypatch.setattr(ui_module, "build_merge_preview", delayed_preview)

    def settle(window: MainWindow) -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            app.processEvents()
            if (
                window._quick_preview_thread is None
                and window._merge_preview_thread is None
                and not window._merge_gap_refresh_timer.isActive()
            ):
                return
            time.sleep(0.003)
        raise AssertionError("preview workers did not settle")

    for _iteration in range(3):
        window = MainWindow()
        try:
            window.load_paths([paths[0]])
            settle(window)
            window.merge_enable_check.setChecked(True)
            window.merge_direction_buttons[MergeDirection.GRID].click()
            window.merge_grid_columns_buttons[2].click()
            for index, path in enumerate(paths[1:], start=2):
                window.load_paths([path])
                if index == 4:
                    window.merge_grid_columns_buttons[3].click()
                elif index == 6:
                    window.merge_grid_columns_buttons[2].click()
                if index in (3, 5, 7):
                    window.merge_gap_spin.setValue(-20)
                elif index in (4, 6, 9):
                    window.merge_gap_spin.setValue(20)
                app.processEvents()

            window.merge_list.setCurrentItem(window.merge_list.topLevelItem(4))
            window.remove_selected_from_merge()
            window.file_tree.setCurrentItem(window.file_tree.topLevelItem(4))
            window.add_selected_to_merge()
            window.merge_gap_spin.setValue(-20)
            window.merge_grid_columns_buttons[2].click()
            settle(window)

            assert window.merge_paths == [*paths[:4], *paths[5:], paths[4]]
            assert [window.file_tree.topLevelItem(index).text(2) for index in range(9)] == ["結合対象"] * 9
            assert "9枚 / グリッド 2列" in window.merge_summary.text()
            assert not window.preview._image_item.pixmap().isNull()
        finally:
            window.close()

def test_merge_order_mouse_drag_reorders_visible_widget() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from quick_processing_tool.ui import MergeOrderTreeWidget

    app = QApplication.instance() or QApplication([])
    tree = MergeOrderTreeWidget()
    tree.resize(260, 160)
    for index in range(1, 5):
        tree.addTopLevelItem(QTreeWidgetItem([str(index)]))
    changes: list[bool] = []
    tree.order_dropped.connect(lambda: changes.append(True))
    tree.show()
    app.processEvents()
    source = tree.visualItemRect(tree.topLevelItem(3)).center()
    target_rect = tree.visualItemRect(tree.topLevelItem(2))
    target = QPoint(target_rect.center().x(), target_rect.top() + 1)
    QTest.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, pos=source)
    QTest.mouseMove(tree.viewport(), target)
    QTest.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, pos=target)
    app.processEvents()
    assert [tree.topLevelItem(index).text(0) for index in range(4)] == ["1", "2", "4", "3"]
    assert changes == [True]
    tree.close()