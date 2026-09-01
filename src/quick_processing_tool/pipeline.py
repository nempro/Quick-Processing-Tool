from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import ProcessingError, TargetSizeUnreachable, UnsupportedImageError
from .image_workspace import MISSING_SOURCE_MESSAGE, MissingSourceError, require_source_file
from .image_splitting import (
    MIN_SPLIT_PANEL_PIXELS,
    ImageSplitOptions,
    SplitDirection,
    split_image,
)
from .models import ImageInfo, OutputFormat, ProcessedImage, ProcessingOptions, ResizeMode
from .processors.encode import encode_best_quality
from .processors.metadata import safe_metadata
from .processors.resize import resize_image
from .processors.transform import apply_transforms, normalize_orientation


LOGGER = logging.getLogger(__name__)
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}


def read_image_info(path: Path) -> ImageInfo:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            if image_format not in SUPPORTED_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP のみ開けます。")
            oriented = normalize_orientation(image)
            return ImageInfo(path, oriented.width, oriented.height, image_format, path.stat().st_size)
    except UnsupportedImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedImageError(f"画像を開けません: {path.name}") from exc


def resolve_output_format(original: str, selected: OutputFormat) -> str:
    if selected is OutputFormat.SAME:
        return "JPEG" if original.upper() in {"JPG", "JPEG"} else original.upper()
    return selected.value.upper()


def process_image(path: Path, options: ProcessingOptions) -> ProcessedImage:
    LOGGER.info("Conversion start: %s", path)
    require_source_file(path)
    try:
        with Image.open(path) as opened:
            original_format = (opened.format or "").upper()
            if original_format not in SUPPORTED_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP のみ開けます。")
            metadata = safe_metadata(opened, options.remove_metadata)
            normalized = normalize_orientation(opened)
            transformed = apply_transforms(normalized, options.transforms)
            resized = resize_image(transformed, options)
            output_format = resolve_output_format(original_format, options.output_format)

            try:
                data, quality = encode_best_quality(
                    resized,
                    output_format,
                    options.target_bytes,
                    options.quality,
                    options.jpeg_background,
                    metadata,
                )
            except TargetSizeUnreachable:
                if options.target_bytes is None or options.resize_mode is not ResizeMode.NONE or output_format == "PNG":
                    raise
                # With no explicit resize request, preserve as much resolution as possible.
                working = resized
                fitted: tuple[bytes, int | None] | None = None
                for _ in range(12):
                    minimum_data, _ = encode_best_quality(
                        working, output_format, None, 1, options.jpeg_background, metadata
                    )
                    ratio = max(
                        0.5,
                        min(0.9, (options.target_bytes / max(1, len(minimum_data))) ** 0.5 * 0.95),
                    )
                    next_size = (max(1, round(working.width * ratio)), max(1, round(working.height * ratio)))
                    if next_size == working.size:
                        break
                    working = working.resize(next_size, Image.Resampling.LANCZOS)
                    try:
                        fitted = encode_best_quality(
                            working, output_format, options.target_bytes, options.quality,
                            options.jpeg_background, metadata
                        )
                        break
                    except TargetSizeUnreachable:
                        continue
                if fitted is None:
                    raise TargetSizeUnreachable("指定容量へ縮小できませんでした。")
                data, quality = fitted
                resized = working
    except ProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if not path.is_file():
            raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
        raise ProcessingError(f"画像処理に失敗しました: {path.name}") from exc

    result = ProcessedImage(data, resized.width, resized.height, output_format, quality, path)
    LOGGER.info("Conversion result: %s, %s bytes, quality=%s", path, len(data), quality)
    return result


def process_image_splits(
    path: Path,
    options: ProcessingOptions,
    split_options: ImageSplitOptions,
) -> list[ProcessedImage]:
    """Process once, then encode ordered equal panels without extra resizing."""
    if not split_options.enabled:
        return [process_image(path, options)]
    LOGGER.info(
        "Split conversion start: %s, direction=%s, count=%s",
        path,
        split_options.direction.value,
        split_options.count,
    )
    require_source_file(path)
    try:
        with Image.open(path) as opened:
            original_format = (opened.format or "").upper()
            if original_format not in SUPPORTED_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP のみ開けます。")
            metadata = safe_metadata(opened, options.remove_metadata)
            normalized = normalize_orientation(opened)
            transformed = apply_transforms(normalized, options.transforms)
            resized = resize_image(transformed, options)
            output_format = resolve_output_format(original_format, options.output_format)
            try:
                panels = split_image(
                    resized,
                    split_options.direction,
                    split_options.count,
                    split_options.boundaries,
                    (
                        MIN_SPLIT_PANEL_PIXELS
                        if split_options.boundaries is not None
                        else 1
                    ),
                )
            except ValueError as exc:
                axis = (
                    "幅"
                    if split_options.direction is SplitDirection.VERTICAL
                    else "高さ"
                )
                raise ProcessingError(
                    f"画像の{axis}が{split_options.count}分割に足りません。"
                ) from exc

            results: list[ProcessedImage] = []
            for panel in panels:
                data, quality = encode_best_quality(
                    panel,
                    output_format,
                    options.target_bytes,
                    options.quality,
                    options.jpeg_background,
                    metadata,
                )
                results.append(
                    ProcessedImage(
                        data,
                        panel.width,
                        panel.height,
                        output_format,
                        quality,
                        path,
                    )
                )
    except ProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if not path.is_file():
            raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
        raise ProcessingError(f"画像処理に失敗しました: {path.name}") from exc

    LOGGER.info("Split conversion result: %s, %s panels", path, len(results))
    return results


def write_processed(result: ProcessedImage, destination: Path, preserve_timestamp: bool) -> None:
    created = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            created = True
            output.write(result.data)
        if preserve_timestamp:
            source_stat = result.source.stat()
            os.utime(destination, (source_stat.st_atime, source_stat.st_mtime))
    except (OSError, PermissionError) as exc:
        if created:
            destination.unlink(missing_ok=True)
        if preserve_timestamp and not result.source.is_file():
            raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
        raise ProcessingError(f"保存できません: {destination}") from exc
