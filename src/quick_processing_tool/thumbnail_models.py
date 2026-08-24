from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TextAlignment(str, Enum):
    LEFT = "Left"
    CENTER = "Center"
    RIGHT = "Right"


class ThumbnailFormat(str, Enum):
    PNG = "PNG"
    JPEG = "JPEG"


@dataclass(frozen=True, slots=True)
class CanvasPreset:
    name: str
    width: int
    height: int


CANVAS_PRESETS = (
    CanvasPreset("1:1", 1200, 1200),
    CanvasPreset("16:9", 1920, 1080),
    CanvasPreset("4:3", 1200, 900),
    CanvasPreset("3:4", 900, 1200),
    CanvasPreset("9:16", 1080, 1920),
)


@dataclass(frozen=True, slots=True)
class TitleRecord:
    index: int
    title: str
    label: str = ""
    filename: str = ""


@dataclass(slots=True)
class ThumbnailSettings:
    width: int = 1200
    height: int = 1200
    background_color: str = "#171923"
    font_family: str = "Yu Gothic UI"
    font_size: int = 72
    min_font_size: int = 36
    font_color: str = "#FFFFFF"
    bold: bool = True
    alignment: TextAlignment = TextAlignment.CENTER
    margin: int = 100
    max_lines: int = 5
    line_spacing: float = 1.15
    output_format: ThumbnailFormat = ThumbnailFormat.JPEG
    quality: int = 90


def parse_title_records(text: str) -> list[TitleRecord]:
    titles = [line.strip() for line in text.splitlines() if line.strip()]
    return [
        TitleRecord(index=index, title=title)
        for index, title in enumerate(titles, start=1)
    ]
