from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from .errors import ProcessingError, UnsupportedImageError
from .image_workspace import SUPPORTED_SOURCE_SUFFIXES, require_source_file
from .naming import write_unique_bytes


LOGGER = logging.getLogger(__name__)
SUPPORTED_METADATA_FORMATS = {"PNG", "JPEG", "WEBP"}


@dataclass(frozen=True, slots=True)
class MetadataRemovalOutput:
    source: Path
    output: Path
    image_format: str
    width: int
    height: int


def is_supported_image_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES


def collect_supported_image_paths(paths: list[Path]) -> list[Path]:
    """Collect direct image files and only the direct children of folders."""
    collected: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        candidates = (
            sorted(path.iterdir()) if path.is_dir() else [path]
        )
        for candidate in candidates:
            if not is_supported_image_path(candidate):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate.absolute()
            key = str(resolved).casefold()
            if key not in seen:
                collected.append(resolved)
                seen.add(key)
    return collected


def metadata_output_folder(
    source: Path,
    destination_mode: str,
    custom_folder: Path | None = None,
) -> Path:
    if destination_mode == "Same folder":
        return source.parent
    if destination_mode == "Processed":
        return source.parent / "Processed"
    if destination_mode == "Custom folder" and custom_folder is not None:
        return custom_folder
    raise ProcessingError("保存先フォルダーを選んでください。")


def _display_image(image: Image.Image, image_format: str) -> Image.Image:
    """Apply orientation while keeping modes needed for alpha and palettes."""
    icc_profile = image.info.get("icc_profile")
    displayed = ImageOps.exif_transpose(image).copy()
    displayed.info.clear()
    if icc_profile:
        displayed.info["icc_profile"] = icc_profile
    if image_format == "JPEG" and displayed.mode not in {"RGB", "L"}:
        return displayed.convert("RGB")
    if displayed.mode == "P" and "transparency" in image.info:
        return displayed.convert("RGBA")
    return displayed


def _common_save_kwargs(image: Image.Image) -> dict[str, object]:
    icc_profile = image.info.get("icc_profile")
    return {"icc_profile": icc_profile} if icc_profile else {}


def _encode_static(image: Image.Image, image_format: str) -> bytes:
    output = BytesIO()
    kwargs = _common_save_kwargs(image)
    if image_format == "JPEG":
        kwargs.update(quality=95, optimize=True)
    elif image_format == "WEBP":
        kwargs.update(quality=100, method=6)
    image.save(output, format=image_format, **kwargs)
    return output.getvalue()


def _encode_animation(image: Image.Image, image_format: str) -> bytes:
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        cleaned = _display_image(frame, image_format)
        if cleaned.mode not in {"RGB", "RGBA"}:
            cleaned = cleaned.convert("RGBA")
        frames.append(cleaned)
        durations.append(int(frame.info.get("duration", image.info.get("duration", 0))))
    if not frames:
        raise ProcessingError("画像フレームを読み込めませんでした。")
    output = BytesIO()
    kwargs = _common_save_kwargs(image)
    kwargs.update(
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=int(image.info.get("loop", 0)),
    )
    if image_format == "WEBP":
        kwargs.update(quality=100, method=6)
    frames[0].save(output, format=image_format, **kwargs)
    return output.getvalue()


def sanitize_image_bytes(source: Path) -> tuple[bytes, str, int, int]:
    """Re-encode a supported image without privacy or generation metadata."""
    resolved = require_source_file(source)
    try:
        with Image.open(resolved) as opened:
            image_format = (opened.format or "").upper()
            if image_format == "JPG":
                image_format = "JPEG"
            if image_format not in SUPPORTED_METADATA_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP画像を選んでください。")
            width, height = ImageOps.exif_transpose(opened).size
            if getattr(opened, "n_frames", 1) > 1:
                data = _encode_animation(opened, image_format)
            else:
                data = _encode_static(_display_image(opened, image_format), image_format)
    except UnsupportedImageError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ProcessingError(f"画像を開けませんでした: {resolved.name}") from exc
    return data, image_format, width, height


def remove_image_metadata(source: Path, destination_folder: Path) -> MetadataRemovalOutput:
    """Save a collision-safe metadata-free copy and prove it can be reopened."""
    source = require_source_file(source)
    data, image_format, width, height = sanitize_image_bytes(source)
    output = write_unique_bytes(destination_folder, source.stem, source.suffix.lower(), data)
    try:
        if output.stat().st_size <= 0:
            raise ProcessingError("保存した画像が空です。")
        with Image.open(output) as reopened:
            reopened.load()
            if (reopened.format or "").upper() not in SUPPORTED_METADATA_FORMATS:
                raise ProcessingError("保存した画像を再度開けませんでした。")
    except Exception as exc:
        output.unlink(missing_ok=True)
        if isinstance(exc, ProcessingError):
            raise
        raise ProcessingError("保存した画像を再度開けませんでした。") from exc
    LOGGER.info("Metadata removed: %s -> %s", source, output)
    return MetadataRemovalOutput(source, output, image_format, width, height)
