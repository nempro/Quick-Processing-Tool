from __future__ import annotations

from dataclasses import replace
import os

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quick_processing_tool.editing import (
    CanvasBackground,
    CanvasSettings,
    EditSettings,
    EditOutputFormat,
    EditService,
    HandDrawSettings,
    HandPoint,
    HandStroke,
    HandTool,
    PaletteSettings,
    StickerSettings,
    TextSettings,
    compose_hand_draw,
    render_edit,
    render_preview,
    render_hand_overlay,
    scale_hand_draw,
)
from quick_processing_tool.editing import hand_draw as hand_draw_module


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _stroke(
    *points: tuple[float, float],
    tool: HandTool = HandTool.PEN,
    color: tuple[int, int, int, int] = (10, 30, 220, 128),
    width: float = 8,
) -> HandStroke:
    return HandStroke(
        tool=tool,
        points=tuple(HandPoint(x, y) for x, y in points),
        color=color,
        width=width,
    )


def test_click_is_round_antialiased_dot_with_rgba() -> None:
    settings = HandDrawSettings(
        strokes=(_stroke((10, 10)),),
        base_width=20,
        base_height=20,
    )
    overlay = render_hand_overlay(settings)
    center = overlay.getpixel((10, 10))
    edge_alphas = {
        overlay.getpixel((x, y))[3]
        for x in range(5, 16)
        for y in range(5, 16)
        if overlay.getpixel((x, y))[3]
    }
    assert all(abs(actual - expected) <= 1 for actual, expected in zip(center[:3], (10, 30, 220)))
    assert 120 <= center[3] <= 128
    assert any(0 < alpha < 128 for alpha in edge_alphas)


def test_pen_then_eraser_only_clears_hand_overlay() -> None:
    settings = HandDrawSettings(
        strokes=(
            _stroke((2, 10), (18, 10), color=(255, 0, 0, 255), width=6),
            _stroke((10, 6), (10, 14), tool=HandTool.ERASER, width=6),
        ),
        base_width=20,
        base_height=20,
    )
    source = Image.new("RGBA", (20, 20), (20, 80, 160, 255))
    overlay = render_hand_overlay(settings)
    composed = compose_hand_draw(source, settings)
    assert overlay.getpixel((10, 10))[3] == 0
    assert composed.getpixel((10, 10)) == source.getpixel((10, 10))
    assert composed.getpixel((4, 10))[:3] == (255, 0, 0)


def test_sparse_points_have_round_connected_joins_and_clip_at_bounds() -> None:
    settings = HandDrawSettings(
        strokes=(_stroke((0, 0), (50, 45), (99, 0), color=(20, 200, 40, 255), width=7),),
        base_width=100,
        base_height=50,
    )
    overlay = render_hand_overlay(settings)
    assert overlay.getpixel((0, 0))[3] > 0
    assert overlay.getpixel((25, 22))[3] > 0
    assert overlay.getpixel((50, 45))[3] > 0
    assert overlay.getpixel((75, 22))[3] > 0
    assert overlay.getpixel((99, 0))[3] > 0


def test_semitransparent_pen_and_eraser_keep_source_and_clear_overlay() -> None:
    settings = HandDrawSettings(
        strokes=(
            _stroke((2, 8), (18, 8), color=(255, 20, 20, 96), width=8),
            _stroke((10, 2), (10, 14), tool=HandTool.ERASER, color=(1, 2, 3, 7), width=5),
        ),
        base_width=20,
        base_height=16,
    )
    source = Image.new("RGBA", (20, 16), (10, 40, 90, 140))
    overlay = render_hand_overlay(settings)
    composed = compose_hand_draw(source, settings)
    assert overlay.getpixel((10, 8))[3] == 0
    assert composed.getpixel((10, 8)) == source.getpixel((10, 8))
    assert 0 < overlay.getpixel((3, 8))[3] <= 96


