from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageChops
from PySide6.QtWidgets import QApplication

from quick_processing_tool.sound_effect import SoundEffectSettings, TextDirection, render_sound_effect, save_sound_effect
from quick_processing_tool.sound_effect_ui import SoundEffectPage
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_renders_japanese_unicode_multiline_rgba(qt_app: QApplication) -> None:
    image = render_sound_effect(SoundEffectSettings(text="ドン！\nざわ… ✨", padding=18))
    assert image is not None and image.mode == "RGBA"
    assert image.width > 40 and image.height > 40
    assert image.getchannel("A").getextrema() == (0, 255)


@pytest.mark.parametrize("settings", [
    SoundEffectSettings(text="ドン！", outline_width=20, padding=12),
    SoundEffectSettings(text="ざわ…", shadow_enabled=True, shadow_distance=30, shadow_blur=12, padding=12),
    SoundEffectSettings(text="回転", rotation=45, padding=12),
    SoundEffectSettings(text="拡大", scale_x=200, scale_y=200, font_size=240, padding=12),
])
def test_effects_and_transforms_are_not_clipped(qt_app: QApplication, settings: SoundEffectSettings) -> None:
    image = render_sound_effect(settings)
    assert image is not None
    box = image.getchannel("A").getbbox()
    assert box is not None
    assert box[0] > 0 and box[1] > 0 and box[2] < image.width and box[3] < image.height


def test_vertical_columns_spacing_rotation_and_scale_change_output(qt_app: QApplication) -> None:
    base = render_sound_effect(SoundEffectSettings(text="ゴゴゴ", outline_enabled=False))
    vertical = render_sound_effect(SoundEffectSettings(text="ゴゴゴ\n！？っ", direction=TextDirection.VERTICAL, letter_spacing=20, rotation=-12, scale_x=70, scale_y=140))
    assert base is not None and vertical is not None
    assert vertical.height > vertical.width and vertical.size != base.size


def test_transparent_pixels_have_no_rgb_halo(qt_app: QApplication) -> None:
    image = render_sound_effect(SoundEffectSettings(text="Alpha", color=(255, 80, 40, 180), outline_color=(30, 80, 255, 160), shadow_enabled=True, shadow_color=(0, 0, 0, 120)))
    assert image is not None
    for red, green, blue, alpha in image.get_flattened_data():
        if alpha == 0:
            assert (red, green, blue) == (0, 0, 0)


def test_translucent_fill_is_not_mixed_with_outline_interior(qt_app: QApplication) -> None:
    image = render_sound_effect(SoundEffectSettings(text="I", color=(255, 0, 0, 128), outline_color=(0, 255, 0, 255), outline_width=20, padding=20))
    assert image is not None
    red_pixels = [(red, green, blue, alpha) for red, green, blue, alpha in image.get_flattened_data() if red > 240 and green < 10 and blue < 10]
    assert red_pixels and max(alpha for _red, _green, _blue, alpha in red_pixels) <= 128


def test_rejects_pathological_canvas_before_allocation(qt_app: QApplication) -> None:
    with pytest.raises(ValueError, match="大きすぎます"):
        render_sound_effect(SoundEffectSettings(text="大" * 100, font_size=512, scale_x=200, scale_y=200, shadow_enabled=True, shadow_blur=50))


def test_save_supports_japanese_sanitizer_collision_and_rgba(qt_app: QApplication, tmp_path: Path) -> None:
    image = render_sound_effect(SoundEffectSettings(text="ぎゅっ", color=(255, 80, 150, 255)))
    assert image is not None
    first = save_sound_effect(image, tmp_path, "ぎゅっ:素材.png")
    second = save_sound_effect(image, tmp_path, "ぎゅっ:素材.png")
    assert first.name == "ぎゅっ_素材.png" and second.name == "ぎゅっ_素材_2.png"
    with Image.open(first) as reopened:
        reopened.load()
        assert reopened.mode == "RGBA" and reopened.getchannel("A").getbbox() is not None


def test_page_immediate_preview_reset_and_export_parity(qt_app: QApplication, tmp_path: Path) -> None:
    page = SoundEffectPage()
    page.output_folder = tmp_path
    page.text_edit.setPlainText("ドン！")
    page.shadow_enabled.setChecked(True)
    page.rotation_spin.setValue(15)
    page.scale_x_spin.setValue(140)
    page.scale_y_spin.setValue(80)
    page.update_preview()
    expected = render_sound_effect(page.settings())
    assert page.preview_image is not None and expected is not None
    assert ImageChops.difference(page.preview_image, expected).getbbox() is None
    page.filename_edit.setText("ドン_test")
    page.save_png()
    with Image.open(tmp_path / "ドン_test.png") as reopened:
        reopened.load()
        assert ImageChops.difference(reopened.convert("RGBA"), expected).getbbox() is None
    page.reset_settings()
    assert page.text_edit.toPlainText() == "ドン！" and page.rotation_spin.value() == 0
    assert page.scale_x_spin.value() == 100
    page.close()


