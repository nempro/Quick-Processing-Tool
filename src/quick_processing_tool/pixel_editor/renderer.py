from __future__ import annotations

from PIL import Image


def scale_nearest(image: Image.Image, zoom: int) -> Image.Image:
    """Return an integer nearest-neighbour display copy."""
    zoom = max(1, int(zoom))
    return image.resize((image.width * zoom, image.height * zoom), Image.Resampling.NEAREST)


def composite_reference(canvas: Image.Image, reference, visible: bool = True) -> Image.Image:
    """Composite a display-only reference below the editable canvas."""
    result = reference.image.copy().convert("RGBA") if visible and reference is not None else Image.new("RGBA", canvas.size)
    if result.size != canvas.size:
        result = result.resize(canvas.size, Image.Resampling.NEAREST)
    if visible and reference is not None:
        result.putalpha(result.getchannel("A").point(lambda a: a * int(reference.opacity) // 100))
    result.alpha_composite(canvas.convert("RGBA"))
    return result
