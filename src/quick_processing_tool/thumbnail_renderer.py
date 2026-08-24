from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QTextLayout,
    QTextOption,
)

from .errors import ProcessingError
from .naming import unique_named_path, write_unique_bytes
from .thumbnail_models import (
    OverlayPosition,
    TextAlignment,
    ThumbnailFormat,
    ThumbnailSettings,
    TitleRecord,
    VerticalAlignment,
)


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
TOP_POSITIONS = {
    OverlayPosition.TOP_LEFT,
    OverlayPosition.TOP_CENTER,
    OverlayPosition.TOP_RIGHT,
}
BOTTOM_POSITIONS = {
    OverlayPosition.BOTTOM_LEFT,
    OverlayPosition.BOTTOM_CENTER,
    OverlayPosition.BOTTOM_RIGHT,
}


class ThumbnailLayoutError(ProcessingError):
    """The title or auxiliary text cannot fit without truncation."""


@dataclass(slots=True)
class TextLayoutResult:
    layout: QTextLayout
    font_size: int
    line_count: int
    total_height: float


@dataclass(slots=True)
class RenderedOverlay:
    text: str
    font: QFont
    color: QColor
    position: OverlayPosition
    height: float


@dataclass(slots=True)
class RenderedThumbnail:
    image: QImage
    font_size: int
    line_count: int
    overlay_texts: tuple[str, ...] = ()


def _qt_alignment(alignment: TextAlignment) -> Qt.AlignmentFlag:
    if alignment is TextAlignment.LEFT:
        return Qt.AlignmentFlag.AlignLeft
    if alignment is TextAlignment.RIGHT:
        return Qt.AlignmentFlag.AlignRight
    return Qt.AlignmentFlag.AlignHCenter


def _overlay_alignment(position: OverlayPosition) -> Qt.AlignmentFlag:
    if position in (OverlayPosition.TOP_LEFT, OverlayPosition.BOTTOM_LEFT):
        return Qt.AlignmentFlag.AlignLeft
    if position in (OverlayPosition.TOP_RIGHT, OverlayPosition.BOTTOM_RIGHT):
        return Qt.AlignmentFlag.AlignRight
    return Qt.AlignmentFlag.AlignHCenter


def _fitted_overlay(
    text: str,
    family: str,
    requested_size: int,
    color: str,
    position: OverlayPosition,
    available_width: float,
) -> RenderedOverlay:
    low = 8
    high = max(low, requested_size)
    best: QFont | None = None
    while low <= high:
        size = (low + high) // 2
        font = QFont(family)
        font.setPixelSize(size)
        if QFontMetricsF(font).horizontalAdvance(text) <= available_width:
            best = font
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise ThumbnailLayoutError(
            f"ラベルまたは連番が横幅に収まりません: {text[:40]}"
        )
    metrics = QFontMetricsF(best)
    return RenderedOverlay(text, best, QColor(color), position, metrics.height())


def _position_band(position: OverlayPosition) -> str:
    return "top" if position in TOP_POSITIONS else "bottom"


def _horizontal_slot_widths(
    candidates: list[tuple[str, str, int, str, OverlayPosition]],
    available_width: float,
) -> dict[str, float]:
    occupied = {
        (_position_band(position), position)
        for _, _, _, _, position in candidates
    }
    counts = {
        band: len({position for item_band, position in occupied if item_band == band})
        for band in ("top", "bottom")
    }
    return {
        band: available_width if counts[band] <= 1 else available_width / 3.0
        for band in ("top", "bottom")
    }


