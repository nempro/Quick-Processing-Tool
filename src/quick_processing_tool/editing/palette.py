from __future__ import annotations

import hashlib
from dataclasses import dataclass

from PIL import Image


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
    index_by_color = {color: index for index, color in enumerate(palette)}
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


def quantize_image(image: Image.Image, color_count: int) -> tuple[Image.Image, PaletteMapping]:
    mapping = extract_palette(image, color_count)
    return apply_palette_mapping(image, mapping), mapping
