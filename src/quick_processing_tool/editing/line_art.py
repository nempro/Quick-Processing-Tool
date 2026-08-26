from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from .models import LineArtAmount, LineArtBackground, LineArtSettings


EDGE_METHOD = "sobel"


@dataclass(frozen=True, slots=True)
class _LineArtProfile:
    blur: float
    threshold: int
    contrast: float
    dilate: int = 0
    erode: int = 0


_PROFILES = {
    LineArtAmount.CLEAN: _LineArtProfile(0.80, 128, 1.00, 0, 0),
    LineArtAmount.STANDARD: _LineArtProfile(0.48, 92, 1.32, 0, 0),
    LineArtAmount.DETAILED: _LineArtProfile(0.26, 72, 1.52, 1, 0),
    LineArtAmount.COMIC: _LineArtProfile(0.32, 82, 1.82, 2, 0),
}


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


def _find_edges_candidate(image: Image.Image, blur: float) -> Image.Image:
    gray = ImageOps.grayscale(image.convert("RGB")).filter(ImageFilter.GaussianBlur(blur))
    return gray.filter(ImageFilter.FIND_EDGES)


def _sobel_candidate(image: Image.Image, blur: float) -> Image.Image:
    gray = ImageOps.grayscale(image.convert("RGB")).filter(ImageFilter.GaussianBlur(blur))
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


def _profile(amount: LineArtAmount) -> _LineArtProfile:
    return _PROFILES[LineArtAmount(amount)]


def _mask_from_candidate(candidate: Image.Image, amount: LineArtAmount) -> Image.Image:
    profile = _profile(amount)
    enhanced = ImageEnhance.Contrast(candidate).enhance(profile.contrast)
    mask = enhanced.point(lambda value: 255 if value >= profile.threshold else 0)
    for _ in range(profile.dilate):
        mask = mask.filter(ImageFilter.MaxFilter(3))
    for _ in range(profile.erode):
        mask = mask.filter(ImageFilter.MinFilter(3))
    return mask


def edge_mask_candidate(image: Image.Image, amount: LineArtAmount, method: str) -> Image.Image:
    """Return one internal candidate; method is not exposed in normal UI."""
    profile = _profile(amount)
    if method == "find_edges":
        candidate = _find_edges_candidate(image, profile.blur)
    elif method == "sobel":
        candidate = _sobel_candidate(image, profile.blur)
    else:
        raise ValueError(f"Unknown line-art method: {method}")
    return _mask_from_candidate(candidate, amount)


def edge_mask(image: Image.Image, amount: LineArtAmount) -> Image.Image:
    """Use the selected Pillow-only candidate for the public line-art result."""
    return edge_mask_candidate(image, amount, EDGE_METHOD)


def compare_edge_candidates(
    image: Image.Image, amount: LineArtAmount = LineArtAmount.STANDARD
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
