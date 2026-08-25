from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from quick_processing_tool.upscale_ui import UpscalePage
from quick_processing_tool.upscaler.backend import BackendAvailability, UpscaleBackend
from quick_processing_tool.upscaler.batch import (
    BatchCallbacks,
    BatchJob,
    QueueStatus,
    run_sequential_batch,
)
from quick_processing_tool.upscaler.errors import UpscaleCancelledError, UpscaleProcessingError
from quick_processing_tool.upscaler.models import UpscaleMode, UpscaleOptions, UpscaleOutputFormat
from quick_processing_tool.upscaler.service import UpscaleService


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class TrackingBackend(UpscaleBackend):
    name = "Tracking backend"

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(True, user_message="高画質化エンジンを利用できます")

    def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
        self.calls += 1
        call = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if cancel_event.is_set():
                raise UpscaleCancelledError("cancelled")
            if call == self.fail_on_call:
                raise UpscaleProcessingError("intentional failure")
            with Image.open(input_path) as source:
                output = source.resize(
                    (source.width * scale, source.height * scale),
                    Image.Resampling.NEAREST,
                )
                output.save(output_path, format="PNG")
            progress(100)
        finally:
            self.active -= 1


def make_source(path: Path, size: tuple[int, int] = (11, 7), mode: str = "RGB") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (31, 87, 143, 109) if mode == "RGBA" else (31, 87, 143)
    Image.new(mode, size, color).save(path)
    return path


def run_batch(
    sources: list[Path],
    destination: Path | None = None,
    *,
    backend: TrackingBackend | None = None,
    cancel_after: int | None = None,
    output_format: UpscaleOutputFormat = UpscaleOutputFormat.SAME,
):
    backend = backend or TrackingBackend()
    cancel = Event()
    statuses: list[tuple[int, QueueStatus, str]] = []
    currents: list[tuple[int, int, int, str]] = []
    results = {}
    progress = []

    def got_result(index, result):
        results[index] = result
        if cancel_after is not None and len(results) == cancel_after:
            cancel.set()

    jobs = [
        BatchJob(index, source, destination or source.parent)
        for index, source in enumerate(sources)
    ]
    outcome = run_sequential_batch(
        UpscaleService(backend),
        jobs,
        UpscaleOptions(scale=2, output_format=output_format),
        cancel,
        BatchCallbacks(
            status=lambda index, status, detail: statuses.append((index, status, detail)),
            current=lambda *args: currents.append(args),
            result=got_result,
            progress=lambda done, total: progress.append((done, total)),
        ),
    )
    return outcome, backend, statuses, currents, results, progress


@pytest.mark.parametrize("count", [1, 2, 10])
def test_batch_processes_in_order_with_maximum_concurrency_one(tmp_path: Path, count: int) -> None:
    sources = [make_source(tmp_path / "in" / f"image-{index}.png") for index in range(count)]
    outcome, backend, statuses, currents, results, progress = run_batch(sources, tmp_path / "out")

    assert outcome.total == count
    assert outcome.succeeded == count
    assert outcome.failed == outcome.cancelled == 0
    assert backend.calls == count
    assert backend.max_active == 1
    assert [entry[0] for entry in currents] == list(range(count))
    assert progress[-1] == (count, count)
    assert len(results) == count
    for index, result in results.items():
        assert result.output_path.is_file()
        with Image.open(result.output_path) as reopened:
            reopened.load()
            assert reopened.size == (22, 14)
        assert (index, QueueStatus.DONE, result.output_path.name) in statuses


def test_batch_continues_after_one_item_fails(tmp_path: Path) -> None:
    sources = [make_source(tmp_path / f"item-{index}.png") for index in range(3)]
    outcome, backend, statuses, currents, results, progress = run_batch(
        sources,
        tmp_path / "out",
        backend=TrackingBackend(fail_on_call=2),
    )
    assert backend.calls == 3
    assert outcome.succeeded == 2
    assert outcome.failed == 1
    assert outcome.cancelled == 0
    assert set(results) == {0, 2}
    assert any(index == 1 and status is QueueStatus.FAILED for index, status, _ in statuses)
    assert progress[-1] == (3, 3)
    assert [entry[0] for entry in currents] == [0, 1, 2]


