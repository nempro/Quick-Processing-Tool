from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter

from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _splitter(window: MainWindow, tab: int, name: str) -> QSplitter:
    page = window.navigation.widget(tab)
    result = (
        page
        if isinstance(page, QSplitter) and page.objectName() == name
        else page.findChild(QSplitter, name)
    )
    assert result is not None
    return result


def test_primary_workspaces_use_compact_but_clickable_control_heights(
    app: QApplication,
) -> None:
    window = MainWindow()
    try:
        window.resize(1180, 720)
        window.show()
        app.processEvents()

        inputs = (
            window.target_combo,
            window.edit_page.format_combo,
            window.upscale_page.format_combo,
            window.thumbnail_page.format_combo,
        )
        regular_buttons = (
            window.edit_page.folder_button,
            window.upscale_page.folder_button,
            window.thumbnail_page.folder_button,
        )
        primary_buttons = (
            window.edit_page.save_button,
            window.upscale_page.start_button,
            window.thumbnail_page.generate_button,
        )

        assert all(32 <= widget.sizeHint().height() <= 36 for widget in inputs)
        assert all(32 <= button.sizeHint().height() <= 36 for button in regular_buttons)
        assert 38 <= window.quick_save_button.sizeHint().height() <= 42
        assert all(46 <= button.sizeHint().height() <= 52 for button in primary_buttons)

        for widget in (*inputs, *regular_buttons, *primary_buttons):
            assert widget.sizeHint().height() >= widget.fontMetrics().height() + 10
    finally:
        window.close()


def test_primary_panes_use_compact_spacing_and_margins(
    app: QApplication,
) -> None:
    window = MainWindow()
    try:
        window.resize(1180, 720)
        window.show()
        app.processEvents()

        quick_layout = window.quick_settings_scroll.widget().layout()
        assert quick_layout.spacing() <= 6
        for section in window.quick_sections:
            assert section.layout().spacing() <= 3
            assert section.layout().contentsMargins().bottom() <= 4

        edit_layout = window.edit_page.settings_scroll.widget().layout()
        assert edit_layout.spacing() <= 2
        for section in window.edit_page.sections:
            assert section.layout().spacing() <= 2
            assert section.layout().contentsMargins().top() <= 1

        assert window.thumbnail_page.settings_content.layout().spacing() <= 6
        upscale = _splitter(window, window.upscale_tab, "upscaleWorkspace")
        upscale_left = upscale.widget(0).layout()
        upscale_right = upscale.widget(2).layout()
        assert upscale_left.spacing() <= 5
        assert upscale_left.contentsMargins().top() <= 8
        assert upscale_right.spacing() <= 5
        assert upscale_right.contentsMargins().top() <= 8

        window.navigation.setCurrentIndex(window.image_edit_tab)
        app.processEvents()
        assert window.edit_page.save_scroll.horizontalScrollBar().maximum() == 0
        assert window.edit_page.save_scroll.verticalScrollBar().maximum() == 0
    finally:
        window.close()


def test_compact_window_keeps_primary_actions_reachable_without_horizontal_scroll(
    app: QApplication,
) -> None:
    window = MainWindow()
    try:
        window.resize(900, 620)
        window.show()
        app.processEvents()

        checks = (
            (window.quick_tab, window.quick_save_button),
            (window.image_edit_tab, window.edit_page.save_button),
            (window.upscale_tab, window.upscale_page.start_button),
            (window.thumbnail_tab, window.thumbnail_page.generate_button),
        )
        for tab, primary in checks:
            window.navigation.setCurrentIndex(tab)
            app.processEvents()
            assert primary.isVisible()
            assert primary.height() >= primary.fontMetrics().height() + 10
            page = window.navigation.currentWidget()
            for scroll in page.findChildren(QScrollArea):
                assert scroll.horizontalScrollBar().maximum() == 0

        assert window.quick_save_button.isVisibleTo(window.quick_settings_footer)
        assert window.edit_page.save_scroll.verticalScrollBar().maximum() > 0
    finally:
        window.close()
