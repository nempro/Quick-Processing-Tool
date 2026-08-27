from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QObject, Signal

from .errors import ProcessingError, UnsupportedImageError


SUPPORTED_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MISSING_SOURCE_MESSAGE = (
    "元画像が見つかりません。\n\n"
    "ファイルが移動または削除された可能性があります。"
)


class SourceValidation(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


class MissingSourceError(ProcessingError):
    """The source was valid when selected but no longer exists."""


@dataclass(frozen=True, slots=True)
class SourceImage:
    path: Path
    filename: str
    width: int
    height: int
    format: str
    size_bytes: int
    has_alpha: bool
    document_id: str
    generation_id: int


def normalized_path_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def classify_source(path: Path) -> SourceValidation:
    candidate = Path(path)
    if not candidate.is_file():
        return SourceValidation.MISSING
    if candidate.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
        return SourceValidation.UNSUPPORTED
    return SourceValidation.VALID


def require_source_file(path: Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise MissingSourceError(MISSING_SOURCE_MESSAGE)
    return resolved


def read_source_image(path: Path, generation_id: int) -> SourceImage:
    resolved = require_source_file(path)
    if resolved.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
        raise UnsupportedImageError("PNG / JPEG / WebP画像を選んでください。")
    try:
        with Image.open(resolved) as opened:
            opened.load()
            normalized = ImageOps.exif_transpose(opened)
            image_format = (opened.format or resolved.suffix[1:]).upper()
            if image_format == "JPG":
                image_format = "JPEG"
            if image_format not in {"PNG", "JPEG", "WEBP"}:
                raise UnsupportedImageError("PNG / JPEG / WebP画像を選んでください。")
            has_alpha = "A" in normalized.getbands() or (
                normalized.mode == "P" and "transparency" in normalized.info
            )
            width, height = normalized.size
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        if not resolved.is_file():
            raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
        raise UnsupportedImageError("PNG / JPEG / WebP画像を選んでください。") from exc
    try:
        size_bytes = resolved.stat().st_size
    except OSError as exc:
        raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
    return SourceImage(
        path=resolved,
        filename=resolved.name,
        width=width,
        height=height,
        format=image_format,
        size_bytes=size_bytes,
        has_alpha=has_alpha,
        document_id=uuid.uuid4().hex,
        generation_id=generation_id,
    )


class ImageWorkspace(QObject):
    """Application-level source state. It deliberately stores no decoded image."""

    source_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current: SourceImage | None = None
        self._generation_id = 0

    @property
    def current(self) -> SourceImage | None:
        return self._current

    @property
    def generation_id(self) -> int:
        return self._generation_id

    def set_source(self, path: Path) -> SourceImage:
        candidate = Path(path).resolve()
        if self._current is not None and normalized_path_key(candidate) == normalized_path_key(self._current.path):
            require_source_file(candidate)
            return self._current
        next_generation = self._generation_id + 1
        source = read_source_image(candidate, next_generation)
        self._generation_id = next_generation
        self._current = source
        self.source_changed.emit(source)
        return source

    def validate_current(self) -> SourceValidation:
        if self._current is None:
            return SourceValidation.MISSING
        return classify_source(self._current.path)
