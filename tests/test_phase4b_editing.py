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


def _wait_for_palette_thread_idle(qt_app, page, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while page._palette_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    qt_app.processEvents()
    assert page._palette_thread is None


def test_palette_thread_runs_off_gui_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    import threading
    import shiboken6
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
    thread = page._palette_thread
    assert thread is not None
    _wait_for_palette_thread_idle(qt_app, page)
    assert thread_checks == [True]
    assert page.can_close()
    assert page.palette_extract_button.isEnabled()
    qt_app.processEvents()
    assert not shiboken6.isValid(thread)
    page.close()


def test_palette_worker_runtime_failure_localizes_and_cleans_up(qt_app, tmp_path: Path, monkeypatch) -> None:
    import shiboken6
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "worker-error.png"
    Image.new("RGB", (32, 24), "red").save(source)

    def boom(_image, _count):
        raise RuntimeError("boom")

    monkeypatch.setattr(edit_ui, "extract_palette", boom)
    page = QuickEditPage()
    page.load_image(source)
    finished_threads = []
    for _ in range(20):
        page.extract_palette()
        thread = page._palette_thread
        assert thread is not None
        _wait_for_palette_thread_idle(qt_app, page)
        assert page._palette_active_request is None
        assert page.can_close()
        assert page.palette_extract_button.isEnabled()
        assert "代表色を抽出できませんでした。" in page.preview_status.text()
        assert "c62828" in page.preview_status.styleSheet()
        finished_threads.append(thread)
    qt_app.processEvents()
    assert all(not shiboken6.isValid(thread) for thread in finished_threads)
    page.close()


def test_palette_worker_cancel_cleans_up_and_deletes_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    import shiboken6
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import PaletteExtractionThread, QuickEditPage

    source = tmp_path / "cancel-thread-check.png"
    Image.new("RGB", (32, 24), "red").save(source)
    started: list[bool] = []

    class SlowCancelableThread(PaletteExtractionThread):
        def run(self) -> None:
            started.append(True)
            deadline = time.monotonic() + 1.0
            while not self.isInterruptionRequested() and time.monotonic() < deadline:
                time.sleep(0.01)
            if self.isInterruptionRequested():
                return
            super().run()

    monkeypatch.setattr(edit_ui, "PaletteExtractionThread", SlowCancelableThread)
    page = QuickEditPage()
    page.load_image(source)
    page.extract_palette()
    thread = page._palette_thread
    assert thread is not None
    assert not page.can_close()
    page.cancel_palette_extraction()
    _wait_for_palette_thread_idle(qt_app, page)
    assert started == [True]
    assert page._palette_active_request is None
    assert page.can_close()
    assert page.palette_extract_button.isEnabled()
    qt_app.processEvents()
    assert not shiboken6.isValid(thread)
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



def test_text_auto_activation_and_clear_keep_section_visible(qt_app) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    page = QuickEditPage()
    page.text_section.toggle.setChecked(True)
    page.show()
    qt_app.processEvents()
    assert not page.text_enabled.isVisible()
    assert page.text_details.isVisible()

    page.text_edit.setPlainText("なかよしこよし")
    qt_app.processEvents()
    page._commit_text_history()
    assert page.text_enabled.isChecked()
    assert page.settings().text.enabled

    page.clear_text()
    qt_app.processEvents()
    assert not page.text_enabled.isChecked()
    assert page.text_details.isVisible()
    assert not page.settings().text.enabled
    page.close()


def test_transparency_slider_spin_sync_and_sticker_navigation_does_not_auto_enable(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "opaque.png"
    Image.new("RGB", (12, 12), "white").save(source)
    page = QuickEditPage()
    page.load_image(source)
    page.material_section.toggle.setChecked(True)
    page.show()
    page.transparency_section.toggle.setChecked(False)
    page.transparency_enabled.setChecked(False)
    qt_app.processEvents()

    page.tolerance_slider.setValue(42)
    qt_app.processEvents()
    assert page.tolerance_spin.value() == 42
    page.softness_spin.setValue(17)
    qt_app.processEvents()
    assert page.softness_slider.value() == 17

    page.open_transparency_settings()
    qt_app.processEvents()
    assert page.transparency_section.toggle.isChecked()
    assert not page.transparency_enabled.isChecked()
    assert page.sticker_prereq_button.isVisible()
    page.close()


def test_line_art_presets_have_visible_distinct_outputs_and_background_contract() -> None:
    image = Image.new("RGB", (72, 72), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 60, 60), outline="black", width=3)
    draw.ellipse((18, 18, 50, 44), outline="black", width=2)
    draw.line((0, 71, 71, 0), fill="black", width=2)

    densities = []
    render_bytes = {}
    for amount in LineArtAmount:
        result = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.WHITE),
        )
        densities.append(sum(1 for x in range(result.width) for y in range(result.height) if result.getpixel((x, y))[:3] != (255, 255, 255)))
        render_bytes[amount] = result.tobytes()

        transparent = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.TRANSPARENT),
        )
        assert transparent.size == image.size and transparent.mode == "RGBA"
        assert any(transparent.getpixel((x, y))[3] > 0 for x in range(transparent.width) for y in range(transparent.height))

        black = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(enabled=True, amount=amount, background=LineArtBackground.BLACK),
        )
        assert black.getpixel((0, 0))[3] == 255

        custom = apply_line_art(
            image.convert("RGBA"),
            LineArtSettings(
                enabled=True,
                amount=amount,
                background=LineArtBackground.CUSTOM,
                custom_background=(12, 34, 56, 255),
            ),
        )
        assert custom.getpixel((4, 4)) == (12, 34, 56, 255)

    assert densities[0] > 0
    assert densities[0] < densities[1] < densities[2] <= densities[3]
    assert len(set(render_bytes.values())) == len(LineArtAmount)


