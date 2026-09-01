from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from quick_processing_tool.image_splitting import ImageSplitOptions, SplitDirection
from quick_processing_tool.models import OutputFormat, ProcessingOptions
from quick_processing_tool.pipeline import read_image_info
from quick_processing_tool.ui import MainWindow, ProcessingWorker


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_source(path: Path, color: str = "navy") -> Path:
    Image.new("RGB", (40, 24), color).save(path)
    return path


def test_worker_reports_only_folders_with_successful_outputs(tmp_path: Path) -> None:
    valid = make_source(tmp_path / "valid.png")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    output = tmp_path / "output"
    folders: list[Path] = []
    counts: list[int] = []
    summaries: list[tuple[int, int]] = []
    worker = ProcessingWorker(
        [valid, broken],
        ProcessingOptions(output_format=OutputFormat.PNG),
        False,
        "Custom folder",
        output,
        False,
    )
    worker.folder_saved.connect(lambda folder: folders.append(Path(folder)))
    worker.outputs_saved.connect(counts.append)
    worker.finished.connect(lambda succeeded, failed: summaries.append((succeeded, failed)))

    worker.run()

    assert folders == [output]
    assert counts == [1]
    assert summaries == [(1, 1)]


def test_destination_section_always_shows_current_folder(
    app: QApplication,
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source.png")
    custom = tmp_path / "a-very-long-folder-name" / "nested-output-folder"
    window = MainWindow()
    try:
        window.files = [read_image_info(source)]
        window._update_current_destination_display()
        expected_default = tmp_path / "Processed"
        assert window.current_destination_label._value == str(expected_default)
        assert window.current_destination_label.toolTip() == str(expected_default)
        assert "保存先:" in window.destination_section.description.text()

        window.custom_folder = custom
        window.destination_combo.setCurrentIndex(
            window.destination_combo.findData("Custom folder")
        )
        assert window.current_destination_label._value == str(custom)
        assert window.current_destination_label.toolTip() == str(custom)
        assert window.destination_section.description.toolTip() == str(custom)
    finally:
        window.close()


def test_save_result_is_shared_by_split_normal_and_batch(
    app: QApplication,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    window = MainWindow()
    try:
        window._saved_output_folders = {output}
        window._saved_output_count = 4
        window._active_split_options = ImageSplitOptions(
            True,
            SplitDirection.VERTICAL,
            4,
        )
        window._on_finished(1, 0)
        assert not window.quick_save_result_box.isHidden()
        assert window.quick_save_result_summary.text() == "4枚の画像を保存しました"
        assert window.quick_saved_folder_label._value == str(output)
        assert window.quick_saved_folder_label.toolTip() == str(output)
        assert window.quick_open_folder_button.isEnabled()

        window._saved_output_count = 2
        window._active_split_options = ImageSplitOptions()
        window._on_finished(2, 0)
        assert window.quick_save_result_summary.text() == "2枚の画像を保存しました"
        assert not window.quick_save_result_box.isHidden()
    finally:
        window.close()


def test_total_failure_never_displays_open_folder_as_success(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    window = MainWindow()
    try:
        window._clear_quick_save_result()
        window._saved_output_count = 0
        window._active_split_options = ImageSplitOptions(
            True,
            SplitDirection.VERTICAL,
            4,
        )
        window._on_finished(0, 1)
        assert window.quick_save_result_box.isHidden()
        assert not window.quick_open_folder_button.isEnabled()
    finally:
        window.close()


def test_open_saved_folder_uses_local_folder_url(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    opened: list[Path] = []

    def capture(url) -> bool:
        opened.append(Path(url.toLocalFile()))
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", capture)
    window = MainWindow()
    try:
        window._saved_output_folders = {output}
        window.open_quick_saved_folders()
        assert opened == [output]
    finally:
        window.close()


def test_single_split_hint_uses_natural_japanese(
    app: QApplication,
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source.png")
    second = make_source(tmp_path / "second.png", "green")
    window = MainWindow()
    try:
        window.files = [read_image_info(source)]
        window.split_enable_check.setChecked(True)
        window._update_quick_actions()
        assert window.quick_save_hint.text() == "4枚の画像として保存します"

        window.files.append(read_image_info(second))
        window._update_quick_actions()
        assert window.quick_save_hint.text() == (
            "2枚をそれぞれ4分割し、合計8枚を保存します"
        )
    finally:
        window.close()


def test_save_result_stays_compact_at_small_window(
    app: QApplication,
    tmp_path: Path,
) -> None:
    output = tmp_path / "a-very-long-folder-name" / "nested-output-folder"
    output.mkdir(parents=True)
    window = MainWindow()
    try:
        window.setMinimumSize(0, 0)
        window.resize(900, 620)
        window.show()
        window._saved_output_folders = {output}
        window._saved_output_count = 4
        window._show_quick_save_result(0)
        app.processEvents()

        assert window.quick_settings_scroll.horizontalScrollBar().maximum() == 0
        assert window.quick_save_result_box.width() <= window.quick_settings_footer.width()
        assert window.quick_saved_folder_label.toolTip() == str(output)
        assert window.quick_open_folder_button.isVisibleTo(window)
    finally:
        window.close()
