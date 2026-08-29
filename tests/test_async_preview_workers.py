from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wait(qt_app, predicate, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.002)
    qt_app.processEvents()
    assert predicate()


def _preview_pixel(page) -> tuple[int, int, int, int]:
    image = page.drop_zone.preview._item.pixmap().toImage()
    color = image.pixelColor(0, 0)
    return color.red(), color.green(), color.blue(), color.alpha()


def test_edit_preview_latest_pending_wins(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import FilterPreset

    source = tmp_path / "edit-latest.png"
    Image.new("RGB", (20, 12), "white").save(source)
    calls = []

    def fake_render(_path, settings, _max_dimension):
        calls.append(settings.filter_preset)
        if settings.filter_preset is FilterPreset.NONE:
            time.sleep(0.08)
            return Image.new("RGBA", (5, 4), "red")
        return Image.new("RGBA", (7, 6), "blue")

    monkeypatch.setattr(edit_ui, "render_path_preview", fake_render)
    page = QuickEditPage()
    assert page._preview_timer.interval() == 120
    assert page.load_image(source)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.GRAYSCALE.value))
    page.update_preview()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert calls[0] is FilterPreset.NONE
    assert calls[-1] is FilterPreset.GRAYSCALE
    assert _preview_pixel(page)[:3] == (0, 0, 255)
    assert "7 × 6" in page.preview_status.text()
    assert page.can_close()
    page.close()


