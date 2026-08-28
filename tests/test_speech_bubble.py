from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageChops
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from quick_processing_tool.sound_effect_ui import SoundEffectPage
from quick_processing_tool.speech_bubble import (
    BubbleShape, SpeechBubbleSettings, TailPreset, render_speech_bubble, save_speech_bubble,
)
from quick_processing_tool.speech_bubble_ui import SpeechBubblePage


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("shape", list(BubbleShape))
def test_shapes_render_japanese_multiline_rgba_deterministically(qt_app: QApplication, shape: BubbleShape) -> None:
    settings = SpeechBubbleSettings(text="それ本当に\n言ってる？✨", shape=shape)
    first = render_speech_bubble(settings)
    second = render_speech_bubble(settings)
    assert first is not None and second is not None
    assert first.image.mode == "RGBA"
    assert ImageChops.difference(first.image, second.image).getbbox() is None
    box = first.image.getchannel("A").getbbox()
    assert box is not None
    assert box[0] > 0 and box[1] > 0 and box[2] < first.image.width and box[3] < first.image.height


def test_style_padding_and_tail_off_affect_geometry(qt_app: QApplication) -> None:
    compact = render_speech_bubble(SpeechBubbleSettings(text="会話", padding=8, tail_enabled=False, fill_color=(255, 220, 220, 180), stroke_color=(20, 40, 200, 200), stroke_width=1))
    roomy = render_speech_bubble(SpeechBubbleSettings(text="会話", padding=60, tail_enabled=True, stroke_width=20))
    assert compact is not None and roomy is not None
    assert compact.geometry.tail_tip is None
    assert roomy.image.width > compact.image.width and roomy.image.height > compact.image.height


def test_bubble_off_exports_text_only_on_transparent_canvas(qt_app: QApplication) -> None:
    enabled = render_speech_bubble(SpeechBubbleSettings(text="えっ！？", text_color=(210, 30, 70, 255)))
    text_only = render_speech_bubble(SpeechBubbleSettings(text="えっ！？", text_color=(210, 30, 70, 255), bubble_enabled=False))
    assert enabled is not None and text_only is not None
    assert text_only.geometry.tail_tip is None
    assert text_only.image.width < enabled.image.width
    assert text_only.image.height < enabled.image.height
    assert text_only.image.getchannel("A").getbbox() is not None
    assert text_only.image.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize("preset", list(TailPreset))
def test_tail_presets_point_to_expected_quadrant_without_clipping(qt_app: QApplication, preset: TailPreset) -> None:
    result = render_speech_bubble(SpeechBubbleSettings(text="えっ！？", tail_preset=preset))
    assert result is not None and result.geometry.tail_tip is not None
    cx, cy = result.geometry.body_center
    tx, ty = result.geometry.tail_tip
    assert (tx < cx) == preset.value.startswith("left")
    assert (ty < cy) == preset.value.endswith("top")
    box = result.image.getchannel("A").getbbox()
    assert box is not None and box[0] > 0 and box[1] > 0
    assert box[2] < result.image.width and box[3] < result.image.height


def test_far_tail_expands_canvas_and_is_clamped(qt_app: QApplication) -> None:
    normal = render_speech_bubble(SpeechBubbleSettings(text="遠く"))
    far = render_speech_bubble(SpeechBubbleSettings(text="遠く", tail_tip=(-3.0, 3.0)))
    assert normal is not None and far is not None
    assert far.image.width > normal.image.width and far.image.height > normal.image.height
    assert far.image.getchannel("A").getbbox() is not None


def test_tail_union_has_no_internal_base_line(qt_app: QApplication) -> None:
    result = render_speech_bubble(SpeechBubbleSettings(text="接合", shape=BubbleShape.ROUNDED, fill_color=(255, 255, 255, 255), stroke_color=(0, 0, 0, 255), stroke_width=8, tail_preset=TailPreset.RIGHT_BOTTOM))
    assert result is not None
    x, y, width, height = result.geometry.body_rect
    sample_x = round(x + width * 0.70)
    sample_y = round(y + height * 0.84)
    red, green, blue, alpha = result.image.getpixel((sample_x, sample_y))
    assert (red, green, blue, alpha) == (255, 255, 255, 255)


@pytest.mark.parametrize("percent, expected_alpha", [(100, 255), (70, 178), (50, 128), (25, 64), (0, 0)])
def test_fill_opacity_is_uniform_across_body_tail_join(qt_app: QApplication, percent: int, expected_alpha: int) -> None:
    result = render_speech_bubble(SpeechBubbleSettings(
        text="透明", fill_color=(255, 255, 255, expected_alpha), stroke_color=(0, 0, 0, 255),
        stroke_width=4, tail_preset=TailPreset.RIGHT_BOTTOM,
    ))
    assert result is not None and result.geometry.tail_tip is not None
    x, y, width, height = result.geometry.body_rect
    join = result.image.getpixel((round(x + width * 0.70), round(y + height * 0.84)))
    assert join[3] == expected_alpha
    assert join[:3] == ((0, 0, 0) if expected_alpha == 0 else (255, 255, 255))
    base_x, base_y = x + width * 0.70, y + height * 0.84
    tip_x, tip_y = result.geometry.tail_tip
    tail = result.image.getpixel((round((base_x + tip_x) / 2), round((base_y + tip_y) / 2)))
    assert tail[3] == expected_alpha
    assert tail[:3] == ((0, 0, 0) if expected_alpha == 0 else (255, 255, 255))


