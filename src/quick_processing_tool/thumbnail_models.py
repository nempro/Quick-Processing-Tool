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
class TitleRecord:
    index: int
    title: str
    label: str = ""
    filename: str = ""


@dataclass(slots=True)
class ThumbnailSettings:
    width: int = 1080
    height: int = 1080
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
    return [TitleRecord(index=index, title=title) for index, title in enumerate(titles, start=1)]