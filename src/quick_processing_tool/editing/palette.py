from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter


RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class PaletteMapping:
    palette: tuple[RGB, ...]
    indices: tuple[int, ...]
    width: int
    height: int
    digest: str = ""


def rgba_digest(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    header = f"{rgba.width}x{rgba.height}:".encode("ascii")
    return hashlib.sha256(header + rgba.tobytes()).hexdigest()


def _distance(left: RGB, right: RGB) -> int:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _luminance_byte(rgb: RGB) -> int:
    red, green, blue = rgb
    return max(0, min(255, round(0.2126 * red + 0.7152 * green + 0.0722 * blue)))


def _clamp_channel(value: float) -> int:
    return max(0, min(255, round(value)))


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


_NONZERO_MASK_LUT = [0] + [255] * 255
_VALID_INDEX_MASK_LUT = [255] * 255 + [0]
_SMOOTH_BLEND_MASK_LUT = [0] * 255 + [112]


def _offset_with_fill(image: Image.Image, dx: int, dy: int, fill: int) -> Image.Image:
    shifted = ImageChops.offset(image, dx, dy)
    width, height = image.size
    if dx > 0:
        shifted.paste(fill, (0, 0, dx, height))
    elif dx < 0:
        shifted.paste(fill, (width + dx, 0, width, height))
    if dy > 0:
        shifted.paste(fill, (0, 0, width, dy))
    elif dy < 0:
        shifted.paste(fill, (0, height + dy, width, height))
    return shifted


def mapping_for_palette(image: Image.Image, palette: tuple[RGB, ...]) -> PaletteMapping:
    """Map pixels to a previously extracted palette without re-extracting it."""
    rgba = image.convert("RGBA")
    pixels = rgba.get_flattened_data()
    digest = rgba_digest(rgba)
    if not palette:
        return PaletteMapping((), tuple(-1 for _ in pixels), rgba.width, rgba.height, digest)
    indices: list[int] = []
    for red, green, blue, alpha in pixels:
        if alpha == 0:
            indices.append(-1)
        else:
            indices.append(min(range(len(palette)), key=lambda index: _distance((red, green, blue), palette[index])))
    return PaletteMapping(tuple(palette), tuple(indices), rgba.width, rgba.height, digest)


def extract_palette(image: Image.Image, color_count: int) -> PaletteMapping:
    """Extract dominant opaque colors and a stable per-pixel nearest-color map."""
    rgba = image.convert("RGBA")
    opaque = [pixel[:3] for pixel in rgba.get_flattened_data() if pixel[3] > 0]
    if not opaque:
        return PaletteMapping((), tuple(-1 for _ in rgba.get_flattened_data()), rgba.width, rgba.height, rgba_digest(rgba))
    sample = Image.new("RGB", (len(opaque), 1))
    sample.putdata(opaque)
    quantized = sample.quantize(colors=color_count, method=Image.Quantize.FASTOCTREE)
    counts = quantized.getcolors(maxcolors=len(opaque)) or []
    raw_palette = quantized.getpalette()
    ranked: list[tuple[int, RGB]] = []
    for count, index in counts:
        start = index * 3
        ranked.append((count, tuple(raw_palette[start:start + 3])))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    palette = tuple(color for _, color in ranked)
    indices: list[int] = []
    for pixel in rgba.get_flattened_data():
        if pixel[3] == 0:
            indices.append(-1)
            continue
        indices.append(min(range(len(palette)), key=lambda index: _distance(pixel[:3], palette[index])))
    return PaletteMapping(palette, tuple(indices), rgba.width, rgba.height, rgba_digest(rgba))


def apply_palette_mapping(
    image: Image.Image,
    mapping: PaletteMapping,
    replacements: tuple[RGB, ...] = (),
) -> Image.Image:
    rgba = image.convert("RGBA")
    if rgba.size != (mapping.width, mapping.height):
        raise ValueError("Palette mapping dimensions do not match image")
    colors = replacements if replacements else mapping.palette
    if len(colors) != len(mapping.palette):
        raise ValueError("Palette replacements must match extracted palette")
    output = []
    for pixel, index in zip(rgba.get_flattened_data(), mapping.indices):
        output.append(pixel if index < 0 else (*colors[index], pixel[3]))
    result = Image.new("RGBA", rgba.size)
    result.putdata(output)
    return result


def apply_palette_mapping_smooth(
    image: Image.Image,
    mapping: PaletteMapping,
    replacements: tuple[RGB, ...] = (),
) -> Image.Image:
    rgba = image.convert("RGBA")
    if rgba.size != (mapping.width, mapping.height):
        raise ValueError("Palette mapping dimensions do not match image")
    colors = replacements if replacements else mapping.palette
    if len(colors) != len(mapping.palette):
        raise ValueError("Palette replacements must match extracted palette")
    alpha = rgba.getchannel("A")
    sharp = apply_palette_mapping(rgba, mapping, colors)
    sharp_rgb = sharp.convert("RGB")
    valid_mask = alpha.point(_NONZERO_MASK_LUT, "L")
    if valid_mask.getbbox() is None:
        return sharp
    interior_mask = valid_mask.filter(ImageFilter.MinFilter(3))
    index_image = Image.frombytes(
        "L",
        rgba.size,
        bytes(index if index >= 0 else 255 for index in mapping.indices),
    )
    index_valid = index_image.point(_VALID_INDEX_MASK_LUT, "L")
    interior_mask = ImageChops.multiply(interior_mask, index_valid)
    if interior_mask.getbbox() is None:
        return sharp
    boundary = Image.new("L", rgba.size, 0)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted_index = _offset_with_fill(index_image, dx, dy, 255)
        shifted_valid = _offset_with_fill(index_valid, dx, dy, 0)
        different_indices = ImageChops.difference(index_image, shifted_index).point(_NONZERO_MASK_LUT, "L")
        boundary = ImageChops.lighter(
            boundary,
            ImageChops.multiply(different_indices, ImageChops.multiply(index_valid, shifted_valid)),
        )
    if boundary.getbbox() is None:
        return sharp
    boundary_band = boundary.filter(ImageFilter.MaxFilter(3))
    boundary_band = ImageChops.multiply(boundary_band, interior_mask)
    if boundary_band.getbbox() is None:
        return sharp
    blurred_rgb = sharp_rgb.filter(ImageFilter.BoxBlur(0.8))
    blend_mask = boundary_band.point(_SMOOTH_BLEND_MASK_LUT, "L")
    smooth_rgb = Image.composite(blurred_rgb, sharp_rgb, blend_mask)
    result = smooth_rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def _shading_lut(target: RGB, low: int, high: int, average: int) -> list[RGB]:
    hue, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255.0 for channel in target))
    dark_target = _clamp_unit(lightness - max(0.12, lightness * 0.45))
    bright_target = _clamp_unit(lightness + max(0.12, (1.0 - lightness) * 0.35))
    if lightness < 0.08:
        bright_target = max(bright_target, 0.18)
    if lightness > 0.92:
        dark_target = min(dark_target, 0.82)
    if average < low:
        average = low
    if average > high:
        average = high
    dark_span = max(1, average - low)
    bright_span = max(1, high - average)
    lut: list[RGB] = []
    for lum in range(256):
        if lum <= average:
            factor = 1.0 if average == low else (lum - low) / dark_span
            factor = _clamp_unit(factor)
            mapped_lightness = dark_target + (lightness - dark_target) * factor
        else:
            factor = 1.0 if high == average else (lum - average) / bright_span
            factor = _clamp_unit(factor)
            mapped_lightness = lightness + (bright_target - lightness) * factor
        red, green, blue = colorsys.hls_to_rgb(hue, _clamp_unit(mapped_lightness), saturation)
        lut.append((_clamp_channel(red * 255.0), _clamp_channel(green * 255.0), _clamp_channel(blue * 255.0)))
    return lut


