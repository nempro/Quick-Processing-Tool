from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import ProcessingError, UnsupportedImageError
from .image_workspace import require_source_file
from .models import OutputFormat, ProcessedImage, ProcessingOptions
from .pipeline import SUPPORTED_FORMATS, resolve_output_format
from .processors.crop import crop_image
from .processors.encode import encode_best_quality
from .processors.metadata import safe_metadata
from .processors.resize import resize_image
from .processors.transform import apply_transforms, normalize_orientation


MIN_VISIBLE_OVERLAP_PIXELS = 16


class MergeDirection(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    GRID = "grid"


class MergeSizeMode(str, Enum):
    ORIGINAL = "original"
    MATCH_HEIGHT = "match_height"
    MATCH_WIDTH = "match_width"
    CELL_FIT = "cell_fit"


class MergeAlignment(str, Enum):
    START = "start"
    CENTER = "center"
    END = "end"


@dataclass(frozen=True, slots=True)
class ImageMergeOptions:
    direction: MergeDirection = MergeDirection.HORIZONTAL
    size_mode: MergeSizeMode = MergeSizeMode.ORIGINAL
    alignment: MergeAlignment = MergeAlignment.CENTER
    gap: int = 0
    # None is transparent; RGB tuples are opaque canvas backgrounds.
    background: tuple[int, int, int] | None = (255, 255, 255)
    columns: int = 2

    def __post_init__(self) -> None:
        if not -100 <= self.gap <= 100:
            raise ValueError("merge gap must be between -100 and 100")
        if self.direction is MergeDirection.GRID and self.gap < 0:
            raise ValueError("grid merge gap must not be negative")
        if self.columns not in (2, 3):
            raise ValueError("grid columns must be 2 or 3")
        if self.background is not None and (
            len(self.background) != 3 or any(not 0 <= value <= 255 for value in self.background)
        ):
            raise ValueError("merge background must be RGB or transparent")


def max_merge_images(options: ImageMergeOptions) -> int:
    return 9 if options.direction is MergeDirection.GRID else 6


def _validate_count(count: int, options: ImageMergeOptions) -> None:
    if not 2 <= count <= max_merge_images(options):
        raise ValueError(f"merge requires between 2 and {max_merge_images(options)} images")


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def _resized(images: list[Image.Image], options: ImageMergeOptions) -> list[Image.Image]:
    if options.size_mode is MergeSizeMode.MATCH_HEIGHT:
        target = max(image.height for image in images)
        return [
            image if image.height == target else image.resize(
                (max(1, round(image.width * target / image.height)), target),
                Image.Resampling.LANCZOS,
            )
            for image in images
        ]
    if options.size_mode is MergeSizeMode.MATCH_WIDTH:
        target = max(image.width for image in images)
        return [
            image if image.width == target else image.resize(
                (target, max(1, round(image.height * target / image.width))),
                Image.Resampling.LANCZOS,
            )
            for image in images
        ]
    if options.size_mode is MergeSizeMode.CELL_FIT and options.direction is MergeDirection.GRID:
        cell_width = max(image.width for image in images)
        cell_height = max(image.height for image in images)
        resized: list[Image.Image] = []
        for image in images:
            scale = min(cell_width / image.width, cell_height / image.height)
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            resized.append(image if image.size == size else image.resize(size, Image.Resampling.LANCZOS))
        return resized
    return images


def _aligned_offset(space: int, alignment: MergeAlignment) -> int:
    if alignment is MergeAlignment.START:
        return 0
    if alignment is MergeAlignment.END:
        return space
    return space // 2


def _resolved_linear_gap(sizes: list[tuple[int, int]], options: ImageMergeOptions) -> int:
    """Keep every linear-merge input visibly represented after overlap."""
    if options.gap >= 0:
        return options.gap
    primary_sizes = [
        width if options.direction is MergeDirection.HORIZONTAL else height
        for width, height in sizes
    ]
    minimum_primary = min(primary_sizes)
    minimum_visible = min(MIN_VISIBLE_OVERLAP_PIXELS, minimum_primary)
    return max(options.gap, minimum_visible - minimum_primary)


def merged_dimensions(sizes: list[tuple[int, int]], options: ImageMergeOptions) -> tuple[int, int]:
    """Return the exact canvas size for already-resolved input sizes."""
    _validate_count(len(sizes), options)
    if options.direction is MergeDirection.GRID:
        rows = (len(sizes) + options.columns - 1) // options.columns
        cell_width = max(width for width, _ in sizes)
        cell_height = max(height for _, height in sizes)
        return (
            options.columns * cell_width + options.gap * (options.columns - 1),
            rows * cell_height + options.gap * (rows - 1),
        )
    gap = _resolved_linear_gap(sizes, options)
    if options.direction is MergeDirection.HORIZONTAL:
        return sum(width for width, _ in sizes) + gap * (len(sizes) - 1), max(
            height for _, height in sizes
        )
    return max(width for width, _ in sizes), sum(height for _, height in sizes) + gap * (
        len(sizes) - 1
    )


def merge_images(images: list[Image.Image], options: ImageMergeOptions) -> Image.Image:
    """Compose ordered images without writing an artifact."""
    _validate_count(len(images), options)
    prepared = _resized(images, options)
    canvas_size = merged_dimensions([image.size for image in prepared], options)
    preserve_alpha = options.background is None or any(_has_alpha(image) for image in prepared)
    positions: list[tuple[int, int]] = []
    if options.direction is MergeDirection.GRID:
        cell_width = max(image.width for image in prepared)
        cell_height = max(image.height for image in prepared)
        for index, image in enumerate(prepared):
            row, column = divmod(index, options.columns)
            left = column * (cell_width + options.gap)
            top = row * (cell_height + options.gap)
            positions.append((
                left + _aligned_offset(cell_width - image.width, options.alignment),
                top + _aligned_offset(cell_height - image.height, options.alignment),
            ))
    else:
        gap = _resolved_linear_gap([image.size for image in prepared], options)
        offset = 0
        for image in prepared:
            if options.direction is MergeDirection.HORIZONTAL:
                cross = canvas_size[1] - image.height
                positions.append((offset, _aligned_offset(cross, options.alignment)))
                offset += image.width + gap
            else:
                cross = canvas_size[0] - image.width
                positions.append((_aligned_offset(cross, options.alignment), offset))
                offset += image.height + gap

    if not preserve_alpha:
        canvas = Image.new("RGB", canvas_size, options.background)
        for image, position in zip(prepared, positions, strict=True):
            canvas.paste(image.convert("RGB"), position)
        return canvas

    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    if options.background is not None:
        coverage = Image.new("L", canvas_size, 0)
        for image, (left, top) in zip(prepared, positions, strict=True):
            coverage.paste(255, (left, top, left + image.width, top + image.height))
        canvas.paste((*options.background, 255), mask=coverage.point(lambda value: 255 - value))
    for image, position in zip(prepared, positions, strict=True):
        canvas.alpha_composite(image.convert("RGBA"), dest=position)
    return canvas

def load_merge_image(path: Path, processing: ProcessingOptions) -> tuple[Image.Image, str, dict[str, object]]:
    """Load one source through Quick's non-encoding transformation order."""
    require_source_file(path)
    try:
        with Image.open(path) as opened:
            original_format = (opened.format or "").upper()
            if original_format not in SUPPORTED_FORMATS:
                raise UnsupportedImageError("PNG / JPEG / WebP のみ開けます。")
            metadata = safe_metadata(opened, processing.remove_metadata)
            normalized = normalize_orientation(opened)
            image = resize_image(
                apply_transforms(crop_image(normalized, processing.crop_rect), processing.transforms),
                processing,
            )
            return image.copy(), original_format, metadata
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ProcessingError(f"画像処理に失敗しました: {path.name}") from exc


def process_image_merge(
    paths: list[Path], processing: ProcessingOptions, merge: ImageMergeOptions
) -> ProcessedImage:
    """Apply Quick transforms to ordered inputs, then encode one merged image."""
    if not 2 <= len(paths) <= max_merge_images(merge):
        raise ProcessingError(f"画像結合には2〜{max_merge_images(merge)}枚の画像が必要です。")
    loaded = [load_merge_image(path, processing) for path in paths]
    image = merge_images([entry[0] for entry in loaded], merge)
    output_format = resolve_output_format(loaded[0][1], processing.output_format)
    # The first image is the deterministic "元の形式" and metadata source.
    data, quality = encode_best_quality(
        image,
        output_format,
        processing.target_bytes,
        processing.quality,
        processing.jpeg_background,
        loaded[0][2],
    )
    return ProcessedImage(data, image.width, image.height, output_format, quality, paths[0])


def build_merge_preview(
    paths: list[Path], processing: ProcessingOptions, merge: ImageMergeOptions, max_edge: int = 2400
) -> Image.Image:
    """Build an in-memory preview using the exact merge transform order."""
    loaded = [load_merge_image(path, processing) for path in paths]
    image = merge_images([entry[0] for entry in loaded], merge)
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return image
