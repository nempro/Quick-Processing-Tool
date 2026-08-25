from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageOps

from .canvas import output_dimensions, place_on_canvas
from .filters import apply_filter
from .models import EditSettings
from .text import draw_text
from .transparency import apply_color_transparency


def load_normalized(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        opened.load()
        return ImageOps.exif_transpose(opened).convert("RGBA")


def render_edit(source: Image.Image, settings: EditSettings) -> Image.Image:
    filtered = apply_filter(source, settings.filter_preset)
    transparent = apply_color_transparency(filtered, settings.transparency)
    composed = place_on_canvas(transparent, settings.canvas)
    return draw_text(composed, settings.text)


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
        preview_settings = replace(settings, canvas=canvas, text=text)
    elif source_scale < 1.0 and settings.canvas.width == 0 and settings.canvas.height == 0:
        text = replace(
            settings.text,
            font_size=max(8, round(settings.text.font_size * source_scale)),
            outline_width=max(0, round(settings.text.outline_width * source_scale)),
            safe_margin=max(0, round(settings.text.safe_margin * source_scale)),
        )
        preview_settings = replace(settings, text=text)
    return render_edit(working_source, preview_settings)


def render_path(path: Path, settings: EditSettings) -> Image.Image:
    return render_edit(load_normalized(path), settings)


def render_path_preview(
    path: Path,
    settings: EditSettings,
    max_dimension: int = 1400,
) -> Image.Image:
    return render_preview(load_normalized(path), settings, max_dimension)
