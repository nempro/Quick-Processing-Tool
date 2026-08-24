import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from quick_processing_tool.models import OutputFormat, ProcessingOptions
from quick_processing_tool.naming import unique_output_path
from quick_processing_tool.pipeline import read_image_info
from quick_processing_tool.ui import MainWindow, ProcessingWorker


def test_duplicate_safe_naming_never_overwrites_source(tmp_path: Path) -> None:
    source = tmp_path / "image.jpg"
    source.write_bytes(b"source")
    assert unique_output_path(tmp_path, source, "JPEG") == tmp_path / "image_2.jpg"
    (tmp_path / "image_2.jpg").write_bytes(b"existing")
    assert unique_output_path(tmp_path, source, "JPEG") == tmp_path / "image_3.jpg"


def test_batch_continues_after_partial_failure(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    broken = tmp_path / "broken.png"
    last = tmp_path / "last.png"
    Image.new("RGB", (20, 10), "red").save(first)
    broken.write_bytes(b"not an image")
    Image.new("RGB", (20, 10), "green").save(last)
    destination = tmp_path / "out"

    statuses: list[tuple[int, str]] = []
    summary: list[tuple[int, int]] = []
    worker = ProcessingWorker(
        [first, broken, last],
        ProcessingOptions(output_format=OutputFormat.JPEG),
        False,
        "Custom folder",
        destination,
        False,
    )
    worker.file_status.connect(lambda index, status, detail: statuses.append((index, status)))
    worker.finished.connect(lambda succeeded, failed: summary.append((succeeded, failed)))
    worker.run()

    assert summary == [(2, 1)]
    assert (1, "Error") in statuses
    assert (2, "Done") in statuses
    assert len(list(destination.glob("*.jpg"))) == 2

def test_window_can_start_a_second_worker_after_thread_teardown(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 10), "blue").save(source)
    output = tmp_path / "out"

    window = MainWindow()
    window.files = [read_image_info(source)]
    window.custom_folder = output
    window.destination_combo.setCurrentText("Custom folder")

    for _ in range(2):
        window._start_worker([source], copy_mode=False, row_indices=[0])
        thread = window._thread
        assert thread is not None
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(5_000, loop.quit)
        loop.exec()
        app.processEvents()
        assert window._thread is None

    assert len(list(output.glob("*.png"))) == 2
    window.close()