def test_hidden_layer_keeps_vectors_but_is_not_composited() -> None:
    visible = HandDrawSettings(
        strokes=(_stroke((3, 3), color=(255, 0, 0, 255)),),
        base_width=8,
        base_height=8,
    )
    hidden = replace(visible, visible=False)
    source = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    assert hidden.strokes == visible.strokes
    assert compose_hand_draw(source, hidden).getbbox() is None
    assert compose_hand_draw(source, visible).getbbox() is not None


def test_canvas_resize_scales_points_axes_and_width_by_min_ratio() -> None:
    original = HandDrawSettings(
        strokes=(_stroke((10, 20), (90, 80), width=10),),
        base_width=100,
        base_height=100,
    )
    scaled = scale_hand_draw(original, 200, 50)
    assert scaled.base_width == 200
    assert scaled.base_height == 50
    assert scaled.strokes[0].points == (HandPoint(20, 10), HandPoint(180, 40))
    assert scaled.strokes[0].width == 5


def test_preview_target_implicitly_scales_full_canvas_vectors() -> None:
    settings = HandDrawSettings(
        strokes=(_stroke((50, 50), color=(0, 80, 255, 255), width=20),),
        base_width=100,
        base_height=100,
    )
    preview = render_hand_overlay(settings, (20, 10))
    assert preview.size == (20, 10)
    assert preview.getpixel((10, 5))[2] > 200
    assert preview.getpixel((0, 0))[3] == 0


def test_small_preview_matches_final_renderer_and_downsample_keeps_position() -> None:
    source = Image.new("RGBA", (80, 40), (30, 60, 90, 255))
    settings = EditSettings(
        hand_draw=HandDrawSettings(
            strokes=(_stroke((60, 20), color=(250, 40, 150, 255), width=10),),
            base_width=80,
            base_height=40,
        )
    )
    final = render_edit(source, settings)
    assert render_preview(source, settings, 200).tobytes() == final.tobytes()
    preview = render_preview(source, settings, 20)
    assert preview.size == (20, 10)
    assert preview.getpixel((15, 5))[0] > 200


def test_25_percent_hand_alpha_matches_preview_and_png_export(tmp_path) -> None:
    source_path = tmp_path / "opacity-parity.png"
    source = Image.new("RGBA", (32, 24), (0, 0, 0, 0))
    source.save(source_path)
    settings = EditSettings(
        hand_draw=HandDrawSettings(
            strokes=(_stroke((16, 12), color=(220, 40, 90, 64), width=8),),
            base_width=32,
            base_height=24,
        )
    )
    final = render_edit(source, settings)
    preview = render_preview(source, settings, 1400)
    assert preview.tobytes() == final.tobytes()
    assert final.getpixel((16, 12))[3] == pytest.approx(64, abs=1)

    result = EditService().export(source_path, tmp_path, settings, EditOutputFormat.PNG)
    with Image.open(result.output_path) as reopened:
        reopened.load()
        assert reopened.convert("RGBA").getpixel((16, 12)) == final.getpixel((16, 12))


def test_hand_draw_is_topmost_after_text_and_canvas(app: QApplication) -> None:
    del app
    source = Image.new("RGBA", (256, 160), (255, 255, 255, 255))
    base_settings = EditSettings(
        canvas=CanvasSettings(width=256, height=160, background=CanvasBackground.TRANSPARENT),
        text=TextSettings(text="MMMM", font_size=36, color=(0, 0, 0, 255)),
    )
    without_hand = render_edit(source, base_settings)
    text_pixel = min(
        ((x, y) for y in range(without_hand.height) for x in range(without_hand.width)),
        key=lambda point: sum(without_hand.getpixel(point)[:3]),
    )
    hand = HandDrawSettings(
        strokes=(_stroke(text_pixel, color=(255, 0, 255, 255), width=8),),
        base_width=256,
        base_height=160,
    )
    settings = replace(base_settings, hand_draw=hand)
    rendered = render_edit(source, settings)
    assert sum(without_hand.getpixel(text_pixel)[:3]) < 300
    assert rendered.getpixel(text_pixel)[:3] == (255, 0, 255)


