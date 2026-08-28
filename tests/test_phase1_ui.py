import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QSplitter, QToolButton, QWidget

from quick_processing_tool.models import OutputFormat, ResizeMode, Transform
from quick_processing_tool.ui import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(app: QApplication) -> MainWindow:
    result = MainWindow()
    yield result
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and (
        result._quick_preview_thread is not None
        or result.edit_page._preview_thread is not None
        or result.pixel_page._import_thread is not None
    ):
        app.processEvents()
    assert result._quick_preview_thread is None
    assert result.edit_page._preview_thread is None
    assert result.pixel_page._import_thread is None
    result.close()
    app.processEvents()


def make_image(path: Path, size: tuple[int, int] = (64, 48), color: str = "blue") -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def image_mime(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def send_drop(target, mime: QMimeData) -> tuple[QDragEnterEvent, QDropEvent]:
    enter = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, enter)
    drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, drop)
    return enter, drop


def test_empty_state_explains_all_three_entry_requirements(window: MainWindow) -> None:
    assert window.drop_zone.acceptDrops()
    assert window.drop_zone.drop_title.text() == "画像をここにドロップ"
    assert "PNG / JPG / WebP" in window.drop_zone.drop_subtitle.text()
    assert "複数枚まとめて追加できます" in window.drop_zone.drop_subtitle.text()
    assert window.drop_zone.choose_button.text() == "画像を選ぶ"
    assert not window.drop_zone.overlay.isHidden()
    assert window.drop_zone._stack.currentWidget() is window.drop_zone.overlay
    assert not window.export_action.isEnabled()
    assert not window.copy_action.isEnabled()


def test_three_column_layout_keeps_loaded_images_panel_visible(
    window: MainWindow, app: QApplication
) -> None:
    window.resize(1180, 760)
    window.show()
    app.processEvents()

    loaded_panel = window.findChild(QWidget, "loaded_images_panel")
    assert loaded_panel is not None
    assert loaded_panel.width() >= 240
    assert window.drop_zone.width() >= 400

def test_choose_button_uses_file_picker_and_loads_image(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_image(tmp_path / "picked.png")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: ([str(source)], ""),
    )

    window.drop_zone.choose_button.click()

    assert [info.path for info in window.files] == [source]
    assert window.file_tree.topLevelItemCount() == 1
    assert window.files_heading.text() == "読み込んだ画像　1枚"


def test_central_drop_appends_and_preserves_preview_selection(
    window: MainWindow, tmp_path: Path
) -> None:
    first = make_image(tmp_path / "first.png", color="red")
    second = make_image(tmp_path / "second.webp", color="green")
    window.load_paths([first])
    assert window.current_index == 0

    enter, drop = send_drop(window.drop_zone.preview.viewport(), image_mime(second))

    assert enter.isAccepted()
    assert drop.isAccepted()
    assert [info.path for info in window.files] == [first, second]
    assert window.current_index == 0
    assert window.file_tree.topLevelItemCount() == 2
    assert window.files_heading.text() == "読み込んだ画像　2枚"
    assert window.export_action.text() == "2枚をまとめて保存"


def test_drag_feedback_is_immediate_and_clears_after_drop(
    window: MainWindow, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "drag.png")
    mime = image_mime(source)
    enter = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(window.drop_zone.overlay, enter)
    assert enter.isAccepted()
    assert window.drop_zone.drop_title.text() == "ここにドロップして画像を読み込み"
    assert window.drop_zone.choose_button.isHidden()

    drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.drop_zone.overlay, drop)
    assert drop.isAccepted()
    assert window.drop_zone.drop_title.text() == "画像をここにドロップ"
    assert window.drop_zone.overlay.isHidden()
    assert window.drop_zone._stack.currentWidget() is window.drop_zone.preview


def test_loaded_state_is_japanese_and_shows_output_forecast(
    window: MainWindow, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "info.png", size=(320, 180))
    window.load_paths([source])

    text = window.info_label.text()
    assert "元画像" in text
    assert "info.png" in text
    assert "320 × 180 / PNG" in text
    assert "保存後（見込み）" in text
    assert window.info_label.isHidden() is False
    assert window.export_action.isEnabled()
    assert window.copy_action.isEnabled()
    assert window.open_action.text() == "画像を開く"
    assert window.copy_action.text() == "クリップボードにコピー"


