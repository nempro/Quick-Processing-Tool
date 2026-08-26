from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .history import PixelHistory

RGBA = tuple[int, int, int, int]
PRESETS = (32, 64, 128)
MIN_SIZE = 8
MAX_SIZE = 512


def validate_size(width: int, height: int) -> tuple[int, int]:
    if not MIN_SIZE <= width <= MAX_SIZE or not MIN_SIZE <= height <= MAX_SIZE:
        raise ValueError(f"Canvas size must be between {MIN_SIZE} and {MAX_SIZE}")
    return int(width), int(height)


def pixel_from_display(
    display_x: float,
    display_y: float,
    *,
    origin_x: int,
    origin_y: int,
    zoom: int,
    scroll_x: int = 0,
    scroll_y: int = 0,
    width: int,
    height: int,
) -> tuple[int, int] | None:
    """Map logical Qt viewport coordinates to one integer canvas pixel."""
    if zoom < 1:
        raise ValueError("Zoom must be positive")
    x = int((display_x + scroll_x - origin_x) // zoom)
    y = int((display_y + scroll_y - origin_y) // zoom)
    return (x, y) if 0 <= x < width and 0 <= y < height else None


def _bresenham(start: tuple[int, int], end: tuple[int, int]):
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


class PixelCanvas:
    def __init__(self, width: int = 128, height: int = 128) -> None:
        self.width, self.height = validate_size(width, height)
        self._image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        self.history = PixelHistory(self.snapshot())

    @property
    def image(self) -> Image.Image:
        return self._image.copy()

    def snapshot(self) -> bytes:
        return self._image.tobytes()

    def restore(self, snapshot: bytes) -> None:
        expected = self.width * self.height * 4
        if len(snapshot) != expected:
            raise ValueError("Invalid pixel snapshot")
        self._image = Image.frombytes("RGBA", (self.width, self.height), snapshot)

    def pixel(self, x: int, y: int) -> RGBA:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError("Pixel outside canvas")
        return self._image.getpixel((x, y))

    def set_pixel(self, x: int, y: int, color: RGBA) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._image.putpixel((x, y), tuple(max(0, min(255, int(v))) for v in color))

    def stroke(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        color: RGBA,
        *,
        size: int = 1,
        erase: bool = False,
        commit: bool = True,
    ) -> bool:
        if size not in (1, 2, 3):
            raise ValueError("Pencil size must be 1, 2, or 3")
        before = self.snapshot()
        for x, y in _bresenham(start, end):
            radius = size // 2
            for yy in range(y - radius, y - radius + size):
                for xx in range(x - radius, x - radius + size):
                    self.set_pixel(xx, yy, (0, 0, 0, 0) if erase else color)
        changed = before != self.snapshot()
        if changed and commit:
            self.history.commit(self.snapshot())
        return changed

    def clear(self, *, commit: bool = True) -> bool:
        before = self.snapshot()
        self._image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        changed = before != self.snapshot()
        if changed and commit:
            self.history.commit(self.snapshot())
        return changed

    def import_image(self, source: Image.Image, *, commit: bool = True) -> None:
        rgba = source.convert("RGBA")
        fitted = ImageOps.contain(rgba, (self.width, self.height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        canvas.alpha_composite(fitted, ((self.width - fitted.width) // 2, (self.height - fitted.height) // 2))
        self._image = canvas
        if commit:
            self.history.commit(self.snapshot())

    def undo(self) -> None:
        self.restore(self.history.undo())

    def redo(self) -> None:
        self.restore(self.history.redo())