def test_recolor_sticker_text_and_hand_combination_keeps_hand_topmost(app: QApplication) -> None:
    del app
    source = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    for y in range(12, 52):
        for x in range(12, 52):
            source.putpixel((x, y), (255, 0, 0, 255))
    hand = HandDrawSettings(
        strokes=(_stroke((32, 32), color=(20, 240, 200, 255), width=12),),
        base_width=64,
        base_height=64,
    )
    settings = EditSettings(
        palette=PaletteSettings(
            enabled=True,
            palette=((255, 0, 0),),
            replacements=((30, 80, 230),),
        ),
        sticker=StickerSettings(enabled=True, outline_width=3),
        text=TextSettings(text="A", font_size=20),
        hand_draw=hand,
    )
    rendered = render_edit(source, settings)
    assert rendered.getpixel((32, 32))[:3] == (20, 240, 200)
    assert rendered.getpixel((20, 32))[:3] != (255, 0, 0)


@pytest.mark.parametrize(
    "output_format,expected_alpha",
    [(EditOutputFormat.PNG, True), (EditOutputFormat.WEBP, True), (EditOutputFormat.JPEG, False)],
)
def test_export_formats_reopen_with_committed_hand_layer(
    tmp_path, output_format: EditOutputFormat, expected_alpha: bool
) -> None:
    source_path = tmp_path / "transparent-source.png"
    Image.new("RGBA", (32, 24), (0, 0, 0, 0)).save(source_path)
    settings = EditSettings(
        hand_draw=HandDrawSettings(
            strokes=(_stroke((16, 12), color=(220, 40, 90, 180), width=8),),
            base_width=32,
            base_height=24,
        )
    )
    result = EditService().export(source_path, tmp_path, settings, output_format)
    with Image.open(result.output_path) as reopened:
        reopened.load()
        has_alpha = "A" in reopened.getbands()
        assert has_alpha is expected_alpha
        assert reopened.size == (32, 24)
        pixel = reopened.convert("RGBA").getpixel((16, 12))
        assert pixel[3] > 0


def test_large_canvas_domain_keeps_only_vectors_until_render() -> None:
    settings = HandDrawSettings(
        strokes=(_stroke((1, 1), (99999, 79999), width=100),),
        base_width=100000,
        base_height=80000,
    )
    assert len(settings.strokes) == 1
    assert len(settings.strokes[0].points) == 2
    assert not hasattr(settings, "image")


def test_native_antialias_buffer_never_allocates_supersampled_diagonal(monkeypatch) -> None:
    allocations: list[tuple[int, int]] = []
    original = hand_draw_module._create_overlay_image

    def tracked(width: int, height: int):
        allocations.append((width, height))
        return original(width, height)

    monkeypatch.setattr(hand_draw_module, "_create_overlay_image", tracked)
    settings = HandDrawSettings(
        strokes=(_stroke((0, 0), (9999, 63), width=12),),
        base_width=10000,
        base_height=64,
    )
    overlay = render_hand_overlay(settings)
    assert overlay.size == (10000, 64)
    assert allocations == [(10000, 64)]


def test_domain_normalizes_sequences_and_tools_and_rejects_invalid_values() -> None:
    stroke = HandStroke(tool="eraser", points=[HandPoint(1, 2)], color=[1, 2, 3, 4], width=3)
    settings = HandDrawSettings(strokes=[stroke], base_width=10, base_height=10)
    assert stroke.tool is HandTool.ERASER
    assert isinstance(stroke.points, tuple)
    assert isinstance(stroke.color, tuple)
    assert isinstance(settings.strokes, tuple)
    with pytest.raises(ValueError):
        HandStroke(points=(HandPoint(1, 2),), color=(1.0, 2, 3, 4))
    with pytest.raises(ValueError):
        HandDrawSettings(strokes=(stroke,))
    with pytest.raises(TypeError):
        HandStroke(points=((1, 2),))
