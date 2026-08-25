from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops
from PySide6.QtWidgets import QApplication

from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.editing.canvas import place_on_canvas
from quick_processing_tool.editing.filters import apply_filter
from quick_processing_tool.editing.models import (
    CanvasBackground,
    CanvasSettings,
    EditOutputFormat,
    EditSettings,
    FilterPreset,
    PlacementMode,
    TextPosition,
    TextSettings,
    TransparencySettings,
)
from quick_processing_tool.editing.service import EditProcessingError, EditService
from quick_processing_tool.editing.text import draw_text
from quick_processing_tool.editing.transparency import apply_color_transparency


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_filter_and_transparency_preserve_dimensions_and_alpha() -> None:
    source = Image.new("RGBA", (24, 16), (255, 255, 255, 255))
    source.putpixel((12, 8), (200, 20, 30, 255))

    gray = apply_filter(source, FilterPreset.GRAYSCALE)
    assert gray.size == source.size
    assert gray.getpixel((12, 8))[0] == gray.getpixel((12, 8))[1]

    transparent = apply_color_transparency(
        source,
        TransparencySettings(enabled=True, target_color=(255, 255, 255), tolerance=0, edge_softness=0),
    )
    assert transparent.getpixel((0, 0))[3] == 0
    assert transparent.getpixel((12, 8))[3] == 255


def test_transparency_tolerance_and_softness() -> None:
    source = Image.new("RGBA", (3, 1), (255, 255, 255, 255))
    source.putpixel((1, 0), (245, 245, 245, 255))
    source.putpixel((2, 0), (220, 220, 220, 255))

    tolerant = apply_color_transparency(
        source,
        TransparencySettings(enabled=True, target_color=(255, 255, 255), tolerance=20, edge_softness=0),
    )
    assert tolerant.getpixel((1, 0))[3] == 0
    assert tolerant.getpixel((2, 0))[3] == 255

    softened = apply_color_transparency(
        source,
        TransparencySettings(enabled=True, target_color=(255, 255, 255), tolerance=0, edge_softness=40),
    )
    assert 0 < softened.getpixel((1, 0))[3] < 255


def test_canvas_fit_and_fill_keep_requested_dimensions() -> None:
    source = Image.new("RGBA", (80, 40), (10, 20, 30, 255))
    fit = place_on_canvas(source, CanvasSettings(32, 32, PlacementMode.FIT))
    fill = place_on_canvas(source, CanvasSettings(32, 32, PlacementMode.FILL))
    assert fit.size == (32, 32)
    assert fill.size == (32, 32)
    assert fit.getpixel((0, 0))[3] == 0
    assert fill.getpixel((0, 0))[3] == 255


def test_japanese_text_with_outline_changes_pixels(qt_app: QApplication) -> None:
    source = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    settings = TextSettings(
        enabled=True,
        text="おはよう\n世界",
        font_family="Meiryo",
        font_size=64,
        bold=True,
        outline_enabled=True,
        outline_width=5,
        position=TextPosition.BOTTOM_CENTER,
    )
    rendered = draw_text(source, settings)
    assert rendered.size == source.size
    assert ImageChops.difference(source, rendered).getbbox() is not None


def test_edit_service_saves_verified_alpha_and_collision_safe_names(tmp_path: Path) -> None:
    source_path = tmp_path / "image.png"
    source = Image.new("RGBA", (40, 20), (255, 255, 255, 255))
    source.putpixel((20, 10), (20, 30, 40, 255))
    source.save(source_path)

    settings = EditSettings(
        transparency=TransparencySettings(
            enabled=True, target_color=(255, 255, 255), tolerance=0, edge_softness=0
        ),
        canvas=CanvasSettings(32, 32, PlacementMode.FIT),
    )
    service = EditService()
    first = service.export(source_path, tmp_path, settings, EditOutputFormat.PNG)
    second = service.export(source_path, tmp_path, settings, EditOutputFormat.PNG)

    assert first.output_path.name == "image_edited.png"
    assert second.output_path.name == "image_edited_2.png"
    with Image.open(first.output_path) as reopened:
        reopened.load()
        assert reopened.size == (32, 32)
        assert "A" in reopened.getbands()
        assert reopened.getpixel((0, 0))[3] == 0


def test_quick_edit_page_supports_undo_redo_and_reset(qt_app: QApplication, tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    Image.new("RGB", (32, 24), "red").save(source_path)

    page = QuickEditPage()
    page.load_image(source_path)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.SEPIA.value))
    qt_app.processEvents()
    assert page.undo_button.isEnabled()
    page.undo()
    assert page.settings().filter_preset is FilterPreset.NONE
    page.redo()
    assert page.settings().filter_preset is FilterPreset.SEPIA
    page.reset_edits()
    reset = page.settings()
    assert reset.filter_preset is FilterPreset.NONE
    assert reset.canvas == EditSettings().canvas
    assert not reset.text.enabled
