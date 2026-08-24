from io import BytesIO
import os
from pathlib import Path

from PIL import Image

from quick_processing_tool.models import OutputFormat, ProcessingOptions, Transform
from quick_processing_tool.pipeline import process_image, write_processed
from quick_processing_tool.processors.transform import apply_transforms


def test_exif_is_removed(tmp_path: Path) -> None:
    path = tmp_path / "metadata.jpg"
    image = Image.new("RGB", (30, 20), "blue")
    exif = Image.Exif()
    exif[270] = "private comment"
    exif[271] = "camera maker"
    image.save(path, "JPEG", exif=exif)

    result = process_image(path, ProcessingOptions(output_format=OutputFormat.JPEG, remove_metadata=True))
    with Image.open(BytesIO(result.data)) as output:
        assert len(output.getexif()) == 0


def test_rotate_and_flip_pixel_positions() -> None:
    image = Image.new("RGB", (2, 3), "black")
    image.putpixel((0, 0), (255, 0, 0))
    right = apply_transforms(image, [Transform.ROTATE_RIGHT])
    assert right.size == (3, 2)
    assert right.getpixel((2, 0)) == (255, 0, 0)

    horizontal = apply_transforms(image, [Transform.FLIP_HORIZONTAL])
    assert horizontal.getpixel((1, 0)) == (255, 0, 0)
    vertical = apply_transforms(image, [Transform.FLIP_VERTICAL])
    assert vertical.getpixel((0, 2)) == (255, 0, 0)


def test_rotate_180() -> None:
    image = Image.new("RGB", (2, 2), "black")
    image.putpixel((0, 0), (255, 0, 0))
    rotated = apply_transforms(image, [Transform.ROTATE_180])
    assert rotated.getpixel((1, 1)) == (255, 0, 0)

def test_exif_orientation_is_normalized_on_save(tmp_path: Path) -> None:
    path = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (2, 3), "black")
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, "JPEG", quality=100, exif=exif)

    result = process_image(path, ProcessingOptions(output_format=OutputFormat.PNG))
    with Image.open(BytesIO(result.data)) as output:
        assert output.size == (3, 2)
        assert 274 not in output.getexif()


def test_write_preserves_modified_timestamp(rgb_image_path: Path, tmp_path: Path) -> None:
    expected_mtime = 1_700_000_000
    os.utime(rgb_image_path, (expected_mtime, expected_mtime))
    result = process_image(rgb_image_path, ProcessingOptions(output_format=OutputFormat.PNG))
    destination = tmp_path / "out.png"
    write_processed(result, destination, preserve_timestamp=True)
    assert destination.stat().st_mtime == expected_mtime
