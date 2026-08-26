from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .models import LineArtAmount, LineArtBackground, LineArtSettings


_THRESHOLDS = {LineArtAmount.LOW: 105, LineArtAmount.NORMAL: 65, LineArtAmount.HIGH: 28}
EDGE_METHOD = "sobel"


def _background(settings: LineArtSettings, size: tuple[int, int]) -> Image.Image:
    if settings.background is LineArtBackground.TRANSPARENT:
        color = (0, 0, 0, 0)
    elif settings.background is LineArtBackground.WHITE:
        color = (255, 255, 255, 255)
    elif settings.background is LineArtBackground.BLACK:
        color = (0, 0, 0, 255)
    else:
        color = settings.custom_background
    return Image.new("RGBA", size, color)


def _find_edges_candidate(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image.convert("RGB")).filter(ImageFilter.GaussianBlur(0.45))
    return gray.filter(ImageFilter.FIND_EDGES)


def _sobel_candidate(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image.convert("RGB")).filter(ImageFilter.GaussianBlur(0.45))
    horizontal = gray.filter(
        ImageFilter.Kernel(
            (3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=1, offset=128
        )
    )
    vertical = gray.filter(
        ImageFilter.Kernel(
            (3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=1, offset=128
        )
    )
    midpoint = Image.new("L", gray.size, 128)
    return ImageChops.lighter(
        ImageChops.difference(horizontal, midpoint),
        ImageChops.difference(vertical, midpoint),
    )


def edge_mask_candidate(image: Image.Image, amount: LineArtAmount, method: str) -> Image.Image:
    """Return one internal candidate; method is not exposed in normal UI."""
    if method == "find_edges":
        edges = _find_edges_candidate(image)
    elif method == "sobel":
        edges = _sobel_candidate(image)
    else:
        raise ValueError(f"Unknown line-art method: {method}")
    threshold = _THRESHOLDS[amount]
    return edges.point(lambda value: 255 if value >= threshold else 0)


def edge_mask(image: Image.Image, amount: LineArtAmount) -> Image.Image:
    """Use the selected Pillow-only candidate for the public line-art result."""
    return edge_mask_candidate(image, amount, EDGE_METHOD)


def compare_edge_candidates(
    image: Image.Image, amount: LineArtAmount = LineArtAmount.NORMAL
) -> dict[str, dict[str, float]]:
    """Return deterministic density metrics used by QA to choose the internal default."""
    metrics: dict[str, dict[str, float]] = {}
    for method in ("find_edges", "sobel"):
        mask = edge_mask_candidate(image, amount, method)
        pixels = list(mask.get_flattened_data())
        density = sum(1 for value in pixels if value > 0) / max(1, len(pixels))
        metrics[method] = {"edge_density": density}
    return metrics


def apply_line_art(image: Image.Image, settings: LineArtSettings) -> Image.Image:
    source = image.convert("RGBA")
    if not settings.enabled:
        return source.copy()
    mask = edge_mask(source, settings.amount)
    result = _background(settings, source.size)
    lines = Image.new("RGBA", source.size, settings.line_color)
    lines.putalpha(mask.point(lambda value: value * settings.line_color[3] // 255))
    result.alpha_composite(lines)
    return result
