from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TextAlignment(str, Enum):
    LEFT = "Left"
    CENTER = "Center"
    RIGHT = "Right"


class VerticalAlignment(str, Enum):
    TOP = "Top"
    CENTER = "Center"
    BOTTOM = "Bottom"


class ThumbnailFormat(str, Enum):
    PNG = "PNG"
    JPEG = "JPEG"


class OverlayPosition(str, Enum):
    TOP_LEFT = "TopLeft"
    TOP_CENTER = "TopCenter"
    TOP_RIGHT = "TopRight"
    BOTTOM_LEFT = "BottomLeft"
    BOTTOM_CENTER = "BottomCenter"
    BOTTOM_RIGHT = "BottomRight"


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
class FixedLabelSettings:
    enabled: bool = False
    text: str = ""
    font_family: str = "Yu Gothic UI"
    font_size: int = 28
    color: str = "#FFFFFF"
    position: OverlayPosition = OverlayPosition.TOP_LEFT


@dataclass(slots=True)
class NumberingSettings:
    enabled: bool = False
    prefix: str = "#"
    start_number: int = 1
    digits: int = 3
    font_family: str = "Yu Gothic UI"
    font_size: int = 28
    color: str = "#FFFFFF"
    position: OverlayPosition = OverlayPosition.TOP_RIGHT

    def text_for_index(self, index: int) -> str:
        number = self.start_number + max(0, index - 1)
        return f"{self.prefix}{number:0{self.digits}d}"


@dataclass(slots=True)
class ThumbnailSettings:
    width: int = 1200
    height: int = 1200
    canvas_preset_name: str = "1:1"
    background_color: str = "#171923"
    font_family: str = "Yu Gothic UI"
    font_size: int = 72
    min_font_size: int = 36
    font_color: str = "#FFFFFF"
    bold: bool = True
    alignment: TextAlignment = TextAlignment.CENTER
    vertical_alignment: VerticalAlignment = VerticalAlignment.CENTER
    margin: int = 100
    max_lines: int = 5
    line_spacing: float = 1.15
    labels: list[FixedLabelSettings] = field(
        default_factory=lambda: [FixedLabelSettings()]
    )
    numbering: NumberingSettings = field(default_factory=NumberingSettings)
    output_format: ThumbnailFormat = ThumbnailFormat.JPEG
    quality: int = 90

    @property
    def fixed_label(self) -> FixedLabelSettings:
        if not self.labels:
            self.labels.append(FixedLabelSettings())
        return self.labels[0]


@dataclass(frozen=True, slots=True)
class ThumbnailTemplate:
    schema_version: int
    template_id: str
    name: str
    settings: ThumbnailSettings
    built_in: bool = False

def parse_title_records(text: str) -> list[TitleRecord]:
    titles = [line.strip() for line in text.splitlines() if line.strip()]
    return [
        TitleRecord(index=index, title=title)
        for index, title in enumerate(titles, start=1)
    ]