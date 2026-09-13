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


def apply_color_adjustments(image: Image.Image, adjustments) -> Image.Image:
    """Apply compact color controls while preserving the original alpha channel."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    if adjustments is None:
        return rgba.copy()
    if adjustments.brightness:
        rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, 1.0 + adjustments.brightness / 100.0))
    if adjustments.contrast:
        rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, 1.0 + adjustments.contrast / 100.0))
    if adjustments.saturation:
        rgb = ImageEnhance.Color(rgb).enhance(max(0.0, 1.0 + adjustments.saturation / 100.0))
    if adjustments.temperature or adjustments.tint:
        red, green, blue = rgb.split()
        warm = adjustments.temperature / 100.0
        tint = adjustments.tint / 100.0
        red_factor = 1.0 + 0.20 * warm + 0.10 * tint
        green_factor = 1.0 - 0.05 * abs(warm) - 0.12 * tint
        blue_factor = 1.0 - 0.20 * warm - 0.10 * tint
        rgb = Image.merge("RGB", (
            _channel_scale(red, max(0.0, red_factor)),
            _channel_scale(green, max(0.0, green_factor)),
            _channel_scale(blue, max(0.0, blue_factor)),
        ))
    if adjustments.hue:
        hsv = rgb.convert("HSV")
        shift = round(adjustments.hue * 255 / 360)
        hue, sat, value = hsv.split()
        hue = hue.point(lambda channel: (channel + shift) % 256)
        hsv = Image.merge("HSV", (hue, sat, value))
        rgb = hsv.convert("RGB")
    if adjustments.fade:
        amount = max(0.0, min(1.0, adjustments.fade / 100.0))
        rgb = Image.blend(rgb, Image.new("RGB", rgb.size, (255, 255, 255)), amount * 0.45)
    return _with_alpha(rgb, alpha)