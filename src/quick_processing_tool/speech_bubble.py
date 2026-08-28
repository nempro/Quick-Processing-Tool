from __future__ import annotations

import io
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF

from .editing.text import qimage_to_pil
from .naming import normalize_filename_stem, write_unique_bytes


MAX_CANVAS_DIMENSION = 16_384
MAX_CANVAS_PIXELS = 48_000_000


class BubbleShape(str, Enum):
    ELLIPSE = "ellipse"
    ROUNDED = "rounded"
    BURST = "burst"


class TailPreset(str, Enum):
    LEFT_TOP = "left_top"
    RIGHT_TOP = "right_top"
    LEFT_BOTTOM = "left_bottom"
    RIGHT_BOTTOM = "right_bottom"


TAIL_TIPS = {
    TailPreset.LEFT_TOP: (-0.72, -1.25),
    TailPreset.RIGHT_TOP: (0.72, -1.25),
    TailPreset.LEFT_BOTTOM: (-0.72, 1.25),
    TailPreset.RIGHT_BOTTOM: (0.72, 1.25),
}


@dataclass(frozen=True)
class SpeechBubbleSettings:
    text: str = ""
    font_family: str = "Yu Gothic UI"
    font_size: int = 48
    text_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    bubble_enabled: bool = True
    shape: BubbleShape = BubbleShape.ELLIPSE
    fill_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    stroke_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    stroke_width: int = 4
    padding: int = 24
    tail_enabled: bool = True
    tail_preset: TailPreset = TailPreset.RIGHT_BOTTOM
    tail_tip: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not 8 <= self.font_size <= 256:
            raise ValueError("font_size must be between 8 and 256")
        if not 1 <= self.stroke_width <= 20:
            raise ValueError("stroke_width must be between 1 and 20")
        if not 4 <= self.padding <= 100:
            raise ValueError("padding must be between 4 and 100")
        for name in ("text_color", "fill_color", "stroke_color"):
            value = getattr(self, name)
            if len(value) != 4 or any(not 0 <= channel <= 255 for channel in value):
                raise ValueError(f"{name} must be an RGBA tuple")
        if self.tail_tip is not None and any(not -3.0 <= value <= 3.0 for value in self.tail_tip):
            raise ValueError("tail_tip must stay inside the safe preview range")


@dataclass(frozen=True)
class BubbleGeometry:
    body_rect: tuple[float, float, float, float]
    body_center: tuple[float, float]
    tail_tip: tuple[float, float] | None


@dataclass(frozen=True)
class BubbleRenderResult:
    image: Image.Image
    geometry: BubbleGeometry


def _font(settings: SpeechBubbleSettings) -> QFont:
    font = QFont(settings.font_family)
    font.setPixelSize(settings.font_size)
    return font


def _text_path(settings: SpeechBubbleSettings) -> tuple[QPainterPath, QRectF]:
    font = _font(settings)
    metrics = QFontMetricsF(font)
    lines = settings.text.split("\n")
    widths = [metrics.horizontalAdvance(line or " ") for line in lines]
    widest = max(widths, default=1.0)
    path = QPainterPath()
    for row, (line, width) in enumerate(zip(lines, widths)):
        path.addText(QPointF((widest - width) / 2.0, metrics.ascent() + row * metrics.lineSpacing()), font, line)
    bounds = path.boundingRect()
    if bounds.isEmpty():
        bounds = QRectF(0, 0, widest, max(metrics.height(), 1.0))
    return path, bounds


def _burst_path(rect: QRectF) -> QPainterPath:
    center = rect.center()
    points: list[QPointF] = []
    spikes = 18
    for index in range(spikes * 2):
        angle = -math.pi / 2 + index * math.pi / spikes
        radius = 1.0 if index % 2 == 0 else 0.78
        points.append(QPointF(center.x() + math.cos(angle) * rect.width() / 2 * radius, center.y() + math.sin(angle) * rect.height() / 2 * radius))
    path = QPainterPath()
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


def _body_path(shape: BubbleShape, rect: QRectF) -> QPainterPath:
    path = QPainterPath()
    if shape is BubbleShape.ELLIPSE:
        path.addEllipse(rect)
    elif shape is BubbleShape.ROUNDED:
        radius = min(36.0, rect.height() * 0.28)
        path.addRoundedRect(rect, radius, radius)
    else:
        path = _burst_path(rect)
    return path


def _tail_points(rect: QRectF, preset: TailPreset, tip: QPointF) -> tuple[QPointF, QPointF, QPointF]:
    if preset in {TailPreset.LEFT_BOTTOM, TailPreset.RIGHT_BOTTOM}:
        y = rect.bottom() - rect.height() * 0.16
        if preset is TailPreset.LEFT_BOTTOM:
            a, b = rect.left() + rect.width() * 0.18, rect.left() + rect.width() * 0.42
        else:
            a, b = rect.left() + rect.width() * 0.58, rect.left() + rect.width() * 0.82
        return QPointF(a, y), QPointF(b, y), tip
    y = rect.top() + rect.height() * 0.16
    if preset is TailPreset.LEFT_TOP:
        a, b = rect.left() + rect.width() * 0.18, rect.left() + rect.width() * 0.42
    else:
        a, b = rect.left() + rect.width() * 0.58, rect.left() + rect.width() * 0.82
    return QPointF(a, y), QPointF(b, y), tip