def test_cancel_after_two_keeps_verified_outputs_and_stops_queue(tmp_path: Path) -> None:
    sources = [make_source(tmp_path / "in" / f"cancel-{index}.png") for index in range(5)]
    outcome, backend, statuses, currents, results, progress = run_batch(
        sources,
        tmp_path / "out",
        cancel_after=2,
    )
    assert backend.calls == 2
    assert outcome.succeeded == 2
    assert outcome.failed == 0
    assert outcome.cancelled == 3
    assert set(results) == {0, 1}
    assert len(list((tmp_path / "out").glob("*"))) == 2
    assert [entry[0] for entry in currents] == [0, 1]
    cancelled = {index for index, status, _ in statuses if status is QueueStatus.CANCELLED}
    assert cancelled == {2, 3, 4}
    assert progress[-1] == (2, 5)


def test_duplicate_content_gets_safe_unique_outputs_without_source_change(tmp_path: Path) -> None:
    first = make_source(tmp_path / "a" / "same.png", mode="RGBA")
    second = make_source(tmp_path / "b" / "same.png", mode="RGBA")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)}
    outcome, _, _, _, results, _ = run_batch([first, second], tmp_path / "out")
    assert outcome.succeeded == 2
    names = [results[index].output_path.name for index in (0, 1)]
    assert names == ["same_2x.png", "same_2x_2.png"]
    output_hashes = [hashlib.sha256(results[index].output_path.read_bytes()).hexdigest() for index in (0, 1)]
    assert output_hashes[0] == output_hashes[1]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)} == before

    first_output_hash = hashlib.sha256(results[0].output_path.read_bytes()).hexdigest()
    repeated, _, _, _, repeated_results, _ = run_batch([first, second], tmp_path / "out")
    assert repeated.succeeded == 2
    assert [repeated_results[index].output_path.name for index in (0, 1)] == [
        "same_2x_3.png",
        "same_2x_4.png",
    ]
    assert hashlib.sha256(results[0].output_path.read_bytes()).hexdigest() == first_output_hash


def test_mixed_formats_dimensions_alpha_and_source_folders(tmp_path: Path) -> None:
    sources = [
        make_source(tmp_path / "png" / "wide.png", (17, 5), "RGBA"),
        make_source(tmp_path / "jpeg" / "tall.jpg", (6, 19), "RGB"),
        make_source(tmp_path / "webp" / "square.webp", (9, 9), "RGB"),
    ]
    outcome, _, _, _, results, _ = run_batch(sources)
    assert outcome.succeeded == 3
    expected = [(34, 10), (12, 38), (18, 18)]
    expected_suffixes = [".png", ".jpg", ".webp"]
    for index, result in results.items():
        assert result.output_path.parent == sources[index].parent
        assert result.output_path.suffix == expected_suffixes[index]
        with Image.open(result.output_path) as reopened:
            reopened.load()
            assert reopened.size == expected[index]
            if index == 0:
                assert "A" in reopened.getbands()


def test_queue_add_deduplicate_select_and_clear(app: QApplication, tmp_path: Path) -> None:
    paths = [make_source(tmp_path / f"queue-{index}.png", (10 + index, 8)) for index in range(10)]
    page = UpscalePage(UpscaleService(TrackingBackend()))
    page.load_paths([paths[0]])
    assert len(page.items) == 1
    assert page.start_button.text() == "2倍で高画質化を開始"
    page.load_paths([paths[0], *paths[1:]])
    assert len(page.items) == 10
    assert page.queue.topLevelItemCount() == 10
    assert "重複1枚" in page.queue_feedback.text()
    assert page.start_button.text() == "10枚まとめて2倍で高画質化"
    page.queue.setCurrentItem(page.queue.topLevelItem(7))
    app.processEvents()
    assert page.current_index == 7
    assert page.source_path == paths[7].resolve()
    page.clear_queue()
    assert not page.items
    assert page.queue.topLevelItemCount() == 0
    assert not page.start_button.isEnabled()
    page.close()


def test_drop_zone_emits_all_supported_paths(app: QApplication, tmp_path: Path) -> None:
    paths = [make_source(tmp_path / f"drop-{index}.png") for index in range(2)]
    page = UpscalePage(UpscaleService(TrackingBackend()))
    page.show(); app.processEvents()
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    enter = QDragEnterEvent(
        QPoint(10, 10), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_zone, enter)
    assert enter.isAccepted()
    drop = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_zone, drop); app.processEvents()
    assert drop.isAccepted()
    assert [item.source_path for item in page.items] == [path.resolve() for path in paths]
    page.close()


