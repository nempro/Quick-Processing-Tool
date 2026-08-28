from __future__ import annotations

from dataclasses import replace

from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .models import HandDrawSettings, HandPoint, HandStroke, HandTool


def scale_hand_draw(
    settings: HandDrawSettings,
    width: int,
    height: int,
) -> HandDrawSettings:
    """Scale immutable final-canvas vectors to a new final canvas."""
    if width <= 0 or height <= 0:
        raise ValueError("Hand drawing canvas dimensions must be positive")
    if not settings.strokes:
        return replace(settings, base_width=width, base_height=height)
    if settings.base_width <= 0 or settings.base_height <= 0:
        return replace(settings, base_width=width, base_height=height)
    if settings.base_width == width and settings.base_height == height:
        return settings
    scale_x = width / settings.base_width
    scale_y = height / settings.base_height
    width_scale = min(scale_x, scale_y)
    strokes = tuple(
        replace(
            stroke,
            points=tuple(
                HandPoint(point.x * scale_x, point.y * scale_y)
                for point in stroke.points
            ),
            width=max(0.25, stroke.width * width_scale),
        )
        for stroke in settings.strokes
    )
    return replace(
        settings,
        strokes=strokes,
        base_width=width,
        base_height=height,
    )


def _stroke_for_target(
    stroke: HandStroke,
    settings: HandDrawSettings,
    target_size: tuple[int, int],
) -> HandStroke:
    width, height = target_size
    base_width = settings.base_width or width
    base_height = settings.base_height or height
    scale_x = width / base_width
    scale_y = height / base_height
    return replace(
        stroke,
        points=tuple(
            HandPoint(point.x * scale_x, point.y * scale_y)
            for point in stroke.points
        ),
        width=max(0.25, stroke.width * min(scale_x, scale_y)),
    )


def _create_overlay_image(width: int, height: int) -> QImage:
    """Allocate only the final overlay; antialiasing never scales this buffer."""
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(0, 0, 0, 0).rgba())
    return image


def _qimage_to_pil(image: QImage) -> Image.Image:
    size = image.sizeInBytes()
    return Image.frombytes(
        "RGBA",
        (image.width(), image.height()),
        bytes(image.constBits()[:size]),
        "raw",
        "RGBA",
        image.bytesPerLine(),
    )


def _paint_stroke(painter: QPainter, stroke: HandStroke) -> None:
    if stroke.tool is HandTool.ERASER:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        color = QColor(0, 0, 0, 255)
    else:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        color = QColor(*stroke.color)
    pen = QPen(color)
    pen.setWidthF(stroke.width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    points = [QPointF(point.x, point.y) for point in stroke.points]
    if len(points) == 1:
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(color)
        radius = stroke.width / 2
        painter.drawEllipse(points[0], radius, radius)
        return
    painter.drawPolyline(points)


def render_hand_overlay(
    settings: HandDrawSettings,
    target_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Rasterize vectors into a transparent overlay without changing the source."""
    if target_size is None:
        target_size = (settings.base_width, settings.base_height)
    width, height = target_size
    if width <= 0 or height <= 0:
        raise ValueError("Hand drawing target dimensions must be positive")
    qimage = _create_overlay_image(width, height)
    if not settings.visible:
        return _qimage_to_pil(qimage)
    painter = QPainter(qimage)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for original in settings.strokes:
        stroke = _stroke_for_target(original, settings, target_size)
        _paint_stroke(painter, stroke)
    painter.end()
    return _qimage_to_pil(qimage)


def compose_hand_draw(image: Image.Image, settings: HandDrawSettings) -> Image.Image:
    result = image.convert("RGBA").copy()
    if not settings.visible or not settings.strokes:
        return result
    result.alpha_composite(render_hand_overlay(settings, result.size))
    return result
