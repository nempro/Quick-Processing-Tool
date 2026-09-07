"""Non-destructive pixelation strokes for quick image editing."""

from __future__ import annotations

from dataclasses import replace

from PIL import Image, ImageDraw

from .models import MosaicSettings, MosaicStroke, MosaicTool


def scale_mosaic(settings: MosaicSettings, width: int, height: int) -> MosaicSettings:
    """Scale mosaic vectors and block sizes to a new final canvas."""
    if width <= 0 or height <= 0:
        raise ValueError("Mosaic canvas dimensions must be positive")
    if not settings.strokes:
        return replace(settings, base_width=width, base_height=height)
    if settings.base_width <= 0 or settings.base_height <= 0:
        return replace(settings, base_width=width, base_height=height)
    if settings.base_width == width and settings.base_height == height:
        return settings
    scale_x = width / settings.base_width
    scale_y = height / settings.base_height
    width_scale = min(scale_x, scale_y)
    strokes = tuple(
        replace(
            stroke,
            points=tuple(type(point)(point.x * scale_x, point.y * scale_y) for point in stroke.points),
            width=max(0.25, stroke.width * width_scale),
            block_size=max(1, round(stroke.block_size * width_scale)),
        )
        for stroke in settings.strokes
    )
    return replace(settings, strokes=strokes, base_width=width, base_height=height)


def _stroke_for_target(stroke: MosaicStroke, settings: MosaicSettings, target_size: tuple[int, int]) -> MosaicStroke:
    width, height = target_size
    base_width = settings.base_width or width
    base_height = settings.base_height or height
    scale_x = width / base_width
    scale_y = height / base_height
    width_scale = min(scale_x, scale_y)
    return replace(
        stroke,
        points=tuple(type(point)(point.x * scale_x, point.y * scale_y) for point in stroke.points),
        width=max(0.25, stroke.width * width_scale),
        block_size=max(1, round(stroke.block_size * width_scale)),
    )


def _mask_for_stroke(stroke: MosaicStroke, size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    points = [(point.x, point.y) for point in stroke.points]
    radius = max(0.5, stroke.width / 2)
    if len(points) == 1:
        x, y = points[0]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    else:
        draw.line(points, fill=255, width=max(1, round(stroke.width)), joint="curve")
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    return mask


def _pixelated(image: Image.Image, block_size: int) -> Image.Image:
    block_size = max(1, int(block_size))
    if block_size == 1:
        return image.copy()
    small = image.resize(
        (max(1, (image.width + block_size - 1) // block_size), max(1, (image.height + block_size - 1) // block_size)),
        Image.Resampling.BOX,
    )
    return small.resize(image.size, Image.Resampling.NEAREST)


def compose_mosaic(image: Image.Image, settings: MosaicSettings) -> Image.Image:
    result = image.convert("RGBA").copy()
    if not settings.visible or not settings.strokes:
        return result
    original = result.copy()
    for original_stroke in settings.strokes:
        stroke = _stroke_for_target(original_stroke, settings, result.size)
        mask = _mask_for_stroke(stroke, result.size)
        if stroke.tool is MosaicTool.ERASER:
            result = Image.composite(original, result, mask)
        else:
            result = Image.composite(_pixelated(original, stroke.block_size), result, mask)
    return result


def render_mosaic_overlay(settings: MosaicSettings, target_size: tuple[int, int] | None = None) -> Image.Image:
    """Render a transparent mask-like overlay for inspection/debugging."""
    if target_size is None:
        target_size = (settings.base_width, settings.base_height)
    width, height = target_size
    if width <= 0 or height <= 0:
        raise ValueError("Mosaic target dimensions must be positive")
    overlay = Image.new("RGBA", target_size, (0, 0, 0, 0))
    if not settings.visible:
        return overlay
    # Use a neutral translucent blue only for the UI overlay; final output uses compose_mosaic.
    for original_stroke in settings.strokes:
        stroke = _stroke_for_target(original_stroke, settings, target_size)
        if stroke.tool is MosaicTool.MOSAIC:
            mask = _mask_for_stroke(stroke, target_size)
            tint = Image.new("RGBA", target_size, (49, 95, 189, 72))
            overlay = Image.composite(tint, overlay, mask)
    return overlay
