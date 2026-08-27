from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from quick_processing_tool.edit_ui import QuickEditPage
from quick_processing_tool.upscale_ui import UpscalePage
from quick_processing_tool.upscaler.backend import BackendAvailability, UpscaleBackend
from quick_processing_tool.upscaler.batch import BatchCallbacks, BatchJob, BatchOutcome, QueueStatus, run_sequential_batch
from quick_processing_tool.upscaler.guard import LARGE_OUTPUT_WARNING_PIXELS, projected_output
from quick_processing_tool.upscaler.models import UpscaleMode, UpscaleOptions
from quick_processing_tool.upscaler.service import UpscaleService


class CountingBackend(UpscaleBackend):
    name = "Counting backend"

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(True, user_message="available")

    def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("intentional failure")
        with Image.open(input_path) as source:
            source.resize((source.width * scale, source.height * scale)).save(output_path, format="PNG")
        progress(100)


@pytest.fixture
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def source_path(tmp_path: Path) -> Path:
    path = tmp_path / "source.png"
    Image.new("RGB", (8, 6), "navy").save(path)
    return path


def _callbacks(statuses: list[tuple[int, QueueStatus, str]], progress: list[tuple[int, int]]) -> BatchCallbacks:
    return BatchCallbacks(
        status=lambda index, status, detail: statuses.append((index, status, detail)),
        current=lambda *_args: None,
        result=lambda *_args: None,
        progress=lambda done, total: progress.append((done, total)),
    )


def test_projected_output_warns_at_inclusive_boundary() -> None:
    below = projected_output(9_999, 10_000, 1)
    exact = projected_output(10_000, 10_000, 1)
    example = projected_output(5_964, 4_220, 2)
    assert below.total_pixels == LARGE_OUTPUT_WARNING_PIXELS - 10_000
    assert not below.is_large
    assert exact.total_pixels == LARGE_OUTPUT_WARNING_PIXELS
    assert exact.is_large
    assert (example.width, example.height) == (11_928, 8_440)
    assert example.is_large


def test_batch_skips_warned_items_and_continues_normal_jobs(source_path: Path, tmp_path: Path) -> None:
    backend = CountingBackend()
    skipped = BatchJob(0, source_path, tmp_path / "out")
    normal = BatchJob(1, source_path, tmp_path / "out")
    statuses: list[tuple[int, QueueStatus, str]] = []
    progress: list[tuple[int, int]] = []
    outcome = run_sequential_batch(
        UpscaleService(backend), [normal], UpscaleOptions(scale=2), Event(),
        _callbacks(statuses, progress), [skipped],
    )
    assert backend.calls == 1
    assert (outcome.total, outcome.succeeded, outcome.failed, outcome.skipped) == (2, 1, 0, 1)
    assert statuses[0][0:2] == (0, QueueStatus.SKIPPED)
    assert any(index == 1 and status is QueueStatus.DONE for index, status, _ in statuses)
    assert progress[-1] == (2, 2)


def test_all_warned_items_skip_without_backend(source_path: Path, tmp_path: Path) -> None:
    backend = CountingBackend()
    jobs = [BatchJob(index, source_path, tmp_path / "out") for index in range(2)]
    statuses: list[tuple[int, QueueStatus, str]] = []
    progress: list[tuple[int, int]] = []
    outcome = run_sequential_batch(
        UpscaleService(backend), [], UpscaleOptions(scale=2), Event(),
        _callbacks(statuses, progress), jobs,
    )
    assert backend.calls == 0
    assert (outcome.total, outcome.succeeded, outcome.failed, outcome.skipped) == (2, 0, 0, 2)
    assert {index for index, status, _ in statuses if status is QueueStatus.SKIPPED} == {0, 1}
    assert progress[-1] == (2, 2)


def test_partial_failure_keeps_skipped_separate(source_path: Path, tmp_path: Path) -> None:
    backend = CountingBackend(fail_on_call=2)
    skipped = BatchJob(0, source_path, tmp_path / "out")
    normal = [BatchJob(1, source_path, tmp_path / "out"), BatchJob(2, source_path, tmp_path / "out")]
    statuses: list[tuple[int, QueueStatus, str]] = []
    progress: list[tuple[int, int]] = []
    outcome = run_sequential_batch(
        UpscaleService(backend), normal, UpscaleOptions(scale=2), Event(),
        _callbacks(statuses, progress), [skipped],
    )
    assert (outcome.succeeded, outcome.failed, outcome.skipped) == (1, 1, 1)
    assert any(index == 0 and status is QueueStatus.SKIPPED for index, status, _ in statuses)
    assert any(index == 2 and status is QueueStatus.FAILED for index, status, _ in statuses)


def test_upscale_completion_counts_skips_as_completed(qt_app: QApplication) -> None:
    page = UpscalePage(UpscaleService(CountingBackend()))
    page._on_completed(BatchOutcome(2, 0, 0, 0, 0.0, 2))
    assert page.progress.value() == 2
    assert "2 / 2" in page.progress_label.text()
    page.close()


def test_edit_settings_scroll_is_vertical_and_sections_can_all_stay_open(qt_app: QApplication) -> None:
    page = QuickEditPage()
    page.resize(900, 620)
    page.show()
    qt_app.processEvents()
    for section in page.sections:
        section.toggle.setChecked(True)
    qt_app.processEvents()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert page.settings_scroll.verticalScrollBar().maximum() > 0
    assert all(section.toggle.isChecked() for section in page.sections)
    page.close()


def test_main_window_leaving_edit_tab_finishes_ime(qt_app: QApplication) -> None:
    from quick_processing_tool.ui import MainWindow

    window = MainWindow()
    window.show()
    window.navigation.setCurrentIndex(window.image_edit_tab)
    page = window.edit_page
    page.text_enabled.setChecked(True)
    page.text_edit.setPlainText("タブ移動で確定")
    page.text_edit.setFocus()
    window.navigation.setCurrentIndex(window.quick_tab)
    qt_app.processEvents()
    assert page.text_edit.toPlainText() == "タブ移動で確定"
    assert not page.text_edit.hasFocus()
    window.close()


def test_edit_image_replacement_resets_text_and_preserves_committed_text(qt_app: QApplication, tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (20, 12), "red").save(first)
    Image.new("RGB", (24, 16), "blue").save(second)
    page = QuickEditPage()
    page.load_image(first)
    page.text_enabled.setChecked(True)
    page.text_edit.setPlainText("確定した文字")
    page.text_edit.setFocus()
    page.finish_ime(clear_focus=True)
    assert page.text_edit.toPlainText() == "確定した文字"
    page.load_image(second)
    assert page.source_path == second.resolve()
    assert page.settings().text.enabled is False
    assert page.text_edit.toPlainText() == ""
    deadline = time.monotonic() + 3.0
    while page._preview_thread is not None and time.monotonic() < deadline:
        qt_app.processEvents()
    assert page._preview_thread is None
    page.close()
