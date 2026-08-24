from __future__ import annotations

from PIL import Image, ImageOps

from ..models import Transform


def normalize_orientation(image: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(image)


def apply_transforms(image: Image.Image, transforms: list[Transform]) -> Image.Image:
    result = image
    for transform in transforms:
        if transform is Transform.ROTATE_LEFT:
            result = result.transpose(Image.Transpose.ROTATE_90)
        elif transform is Transform.ROTATE_RIGHT:
            result = result.transpose(Image.Transpose.ROTATE_270)
        elif transform is Transform.ROTATE_180:
            result = result.transpose(Image.Transpose.ROTATE_180)
        elif transform is Transform.FLIP_HORIZONTAL:
            result = result.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif transform is Transform.FLIP_VERTICAL:
            result = result.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return result