def test_transparent_pixels_are_rgb_zero(qt_app: QApplication) -> None:
    result = render_speech_bubble(SpeechBubbleSettings(text="Alpha", fill_color=(255, 255, 255, 160), stroke_color=(0, 0, 0, 160), text_color=(220, 20, 80, 180)))
    assert result is not None
    for red, green, blue, alpha in result.image.get_flattened_data():
        if alpha == 0:
            assert (red, green, blue) == (0, 0, 0)


def test_save_japanese_sanitizer_duplicate_reopen_and_handle_exclusion(qt_app: QApplication, tmp_path: Path) -> None:
    result = render_speech_bubble(SpeechBubbleSettings(text="えっ！？"))
    assert result is not None
    first = save_speech_bubble(result.image, tmp_path, "えっ！？:bubble.png")
    second = save_speech_bubble(result.image, tmp_path, "えっ！？:bubble.png")
    assert first.name == "えっ！？_bubble.png"
    assert second.name == "えっ！？_bubble_2.png"
    with Image.open(first) as reopened:
        reopened.load()
        assert reopened.mode == "RGBA"
        assert ImageChops.difference(reopened.convert("RGBA"), result.image).getbbox() is None


def test_preview_coordinate_roundtrip_fit_100_200_and_125(qt_app: QApplication) -> None:
    page = SpeechBubblePage()
    page.resize(1180, 760)
    page.show()
    page.text_edit.setPlainText("座標")
    page.update_preview()
    qt_app.processEvents()
    assert page.preview_result is not None and page.preview_result.geometry.tail_tip is not None
    source = QPointF(*page.preview_result.geometry.tail_tip)
    for mode in ("Fit", "100%", "200%"):
        page.preview.apply_zoom(mode)
        viewport = page.preview.image_to_viewport(source)
        mapped = page.preview.viewport_to_image(viewport)
        assert abs(mapped.x() - source.x()) < 1.1
        assert abs(mapped.y() - source.y()) < 1.1
    page.preview.resetTransform()
    page.preview.scale(1.25, 1.25)
    viewport = page.preview.image_to_viewport(source)
    mapped = page.preview.viewport_to_image(viewport)
    assert abs(mapped.x() - source.x()) < 1.1 and abs(mapped.y() - source.y()) < 1.1
    page.close()


def test_tail_drag_inside_far_and_reset(qt_app: QApplication) -> None:
    page = SpeechBubblePage()
    page.text_edit.setPlainText("Drag")
    page.update_preview()
    assert page.preview_result is not None
    center = QPointF(*page.preview_result.geometry.body_center)
    page._tail_dragged(center)
    assert page._tail_tip is not None
    assert (page._tail_tip[0] ** 2 + page._tail_tip[1] ** 2) ** 0.5 >= 1.049
    page._tail_dragged(QPointF(-100000, 100000))
    assert page._tail_tip == (-3.0, 3.0)
    page.reset_settings()
    assert page.text_edit.toPlainText() == "Drag"
    assert page._tail_tip is None
    assert page.settings().tail_preset is TailPreset.RIGHT_BOTTOM
    page.close()


def test_tail_drag_updates_shape_live_without_refitting(qt_app: QApplication) -> None:
    page = SpeechBubblePage()
    page.text_edit.setPlainText("Live drag")
    page.update_preview()
    assert page.preview_result is not None
    old_key = page.preview._item.pixmap().cacheKey()
    old_body_scene = QPointF(page.preview._body_center_scene)
    x, y, width, height = page.preview_result.geometry.body_rect
    page.preview._dragging = True
    page._tail_dragged(QPointF(x - width, y + height / 2))
    page.preview._dragging = False
    assert page.preview._item.pixmap().cacheKey() != old_key
    assert page.preview._body_center_scene is not None
    assert abs(page.preview._body_center_scene.x() - old_body_scene.x()) < 0.01
    assert abs(page.preview._body_center_scene.y() - old_body_scene.y()) < 0.01
    page.close()


def test_tail_off_hides_preset_details(qt_app: QApplication) -> None:
    page = SpeechBubblePage()
    page.tail_enabled.setChecked(False)
    assert page.tail_preset_combo.isHidden()
    assert page.tail_preset_label is not None and page.tail_preset_label.isHidden()
    assert page.tail_note.isHidden()
    page.reset_settings()
    assert not page.tail_preset_combo.isHidden()
    assert not page.tail_preset_label.isHidden()
    assert not page.tail_note.isHidden()
    page.close()


