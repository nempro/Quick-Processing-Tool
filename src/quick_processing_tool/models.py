from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ResizeMode(str, Enum):
    NONE = "No Resize"
    DIMENSIONS = "Width / Height"
    LONG_EDGE = "Long Edge"
    PERCENTAGE = "Percentage"


class OutputFormat(str, Enum):
    SAME = "Same as Original"
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WebP"


class Transform(str, Enum):
    ROTATE_LEFT = "Rotate Left 90°"
    ROTATE_RIGHT = "Rotate Right 90°"
    ROTATE_180 = "Rotate 180°"
    FLIP_HORIZONTAL = "Flip Horizontal"
    FLIP_VERTICAL = "Flip Vertical"


@dataclass(slots=True)
class ProcessingOptions:
    resize_mode: ResizeMode = ResizeMode.NONE
    width: int = 1600
    height: int = 1600
    keep_aspect: bool = True
    long_edge: int = 1600
    percentage: float = 100.0
    output_format: OutputFormat = OutputFormat.SAME
    target_bytes: int | None = None
    quality: int = 95
    remove_metadata: bool = True
    preserve_timestamp: bool = True
    jpeg_background: tuple[int, int, int] = (255, 255, 255)
    transforms: list[Transform] = field(default_factory=list)
    # (x, y, width, height), normalized to the oriented source image.
    crop_rect: tuple[float, float, float, float] | None = None


@dataclass(slots=True)
class ImageInfo:
    path: Path
    width: int
    height: int
    format: str
    size_bytes: int


@dataclass(slots=True)
class ProcessedImage:
    data: bytes
    width: int
    height: int
    format: str
    quality: int | None
    source: Path

    @property
    def size_bytes(self) -> int:
        return len(self.data)
