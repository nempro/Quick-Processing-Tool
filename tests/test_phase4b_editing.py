from PIL import Image
import time
from pathlib import Path
from quick_processing_tool.editing.renderer import render_edit
from quick_processing_tool.editing.palette import rgba_digest
from PIL import ImageDraw
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])

from quick_processing_tool.editing.line_art import EDGE_METHOD, apply_line_art, compare_edge_candidates, edge_mask_candidate
from quick_processing_tool.editing.models import (
    FilterPreset,
    LineArtAmount,
    LineArtBackground,
    LineArtSettings,
    PaletteSettings,
    StickerSettings,
)
from quick_processing_tool.editing.palette import apply_palette_mapping, extract_palette, mapping_for_palette
from quick_processing_tool.editing.sticker import apply_sticker


def test_palette_ignores_transparent_pixels_and_recolors_without_alpha_halo() -> None:
    image = Image.new("RGBA", (4, 1), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 0))
    mapping = extract_palette(image, 5)
    assert len(mapping.palette) == 2
    recolored = apply_palette_mapping(image, mapping, ((10, 20, 30),) * len(mapping.palette))
    assert recolored.getpixel((0, 0))[:3] == (10, 20, 30)
    assert recolored.getpixel((2, 0)) == (0, 0, 255, 0)


def test_sticker_outline_is_behind_foreground_and_rejects_opaque() -> None:
    image = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    image.putpixel((4, 4), (20, 40, 60, 255))
    result = apply_sticker(image, StickerSettings(enabled=True, outline_width=2))
    assert result.getpixel((4, 4)) == (20, 40, 60, 255)
    assert result.getpixel((2, 4))[:3] == (255, 255, 255)
    try:
        apply_sticker(Image.new("RGBA", (2, 2), (1, 2, 3, 255)), StickerSettings(enabled=True))
    except ValueError as exc:
        assert "ステッカー化" in str(exc)
    else:
        raise AssertionError("opaque sticker input must provide guidance")


def test_line_art_presets_keep_dimensions_and_background_contract() -> None:
    image = Image.new("RGBA", (12, 8), (255, 255, 255, 255))
    for y in range(8):
        image.putpixel((6, y), (0, 0, 0, 255))
    for amount in LineArtAmount:
        result = apply_line_art(
            image,
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.WHITE),
        )
        assert result.size == image.size
        assert result.mode == "RGBA"
        assert result.getpixel((0, 0))[3] == 255
        assert any(result.getpixel((x, y))[:3] != (255, 255, 255) for x in range(12) for y in range(8))


def test_phase4b_settings_defaults_are_non_destructive() -> None:
    settings = PaletteSettings()
    assert settings.color_count == 6
    assert not settings.enabled and not settings.quantize_enabled
    assert not StickerSettings().enabled
    assert not LineArtSettings().enabled


def test_cached_palette_mapping_recolor_changes_values_only() -> None:
    image = Image.new("RGBA", (3, 1), (250, 0, 0, 255))
    image.putpixel((1, 0), (0, 250, 0, 160))
    mapping = extract_palette(image, 6)
    scaled = image.resize((6, 2), Image.Resampling.NEAREST)
    stable = mapping_for_palette(scaled, mapping.palette)
    result = apply_palette_mapping(scaled, stable, tuple((1, 2, 3) for _ in mapping.palette))
    assert result.size == scaled.size
    assert result.getpixel((2, 0))[3] == 160
    assert result.getpixel((0, 0))[:3] == (1, 2, 3)


def test_palette_counts_and_transparent_only_contract() -> None:
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    assert extract_palette(image, 5).palette == ()
    for count in (5, 6, 8):
        source = Image.new("RGBA", (count, 1))
        for x in range(count):
            source.putpixel((x, 0), ((x * 37) % 256, (x * 61) % 256, (x * 89) % 256, 255))
        mapping = extract_palette(source, count)
        assert 1 <= len(mapping.palette) <= count
        assert len(mapping.indices) == count