def test_palette_extract_only_keeps_preview_unquantized_until_toggle() -> None:
    from quick_processing_tool.editing import EditSettings

    image = Image.new("RGBA", (6, 1))
    image.putdata([
        (255, 0, 0, 255),
        (230, 10, 10, 255),
        (0, 255, 0, 255),
        (0, 230, 20, 255),
        (0, 0, 255, 255),
        (20, 20, 230, 255),
    ])
    mapping = extract_palette(image, 5)
    settings = EditSettings(palette=PaletteSettings(True, False, 5, mapping.palette, mapping.palette, mapping.indices, mapping.width, mapping.height, mapping.digest))
    untouched = render_edit(image, settings)
    assert untouched.tobytes() == image.tobytes()

    quantized = render_edit(image, __import__("quick_processing_tool.editing", fromlist=["EditSettings"]).EditSettings(palette=PaletteSettings(True, True, 5, mapping.palette, mapping.palette, mapping.indices, mapping.width, mapping.height, mapping.digest)))
    assert quantized.tobytes() != image.tobytes()


def test_palette_rows_and_reset_and_handoff(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "palette-ui.png"
    image = Image.new("RGB", (2, 1))
    image.putdata([(255, 0, 0), (0, 255, 0)])
    image.save(source)

    page = QuickEditPage()
    page.load_image(source)
    mapping = extract_palette(Image.open(source), 6)
    page._on_palette_extracted((page._palette_generation, 1, page._palette_source_identity(source), (page.settings().filter_preset.value, page.settings().transparency, page.settings().palette.color_count), mapping))
    token = page._palette_active_request = (page._palette_generation, 99, page._palette_source_identity(source), (page.settings().filter_preset.value, page.settings().transparency, page.settings().palette.color_count))
    page._on_palette_extracted((*token, mapping))
    qt_app.processEvents()
    assert page.palette_chips_layout.count() >= len(mapping.palette) * 3
    assert page.palette_send_button.isEnabled()
    assert page.palette_send_button.text() == "現在の配色をドット絵パレットへ送る"

    page._palette_replacements = tuple(reversed(mapping.palette))
    page._rebuild_palette_chips()
    emitted = []
    page.palette_handoff_requested.connect(emitted.append)
    page.send_palette_to_pixel()
    assert emitted == [tuple(reversed(mapping.palette))]
    assert page.palette_feedback_label.text() == f"✓ {len(mapping.palette)}色のパレットをドット絵へ送りました"

    page.reset_palette()
    assert page._palette_replacements == mapping.palette
    page.close()


def test_main_window_palette_handoff_switches_tab_and_preserves_pixel_source(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.ui import MainWindow

    window = MainWindow()
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)
    window.pixel_page._load_reference_path(source)
    window.pixel_page.output_folder = tmp_path
    window.pixel_page._update_save_ui()
    window.pixel_page.filename_edit.setText("keep_name")
    window.pixel_page.zoom_combo.setCurrentIndex(3)
    window.pixel_page.grid_check.setChecked(False)
    window.pixel_page.canvas.stroke((0, 0), (2, 0), (123, 45, 67, 255))
    snapshot = window.pixel_page.canvas.snapshot()
    history_index = window.pixel_page.canvas.history._index
    history_len = len(window.pixel_page.canvas.history._entries)
    reference = window.pixel_page.reference

    window._handoff_palette_to_pixel(((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18)))
    qt_app.processEvents()
    assert window.navigation.currentIndex() == window.pixel_tab
    assert window.pixel_page._received_palette == ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18))
    assert window.pixel_page.source_path == source
    assert window.pixel_page.reference == reference
    assert window.pixel_page.filename_edit.text() == "keep_name"
    assert window.pixel_page.output_folder == tmp_path
    assert window.pixel_page.zoom_combo.currentIndex() == 3
    assert window.pixel_page.canvas_view.zoom_factor == 8
    assert not window.pixel_page.grid_check.isChecked()
    assert not window.pixel_page.canvas_view.grid_enabled
    assert window.pixel_page.canvas.snapshot() == snapshot
    assert window.pixel_page.canvas.history._index == history_index
    assert len(window.pixel_page.canvas.history._entries) == history_len
    assert window.pixel_page._palette_selected_index == -1
    window.close()



