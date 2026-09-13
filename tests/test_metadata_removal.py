from pathlib import Path

import pytest
from PIL import Image, ImageOps, PngImagePlugin

from quick_processing_tool.errors import ProcessingError
from quick_processing_tool.metadata_removal import (
    collect_supported_image_paths,
    metadata_output_folder,
    remove_image_metadata,
)
from quick_processing_tool.metadata_removal_ui import MetadataRemovalWorker


def _jpeg_with_private_metadata(path: Path) -> None:
    image = Image.new("RGB", (30, 20), "#336699")
    exif = Image.Exif()
    exif[270] = "private description"
    exif[271] = "private camera"
    exif[274] = 6
    image.save(path, "JPEG", exif=exif, comment=b"private comment")


def test_jpeg_metadata_is_removed_and_orientation_stays_displayable(tmp_path: Path) -> None:
    source = tmp_path / "oriented.jpg"
    _jpeg_with_private_metadata(source)
    output = remove_image_metadata(source, tmp_path / "Processed")

    with Image.open(source) as original, Image.open(output.output) as cleaned:
        assert len(original.getexif()) > 0
        assert len(cleaned.getexif()) == 0
        assert cleaned.size == ImageOps.exif_transpose(original).size
        assert cleaned.info.get("comment") is None


def test_png_text_is_removed_without_changing_alpha_pixels(tmp_path: Path) -> None:
    source = tmp_path / "private.png"
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("prompt", "private generation parameters")
    image = Image.new("RGBA", (12, 8), (10, 20, 30, 40))
    image.putpixel((5, 4), (200, 100, 50, 0))
    icc_profile = b"display-profile"
    image.save(source, "PNG", pnginfo=pnginfo, icc_profile=icc_profile)
    output = remove_image_metadata(source, tmp_path / "Processed")

    with Image.open(output.output) as cleaned:
        assert cleaned.size == image.size
        assert cleaned.convert("RGBA").tobytes() == image.tobytes()
        assert "prompt" not in cleaned.info
        assert cleaned.info.get("icc_profile") == icc_profile


def test_webp_metadata_is_removed_and_image_reopens(tmp_path: Path) -> None:
    source = tmp_path / "private.webp"
    exif = Image.Exif()
    exif[270] = "private description"
    Image.new("RGBA", (18, 11), (40, 80, 120, 160)).save(
        source,
        "WEBP",
        exif=exif.tobytes(),
        xmp=b"<xmp>private generation parameters</xmp>",
        lossless=True,
    )
    output = remove_image_metadata(source, tmp_path / "Processed")

    with Image.open(output.output) as cleaned:
        cleaned.load()
        assert cleaned.size == (18, 11)
        assert not cleaned.info.get("exif")
        assert not cleaned.info.get("xmp")


def test_batch_saves_each_file_keeps_sources_and_uses_collision_suffixes(tmp_path: Path) -> None:
    sources = []
    for index in range(100):
        source = tmp_path / f"image_{index:03d}.png"
        Image.new("RGBA", (4, 3), (index, 20, 30, 40)).save(source, "PNG")
        sources.append(source)
    collision_folder = tmp_path / "Processed"
    collision_folder.mkdir()
    Image.new("RGB", (1, 1), "white").save(collision_folder / "image_000.png")

    outputs = [remove_image_metadata(source, collision_folder).output for source in sources]

    assert len(outputs) == 100
    assert (collision_folder / "image_000_2.png") in outputs
    assert all(source.is_file() for source in sources)
    assert all(output.is_file() and output.stat().st_size > 0 for output in outputs)


def test_folder_collection_is_direct_only_and_ignores_non_images(tmp_path: Path) -> None:
    direct_png = tmp_path / "direct.png"
    direct_jpeg = tmp_path / "direct.jpg"
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_png = nested / "nested.png"
    Image.new("RGB", (1, 1), "blue").save(direct_png)
    Image.new("RGB", (1, 1), "green").save(direct_jpeg)
    Image.new("RGB", (1, 1), "red").save(nested_png)
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    assert collect_supported_image_paths([tmp_path]) == [direct_jpeg.resolve(), direct_png.resolve()]


def test_destination_mode_and_partial_failure_are_isolated(tmp_path: Path) -> None:
    source = tmp_path / "valid.png"
    Image.new("RGB", (4, 4), "blue").save(source)
    assert metadata_output_folder(source, "Processed") == tmp_path / "Processed"
    assert metadata_output_folder(source, "Same folder") == tmp_path
    with pytest.raises(ProcessingError):
        metadata_output_folder(source, "Custom folder")

    statuses: list[tuple[int, str, str]] = []
    completion: list[tuple[int, int, bool]] = []
    worker = MetadataRemovalWorker([source, tmp_path / "missing.png"], "Processed", None)
    worker.file_status.connect(lambda row, status, detail: statuses.append((row, status, detail)))
    worker.finished.connect(lambda succeeded, failed, cancelled: completion.append((succeeded, failed, cancelled)))
    worker.run()

    assert completion == [(1, 1, False)]
    assert statuses[-1][1] == "失敗"
    assert (tmp_path / "Processed" / "valid.png").is_file()
