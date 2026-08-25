from __future__ import annotations

from PIL import Image, ImageChops

from .models import TransparencySettings


def apply_color_transparency(
    image: Image.Image,
    settings: TransparencySettings,
) -> Image.Image:
    rgba = image.convert("RGBA")
    if not settings.enabled:
        return rgba.copy()
    rgb = rgba.convert("RGB")
    target = Image.new("RGB", rgba.size, settings.target_color)
    difference = ImageChops.difference(rgb, target)
    red, green, blue = difference.split()
    distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    tolerance = settings.tolerance
    softness = settings.edge_softness
    if softness == 0:
        lut = [0 if value <= tolerance else 255 for value in range(256)]
    else:
        limit = min(255, tolerance + softness)
        lut = []
        for value in range(256):
            if value <= tolerance:
                lut.append(0)
            elif value >= limit:
                lut.append(255)
            else:
                lut.append(round((value - tolerance) * 255 / max(1, limit - tolerance)))
    removal_mask = distance.point(lut)
    output_alpha = ImageChops.multiply(rgba.getchannel("A"), removal_mask)
    rgba.putalpha(output_alpha)
    return rgba