def _build_overlays(
    settings: ThumbnailSettings,
    record_index: int,
) -> list[RenderedOverlay]:
    inset = max(16.0, settings.margin / 2.0)
    available_width = settings.width - inset * 2.0
    if available_width <= 0:
        raise ThumbnailLayoutError("ラベル用の横幅が不足しています。")

    candidates: list[tuple[str, str, int, str, OverlayPosition]] = []
    for label in settings.labels:
        if label.enabled and label.text.strip():
            candidates.append(
                (
                    label.text,
                    label.font_family,
                    label.font_size,
                    label.color,
                    label.position,
                )
            )
    numbering = settings.numbering
    if numbering.enabled:
        candidates.append(
            (
                numbering.text_for_index(record_index),
                numbering.font_family,
                numbering.font_size,
                numbering.color,
                numbering.position,
            )
        )

    slot_widths = _horizontal_slot_widths(candidates, available_width)
    overlays: list[RenderedOverlay] = []
    for text, family, size, color, position in candidates:
        slot_width = slot_widths[_position_band(position)]
        overlays.append(
            _fitted_overlay(
                text,
                family,
                size,
                color,
                position,
                max(1.0, slot_width - 12.0),
            )
        )
    return overlays

def _reserved_bands(overlays: list[RenderedOverlay]) -> tuple[float, float]:
    gap = 6.0
    top_groups: dict[OverlayPosition, list[RenderedOverlay]] = {}
    bottom_groups: dict[OverlayPosition, list[RenderedOverlay]] = {}
    for overlay in overlays:
        groups = top_groups if overlay.position in TOP_POSITIONS else bottom_groups
        groups.setdefault(overlay.position, []).append(overlay)

    def band_height(groups: dict[OverlayPosition, list[RenderedOverlay]]) -> float:
        heights = []
        for items in groups.values():
            heights.append(
                sum(item.height for item in items) + gap * max(0, len(items) - 1)
            )
        return max(heights, default=0.0)

    return band_height(top_groups), band_height(bottom_groups)


def _title_rect(
    settings: ThumbnailSettings,
    overlays: list[RenderedOverlay],
) -> QRectF:
    top_band, bottom_band = _reserved_bands(overlays)
    inset = max(16.0, settings.margin / 2.0)
    overlay_gap = 8.0
    left = float(settings.margin)
    top = float(settings.margin)
    bottom = float(settings.height - settings.margin)
    if top_band:
        top = max(top, inset + top_band + overlay_gap)
    if bottom_band:
        bottom = min(
            bottom,
            float(settings.height) - inset - bottom_band - overlay_gap,
        )
    width = float(settings.width - settings.margin * 2)
    height = bottom - top
    if width <= 0 or height <= 0:
        raise ThumbnailLayoutError("文字領域の余白がキャンバスより大きすぎます。")
    return QRectF(left, top, width, height)


def _layout_at_size(
    title: str,
    settings: ThumbnailSettings,
    font_size: int,
    max_width: float,
) -> TextLayoutResult:
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
        line.setLineWidth(max_width)
        line.setPosition(QPointF(0.0, y))
        lines.append(line)
        y += line.height() * settings.line_spacing
    layout.endLayout()

    total_height = (
        y - lines[-1].height() * (settings.line_spacing - 1.0)
        if lines
        else 0.0
    )
    return TextLayoutResult(layout, font_size, len(lines), total_height)


def _layout_title_in_rect(
    title: str,
    settings: ThumbnailSettings,
    rect: QRectF,
) -> TextLayoutResult:
    if not title.strip():
        raise ThumbnailLayoutError("タイトルが空です。")
    if settings.width <= 0 or settings.height <= 0:
        raise ThumbnailLayoutError("キャンバスサイズは1px以上にしてください。")
    minimum = min(settings.font_size, settings.min_font_size)

    def fits(result: TextLayoutResult) -> bool:
        return result.line_count <= settings.max_lines and result.total_height <= rect.height()

    base_result = _layout_at_size(title, settings, settings.font_size, rect.width())
    if fits(base_result):
        return base_result

    best: TextLayoutResult | None = None
    low = minimum
    high = settings.font_size - 1
    while low <= high:
        candidate_size = (low + high) // 2
        candidate = _layout_at_size(title, settings, candidate_size, rect.width())
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


def layout_title(
    title: str,
    settings: ThumbnailSettings,
    record_index: int = 1,
) -> TextLayoutResult:
    overlays = _build_overlays(settings, record_index)
    return _layout_title_in_rect(title, settings, _title_rect(settings, overlays))


