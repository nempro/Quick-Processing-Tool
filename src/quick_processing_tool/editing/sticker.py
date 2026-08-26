from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter

from .models import StickerSettings


def _translated(mask: Image.Image, dx: int, dy: int) -> Image.Image:
    return mask.transform(mask.size, Image.Transform.AFFINE, (1, 0, -dx, 0, 1, -dy), Image.Resampling.BILINEAR)


def apply_sticker(image: Image.Image, settings: StickerSettings) -> Image.Image:
    """Add an outside-only alpha outline and optional fixed soft shadow."""
    source = image.convert("RGBA")
    if not settings.enabled:
        return source.copy()
    alpha = source.getchannel("A")
    if alpha.getextrema()[1] == 0 or alpha.getextrema()[0] == 255:
        raise ValueError("ステッカー化には、先に背景を透明にしてください。")
    size = settings.outline_width * 2 + 1
    expanded = alpha.filter(ImageFilter.MaxFilter(size))
    outline_alpha = ImageChops.subtract(expanded, alpha)
    result = Image.new("RGBA", source.size, (0, 0, 0, 0))
    if settings.shadow_enabled:
        shadow_alpha = _translated(expanded.filter(ImageFilter.GaussianBlur(max(1, settings.outline_width // 2))), 2, 3)
        shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.38))
        shadow = Image.new("RGBA", source.size, (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        result.alpha_composite(shadow)
    outline = Image.new("RGBA", source.size, settings.outline_color)
    outline.putalpha(outline_alpha)
    result.alpha_composite(outline)
    result.alpha_composite(source)
    return result
