from __future__ import annotations

import io
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QTransform

from .editing.text import qimage_to_pil
from .naming import normalize_filename_stem, write_unique_bytes


MAX_CANVAS_DIMENSION = 16_384
MAX_CANVAS_PIXELS = 48_000_000


class TextDirection(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True)
class SoundEffectSettings:
    text: str = ""
    font_family: str = "Yu Gothic UI"
    font_size: int = 96
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    outline_enabled: bool = True
    outline_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    outline_width: int = 6
    shadow_enabled: bool = False
    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 160)
    shadow_distance: int = 12
    shadow_blur: int = 6
    direction: TextDirection = TextDirection.HORIZONTAL
    letter_spacing: int = 0
    line_spacing: int = 0
    rotation: int = 0
    scale_x: int = 100
    scale_y: int = 100
    padding: int = 24

    def __post_init__(self) -> None:
        ranges = {
            "font_size": (self.font_size, 8, 512),
            "outline_width": (self.outline_width, 0, 40),
            "shadow_distance": (self.shadow_distance, 0, 100),
            "shadow_blur": (self.shadow_blur, 0, 50),
            "letter_spacing": (self.letter_spacing, -20, 100),
            "line_spacing": (self.line_spacing, 0, 100),
            "rotation": (self.rotation, -180, 180),
            "scale_x": (self.scale_x, 50, 200),
            "scale_y": (self.scale_y, 50, 200),
            "padding": (self.padding, 0, 100),
        }
        for name, (value, minimum, maximum) in ranges.items():
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        for name in ("color", "outline_color", "shadow_color"):
            value = getattr(self, name)
            if len(value) != 4 or any(not 0 <= channel <= 255 for channel in value):
                raise ValueError(f"{name} must be an RGBA tuple")


def _font(settings: SoundEffectSettings) -> QFont:
    font = QFont(settings.font_family)
    font.setPixelSize(settings.font_size)
    return font


def _horizontal_path(settings: SoundEffectSettings, font: QFont) -> QPainterPath:
    metrics = QFontMetricsF(font)
    line_paths: list[tuple[QPainterPath, float]] = []
    widest = 0.0
    for line in settings.text.split("\n"):
        line_path = QPainterPath()
        x = 0.0
        for character in line:
            line_path.addText(QPointF(x, metrics.ascent()), font, character)
            x += max(1.0, metrics.horizontalAdvance(character) + settings.letter_spacing)
        width = max(x - settings.letter_spacing, line_path.boundingRect().right(), 0.0)
        widest = max(widest, width)
        line_paths.append((line_path, width))
    result = QPainterPath()
    step = metrics.lineSpacing() + settings.line_spacing
    for row, (line_path, width) in enumerate(line_paths):
        line_path.translate((widest - width) / 2.0, row * step)
        result.addPath(line_path)
    return result


def _vertical_path(settings: SoundEffectSettings, font: QFont) -> QPainterPath:
    """Render one input line per Japanese-style column, ordered right to left."""
    metrics = QFontMetricsF(font)
    columns = settings.text.split("\n")
    column_width = max(metrics.maxWidth(), metrics.height(), 1.0)
    column_step = column_width + settings.line_spacing
    character_step = max(1.0, metrics.height() + settings.letter_spacing)
    result = QPainterPath()
    for index, column in enumerate(columns):
        x = (len(columns) - index - 1) * column_step
        for row, character in enumerate(column):
            glyph = QPainterPath()
            glyph.addText(QPointF(0.0, metrics.ascent()), font, character)
            bounds = glyph.boundingRect()
            glyph.translate(x + (column_width - bounds.width()) / 2.0 - bounds.left(), row * character_step)
            result.addPath(glyph)
    return result


def _transformed_path(settings: SoundEffectSettings) -> QPainterPath:
    font = _font(settings)
    path = _vertical_path(settings, font) if settings.direction is TextDirection.VERTICAL else _horizontal_path(settings, font)
    if path.isEmpty():
        return path
    scale = QTransform()
    scale.scale(settings.scale_x / 100.0, settings.scale_y / 100.0)
    path = scale.map(path)
    rotation = QTransform()
    rotation.rotate(settings.rotation)
    return rotation.map(path)


def _paint_mask(path: QPainterPath, size: tuple[int, int], stroke: int) -> Image.Image:
    image = QImage(size[0], size[1], QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if stroke > 0:
            pen = QPen(QColor(255, 255, 255, 255), stroke * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
        painter.fillPath(path, QBrush(QColor(255, 255, 255, 255)))
    finally:
        painter.end()
    return qimage_to_pil(image).getchannel("A")


def _colored_layer(mask: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    alpha = mask.point(lambda value: round(value * color[3] / 255))
    layer = Image.new("RGBA", mask.size, color[:3] + (0,))
    layer.putalpha(alpha)
    return layer


def render_sound_effect(settings: SoundEffectSettings) -> Image.Image | None:
    """Render a tightly bounded, transparent RGBA sound-effect asset."""
    if not settings.text.strip():
        return None
    path = _transformed_path(settings)
    if path.isEmpty():
        return None
    stroke = settings.outline_width if settings.outline_enabled else 0
    base_bounds = path.boundingRect().adjusted(-stroke, -stroke, stroke, stroke)
    bounds = base_bounds
    shadow_dx = shadow_dy = 0.0
    if settings.shadow_enabled:
        shadow_dx = settings.shadow_distance / math.sqrt(2.0)
        shadow_dy = settings.shadow_distance / math.sqrt(2.0)
        blur_extent = settings.shadow_blur * 3
        bounds = bounds.united(base_bounds.translated(shadow_dx, shadow_dy).adjusted(-blur_extent, -blur_extent, blur_extent, blur_extent))
    width = max(1, math.ceil(bounds.width() + settings.padding * 2))
    height = max(1, math.ceil(bounds.height() + settings.padding * 2))
    if width > MAX_CANVAS_DIMENSION or height > MAX_CANVAS_DIMENSION or width * height > MAX_CANVAS_PIXELS:
        raise ValueError("文字量または効果が大きすぎます。文字サイズ・拡大率・影を小さくしてください。")
    translated = QPainterPath(path)
    translated.translate(settings.padding - bounds.left(), settings.padding - bounds.top())
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if settings.shadow_enabled:
        shadow_path = QPainterPath(translated)
        shadow_path.translate(shadow_dx, shadow_dy)
        shadow_mask = _paint_mask(shadow_path, result.size, stroke)
        if settings.shadow_blur:
            shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(settings.shadow_blur))
        result = Image.alpha_composite(result, _colored_layer(shadow_mask, settings.shadow_color))
    fill_mask = _paint_mask(translated, result.size, 0)
    if settings.outline_enabled and stroke:
        outline_mask = _paint_mask(translated, result.size, stroke)
        outline_ring = ImageChops.subtract(outline_mask, fill_mask)
        result = Image.alpha_composite(result, _colored_layer(outline_ring, settings.outline_color))
    result = Image.alpha_composite(result, _colored_layer(fill_mask, settings.color))
    return result


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGBA").save(output, format="PNG", optimize=True)
    return output.getvalue()


def save_sound_effect(image: Image.Image, folder: Path, filename: str) -> Path:
    stem = normalize_filename_stem(filename, default="sound_effect", strip_extensions=(".png",))
    return write_unique_bytes(Path(folder), stem, ".png", encode_png(image))
