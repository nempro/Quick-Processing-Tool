from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from quick_processing_tool.errors import TargetSizeUnreachable
from quick_processing_tool.models import OutputFormat, ProcessingOptions, ResizeMode
from quick_processing_tool.pipeline import process_image
from quick_processing_tool.processors.encode import encode_once


@pytest.mark.parametrize("output_format,target", [(OutputFormat.JPEG, 55_000), (OutputFormat.WEBP, 45_000)])
def test_target_size_and_maximum_quality(rgb_image_path: Path, output_format: OutputFormat, target: int) -> None:
    options = ProcessingOptions(output_format=output_format, target_bytes=target, quality=95)
    result = process_image(rgb_image_path, options)
    assert result.size_bytes <= target
    assert result.quality is not None
    if result.quality < options.quality:
        with Image.open(rgb_image_path) as source:
            next_quality = encode_once(source, result.format, result.quality + 1, options.jpeg_background)
        assert len(next_quality) > target


def test_explicit_resize_is_not_changed_when_target_is_unreachable(rgb_image_path: Path) -> None:
    options = ProcessingOptions(
        resize_mode=ResizeMode.DIMENSIONS,
        width=640,
        height=480,
        output_format=OutputFormat.JPEG,
        target_bytes=100,
    )
    with pytest.raises(TargetSizeUnreachable):
        process_image(rgb_image_path, options)


def test_png_target_does_not_silently_change_format(rgba_image_path: Path) -> None:
    options = ProcessingOptions(output_format=OutputFormat.PNG, target_bytes=50)
    with pytest.raises(TargetSizeUnreachable, match="JPEGまたはWebP"):
        process_image(rgba_image_path, options)


def test_no_resize_may_reduce_resolution_to_reach_target(rgb_image_path: Path) -> None:
    options = ProcessingOptions(output_format=OutputFormat.JPEG, target_bytes=2_000)
    result = process_image(rgb_image_path, options)
    assert result.size_bytes <= 2_000
    assert (result.width, result.height) != (640, 480)

def test_tiny_image_with_impossible_target_has_user_facing_error(tmp_path: Path) -> None:
    path = tmp_path / "tiny.png"
    Image.new("RGB", (1, 1), "red").save(path)
    options = ProcessingOptions(output_format=OutputFormat.JPEG, target_bytes=1)
    with pytest.raises(TargetSizeUnreachable, match="指定容量"):
        process_image(path, options)
