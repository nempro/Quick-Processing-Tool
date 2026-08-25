from __future__ import annotations

from PIL import Image

from .models import CanvasBackground, CanvasSettings, PlacementMode


def output_dimensions(source_size: tuple[int, int], settings: CanvasSettings) -> tuple[int, int]:
    width = settings.width or source_size[0]
    height = settings.height or source_size[1]
    return max(1, width), max(1, height)


def _background_color(settings: CanvasSettings) -> tuple[int, int, int, int]:
    if settings.background is CanvasBackground.TRANSPARENT:
        return (0, 0, 0, 0)
    if settings.background is CanvasBackground.WHITE:
        return (255, 255, 255, 255)
    if settings.background is CanvasBackground.BLACK:
        return (0, 0, 0, 255)
    return settings.custom_color


def place_on_canvas(image: Image.Image, settings: CanvasSettings) -> Image.Image:
    source = image.convert("RGBA")
    target_width, target_height = output_dimensions(source.size, settings)
    padding_x = round(target_width * settings.padding_percent / 100)
    padding_y = round(target_height * settings.padding_percent / 100)
    inner_width = max(1, target_width - padding_x * 2)
    inner_height = max(1, target_height - padding_y * 2)
    scale_x = inner_width / source.width
    scale_y = inner_height / source.height
    scale = min(scale_x, scale_y) if settings.placement is PlacementMode.FIT else max(scale_x, scale_y)
    resized_size = (
        max(1, round(source.width * scale)),
        max(1, round(source.height * scale)),
    )
    resized = source.resize(resized_size, Image.Resampling.LANCZOS)
    if settings.placement is PlacementMode.FILL:
        left = max(0, (resized.width - inner_width) // 2)
        top = max(0, (resized.height - inner_height) // 2)
        resized = resized.crop((left, top, left + inner_width, top + inner_height))
    canvas = Image.new("RGBA", (target_width, target_height), _background_color(settings))
    x = (target_width - resized.width) // 2
    y = (target_height - resized.height) // 2
    canvas.alpha_composite(resized, (x, y))
    return canvas