def test_edit_preview_source_generation_discards_old_result(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    first = tmp_path / "old.png"
    second = tmp_path / "new.png"
    Image.new("RGB", (8, 8), "red").save(first)
    Image.new("RGB", (9, 7), "blue").save(second)

    def fake_render(path, _settings, _max_dimension):
        if Path(path) == first.resolve():
            time.sleep(0.08)
            return Image.new("RGBA", (3, 3), "red")
        return Image.new("RGBA", (4, 5), "blue")

    monkeypatch.setattr(edit_ui, "render_path_preview", fake_render)
    page = QuickEditPage()
    assert page.load_image(first)
    assert page.load_image(second)
    _wait(qt_app, lambda: page._preview_thread is None)
    assert page.source_path == second.resolve()
    assert _preview_pixel(page)[:3] == (0, 0, 255)
    assert "4 × 5" in page.preview_status.text()
    page.close()


def test_edit_preview_current_error_is_localized(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "error.png"
    Image.new("RGB", (8, 8), "red").save(source)
    monkeypatch.setattr(edit_ui, "render_path_preview", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))
    page = QuickEditPage()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    assert "プレビューを更新できませんでした" in page.preview_status.text()
    assert "c62828" in page.preview_status.styleSheet()
    assert not page.preview_activity.isVisible() or "失敗" in page.preview_activity.status_label.text()
    page.close()


def test_edit_palette_activity_takes_over_active_preview_then_starts_fresh_preview(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage

    source = tmp_path / "palette-takeover.png"
    Image.new("RGB", (24, 18), "red").save(source)
    started, release = Event(), Event()
    calls = []

    def delayed_first_render(_path, _settings, _max_dimension):
        calls.append(True)
        if len(calls) == 1:
            started.set()
            release.wait(2)
        return Image.new("RGBA", (8, 6), "blue")

    monkeypatch.setattr(edit_ui, "render_path_preview", delayed_first_render)
    page = QuickEditPage()
    labels = []
    real_begin = page.preview_activity.begin

    def recording_begin(label):
        labels.append(label)
        return real_begin(label)

    monkeypatch.setattr(page.preview_activity, "begin", recording_begin)
    assert page.load_image(source)
    _wait(qt_app, started.is_set)
    first_preview_token = page._preview_activity_token
    page.extract_palette()
    assert page._preview_activity_token is None
    assert page._palette_activity_token is not None
    assert first_preview_token != page._palette_activity_token
    _wait(qt_app, lambda: page._palette_thread is None)
    _wait(qt_app, lambda: page._preview_pending_request is not None)
    assert labels[:2] == ["プレビューを更新しています", "代表色を抽出しています"]
    assert labels[-1] == "プレビューを更新しています"
    assert page._preview_activity_token is not None
    release.set()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert len(calls) == 2
    assert page._preview_activity_token is None
    assert page._palette_activity_token is None
    assert "8 × 6" in page.preview_status.text()
    page.close()


def test_edit_line_expression_rapid_changes_apply_latest_standard_only(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing import LineArtAmount

    source = tmp_path / "line-latest.png"
    Image.new("RGB", (24, 18), "white").save(source)
    started, release = Event(), Event()
    calls = []

    def controlled_render(_path, settings, _max_dimension):
        if not settings.line_art.enabled:
            return Image.new("RGBA", (6, 4), "white")
        calls.append(settings.line_art)
        if settings.line_art.enabled and settings.line_art.line_color == (0, 0, 0, 255):
            started.set()
            release.wait(2)
        color = "blue" if settings.line_art.line_color == (0, 70, 255, 255) else "red"
        return Image.new("RGBA", (6, 4), color)

    monkeypatch.setattr(edit_ui, "render_path_preview", controlled_render)
    page = QuickEditPage()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    page.line_art_enabled.setChecked(True)
    page.update_preview()
    _wait(qt_app, started.is_set)
    page.line_art_enabled.setChecked(False)
    page.update_preview()
    page._line_art_color = QColor(0, 70, 255, 255)
    page.line_art_enabled.setChecked(True)
    page.update_preview()
    release.set()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert len(calls) == 2
    assert all(settings.amount is LineArtAmount.STANDARD for settings in calls)
    assert calls[-1].enabled and calls[-1].line_color == (0, 70, 255, 255)
    assert _preview_pixel(page)[:3] == (0, 0, 255)
    assert page.settings().line_art.amount is LineArtAmount.STANDARD
    assert page._preview_activity_token is None
    assert not page.preview_activity._delay_timer.isActive()
    assert not page.preview_activity._tick_timer.isActive()
    page.close()


def test_pixel_import_success_commits_once_and_stroke_has_no_activity(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage

    source = tmp_path / "pixel-success.png"
    Image.new("RGBA", (16, 12), (20, 80, 220, 255)).save(source)
    page = PixelEditorPage()
    history_before = len(page.canvas.history._entries)
    assert page._load_pixels_path(source)
    assert not page.can_close()
    _wait(qt_app, lambda: page._import_thread is None)
    assert len(page.canvas.history._entries) == history_before + 1
    assert page.canvas.history._index == history_before
    assert page.source_path == source.resolve()
    page.preview_activity.invalidate()
    page.canvas.stroke((0, 0), (2, 0), (1, 2, 3, 255))
    page._refresh()
    assert page._import_thread is None
    assert page._import_activity_token is None
    assert not page.preview_activity.isVisible()
    page.close()


def test_pixel_import_error_preserves_canvas_reference_and_history(qt_app, tmp_path: Path, monkeypatch) -> None:
    from quick_processing_tool.pixel_editor.models import ReferenceImage
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage
    from PySide6.QtWidgets import QMessageBox

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (3, 1), (9, 8, 7, 255))
    reference = ReferenceImage(Image.new("RGBA", (128, 128), "white"), 40)
    page.reference = reference
    page.canvas_view.reference = reference
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    before = (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries), page.reference)
    assert not page._load_pixels_path(corrupt)
    after = (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries), page.reference)
    assert after == before
    assert warnings == [("画像を開けません", "PNG / JPEG / WebP画像を選んでください。")]
    page.close()


def test_pixel_import_latest_pending_wins(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.pixel_editor_ui as pixel_ui
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (10, 10), "red").save(first)
    Image.new("RGBA", (10, 10), "blue").save(second)
    real_load = pixel_ui.load_rgba

    def slow_first(path):
        if Path(path) == first.resolve():
            time.sleep(0.08)
        return real_load(path)

    monkeypatch.setattr(pixel_ui, "load_rgba", slow_first)
    page = PixelEditorPage()
    assert page._load_pixels_path(first)
    assert page._load_pixels_path(second)
    _wait(qt_app, lambda: page._import_thread is None)
    assert page.source_path == second.resolve()
    assert page.canvas.pixel(64, 64)[:3] == (0, 0, 255)
    assert len(page.canvas.history._entries) == 2
    page.close()


def test_pixel_import_busy_blocks_document_mutation_but_accepts_latest(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.pixel_editor_ui as pixel_ui
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage

    first = tmp_path / "busy-reference.png"
    second = tmp_path / "busy-pixels.png"
    Image.new("RGBA", (16, 12), "red").save(first)
    Image.new("RGBA", (16, 12), "blue").save(second)
    real_reference = pixel_ui.load_reference
    started, release = Event(), Event()

    def delayed_reference(path, canvas_size, opacity):
        started.set()
        release.wait(2)
        return real_reference(path, canvas_size, opacity)

    monkeypatch.setattr(pixel_ui, "load_reference", delayed_reference)
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (3, 1), (9, 8, 7, 255))
    before = (page.canvas.snapshot(), page.canvas.history._index, page.filename_edit.text())
    opacity = page.opacity_slider.value()
    assert page._load_reference_path(first)
    _wait(qt_app, started.is_set)
    for widget in (
        page.canvas_view,
        page.tools_group,
        page.edit_group,
        page.view_group,
        page.canvas_group,
        page.palette_group,
        page.clear_button,
        page.undo_button,
        page.redo_button,
        page.opacity_slider,
        page.filename_edit,
    ):
        assert not widget.isEnabled()
    assert not hasattr(page, "reference_button")
    assert not hasattr(page, "pixelize_button")
    page.clear()
    page.undo()
    page.redo()
    assert (page.canvas.snapshot(), page.canvas.history._index, page.filename_edit.text()) == before
    assert page._load_pixels_path(second)
    assert page.preview_activity._label == "画像をドット化しています"
    assert page._import_pending_request[-1] == page._import_activity_token
    assert page._import_active_request[6] == opacity
    assert page.opacity_slider.value() == opacity
    assert page.filename_edit.text() == before[2]
    release.set()
    _wait(qt_app, lambda: page._import_thread is None)
    assert page.source_path == second.resolve()
    assert page.opacity_slider.value() == opacity
    assert page.filename_edit.text() == "busy-pixels_pixel"
    assert page.preview_hint_label.styleSheet() == "color: #667085;"
    assert page.preview_hint_label.text().startswith("現在色:")
    assert page.canvas_view.isEnabled()
    page.close()


def test_pixel_reference_import_uses_disabled_captured_opacity(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage

    source = tmp_path / "opacity-reference.png"
    Image.new("RGBA", (16, 12), "red").save(source)
    page = PixelEditorPage()
    page.opacity_slider.setValue(70)
    assert page._load_reference_path(source)
    assert not page.opacity_slider.isEnabled()
    assert page._import_active_request[6] == page.opacity_slider.value() == 70
    _wait(qt_app, lambda: page._import_thread is None)
    assert page.reference is not None
    assert page.reference.opacity == page.opacity_slider.value() == 70
    page.close()


def test_pixel_current_worker_failure_shows_dialog_and_preserves_state(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.pixel_editor_ui as pixel_ui
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage
    from PySide6.QtWidgets import QMessageBox

    source = tmp_path / "runtime-failure.png"
    Image.new("RGBA", (12, 10), "red").save(source)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    monkeypatch.setattr(pixel_ui, "load_rgba", lambda _path: (_ for _ in ()).throw(RuntimeError("boom")))
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (3, 1), (9, 8, 7, 255))
    before = (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries))
    assert page._load_pixels_path(source)
    _wait(qt_app, lambda: page._import_thread is None)
    assert (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries)) == before
    assert warnings and warnings[0][0] == "画像を開けません"
    assert "boom" in warnings[0][1]
    assert page._import_activity_token is None
    page.close()