def apply_palette_mapping_preserve_shading(
    image: Image.Image,
    mapping: PaletteMapping,
    replacements: tuple[RGB, ...] = (),
) -> Image.Image:
    rgba = image.convert("RGBA")
    if rgba.size != (mapping.width, mapping.height):
        raise ValueError("Palette mapping dimensions do not match image")
    colors = replacements if replacements else mapping.palette
    if len(colors) != len(mapping.palette):
        raise ValueError("Palette replacements must match extracted palette")
    pixels = list(rgba.get_flattened_data())
    if not colors:
        result = Image.new("RGBA", rgba.size)
        result.putdata(pixels)
        return result
    low_values = [255] * len(colors)
    high_values = [0] * len(colors)
    totals = [0] * len(colors)
    counts = [0] * len(colors)
    luminances = [0] * len(pixels)
    for pos, (pixel, index) in enumerate(zip(pixels, mapping.indices)):
        if index < 0 or pixel[3] == 0:
            continue
        lum = _luminance_byte(pixel[:3])
        luminances[pos] = lum
        low_values[index] = min(low_values[index], lum)
        high_values[index] = max(high_values[index], lum)
        totals[index] += lum
        counts[index] += 1
    luts: list[list[RGB]] = []
    for index, target in enumerate(colors):
        if counts[index] == 0:
            low = high = average = _luminance_byte(target)
        else:
            low = low_values[index]
            high = high_values[index]
            average = round(totals[index] / counts[index])
        luts.append(_shading_lut(target, low, high, average))
    output = []
    for pos, (pixel, index) in enumerate(zip(pixels, mapping.indices)):
        if index < 0:
            output.append(pixel)
            continue
        red, green, blue = luts[index][luminances[pos]]
        output.append((red, green, blue, pixel[3]))
    result = Image.new("RGBA", rgba.size)
    result.putdata(output)
    return result


def quantize_image(image: Image.Image, color_count: int) -> tuple[Image.Image, PaletteMapping]:
    mapping = extract_palette(image, color_count)
    return apply_palette_mapping(image, mapping), mapping
