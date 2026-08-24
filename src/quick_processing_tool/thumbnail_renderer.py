from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextLayout, QTextOption

from .errors import ProcessingError
from .naming import unique_named_path, write_unique_bytes
from .thumbnail_models import TextAlignment, ThumbnailFormat, ThumbnailSettings, TitleRecord


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ThumbnailLayoutError(ProcessingError):
    """The title cannot fit without truncation."""


@dataclass(slots=True)
class TextLayoutResult:
    layout: QTextLayout
    font_size: int
    line_count: int
    total_height: float


@dataclass(slots=True)
class RenderedThumbnail:
    image: QImage
    font_size: int
    line_count: int


def _qt_alignment(alignment: TextAlignment) -> Qt.AlignmentFlag:
    if alignment is TextAlignment.LEFT:
        return Qt.AlignmentFlag.AlignLeft
    if alignment is TextAlignment.RIGHT:
        return Qt.AlignmentFlag.AlignRight
    return Qt.AlignmentFlag.AlignHCenter


def _layout_at_size(title: str, settings: ThumbnailSettings, font_size: int) -> TextLayoutResult:
    max_width = settings.width - settings.margin * 2
    if max_width <= 0:
        raise ThumbnailLayoutError("文字領域の余白がキャンバスより大きすぎます。")

    font = QFont(settings.font_family)
    font.setPixelSize(font_size)
    font.setBold(settings.bold)

    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setAlignment(_qt_alignment(settings.alignment))

    layout = QTextLayout(title, font)
    layout.setTextOption(option)
    layout.beginLayout()
    lines = []
    y = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(float(max_width))
        line.setPosition(QPointF(0.0, y))
        lines.append(line)
        y += line.height() * settings.line_spacing
    layout.endLayout()

    if lines:
        total_height = y - lines[-1].height() * (settings.line_spacing - 1.0)
    else:
        total_height = 0.0
    return TextLayoutResult(layout, font_size, len(lines), total_height)


def layout_title(title: str, settings: ThumbnailSettings) -> TextLayoutResult:
    if not title.strip():
        raise ThumbnailLayoutError("タイトルが空です。")
    if settings.width <= 0 or settings.height <= 0:
        raise ThumbnailLayoutError("キャンバスサイズは1px以上にしてください。")
    max_height = settings.height - settings.margin * 2
    if max_height <= 0:
        raise ThumbnailLayoutError("文字領域の余白がキャンバスより大きすぎます。")

    minimum = min(settings.font_size, settings.min_font_size)

    def fits(result: TextLayoutResult) -> bool:
        return (
            result.line_count <= settings.max_lines
            and result.total_height <= max_height
        )

    base_result = _layout_at_size(title, settings, settings.font_size)
    if fits(base_result):
        return base_result

    best: TextLayoutResult | None = None
    low = minimum
    high = settings.font_size - 1
    while low <= high:
        candidate_size = (low + high) // 2
        candidate = _layout_at_size(title, settings, candidate_size)
        if fits(candidate):
            best = candidate
            low = candidate_size + 1
        else:
            high = candidate_size - 1
    if best is not None:
        return best
    raise ThumbnailLayoutError(
        f"このタイトルは最小文字サイズでも収まりません: {title[:40]}"
    )


def render_thumbnail(title: str, settings: ThumbnailSettings) -> RenderedThumbnail:
    result = layout_title(title, settings)
    image = QImage(settings.width, settings.height, QImage.Format.Format_ARGB32)
    image.fill(QColor(settings.background_color))

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QColor(settings.font_color))
        max_height = settings.height - settings.margin * 2
        origin_y = settings.margin + (max_height - result.total_height) / 2.0
        result.layout.draw(painter, QPointF(float(settings.margin), origin_y))
    finally:
        painter.end()
    return RenderedThumbnail(image, result.font_size, result.line_count)


def encode_thumbnail(image: QImage, output_format: ThumbnailFormat, quality: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ProcessingError("画像の変換用バッファを開けません。")
    format_name = output_format.value
    save_quality = quality if output_format is ThumbnailFormat.JPEG else -1
    if not image.save(buffer, format_name, save_quality):
        raise ProcessingError(f"{output_format.value}画像へ変換できません。")
    buffer.close()
    return bytes(data)


def sanitize_filename_component(value: str, max_length: int = 80) -> str:
    safe = INVALID_FILENAME.sub("_", unicodedata.normalize("NFC", value))
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if not safe:
        safe = "untitled"
    reserved_stem = safe.split(".", 1)[0].upper()
    if reserved_stem in RESERVED_FILENAMES:
        safe += "_"
    safe = safe[:max_length].rstrip(" .")
    return safe or "untitled"


def _thumbnail_name(record: TitleRecord, total: int, output_format: ThumbnailFormat) -> tuple[str, str]:
    digits = max(3, len(str(max(1, total))))
    number = f"{record.index:0{digits}d}"
    title = sanitize_filename_component(record.title)
    extension = ".jpg" if output_format is ThumbnailFormat.JPEG else ".png"
    return f"{number}_{title}", extension


def thumbnail_output_path(folder: Path, record: TitleRecord, total: int, output_format: ThumbnailFormat) -> Path:
    stem, extension = _thumbnail_name(record, total, output_format)
    return unique_named_path(folder, stem, extension)


def write_thumbnail_output(
    folder: Path,
    record: TitleRecord,
    total: int,
    output_format: ThumbnailFormat,
    data: bytes,
) -> Path:
    stem, extension = _thumbnail_name(record, total, output_format)
    return write_unique_bytes(folder, stem, extension, data)


def render_record(record: TitleRecord, settings: ThumbnailSettings) -> tuple[bytes, RenderedThumbnail]:
    rendered = render_thumbnail(record.title, settings)
    return encode_thumbnail(rendered.image, settings.output_format, settings.quality), rendered
