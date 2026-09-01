from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from quick_processing_tool.image_splitting import SplitDirection
from quick_processing_tool.models import ResizeMode
from quick_processing_tool.ui import MainWindow
from quick_processing_tool.upscaler.models import UpscaleMode, UpscaleResult


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _wait_window_idle(app: QApplication, window: MainWindow) -> None:
    _wait_until(
        app,
        lambda: (
            window._quick_preview_thread is None
            and window.edit_page.can_close()
            and window.upscale_page.can_close()
            and window.pixel_page.can_close()
        ),
    )


def _rgba_image(path: Path, size: tuple[int, int]) -> None:
    image = Image.new("RGBA", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (x * 17 % 256, y * 29 % 256, 120, (x + y) * 19 % 256))
    image.save(path, "PNG")


@pytest.mark.parametrize("scale", [2, 4])
def test_upscale_result_handoff_opens_quick_split_and_preserves_result(
    qt_app: QApplication, tmp_path: Path, scale: int
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / f"source_{scale}x.png"
    _rgba_image(source, (8, 6))
    _rgba_image(output, (8 * scale, 6 * scale))

    window = MainWindow()
    window.set_current_source(source)
    page = window.upscale_page
    assert len(page.items) == 1
    result = UpscaleResult(
        output,
        8 * scale,
        6 * scale,
        output.stat().st_size,
        0.1,
        UpscaleMode.ILLUSTRATION,
        scale,
        "Mock GPU",
    )
    page.result = result
    page._view_after = True
    page._show_selected()
    page.saved_box.show()

    assert page.split_result_button.isEnabled()
    page.split_result_button.click()
    qt_app.processEvents()

    assert window.navigation.currentIndex() == window.quick_tab
    assert [info.path for info in window.files] == [output.resolve()]
    assert window.workspace.current is not None
    assert window.workspace.current.path == output.resolve()
    assert window.split_enable_check.isChecked()
    assert window.split_count_group.checkedId() == 4
    assert window.split_direction_buttons[SplitDirection.VERTICAL].isChecked()
    assert window.split_section.toggle.isChecked()
    assert not window.split_section.content.isHidden()
    assert "高画質化済みの画像" in window._quick_source_origin_html(output)
    assert page.result == result

    with Image.open(output) as reopened:
        reopened.load()
        assert reopened.size == (8 * scale, 6 * scale)
        assert reopened.mode == "RGBA"

    window.clear_quick_all()
    assert not window.files
    assert page.result == result
    _wait_window_idle(qt_app, window)
    window.close()


@pytest.mark.parametrize("size", [(900, 620), (1180, 720)])
def test_handoff_keeps_split_header_visible_and_resets_stale_quick_scroll(
    qt_app: QApplication, tmp_path: Path, size: tuple[int, int]
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "source_2x.png"
    _rgba_image(source, (80, 60))
    _rgba_image(output, (160, 120))

    window = MainWindow()
    window.resize(*size)
    window.show()
    window.set_current_source(source)
    page = window.upscale_page
    page.result = UpscaleResult(
        output, 160, 120, output.stat().st_size, 0.1,
        UpscaleMode.ILLUSTRATION, 2, "Mock GPU",
    )
    page._show_selected()
    page.saved_box.show()
    page.split_result_button.click()
    qt_app.processEvents()
    qt_app.processEvents()

    scroll = window.quick_settings_scroll
    viewport = scroll.viewport()
    header = window.split_section.toggle
    header_top = header.mapTo(viewport, QPoint(0, 0)).y()
    assert header_top >= 8
    assert header_top + header.height() <= viewport.height() - 8
    assert scroll.horizontalScrollBar().maximum() == 0

    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    window.navigation.setCurrentIndex(window.upscale_tab)
    window.navigation.setCurrentIndex(window.quick_tab)
    qt_app.processEvents()
    assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().minimum()

    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    window.reset_settings()
    assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().minimum()
    _wait_window_idle(qt_app, window)
    window.close()


def test_handoff_result_exports_four_split_images_without_original_queue(
    qt_app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "original.png"
    output = tmp_path / "original_2x.png"
    destination = tmp_path / "split-output"
    _rgba_image(source, (7, 5))
    _rgba_image(output, (14, 10))

    window = MainWindow()
    window.set_current_source(source)
    page = window.upscale_page
    result = UpscaleResult(
        output, 14, 10, output.stat().st_size, 0.1,
        UpscaleMode.ILLUSTRATION, 2, "Mock GPU",
    )
    page.result = result
    page._show_selected()
    page.saved_box.show()
    page.split_result_button.click()
    qt_app.processEvents()

    destination.mkdir()
    window.custom_folder = destination
    window.destination_combo.setCurrentIndex(
        window.destination_combo.findData("Custom folder")
    )
    window.export_all()
    _wait_until(qt_app, lambda: window._thread is None)

    outputs = sorted(destination.glob("original_2x_*.png"))
    assert [path.name for path in outputs] == [
        "original_2x_01.png",
        "original_2x_02.png",
        "original_2x_03.png",
        "original_2x_04.png",
    ]
    panels = []
    for path in outputs:
        with Image.open(path) as opened:
            panels.append(opened.convert("RGBA"))
    assert sum(panel.width for panel in panels) == 14
    assert {panel.height for panel in panels} == {10}
    joined = Image.new("RGBA", (14, 10))
    x = 0
    for panel in panels:
        joined.paste(panel, (x, 0))
        x += panel.width
    with Image.open(output) as expected:
        assert joined.tobytes() == expected.convert("RGBA").tobytes()
    assert page.result == result
    _wait_window_idle(qt_app, window)
    window.close()


def test_quick_queue_clear_preserves_source_and_settings(
    qt_app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "queue.png"
    _rgba_image(source, (20, 12))
    window = MainWindow()
    window.set_current_source(source)
    window.resize_mode.setCurrentIndex(
        window.resize_mode.findData(ResizeMode.PERCENTAGE)
    )
    window.percent_spin.setValue(75)

    window.clear_quick_queue()

    assert not window.files
    assert window.workspace.current is not None
    assert window.workspace.current.path == source.resolve()
    assert ResizeMode(window.resize_mode.currentData()) is ResizeMode.PERCENTAGE
    assert window.percent_spin.value() == 75
    assert not window.quick_queue_clear_button.isEnabled()
    _wait_window_idle(qt_app, window)
    window.close()


@pytest.mark.parametrize("size", [(900, 620), (1180, 720)])
def test_operation_placement_roles_and_compact_layout(
    qt_app: QApplication, size: tuple[int, int]
) -> None:
    window = MainWindow()
    window.resize(*size)
    window.show()
    qt_app.processEvents()

    for button in (
        window.quick_save_button,
        window.edit_page.save_button,
        window.upscale_page.start_button,
        window.pixel_page.save_button,
        window.thumbnail_page.generate_button,
        window.sound_effect_page.save_button,
        window.speech_bubble_page.save_button,
    ):
        assert button.property("operationRole") == "primary"

    assert window.quick_reset_button.parentWidget() is window.quick_settings_footer
    assert window.quick_clear_button.parentWidget() is window.quick_settings_footer
    assert window.edit_page.reset_button.parentWidget() is window.edit_page.settings_footer
    assert window.edit_page.clear_all_button.parentWidget() is window.edit_page.settings_footer
    assert window.quick_add_button.parentWidget() is window.quick_queue_clear_button.parentWidget()

    scrolls = (
        window.quick_settings_scroll,
        window.edit_page.settings_scroll,
        window.pixel_page.findChild(QScrollArea, "pixelSettingsScroll"),
        window.thumbnail_page.settings_scroll,
        window.sound_effect_page.settings_scroll,
        window.speech_bubble_page.settings_scroll,
    )
    for scroll in scrolls:
        assert scroll is not None
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert scroll.horizontalScrollBar().maximum() == 0

    for tab, tree in (
        (window.quick_tab, window.file_tree),
        (window.upscale_tab, window.upscale_page.queue),
        (window.thumbnail_tab, window.thumbnail_page.result_tree),
    ):
        window.navigation.setCurrentIndex(tab)
        qt_app.processEvents()
        assert tree.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert tree.horizontalScrollBar().maximum() == 0

    assert not window.quick_settings_footer.isHidden()
    assert not window.edit_page.settings_footer.isHidden()
    window.close()