def test_output_forecast_updates_for_resize_rotate_format_and_target(
    window: MainWindow, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "forecast.png", size=(320, 180))
    window.load_paths([source])

    window.resize_mode.setCurrentIndex(window.resize_mode.findData(ResizeMode.LONG_EDGE))
    window.long_edge_spin.setValue(100)
    assert "100 × 56" in window.info_label.text()
    assert "容量は保存時に確定" in window.info_label.text()

    window.add_transform(Transform.ROTATE_RIGHT)
    assert "56 × 100" in window.info_label.text()

    window.format_combo.setCurrentIndex(
        window.format_combo.findData(OutputFormat.JPEG)
    )
    assert "/ JPEG /" in window.info_label.text()

    window.target_combo.setCurrentIndex(2)
    assert "1 MB以下" in window.info_label.text()


def test_invalid_drag_clears_highlight(
    window: MainWindow, tmp_path: Path
) -> None:
    valid = make_image(tmp_path / "valid.png")
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("not an image", encoding="utf-8")

    valid_mime = image_mime(valid)
    active = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        valid_mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.drop_zone.overlay, active)
    assert window.drop_zone.drop_title.text() == "ここにドロップして画像を読み込み"

    invalid_mime = image_mime(invalid)
    rejected = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        invalid_mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.drop_zone.overlay, rejected)
    assert not rejected.isAccepted()
    assert window.drop_zone.drop_title.text() == "画像をここにドロップ"

def test_quick_inputs_define_distinct_interaction_states(
    window: MainWindow,
) -> None:
    workspace = window.findChild(QSplitter, "quick_workspace")
    assert workspace is not None
    style = workspace.styleSheet()

    for selector in (
        "QComboBox, QSpinBox, QLineEdit",
        "QComboBox:hover",
        "QSpinBox:hover",
        "QComboBox:focus",
        "QSpinBox:focus",
        "QComboBox:disabled",
        "QSpinBox:disabled",
    ):
        assert selector in style
    assert "background-color: #dce5ef" in style
    assert "border: 2px solid #2457b2" in style
    assert "background-color: #f3f4f6" in style

def test_settings_are_purpose_first_and_future_tabs_are_disabled(
    window: MainWindow,
) -> None:
    labels = [
        button.text()
        for button in window.navigation.widget(0).findChildren(QToolButton)
        if button.isCheckable() and button.text()
    ]
    assert labels == [
        "容量を小さくする",
        "画像サイズを変更する",
        "画像形式を変える",
        "回転・反転する",
        "保存先とプライバシー",
    ]
    for object_name in (
        "capacity_settings",
        "resize_settings",
        "format_settings",
        "transform_settings",
        "destination_settings",
    ):
        assert window.findChild(object, object_name).isHidden()

    assert window.navigation.tabText(0) == "かんたん変換"
    assert window.navigation.tabText(1) == "文字サムネ"
    assert window.navigation.tabText(2) == "擬音素材"
    assert window.navigation.isTabEnabled(2)
    assert window.navigation.tabText(3) == "画像加工"
    assert window.navigation.isTabEnabled(3)
    assert window.navigation.tabText(4) == "高画質化"
    assert window.navigation.isTabEnabled(4)
    assert not window.navigation.isTabEnabled(5)
    assert window.navigation.tabToolTip(5) == "今後追加予定"


def test_navigation_tabs_have_uniform_larger_click_targets(
    window: MainWindow,
) -> None:
    window.resize(1180, 760)
    window.show()
    QApplication.processEvents()

    tab_bar = window.navigation.tabBar()
    heights = [
        tab_bar.tabRect(index).height()
        for index in range(tab_bar.count())
    ]
    assert len(set(heights)) == 1
    assert 39 <= heights[0] <= 43
    assert not tab_bar.expanding()
    assert window.navigation.currentWidget().height() >= 650

    style = window.navigation.styleSheet()
    for selector in (
        "QTabBar::tab {",
        "QTabBar::tab:hover:!selected:!disabled",
        "QTabBar::tab:selected",
        "QTabBar::tab:disabled",
    ):
        assert selector in style
    assert "padding: 7px 16px" in style
    assert "font-size: 14px" in style
    assert "border-bottom: 3px solid #315fbd" in style


def test_duplicate_drop_does_not_duplicate_the_loaded_list(
    window: MainWindow, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "same.png")
    window.load_paths([source])
    window.load_paths([source])

    assert len(window.files) == 1
    assert window.file_tree.topLevelItemCount() == 1
