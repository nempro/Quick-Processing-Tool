from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from quick_processing_tool.naming import normalize_filename_stem

from quick_processing_tool.pixel_editor.canvas import (
    MAX_SIZE,
    MIN_SIZE,
    PRESETS,
    PixelCanvas,
    pixel_from_display,
)
from quick_processing_tool.pixel_editor.importers import import_as_pixels, load_reference
from quick_processing_tool.pixel_editor.models import ReferenceImage
from quick_processing_tool.pixel_editor.renderer import composite_reference, scale_nearest
from quick_processing_tool.pixel_editor.service import PixelExportError, save_png


def test_canvas_presets_bounds_and_transparent_default() -> None:
    assert PRESETS == (32, 64, 128)
    canvas = PixelCanvas()
    assert (canvas.width, canvas.height) == (128, 128)
    assert canvas.pixel(0, 0) == (0, 0, 0, 0)
    for size in (MIN_SIZE, MAX_SIZE):
        assert PixelCanvas(size, size).image.size == (size, size)
    with pytest.raises(ValueError):
        PixelCanvas(MIN_SIZE - 1, 32)
    with pytest.raises(ValueError):
        PixelCanvas(MAX_SIZE + 1, 32)


def test_click_diagonal_brush_eraser_and_transparent_eyedropper() -> None:
    canvas = PixelCanvas(16, 16)
    canvas.stroke((1, 1), (1, 1), (10, 20, 30, 255))
    canvas.stroke((2, 2), (7, 7), (10, 20, 30, 255))
    assert all(canvas.pixel(i, i) == (10, 20, 30, 255) for i in range(1, 8))
    canvas.stroke((8, 8), (8, 8), (1, 2, 3, 255), size=3)
    assert canvas.pixel(7, 7) == (1, 2, 3, 255)
    canvas.stroke((8, 8), (8, 8), (1, 2, 3, 255), erase=True)
    assert canvas.pixel(8, 8) == (0, 0, 0, 0)
    assert canvas.pixel(7, 7) == (1, 2, 3, 255)


def test_history_is_one_entry_per_operation_and_branch_invalidates_redo() -> None:
    canvas = PixelCanvas(8, 8)
    canvas.stroke((0, 0), (0, 0), (255, 0, 0, 255))
    assert canvas.history.can_undo
    canvas.undo()
    assert canvas.history.can_redo
    canvas.stroke((1, 1), (1, 1), (0, 255, 0, 255))
    assert not canvas.history.can_redo
    canvas.clear()
    canvas.undo()
    assert canvas.pixel(1, 1) == (0, 255, 0, 255)


def test_mapping_zoom_center_scroll_borders_and_dpi_logical_coordinates() -> None:
    for zoom in (1, 4, 8, 16):
        assert pixel_from_display(10, 20, origin_x=10, origin_y=20, zoom=zoom, width=32, height=32) == (0, 0)
        assert pixel_from_display(10 + zoom - 1, 20 + zoom - 1, origin_x=10, origin_y=20, zoom=zoom, width=32, height=32) == (0, 0)
        assert pixel_from_display(10 + 32 * zoom, 20, origin_x=10, origin_y=20, zoom=zoom, width=32, height=32) is None
    assert pixel_from_display(0, 0, origin_x=0, origin_y=0, zoom=4, scroll_x=12, scroll_y=8, width=16, height=16) == (3, 2)
    assert pixel_from_display(1.9, 1.9, origin_x=0, origin_y=0, zoom=1, width=2, height=2) == (1, 1)


def test_nearest_renderer_keeps_uniform_pixel_blocks() -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(255, 0, 0, 255), (0, 0, 255, 255)])
    scaled = scale_nearest(image, 4)
    assert scaled.size == (8, 4)
    assert all(scaled.getpixel((x, y)) == (255, 0, 0, 255) for x in range(4) for y in range(4))
    assert all(scaled.getpixel((x, y)) == (0, 0, 255, 255) for x in range(4, 8) for y in range(4))


