from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtGui import QColor
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

import quick_processing_tool.edit_ui as edit_ui_module
from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.editing import (
    EditSettings,
    FilterPreset,
    LineArtBackground,
    PaletteSettings,
    RecolorBlendMode,
)
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


PALETTE = ((10, 20, 30), (40, 50, 60), (70, 80, 90))
MAPPING = (0, 1, 2, 0)


def palette_settings(replacements=PALETTE) -> EditSettings:
    return EditSettings(
        palette=PaletteSettings(
            enabled=True,
            quantize_enabled=True,
            color_count=6,
            palette=PALETTE,
            replacements=replacements,
            mapping=MAPPING,
            mapping_width=2,
            mapping_height=2,
            mapping_digest="stable-digest",
            blend_mode=RecolorBlendMode.PRESERVE_SHADING,
        )
    )


def seed_palette(page: QuickEditPage, replacements=PALETTE) -> None:
    page.apply_settings(replace(page.settings(), palette=palette_settings(replacements).palette))
    page._control_changed()


def test_sequential_replacements_use_existing_history_once_and_cancel_noop_do_not(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = QuickEditPage()
    try:
        seed_palette(page)
        base = page._history_index
        colors = iter((QColor(101, 1, 1), QColor(2, 102, 2), QColor(3, 3, 103)))
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: next(colors))
        for index in range(3):
            page._replace_palette_color(index)
            assert page._history_index == base + index + 1
            assert page.settings().palette.mapping == MAPPING
            assert page.settings().palette.mapping_digest == "stable-digest"
            assert page.settings().palette.quantize_enabled
            assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING

        final = page.settings().palette.replacements
        page.undo()
        assert page.settings().palette.replacements[2] == PALETTE[2]
        page.undo()
        assert page.settings().palette.replacements[1:] == PALETTE[1:]
        page.redo()
        page.redo()
        assert page.settings().palette.replacements == final

        unchanged_index = page._history_index
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor())
        page._replace_palette_color(0)
        assert page._history_index == unchanged_index
        same = page._palette_replacements[0]
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor(*same))
        page._replace_palette_color(0)
        assert page._history_index == unchanged_index
    finally:
        page.close()


def test_individual_and_all_reset_are_single_undoable_actions_and_branch_redo(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = ((101, 1, 1), (2, 102, 2), (3, 3, 103))
    page = QuickEditPage()
    try:
        seed_palette(page, changed)
        base = page._history_index
        page._reset_palette_color(1)
        assert page._history_index == base + 1
        assert page._palette_replacements == (changed[0], PALETTE[1], changed[2])
        page.undo()
        assert page._palette_replacements == changed
        page.redo()
        assert page._palette_replacements == (changed[0], PALETTE[1], changed[2])
        no_op = page._history_index
        page._reset_palette_color(1)
        assert page._history_index == no_op

        page.reset_palette()
        assert page._history_index == no_op + 1
        assert page._palette_replacements == PALETTE
        page.undo()
        assert page._palette_replacements == (changed[0], PALETTE[1], changed[2])
        assert page._history_index + 1 < len(page._history)
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor(9, 8, 7))
        page._replace_palette_color(0)
        assert page._history_index + 1 == len(page._history)
    finally:
        page.close()


def test_upstream_invalidation_scrubs_active_history_and_keeps_meaningful_undo(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = QuickEditPage()
    try:
        page.sticker_enabled.setChecked(True)
        seed_palette(page)
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor(111, 22, 33))
        page._replace_palette_color(0)
        page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.GRAYSCALE.value))
        app.processEvents()
        assert page._palette_needs_reextract
        assert all(
            not state.palette.enabled
            and not state.palette.quantize_enabled
            and not state.palette.palette
            and not state.palette.replacements
            and not state.palette.mapping
            and state.palette.mapping_width == 0
            and state.palette.mapping_height == 0
            and not state.palette.mapping_digest
            for state in page._history[1:]
        )
        assert page._history[-1].palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
        assert all(a != b for a, b in zip(page._history, page._history[1:]))
        assert page._history_index == len(page._history) - 1
        page.undo()
        assert page.settings().filter_preset is FilterPreset.NONE
        assert page.settings().sticker.enabled
        assert not page.settings().palette.mapping
        page.redo()
        assert page.settings().filter_preset is FilterPreset.GRAYSCALE
    finally:
        page.close()