def test_sticker_shadow_and_line_art_transparency_preserve_dimensions() -> None:
    source = Image.new("RGBA", (20, 14), (0, 0, 0, 0))
    source.paste((200, 20, 30, 220), (5, 4, 14, 10))
    sticker = apply_sticker(source, StickerSettings(enabled=True, outline_width=3, shadow_enabled=True))
    assert sticker.size == source.size and sticker.mode == "RGBA"
    line = apply_line_art(source, LineArtSettings(enabled=True, background=LineArtBackground.TRANSPARENT))
    assert line.size == source.size and line.mode == "RGBA"


def test_material_ui_defaults_and_vertical_scroll(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    page.resize(900, 620)
    page.show()
    qt_app.processEvents()
    assert len(page.sections) == 5
    assert page.settings().palette.color_count == 6
    assert not page.settings().sticker.enabled
    assert not page.settings().line_art.enabled
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    page.material_section.toggle.click()
    qt_app.processEvents()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    page.close()


def test_material_controls_round_trip_settings(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings
    page = QuickEditPage()
    page.sticker_enabled.setChecked(True)
    page.sticker_outline_width_spin.setValue(12)
    page.line_art_enabled.setChecked(True)
    page.palette_enabled.setChecked(True)
    page.palette_quantize_enabled.setChecked(True)
    page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(8))
    settings = page.settings()
    assert settings.sticker.outline_width == 12
    assert settings.line_art.enabled
    assert settings.palette.quantize_enabled and settings.palette.color_count == 8
    page.apply_settings(EditSettings())
    assert not page.settings().sticker.enabled
    assert not page.settings().line_art.enabled
    page.close()

def test_line_art_candidates_differ_and_amounts_are_monotonic() -> None:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 52, 52), outline="black", width=3)
    candidates = compare_edge_candidates(image)
    assert candidates["find_edges"]["edge_density"] != candidates["sobel"]["edge_density"]
    amounts = [sum(edge_mask_candidate(image, amount, "sobel").get_flattened_data()) for amount in LineArtAmount]
    assert amounts[0] <= amounts[1] <= amounts[2]
    assert EDGE_METHOD == "sobel"


