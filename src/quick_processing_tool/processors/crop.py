from __future__ import annotations

from PIL import Image


CropRect = tuple[float, float, float, float]


def normalize_crop_rect(rect: CropRect | None) -> CropRect | None:
    """Validate a normalized crop rectangle; None means the full image."""
    if rect is None:
        return None
    if len(rect) != 4:
        raise ValueError("crop rect must contain x, y, width, and height")
    x, y, width, height = (float(value) for value in rect)
    if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0):
        raise ValueError("crop rect must stay inside the image")
    if x + width > 1 or y + height > 1:
        raise ValueError("crop rect must stay inside the image")
    if x == 0 and y == 0 and width == 1 and height == 1:
        return None
    return (x, y, width, height)


def crop_box_for_image(size: tuple[int, int], rect: CropRect | None) -> tuple[int, int, int, int]:
    """Resolve normalized boundaries to one non-empty Pillow crop box."""
    width, height = size
    if width < 1 or height < 1:
        raise ValueError("image must have a positive size")
    normalized = normalize_crop_rect(rect)
    if normalized is None:
        return (0, 0, width, height)
    x, y, crop_width, crop_height = normalized
    left = min(width - 1, max(0, round(x * width)))
    top = min(height - 1, max(0, round(y * height)))
    right = min(width, max(left + 1, round((x + crop_width) * width)))
    bottom = min(height, max(top + 1, round((y + crop_height) * height)))
    return (left, top, right, bottom)


def crop_image(image: Image.Image, rect: CropRect | None) -> Image.Image:
    box = crop_box_for_image(image.size, rect)
    if box == (0, 0, image.width, image.height):
        return image
    return image.crop(box)