def test_pixel_palette_chip_accessibility_tracks_selection(qt_app) -> None:
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage

    page = PixelEditorPage()
    page.receive_palette([(10, 20, 30), (200, 210, 220)])
    assert page._palette_chip_buttons[0].accessibleName() == "パレット 1: #0A141E・未選択"
    page._apply_palette_color((200, 210, 220), 1)
    assert page._palette_chip_buttons[0].accessibleName().endswith("未選択")
    assert page._palette_chip_buttons[1].accessibleName() == "パレット 2: #C8D2DC・選択中"
    page.close()


def test_quick_preview_latest_selection_wins(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.ui as ui
    from quick_processing_tool.ui import MainWindow

    first = tmp_path / "quick-first.png"
    second = tmp_path / "quick-second.png"
    Image.new("RGB", (12, 10), "red").save(first)
    Image.new("RGB", (14, 11), "blue").save(second)
    real_normalize = ui.normalize_orientation

    def slow_first(opened):
        if Path(opened.filename) == first:
            time.sleep(0.08)
            return Image.new("RGBA", (5, 4), "red")
        if Path(opened.filename) == second:
            return Image.new("RGBA", (7, 6), "blue")
        return real_normalize(opened)

    monkeypatch.setattr(ui, "normalize_orientation", slow_first)
    window = MainWindow()
    window.load_paths([first, second], update_workspace=False)
    window.file_tree.setCurrentItem(window.file_tree.topLevelItem(1))
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    image = window.preview._image_item.pixmap().toImage()
    assert image.pixelColor(0, 0).blue() == 255
    assert window.current_index == 1
    window.close()


def test_quick_preview_current_error_and_missing_clear(qt_app, tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.ui as ui
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE
    from quick_processing_tool.ui import MainWindow

    source = tmp_path / "quick-error.png"
    Image.new("RGB", (12, 10), "red").save(source)
    window = MainWindow()
    window.load_paths([source], update_workspace=False)
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    monkeypatch.setattr(ui, "normalize_orientation", lambda _opened: (_ for _ in ()).throw(RuntimeError("boom")))
    window._show_current()
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    assert window.preview._image_item.pixmap().isNull()
    assert "プレビューを表示できませんでした" in window.statusBar().currentMessage()
    source.unlink()
    window._show_current()
    assert window.statusBar().currentMessage() == MISSING_SOURCE_MESSAGE
    assert window.preview._image_item.pixmap().isNull()
    window.close()


def test_quick_preview_decoder_opens_image_once(tmp_path: Path, monkeypatch) -> None:
    import quick_processing_tool.ui as ui

    source = tmp_path / "single-open.png"
    Image.new("RGBA", (17, 13), (1, 2, 3, 120)).save(source)
    real_open = ui.Image.open
    calls = []

    def counted_open(path, *args, **kwargs):
        calls.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(ui.Image, "open", counted_open)
    preview, info = ui.decode_quick_preview(source)
    assert calls == [source]
    assert (info.width, info.height, info.format, info.size_bytes) == (17, 13, "PNG", source.stat().st_size)
    assert (preview.width(), preview.height()) == (17, 13)


def test_edit_palette_identity_mismatch_resolves_current_activity(qt_app, tmp_path: Path) -> None:
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.editing.palette import extract_palette
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE

    source = tmp_path / "palette-moving.png"
    Image.new("RGB", (12, 10), "red").save(source)
    page = QuickEditPage()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    identity = page._palette_source_identity(source)
    signature = (
        page.settings().filter_preset.value,
        page.settings().transparency,
        page.settings().palette.color_count,
    )
    request = (page._palette_generation, 99, identity, signature)
    page._palette_active_request = request
    page._palette_activity_token = page.preview_activity.begin("代表色を抽出しています")
    mapping = extract_palette(Image.new("RGB", (2, 2), "red"), signature[2])
    source.unlink()
    page._on_palette_extracted((*request, mapping))
    assert page._palette_activity_token is None
    assert page.preview_status.text() == MISSING_SOURCE_MESSAGE
    assert "c62828" in page.preview_status.styleSheet()
    page.cancel_palette_extraction()
    page.close()


def test_edit_preview_file_move_releases_current_activity_without_commit(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.edit_ui as edit_ui
    from quick_processing_tool.edit_ui import QuickEditPage
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE

    source = tmp_path / "edit-moving.png"
    moved = tmp_path / "edit-moved.png"
    Image.new("RGB", (16, 12), "red").save(source)
    started, release = Event(), Event()

    def delayed_render(*_args):
        started.set()
        release.wait(2)
        return Image.new("RGBA", (4, 3), "blue")

    monkeypatch.setattr(edit_ui, "render_path_preview", delayed_render)
    page = QuickEditPage()
    assert page.load_image(source)
    _wait(qt_app, started.is_set)
    source.rename(moved)
    release.set()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert page.source_path == source.resolve()
    assert page.drop_zone.preview._item.pixmap().isNull()
    assert page.preview_status.text() == MISSING_SOURCE_MESSAGE
    assert page._preview_activity_token is None
    assert not page.preview_activity._delay_timer.isActive()
    assert not page.preview_activity._tick_timer.isActive()
    assert page.can_close()
    page.close()


def test_pixel_import_file_move_releases_busy_and_preserves_state(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.pixel_editor_ui as pixel_ui
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE
    from quick_processing_tool.pixel_editor_ui import PixelEditorPage
    from PySide6.QtWidgets import QMessageBox

    source = tmp_path / "pixel-moving.png"
    moved = tmp_path / "pixel-moved.png"
    Image.new("RGBA", (16, 12), "red").save(source)
    started, release = Event(), Event()

    def delayed_load(_path):
        started.set()
        release.wait(2)
        return Image.new("RGBA", (16, 12), "blue")

    monkeypatch.setattr(pixel_ui, "load_rgba", delayed_load)
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (3, 1), (9, 8, 7, 255))
    before = (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    assert page._load_pixels_path(source)
    _wait(qt_app, started.is_set)
    source.rename(moved)
    release.set()
    _wait(qt_app, lambda: page._import_thread is None)
    assert (page.canvas.snapshot(), page.canvas.history._index, len(page.canvas.history._entries)) == before
    assert page.preview_hint_label.text() == MISSING_SOURCE_MESSAGE
    assert warnings == [("元画像が見つかりません", MISSING_SOURCE_MESSAGE)]
    assert page._import_activity_token is None
    assert not page.preview_activity._delay_timer.isActive()
    assert not page.preview_activity._tick_timer.isActive()
    assert page.can_close()
    page.close()


def test_quick_preview_file_move_releases_current_activity(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.ui as ui
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE
    from quick_processing_tool.ui import MainWindow

    source = tmp_path / "quick-moving.png"
    moved = tmp_path / "quick-moved.png"
    Image.new("RGB", (16, 12), "red").save(source)
    window = MainWindow()
    window.load_paths([source], update_workspace=False)
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    real_preview = ui.decode_quick_preview(source)
    started, release = Event(), Event()

    def delayed_preview(_path):
        started.set()
        release.wait(2)
        return real_preview

    monkeypatch.setattr(ui, "decode_quick_preview", delayed_preview)
    window._show_current()
    _wait(qt_app, started.is_set)
    source.rename(moved)
    release.set()
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    assert [info.path for info in window.files] == [source]
    assert window.current_index == 0
    assert window.preview._image_item.pixmap().isNull()
    assert window.statusBar().currentMessage() == MISSING_SOURCE_MESSAGE
    assert window._quick_preview_activity_token is None
    assert not window.quick_preview_activity._delay_timer.isActive()
    assert not window.quick_preview_activity._tick_timer.isActive()
    window.close()


def test_quick_missing_new_selection_is_not_overwritten_by_old_preview(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    import quick_processing_tool.ui as ui
    from quick_processing_tool.image_workspace import MISSING_SOURCE_MESSAGE
    from quick_processing_tool.ui import MainWindow
    from PySide6.QtWidgets import QMessageBox

    first = tmp_path / "active-a.png"
    second = tmp_path / "missing-b.png"
    Image.new("RGB", (16, 12), "red").save(first)
    Image.new("RGB", (14, 10), "blue").save(second)
    real_decode = ui.decode_quick_preview
    started, release = Event(), Event()

    def delayed_first(path):
        if Path(path) == first.resolve():
            started.set()
            release.wait(2)
        return real_decode(path)

    warnings = []
    monkeypatch.setattr(ui, "decode_quick_preview", delayed_first)
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    window = MainWindow()
    window.load_paths([first, second], update_workspace=False)
    _wait(qt_app, started.is_set)
    old_request_id = window._quick_preview_request_id
    second.unlink()
    window.file_tree.setCurrentItem(window.file_tree.topLevelItem(1))
    assert window._quick_preview_request_id == old_request_id + 1
    assert window.statusBar().currentMessage() == MISSING_SOURCE_MESSAGE
    assert warnings == [("元画像が見つかりません", MISSING_SOURCE_MESSAGE)]
    assert window.preview._image_item.pixmap().isNull()
    assert window._quick_preview_activity_token is None
    assert not window.quick_preview_activity.isVisible()
    release.set()
    _wait(qt_app, lambda: window._quick_preview_thread is None)
    assert window.statusBar().currentMessage() == MISSING_SOURCE_MESSAGE
    assert warnings == [("元画像が見つかりません", MISSING_SOURCE_MESSAGE)]
    assert window.preview._image_item.pixmap().isNull()
    assert window._quick_preview_activity_token is None
    assert not window.quick_preview_activity._delay_timer.isActive()
    assert not window.quick_preview_activity._tick_timer.isActive()
    window.close()
