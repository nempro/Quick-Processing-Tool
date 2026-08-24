from io import BytesIO
from pathlib import Path

from PIL import Image

from quick_processing_tool.models import OutputFormat, ProcessingOptions
from quick_processing_tool.pipeline import process_image


def test_png_to_jpeg_flattens_transparency(rgba_image_path: Path) -> None:
    result = process_image(
        rgba_image_path,
        ProcessingOptions(output_format=OutputFormat.JPEG, jpeg_background=(0, 0, 0)),
    )
    with Image.open(BytesIO(result.data)) as output:
        assert output.format == "JPEG"
        assert output.mode == "RGB"
        assert output.getpixel((0, 0))[0] < 10


def test_jpeg_to_png(rgb_image_path: Path) -> None:
    result = process_image(rgb_image_path, ProcessingOptions(output_format=OutputFormat.PNG))
    with Image.open(BytesIO(result.data)) as output:
        assert output.format == "PNG"


def test_png_transparency_is_preserved(rgba_image_path: Path) -> None:
    result = process_image(rgba_image_path, ProcessingOptions(output_format=OutputFormat.PNG))
    with Image.open(BytesIO(result.data)) as output:
        assert output.mode == "RGBA"
        assert output.getpixel((0, 0))[3] == 0
        assert output.getpixel((20, 20))[3] == 128


def test_webp_transparency_is_preserved(rgba_image_path: Path) -> None:
    result = process_image(rgba_image_path, ProcessingOptions(output_format=OutputFormat.WEBP))
    with Image.open(BytesIO(result.data)) as output:
        assert output.format == "WEBP"
        assert output.convert("RGBA").getpixel((0, 0))[3] == 0
