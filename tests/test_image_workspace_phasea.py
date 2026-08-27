from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QScrollArea

from quick_processing_tool.editing import (
    EditOutputFormat,
    EditResult,
    EditService,
    EditSettings,
    FilterPreset,
)
from quick_processing_tool.errors import UnsupportedImageError
from quick_processing_tool.image_workspace import (
    ImageWorkspace,
    MISSING_SOURCE_MESSAGE,
    MissingSourceError,
    SourceValidation,
    read_source_image,
)
from quick_processing_tool.models import ProcessingOptions
from quick_processing_tool.pixel_editor_ui import PixelEditorPage
from quick_processing_tool.ui import MainWindow, ProcessingWorker
from quick_processing_tool.upscale_ui import UpscalePage
from quick_processing_tool.upscaler.backend import BackendAvailability, UpscaleBackend
from quick_processing_tool.upscaler.batch import (
    BatchCallbacks,
    BatchJob,
    QueueStatus,
    run_sequential_batch,
)
from quick_processing_tool.upscaler.models import UpscaleOptions
from quick_processing_tool.upscaler.service import UpscaleService


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_image(
    path: Path,
    size: tuple[int, int] = (48, 32),
    *,
    alpha: bool = False,
    color=(40, 90, 140, 120),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA" if alpha else "RGB", size, color if alpha else color[:3])
    image.save(path)
    return path


def wait_for_window_idle(app: QApplication, window: MainWindow, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = (
            window._quick_preview_thread is not None
            or window.edit_page._preview_thread is not None
            or window.pixel_page._import_thread is not None
        )
        if not active:
            break
        app.processEvents()
    app.processEvents()
    assert window._quick_preview_thread is None
    assert window.edit_page._preview_thread is None
    assert window.pixel_page._import_thread is None


class CopyBackend(UpscaleBackend):
    name = "Phase A test backend"

    def __init__(self) -> None:
        self.calls = 0

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(True, user_message="利用できます")

    def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
        self.calls += 1
        with Image.open(input_path) as source:
            source.resize((source.width * scale, source.height * scale)).save(output_path, "PNG")
        progress(100)


def test_workspace_metadata_generation_signal_same_path_and_missing(tmp_path: Path) -> None:
    first = make_image(tmp_path / "alpha.png", (31, 19), alpha=True)
    second = make_image(tmp_path / "second.webp", (17, 23))
    workspace = ImageWorkspace()
    changes = []
    workspace.source_changed.connect(changes.append)

    source = workspace.set_source(first)
    assert source.path == first.resolve()
    assert (source.filename, source.width, source.height, source.format) == ("alpha.png", 31, 19, "PNG")
    assert source.size_bytes == first.stat().st_size
    assert source.has_alpha
    assert not hasattr(source, "image")
    assert source.document_id
    assert source.generation_id == workspace.generation_id == 1
    assert changes == [source]

    same = workspace.set_source(first.parent / "." / first.name)
    assert same is source
    assert workspace.generation_id == 1
    assert changes == [source]

    replacement = workspace.set_source(second)
    assert replacement.document_id != source.document_id
    assert replacement.generation_id == workspace.generation_id == 2
    assert changes == [source, replacement]
    second.unlink()
    assert workspace.validate_current() is SourceValidation.MISSING
    established = workspace.current
    established_generation = workspace.generation_id
    established_document = established.document_id
    signal_count = len(changes)
    with pytest.raises(MissingSourceError):
        workspace.set_source(second)
    with pytest.raises(MissingSourceError, match="元画像が見つかりません"):
        ImageWorkspace().set_source(second)
    with pytest.raises(MissingSourceError):
        workspace.set_source(tmp_path / "missing.png")
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(UnsupportedImageError):
        workspace.set_source(corrupt)
    unsupported = tmp_path / "unsupported.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    with pytest.raises(UnsupportedImageError):
        workspace.set_source(unsupported)
    assert workspace.current is established
    assert workspace.generation_id == established_generation
    assert workspace.current.document_id == established_document
    assert len(changes) == signal_count


def test_global_open_and_cross_tab_source_sharing(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_image(tmp_path / "shared.png", (1883, 61), alpha=True)
    window = MainWindow()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    try:
        window.navigation.setCurrentIndex(window.image_edit_tab)
        assert window.open_action.isEnabled()
        window.open_action.trigger()
        app.processEvents()

        assert window.workspace.current is not None and window.workspace.current.path == source.resolve()
        assert [info.path for info in window.files] == [source]
        assert window.edit_page.source_path == source.resolve()
        assert [item.source_path for item in window.upscale_page.items] == [source.resolve()]
        assert window.pixel_page._current_source is window.workspace.current
        pixmap = window.upscale_page.drop_zone.preview._item.pixmap()
        assert max(pixmap.width(), pixmap.height()) <= 1800
        for card in (
            window.quick_source_card,
            window.edit_page.current_source_card,
            window.upscale_page.current_source_card,
            window.pixel_page.current_source_card,
        ):
            assert "shared.png" in card.name_label.toolTip()
            assert "1883 × 61 / PNG" in card.meta_label.text()
    finally:
        wait_for_window_idle(app, window)
        window.close()


def test_edit_replacement_resets_source_state_and_result_never_becomes_source(
    app: QApplication, tmp_path: Path
) -> None:
    first = make_image(tmp_path / "first.png")
    second = make_image(tmp_path / "second.png", (60, 40))
    output = make_image(tmp_path / "first_edited.png")
    window = MainWindow()
    try:
        window.set_current_source(first)
        window.upscale_page.scale_4.setChecked(True)
        window.upscale_page.format_combo.setCurrentIndex(
            window.upscale_page.format_combo.findData("PNG")
        )
        window.edit_page.filter_combo.setCurrentIndex(
            window.edit_page.filter_combo.findData(FilterPreset.SEPIA.value)
        )
        window.edit_page._last_output = output
        window.edit_page._on_saved(EditResult(output, 48, 32, output.stat().st_size, "PNG", False))
        assert window.workspace.current.path == first.resolve()
        assert window.upscale_page.scale_4.isChecked()
        assert window.upscale_page.format_combo.currentData() == "PNG"
        window.set_current_source(first)
        assert window.edit_page.settings().filter_preset is FilterPreset.SEPIA
        assert window.edit_page._last_output == output

        window.set_current_source(second)
        app.processEvents()
        assert window.edit_page.source_path == second.resolve()
        assert window.edit_page.settings().filter_preset is FilterPreset.NONE
        assert window.edit_page._history_index == 0
        assert window.edit_page._last_output is None
        assert window.workspace.current.path == second.resolve()
        assert window.upscale_page.scale_4.isChecked()
        assert window.upscale_page.format_combo.currentData() == "PNG"
    finally:
        wait_for_window_idle(app, window)
        window.close()


def test_quick_and_upscale_nonempty_batches_are_preserved_and_use_explicit_add(
    app: QApplication, tmp_path: Path
) -> None:
    first = make_image(tmp_path / "first.png")
    second = make_image(tmp_path / "second.png")
    third = make_image(tmp_path / "third.png")
    window = MainWindow()
    try:
        window.set_current_source(first)
        window.load_paths([second])
        quick_before = [info.path for info in window.files]
        window.upscale_page.items[0].detail = "既存結果を保持"
        upscale_before = tuple(
            (item.source_path, item.status, item.detail, item.result)
            for item in window.upscale_page.items
        )

        window.set_current_source(third)
        app.processEvents()
        assert [info.path for info in window.files] == quick_before
        assert tuple(
            (item.source_path, item.status, item.detail, item.result)
            for item in window.upscale_page.items
        ) == upscale_before
        assert window.upscale_page.items[0].detail == "既存結果を保持"
        assert str(third.resolve()) in window.quick_source_card.name_label.toolTip()

        window.add_current_source_to_quick()
        window.upscale_page.add_current_source()
        assert [info.path for info in window.files] == [first, second, third]
        assert [item.source_path for item in window.upscale_page.items] == [first.resolve(), third.resolve()]
    finally:
        wait_for_window_idle(app, window)
        window.close()


def test_pixel_source_change_is_passive_until_explicit_action(
    app: QApplication, tmp_path: Path
) -> None:
    first = make_image(tmp_path / "first.png", (18, 14), color=(220, 20, 30, 255))
    second = make_image(tmp_path / "second.png", (18, 14), color=(20, 220, 30, 255))
    window = MainWindow()
    try:
        pixel = window.pixel_page
        pixel.canvas.stroke((1, 1), (10, 1), (1, 2, 3, 255))
        before_hash = hashlib.sha256(pixel.canvas.snapshot()).hexdigest()
        history_before = pixel.canvas.history.current
        pixel._load_reference_path(first)
        wait_for_window_idle(app, window)
        reference_before = pixel.reference
        filename_before = pixel.filename_edit.text()
        pixel.output_folder = tmp_path / "pixel-output"
        output_folder_before = pixel.output_folder
        history_index_before = pixel.canvas.history._index
        history_count_before = len(pixel.canvas.history._entries)
        window.set_current_source(first)
        window.set_current_source(second)
        app.processEvents()
        assert hashlib.sha256(pixel.canvas.snapshot()).hexdigest() == before_hash
        assert pixel.canvas.history.current == history_before
        assert pixel.reference is reference_before
        assert pixel.canvas.history._index == history_index_before
        assert len(pixel.canvas.history._entries) == history_count_before
        assert pixel.filename_edit.text() == filename_before
        assert pixel.output_folder == output_folder_before
        assert pixel.source_path == first

        pixel.use_current_as_reference()
        wait_for_window_idle(app, window)
        assert pixel.reference is not None
        assert pixel.reference is not reference_before
        assert hashlib.sha256(pixel.canvas.snapshot()).hexdigest() == before_hash
        pixel.use_current_as_pixels()
        wait_for_window_idle(app, window)
        assert hashlib.sha256(pixel.canvas.snapshot()).hexdigest() != before_hash
    finally:
        wait_for_window_idle(app, window)
        window.close()


def test_quick_batch_marks_missing_and_continues(tmp_path: Path) -> None:
    first = make_image(tmp_path / "first.png")
    missing = make_image(tmp_path / "missing.png")
    last = make_image(tmp_path / "last.png")
    missing.unlink()
    worker = ProcessingWorker(
        [first, missing, last],
        ProcessingOptions(),
        True,
        "Same folder",
        None,
        False,
    )
    statuses = []
    finished = []
    worker.file_status.connect(lambda *args: statuses.append(args))
    worker.finished.connect(lambda *args: finished.append(args))
    worker.run()
    assert finished == [(2, 1)]
    assert any(row == 1 and status == "Missing" and detail == MISSING_SOURCE_MESSAGE for row, status, detail in statuses)
    assert any(row == 2 and status == "Done" for row, status, _ in statuses)


def test_upscale_batch_marks_missing_and_continues(tmp_path: Path) -> None:
    first = make_image(tmp_path / "first.png")
    missing = make_image(tmp_path / "missing.png")
    last = make_image(tmp_path / "last.png")
    missing.unlink()
    backend = CopyBackend()
    statuses = []
    outcome = run_sequential_batch(
        UpscaleService(backend),
        [BatchJob(index, path, tmp_path / "out") for index, path in enumerate((first, missing, last))],
        UpscaleOptions(scale=2),
        Event(),
        BatchCallbacks(
            status=lambda *args: statuses.append(args),
            current=lambda *args: None,
            result=lambda *args: None,
            progress=lambda *args: None,
        ),
    )
    assert backend.calls == 2
    assert (outcome.succeeded, outcome.failed) == (2, 1)
    assert (1, QueueStatus.MISSING, "元画像なし") in statuses
    assert any(index == 2 and status is QueueStatus.DONE for index, status, _ in statuses)


def test_upscale_corrupt_first_does_not_replace_valid_shared_source(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    valid = make_image(tmp_path / "valid.png")
    page = UpscalePage(UpscaleService(CopyBackend()))
    page.set_workspace_managed(True)
    requested = []
    warnings = []
    page.source_change_requested.connect(requested.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    try:
        page.load_paths([corrupt, valid])
        assert [item.source_path for item in page.items] == [valid.resolve()]
        assert requested == [valid.resolve()]
        assert warnings
    finally:
        page.close()


def test_upscale_stale_duplicate_and_wrong_decoded_format_are_not_shared(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = make_image(tmp_path / "stale.png")
    wrong_format = tmp_path / "looks-like-png.png"
    Image.new("RGB", (8, 8), "red").save(wrong_format, format="BMP")
    valid = make_image(tmp_path / "valid-second.png")
    page = UpscalePage(UpscaleService(CopyBackend()))
    page.load_paths([stale])
    page.set_workspace_managed(True)
    requested = []
    page.source_change_requested.connect(requested.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    stale.write_bytes(b"now corrupt")
    try:
        page.load_paths([stale, wrong_format, valid])
        assert requested == [valid.resolve()]
        assert [item.source_path for item in page.items] == [stale.resolve(), valid.resolve()]
    finally:
        page.close()


def test_pixel_corrupt_source_is_rejected_before_workspace_or_import(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    page = PixelEditorPage()
    page.set_workspace_managed(True)
    requested = []
    import_choices = []
    warnings = []
    page.source_change_requested.connect(requested.append)
    page.import_choice_provider = lambda path: import_choices.append(path) or "pixels"
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    before = page.canvas.snapshot()
    try:
        page._handle_dropped_path(corrupt)
        assert requested == []
        assert import_choices == []
        assert page.canvas.snapshot() == before
        assert warnings == [("画像を開けません", "PNG / JPEG / WebP画像を選んでください。")]
    finally:
        page.close()


def test_pixel_standalone_source_choice_updates_only_passive_card(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_image(tmp_path / "standalone.png")
    page = PixelEditorPage()
    before = page.canvas.snapshot()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    try:
        page.choose_current_source()
        assert page._current_source is not None and page._current_source.path == source.resolve()
        assert str(source.resolve()) in page.current_source_card.name_label.toolTip()
        assert page.canvas.snapshot() == before
        assert page.reference is None
        assert page.source_path is None
    finally:
        page.close()


def test_pixel_import_race_preserves_all_editing_state(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quick_processing_tool.pixel_editor_ui as pixel_ui_module

    existing = make_image(tmp_path / "existing.png")
    disappearing_reference = make_image(tmp_path / "reference.png")
    disappearing_pixels = make_image(tmp_path / "pixels.png")
    page = PixelEditorPage()
    page.canvas.stroke((1, 1), (4, 1), (9, 8, 7, 255))
    page._load_reference_path(existing)
    deadline = time.monotonic() + 3.0
    while page._import_thread is not None and time.monotonic() < deadline:
        app.processEvents()
    assert page._import_thread is None
    page.output_folder = tmp_path / "out"
    before = (
        page.canvas.snapshot(),
        page.canvas.history.current,
        page.canvas.history._index,
        len(page.canvas.history._entries),
        page.reference,
        page.filename_edit.text(),
        page.output_folder,
        page.source_path,
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))

    def vanish_reference(*args, **kwargs):
        disappearing_reference.unlink()
        raise OSError("vanished")

    def vanish_pixels(path):
        disappearing_pixels.unlink()
        raise OSError("vanished")

    monkeypatch.setattr(pixel_ui_module, "load_reference", vanish_reference)
    monkeypatch.setattr(pixel_ui_module, "load_rgba", vanish_pixels)
    try:
        assert page._load_reference_path(disappearing_reference) is True
        deadline = time.monotonic() + 3.0
        while page._import_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        assert page._load_pixels_path(disappearing_pixels) is True
        deadline = time.monotonic() + 3.0
        while page._import_thread is not None and time.monotonic() < deadline:
            app.processEvents()
        after = (
            page.canvas.snapshot(),
            page.canvas.history.current,
            page.canvas.history._index,
            len(page.canvas.history._entries),
            page.reference,
            page.filename_edit.text(),
            page.output_folder,
            page.source_path,
        )
        assert after == before
        assert warnings == [
            ("元画像が見つかりません", MISSING_SOURCE_MESSAGE),
            ("元画像が見つかりません", MISSING_SOURCE_MESSAGE),
        ]
    finally:
        page.close()


def test_edit_source_replacement_failure_is_atomic(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_image(tmp_path / "first.png")
    disappearing = make_image(tmp_path / "disappearing.png")
    window = MainWindow()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: warnings.append((title, text)))
    try:
        window.set_current_source(first)
        page = window.edit_page
        page.filter_combo.setCurrentIndex(page.filter_combo.findData(FilterPreset.SEPIA.value))
        previous_path = page.source_path
        previous_card = page.current_source_card.name_label.toolTip()
        candidate = read_source_image(disappearing, 2)
        disappearing.unlink()
        page.set_current_source(candidate)
        assert page.source_path == previous_path
        assert page.current_source_card.name_label.toolTip() == previous_card
        assert page.settings().filter_preset is FilterPreset.SEPIA
        assert warnings == [("元画像が見つかりません", MISSING_SOURCE_MESSAGE)]
    finally:
        wait_for_window_idle(app, window)
        window.close()


def test_processing_races_reclassify_disappearing_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quick_processing_tool.editing.service as edit_service_module
    import quick_processing_tool.ui as ui_module

    quick_source = make_image(tmp_path / "quick.png")
    quick_statuses = []

    def vanish_quick(path, options):
        path.unlink()
        raise OSError("vanished")

    monkeypatch.setattr(ui_module, "process_image", vanish_quick)
    worker = ProcessingWorker([quick_source], ProcessingOptions(), True, "Same folder", None, False)
    worker.file_status.connect(lambda *args: quick_statuses.append(args))
    worker.run()
    assert any(status == "Missing" and detail == MISSING_SOURCE_MESSAGE for _row, status, detail in quick_statuses)

    edit_source = make_image(tmp_path / "edit.png")

    def vanish_edit(path, settings):
        path.unlink()
        raise OSError("vanished")

    monkeypatch.setattr(edit_service_module, "render_path", vanish_edit)
    with pytest.raises(MissingSourceError, match="元画像が見つかりません"):
        EditService().export(
            edit_source,
            tmp_path / "edit-out",
            EditSettings(),
            EditOutputFormat.PNG,
        )

    upscale_source = make_image(tmp_path / "upscale.png")

    class VanishingBackend(CopyBackend):
        def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
            upscale_source.unlink()
            raise RuntimeError("vanished")

    statuses = []
    outcome = run_sequential_batch(
        UpscaleService(VanishingBackend()),
        [BatchJob(0, upscale_source, tmp_path / "upscale-out")],
        UpscaleOptions(scale=2),
        Event(),
        BatchCallbacks(
            status=lambda *args: statuses.append(args),
            current=lambda *args: None,
            result=lambda *args: None,
            progress=lambda *args: None,
        ),
    )
    assert outcome.failed == 1
    assert (0, QueueStatus.MISSING, "元画像なし") in statuses


def test_single_source_missing_dialog_is_specific(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_image(tmp_path / "gone.png")
    window = MainWindow()
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, title, text: messages.append((title, text)))
    try:
        window.set_current_source(source)
        source.unlink()
        window._start_worker([source], False, [0])
        window.edit_page.save_image()
        window.upscale_page.start()
        window.add_current_source_to_quick()
        window.upscale_page.add_current_source()
        window.pixel_page.use_current_as_reference()
        window.pixel_page.use_current_as_pixels()
        assert len(messages) == 7
        assert all(text == MISSING_SOURCE_MESSAGE for _title, text in messages)
    finally:
        wait_for_window_idle(app, window)
        window.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_source_cards_keep_layout_without_horizontal_scroll(
    app: QApplication, tmp_path: Path, width: int
) -> None:
    source = make_image(tmp_path / f"very-long-current-source-name-{width}.png", (120, 90))
    window = MainWindow()
    try:
        window.resize(width, 760)
        window.set_current_source(source)
        window.show()
        app.processEvents()
        for tab_index in (window.quick_tab, window.image_edit_tab, window.upscale_tab, window.pixel_tab):
            window.navigation.setCurrentIndex(tab_index)
            app.processEvents()
            for scroll in window.navigation.currentWidget().findChildren(QScrollArea):
                if scroll.objectName() in {"pixelPreviewScroll"}:
                    continue
                assert scroll.horizontalScrollBar().maximum() == 0, scroll.objectName()
        assert window.drop_zone.width() >= (360 if width == 900 else 400)
        for card in (
            window.quick_source_card,
            window.edit_page.current_source_card,
            window.upscale_page.current_source_card,
            window.pixel_page.current_source_card,
        ):
            assert card.width() <= card.parentWidget().width()
    finally:
        wait_for_window_idle(app, window)
        window.close()