def test_async_preview_is_latest_wins(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_render(settings: SoundEffectSettings) -> Image.Image:
        time.sleep(0.10 if settings.text == "first" else 0.01)
        color = (255, 0, 0, 255) if settings.text == "first" else (0, 0, 255, 255)
        return Image.new("RGBA", (8, 8), color)

    monkeypatch.setattr("quick_processing_tool.sound_effect_ui.render_sound_effect", fake_render)
    page = SoundEffectPage()
    page.text_edit.setPlainText("first")
    page._preview_timer.stop()
    page._request_preview()
    change_at = time.monotonic() + 0.07
    while time.monotonic() < change_at:
        qt_app.processEvents()
        time.sleep(0.005)
    page.text_edit.setPlainText("latest")
    stale_finish_at = time.monotonic() + 0.05
    while time.monotonic() < stale_finish_at:
        qt_app.processEvents()
        time.sleep(0.005)
    assert not page._preview_current
    assert page.preview_image is None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and (page._render_active or page._pending_render or page._preview_timer.isActive()):
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()
    assert not page._render_active
    assert page.preview_image is not None
    assert page.preview_image.getpixel((0, 0)) == (0, 0, 255, 255)
    page.close()


def test_empty_text_disables_save_and_tab_is_independent(qt_app: QApplication) -> None:
    page = SoundEffectPage()
    page.text_edit.clear()
    page.update_preview()
    assert page.preview_image is None and not page.save_button.isEnabled()
    page.close()
    window = MainWindow()
    labels = [window.navigation.tabText(index) for index in range(window.navigation.count())]
    assert "擬音素材" in labels
    assert window.navigation.widget(labels.index("擬音素材")) is window.sound_effect_page
    window.close()


def test_sound_ui_states_are_complete_and_paths_elide(qt_app: QApplication, tmp_path: Path) -> None:
    page = SoundEffectPage()
    style = page.styleSheet()
    for selector in (
        "QPlainTextEdit:hover",
        "QPlainTextEdit:focus",
        "QPlainTextEdit:disabled",
        "QPushButton:hover",
        "QPushButton:focus",
        "QPushButton:disabled",
        "QPushButton#soundSave:hover",
    ):
        assert selector in style
    long_folder = tmp_path.joinpath(*(["very-long-folder-name"] * 6))
    page.output_folder = long_folder
    page.folder_label.resize(120, 28)
    page._update_save_state()
    assert page.folder_label.toolTip() == str(long_folder)
    assert "…" in page.folder_label.text()
    assert page.zoom_combo.currentText() == "全体表示"
    page.close()


def test_reset_preserves_text_and_clear_all_returns_to_fresh_state(
    qt_app: QApplication, tmp_path: Path
) -> None:
    page = SoundEffectPage()
    defaults = page.settings()
    output_folder = tmp_path / "擬音の保存先"
    output_folder.mkdir()
    page.output_folder = output_folder
    page.text_edit.setPlainText("ドン！")
    page.shadow_enabled.setChecked(True)
    page.rotation_spin.setValue(24)
    page.scale_x_spin.setValue(135)
    page.reset_button.click()
    assert page.settings() == replace(defaults, text="ドン！")

    page.shadow_enabled.setChecked(True)
    page.rotation_spin.setValue(-15)
    page.scale_y_spin.setValue(80)
    page.filename_edit.setText("保存済み擬音")
    page.save_png()
    saved_path = page.last_saved_path
    assert saved_path is not None and saved_path.is_file()
    assert not page.open_image_button.isHidden()
    assert not page.open_folder_button.isHidden()

    page.clear_all_button.click()

    assert page.settings() == defaults
    assert page.text_edit.toPlainText() == ""
    assert page.filename_edit.text() == "sound_effect"
    assert page.preview_image is None
    assert not page.save_button.isEnabled()
    assert page.last_saved_path is None
    assert page.save_result.text() == ""
    assert page.save_result.toolTip() == ""
    assert page.open_image_button.isHidden()
    assert page.open_folder_button.isHidden()
    assert page.output_folder == output_folder
    assert saved_path.is_file()
    page.close()