def test_reference_fit_opacity_hide_and_excluded_from_export(tmp_path: Path) -> None:
    source = Image.new("RGBA", (8, 4), (30, 40, 50, 180))
    source_path = tmp_path / "reference.png"
    source.save(source_path)
    reference = load_reference(source_path, (16, 16), 50)
    assert reference.image.size == (16, 16)
    assert reference.opacity == 50
    with pytest.raises(ValueError):
        ReferenceImage(reference.image, 9)
    canvas = PixelCanvas(16, 16)
    composed = composite_reference(canvas.image, reference, True)
    assert composed.getpixel((0, 0))[3] == 0
    assert composed.getpixel((4, 8))[3] in (89, 90)
    output = save_png(canvas, tmp_path)
    with Image.open(output.output_path) as reopened:
        assert reopened.getpixel((4, 8))[3] == 0


def test_import_wide_tall_preserves_alpha_is_editable_and_one_undo(tmp_path: Path) -> None:
    wide = Image.new("RGBA", (40, 10), (200, 10, 20, 128))
    tall = Image.new("RGBA", (10, 40), (20, 30, 200, 64))
    wide_path, tall_path = tmp_path / "wide.png", tmp_path / "tall.png"
    wide.save(wide_path); tall.save(tall_path)
    canvas = PixelCanvas()
    original = wide_path.read_bytes()
    import_as_pixels(wide_path, canvas)
    assert canvas.history.can_undo
    assert canvas.pixel(64, 64)[3] > 0
    canvas.set_pixel(0, 0, (1, 2, 3, 255))
    assert canvas.pixel(0, 0) == (1, 2, 3, 255)
    canvas.undo()
    assert canvas.pixel(0, 0)[3] == 0
    import_as_pixels(tall_path, canvas)
    assert canvas.pixel(64, 64)[3] > 0
    assert wide_path.read_bytes() == original


@pytest.mark.parametrize("size", [(32, 32), (64, 64), (128, 128), (8, 512)])
def test_strict_png_save_dimensions_alpha_name_and_collision(tmp_path: Path, size: tuple[int, int]) -> None:
    canvas = PixelCanvas(*size)
    first = save_png(canvas, tmp_path)
    second = save_png(canvas, tmp_path)
    assert first.output_path.name == "pixel_art.png"
    assert second.output_path.name == "pixel_art_2.png"
    assert (first.width, first.height) == size
    with Image.open(first.output_path) as reopened:
        assert reopened.format == "PNG"
        assert reopened.size == size
        assert reopened.mode == "RGBA"


def test_source_stem_is_used_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "character.png"
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(source)
    original = source.read_bytes()
    result = save_png(PixelCanvas(32, 32), tmp_path, source)
    assert result.output_path.name == "character_pixel.png"
    assert source.read_bytes() == original


def test_custom_stem_normalizes_png_suffix_japanese_and_collision_without_double_extension(tmp_path: Path) -> None:
    canvas = PixelCanvas(32, 32)
    first = save_png(canvas, tmp_path, custom_stem="  キャラ.png.PNG  ")
    second = save_png(canvas, tmp_path, custom_stem="キャラ")
    assert first.output_path.name == "キャラ.png"
    assert second.output_path.name == "キャラ_2.png"
    with Image.open(first.output_path) as reopened:
        assert reopened.size == (32, 32)
        assert reopened.mode == "RGBA"


def test_custom_stem_rejects_blank_after_normalization(tmp_path: Path) -> None:
    with pytest.raises(PixelExportError, match="ファイル名"):
        save_png(PixelCanvas(32, 32), tmp_path, custom_stem="  .png  ")


def test_default_source_stem_normalization_helper_is_windows_safe() -> None:
    assert normalize_filename_stem("pixel_art", default="pixel_art") == "pixel_art"
    assert normalize_filename_stem("AUX", default="pixel_art") == "AUX_"
