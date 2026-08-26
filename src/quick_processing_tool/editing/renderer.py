from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageOps

from .canvas import output_dimensions, place_on_canvas
from .line_art import apply_line_art
from .palette import PaletteMapping, apply_palette_mapping, extract_palette, mapping_for_palette, rgba_digest
from .sticker import apply_sticker
from .filters import apply_filter
from .models import EditSettings
from .text import draw_text
from .transparency import apply_color_transparency


def load_normalized(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        opened.load()
        return ImageOps.exif_transpose(opened).convert("RGBA")


def prepare_palette_source(source: Image.Image, settings: EditSettings) -> Image.Image:
    filtered = apply_filter(source, settings.filter_preset)
    return apply_color_transparency(filtered, settings.transparency)

def extract_palette_for_settings(source: Image.Image, settings: EditSettings):
    from .palette import extract_palette
    return extract_palette(prepare_palette_source(source, settings), settings.palette.color_count)

def _apply_palette_stage(image: Image.Image, settings: EditSettings) -> Image.Image:
    palette_settings = settings.palette
    if not palette_settings.enabled:
        return image.convert("RGBA").copy()
    current_digest = rgba_digest(image)
    if (
        palette_settings.mapping
        and palette_settings.mapping_width == image.width
        and palette_settings.mapping_height == image.height
        and len(palette_settings.mapping) == image.width * image.height
        and palette_settings.mapping_digest == current_digest
    ):
        mapping = PaletteMapping(palette_settings.palette, palette_settings.mapping, image.width, image.height, current_digest)
    elif palette_settings.palette:
        mapping = mapping_for_palette(image, palette_settings.palette)
    else:
        mapping = extract_palette(image, palette_settings.color_count)
    replacements = palette_settings.replacements or mapping.palette
    if not palette_settings.quantize_enabled and replacements == mapping.palette:
        return image.convert("RGBA").copy()
    return apply_palette_mapping(image, mapping, replacements)

def render_edit(source: Image.Image, settings: EditSettings) -> Image.Image:
    filtered = apply_filter(source, settings.filter_preset)
    transparent = apply_color_transparency(filtered, settings.transparency)
    paletted = _apply_palette_stage(transparent, settings)
    line_art = apply_line_art(paletted, settings.line_art)
    composed = place_on_canvas(line_art, settings.canvas)
    sticker = apply_sticker(composed, settings.sticker)
    return draw_text(sticker, settings.text)


def render_preview(
    source: Image.Image,
    settings: EditSettings,
    max_dimension: int = 1400,
) -> Image.Image:
    source = source.convert("RGBA")
    target_width, target_height = output_dimensions(source.size, settings.canvas)
    scale = min(1.0, max_dimension / max(target_width, target_height))
    source_scale = min(1.0, max_dimension / max(source.size))
    working_source = source
    if source_scale < 1.0:
        working_source = source.resize(
            (
                max(1, round(source.width * source_scale)),
                max(1, round(source.height * source_scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    preview_settings = settings
    if scale < 1.0:
        canvas = replace(
            settings.canvas,
            width=max(1, round(target_width * scale)),
            height=max(1, round(target_height * scale)),
        )
        text = replace(
            settings.text,
            font_size=max(8, round(settings.text.font_size * scale)),
            outline_width=max(0, round(settings.text.outline_width * scale)),
            safe_margin=max(0, round(settings.text.safe_margin * scale)),
        )
        preview_settings = replace(settings, canvas=canvas, text=text, sticker=replace(settings.sticker, outline_width=max(1, round(settings.sticker.outline_width * scale))), palette=replace(settings.palette, mapping=(), mapping_width=0, mapping_height=0))
    elif source_scale < 1.0 and settings.canvas.width == 0 and settings.canvas.height == 0:
        text = replace(
            settings.text,
            font_size=max(8, round(settings.text.font_size * source_scale)),
            outline_width=max(0, round(settings.text.outline_width * source_scale)),
            safe_margin=max(0, round(settings.text.safe_margin * source_scale)),
        )
        preview_settings = replace(settings, text=text, palette=replace(settings.palette, mapping=(), mapping_width=0, mapping_height=0))
    return render_edit(working_source, preview_settings)


def render_path(path: Path, settings: EditSettings) -> Image.Image:
    return render_edit(load_normalized(path), settings)


def render_path_preview(
    path: Path,
    settings: EditSettings,
    max_dimension: int = 1400,
) -> Image.Image:
    return render_preview(load_normalized(path), settings, max_dimension)
