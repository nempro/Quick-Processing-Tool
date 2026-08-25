from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)

from .models import TextPosition, TextSettings


LEFT_POSITIONS = {
    TextPosition.TOP_LEFT,
    TextPosition.MIDDLE_LEFT,
    TextPosition.BOTTOM_LEFT,
}
RIGHT_POSITIONS = {
    TextPosition.TOP_RIGHT,
    TextPosition.MIDDLE_RIGHT,
    TextPosition.BOTTOM_RIGHT,
}
TOP_POSITIONS = {
    TextPosition.TOP_LEFT,
    TextPosition.TOP_CENTER,
    TextPosition.TOP_RIGHT,
}
BOTTOM_POSITIONS = {
    TextPosition.BOTTOM_LEFT,
    TextPosition.BOTTOM_CENTER,
    TextPosition.BOTTOM_RIGHT,
}


def pil_to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    raw = rgba.tobytes("raw", "RGBA")
    return QImage(
        raw,
        rgba.width,
        rgba.height,
        rgba.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


def qimage_to_pil(image: QImage) -> Image.Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = bytes(rgba.constBits())
    return Image.frombuffer(
        "RGBA",
        (rgba.width(), rgba.height()),
        raw,
        "raw",
        "RGBA",
        rgba.bytesPerLine(),
        1,
    ).copy()


def _font(settings: TextSettings, size: int) -> QFont:
    font = QFont(settings.font_family)
    font.setPixelSize(size)
    font.setBold(settings.bold)
    return font


def _text_path(settings: TextSettings, size: int) -> QPainterPath:
    lines = settings.text.splitlines() or [settings.text]
    font = _font(settings, size)
    metrics = QFontMetricsF(font)
    widths = [metrics.horizontalAdvance(line or " ") for line in lines]
    block_width = max(widths, default=0.0)
    path = QPainterPath()
    for index, (line, line_width) in enumerate(zip(lines, widths)):
        if settings.position in LEFT_POSITIONS:
            x = 0.0
        elif settings.position in RIGHT_POSITIONS:
            x = block_width - line_width
        else:
            x = (block_width - line_width) / 2.0
        baseline = metrics.ascent() + index * metrics.lineSpacing()
        path.addText(QPointF(x, baseline), font, line)
    return path


def _fitted_path(image_size: tuple[int, int], settings: TextSettings) -> QPainterPath:
    stroke = settings.outline_width if settings.outline_enabled else 0
    available_width = max(1, image_size[0] - (settings.safe_margin + stroke) * 2)
    available_height = max(1, image_size[1] - (settings.safe_margin + stroke) * 2)
    path = QPainterPath()
    for size in range(settings.font_size, 7, -1):
        candidate = _text_path(settings, size)
        bounds = candidate.boundingRect()
        if bounds.width() + stroke * 2 <= available_width and bounds.height() + stroke * 2 <= available_height:
            path = candidate
            break
    return path


def draw_text(image: Image.Image, settings: TextSettings) -> Image.Image:
    if not settings.enabled or not settings.text.strip():
        return image.convert("RGBA").copy()
    qimage = pil_to_qimage(image)
    path = _fitted_path((qimage.width(), qimage.height()), settings)
    if path.isEmpty():
        return image.convert("RGBA").copy()
    stroke = settings.outline_width if settings.outline_enabled else 0
    bounds = path.boundingRect().adjusted(-stroke, -stroke, stroke, stroke)
    if settings.position in LEFT_POSITIONS:
        left = float(settings.safe_margin)
    elif settings.position in RIGHT_POSITIONS:
        left = qimage.width() - settings.safe_margin - bounds.width()
    else:
        left = (qimage.width() - bounds.width()) / 2.0
    if settings.position in TOP_POSITIONS:
        top = float(settings.safe_margin)
    elif settings.position in BOTTOM_POSITIONS:
        top = qimage.height() - settings.safe_margin - bounds.height()
    else:
        top = (qimage.height() - bounds.height()) / 2.0
    path.translate(left - bounds.left(), top - bounds.top())

    painter = QPainter(qimage)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if settings.outline_enabled and settings.outline_width > 0:
            pen = QPen(QColor(*settings.outline_color), settings.outline_width * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
        painter.fillPath(path, QBrush(QColor(*settings.color)))
    finally:
        painter.end()
    return qimage_to_pil(qimage)