def _overlay_rect(
    position: OverlayPosition,
    occupied_positions: set[OverlayPosition],
    inset: float,
    available_width: float,
    y: float,
    height: float,
) -> QRectF:
    if len(occupied_positions) <= 1:
        return QRectF(inset, y, available_width, height)
    slot_width = available_width / 3.0
    if position in (OverlayPosition.TOP_LEFT, OverlayPosition.BOTTOM_LEFT):
        slot = 0
    elif position in (OverlayPosition.TOP_RIGHT, OverlayPosition.BOTTOM_RIGHT):
        slot = 2
    else:
        slot = 1
    return QRectF(inset + slot * slot_width, y, slot_width, height)


def _draw_overlays(
    painter: QPainter,
    settings: ThumbnailSettings,
    overlays: list[RenderedOverlay],
) -> None:
    inset = max(16.0, settings.margin / 2.0)
    available_width = float(settings.width) - inset * 2.0
    gap = 6.0
    groups: dict[OverlayPosition, list[RenderedOverlay]] = {}
    for overlay in overlays:
        groups.setdefault(overlay.position, []).append(overlay)

    occupied_by_band = {
        band: {
            position
            for position in groups
            if _position_band(position) == band
        }
        for band in ("top", "bottom")
    }
    for position, items in groups.items():
        band = _position_band(position)
        occupied = occupied_by_band[band]
        if position in TOP_POSITIONS:
            y = inset
            ordered = items
        else:
            y = float(settings.height) - inset
            ordered = list(reversed(items))
        for overlay in ordered:
            painter.setFont(overlay.font)
            painter.setPen(overlay.color)
            if position in TOP_POSITIONS:
                rect = _overlay_rect(
                    position,
                    occupied,
                    inset,
                    available_width,
                    y,
                    overlay.height,
                )
                y += overlay.height + gap
            else:
                y -= overlay.height
                rect = _overlay_rect(
                    position,
                    occupied,
                    inset,
                    available_width,
                    y,
                    overlay.height,
                )
                y -= gap
            painter.drawText(
                rect,
                _overlay_alignment(position) | Qt.AlignmentFlag.AlignVCenter,
                overlay.text,
            )

def render_thumbnail(
    title: str,
    settings: ThumbnailSettings,
    record_index: int = 1,
) -> RenderedThumbnail:
    overlays = _build_overlays(settings, record_index)
    title_rect = _title_rect(settings, overlays)
    result = _layout_title_in_rect(title, settings, title_rect)
    image = QImage(settings.width, settings.height, QImage.Format.Format_ARGB32)
    image.fill(QColor(settings.background_color))

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QColor(settings.font_color))
        if settings.vertical_alignment is VerticalAlignment.TOP:
            origin_y = title_rect.top()
        elif settings.vertical_alignment is VerticalAlignment.BOTTOM:
            origin_y = title_rect.bottom() - result.total_height
        else:
            origin_y = title_rect.top() + (
                title_rect.height() - result.total_height
            ) / 2.0
        result.layout.draw(painter, QPointF(title_rect.left(), origin_y))
        _draw_overlays(painter, settings, overlays)
    finally:
        painter.end()
    return RenderedThumbnail(
        image,
        result.font_size,
        result.line_count,
        tuple(item.text for item in overlays),
    )


def encode_thumbnail(
    image: QImage,
    output_format: ThumbnailFormat,
    quality: int,
) -> bytes:
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


def _thumbnail_name(
    record: TitleRecord,
    total: int,
    output_format: ThumbnailFormat,
) -> tuple[str, str]:
    digits = max(3, len(str(max(1, total))))
    number = f"{record.index:0{digits}d}"
    title = sanitize_filename_component(record.title)
    extension = ".jpg" if output_format is ThumbnailFormat.JPEG else ".png"
    return f"{number}_{title}", extension


def thumbnail_output_path(
    folder: Path,
    record: TitleRecord,
    total: int,
    output_format: ThumbnailFormat,
) -> Path:
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


def render_record(
    record: TitleRecord,
    settings: ThumbnailSettings,
) -> tuple[bytes, RenderedThumbnail]:
    rendered = render_thumbnail(record.title, settings, record.index)
    return (
        encode_thumbnail(rendered.image, settings.output_format, settings.quality),
        rendered,
    )