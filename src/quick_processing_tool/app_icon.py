from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError


REQUIRED_ICON_SIZES = {
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
}


def validate_icon(path: Path) -> set[tuple[int, int]]:
    if path.suffix.lower() != ".ico":
        raise ValueError("The formal Windows app icon must be an .ico file")
    try:
        with Image.open(path) as image:
            if image.format != "ICO" or not hasattr(image, "ico"):
                raise ValueError("The formal Windows app icon is not a valid ICO file")
            sizes = set(image.ico.sizes())
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"The formal Windows app icon cannot be read: {exc}") from exc
    missing = REQUIRED_ICON_SIZES - sizes
    if missing:
        labels = ", ".join(f"{width}x{height}" for width, height in sorted(missing))
        raise ValueError(f"The formal Windows app icon is missing sizes: {labels}")
    return sizes
