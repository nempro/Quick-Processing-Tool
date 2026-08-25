from __future__ import annotations

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .models import FilterPreset


def _with_alpha(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    output = rgb.convert("RGBA")
    output.putalpha(alpha)
    return output


def _channel_scale(channel: Image.Image, factor: float) -> Image.Image:
    return channel.point(lambda value: max(0, min(255, round(value * factor))))


def apply_filter(image: Image.Image, preset: FilterPreset) -> Image.Image:
    rgba = image.convert("RGBA")
    if preset is FilterPreset.NONE:
        return rgba.copy()
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    if preset is FilterPreset.GRAYSCALE:
        return _with_alpha(ImageOps.grayscale(rgb).convert("RGB"), alpha)
    if preset is FilterPreset.SEPIA:
        gray = ImageOps.grayscale(rgb)
        sepia = ImageOps.colorize(gray, "#2E190D", "#F1D7A2")
        return _with_alpha(sepia, alpha)
    if preset is FilterPreset.BRIGHT:
        return _with_alpha(ImageEnhance.Brightness(rgb).enhance(1.18), alpha)
    if preset is FilterPreset.DARK:
        return _with_alpha(ImageEnhance.Brightness(rgb).enhance(0.76), alpha)
    if preset in (FilterPreset.WARM, FilterPreset.COOL):
        red, green, blue = rgb.split()
        if preset is FilterPreset.WARM:
            adjusted = Image.merge(
                "RGB",
                (_channel_scale(red, 1.10), _channel_scale(green, 1.02), _channel_scale(blue, 0.90)),
            )
        else:
            adjusted = Image.merge(
                "RGB",
                (_channel_scale(red, 0.90), _channel_scale(green, 1.02), _channel_scale(blue, 1.12)),
            )
        return _with_alpha(adjusted, alpha)
    if preset is FilterPreset.SHARP:
        adjusted = rgb.filter(ImageFilter.UnsharpMask(radius=1.5, percent=145, threshold=3))
        return _with_alpha(adjusted, alpha)
    if preset is FilterPreset.SOFT:
        return _with_alpha(rgb.filter(ImageFilter.GaussianBlur(radius=1.2)), alpha)
    if preset is FilterPreset.FADED:
        adjusted = ImageEnhance.Color(rgb).enhance(0.58)
        adjusted = ImageEnhance.Contrast(adjusted).enhance(0.88)
        adjusted = ImageEnhance.Brightness(adjusted).enhance(1.07)
        return _with_alpha(adjusted, alpha)
    raise ValueError(f"Unknown filter preset: {preset}")
