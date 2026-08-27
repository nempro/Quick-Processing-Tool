from __future__ import annotations

import colorsys
import hashlib
import os
import random
import time
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

import quick_processing_tool.edit_ui as edit_ui_module
from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.editing import EditSettings, PaletteSettings, RecolorBlendMode
from quick_processing_tool.editing.palette import (
    PaletteMapping,
    apply_palette_mapping,
    apply_palette_mapping_preserve_shading,
    apply_palette_mapping_smooth,
    extract_palette,
    quantize_image,
)
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


COLORS_12 = (
    (12, 18, 24),
    (28, 72, 132),
    (42, 138, 82),
    (63, 184, 188),
    (88, 46, 144),
    (116, 92, 58),
    (145, 34, 52),
    (174, 112, 198),
    (202, 86, 32),
    (220, 164, 70),
    (236, 202, 176),
    (248, 242, 226),
)


def _bands(colors=COLORS_12, *, height: int = 12) -> Image.Image:
    image = Image.new("RGBA", (len(colors), height))
    for x, color in enumerate(colors):
        for y in range(height):
            image.putpixel((x, y), (*color, 80 + (y * 175 // max(1, height - 1))))
    return image


def _wait_page_idle(app: QApplication, page: QuickEditPage, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while (
        page._palette_thread is not None or page._preview_thread is not None
    ) and time.monotonic() < deadline:
        app.processEvents()
    app.processEvents()
    assert page._palette_thread is None
    assert page._preview_thread is None


def _wait_preview_cycle(app: QApplication, page: QuickEditPage, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while (
        page._preview_timer.isActive() or page._preview_thread is not None
    ) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    assert not page._preview_timer.isActive()
    assert page._preview_thread is None


def _preview_pixel(page: QuickEditPage) -> tuple[int, int, int, int]:
    image = page.drop_zone.preview._item.pixmap().toImage()
    color = image.pixelColor(0, 0)
    return color.red(), color.green(), color.blue(), color.alpha()


def _install_recolor(page: QuickEditPage, source: Image.Image, color_count: int = 6) -> None:
    mapping = extract_palette(source, color_count)
    page._palette_values = mapping.palette
    page._palette_replacements = ((255, 0, 0),) * len(mapping.palette)
    page._palette_mapping = mapping.indices
    page._palette_mapping_size = (mapping.width, mapping.height)
    page._palette_mapping_digest = mapping.digest
    page._palette_extracted_color_count = color_count
    page._palette_needs_reextract = False
    page.palette_enabled.setChecked(True)
    page._rebuild_palette_chips()
    page._control_changed()


def _pixel_document_state(page) -> tuple:
    return (
        hashlib.sha256(page.canvas.snapshot()).hexdigest(),
        page.canvas.history._index,
        len(page.canvas.history._entries),
        page.reference,
        page.source_path,
        page.filename_edit.text(),
        page.output_folder,
        page.zoom_combo.currentIndex(),
        page.grid_check.isChecked(),
        page.canvas_view.color,
    )


class ControlledPaletteThread(QObject):
    succeeded = Signal(object)
    failed = Signal(str, object)
    finished = Signal()
    instances = []

    def __init__(
        self, source, settings, generation, request_id, source_identity, settings_signature
    ) -> None:
        super().__init__()
        self.source = source
        self.settings = settings
        self.generation = generation
        self.request_id = request_id
        self.source_identity = source_identity
        self.settings_signature = settings_signature
        self.interrupted = False

    def start(self) -> None:
        self.instances.append(self)

    def requestInterruption(self) -> None:
        self.interrupted = True


def _palette_thread_token(thread: ControlledPaletteThread) -> tuple:
    return (
        thread.generation,
        thread.request_id,
        thread.source_identity,
        thread.settings_signature,
    )


def test_palette_count_options_default_and_model_contract(app: QApplication) -> None:
    page = QuickEditPage()
    try:
        assert [
            page.palette_count_combo.itemData(index)
            for index in range(page.palette_count_combo.count())
        ] == [5, 6, 8, 12]
        assert page.palette_count_combo.currentData() == 6
        assert page.settings().palette.color_count == 6
        assert page.palette_intro_label.text() == (
            "5 / 6 / 8 / 12色から選べます。"
            "色数を増やすと、近い色を細かく分けられます。"
        )
        page._set_processing(True)
        assert not page.palette_count_combo.isEnabled()
        assert not page.palette_extract_button.isEnabled()
        page._set_processing(False)
        assert PaletteSettings(color_count=12).color_count == 12
        with pytest.raises(ValueError):
            PaletteSettings(color_count=7)
    finally:
        page.close()


def test_extract_quantize_recolor_blend_and_alpha_contract_at_12() -> None:
    source = _bands()
    source.putpixel((0, 0), (250, 1, 200, 0))
    mapping = extract_palette(source, 12)
    assert len(mapping.palette) == 12
    assert len(mapping.indices) == source.width * source.height
    assert mapping.indices[0] == -1
    quantized, quantized_mapping = quantize_image(source, 12)
    assert len(quantized_mapping.palette) == 12
    replacements = tuple(reversed(mapping.palette))
    results = (
        apply_palette_mapping(source, mapping, replacements),
        apply_palette_mapping_smooth(source, mapping, replacements),
        apply_palette_mapping_preserve_shading(source, mapping, replacements),
        quantized,
    )
    original_alpha = source.getchannel("A").tobytes()
    for result in results:
        assert result.size == source.size
        assert result.getchannel("A").tobytes() == original_alpha
        assert result.getpixel((0, 0)) == (250, 1, 200, 0)
    assert results[0].tobytes() != source.tobytes()


def test_12_fixed_index_recolor_reset_and_history(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping = extract_palette(_bands(), 12)
    settings = EditSettings(
        palette=PaletteSettings(
            enabled=True,
            quantize_enabled=True,
            color_count=12,
            palette=mapping.palette,
            replacements=mapping.palette,
            mapping=mapping.indices,
            mapping_width=mapping.width,
            mapping_height=mapping.height,
            mapping_digest=mapping.digest,
            blend_mode=RecolorBlendMode.PRESERVE_SHADING,
        )
    )
    page = QuickEditPage()
    try:
        page.apply_settings(settings)
        page._control_changed()
        base = page._history_index
        chosen = QColor(7, 111, 213)
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *args, **kwargs: chosen)
        page._replace_palette_color(11)
        assert page._history_index == base + 1
        assert page.settings().palette.replacements[:11] == mapping.palette[:11]
        assert page.settings().palette.replacements[11] == (7, 111, 213)
        assert page.settings().palette.mapping == mapping.indices
        assert page.settings().palette.mapping_digest == mapping.digest
        assert page.settings().palette.quantize_enabled
        assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
        page.undo()
        assert page.settings().palette.replacements == mapping.palette
        page.redo()
        assert page.settings().palette.replacements[11] == (7, 111, 213)

        reset_base = page._history_index
        page._reset_palette_color(11)
        assert page._history_index == reset_base + 1
        assert page.settings().palette.replacements == mapping.palette
        page.undo()
        assert page.settings().palette.replacements[11] == (7, 111, 213)
        page.redo()

        changed = list(mapping.palette)
        for index in (1, 4, 9):
            changed[index] = ((index * 17) % 256, (index * 31) % 256, (index * 47) % 256)
        page._commit_palette_replacements(tuple(changed), selected_index=9, feedback="test")
        before_all_reset = page._history_index
        page.reset_palette()
        assert page._history_index == before_all_reset + 1
        assert page.settings().palette.replacements == mapping.palette
        page.undo()
        assert page.settings().palette.replacements == tuple(changed)
        page.redo()
        assert page.settings().palette.replacements == mapping.palette
        assert page.settings().palette.blend_mode is RecolorBlendMode.PRESERVE_SHADING
    finally:
        page.close()


def test_palette_extraction_busy_keeps_only_latest_request(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source = tmp_path / "latest-palette.png"
    _bands().save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_page_idle(app, page)
        page.outline_enabled.setChecked(True)
        page.sticker_enabled.setChecked(True)
        page.line_art_enabled.setChecked(True)
        page.line_art_background_combo.setCurrentIndex(
            page.line_art_background_combo.count() - 1
        )
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(5))
        page.extract_palette()
        assert len(ControlledPaletteThread.instances) == 1
        first = ControlledPaletteThread.instances[0]
        first_activity_token = page._palette_activity_token
        assert first_activity_token is not None
        assert page.palette_count_combo.isEnabled()
        assert page.palette_extract_button.isEnabled()
        assert not page.filter_combo.isEnabled()
        assert not page.current_source_card.change_button.isEnabled()
        protected_conditional_controls = (
            page.outline_color_button,
            page.outline_width_spin,
            page.sticker_outline_width_spin,
            page.sticker_outline_color_button,
            page.sticker_shadow_enabled,
            page.sticker_prereq_button,
            page.line_art_color_button,
            page.line_art_background_combo,
            page.line_art_background_color_button,
            page.original_button,
            page.edited_button,
        )
        assert not any(widget.isEnabled() for widget in protected_conditional_controls)

        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(8))
        assert not any(widget.isEnabled() for widget in protected_conditional_controls)
        assert not page._preview_timer.isActive()
        QTest.qWait(180)
        app.processEvents()
        assert page._preview_thread is None
        assert page._palette_activity_token == first_activity_token
        assert page.preview_activity._token == first_activity_token
        page.extract_palette()
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        assert page._palette_pending_request is not None
        assert page._palette_pending_request[1].palette.color_count == 12
        assert page._palette_activity_token == first_activity_token
        assert page.preview_activity._token == first_activity_token
        QTest.qWait(180)
        app.processEvents()
        assert page._preview_thread is None
        assert page._palette_activity_token == first_activity_token

        first_token = _palette_thread_token(first)
        page.preview_status.setText("処理中…")
        first.failed.emit("古い失敗", first_token)
        first.succeeded.emit((*first_token, extract_palette(_bands(), 5)))
        assert page._palette_values == ()
        assert page.preview_status.text() == "処理中…"
        first.finished.emit()
        app.processEvents()

        assert len(ControlledPaletteThread.instances) == 2
        latest = ControlledPaletteThread.instances[1]
        assert latest.settings.palette.color_count == 12
        latest_mapping = extract_palette(_bands(), 12)
        latest_token = _palette_thread_token(latest)
        latest.succeeded.emit((*latest_token, latest_mapping))
        latest.finished.emit()
        app.processEvents()
        assert page._palette_thread is None
        assert page._palette_pending_request is None
        assert page._palette_values == latest_mapping.palette
        assert page._palette_extracted_color_count == 12
        assert page._palette_activity_token is None
        assert page.palette_extract_button.isEnabled()
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_latest_missing_invalidates_active_pending_and_can_restore(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    warnings = []
    monkeypatch.setattr(
        edit_ui_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    source = tmp_path / "missing-latest.png"
    _bands().save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_page_idle(app, page)
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(5))
        page.extract_palette()
        first = ControlledPaletteThread.instances[0]
        first_token = _palette_thread_token(first)
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(8))
        page.extract_palette()
        assert page._palette_pending_request is not None

        source.unlink()
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        assert page._palette_pending_request is None
        assert page._palette_active_request is None
        assert first.interrupted
        assert page._palette_activity_token is None
        assert page.preview_status.text() == edit_ui_module.MISSING_SOURCE_MESSAGE
        assert warnings == [("元画像が見つかりません", edit_ui_module.MISSING_SOURCE_MESSAGE)]
        first.succeeded.emit((*first_token, extract_palette(_bands(), 5)))
        assert page._palette_values == ()

        _bands().save(source)
        page.extract_palette()
        assert page._palette_pending_request is not None
        assert page._palette_pending_request[1].palette.color_count == 12
        first.finished.emit()
        app.processEvents()
        assert len(ControlledPaletteThread.instances) == 2
        restored = ControlledPaletteThread.instances[1]
        restored_mapping = extract_palette(_bands(), 12)
        restored.succeeded.emit((*_palette_thread_token(restored), restored_mapping))
        restored.finished.emit()
        _wait_page_idle(app, page)
        assert page._palette_values == restored_mapping.palette
        assert page._palette_extracted_color_count == 12
        assert page.palette_extract_button.isEnabled()
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_cancel_drops_pending_and_old_result_cannot_commit(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source = tmp_path / "cancel-pending.png"
    _bands().save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_page_idle(app, page)
        page.extract_palette()
        first = ControlledPaletteThread.instances[0]
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        assert page._palette_pending_request is not None
        page.cancel_palette_extraction()
        assert first.interrupted
        assert page._palette_pending_request is None
        assert page._palette_active_request is None
        assert page._palette_activity_token is None
        first.succeeded.emit((*_palette_thread_token(first), extract_palette(_bands(), 6)))
        first.finished.emit()
        app.processEvents()
        assert len(ControlledPaletteThread.instances) == 1
        assert page._palette_values == ()
        assert page.palette_extract_button.isEnabled()
    finally:
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_source_replacement_cancels_active_and_pending_palette_requests(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    first_source = tmp_path / "replace-first.png"
    second_source = tmp_path / "replace-second.png"
    _bands().save(first_source)
    Image.new("RGB", (24, 18), (20, 60, 180)).save(second_source)
    page = QuickEditPage()
    try:
        assert page.load_image(first_source)
        _wait_page_idle(app, page)
        page.extract_palette()
        first = ControlledPaletteThread.instances[0]
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        assert page._palette_pending_request is not None

        assert page.load_image(second_source)
        assert first.interrupted
        assert page.source_path == second_source.resolve()
        assert page._palette_active_request is None
        assert page._palette_pending_request is None
        first.succeeded.emit((*_palette_thread_token(first), extract_palette(_bands(), 6)))
        first.finished.emit()
        _wait_page_idle(app, page)
        assert page._palette_values == ()
        assert page.palette_extract_button.isEnabled()
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_pending_latest_worker_failure_is_terminal_and_localized(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source = tmp_path / "pending-failure.png"
    _bands().save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_page_idle(app, page)
        page.extract_palette()
        first = ControlledPaletteThread.instances[0]
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        first.finished.emit()
        app.processEvents()
        latest = ControlledPaletteThread.instances[1]
        latest.failed.emit("代表色を抽出できませんでした。", _palette_thread_token(latest))
        latest.finished.emit()
        app.processEvents()
        assert page._palette_thread is None
        assert page._palette_pending_request is None
        assert page._palette_values == ()
        assert page._palette_activity_token is None
        assert page.preview_status.text() == "代表色を抽出できませんでした。"
        assert "c62828" in page.preview_status.styleSheet()
        assert page.palette_extract_button.isEnabled()
        assert page.palette_count_combo.isEnabled()
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_count_change_during_palette_worker_refreshes_cleared_preview(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source_image = _bands()
    source = tmp_path / "count-refresh.png"
    source_image.save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_preview_cycle(app, page)
        original_pixel = _preview_pixel(page)
        _install_recolor(page, source_image)
        _wait_preview_cycle(app, page)
        assert _preview_pixel(page) != original_pixel

        page.extract_palette()
        active = ControlledPaletteThread.instances[0]
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        assert page._palette_values == ()
        assert page._palette_preview_refresh_pending
        assert not page._preview_timer.isActive()

        active.succeeded.emit((*_palette_thread_token(active), extract_palette(source_image, 6)))
        assert page.preview_activity.status_label.text() == "キャンセルしました"
        active.finished.emit()
        app.processEvents()
        assert page.preview_activity.status_label.text() == "キャンセルしました"
        _wait_preview_cycle(app, page)

        assert page._palette_values == ()
        assert not page.settings().palette.enabled
        assert _preview_pixel(page) == original_pixel
        assert "代表色を再抽出してください" in page.preview_status.text()
        assert page.preview_activity.status_label.text() == "キャンセルしました"
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_pending_palette_failure_refreshes_preview_and_keeps_error_feedback(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source_image = _bands()
    source = tmp_path / "failure-refresh.png"
    source_image.save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_preview_cycle(app, page)
        original_pixel = _preview_pixel(page)
        _install_recolor(page, source_image)
        _wait_preview_cycle(app, page)
        assert _preview_pixel(page) != original_pixel

        page.extract_palette()
        first = ControlledPaletteThread.instances[0]
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        first.finished.emit()
        app.processEvents()
        latest = ControlledPaletteThread.instances[1]
        error_message = "代表色を抽出できませんでした。"
        latest.failed.emit(error_message, _palette_thread_token(latest))
        assert page.preview_activity.status_label.text() == "処理に失敗しました"
        latest.finished.emit()
        app.processEvents()
        assert page.preview_activity.status_label.text() == "処理に失敗しました"
        _wait_preview_cycle(app, page)

        assert page._palette_values == ()
        assert not page.settings().palette.enabled
        assert _preview_pixel(page) == original_pixel
        assert page.preview_status.text() == error_message
        assert "c62828" in page.preview_status.styleSheet()
        assert page.preview_activity.status_label.text() == "処理に失敗しました"
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_initial_palette_failure_promotes_armed_preview_refresh(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    source_image = _bands()
    source = tmp_path / "initial-failure-refresh.png"
    source_image.save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_preview_cycle(app, page)
        original_pixel = _preview_pixel(page)
        _install_recolor(page, source_image)
        _wait_preview_cycle(app, page)
        assert _preview_pixel(page) != original_pixel

        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        assert page._preview_timer.isActive()
        page.extract_palette()
        assert page._palette_preview_refresh_pending
        assert not page._preview_timer.isActive()
        worker = ControlledPaletteThread.instances[0]
        error_message = "代表色を抽出できませんでした。"
        worker.failed.emit(error_message, _palette_thread_token(worker))
        worker.finished.emit()
        _wait_preview_cycle(app, page)

        assert _preview_pixel(page) == original_pixel
        assert page.preview_status.text() == error_message
        assert "c62828" in page.preview_status.styleSheet()
        assert not page._palette_preview_refresh_pending
        assert page._palette_preview_handoff_generation is None
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_initial_palette_stat_failure_clears_stale_preview_and_handoff(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    warnings = []
    monkeypatch.setattr(
        edit_ui_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    source_image = _bands()
    source = tmp_path / "initial-stat-missing.png"
    source_image.save(source)
    page = QuickEditPage()
    try:
        assert page.load_image(source)
        _wait_preview_cycle(app, page)
        _install_recolor(page, source_image)
        _wait_preview_cycle(app, page)
        assert not page.drop_zone.preview._item.pixmap().isNull()

        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        assert page._preview_timer.isActive()
        source.unlink()
        page.extract_palette()
        _wait_preview_cycle(app, page)

        assert ControlledPaletteThread.instances == []
        assert page.drop_zone.preview._item.pixmap().isNull()
        assert page.preview_status.text() == edit_ui_module.MISSING_SOURCE_MESSAGE
        assert warnings == [("元画像が見つかりません", edit_ui_module.MISSING_SOURCE_MESSAGE)]
        assert not page._palette_preview_refresh_pending
        assert page._palette_terminal_preview_status is None
        assert not page._palette_terminal_activity_pending
        assert not page._preview_refresh_without_activity
        assert page._palette_preview_handoff_generation is None
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


@pytest.mark.parametrize("terminal", ["failure", "cancel"])
def test_palette_terminal_handoff_cannot_leak_across_source_replacement(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    ControlledPaletteThread.instances = []
    monkeypatch.setattr(edit_ui_module, "PaletteExtractionThread", ControlledPaletteThread)
    first_source = tmp_path / f"handoff-{terminal}-first.png"
    second_source = tmp_path / f"handoff-{terminal}-second.png"
    _bands().save(first_source)
    Image.new("RGB", (18, 14), (0, 0, 255)).save(second_source)
    page = QuickEditPage()
    try:
        assert page.load_image(first_source)
        _wait_preview_cycle(app, page)
        page.palette_count_combo.setCurrentIndex(page.palette_count_combo.findData(12))
        page.extract_palette()
        worker = ControlledPaletteThread.instances[0]
        if terminal == "failure":
            worker.failed.emit("古い代表色エラー", _palette_thread_token(worker))
        else:
            page.cancel_palette_extraction()
        worker.finished.emit()
        app.processEvents()
        assert page._preview_timer.isActive()

        assert page.load_image(second_source)
        _wait_preview_cycle(app, page)

        assert page.source_path == second_source.resolve()
        assert _preview_pixel(page)[:3] == (0, 0, 255)
        assert "古い代表色エラー" not in page.preview_status.text()
        assert page.preview_status.text().startswith("加工後 · Preview")
        assert not page._palette_preview_refresh_pending
        assert page._palette_terminal_preview_status is None
        assert not page._palette_terminal_activity_pending
        assert not page._preview_refresh_without_activity
        assert page._palette_preview_handoff_generation is None
    finally:
        page.cancel_palette_extraction()
        if page._palette_thread is not None:
            page._palette_thread.finished.emit()
        _wait_page_idle(app, page)
        page.close()


def test_12_palette_handoff_order_state_and_clear(
    app: QApplication, tmp_path: Path
) -> None:
    window = MainWindow()
    try:
        pixel = window.pixel_page
        pixel.canvas.stroke((0, 0), (3, 0), (20, 30, 40, 255))
        pixel._refresh()
        pixel.filename_edit.setText("keep-document")
        pixel.output_folder = tmp_path
        pixel.zoom_combo.setCurrentIndex(3)
        pixel.grid_check.setChecked(False)
        before = _pixel_document_state(pixel)

        edit = window.edit_page
        edit._palette_values = COLORS_12
        edit._palette_replacements = tuple(reversed(COLORS_12))
        edit._rebuild_palette_chips()
        edit.send_palette_to_pixel()
        app.processEvents()
        expected = tuple(reversed(COLORS_12))
        assert pixel._received_palette == expected
        assert [button.toolTip() for button in pixel._palette_chip_buttons] == [
            f"#{red:02X}{green:02X}{blue:02X}" for red, green, blue in expected
        ]
        assert len(pixel._palette_chip_buttons) == 12
        assert pixel.palette_count_label.text() == "12色"
        assert _pixel_document_state(pixel) == before

        pixel.clear_palette()
        assert pixel._received_palette == ()
        assert pixel.palette_count_label.text() == "0色"
        assert _pixel_document_state(pixel) == before
    finally:
        window.close()


@pytest.mark.parametrize("width", [900, 1180, 1440])
def test_12_palette_rows_and_pixel_chips_fit_at_supported_widths(
    app: QApplication, width: int
) -> None:
    window = MainWindow()
    try:
        window.resize(width, 760)
        edit = window.edit_page
        edit._palette_values = COLORS_12
        edit._palette_replacements = tuple(reversed(COLORS_12))
        edit._rebuild_palette_chips()
        edit.material_section.set_expanded(True)
        window.navigation.setCurrentIndex(window.image_edit_tab)
        window.show()
        app.processEvents()
        assert edit.settings_scroll.horizontalScrollBar().maximum() == 0
        assert edit.settings_scroll.widget().width() <= edit.settings_scroll.viewport().width()
        assert len(edit._palette_reset_buttons) == 12
        for button in edit._palette_reset_buttons:
            right = button.mapTo(edit.settings_scroll.widget(), QPoint(0, 0)).x() + button.width()
            assert button.width() >= 26
            assert right <= edit.settings_scroll.widget().width() + 2
        assert edit.drop_zone.preview.width() >= {900: 380, 1180: 560, 1440: 701}[width]

        pixel = window.pixel_page
        pixel.receive_palette(COLORS_12)
        window.navigation.setCurrentIndex(window.pixel_tab)
        app.processEvents()
        scroll = pixel.findChild(QScrollArea, "pixelSettingsScroll")
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.widget().width() <= scroll.viewport().width()
        assert len(pixel._palette_chip_buttons) == 12
        positions = [pixel.palette_chips_layout.getItemPosition(index)[:2] for index in range(12)]
        assert positions == [(index // 6, index % 6) for index in range(12)]
        for index, button in enumerate(pixel._palette_chip_buttons):
            assert button.size().width() == button.size().height() == 28
            assert button.accessibleName().startswith(f"パレット {index + 1}:")
            right = button.mapTo(scroll.widget(), QPoint(0, 0)).x() + button.width()
            assert right <= scroll.widget().width() + 2
        assert pixel.canvas_view.width() == {900: 374, 1180: 654, 1440: 914}[width]
    finally:
        window.close()


def _qa_images() -> dict[str, Image.Image]:
    width, height = 320, 240
    pale = Image.new("RGB", (width, height))
    pale_pixels = []
    for y in range(height):
        for x in range(width):
            pale_pixels.append((210 + x % 38, 218 + y % 30, 224 + (x + y) % 28))
    pale.putdata(pale_pixels)

    colorful = Image.new("RGB", (width, height))
    colorful_draw = ImageDraw.Draw(colorful)
    regions = (
        (40, (232, 38, 48)),
        (38, (38, 188, 72)),
        (35, (36, 82, 222)),
        (32, (25, 194, 211)),
        (30, (242, 211, 32)),
        (28, (221, 42, 177)),
        (26, (241, 119, 24)),
        (24, (126, 54, 212)),
        (20, (145, 211, 35)),
        (18, (24, 139, 136)),
        (16, (245, 108, 155)),
        (13, (24, 40, 111)),
    )
    cursor = 0
    for region_width, color in regions:
        colorful_draw.rectangle(
            (cursor, 0, cursor + region_width - 1, height - 1), fill=color
        )
        cursor += region_width
    assert cursor == width

    photo_like = Image.new("RGB", (width, height))
    photo_pixels = []
    horizon = 132
    for y in range(height):
        for x in range(width):
            if y < horizon:
                vertical = y / max(1, horizon - 1)
                horizontal = x / max(1, width - 1)
                photo_pixels.append(
                    (
                        round(66 + 88 * vertical + 10 * horizontal),
                        round(112 + 79 * vertical + 8 * horizontal),
                        round(184 + 47 * vertical),
                    )
                )
            else:
                vertical = (y - horizon) / max(1, height - horizon - 1)
                photo_pixels.append(
                    (
                        round(158 + 54 * vertical),
                        round(96 + 45 * vertical),
                        round(58 + 27 * vertical),
                    )
                )
    photo_like.putdata(photo_pixels)
    scene = ImageDraw.Draw(photo_like)
    scene.rectangle((22, 22, 92, 91), fill=(225, 226, 191), outline=(245, 240, 213), width=5)
    scene.line((0, horizon, width, horizon), fill=(91, 72, 67), width=4)
    scene.ellipse((74, 172, 252, 224), fill=(55, 44, 49))
    scene.rounded_rectangle((104, 78, 190, 201), radius=24, fill=(27, 117, 142), outline=(16, 66, 88), width=5)
    scene.rounded_rectangle((119, 88, 141, 185), radius=10, fill=(92, 186, 196))
    scene.ellipse((202, 139, 289, 224), fill=(218, 99, 41), outline=(139, 49, 30), width=5)
    scene.ellipse((219, 153, 251, 181), fill=(248, 174, 83))
    scene.polygon(((193, 141), (216, 115), (233, 147)), fill=(48, 122, 55))
    scene.rectangle((34, 157, 96, 208), fill=(91, 54, 137), outline=(49, 34, 82), width=4)
    scene.rectangle((42, 164, 88, 176), fill=(190, 142, 214))

    rng = random.Random(20260827)
    textured = []
    for red, green, blue in photo_like.get_flattened_data():
        delta = rng.randint(-4, 4)
        textured.append(
            tuple(max(0, min(255, channel + delta)) for channel in (red, green, blue))
        )
    photo_like.putdata(textured)
    return {"pale": pale, "colorful": colorful, "photo-like": photo_like}


def _hue_families(palette) -> set[int]:
    families = set()
    for red, green, blue in palette:
        hue, saturation, _value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if saturation >= 0.35:
            families.add(int(hue * 6 + 0.5) % 6)
    return families


def _luminance(color) -> float:
    red, green, blue = color
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


@pytest.mark.parametrize(
    ("source_size", "expected_size"),
    [((512, 384), (512, 384)), ((2048, 1536), (1024, 768))],
)
def test_palette_worker_keeps_small_and_downsamples_large_before_12_color_extraction(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    source_size: tuple[int, int],
    expected_size: tuple[int, int],
) -> None:
    source = Image.new("RGBA", source_size, (30, 80, 140, 255))
    seen = {}

    monkeypatch.setattr(edit_ui_module, "load_normalized", lambda _path: source.copy())

    def recording_extract(image: Image.Image, color_count: int) -> PaletteMapping:
        seen["size"] = image.size
        seen["count"] = color_count
        return PaletteMapping(((30, 80, 140),), (), image.width, image.height, "recorded")

    monkeypatch.setattr(edit_ui_module, "extract_palette", recording_extract)
    settings = EditSettings(palette=PaletteSettings(color_count=12))
    worker = edit_ui_module.PaletteExtractionThread(
        Path("large-source.png"),
        settings,
        3,
        7,
        ("large-source.png", 1, 1),
        ("none", settings.transparency, 12),
    )
    payloads = []
    failures = []
    worker.succeeded.connect(payloads.append)
    worker.failed.connect(lambda message, token: failures.append((message, token)))
    worker.run()
    assert failures == []
    assert seen == {"size": expected_size, "count": 12}
    assert payloads and payloads[0][-1].width == expected_size[0]
    assert payloads[0][-1].height == expected_size[1]


def test_palette_qa_records_counts_separation_and_timing() -> None:
    records = []
    for category, image in _qa_images().items():
        actual_counts = []
        for requested in (5, 6, 8, 12):
            started = perf_counter()
            mapping = extract_palette(image, requested)
            elapsed_ms = (perf_counter() - started) * 1000.0
            actual = len(mapping.palette)
            separations = [
                sum((left[channel] - right[channel]) ** 2 for channel in range(3)) ** 0.5
                for index, left in enumerate(mapping.palette)
                for right in mapping.palette[index + 1 :]
            ]
            minimum_separation = min(separations) if separations else 0.0
            assert actual == requested
            assert len(set(mapping.palette)) == actual
            assert minimum_separation > 0.0 or actual == 1
            assert elapsed_ms >= 0.0
            if category == "pale":
                assert min(min(color) for color in mapping.palette) >= 205
            elif category == "colorful":
                families = _hue_families(mapping.palette)
                assert len(families) >= (4 if requested == 5 else 5)
                if requested == 12:
                    assert families == set(range(6))
            else:
                assert any(red - blue >= 30 for red, _green, blue in mapping.palette)
                assert any(blue - red >= 30 for red, _green, blue in mapping.palette)
                luminances = [_luminance(color) for color in mapping.palette]
                assert max(luminances) - min(luminances) >= 70
            actual_counts.append(actual)
            palette_hex = ",".join(
                f"#{red:02X}{green:02X}{blue:02X}" for red, green, blue in mapping.palette
            )
            records.append(
                f"{category}:{requested}->{actual}, palette=[{palette_hex}], "
                f"min-separation={minimum_separation:.1f}, {elapsed_ms:.2f}ms"
            )
        assert actual_counts == sorted(actual_counts)
    print("PALETTE_QA " + " | ".join(records))
