from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class UpscaleMode(str, Enum):
    ILLUSTRATION = "illustration"
    PHOTO = "photo"


class UpscaleOutputFormat(str, Enum):
    SAME = "same"
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"


@dataclass(frozen=True, slots=True)
class UpscaleOptions:
    scale: int = 2
    mode: UpscaleMode = UpscaleMode.ILLUSTRATION
    output_format: UpscaleOutputFormat = UpscaleOutputFormat.SAME
    jpeg_quality: int = 95

    def __post_init__(self) -> None:
        if self.scale not in (2, 4):
            raise ValueError("Upscale scale must be 2 or 4")


@dataclass(frozen=True, slots=True)
class UpscaleResult:
    output_path: Path
    width: int
    height: int
    size_bytes: int
    duration_seconds: float
    mode: UpscaleMode
    scale: int
    backend_name: str