def test_edit_filename_defaults_suffix_and_custom_retention(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    first = tmp_path / "cat_a1b2c3.png"
    second = tmp_path / "dog.webp"
    Image.new("RGB", (12, 12), "red").save(first)
    Image.new("RGB", (10, 10), "blue").save(second)

    page = QuickEditPage()
    page.load_image(first)
    qt_app.processEvents()
    assert page.filename_edit.text() == "cat_a1b2c3_edited"
    assert page.filename_suffix_label.text() == ".png"
    assert page.save_button.isEnabled()

    page.filename_edit.setText("こんにちは.jpeg.PNG")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "こんにちは"

    page.format_combo.setCurrentIndex(page.format_combo.findData("JPEG"))
    qt_app.processEvents()
    assert page.filename_suffix_label.text() == ".jpg"
    assert page.filename_edit.text() == "こんにちは"

    page.filter_combo.setCurrentIndex(page.filter_combo.findData("grayscale"))
    qt_app.processEvents()
    page.reset_edits()
    qt_app.processEvents()
    assert page.filename_edit.text() == "こんにちは"

    page.load_image(second)
    qt_app.processEvents()
    assert page.filename_edit.text() == "dog_edited"
    assert page.filename_suffix_label.text() == ".jpg"
    page.close()


def test_edit_filename_blank_invalid_reserved_and_planned_path(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "green").save(source)
    page = QuickEditPage()
    page.load_image(source)
    qt_app.processEvents()

    page.filename_edit.setText("bad<>:\"/\\|?*" + chr(0) + "name.png.PNG")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "bad__________name"
    assert page.save_button.isEnabled()
    assert page.planned_path_label.toolTip().endswith("bad__________name.png")

    page.filename_edit.setText("CON.webp")
    page._normalize_output_filename_input()
    assert page.filename_edit.text() == "CON_"

    page.filename_edit.setText("")
    page._normalize_output_filename_input()
    qt_app.processEvents()
    assert page.filename_edit.text() == ""
    assert not page.save_button.isEnabled()
    assert "ファイル名" in page.save_hint_label.text()

    invalid_target = tmp_path / "not-a-folder.txt"
    invalid_target.write_text("x", encoding="utf-8")
    page.output_folder = invalid_target
    page._update_save_panel()
    page._update_actions()
    assert not page.save_button.isEnabled()
    assert "選び直してください" in page.save_hint_label.text()
    page.close()


def test_edit_service_custom_stem_duplicate_and_actual_result_filename(tmp_path: Path) -> None:
    from quick_processing_tool.editing import EditService, EditSettings
    from quick_processing_tool.editing.models import EditOutputFormat

    source = tmp_path / "source_hash_ab12cd.png"
    Image.new("RGBA", (6, 6), (255, 0, 0, 180)).save(source)
    service = EditService()

    result1 = service.export(
        source,
        tmp_path,
        EditSettings(),
        EditOutputFormat.SAME,
        (255, 255, 255),
        95,
        "  こんにちは.png.PNG  ",
    )
    result2 = service.export(
        source,
        tmp_path,
        EditSettings(),
        EditOutputFormat.SAME,
        (255, 255, 255),
        95,
        "  こんにちは.png.PNG  ",
    )

    assert result1.output_path.name == "こんにちは.png"
    assert result2.output_path.name == "こんにちは_2.png"
    assert result1.output_path.is_file() and result2.output_path.is_file()
