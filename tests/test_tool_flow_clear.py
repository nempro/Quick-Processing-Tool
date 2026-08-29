from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from quick_processing_tool.editing import (
    EditResult,
    FilterPreset,
    HandDrawSettings,
    HandPoint,
    HandStroke,
    HandTool,
    PaletteSettings,
)
from quick_processing_tool.models import (
    OutputFormat,
    ProcessingOptions,
    ResizeMode,
    Transform,
)
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _image(path: Path, color: tuple[int, int, int, int]) -> Path:
    Image.new("RGBA", (48, 32), color).save(path)
    return path


def _wait_for_preview_idle(app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and (
        window._quick_preview_thread is not None
        or window.edit_page._preview_thread is not None
    ):
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window._quick_preview_thread is None
    assert window.edit_page._preview_thread is None


def test_material_tabs_start_from_text_without_source_guidance(
    app: QApplication,
) -> None:
    window = MainWindow()
    try:
        assert window.workspace.current is None

        window.navigation.setCurrentIndex(window.sound_effect_tab)
        app.processEvents()
        assert window.statusBar().currentMessage() == "文字を入力するとプレビューされます"
        assert window.sound_effect_page.preview_status.text() == "文字を入力するとプレビューされます"
        assert not window.sound_effect_page.save_button.isEnabled()
        window.sound_effect_page.text_edit.setPlainText("ドン！")
        window.sound_effect_page.update_preview()
        assert window.sound_effect_page.preview_image is not None

        window.navigation.setCurrentIndex(window.speech_bubble_tab)
        app.processEvents()
        assert window.statusBar().currentMessage() == "セリフを入力するとプレビューされます"
        assert window.speech_bubble_page.preview_status.text() == "セリフを入力するとプレビューされます"
        assert not window.speech_bubble_page.save_button.isEnabled()
        window.speech_bubble_page.text_edit.setPlainText("えっ！？")
        window.speech_bubble_page.update_preview()
        assert window.speech_bubble_page.preview_result is not None
        assert window.workspace.current is None
    finally:
        window.close()


def test_current_source_sharing_batch_protection_and_quick_clear(
    app: QApplication, tmp_path: Path
) -> None:
    source_a = _image(tmp_path / "source-a.png", (220, 40, 60, 255))
    source_b = _image(tmp_path / "source-b.png", (20, 120, 220, 255))
    saved_file = _image(tmp_path / "already-saved.png", (1, 2, 3, 255))
    window = MainWindow()
    try:
        assert not window.quick_clear_button.isEnabled()
        window.target_combo.setCurrentIndex(1)
        assert window.quick_clear_button.isEnabled()
        window.quick_clear_button.click()
        assert not window.quick_clear_button.isEnabled()

        window.load_paths([source_a])
        _wait_for_preview_idle(app, window)
        assert window.workspace.current is not None
        assert window.workspace.current.path == source_a.resolve()
        assert window.edit_page.source_path == source_a.resolve()

        window.edit_page._request_or_load_image(source_b)
        _wait_for_preview_idle(app, window)
        assert window.workspace.current is not None
        assert window.workspace.current.path == source_b.resolve()
        assert window.quick_source_card._source is not None
        assert window.quick_source_card._source.path == source_b.resolve()
        assert window.edit_page.source_path == source_b.resolve()
        assert [info.path for info in window.files] == [source_a.resolve()]

        window.resize_mode.setCurrentIndex(
            window.resize_mode.findData(ResizeMode.PERCENTAGE)
        )
        window.percent_spin.setValue(45)
        window.format_combo.setCurrentIndex(
            window.format_combo.findData(OutputFormat.PNG)
        )
        window.target_combo.setCurrentIndex(2)
        window.metadata_check.setChecked(False)
        window.timestamp_check.setChecked(False)
        window.add_transform(Transform.ROTATE_RIGHT)
        window.destination_combo.setCurrentIndex(
            window.destination_combo.findData("Custom folder")
        )
        window.custom_folder = tmp_path / "custom-output"
        window.processed_check.setChecked(False)
        window.progress.setValue(100)
        window.file_tree.topLevelItem(0).setText(2, "完了")
        assert window.quick_clear_button.isEnabled()

        window.clear_quick_all()

        assert window.workspace.current is not None
        assert window.workspace.current.path == source_b.resolve()
        assert window.edit_page.source_path == source_b.resolve()
        assert window.quick_source_card._source is not None
        assert window.quick_source_card._source.path == source_b.resolve()
        assert window.files == []
        assert window.file_tree.topLevelItemCount() == 0
        assert window.current_index == -1
        assert window.files_heading.text() == "読み込んだ画像　0枚"
        assert window.drop_zone._stack.currentWidget() is window.drop_zone.overlay
        assert window.info_label.isHidden()
        assert window.options() == ProcessingOptions()
        assert window.destination_combo.currentData() == "Same folder"
        assert window.processed_check.isChecked()
        assert window.custom_folder is None
        assert window.progress.value() == 0
        assert not window.quick_save_button.isEnabled()
        assert saved_file.is_file()
    finally:
        _wait_for_preview_idle(app, window)
        window.close()


def test_edit_clear_resets_all_edit_state_history_and_saved_result(
    app: QApplication, tmp_path: Path
) -> None:
    source = _image(tmp_path / "edit-source.png", (30, 100, 180, 255))
    saved_file = _image(tmp_path / "saved-edit.png", (50, 60, 70, 255))
    window = MainWindow()
    try:
        window.set_current_source(source)
        _wait_for_preview_idle(app, window)
        page = window.edit_page
        source_before = page.source_path
        output_folder_before = page.output_folder
        default = page.settings()
        modified = replace(
            default,
            filter_preset=next(
                preset for preset in FilterPreset if preset is not FilterPreset.NONE
            ),
            transparency=replace(default.transparency, enabled=True),
            canvas=replace(default.canvas, width=64, height=64),
            text=replace(default.text, text="テスト"),
            sticker=replace(default.sticker, enabled=True),
            line_art=replace(default.line_art, enabled=True),
            palette=PaletteSettings(
                enabled=True,
                color_count=6,
                palette=((10, 20, 30), (220, 230, 240)),
                replacements=((30, 40, 50), (200, 210, 220)),
            ),
            hand_draw=HandDrawSettings(
                strokes=(
                    HandStroke(
                        points=(HandPoint(2, 2), HandPoint(30, 20)),
                        color=(255, 0, 0, 180),
                        width=5,
                    ),
                ),
                base_width=48,
                base_height=32,
            ),
        )
        page.apply_settings(modified)
        page._history = [default, page.settings()]
        page._history_index = 1
        page.hand_mode_enabled.setChecked(True)
        page.hand_eraser_button.click()
        page.hand_size_spin.setValue(19)
        page.hand_opacity_spin.setValue(40)
        page._on_saved(
            EditResult(
                saved_file,
                48,
                32,
                saved_file.stat().st_size,
                "PNG",
                True,
            )
        )
        assert page._last_output == saved_file
        assert not page.saved_box.isHidden()
        assert page.clear_all_button.isEnabled()

        page.clear_all_button.click()
        _wait_for_preview_idle(app, window)

        assert page.source_path == source_before
        assert window.workspace.current is not None
        assert window.workspace.current.path == source.resolve()
        assert page.output_folder == output_folder_before
        assert page.settings() == default
        assert page._history == [default]
        assert page._history_index == 0
        assert page._palette_values == ()
        assert page._palette_replacements == ()
        assert page._hand_draw_settings == HandDrawSettings()
        assert page.canvas_width_spin.value() == 320
        assert page.canvas_height_spin.value() == 320
        custom_index = page.canvas_preset_combo.findData("custom")
        page.canvas_preset_combo.setCurrentIndex(custom_index)
        assert page.settings().canvas.width == 320
        assert page.settings().canvas.height == 320
        assert page._hand_tool is HandTool.PEN
        assert page.hand_pen_button.isChecked()
        assert not page.hand_mode_enabled.isChecked()
        assert page.hand_size_spin.value() == 8
        assert page.hand_opacity_spin.value() == 100
        assert page._last_output is None
        assert page.saved_box.isHidden()
        assert page.result_label.text() == ""
        assert page.saved_filename.toolTip() == ""
        assert page.saved_path.toolTip() == ""
        page.saved_filename.resize(120, 24)
        page.saved_path.resize(120, 24)
        app.processEvents()
        assert page.saved_filename.text() == ""
        assert page.saved_path.text() == ""
        assert page.filename_edit.text() == "edit-source_edited"
        assert page.drop_zone.preview._image is not None
        assert saved_file.is_file()
    finally:
        _wait_for_preview_idle(app, window)
        window.close()