def test_palette_upstream_change_clears_and_undo_restores(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import EditSettings
    source = tmp_path / "red-green.png"
    image = Image.new("RGB", (2, 1))
    image.putdata([(255, 0, 0), (0, 255, 0)])
    image.save(source)
    page = QuickEditPage()
    page.load_image(source)
    palette = ((255, 0, 0), (0, 255, 0))
    settings = EditSettings(palette=PaletteSettings(True, True, 6, palette, palette, (0, 1), 2, 1))
    page.apply_settings(settings)
    page._control_changed()
    page.filter_combo.setCurrentIndex(page.filter_combo.findData("grayscale"))
    qt_app.processEvents()
    assert page.settings().palette.palette == ()
    assert page.settings().palette.replacements == ()
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    assert page._palette_needs_reextract
    page.undo()
    assert page.settings().filter_preset.value == "none"
    assert page.settings().palette.mapping == (0, 1)
    assert page.settings().palette.quantize_enabled
    page.redo()
    assert page.settings().filter_preset.value == "grayscale"
    assert page.settings().palette.mapping == ()
    assert not page.settings().palette.quantize_enabled
    page.undo()
    page.line_art_enabled.setChecked(True)
    qt_app.processEvents()
    assert page.settings().palette.mapping == (0, 1)
    page.close()


def test_extract_palette_uses_fastoctree(monkeypatch) -> None:
    seen = []
    original = Image.Image.quantize

    def recording_quantize(self, *args, **kwargs):
        seen.append(kwargs.get("method"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "quantize", recording_quantize)
    image = Image.new("RGBA", (4, 1), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 0))
    extract_palette(image, 5)
    assert seen == [Image.Quantize.FASTOCTREE]


def test_renderer_rejects_stale_cached_mapping_digest() -> None:
    source = Image.new("RGB", (2, 1))
    source.putdata([(255, 0, 0), (0, 255, 0)])
    mapping = extract_palette(source, 6)
    cached = PaletteSettings(True, True, 6, mapping.palette, mapping.palette, mapping.indices, 2, 1, mapping.digest)
    stale_settings = __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(filter_preset=FilterPreset.GRAYSCALE, palette=cached)
    fresh = PaletteSettings(True, True, 6, mapping.palette, mapping.palette, (), 0, 0, "")
    fresh_settings = __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(filter_preset=FilterPreset.GRAYSCALE, palette=fresh)
    assert rgba_digest(source) == mapping.digest
    assert render_edit(source, stale_settings).tobytes() == render_edit(source, fresh_settings).tobytes()


def test_palette_worker_applies_current_result_and_digest(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    source = tmp_path / "worker.png"
    image = Image.new("RGB", (32, 24), "red")
    image.save(source)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    assert not page.palette_extract_button.isEnabled()
    deadline = time.monotonic() + 3
    while page._palette_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert page._palette_thread is None
    assert page._palette_mapping_digest
    assert page.settings().palette.mapping_digest == page._palette_mapping_digest
    assert page.palette_extract_button.isEnabled()
    page.close()


def test_palette_worker_stale_result_and_error_are_ignored_or_localized(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    page._palette_values = ((1, 2, 3),)
    token = (1, 9, ("source.png", 1, 1), ("none", page.settings().transparency, 6))
    page._palette_active_request = token
    stale = (0, 8, token[2], token[3], extract_palette(Image.new("RGB", (1, 1), "blue"), 6))
    page._on_palette_extracted(stale)
    assert page._palette_values == ((1, 2, 3),)
    page.preview_status.setText("unchanged")
    page._on_palette_extraction_failed("代表色を抽出できませんでした。", (0, 8, token[2], token[3]))
    assert page.preview_status.text() == "unchanged"
    page._on_palette_extraction_failed("代表色を抽出できませんでした。", token)
    assert "代表色を抽出できませんでした" in page.preview_status.text()
    assert "c62828" in page.preview_status.styleSheet()
    page.cancel_palette_extraction()
    page.close()


def test_palette_thread_runs_off_gui_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    import threading
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import PaletteExtractionThread, QuickEditPage

    source = tmp_path / "thread-check.png"
    Image.new("RGB", (32, 24), "red").save(source)
    main_thread_id = threading.get_ident()
    thread_checks: list[bool] = []

    class RecordingThread(PaletteExtractionThread):
        def run(self) -> None:
            thread_checks.append(threading.get_ident() != main_thread_id)
            super().run()

    monkeypatch.setattr(edit_ui, "PaletteExtractionThread", RecordingThread)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    deadline = time.monotonic() + 3
    while page._palette_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert thread_checks == [True]
    assert page._palette_thread is None
    page.close()


def test_palette_worker_runtime_failure_localizes_and_cleans_up(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "worker-error.png"
    Image.new("RGB", (32, 24), "red").save(source)

    def boom(_image, _count):
        raise RuntimeError("boom")

    monkeypatch.setattr(edit_ui, "extract_palette", boom)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    deadline = time.monotonic() + 3
    while page._palette_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert page._palette_thread is None
    assert page._palette_active_request is None
    assert page.can_close()
    assert page.palette_extract_button.isEnabled()
    assert "代表色を抽出できませんでした。" in page.preview_status.text()
    assert "c62828" in page.preview_status.styleSheet()
    page.close()


def test_export_worker_edit_processing_error_emits_message() -> None:
    from quick_processing_tool.edit_ui import EditExportWorker
    from quick_processing_tool.editing import EditSettings
    from quick_processing_tool.editing.models import EditOutputFormat
    from quick_processing_tool.editing.service import EditProcessingError

    class FailingService:
        def export(self, *args, **kwargs):
            raise EditProcessingError("boom")

    worker = EditExportWorker(
        FailingService(),
        Path("in.png"),
        Path("."),
        EditSettings(),
        EditOutputFormat.PNG,
        (255, 255, 255),
        95,
    )
    messages: list[str] = []
    worker.failed.connect(messages.append)
    worker.run()
    assert messages == ["boom"]
