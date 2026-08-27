from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .canvas import PixelCanvas
from .models import ReferenceImage

SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}


def load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        opened.load()
        if (opened.format or "").upper() not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError("PNG / JPEG / WebP画像を選んでください。")
        return ImageOps.exif_transpose(opened).convert("RGBA")


def load_reference(path: Path, canvas_size: tuple[int, int], opacity: int = 50) -> ReferenceImage:
    image = load_rgba(path)
    canvas = PixelCanvas(*canvas_size)
    fitted = image.copy()
    fitted.thumbnail(canvas_size, Image.Resampling.LANCZOS)
    target = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    target.alpha_composite(fitted, ((canvas.width - fitted.width) // 2, (canvas.height - fitted.height) // 2))
    return ReferenceImage(target, opacity)


def import_as_pixels(path: Path, canvas: PixelCanvas) -> None:
    canvas.import_image(load_rgba(path))