def test_quick_edit_drop_zone_covers_preview_viewport(qt_app: QApplication) -> None:
    page = QuickEditPage()
    assert page.drop_zone.acceptDrops()
    assert page.drop_zone.preview.viewport().acceptDrops()

@pytest.mark.parametrize("preset", list(FilterPreset))
def test_all_filter_presets_return_same_size(preset: FilterPreset) -> None:
    source = Image.new("RGBA", (40, 24), (80, 120, 180, 200))
    rendered = apply_filter(source, preset)
    assert rendered.size == source.size
    assert "A" in rendered.getbands()


@pytest.mark.parametrize("position", list(TextPosition))
def test_all_text_positions_stay_inside_canvas(qt_app: QApplication, position: TextPosition) -> None:
    source = Image.new("RGBA", (240, 180), (0, 0, 0, 0))
    rendered = draw_text(
        source,
        TextSettings(enabled=True, text="位置", font_family="Meiryo", font_size=34, position=position, safe_margin=8),
    )
    assert rendered.size == source.size
    assert rendered.getchannel("A").getbbox() is not None


@pytest.mark.parametrize(
    "background, expected",
    [
        (CanvasBackground.TRANSPARENT, (0, 0, 0, 0)),
        (CanvasBackground.WHITE, (255, 255, 255, 255)),
        (CanvasBackground.BLACK, (0, 0, 0, 255)),
        (CanvasBackground.CUSTOM, (12, 34, 56, 255)),
    ],
)
def test_canvas_backgrounds_and_padding(background: CanvasBackground, expected: tuple[int, int, int, int]) -> None:
    source = Image.new("RGBA", (20, 10), (100, 120, 140, 255))
    settings = CanvasSettings(40, 40, PlacementMode.FIT, 10, background, (12, 34, 56, 255))
    rendered = place_on_canvas(source, settings)
    assert rendered.size == (40, 40)
    assert rendered.getpixel((0, 0)) == expected


@pytest.mark.parametrize("output_format, expected_alpha", [
    (EditOutputFormat.JPEG, False),
    (EditOutputFormat.WEBP, True),
])
def test_service_output_format_contracts(
    tmp_path: Path, output_format: EditOutputFormat, expected_alpha: bool
) -> None:
    source_path = tmp_path / "source.png"
    source = Image.new("RGBA", (20, 12), (40, 80, 120, 255))
    source.putpixel((0, 0), (40, 80, 120, 0))
    source.save(source_path)
    result = EditService().export(source_path, tmp_path, EditSettings(), output_format)
    with Image.open(result.output_path) as reopened:
        reopened.verify()
    with Image.open(result.output_path) as reopened:
        reopened.load()
        assert reopened.format == output_format.value
        assert reopened.size == (20, 12)
        assert ("A" in reopened.getbands()) is expected_alpha


def test_service_rejects_mislabeled_decoded_format(tmp_path: Path) -> None:
    source_path = tmp_path / "looks-like-png.png"
    Image.new("RGB", (12, 8), "green").save(source_path, format="GIF")
    with pytest.raises(EditProcessingError):
        EditService().export(source_path, tmp_path, EditSettings(), EditOutputFormat.PNG)


def test_ui_rejects_mislabeled_decoded_format(
    qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "looks-like-png.png"
    Image.new("RGB", (12, 8), "green").save(source_path, format="GIF")
    monkeypatch.setattr("quick_processing_tool.edit_ui.QMessageBox.warning", lambda *args: None)
    page = QuickEditPage()
    page.load_image(source_path)
    assert page.source_path is None


def test_thumbnail_processing_locks_and_restores_edit_tab(qt_app: QApplication) -> None:
    from quick_processing_tool.ui import MainWindow

    window = MainWindow()
    window._thumbnail_processing_changed(True)
    assert not window.navigation.isTabEnabled(window.image_edit_tab)
    window._thumbnail_processing_changed(False)
    assert window.navigation.isTabEnabled(window.image_edit_tab)


def test_render_does_not_mutate_original_and_redo_is_invalidated(
    qt_app: QApplication, tmp_path: Path
) -> None:
    source_path = tmp_path / "source.png"
    original = Image.new("RGB", (32, 24), "red")
    original.save(source_path)
    page = QuickEditPage()
    page.load_image(source_path)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.SEPIA.value))
    qt_app.processEvents()
    page.undo()
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.DARK.value))
    qt_app.processEvents()
    assert not page.redo_button.isEnabled()
    assert page.settings().filter_preset is FilterPreset.DARK
    with Image.open(source_path) as unchanged:
        unchanged.load()
        assert unchanged.convert("RGB").tobytes() == original.tobytes()