def render_speech_bubble(settings: SpeechBubbleSettings) -> BubbleRenderResult | None:
    if not settings.text.strip():
        return None
    text_path, text_bounds = _text_path(settings)
    if not settings.bubble_enabled:
        bounds = text_bounds.adjusted(-2.0, -2.0, 2.0, 2.0)
        outer = 12.0
        width = max(1, math.ceil(bounds.width() + outer * 2))
        height = max(1, math.ceil(bounds.height() + outer * 2))
        if width > MAX_CANVAS_DIMENSION or height > MAX_CANVAS_DIMENSION or width * height > MAX_CANVAS_PIXELS:
            raise ValueError("セリフが大きすぎます。文字サイズを小さくしてください。")
        dx, dy = outer - bounds.left(), outer - bounds.top()
        text_path.translate(dx, dy)
        text_rect = text_bounds.translated(dx, dy)
        qimage = QImage(width, height, QImage.Format.Format_RGBA8888)
        qimage.fill(Qt.GlobalColor.transparent)
        painter = QPainter(qimage)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(*settings.text_color)))
            painter.drawPath(text_path)
        finally:
            painter.end()
        image = qimage_to_pil(qimage)
        geometry = BubbleGeometry(
            (text_rect.x(), text_rect.y(), text_rect.width(), text_rect.height()),
            (text_rect.center().x(), text_rect.center().y()),
            None,
        )
        return BubbleRenderResult(image, geometry)

    body_width = max(100.0, text_bounds.width() + settings.padding * 2)
    body_height = max(72.0, text_bounds.height() + settings.padding * 2)
    if settings.shape is BubbleShape.ELLIPSE:
        body_width *= 1.18
        body_height *= 1.18
    body_rect = QRectF(0, 0, body_width, body_height)
    body = _body_path(settings.shape, body_rect)
    normalized_tip = settings.tail_tip or TAIL_TIPS[settings.tail_preset]
    tip = QPointF(body_rect.center().x() + normalized_tip[0] * body_width / 2, body_rect.center().y() + normalized_tip[1] * body_height / 2)
    combined = QPainterPath(body)
    if settings.tail_enabled:
        a, b, tip = _tail_points(body_rect, settings.tail_preset, tip)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([a, b, tip]))
        tail.closeSubpath()
        combined = body.united(tail)

    stroke_extent = settings.stroke_width / 2.0 + 2.0
    bounds = combined.boundingRect().adjusted(-stroke_extent, -stroke_extent, stroke_extent, stroke_extent)
    outer = 12.0
    width = max(1, math.ceil(bounds.width() + outer * 2))
    height = max(1, math.ceil(bounds.height() + outer * 2))
    if width > MAX_CANVAS_DIMENSION or height > MAX_CANVAS_DIMENSION or width * height > MAX_CANVAS_PIXELS:
        raise ValueError("セリフまたはしっぽが大きすぎます。文字サイズや先端位置を小さくしてください。")
    dx, dy = outer - bounds.left(), outer - bounds.top()
    combined.translate(dx, dy)
    body_rect.translate(dx, dy)
    tip_canvas = QPointF(tip.x() + dx, tip.y() + dy) if settings.tail_enabled else None
    text_path.translate(body_rect.center().x() - text_bounds.center().x(), body_rect.center().y() - text_bounds.center().y())

    qimage = QImage(width, height, QImage.Format.Format_RGBA8888)
    qimage.fill(Qt.GlobalColor.transparent)
    painter = QPainter(qimage)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        pen = QPen(QColor(*settings.stroke_color), settings.stroke_width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(*settings.fill_color)))
        painter.drawPath(combined)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(*settings.text_color)))
        painter.drawPath(text_path)
    finally:
        painter.end()
    image = qimage_to_pil(qimage)
    geometry = BubbleGeometry((body_rect.x(), body_rect.y(), body_rect.width(), body_rect.height()), (body_rect.center().x(), body_rect.center().y()), (tip_canvas.x(), tip_canvas.y()) if tip_canvas else None)
    return BubbleRenderResult(image, geometry)


def save_speech_bubble(image: Image.Image, folder: Path, filename: str) -> Path:
    stem = normalize_filename_stem(filename, default="speech_bubble", strip_extensions=(".png",))
    output = io.BytesIO()
    image.convert("RGBA").save(output, format="PNG", optimize=True)
    return write_unique_bytes(Path(folder), stem, ".png", output.getvalue())
