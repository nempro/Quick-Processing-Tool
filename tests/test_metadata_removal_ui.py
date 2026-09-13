from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from quick_processing_tool.metadata_removal_ui import MetadataRemovalDialog
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_header_button_opens_metadata_removal_dialog(app: QApplication) -> None:
    window = MainWindow()
    try:
        assert window.metadata_removal_button.text() == "メタ情報削除"
        window.open_metadata_removal_dialog()
        dialog = window._metadata_removal_dialog
        assert dialog is not None
        assert dialog.windowTitle() == "メタ情報を一括削除"
        assert dialog.processed_radio.isChecked()
        assert dialog.minimumWidth() <= 680
        dialog.close()
        app.processEvents()
    finally:
        window.close()


def test_folder_drop_adds_only_direct_supported_images(app: QApplication, tmp_path: Path) -> None:
    Image.new("RGB", (2, 2), "red").save(tmp_path / "direct.png")
    nested = tmp_path / "nested"
    nested.mkdir()
    Image.new("RGB", (2, 2), "blue").save(nested / "nested.png")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    dialog = MetadataRemovalDialog()
    try:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        drop = QDropEvent(
            QPointF(8, 8),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        dialog.drop_area.dropEvent(drop)
        assert [path.name for path in dialog.paths] == ["direct.png"]
    finally:
        dialog.close()


def test_dialog_queue_removal_clear_and_batch_result(app: QApplication, tmp_path: Path) -> None:
    images = []
    for index in range(3):
        path = tmp_path / f"image_{index}.png"
        Image.new("RGBA", (8, 6), (index, 10, 20, 30)).save(path)
        images.append(path)
    dialog = MetadataRemovalDialog()
    try:
        dialog.add_paths(images)
        assert dialog.file_tree.topLevelItemCount() == 3
        dialog.file_tree.topLevelItem(1).setSelected(True)
        dialog.remove_selected()
        assert [path.name for path in dialog.paths] == ["image_0.png", "image_2.png"]
        dialog.clear_queue()
        assert not dialog.paths

        dialog.add_paths(images)
        dialog.start_processing()
        deadline = time.monotonic() + 5.0
        while dialog._thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert dialog._thread is None
        assert dialog.result_label.text() == "処理完了: 3 / 3 成功"
        assert dialog.open_folder_button.isEnabled()
        assert all((tmp_path / "Processed" / image.name).is_file() for image in images)
    finally:
        dialog.close()