def test_palette_rows_are_compact_accessible_and_reset_state_explicit(app: QApplication) -> None:
    page = QuickEditPage()
    try:
        seed_palette(page, (PALETTE[0], (1, 2, 3), PALETTE[2]))
        page.material_section.set_expanded(True)
        page.show()
        app.processEvents()
        assert len(page._palette_reset_buttons) == 3
        for index, button in enumerate(page._palette_reset_buttons):
            assert button.toolTip() == "この色だけ元に戻す"
            assert f"代表色 {index + 1}" in button.accessibleName()
            assert button.height() <= 32
        assert not page._palette_reset_buttons[0].isEnabled()
        assert page._palette_reset_buttons[1].isEnabled()
        assert not page._palette_reset_buttons[2].isEnabled()
    finally:
        page.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_compact_source_and_pixel_first_viewport_layout(app: QApplication, tmp_path: Path, width: int) -> None:
    source = tmp_path / ("very-long-source-filename-" + "x" * 50 + ".png")
    Image.new("RGBA", (120, 90), (1, 2, 3, 100)).save(source)
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.navigation.setCurrentIndex(window.pixel_tab)
        window.show()
        app.processEvents()
        for card in (
            window.quick_source_card,
            window.edit_page.current_source_card,
            window.upscale_page.current_source_card,
        ):
            assert card.sizeHint().height() <= 64
            assert card.title_label.text() == "画像："
            assert card.change_button.text() == "変更"
            assert card.change_button.sizeHint().height() <= 32
            assert card.change_button.toolTip() == "別の画像を選びます"
            assert card.change_button.accessibleName() == "別の画像を選ぶ"
            assert str(source.resolve()) in card.name_label.toolTip()
            assert card.meta_label.text() == "120 × 90 / PNG"

        pixel_card = window.pixel_page.current_source_card
        assert pixel_card.title_label.text() == "元にする画像"
        assert pixel_card.change_button.text() == "別の画像を選ぶ"
        assert pixel_card.change_button.height() >= 30
        assert pixel_card.change_button.geometry().top() > pixel_card.meta_label.geometry().bottom()
        assert str(source.resolve()) in pixel_card.name_label.toolTip()
        assert pixel_card.meta_label.text() == "120 × 90 / PNG"

        window.navigation.setCurrentIndex(window.pixel_tab)
        app.processEvents()
        scroll = window.pixel_page.findChild(QScrollArea, "pixelSettingsScroll")
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        content = scroll.widget()
        assert content.width() <= scroll.viewport().width()
        assert window.pixel_page.clear_button.height() >= 26
        assert window.pixel_page.new_button.width() >= 110
        assert window.pixel_page.new_button.height() >= 30
        tools = (
            window.pixel_page.pencil_button,
            window.pixel_page.eraser_button,
            window.pixel_page.eyedropper_button,
        )
        assert max(button.width() for button in tools) - min(button.width() for button in tools) <= 1
        assert all(button.height() >= 30 and button.toolTip() and button.accessibleName() for button in tools)
        window.pixel_page.eraser_button.click()
        assert window.pixel_page.eraser_button.isChecked()
        assert not window.pixel_page.pencil_button.isChecked()
        assert window.pixel_page.custom_size_widget.isHidden()
        window.pixel_page.preset_combo.setCurrentIndex(3)
        assert not window.pixel_page.custom_size_widget.isHidden()
        window.pixel_page.preset_combo.setCurrentIndex(2)
        assert window.pixel_page.custom_size_widget.isHidden()
        assert (
            window.pixel_page.current_reference_button.geometry().top()
            == window.pixel_page.current_pixels_button.geometry().top()
        )
        assert window.pixel_page.current_source_usage.geometry().top() < window.pixel_page.tools_group.geometry().top()
    finally:
        deadline = time.monotonic() + 3.0
        while window.edit_page._preview_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        assert window.edit_page._preview_thread is None
        window.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_expanded_edit_material_stays_inside_viewport_with_eight_rows(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = tmp_path / f"edit-layout-{width}.png"
    Image.new("RGB", (120, 90), "red").save(source)
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.navigation.setCurrentIndex(window.image_edit_tab)
        window.show()
        app.processEvents()
        page = window.edit_page
        page.material_section.set_expanded(True)
        palette = tuple((index * 20, index * 15, index * 10) for index in range(8))
        page.apply_settings(
            replace(
                page.settings(),
                palette=PaletteSettings(
                    True, True, 8, palette, palette, tuple(range(8)), 8, 1, "digest"
                ),
            )
        )
        app.processEvents()
        assert page.settings_scroll.horizontalScrollBar().maximum() == 0
        assert page.settings_scroll.widget().width() <= page.settings_scroll.viewport().width()
        assert page.palette_send_button.width() > 0
        assert page.palette_open_button.width() > 0
        assert page.palette_open_button.width() <= 90
        assert page.palette_open_button.height() >= 30
        assert all(button.height() <= 32 for button in page._palette_reset_buttons)
        assert page.drop_zone.preview.width() >= 300
    finally:
        window.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_line_expression_off_on_custom_layout_has_no_overflow_or_preview_regression(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = tmp_path / f"line-layout-{width}.png"
    Image.new("RGB", (120, 90), "white").save(source)
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.navigation.setCurrentIndex(window.image_edit_tab)
        window.show()
        app.processEvents()
        page = window.edit_page
        page.material_section.set_expanded(True)
        app.processEvents()
        viewport = page.settings_scroll.viewport()
        content = page.settings_scroll.widget()
        assert page.line_art_details.isHidden()
        assert page.settings_scroll.horizontalScrollBar().maximum() == 0
        assert content.width() <= viewport.width()
        assert page.drop_zone.preview.width() >= {900: 380, 1180: 560, 1440: 701}[width]

        page.line_art_enabled.setChecked(True)
        app.processEvents()
        assert page.line_art_details.isVisible()
        assert not page.line_art_background_color_button.isVisible()
        assert page.line_art_group.width() <= viewport.width()
        assert page.settings_scroll.horizontalScrollBar().maximum() == 0
        assert content.width() <= viewport.width()

        page.line_art_background_combo.setCurrentIndex(
            page.line_art_background_combo.findData(LineArtBackground.CUSTOM.value)
        )
        app.processEvents()
        assert page.line_art_background_color_button.isVisible()
        assert page.settings_scroll.horizontalScrollBar().maximum() == 0
        assert content.width() <= viewport.width()
        for child in page.line_art_details.findChildren(QWidget):
            if not child.isVisibleTo(content) or child.width() <= 0:
                continue
            left = child.mapTo(content, QPoint(0, 0)).x()
            assert left >= -2
            assert left + child.width() <= content.width() + 2
        assert page.drop_zone.preview.width() >= {900: 380, 1180: 560, 1440: 701}[width]
    finally:
        deadline = time.monotonic() + 3.0
        while window.edit_page._preview_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        window.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_all_implemented_settings_panes_have_no_horizontal_overflow(
    app: QApplication, width: int
) -> None:
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.show()
        for tab in (
            window.quick_tab,
            window.thumbnail_tab,
            window.image_edit_tab,
            window.upscale_tab,
            window.pixel_tab,
        ):
            window.navigation.setCurrentIndex(tab)
            app.processEvents()
            page = window.navigation.currentWidget()
            for scroll in page.findChildren(QScrollArea):
                if scroll.objectName() == "pixelPreviewScroll":
                    continue
                assert scroll.horizontalScrollBar().maximum() == 0, (tab, scroll.objectName())
                content = scroll.widget()
                if content is not None:
                    assert content.width() <= scroll.viewport().width(), (tab, scroll.objectName())
                    for child in content.findChildren(QWidget):
                        if not child.isVisibleTo(content) or child.width() <= 0:
                            continue
                        left = child.mapTo(content, QPoint(0, 0)).x()
                        assert left >= -2, (tab, scroll.objectName(), child.objectName(), left)
                        assert left + child.width() <= content.width() + 2, (
                            tab,
                            scroll.objectName(),
                            child.objectName(),
                            left + child.width(),
                            content.width(),
                        )
    finally:
        window.close()
