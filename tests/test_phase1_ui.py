import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, QThread, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QScrollArea,
    QSplitter,
    QToolButton,
    QWidget,
)

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


def test_quick_processing_locks_edit_and_pixel_tabs(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_image(tmp_path / "source.png")
    monkeypatch.setattr(QThread, "start", lambda _thread: None)
    window._start_worker([source], copy_mode=False, row_indices=[0])
    assert not window.navigation.isTabEnabled(window.image_edit_tab)
    assert not window.navigation.isTabEnabled(window.pixel_tab)
    window._clear_worker_refs()
    assert window.navigation.isTabEnabled(window.image_edit_tab)
    assert window.navigation.isTabEnabled(window.pixel_tab)


def test_custom_output_folder_keeps_full_path_in_tooltip(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path.joinpath(*(["very-long-folder-name"] * 5))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(folder))
    window.choose_folder()
    assert window.folder_button.toolTip() == str(folder)


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


def test_clipboard_receives_decoded_image(
    window: MainWindow, app: QApplication, tmp_path: Path
) -> None:
    source = make_image(tmp_path / "クリップボード.png", size=(37, 23))
    window._set_clipboard(source.read_bytes())
    app.processEvents()
    copied = QApplication.clipboard().image()
    assert not copied.isNull()
    assert (copied.width(), copied.height()) == (37, 23)
    assert window.statusBar().currentMessage() == "画像をクリップボードにコピーしました"


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

def test_settings_are_purpose_first_and_tabs_follow_user_workflow(
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

    assert [
        window.navigation.tabText(index)
        for index in range(window.navigation.count())
    ] == [
        "かんたん変換",
        "画像加工",
        "擬音素材",
        "吹き出し素材",
        "高画質化",
        "ドット絵",
        "文字サムネ",
    ]
    assert all(
        window.navigation.isTabEnabled(index)
        for index in range(window.navigation.count())
    )
    assert (
        window.quick_tab,
        window.image_edit_tab,
        window.sound_effect_tab,
        window.speech_bubble_tab,
        window.upscale_tab,
        window.pixel_tab,
        window.thumbnail_tab,
    ) == tuple(range(7))


def test_metadata_removal_is_discoverable_while_privacy_section_is_closed(
    window: MainWindow,
) -> None:
    summary = window.findChild(QLabel, "metadata_privacy_summary")
    assert summary is not None
    assert summary.text() == "メタ情報（EXIFなど）を保存時に削除できます"
    assert not summary.isHidden()
    assert window.metadata_check.text() == "メタ情報を削除"
    assert window.metadata_check.toolTip() == "EXIFなどの画像情報を保存時に削除します"
    assert window.metadata_check.isChecked()


def test_sticky_save_button_uses_the_same_batch_export_path(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.quick_save_button.text() == "現在の設定で保存"
    assert not window.quick_save_button.isEnabled()
    assert window.quick_save_hint.text() == "画像を開くと保存できます"

    first = make_image(tmp_path / "first-save.png")
    second = make_image(tmp_path / "second-save.png")
    window.load_paths([first, second])
    assert window.quick_save_button.isEnabled()
    assert window.quick_save_hint.text() == "すべての設定をまとめて適用します"

    calls: list[tuple[list[Path], bool, list[int]]] = []
    monkeypatch.setattr(
        window,
        "_start_worker",
        lambda paths, copy_mode, row_indices: calls.append(
            (list(paths), copy_mode, list(row_indices))
        ),
    )
    window.quick_save_button.click()
    window.export_action.trigger()

    expected = ([first, second], False, [0, 1])
    assert calls == [expected, expected]


def test_sticky_save_collects_all_current_settings(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_image(tmp_path / "combined.png")
    window.load_paths([source])
    window.resize_mode.setCurrentIndex(
        window.resize_mode.findData(ResizeMode.PERCENTAGE)
    )
    window.percent_spin.setValue(50)
    window.format_combo.setCurrentIndex(window.format_combo.findData(OutputFormat.PNG))
    window.target_combo.setCurrentIndex(1)
    window.metadata_check.setChecked(False)
    window.timestamp_check.setChecked(False)
    window.add_transform(Transform.ROTATE_RIGHT)

    captured: dict[str, object] = {}

    def capture(paths, copy_mode, row_indices) -> None:
        captured.update(
            paths=list(paths),
            copy_mode=copy_mode,
            row_indices=list(row_indices),
            options=window.options(),
            destination=window.destination_combo.currentData(),
            processed=window.processed_check.isChecked(),
        )

    monkeypatch.setattr(window, "_start_worker", capture)
    window.quick_save_button.click()

    options = captured["options"]
    assert captured["paths"] == [source]
    assert captured["copy_mode"] is False
    assert captured["row_indices"] == [0]
    assert options.resize_mode is ResizeMode.PERCENTAGE
    assert options.percentage == 50
    assert options.output_format is OutputFormat.PNG
    assert options.target_bytes == 500 * 1024
    assert options.transforms == [Transform.ROTATE_RIGHT]
    assert options.remove_metadata is False
    assert options.preserve_timestamp is False
    assert captured["destination"] == "Same folder"
    assert captured["processed"] is True


def test_sticky_save_disables_while_processing(
    window: MainWindow,
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_image(tmp_path / "processing.png")
    window.load_paths([source])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and window._quick_preview_thread is not None:
        app.processEvents()
    assert window._quick_preview_thread is None
    assert window.quick_save_button.isEnabled()

    monkeypatch.setattr(QThread, "start", lambda _thread: None)
    window._start_worker([source], copy_mode=False, row_indices=[0])
    assert not window.quick_save_button.isEnabled()
    assert window.quick_save_hint.text() == "保存中です…"
    assert window.quick_save_button.toolTip() == "保存中です…"
    window.quick_save_button.click()
    assert window._worker is not None

    window._clear_worker_refs()
    assert window.quick_save_button.isEnabled()


def test_quick_footer_is_sticky_and_fits_at_720px(
    window: MainWindow, app: QApplication
) -> None:
    window.setMinimumSize(0, 0)
    window.resize(720, 720)
    window.show()
    app.processEvents()

    scroll = window.findChild(QScrollArea, "quick_settings_scroll")
    panel = window.findChild(QWidget, "quick_settings_panel")
    footer = window.quick_settings_footer
    assert scroll is not None
    assert panel is not None
    assert footer.isVisibleTo(panel)
    assert window.quick_save_button.isVisibleTo(panel)
    assert window.quick_clear_button.isVisibleTo(panel)
    assert footer.rect().contains(window.quick_save_button.geometry().topLeft())
    assert footer.rect().contains(window.quick_save_button.geometry().bottomRight())
    assert footer.rect().contains(window.quick_clear_button.geometry().topLeft())
    assert footer.rect().contains(window.quick_clear_button.geometry().bottomRight())
    assert scroll.horizontalScrollBar().maximum() == 0

    footer_position = footer.mapTo(panel, QPoint(0, 0))
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    assert footer.mapTo(panel, QPoint(0, 0)) == footer_position
    assert footer.geometry().bottom() <= panel.rect().bottom()


def test_quick_accordion_marks_expansion_and_scrolls_only_when_needed(
    window: MainWindow, app: QApplication
) -> None:
    window.setMinimumSize(0, 0)
    window.resize(720, 720)
    window.show()
    app.processEvents()
    scroll = window.quick_settings_scroll
    bar = scroll.verticalScrollBar()

    first = window.quick_sections[0]
    assert first.toggle.arrowType() is Qt.ArrowType.RightArrow
    assert first.toggle.property("expanded") is False
    first.toggle.click()
    app.processEvents()
    assert first.toggle.arrowType() is Qt.ArrowType.DownArrow
    assert first.toggle.property("expanded") is True
    assert bar.value() == 0
    assert 'QToolButton[expanded="true"]' in first.toggle.styleSheet()

    destination = window.quick_sections[-1]
    for section in window.quick_sections[1:-1]:
        section.toggle.setChecked(True)
    app.processEvents()
    bar.setValue(bar.maximum())
    destination.toggle.setChecked(True)
    app.processEvents()
    app.processEvents()

    content_top = destination.content.mapTo(scroll.viewport(), QPoint(0, 0)).y()
    visible_target = min(destination.content.height(), 64)
    assert content_top < scroll.viewport().height()
    assert content_top + visible_target <= scroll.viewport().height()


def test_quick_privacy_checkboxes_wrap_without_losing_full_labels(
    window: MainWindow, app: QApplication
) -> None:
    window.setMinimumSize(0, 0)
    window.resize(720, 720)
    window.quick_sections[-1].toggle.setChecked(True)
    window.show()
    app.processEvents()

    assert window.processed_check.text().replace("\n", "") == "処理済みサブフォルダーを使う"
    assert window.processed_check.accessibleName() == "処理済みサブフォルダーを使う"
    assert "\n" in window.processed_check.text()
    assert window.processed_check.sizeHint().height() >= (
        window.processed_check.fontMetrics().lineSpacing() * 2
    )
    assert window.timestamp_check.text().replace("\n", "") == "元画像の更新日時を引き継ぐ"
    assert window.quick_settings_scroll.horizontalScrollBar().maximum() == 0


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
