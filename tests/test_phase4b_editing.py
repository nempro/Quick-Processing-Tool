from PIL import Image
import inspect
import time
from pathlib import Path
from quick_processing_tool.editing.renderer import render_edit, render_preview
from quick_processing_tool.editing.palette import rgba_digest
from PIL import ImageDraw
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])

def _alpha_bytes(image: Image.Image) -> bytes:
    return image.convert("RGBA").getchannel("A").tobytes()


def _rgba_pixels(image: Image.Image):
    rgba = image.convert("RGBA")
    get_flattened = getattr(rgba, "get_flattened_data", None)
    if callable(get_flattened):
        return get_flattened()
    return rgba.getdata()


def _luminance_values(image: Image.Image) -> list[int]:
    values = []
    for red, green, blue, alpha in _rgba_pixels(image):
        if alpha == 0:
            continue
        values.append(round(0.2126 * red + 0.7152 * green + 0.0722 * blue))
    return values


from quick_processing_tool.editing.line_art import EDGE_METHOD, apply_line_art, compare_edge_candidates, edge_mask, edge_mask_candidate
from quick_processing_tool.editing.models import (
    FilterPreset,
    LineArtAmount,
    LineArtBackground,
    LineArtSettings,
    PaletteSettings,
    RecolorBlendMode,
    StickerSettings,
)
from quick_processing_tool.editing.palette import (
    apply_palette_mapping,
    apply_palette_mapping_preserve_shading,
    apply_palette_mapping_smooth,
    extract_palette,
    mapping_for_palette,
)
from quick_processing_tool.editing.sticker import apply_sticker


def test_palette_ignores_transparent_pixels_and_recolors_without_alpha_halo() -> None:
    image = Image.new("RGBA", (4, 1), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 0))
    mapping = extract_palette(image, 5)
    assert len(mapping.palette) == 2
    recolored = apply_palette_mapping(image, mapping, ((10, 20, 30),) * len(mapping.palette))
    assert recolored.getpixel((0, 0))[:3] == (10, 20, 30)
    assert recolored.getpixel((2, 0)) == (0, 0, 255, 0)


def test_sticker_outline_is_behind_foreground_and_rejects_opaque() -> None:
    image = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    image.putpixel((4, 4), (20, 40, 60, 255))
    result = apply_sticker(image, StickerSettings(enabled=True, outline_width=2))
    assert result.getpixel((4, 4)) == (20, 40, 60, 255)
    assert result.getpixel((2, 4))[:3] == (255, 255, 255)
    try:
        apply_sticker(Image.new("RGBA", (2, 2), (1, 2, 3, 255)), StickerSettings(enabled=True))
    except ValueError as exc:
        assert "ステッカー化" in str(exc)
    else:
        raise AssertionError("opaque sticker input must provide guidance")


def test_line_art_presets_keep_dimensions_and_background_contract() -> None:
    image = Image.new("RGBA", (12, 8), (255, 255, 255, 255))
    for y in range(8):
        image.putpixel((6, y), (0, 0, 0, 255))
    for amount in LineArtAmount:
        result = apply_line_art(
            image,
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.WHITE),
        )
        assert result.size == image.size
        assert result.mode == "RGBA"
        assert result.getpixel((0, 0))[3] == 255
        assert any(result.getpixel((x, y))[:3] != (255, 255, 255) for x in range(12) for y in range(8))


