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
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter, QWidget

import quick_processing_tool.edit_ui as edit_ui_module
from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.editing import (
    CanvasBackground,
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
        assert page.palette_open_button.height() >= 28
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
def test_compact_edit_inspector_states_reduce_height_without_overflow(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = tmp_path / f"compact-edit-{width}.png"
    Image.new("RGB", (120, 90), "white").save(source)
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.navigation.setCurrentIndex(window.image_edit_tab)
        window.show()
        app.processEvents()
        page = window.edit_page
        deadline = time.monotonic() + 3.0
        while page._preview_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        assert page._preview_thread is None
        page.schedule_preview = lambda: None
        baselines = {
            900: {"hand": 845, "transparency": 861, "material": 1260, "palette12": 1345, "combined": 1745},
            1180: {"hand": 803, "transparency": 783, "material": 1190, "palette12": 1275, "combined": 1639},
            1440: {"hand": 803, "transparency": 783, "material": 1176, "palette12": 1275, "combined": 1639},
        }[width]
        colors = tuple((index * 19, index * 13, index * 7) for index in range(12))

        def configure(state: str) -> None:
            page._applying = True
            for section in page.sections:
                section.set_expanded(False)
            page.hand_mode_enabled.setChecked(False)
            page.transparency_enabled.setChecked(False)
            page.sticker_enabled.setChecked(False)
            page.line_art_enabled.setChecked(False)
            page._palette_values = ()
            page._palette_replacements = ()
            page._rebuild_palette_chips()
            page.palette_count_combo.setCurrentIndex(
                page.palette_count_combo.findData(6)
            )
            if state in {"hand", "combined"}:
                page.hand_section.set_expanded(True)
                page.hand_mode_enabled.setChecked(True)
            if state in {"transparency", "combined"}:
                page.transparency_section.set_expanded(True)
                page.transparency_enabled.setChecked(True)
            if state in {"material", "palette12", "combined"}:
                page.material_section.set_expanded(True)
            if state in {"palette12", "combined"}:
                page.palette_count_combo.setCurrentIndex(
                    page.palette_count_combo.findData(12)
                )
                page._palette_values = colors
                page._palette_replacements = colors
                page._rebuild_palette_chips()
            page._applying = False
            page._update_visibility()
            app.processEvents()

        measured: dict[str, int] = {}
        for state in ("hand", "transparency", "material", "palette12", "combined"):
            configure(state)
            content = page.settings_scroll.widget()
            viewport = page.settings_scroll.viewport()
            measured[state] = content.sizeHint().height()
            wide_children = [
                (
                    type(child).__name__,
                    getattr(child, "text", lambda: "")(),
                    child.minimumSizeHint().width(),
                    child.sizeHint().width(),
                )
                for child in content.findChildren(QWidget)
                if child.isVisibleTo(content) and child.minimumSizeHint().width() > 180
            ]
            assert page.settings_scroll.horizontalScrollBar().maximum() == 0, (
                state,
                page.settings_scroll.horizontalScrollBar().maximum(),
                content.width(),
                viewport.width(),
                wide_children,
            )
            assert content.width() <= viewport.width()
            assert page.drop_zone.preview.width() >= {900: 380, 1180: 560, 1440: 701}[width]
            for child in content.findChildren(QWidget):
                if not child.isVisibleTo(content) or child.width() <= 0:
                    continue
                left = child.mapTo(content, QPoint(0, 0)).x()
                assert left >= -2
                assert left + child.width() <= content.width() + 2
        assert all(measured[state] < baselines[state] for state in measured), (
            measured,
            baselines,
        )
        configure("hand")
        for widget in (
            page.hand_pen_button,
            page.hand_eraser_button,
            page.hand_color_button,
            page.hand_size_spin,
            page.hand_opacity_spin,
            page.hand_clear_button,
        ):
            assert widget.isVisibleTo(page.settings_scroll.widget())
            assert widget.height() >= 28, (widget.text(), widget.height())
        assert page.hand_visible_check.isVisibleTo(page.settings_scroll.widget())
        mode_label = next(
            label
            for label in page.hand_section.findChildren(QWidget)
            if getattr(label, "text", lambda: "")() == "描画モード"
        )
        for widget in (mode_label, page.hand_clear_button):
            assert widget.width() >= widget.minimumSizeHint().width()

        configure("transparency")
        assert page.eyedropper_button.text() == "画像から選ぶ"
        assert page.eyedropper_button.width() >= page.eyedropper_button.minimumSizeHint().width()

        configure("palette12")
        assert page.palette_quantize_enabled.text() == "12色に整理"
        for widget in (
            page.palette_quantize_enabled,
            page.palette_blend_mode_combo,
            page.palette_open_button,
        ):
            assert widget.width() >= widget.minimumSizeHint().width(), (
                getattr(widget, "text", lambda: widget.currentText())(),
                widget.width(),
                widget.minimumSizeHint().width(),
            )

        for section in (
            page.text_section,
            page.hand_section,
            page.transparency_section,
            page.canvas_section,
            page.material_section,
        ):
            section.set_expanded(True)
        page.hand_mode_enabled.setChecked(True)
        page.transparency_enabled.setChecked(True)
        page.sticker_enabled.setChecked(True)
        page.line_art_enabled.setChecked(True)
        page.canvas_background_combo.setCurrentIndex(
            page.canvas_background_combo.findData(CanvasBackground.CUSTOM.value)
        )
        page.line_art_background_combo.setCurrentIndex(
            page.line_art_background_combo.findData(LineArtBackground.CUSTOM.value)
        )
        page._update_visibility()
        app.processEvents()
        for button in (
            page.text_color_button,
            page.outline_color_button,
            page.hand_color_button,
            page.target_color_button,
            page.canvas_color_button,
            page.sticker_outline_color_button,
            page.line_art_color_button,
            page.line_art_background_color_button,
        ):
            assert 30 <= button.height() <= 36
    finally:
        window.close()


@pytest.mark.parametrize("width", [720, 900, 1180, 1440])
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
            window.sound_effect_tab,
            window.speech_bubble_tab,
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


@pytest.mark.parametrize("width", [720, 900, 1180, 1440])
def test_all_three_column_workspaces_stay_inside_page_without_overlap(
    app: QApplication, width: int
) -> None:
    window = MainWindow()
    workspaces = (
        (window.quick_tab, "quick_workspace"),
        (window.thumbnail_tab, "thumbnail_workspace"),
        (window.image_edit_tab, "edit_workspace"),
        (window.upscale_tab, "upscaleWorkspace"),
        (window.pixel_tab, "pixel_workspace"),
        (window.sound_effect_tab, "sound_effect_workspace"),
        (window.speech_bubble_tab, "speech_bubble_workspace"),
    )
    try:
        window.resize(width, 760)
        window.show()
        for tab, name in workspaces:
            window.navigation.setCurrentIndex(tab)
            app.processEvents()
            page = window.navigation.currentWidget()
            splitter = page if isinstance(page, QSplitter) and page.objectName() == name else page.findChild(QSplitter, name)
            assert splitter is not None, name
            assert splitter.width() <= page.width(), (name, splitter.width(), page.width())
            columns = [splitter.widget(index) for index in range(splitter.count())]
            assert len(columns) == 3
            assert all(column.width() >= 170 for column in columns), (
                name,
                [column.width() for column in columns],
            )
            for left, right in zip(columns, columns[1:]):
                assert left.geometry().right() < right.geometry().left(), (
                    name,
                    left.geometry(),
                    right.geometry(),
                )
    finally:
        window.close()
