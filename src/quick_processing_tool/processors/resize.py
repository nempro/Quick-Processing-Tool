from __future__ import annotations

from PIL import Image

from ..models import ProcessingOptions, ResizeMode


def output_dimensions(width: int, height: int, options: ProcessingOptions) -> tuple[int, int]:
    mode = options.resize_mode
    if mode is ResizeMode.NONE:
        return width, height
    if mode is ResizeMode.PERCENTAGE:
        scale = max(options.percentage, 1.0) / 100.0
        return max(1, round(width * scale)), max(1, round(height * scale))
    if mode is ResizeMode.LONG_EDGE:
        target = max(1, options.long_edge)
        scale = target / max(width, height)
        return max(1, round(width * scale)), max(1, round(height * scale))

    wanted_w = max(1, options.width)
    wanted_h = max(1, options.height)
    if not options.keep_aspect:
        return wanted_w, wanted_h
    scale = min(wanted_w / width, wanted_h / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize_image(image: Image.Image, options: ProcessingOptions) -> Image.Image:
    size = output_dimensions(image.width, image.height, options)
    if size == image.size:
        return image.copy()
    return image.resize(size, Image.Resampling.LANCZOS)