def test_ui_batch_auto_saves_and_selection_stays_independent(app: QApplication, tmp_path: Path) -> None:
    paths = [make_source(tmp_path / "in" / f"ui-{index}.png", (12 + index, 8)) for index in range(3)]
    destination = tmp_path / "saved"
    page = UpscalePage(UpscaleService(TrackingBackend()))
    page.show(); page.load_paths(paths)
    page.queue.setCurrentItem(page.queue.topLevelItem(1)); app.processEvents()
    page.output_folder = destination; page._output_folder_explicit = True; page.folder_label.set_path(destination)
    page.start()
    deadline = time.monotonic() + 5
    while not page.can_close() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    app.processEvents()
    assert page.can_close()
    assert page.current_index == 1
    assert page._last_outcome is not None and page._last_outcome.succeeded == 3
    assert [item.status for item in page.items] == [QueueStatus.DONE] * 3
    assert all(item.result and item.result.output_path.is_file() for item in page.items)
    assert "3枚の画像を保存しました" in page.result_label.text()
    assert page.saved_path.toolTip() == str(destination)
    assert page.add_button.isEnabled()
    assert page.clear_button.isEnabled()
    page.close()
class ControlledBackend(UpscaleBackend):
    name = "Controlled backend"

    def __init__(self, count: int) -> None:
        self.calls = 0
        self.started = [Event() for _ in range(count)]
        self.release = [Event() for _ in range(count)]
        self.active = 0
        self.max_active = 0

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(True, user_message="高画質化エンジンを利用できます")

    def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
        index = self.calls
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started[index].set()
        try:
            if not self.release[index].wait(3):
                raise RuntimeError("test gate timeout")
            if cancel_event.is_set():
                raise UpscaleCancelledError("cancelled")
            with Image.open(input_path) as source:
                output = source.resize(
                    (source.width * scale, source.height * scale),
                    Image.Resampling.NEAREST,
                )
                output.save(output_path, format="PNG")
            progress(100)
        finally:
            self.active -= 1


def wait_for_ui(app: QApplication, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def test_ui_partial_failure_marks_rows_and_restores_controls(app: QApplication, tmp_path: Path) -> None:
    paths = [make_source(tmp_path / "partial" / f"item-{index}.png") for index in range(3)]
    page = UpscalePage(UpscaleService(TrackingBackend(fail_on_call=2)))
    page.load_paths(paths)
    page.output_folder = tmp_path / "partial-out"
    page._output_folder_explicit = True
    page.start()
    assert wait_for_ui(app, page.can_close)
    assert page._last_outcome is not None
    assert (page._last_outcome.succeeded, page._last_outcome.failed) == (2, 1)
    assert [item.status for item in page.items] == [
        QueueStatus.DONE,
        QueueStatus.FAILED,
        QueueStatus.DONE,
    ]
    assert "2枚保存 / 1件失敗" in page.result_label.text()
    assert page.add_button.isEnabled() and page.clear_button.isEnabled()
    page.close()


def test_ui_inflight_cancel_keeps_two_outputs_and_stops_worker(app: QApplication, tmp_path: Path) -> None:
    paths = [make_source(tmp_path / "cancel-ui" / f"item-{index}.png") for index in range(5)]
    destination = tmp_path / "cancel-ui-out"
    backend = ControlledBackend(5)
    page = UpscalePage(UpscaleService(backend))
    page.load_paths(paths)
    page.output_folder = destination
    page._output_folder_explicit = True
    page.start()
    try:
        assert wait_for_ui(app, backend.started[0].is_set)
        assert not page.can_close()
        assert not page.add_button.isEnabled()
        assert not page.clear_button.isEnabled()

        backend.release[0].set()
        assert wait_for_ui(app, lambda: page.items[0].status is QueueStatus.DONE)
        assert wait_for_ui(app, backend.started[1].is_set)
        backend.release[1].set()
        assert wait_for_ui(app, lambda: page.items[1].status is QueueStatus.DONE)
        assert wait_for_ui(app, backend.started[2].is_set)

        page.cancel()
        backend.release[2].set()
        assert wait_for_ui(app, page.can_close)
        assert backend.calls == 3
        assert backend.max_active == 1
        assert page._last_outcome is not None
        assert page._last_outcome.succeeded == 2
        assert page._last_outcome.cancelled == 3
        assert [item.status for item in page.items] == [
            QueueStatus.DONE,
            QueueStatus.DONE,
            QueueStatus.CANCELLED,
            QueueStatus.CANCELLED,
            QueueStatus.CANCELLED,
        ]
        assert len(list(destination.glob("*"))) == 2
        assert "処理をキャンセルしました" in page.result_label.text()
        assert page.add_button.isEnabled() and page.clear_button.isEnabled()
    finally:
        for gate in backend.release:
            gate.set()
        wait_for_ui(app, page.can_close)
        page.close()