def test_line_expression_ui_labels_tooltips_data_and_details_contract(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    page = QuickEditPage()
    page.material_section.set_expanded(True)
    page.show()
    qt_app.processEvents()
    assert page.line_art_group.title() == "輪郭・線表現"
    assert page.line_art_enabled.text() == "線で表現する"
    assert page.line_art_description.text() == "線主体の表現へ変えます"
    assert not hasattr(page, "line_art_amount_combo")
    assert page.line_art_color_button.toolTip().startswith("輪郭・線表現に使う線の色を選びます\n現在:")
    assert page.line_art_background_color_button.toolTip().startswith("輪郭・線表現の背景色を選びます\n現在:")
    assert page.settings().line_art.amount is LineArtAmount.STANDARD
    assert page.line_art_details.isHidden()
    page.line_art_enabled.setChecked(True)
    qt_app.processEvents()
    assert page.line_art_details.isVisible()
    assert not page.line_art_background_color_button.isVisible()
    page.line_art_background_combo.setCurrentIndex(
        page.line_art_background_combo.findData(LineArtBackground.CUSTOM.value)
    )
    qt_app.processEvents()
    assert page.line_art_background_color_button.isVisible()
    page.line_art_enabled.setChecked(False)
    qt_app.processEvents()
    assert page.line_art_details.isHidden()
    passthrough = Image.new("RGBA", (7, 5), (20, 40, 60, 120))
    assert apply_line_art(passthrough, LineArtSettings()).tobytes() == passthrough.tobytes()
    page.close()


def test_edit_inspector_descriptions_and_conditional_groups_are_compact(
    qt_app, tmp_path: Path
) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "compact-inspector.png"
    Image.new("RGB", (32, 24), "white").save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        page.show()
        _wait_for_edit_preview_idle(qt_app, page)
        expected_descriptions = {
            page.filter_section: "",
            page.text_section: "",
            page.hand_section: "",
            page.transparency_section: "近い色もまとめて透明にします",
            page.canvas_section: "",
            page.material_section: "",
        }
        assert len(page.sections) == 6
        for section, text in expected_descriptions.items():
            section.set_expanded(False)
            assert section.description.text() == text
            assert section.description.isHidden()
            section.set_expanded(True)
            assert section.description.isVisible() is bool(text)
            section.set_expanded(False)

        page.hand_section.set_expanded(True)
        assert page.hand_mode_enabled.text() == "ON"
        assert page.hand_mode_enabled.accessibleName() == "手書きの描画モード"
        assert page.hand_details.isHidden()
        page.hand_mode_enabled.setChecked(True)
        assert page.hand_details.isVisible()
        assert page.hand_visible_check.text() == "表示"
        assert "プレビューと保存画像" in page.hand_visible_check.toolTip()

        page.transparency_section.set_expanded(True)
        assert page.transparency_details.isHidden()
        page.transparency_enabled.setChecked(True)
        assert page.transparency_details.isVisible()

        page.material_section.set_expanded(True)
        assert page.sticker_details.isHidden()
        page.sticker_enabled.setChecked(True)
        assert page.sticker_details.isVisible()
        assert page.sticker_prereq_status.text() == ""
        assert page.sticker_prereq_button.isHidden()
        page.transparency_enabled.setChecked(False)
        assert page.sticker_prereq_status.text() == "背景透過が必要です"
        assert page.sticker_prereq_button.text() == "背景透過を開く →"
        assert page.sticker_prereq_button.isVisible()

        assert page.line_art_details.isHidden()
        page.line_art_enabled.setChecked(True)
        assert page.line_art_details.isVisible()
        assert page.line_art_description.text() == "線主体の表現へ変えます"

        assert page.palette_results_widget.isHidden()
        assert page.palette_count_combo.isVisible()
        assert page.palette_extract_button.isVisible()
        colors = tuple((index * 19, index * 13, index * 7) for index in range(12))
        page._palette_values = colors
        page._palette_replacements = colors
        page._rebuild_palette_chips()
        assert page.palette_results_widget.isVisible()
        assert len(page._palette_reset_buttons) == 12
        assert page.palette_send_button.height() <= 36
        assert page.sticker_prereq_button.minimumHeight() >= 30
        assert page.hand_clear_button.height() <= 36
    finally:
        _wait_for_edit_preview_idle(qt_app, page)
        page.close()


@pytest.mark.parametrize(
    "line_color,background,custom_background,expected_background",
    [
        ((0, 0, 0, 255), LineArtBackground.WHITE, (1, 2, 3, 255), (255, 255, 255, 255)),
        ((255, 105, 180, 255), LineArtBackground.TRANSPARENT, (1, 2, 3, 255), (0, 0, 0, 0)),
        ((0, 70, 255, 255), LineArtBackground.CUSTOM, (255, 248, 220, 255), (255, 248, 220, 255)),
        ((255, 255, 255, 255), LineArtBackground.BLACK, (1, 2, 3, 255), (0, 0, 0, 255)),
    ],
)
def test_line_expression_exact_line_and_background_colors(
    line_color, background, custom_background, expected_background
) -> None:
    source = Image.new("RGBA", (48, 32), (255, 255, 255, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((8, 7, 38, 25), outline="black", width=3)
    settings = LineArtSettings(
        enabled=True,
        amount=LineArtAmount.STANDARD,
        line_color=line_color,
        background=background,
        custom_background=custom_background,
    )
    mask = edge_mask(source, settings.amount)
    result = apply_line_art(source, settings)
    line_point = next((x, y) for y in range(mask.height) for x in range(mask.width) if mask.getpixel((x, y)) == 255)
    background_point = next((x, y) for y in range(mask.height) for x in range(mask.width) if mask.getpixel((x, y)) == 0)
    assert result.getpixel(line_point) == line_color
    assert result.getpixel(background_point) == expected_background


def test_line_expression_clips_hidden_rgb_and_transparency_background_edges() -> None:
    source = Image.new("RGBA", (32, 24), (255, 255, 255, 0))
    for y in range(source.height):
        for x in range(source.width):
            source.putpixel((x, y), ((255, 0, 0, 0) if (x + y) % 2 else (0, 0, 255, 0)))
    ImageDraw.Draw(source).rectangle((10, 7, 21, 16), fill=(20, 180, 40, 255))
    settings = LineArtSettings(enabled=True, amount=LineArtAmount.COMIC)
    result = apply_line_art(source, settings)
    assert all(
        result.getpixel((x, y))[3] == 0
        for y in range(source.height)
        for x in range(source.width)
        if source.getpixel((x, y))[3] == 0
    )
    assert result.getchannel("A").getbbox() is not None

    from quick_processing_tool.editing import EditSettings, TransparencySettings
    opaque = Image.new("RGBA", (32, 24), "white")
    ImageDraw.Draw(opaque).rectangle((10, 7, 21, 16), fill=(220, 20, 30, 255))
    rendered = render_edit(
        opaque,
        EditSettings(
            transparency=TransparencySettings(True, (255, 255, 255), 0, 0),
            line_art=LineArtSettings(enabled=True, amount=LineArtAmount.DETAILED),
        ),
    )
    assert all(
        rendered.getpixel((x, y))[3] == 0
        for y in range(opaque.height)
        for x in range(opaque.width)
        if not (10 <= x <= 21 and 7 <= y <= 16)
    )


def test_fixed_standard_line_expression_preserves_selected_line_alpha() -> None:
    source = Image.new("RGBA", (32, 24), "white")
    ImageDraw.Draw(source).rectangle((7, 5, 24, 18), outline="black", width=3)
    settings = LineArtSettings(
        enabled=True,
        amount=LineArtAmount.STANDARD,
        line_color=(12, 34, 56, 128),
        background=LineArtBackground.TRANSPARENT,
    )
    mask = edge_mask(source, LineArtAmount.STANDARD)
    line_point = next(
        (x, y)
        for y in range(mask.height)
        for x in range(mask.width)
        if mask.getpixel((x, y)) == 255
    )
    result = apply_line_art(source, settings)
    assert result.getpixel(line_point) == (12, 34, 56, 128)


def test_recolor_then_line_expression_uses_final_line_and_background_colors() -> None:
    from quick_processing_tool.editing import EditSettings

    source = Image.new("RGBA", (48, 32), (240, 40, 30, 255))
    ImageDraw.Draw(source).ellipse((8, 5, 38, 27), fill=(20, 170, 210, 255), outline=(5, 10, 20, 255), width=2)
    mapping = extract_palette(source, 5)
    replacements = tuple((255 - red, 255 - green, 255 - blue) for red, green, blue in mapping.palette)
    line_color = (12, 34, 56, 255)
    background = (255, 248, 220, 255)
    settings = EditSettings(
        palette=PaletteSettings(
            True,
            True,
            5,
            mapping.palette,
            replacements,
            mapping.indices,
            mapping.width,
            mapping.height,
            mapping.digest,
        ),
        line_art=LineArtSettings(
            True,
            LineArtAmount.DETAILED,
            line_color,
            LineArtBackground.CUSTOM,
            background,
        ),
    )
    result = render_edit(source, settings)
    colors = set(_rgba_pixels(result))
    assert colors <= {line_color, background}
    assert line_color in colors and background in colors


def test_small_line_expression_preview_and_export_match_render_edit(tmp_path: Path) -> None:
    from quick_processing_tool.editing import EditOutputFormat, EditSettings
    from quick_processing_tool.editing.service import EditService

    source_path = tmp_path / "line-parity.png"
    source = Image.new("RGBA", (52, 36), "white")
    ImageDraw.Draw(source).rectangle((7, 6, 43, 29), outline="black", width=3)
    source.save(source_path)
    settings = EditSettings(
        line_art=LineArtSettings(
            True,
            LineArtAmount.COMIC,
            (0, 70, 255, 255),
            LineArtBackground.CUSTOM,
            (255, 248, 220, 255),
        )
    )
    rendered = render_edit(source, settings)
    preview = render_preview(source, settings, 1400)
    assert preview.size == rendered.size
    assert preview.tobytes() == rendered.tobytes()
    exported = EditService().export(source_path, tmp_path, settings, EditOutputFormat.PNG)
    with Image.open(exported.output_path) as reopened:
        reopened.load()
        assert reopened.convert("RGBA").tobytes() == rendered.tobytes()


def test_line_expression_changes_are_single_history_actions_and_branch(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "line-history.png"
    Image.new("RGB", (24, 18), "white").save(source)
    page = QuickEditPage()
    assert page.load_image(source)
    start = len(page._history)
    page.line_art_enabled.setChecked(True)
    monkeypatch.setattr(edit_ui, "choose_color", lambda *_args, **_kwargs: QColor(255, 105, 180, 255))
    page.choose_line_art_color()
    page.line_art_background_combo.setCurrentIndex(
        page.line_art_background_combo.findData(LineArtBackground.WHITE.value)
    )
    assert len(page._history) == start + 3
    assert page.settings().line_art == LineArtSettings(
        True,
        LineArtAmount.STANDARD,
        (255, 105, 180, 255),
        LineArtBackground.WHITE,
        (255, 255, 255, 255),
    )
    page.undo()
    assert page.settings().line_art.background is LineArtBackground.TRANSPARENT
    page.undo()
    assert page.settings().line_art.line_color == (0, 0, 0, 255)
    page.undo()
    assert not page.settings().line_art.enabled
    for _ in range(3):
        page.redo()
    assert page.settings().line_art.background is LineArtBackground.WHITE
    page.undo()
    page.line_art_background_combo.setCurrentIndex(
        page.line_art_background_combo.findData(LineArtBackground.BLACK.value)
    )
    assert page.settings().line_art.amount is LineArtAmount.STANDARD
    assert page.settings().line_art.background is LineArtBackground.BLACK
    assert not page.redo_button.isEnabled()
    _wait_for_edit_preview_idle(qt_app, page)
    page.close()


def test_line_expression_same_source_keeps_settings_replacement_resets(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.image_workspace import read_source_image

    first = tmp_path / "line-source-a.png"
    second = tmp_path / "line-source-b.png"
    Image.new("RGB", (24, 18), "white").save(first)
    Image.new("RGB", (30, 20), "blue").save(second)
    page = QuickEditPage()
    assert page.load_image(first)
    page.line_art_enabled.setChecked(True)
    page._line_art_color = QColor(20, 30, 40, 255)
    page._control_changed()
    kept = page.settings().line_art
    page.set_current_source(read_source_image(first, 1))
    assert page.settings().line_art == kept
    page.set_current_source(read_source_image(second, 2))
    assert page.source_path == second.resolve()
    assert page.settings().line_art == LineArtSettings()
    assert page.line_art_details.isHidden()
    assert len(page._history) == 1 and page._history_index == 0
    _wait_for_edit_preview_idle(qt_app, page)
    page.close()


def test_legacy_line_profiles_are_canonicalized_and_deduped_in_history(qt_app) -> None:
    from dataclasses import replace
    from quick_processing_tool.edit_ui import QuickEditPage

    page = QuickEditPage()
    base = page.settings()
    clean = replace(
        base,
        line_art=replace(base.line_art, enabled=True, amount=LineArtAmount.CLEAN),
    )
    comic = replace(clean, line_art=replace(clean.line_art, amount=LineArtAmount.COMIC))
    colored = replace(
        comic,
        line_art=replace(
            comic.line_art,
            amount=LineArtAmount.DETAILED,
            line_color=(12, 34, 56, 255),
            background=LineArtBackground.BLACK,
        ),
    )
    page._history = [base, clean, comic, colored]
    page._history_index = 3
    page.apply_settings(colored)
    page._canonicalize_line_expression_history()
    assert [item.line_art.amount for item in page._history] == [
        LineArtAmount.STANDARD,
        LineArtAmount.STANDARD,
        LineArtAmount.STANDARD,
    ]
    assert len(page._history) == 3
    assert page.settings().line_art == replace(
        colored.line_art, amount=LineArtAmount.STANDARD
    )
    page.undo()
    assert page.settings().line_art.enabled
    assert page.settings().line_art.line_color == (0, 0, 0, 255)
    page.undo()
    assert not page.settings().line_art.enabled
    page.redo()
    page.redo()
    assert page.settings().line_art.line_color == (12, 34, 56, 255)
    assert page.settings().line_art.background is LineArtBackground.BLACK
    page.close()


def test_phase4b_settings_defaults_are_non_destructive() -> None:
    settings = PaletteSettings()
    assert settings.color_count == 6
    assert settings.blend_mode is RecolorBlendMode.SHARP
    assert not settings.enabled and not settings.quantize_enabled
    assert not StickerSettings().enabled
    assert not LineArtSettings().enabled


def test_cached_palette_mapping_recolor_changes_values_only() -> None:
    image = Image.new("RGBA", (3, 1), (250, 0, 0, 255))
    image.putpixel((1, 0), (0, 250, 0, 160))
    mapping = extract_palette(image, 6)
    scaled = image.resize((6, 2), Image.Resampling.NEAREST)
    stable = mapping_for_palette(scaled, mapping.palette)
    result = apply_palette_mapping(scaled, stable, tuple((1, 2, 3) for _ in mapping.palette))
    assert result.size == scaled.size
    assert result.getpixel((2, 0))[3] == 160
    assert result.getpixel((0, 0))[:3] == (1, 2, 3)


def test_sharp_blend_mode_matches_existing_recolor_bytes() -> None:
    from quick_processing_tool.editing import EditSettings

    image = Image.new("RGBA", (4, 1))
    image.putdata([(255, 0, 0, 255), (220, 10, 10, 255), (0, 255, 0, 255), (0, 210, 20, 255)])
    mapping = extract_palette(image, 6)
    replacements = tuple((30, 120, 240) if i == 0 else (250, 200, 20) for i in range(len(mapping.palette)))
    legacy = apply_palette_mapping(image, mapping, replacements)
    settings = EditSettings(palette=PaletteSettings(True, False, 6, mapping.palette, replacements, mapping.indices, mapping.width, mapping.height, mapping.digest, RecolorBlendMode.SHARP))
    assert render_edit(image, settings).tobytes() == legacy.tobytes()


def test_smooth_blend_changes_only_boundary_band_and_preserves_alpha() -> None:
    image = Image.new("RGBA", (8, 6), (0, 0, 0, 0))
    for y in range(1, 5):
        for x in range(1, 4):
            image.putpixel((x, y), (255, 0, 0, 255))
        for x in range(4, 7):
            image.putpixel((x, y), (0, 255, 0, 255))
    mapping = extract_palette(image, 6)
    replacements = tuple(reversed(mapping.palette))
    sharp = apply_palette_mapping(image, mapping, replacements)
    smooth = apply_palette_mapping_smooth(image, mapping, replacements)
    assert smooth.size == sharp.size
    assert _alpha_bytes(smooth) == _alpha_bytes(sharp)
    assert smooth.getpixel((3, 2)) != sharp.getpixel((3, 2))
    assert smooth.getpixel((1, 2)) == sharp.getpixel((1, 2))
    assert smooth.getpixel((6, 3)) == sharp.getpixel((6, 3))
    assert smooth.getpixel((0, 0)) == sharp.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("replacement", "dominant"),
    [
        ((0, 255, 255), "cyan"),
        ((255, 0, 255), "magenta"),
        ((255, 255, 0), "yellow"),
        ((0, 0, 0), "black"),
        ((255, 255, 255), "white"),
    ],
)
def test_shading_preserves_luminance_order_and_target_hue_dominance(replacement, dominant) -> None:
    source = Image.new("RGBA", (4, 1))
    source.putdata([(24, 24, 24, 255), (96, 96, 96, 255), (176, 176, 176, 255), (240, 240, 240, 255)])
    mapping = mapping_for_palette(source, ((128, 128, 128),))
    shaded = apply_palette_mapping_preserve_shading(source, mapping, (replacement,))
    luminances = _luminance_values(shaded)
    assert luminances == sorted(luminances)
    probe = shaded.getpixel((2, 0))[:3]
    if dominant == "cyan":
        assert probe[1] > probe[0] and probe[2] > probe[0]
    elif dominant == "magenta":
        assert probe[0] > probe[1] and probe[2] > probe[1]
    elif dominant == "yellow":
        assert probe[0] > probe[2] and probe[1] > probe[2]
    elif dominant == "black":
        assert max(probe) < 80
    else:
        assert luminances[-1] - luminances[0] >= 20
        assert max(shaded.getpixel((0, 0))[:3]) < 245
        assert max(probe) >= 245


def test_palette_recolor_single_and_multiple_changes_apply_expected_indices() -> None:
    image = Image.new("RGBA", (3, 1))
    image.putdata([
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 180),
    ])
    mapping = extract_palette(image, 6)
    first_index, second_index, third_index = mapping.indices

    single = list(mapping.palette)
    single[first_index] = (120, 30, 220)
    single_result = apply_palette_mapping(image, mapping, tuple(single))
    assert single_result.getpixel((0, 0))[:3] == (120, 30, 220)

    multiple = list(mapping.palette)
    multiple[first_index] = (120, 30, 220)
    multiple[second_index] = (15, 25, 35)
    multiple[third_index] = (250, 210, 180)
    multi_result = apply_palette_mapping(image, mapping, tuple(multiple))
    assert multi_result.getpixel((0, 0))[:3] == (120, 30, 220)
    assert multi_result.getpixel((1, 0))[:3] == (15, 25, 35)
    assert multi_result.getpixel((2, 0)) == (250, 210, 180, 180)


def test_palette_counts_and_transparent_only_contract() -> None:
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    assert extract_palette(image, 5).palette == ()
    for count in (5, 6, 8, 12):
        source = Image.new("RGBA", (count, 1))
        for x in range(count):
            source.putpixel((x, 0), ((x * 37) % 256, (x * 61) % 256, (x * 89) % 256, 255))
        mapping = extract_palette(source, count)
        assert 1 <= len(mapping.palette) <= count
        assert len(mapping.indices) == count


def test_sticker_shadow_and_line_art_transparency_preserve_dimensions() -> None:
    source = Image.new("RGBA", (20, 14), (0, 0, 0, 0))
    source.paste((200, 20, 30, 220), (5, 4, 14, 10))
    sticker = apply_sticker(source, StickerSettings(enabled=True, outline_width=3, shadow_enabled=True))
    assert sticker.size == source.size and sticker.mode == "RGBA"
    line = apply_line_art(source, LineArtSettings(enabled=True, background=LineArtBackground.TRANSPARENT))
    assert line.size == source.size and line.mode == "RGBA"


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_material_ui_defaults_and_vertical_scroll(qt_app, width: int) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    page.resize(width, 680)
    page.show()
    qt_app.processEvents()
    assert len(page.sections) == 6
    assert page.settings().palette.color_count == 6
    assert page.settings().palette.blend_mode is RecolorBlendMode.SHARP
    assert not page.settings().sticker.enabled
    assert not page.settings().line_art.enabled
    assert page.palette_extract_button.text() == "色を取り出す"
    assert page.palette_quantize_enabled.text() == "6色に整理"
    assert page.palette_blend_mode_combo.currentText() == "鮮明"
    assert page.palette_blend_description_label.text() == "色面をはっきり分けます"
    assert [page.palette_blend_mode_combo.itemText(i) for i in range(page.palette_blend_mode_combo.count())] == ["鮮明", "滑らか", "陰影"]
    assert not page.palette_quantize_enabled.isEnabled()
    assert page.palette_quantize_guide_label.text() == ""
    assert page.palette_results_widget.isHidden()
    assert not page.palette_send_button.isVisible()
    assert not page.palette_open_button.isVisible()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    page.material_section.toggle.click()
    qt_app.processEvents()
    viewport = page.settings_scroll.viewport()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert page.palette_count_combo.width() <= viewport.width()
    assert not page.palette_blend_mode_combo.isVisible()
    assert not page.palette_blend_description_label.isVisible()
    page.material_section.toggle.click()
    qt_app.processEvents()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    page.close()


def test_material_controls_round_trip_settings(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings
    page = QuickEditPage()
    page.sticker_enabled.setChecked(True)
    page.sticker_outline_width_spin.setValue(12)
    page.line_art_enabled.setChecked(True)
    page.palette_enabled.setChecked(True)
    page.palette_quantize_enabled.setChecked(True)
    page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(8))
    page.palette_blend_mode_combo.setCurrentIndex(page.palette_blend_mode_combo.findData(RecolorBlendMode.PRESERVE_SHADING.value))
    settings = page.settings()
    assert settings.sticker.outline_width == 12
    assert settings.line_art.enabled
    assert settings.palette.quantize_enabled and settings.palette.color_count == 8
    assert settings.palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
    page.apply_settings(EditSettings())
    assert not page.settings().sticker.enabled
    assert not page.settings().line_art.enabled
    assert page.settings().palette.blend_mode is RecolorBlendMode.SHARP
    page.close()

def test_line_art_candidates_differ_and_amounts_are_monotonic() -> None:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 52, 52), outline="black", width=3)
    candidates = compare_edge_candidates(image)
    assert candidates["find_edges"]["edge_density"] != candidates["sobel"]["edge_density"]
    amounts = [sum(edge_mask_candidate(image, amount, "sobel").get_flattened_data()) for amount in LineArtAmount]
    assert amounts[0] <= amounts[1] <= amounts[2]
    assert EDGE_METHOD == "sobel"


def test_palette_upstream_change_clears_and_scrubs_undo_history(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings
    source = tmp_path / "red-green.png"
    image = Image.new("RGB", (2, 1))
    image.putdata([(255, 0, 0), (0, 255, 0)])
    image.save(source)
    page = QuickEditPage()
    page.load_image(source)
    palette = ((255, 0, 0), (0, 255, 0))
    settings = EditSettings(palette=PaletteSettings(True, True, 6, palette, palette, (0, 1), 2, 1))
    page.apply_settings(settings)
    page._control_changed()
    page.filter_combo.setCurrentIndex(page.filter_combo.findData("grayscale"))
    qt_app.processEvents()
    assert page.settings().palette.palette == ()
    assert page.settings().palette.replacements == ()
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    assert page._palette_needs_reextract
    assert page.palette_quantize_guide_label.text() == "画像が変更されました。もう一度色を取り出してください。"
    page.undo()
    assert page.settings().filter_preset.value == "none"
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    page.redo()
    assert page.settings().filter_preset.value == "grayscale"
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    page.undo()
    page.line_art_enabled.setChecked(True)
    qt_app.processEvents()
    assert page.settings().palette.mapping == ()
    assert all(not item.palette.mapping for item in page._history)
    page.close()



def test_palette_count_change_clears_and_requests_reextract(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings

    source = tmp_path / "count-change.png"
    Image.new("RGB", (4, 1), "red").save(source)
    page = QuickEditPage()
    page.load_image(source)
    settings = EditSettings(palette=PaletteSettings(True, True, 6, ((255, 0, 0),), ((255, 0, 0),), (0, 0, 0, 0), 4, 1))
    page.apply_settings(settings)
    page._control_changed()

    page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(5))
    qt_app.processEvents()
    assert page.settings().palette.palette == ()
    assert page.settings().palette.replacements == ()
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    assert page._palette_needs_reextract
    assert page.palette_quantize_guide_label.text() == "色数が変更されました。5色で取り直してください。"

    page.undo()
    assert page.settings().palette.color_count == 6
    assert page.settings().palette.palette == ()
    assert all(not item.palette.mapping for item in page._history)
    page.close()

def test_palette_blend_mode_history_and_mapping_persist(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings

    source = tmp_path / "blend-history.png"
    Image.new("RGB", (4, 1), "red").save(source)
    page = QuickEditPage()
    page.load_image(source)
    settings = EditSettings(palette=PaletteSettings(True, True, 6, ((255, 0, 0),), ((255, 0, 0),), (0, 0, 0, 0), 4, 1, "digest", RecolorBlendMode.SHARP))
    page.apply_settings(settings)
    page._control_changed()

    page.palette_blend_mode_combo.setCurrentIndex(page.palette_blend_mode_combo.findData(RecolorBlendMode.PRESERVE_SHADING.value))
    qt_app.processEvents()
    assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
    assert page.settings().palette.mapping == (0, 0, 0, 0)
    assert page.settings().palette.mapping_digest == "digest"

    page.reset_palette()
    qt_app.processEvents()
    assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING

    page.undo()
    assert page.settings().palette.blend_mode is RecolorBlendMode.SHARP
    page.redo()
    assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
    page.close()


def test_extract_palette_uses_fastoctree(monkeypatch) -> None:
    seen = []
    original = Image.Image.quantize

    def recording_quantize(self, *args, **kwargs):
        seen.append(kwargs.get("method"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "quantize", recording_quantize)
    image = Image.new("RGBA", (4, 1), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 0))
    extract_palette(image, 5)
    assert seen == [Image.Quantize.FASTOCTREE]


def test_renderer_rejects_stale_cached_mapping_digest() -> None:
    source = Image.new("RGB", (2, 1))
    source.putdata([(255, 0, 0), (0, 255, 0)])
    mapping = extract_palette(source, 6)
    cached = PaletteSettings(True, True, 6, mapping.palette, mapping.palette, mapping.indices, 2, 1, mapping.digest)
    stale_settings = __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(filter_preset=FilterPreset.GRAYSCALE, palette=cached)
    fresh = PaletteSettings(True, True, 6, mapping.palette, mapping.palette, (), 0, 0, "")
    fresh_settings = __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(filter_preset=FilterPreset.GRAYSCALE, palette=fresh)
    assert rgba_digest(source) == mapping.digest
    assert render_edit(source, stale_settings).tobytes() == render_edit(source, fresh_settings).tobytes()


def test_palette_worker_applies_current_result_and_digest(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    source = tmp_path / "worker.png"
    image = Image.new("RGB", (32, 24), "red")
    image.save(source)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    assert page.palette_extract_button.isEnabled()
    assert page.palette_count_combo.isEnabled()
    deadline = time.monotonic() + 3
    while page._palette_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert page._palette_thread is None
    assert page._palette_mapping_digest
    assert page.settings().palette.mapping_digest == page._palette_mapping_digest
    assert page.palette_extract_button.isEnabled()
    page.close()


def test_palette_worker_stale_result_and_error_are_ignored_or_localized(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    page._palette_values = ((1, 2, 3),)
    token = (1, 9, ("source.png", 1, 1), ("none", page.settings().transparency, 6))
    page._palette_active_request = token
    stale = (0, 8, token[2], token[3], extract_palette(Image.new("RGB", (1, 1), "blue"), 6))
    page._on_palette_extracted(stale)
    assert page._palette_values == ((1, 2, 3),)
    page.preview_status.setText("unchanged")
    page._on_palette_extraction_failed("代表色を抽出できませんでした。", (0, 8, token[2], token[3]))
    assert page.preview_status.text() == "unchanged"
    page._on_palette_extraction_failed("代表色を抽出できませんでした。", token)
    assert "代表色を抽出できませんでした" in page.preview_status.text()
    assert "c62828" in page.preview_status.styleSheet()
    page.cancel_palette_extraction()
    page.close()


def _wait_for_palette_thread_idle(qt_app, page, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while (page._palette_thread is not None or page._preview_thread is not None) and time.monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert page._palette_thread is None
    assert page._preview_thread is None


def _wait_for_edit_preview_idle(qt_app, page, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while page._preview_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert page._preview_thread is None


def test_palette_thread_runs_off_gui_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    import threading
    import shiboken6
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import PaletteExtractionThread, QuickEditPage

    source = tmp_path / "thread-check.png"
    Image.new("RGB", (32, 24), "red").save(source)
    main_thread_id = threading.get_ident()
    thread_checks: list[bool] = []

    class RecordingThread(PaletteExtractionThread):
        def run(self) -> None:
            thread_checks.append(threading.get_ident() != main_thread_id)
            super().run()

    monkeypatch.setattr(edit_ui, "PaletteExtractionThread", RecordingThread)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    thread = page._palette_thread
    assert thread is not None
    _wait_for_palette_thread_idle(qt_app, page)
    assert thread_checks == [True]
    assert page.can_close()
    assert page.palette_extract_button.isEnabled()
    qt_app.processEvents()
    assert not shiboken6.isValid(thread)
    page.close()


def test_palette_worker_runtime_failure_localizes_and_cleans_up(qt_app, tmp_path: Path, monkeypatch) -> None:
    import shiboken6
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "worker-error.png"
    Image.new("RGB", (32, 24), "red").save(source)

    def boom(_image, _count):
        raise RuntimeError("boom")

    monkeypatch.setattr(edit_ui, "extract_palette", boom)
    page = QuickEditPage()
    page.load_image(source)
    finished_threads = []
    for _ in range(20):
        page.extract_palette()
        thread = page._palette_thread
        assert thread is not None
        _wait_for_palette_thread_idle(qt_app, page)
        assert page._palette_active_request is None
        assert page.can_close()
        assert page.palette_extract_button.isEnabled()
        assert "代表色を抽出できませんでした。" in page.preview_status.text()
        assert "c62828" in page.preview_status.styleSheet()
        finished_threads.append(thread)
    qt_app.processEvents()
    assert all(not shiboken6.isValid(thread) for thread in finished_threads)
    page.close()


def test_palette_worker_cancel_cleans_up_and_deletes_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    import shiboken6
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import PaletteExtractionThread, QuickEditPage

    source = tmp_path / "cancel-thread-check.png"
    Image.new("RGB", (32, 24), "red").save(source)
    started: list[bool] = []

    class SlowCancelableThread(PaletteExtractionThread):
        def run(self) -> None:
            started.append(True)
            deadline = time.monotonic() + 1.0
            while not self.isInterruptionRequested() and time.monotonic() < deadline:
                time.sleep(0.01)
            if self.isInterruptionRequested():
                return
            super().run()

    monkeypatch.setattr(edit_ui, "PaletteExtractionThread", SlowCancelableThread)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    thread = page._palette_thread
    assert thread is not None
    assert not page.can_close()
    page.cancel_palette_extraction()
    _wait_for_palette_thread_idle(qt_app, page)
    assert started == [True]
    assert page._palette_active_request is None
    assert page.can_close()
    assert page.palette_extract_button.isEnabled()
    qt_app.processEvents()
    assert not shiboken6.isValid(thread)
    page.close()


def test_export_worker_edit_processing_error_emits_message() -> None:
    from quick_processing_tool.edit_ui import EditExportWorker
    from quick_processing_tool.editing import EditSettings
    from quick_processing_tool.editing.models import EditOutputFormat
    from quick_processing_tool.editing.service import EditProcessingError

    class FailingService:
        def export(self, *args, **kwargs):
            raise EditProcessingError("boom")

    worker = EditExportWorker(
        FailingService(),
        Path("in.png"),
        Path("."),
        EditSettings(),
        EditOutputFormat.PNG,
        (255, 255, 255),
        95,
    )
    messages: list[str] = []
    worker.failed.connect(messages.append)
    worker.run()
    assert messages == ["boom"]



def test_text_auto_activation_and_clear_keep_section_visible(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    page = QuickEditPage()
    page.text_section.toggle.setChecked(True)
    page.show()
    qt_app.processEvents()
    assert not page.text_enabled.isVisible()
    assert page.text_details.isVisible()

    page.text_edit.setPlainText("なかよしこよし")
    qt_app.processEvents()
    page._commit_text_history()
    assert page.text_enabled.isChecked()
    assert page.settings().text.enabled

    page.clear_text()
    qt_app.processEvents()
    assert not page.text_enabled.isChecked()
    assert page.text_details.isVisible()
    assert not page.settings().text.enabled
    page.close()


def test_transparency_slider_spin_sync_and_sticker_navigation_does_not_auto_enable(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "opaque.png"
    Image.new("RGB", (12, 12), "white").save(source)
    page = QuickEditPage()
    page.load_image(source)
    page.material_section.toggle.setChecked(True)
    page.show()
    page.transparency_section.toggle.setChecked(False)
    page.transparency_enabled.setChecked(False)
    page.sticker_enabled.setChecked(True)
    qt_app.processEvents()

    page.tolerance_slider.setValue(42)
    qt_app.processEvents()
    assert page.tolerance_spin.value() == 42
    page.softness_spin.setValue(17)
    qt_app.processEvents()
    assert page.softness_slider.value() == 17

    page.open_transparency_settings()
    qt_app.processEvents()
    assert page.transparency_section.toggle.isChecked()
    assert not page.transparency_enabled.isChecked()
    assert page.sticker_prereq_button.isVisible()
    page.close()


def test_line_art_presets_have_visible_distinct_outputs_and_background_contract() -> None:
    image = Image.new("RGB", (72, 72), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 60, 60), outline="black", width=3)
    draw.ellipse((18, 18, 50, 44), outline="black", width=2)
    draw.line((0, 71, 71, 0), fill="black", width=2)

    densities = []
    render_bytes = {}
    for amount in LineArtAmount:
        result = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.WHITE),
        )
        densities.append(sum(1 for x in range(result.width) for y in range(result.height) if result.getpixel((x, y))[:3] != (255, 255, 255)))
        render_bytes[amount] = result.tobytes()

        transparent = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.TRANSPARENT),
        )
        assert transparent.size == image.size and transparent.mode == "RGBA"
        assert any(transparent.getpixel((x, y))[3] > 0 for x in range(transparent.width) for y in range(transparent.height))

        black = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.BLACK),
        )
        assert black.getpixel((0, 0))[3] == 255

        custom = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(
                enabled=True,
                amount=amount,
                background=LineArtBackground.CUSTOM,
                custom_background=(12, 34, 56, 255),
            ),
        )
        assert custom.getpixel((4, 4)) == (12, 34, 56, 255)

    assert densities[0] > 0
    assert densities[0] < densities[1] < densities[2] <= densities[3]
    assert len(set(render_bytes.values())) == len(LineArtAmount)


def test_palette_extract_only_keeps_preview_unquantized_until_toggle() -> None:
    from quick_processing_tool.editing import EditSettings

    image = Image.new("RGBA", (6, 1))
    image.putdata([
        (255, 0, 0, 255),
        (230, 10, 10, 255),
        (0, 255, 0, 255),
        (0, 230, 20, 255),
        (0, 0, 255, 255),
        (20, 20, 230, 255),
    ])
    mapping = extract_palette(image, 5)
    settings = EditSettings(palette=PaletteSettings(True, False, 5, mapping.palette, mapping.palette, mapping.indices, mapping.width, mapping.height, mapping.digest))
    untouched = render_edit(image, settings)
    assert untouched.tobytes() == image.tobytes()

    quantized = render_edit(image, __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(palette=PaletteSettings(True, True, 5, mapping.palette, mapping.palette, mapping.indices, mapping.width, mapping.height, mapping.digest)))
    assert quantized.tobytes() != image.tobytes()

def test_palette_mode_mapping_digest_remain_stable_and_preview_matches_render() -> None:
    from quick_processing_tool.editing import EditSettings

    image = Image.new("RGBA", (6, 1))
    image.putdata([
        (255, 0, 0, 255),
        (220, 10, 10, 255),
        (0, 255, 0, 255),
        (0, 220, 20, 255),
        (0, 0, 255, 255),
        (20, 20, 220, 255),
    ])
    mapping = extract_palette(image, 5)
    replacements = tuple((0, 255, 255) for _ in mapping.palette)
    for mode in (RecolorBlendMode.SHARP, RecolorBlendMode.SMOOTH, RecolorBlendMode.PRESERVE_SHADING):
        settings = EditSettings(
            palette=PaletteSettings(True, False, 5, mapping.palette, replacements, mapping.indices, mapping.width, mapping.height, mapping.digest, mode)
        )
        rendered = render_edit(image, settings)
        preview = render_preview(image, settings, max_dimension=2000)
        assert preview.tobytes() == rendered.tobytes()
        assert settings.palette.mapping == mapping.indices
        assert settings.palette.mapping_digest == mapping.digest


def test_palette_shading_quantize_contract_is_deterministic() -> None:
    from quick_processing_tool.editing import EditSettings

    image = Image.new("RGBA", (6, 1))
    image.putdata([
        (40, 40, 40, 255),
        (80, 80, 80, 255),
        (120, 120, 120, 255),
        (160, 160, 160, 255),
        (200, 200, 200, 255),
        (240, 240, 240, 255),
    ])
    mapping = mapping_for_palette(image, ((120, 120, 120),))
    off_settings = EditSettings(palette=PaletteSettings(True, False, 6, mapping.palette, ((0, 255, 255),), mapping.indices, mapping.width, mapping.height, mapping.digest, RecolorBlendMode.PRESERVE_SHADING))
    on_settings = EditSettings(palette=PaletteSettings(True, True, 6, mapping.palette, ((0, 255, 255),), mapping.indices, mapping.width, mapping.height, mapping.digest, RecolorBlendMode.PRESERVE_SHADING))
    off_render = render_edit(image, off_settings)
    on_render_a = render_edit(image, on_settings)
    on_render_b = render_edit(image, on_settings)
    assert on_render_a.tobytes() == on_render_b.tobytes()
    assert off_render.size == on_render_a.size
    assert _alpha_bytes(off_render) == _alpha_bytes(on_render_a)
    assert off_render.tobytes() != on_render_a.tobytes()


def test_smooth_blend_uses_mask_based_pillow_operations() -> None:
    source = inspect.getsource(apply_palette_mapping_smooth)
    assert "Image.composite" in source
    assert "ImageFilter.BoxBlur" in source
    assert "for y in range(1, height - 1)" not in source
    assert "average_neighbor" not in source


def test_nonsharp_preview_caps_large_dimension_for_ui_responsiveness() -> None:
    from quick_processing_tool.editing import EditSettings

    width, height = 1600, 800
    row = [(tone, min(255, tone + 40), 255 - tone, 255) for tone in (x % 255 for x in range(width))]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.putdata(row * height)
    mapping = extract_palette(image, 6)
    replacements = tuple(reversed(mapping.palette))
    settings = EditSettings(
        palette=PaletteSettings(True, False, 6, mapping.palette, replacements, mapping.indices, mapping.width, mapping.height, mapping.digest, RecolorBlendMode.SMOOTH)
    )
    preview = render_preview(image, settings, max_dimension=1400)
    smaller_preview = render_preview(image, settings, max_dimension=600)
    assert max(preview.size) <= 768
    assert max(smaller_preview.size) <= 600


def test_palette_rows_reset_and_handoff_use_recolored_values(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "palette-ui.png"
    image = Image.new("RGB", (2, 1))
    image.putdata([(255, 0, 0), (0, 255, 0)])
    image.save(source)

    page = QuickEditPage()
    page.show()
    page.load_image(source)
    mapping = extract_palette(Image.open(source), 6)
    token = (page._palette_generation, 99, page._palette_source_identity(source), (page.settings().filter_preset.value, page.settings().transparency, page.settings().palette.color_count))
    page._palette_active_request = token
    page._on_palette_extracted((*token, mapping))
    qt_app.processEvents()
    page.palette_blend_mode_combo.setCurrentIndex(page.palette_blend_mode_combo.findData(RecolorBlendMode.PRESERVE_SHADING.value))
    qt_app.processEvents()
    assert page.palette_current_label.text() == "現在の配色"
    assert page.palette_instruction_label.text() == "抽出した色をクリックして、好きな配色へ変更できます。"
    assert page.palette_blend_description_label.text() == "元画像の明るさを残して色を変えます"
    assert page.palette_chips_layout.count() >= len(mapping.palette) * 3
    assert page.palette_quantize_enabled.text() == "6色に整理"
    assert page.palette_send_button.isEnabled()
    assert page.palette_send_button.text() == "ドット絵へ送る"
    assert page.palette_send_button.toolTip() == "現在の配色をドット絵パレットへ送る"
    assert page.palette_open_button.isEnabled()

    page._palette_replacements = tuple(reversed(mapping.palette))
    page._rebuild_palette_chips()
    emitted = []
    page.palette_handoff_requested.connect(emitted.append)
    page.send_palette_to_pixel()
    assert emitted == [tuple(reversed(mapping.palette))]
    assert page.palette_feedback_label.text() == f"✓ {len(mapping.palette)}色のパレットをドット絵へ送りました"

    page.reset_palette()
    assert page._palette_replacements == mapping.palette
    assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
    page.close()


def test_main_window_extract_palette_does_not_switch_tabs(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.ui import MainWindow

    window = MainWindow()
    source = tmp_path / "extract-stays.png"
    Image.new("RGB", (4, 1), "red").save(source)
    window.navigation.setCurrentIndex(window.image_edit_tab)
    window.edit_page.load_image(source)
    mapping = extract_palette(Image.open(source), 6)
    token = (window.edit_page._palette_generation, 7, window.edit_page._palette_source_identity(source), (window.edit_page.settings().filter_preset.value, window.edit_page.settings().transparency, window.edit_page.settings().palette.color_count))
    window.edit_page._palette_active_request = token
    window.edit_page._on_palette_extracted((*token, mapping))
    qt_app.processEvents()
    assert window.navigation.currentIndex() == window.image_edit_tab
    _wait_for_edit_preview_idle(qt_app, window.edit_page)
    window.close()


def test_main_window_palette_handoff_preserves_pixel_source_without_switching_tabs(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.ui import MainWindow

    window = MainWindow()
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)
    window.pixel_page._load_reference_path(source)
    deadline = time.monotonic() + 3.0
    while window.pixel_page._import_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert window.pixel_page._import_thread is None
    window.pixel_page.output_folder = tmp_path
    window.pixel_page._update_save_ui()
    window.pixel_page.filename_edit.setText("keep_name")
    window.pixel_page.zoom_combo.setCurrentIndex(3)
    window.pixel_page.grid_check.setChecked(False)
    window.pixel_page.canvas.stroke((0, 0), (2, 0), (123, 45, 67, 255))
    snapshot = window.pixel_page.canvas.snapshot()
    history_index = window.pixel_page.canvas.history._index
    history_len = len(window.pixel_page.canvas.history._entries)
    reference = window.pixel_page.reference
    window.navigation.setCurrentIndex(window.image_edit_tab)

    colors = ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18))
    window._handoff_palette_to_pixel(colors)
    qt_app.processEvents()
    assert window.navigation.currentIndex() == window.image_edit_tab
    assert window.pixel_page._received_palette == colors
    assert window.pixel_page.palette_status_label.text() == "✓ 6色のパレットを受け取りました"
    assert window.pixel_page.source_path is None
    assert window.pixel_page.reference == reference
    assert window.pixel_page.filename_edit.text() == "keep_name"
    assert window.pixel_page.output_folder == tmp_path
    assert window.pixel_page.zoom_combo.currentIndex() == 3
    assert window.pixel_page.canvas_view.zoom_factor == 8
    assert not window.pixel_page.grid_check.isChecked()
    assert not window.pixel_page.canvas_view.grid_enabled
    assert window.pixel_page.canvas.snapshot() == snapshot
    assert window.pixel_page.canvas.history._index == history_index
    assert len(window.pixel_page.canvas.history._entries) == history_len
    assert window.pixel_page._palette_selected_index == -1

    window._open_pixel_tab_from_edit()
    qt_app.processEvents()
    assert window.navigation.currentIndex() == window.pixel_tab
    window.close()



def test_edit_filename_defaults_suffix_and_custom_retention(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    first = tmp_path / "cat_a1b2c3.png"
    second = tmp_path / "dog.webp"
    Image.new("RGB", (12, 12), "red").save(first)
    Image.new("RGB", (10, 10), "blue").save(second)

    page = QuickEditPage()
    page.load_image(first)
    qt_app.processEvents()
    assert page.filename_edit.text() == "cat_a1b2c3_edited"
    assert page.filename_suffix_label.text() == ".png"
    assert page.save_button.isEnabled()

    page.filename_edit.setText("こんにちは.jpeg.PNG")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "こんにちは"

    page.format_combo.setCurrentIndex(page.format_combo.findData("JPEG"))
    qt_app.processEvents()
    assert page.filename_suffix_label.text() == ".jpg"
    assert page.filename_edit.text() == "こんにちは"

    page.filter_combo.setCurrentIndex(page.filter_combo.findData("grayscale"))
    qt_app.processEvents()
    page.reset_edits()
    qt_app.processEvents()
    assert page.filename_edit.text() == "こんにちは"

    page.load_image(second)
    qt_app.processEvents()
    assert page.filename_edit.text() == "dog_edited"
    assert page.filename_suffix_label.text() == ".jpg"
    page.close()


def test_edit_filename_blank_invalid_reserved_and_planned_path(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "green").save(source)
    page = QuickEditPage()
    page.load_image(source)
    qt_app.processEvents()

    page.filename_edit.setText("bad<>:\"/\\|?*" + chr(0) + "name.png.PNG")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "bad__________name"
    assert page.save_button.isEnabled()
    assert page.planned_path_label.toolTip().endswith("bad__________name.png")

    page.filename_edit.setText("CON.webp")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "CON_"

    page.filename_edit.setText("")
    page._normalize_output_filename_input()
    qt_app.processEvents()
    assert page.filename_edit.text() == ""
    assert not page.save_button.isEnabled()
    assert "ファイル名" in page.save_hint_label.text()

    invalid_target = tmp_path / "not-a-folder.txt"
    invalid_target.write_text("x", encoding="utf-8")
    page.output_folder = invalid_target
    page._update_save_panel()
    page._update_actions()
    assert not page.save_button.isEnabled()
    assert "選び直してください" in page.save_hint_label.text()
    page.close()


def test_edit_service_custom_stem_duplicate_and_actual_result_filename(tmp_path: Path) -> None:
    from quick_processing_tool.editing import EditService, EditSettings
    from quick_processing_tool.editing.models import EditOutputFormat

    source = tmp_path / "source_hash_ab12cd.png"
    Image.new("RGBA", (6, 6), (255, 0, 0, 180)).save(source)
    service = EditService()

    result1 = service.export(
        source,
        tmp_path,
        EditSettings(),
        EditOutputFormat.SAME,
        (255, 255, 255),
        95,
        "  こんにちは.png.PNG  ",
    )
    result2 = service.export(
        source,
        tmp_path,
        EditSettings(),
        EditOutputFormat.SAME,
        (255, 255, 255),
        95,
        "  こんにちは.png.PNG  ",
    )

    assert result1.output_path.name == "こんにちは.png"
    assert result2.output_path.name == "こんにちは_2.png"
    assert result1.output_path.is_file() and result2.output_path.is_file()
