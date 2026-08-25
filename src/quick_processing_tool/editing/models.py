from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class FilterPreset(str, Enum):
    NONE = "none"
    GRAYSCALE = "grayscale"
    SEPIA = "sepia"
    BRIGHT = "bright"
    DARK = "dark"
    WARM = "warm"
    COOL = "cool"
    SHARP = "sharp"
    SOFT = "soft"
    FADED = "faded"


class PlacementMode(str, Enum):
    FIT = "fit"
    FILL = "fill"


class CanvasBackground(str, Enum):
    TRANSPARENT = "transparent"
    WHITE = "white"
    BLACK = "black"
    CUSTOM = "custom"


class TextPosition(str, Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    CENTER = "center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class EditOutputFormat(str, Enum):
    SAME = "same"
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"


@dataclass(frozen=True, slots=True)
class TransparencySettings:
    enabled: bool = False
    target_color: tuple[int, int, int] = (255, 255, 255)
    tolerance: int = 24
    edge_softness: int = 8

    def __post_init__(self) -> None:
        if not 0 <= self.tolerance <= 255:
            raise ValueError("Tolerance must be between 0 and 255")
        if not 0 <= self.edge_softness <= 255:
            raise ValueError("Edge softness must be between 0 and 255")


@dataclass(frozen=True, slots=True)
class CanvasSettings:
    width: int = 0
    height: int = 0
    placement: PlacementMode = PlacementMode.FIT
    padding_percent: int = 0
    background: CanvasBackground = CanvasBackground.TRANSPARENT
    custom_color: tuple[int, int, int, int] = (255, 255, 255, 255)

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("Canvas dimensions cannot be negative")
        if not 0 <= self.padding_percent <= 45:
            raise ValueError("Canvas padding must be between 0 and 45 percent")


@dataclass(frozen=True, slots=True)
class TextSettings:
    enabled: bool = False
    text: str = ""
    font_family: str = "Yu Gothic UI"
    font_size: int = 64
    bold: bool = True
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    outline_enabled: bool = True
    outline_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    outline_width: int = 4
    position: TextPosition = TextPosition.BOTTOM_CENTER
    safe_margin: int = 24

    def __post_init__(self) -> None:
        if not 8 <= self.font_size <= 500:
            raise ValueError("Font size must be between 8 and 500")
        if not 0 <= self.outline_width <= 40:
            raise ValueError("Outline width must be between 0 and 40")
        if not 0 <= self.safe_margin <= 500:
            raise ValueError("Safe margin cannot be negative")


@dataclass(frozen=True, slots=True)
class EditSettings:
    filter_preset: FilterPreset = FilterPreset.NONE
    transparency: TransparencySettings = field(default_factory=TransparencySettings)
    canvas: CanvasSettings = field(default_factory=CanvasSettings)
    text: TextSettings = field(default_factory=TextSettings)


@dataclass(frozen=True, slots=True)
class EditResult:
    output_path: Path
    width: int
    height: int
    size_bytes: int
    output_format: str
    has_alpha: bool
