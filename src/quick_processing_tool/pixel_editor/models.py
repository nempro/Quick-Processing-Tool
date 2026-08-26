from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PixelTool(str, Enum):
    PENCIL = "pencil"
    ERASER = "eraser"
    EYEDROPPER = "eyedropper"


@dataclass(frozen=True, slots=True)
class ReferenceImage:
    image: object
    opacity: int = 50

    def __post_init__(self) -> None:
        if not 10 <= self.opacity <= 100:
            raise ValueError("Reference opacity must be between 10 and 100")


@dataclass(frozen=True, slots=True)
class PixelExportResult:
    output_path: Path
    width: int
    height: int
    size_bytes: int
    has_alpha: bool
