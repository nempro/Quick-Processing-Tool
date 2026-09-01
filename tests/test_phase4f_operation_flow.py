from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from quick_processing_tool.drop_overlay import RoundedDropOverlay
from quick_processing_tool.editing import EditResult
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("device_pixel_ratio", [1.0, 1.25, 1.5])
def test_rounded_drop_overlay_paints_corner_background_without_qss_fragments(
    qt_app: QApplication,
    active: bool,
    device_pixel_ratio: float,
) -> None:
    overlay = RoundedDropOverlay()
    try:
        overlay.resize(240, 140)
        overlay.set_drop_active(active)
        target = QImage(
            round(240 * device_pixel_ratio),
            round(140 * device_pixel_ratio),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        target.setDevicePixelRatio(device_pixel_ratio)
        target.fill(QColor("#000000"))
        overlay.render(target)

        expected = QColor("#eaf1ff") if active else QColor("#f7f9fc")
        assert target.pixelColor(0, 0) == expected
        assert target.pixelColor(target.width() - 1, 0) == expected
        assert target.pixelColor(0, target.height() - 1) == expected
        assert target.pixelColor(target.width() - 1, target.height() - 1) == expected
    finally:
        overlay.close()


def test_edit_image_clear_preserves_shared_source_but_empties_edit_tab(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGBA", (48, 32), (20, 90, 180, 150)).save(source)
    window = MainWindow()
    try:
        window.set_current_source(source)
        _wait_until(qt_app, lambda: window.edit_page.source_path is not None)

        window.edit_page.clear_image()

        assert window.workspace.current is not None
        assert window.workspace.current.path == source.resolve()
        assert window.edit_page.source_path is None
        assert window.edit_page.drop_zone._empty
        assert window.edit_page.saved_box.isHidden()
        assert not window.edit_page.save_button.isEnabled()
    finally:
        _wait_until(qt_app, window.edit_page.can_close)
        window.close()


def test_edit_saved_result_handoff_reuses_upscale_queue(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "source_edited.png"
    Image.new("RGBA", (48, 32), (20, 90, 180, 150)).save(source)
    Image.new("RGBA", (48, 32), (80, 120, 50, 200)).save(output)
    window = MainWindow()
    try:
        window.set_current_source(source)
        _wait_until(qt_app, lambda: window.edit_page.source_path is not None)
        window.edit_page._on_saved(
            EditResult(
                output,
                48,
                32,
                output.stat().st_size,
                "PNG",
                True,
            )
        )

        assert window.edit_page.edit_upscale_button.isEnabled()
        window.edit_page.edit_upscale_button.click()
        qt_app.processEvents()

        assert window.navigation.currentIndex() == window.upscale_tab
        assert [item.source_path for item in window.upscale_page.items] == [
            output.resolve()
        ]
        assert window.workspace.current is not None
        assert window.workspace.current.path == output.resolve()
    finally:
        _wait_until(qt_app, window.edit_page.can_close)
        window.close()


def test_thumbnail_target_list_clear_does_not_affect_generated_files(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    saved = tmp_path / "saved-thumbnail.png"
    Image.new("RGBA", (16, 16), (10, 20, 30, 255)).save(saved)
    window = MainWindow()
    try:
        page = window.thumbnail_page
        page.titles_edit.setPlainText("一つ目\n二つ目")
        assert page.clear_titles_button.text() == "一覧をクリア"
        assert page.clear_titles_button.isEnabled()

        page.clear_titles_button.click()

        assert page.titles_edit.toPlainText() == ""
        assert saved.is_file()
    finally:
        window.close()
