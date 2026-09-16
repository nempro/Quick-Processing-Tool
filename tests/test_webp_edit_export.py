from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from quick_processing_tool.editing.models import (
    ColorAdjustmentSettings,
    EditOutputFormat,
    EditSettings,
    HandDrawSettings,
    HandPoint,
    HandStroke,
    MosaicSettings,
    MosaicStroke,
)
from quick_processing_tool.editing.service import EditProcessingError, EditService


def _save_webp(path: Path, image: Image.Image) -> None:
    image.save(path, format="WEBP", quality=95, method=6)


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or (
        image.mode == "P" and "transparency" in image.info
    )


@pytest.mark.parametrize(
    ("mode", "color", "size", "settings", "expected_alpha"),
    [
        ("RGB", (30, 70, 140), (1078, 2048), EditSettings(), False),
        (
            "RGBA",
            (30, 70, 140, 255),
            (64, 48),
            EditSettings(color_adjustments=ColorAdjustmentSettings(brightness=25, hue=30)),
            False,
        ),
        (
            "RGBA",
            (30, 70, 140, 0),
            (64, 48),
            EditSettings(
                hand_draw=HandDrawSettings(
                    strokes=(HandStroke(points=(HandPoint(32, 24),), color=(240, 40, 100, 180), width=8),),
                    base_width=64,
                    base_height=48,
                )
            ),
            True,
        ),
        (
            "RGBA",
            (30, 70, 140, 0),
            (64, 48),
            EditSettings(
                mosaic=MosaicSettings(
                    strokes=(MosaicStroke(points=(HandPoint(32, 24),), width=16, block_size=4),),
                    base_width=64,
                    base_height=48,
                )
            ),
            True,
        ),
    ],
)
def test_webp_same_format_export_reopens_with_expected_alpha(
    tmp_path: Path,
    mode: str,
    color: tuple[int, ...],
    size: tuple[int, int],
    settings: EditSettings,
    expected_alpha: bool,
) -> None:
    source = tmp_path / "source.webp"
    _save_webp(source, Image.new(mode, size, color))

    result = EditService().export(source, tmp_path, settings, EditOutputFormat.SAME)
    assert result.output_format == "WEBP"
    assert result.width == size[0]
    assert result.height == size[1]
    assert result.has_alpha is expected_alpha
    assert result.output_path.is_file()
    assert result.output_path.stat().st_size > 0

    with Image.open(result.output_path) as reopened:
        reopened.load()
        assert reopened.format == "WEBP"
        assert reopened.size == size
        assert _has_alpha(reopened) is expected_alpha
def test_webp_export_rejects_actual_alpha_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "transparent.webp"
    _save_webp(source, Image.new("RGBA", (32, 24), (30, 70, 140, 0)))

    def encode_without_alpha(image: Image.Image, *_args, **_kwargs) -> bytes:
        stream = BytesIO()
        image.convert("RGB").save(stream, format="WEBP")
        return stream.getvalue()

    monkeypatch.setattr(
        "quick_processing_tool.editing.service.encode_once",
        encode_without_alpha,
    )
    with pytest.raises(
        EditProcessingError,
        match="透明情報を保持できませんでした",
    ):
        EditService().export(source, tmp_path, EditSettings(), EditOutputFormat.SAME)
    assert not (tmp_path / "transparent_edited.webp").exists()