def test_bubble_toggle_hides_details_and_preserves_settings(qt_app: QApplication) -> None:
    page = SpeechBubblePage()
    page.text_edit.setPlainText("保持")
    page.shape_combo.setCurrentIndex(page.shape_combo.findData(BubbleShape.ROUNDED.value))
    page._fill_color.setRgb(20, 80, 160, 178)
    page.fill_opacity_spin.setValue(70)
    page._stroke_color.setRgb(10, 30, 200, 255)
    page.tail_preset_combo.setCurrentIndex(page.tail_preset_combo.findData(TailPreset.LEFT_TOP.value))
    page._tail_tip = (-1.4, -1.8)
    page.bubble_enabled.setChecked(False)
    page.update_preview()
    assert page.bubble_details.isHidden()
    assert page.tail_group.isHidden()
    assert page.preview_result is not None and page.preview_result.geometry.tail_tip is None
    assert not page.settings().bubble_enabled
    page.bubble_enabled.setChecked(True)
    assert not page.bubble_details.isHidden()
    assert not page.tail_group.isHidden()
    settings = page.settings()
    assert settings.shape is BubbleShape.ROUNDED
    assert settings.fill_color == (20, 80, 160, 178)
    assert settings.stroke_color == (10, 30, 200, 255)
    assert settings.tail_preset is TailPreset.LEFT_TOP
    assert settings.tail_tip == (-1.4, -1.8)
    page.close()


def test_fill_opacity_ui_and_picker_alpha_stay_in_sync(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    page = SpeechBubblePage()
    page.fill_opacity_spin.setValue(70)
    assert page._fill_color.alpha() == 178
    monkeypatch.setattr("quick_processing_tool.speech_bubble_ui.choose_color", lambda *_args, **_kwargs: QColor(12, 34, 56, 127))
    page._choose_color("塗り色", "_fill_color")
    assert page.fill_opacity_spin.value() == 50
    assert page._fill_color.alpha() == 127
    assert page.fill_color_button.text() == "#0C2238"
    page.fill_opacity_spin.setValue(49)
    page.fill_opacity_spin.setValue(50)
    assert page._fill_color.alpha() == 128
    page.close()


def test_fill_opacity_preview_export_parity(qt_app: QApplication, tmp_path: Path) -> None:
    page = SpeechBubblePage()
    page.output_folder = tmp_path
    page.text_edit.setPlainText("半透明")
    page.fill_opacity_spin.setValue(70)
    page.update_preview()
    assert page.preview_result is not None
    expected = page.preview_result.image.copy()
    page.filename_edit.setText("opacity")
    page.save_png()
    assert page.last_saved_path is not None
    with Image.open(page.last_saved_path) as reopened:
        reopened.load()
        assert ImageChops.difference(reopened.convert("RGBA"), expected).getbbox() is None
    page.close()


def test_bubble_and_sound_open_exact_duplicate_saved_path(qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Path] = []

    def capture(url) -> bool:
        opened.append(Path(url.toLocalFile()))
        return True

    monkeypatch.setattr("quick_processing_tool.speech_bubble_ui.QDesktopServices.openUrl", capture)
    bubble = SpeechBubblePage()
    bubble.output_folder = tmp_path
    bubble.text_edit.setPlainText("Bubble")
    bubble.filename_edit.setText("same")
    bubble.save_png()
    bubble.save_png()
    assert bubble.last_saved_path == tmp_path / "same_2.png"
    bubble.open_saved_image()
    bubble.open_saved_folder()
    assert opened[-2:] == [tmp_path / "same_2.png", tmp_path]
    bubble.close()

    opened.clear()
    monkeypatch.setattr("quick_processing_tool.sound_effect_ui.QDesktopServices.openUrl", capture)
    sound = SoundEffectPage()
    sound.output_folder = tmp_path
    sound.text_edit.setPlainText("ドン！")
    sound.filename_edit.setText("sound")
    sound.save_png()
    sound.save_png()
    assert sound.last_saved_path == tmp_path / "sound_2.png"
    sound.open_saved_image()
    sound.open_saved_folder()
    assert opened[-2:] == [tmp_path / "sound_2.png", tmp_path]
    sound.close()


def test_bubble_ui_states_and_full_saved_paths(qt_app: QApplication, tmp_path: Path) -> None:
    page = SpeechBubblePage()
    style = page.styleSheet()
    for selector in (
        "QPlainTextEdit:hover",
        "QPlainTextEdit:focus",
        "QPlainTextEdit:disabled",
        "QPushButton:hover",
        "QPushButton:focus",
        "QPushButton:disabled",
        "QPushButton#bubbleSave:hover",
    ):
        assert selector in style
    long_folder = tmp_path.joinpath(*(["very-long-folder-name"] * 6))
    page.output_folder = long_folder
    page.folder_label.resize(120, 28)
    page._update_save_state()
    assert page.folder_label.toolTip() == str(long_folder)
    assert "…" in page.folder_label.text()
    page.output_folder = tmp_path
    page.text_edit.setPlainText("保存先")
    page.filename_edit.setText("bubble_path")
    page.save_png()
    assert page.last_saved_path is not None
    assert page.save_result.toolTip() == str(page.last_saved_path)
    assert page.zoom_combo.currentText() == "全体表示"
    page.close()
