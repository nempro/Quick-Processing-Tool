from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog

import quick_processing_tool.color_picker as color_picker_module
import quick_processing_tool.edit_ui as edit_ui_module
from quick_processing_tool.color_picker import (
    ColorPickerDialog,
    choose_color,
    install_japanese_qt_translations,
)
from quick_processing_tool.edit_ui import QuickEditPage


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dialog_has_explicit_japanese_equal_actions_and_alpha_options(
    app: QApplication,
) -> None:
    initial = QColor(11, 22, 33, 44)
    dialog = ColorPickerDialog(initial, title="描画色を選ぶ", show_alpha=True)
    try:
        dialog.show()
        app.processEvents()
        assert dialog.windowTitle() == "描画色を選ぶ"
        assert dialog.cancel_button.text() == "キャンセル"
        assert dialog.apply_button.text() == "この色に変更"
        assert abs(dialog.cancel_button.width() - dialog.apply_button.width()) <= 1
        assert dialog.cancel_button.height() == dialog.apply_button.height() >= 36
        assert dialog.cancel_button.sizePolicy().horizontalPolicy().name == "Expanding"
        assert dialog.apply_button.sizePolicy().horizontalPolicy().name == "Expanding"
        assert dialog.apply_button.isDefault() and dialog.apply_button.autoDefault()
        assert "#315fbd" in dialog.apply_button.styleSheet()
        assert dialog.apply_button.accessibleName() == "選択した色に変更"
        assert dialog.cancel_button.accessibleName() == "色の変更をキャンセル"
        assert dialog.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        assert dialog.testOption(QColorDialog.ColorDialogOption.NoButtons)
        assert dialog.testOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
        assert dialog.currentColor().getRgb() == initial.getRgb()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_rgb_dialog_hides_alpha_and_helper_enforces_opaque(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = ColorPickerDialog(QColor(1, 2, 3, 4), show_alpha=False)
    try:
        assert not dialog.testOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()

    def accepted(self) -> int:
        self.setCurrentColor(QColor(21, 31, 41, 51))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ColorPickerDialog, "exec", accepted)
    rgb = choose_color(QColor(1, 2, 3, 4), None, show_alpha=False)
    rgba = choose_color(QColor(1, 2, 3, 4), None, show_alpha=True)
    assert rgb.getRgb() == (21, 31, 41, 255)
    assert rgba.getRgb() == (21, 31, 41, 51)


def test_choose_color_reject_returns_invalid(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ColorPickerDialog,
        "exec",
        lambda self: QDialog.DialogCode.Rejected,
    )
    assert not choose_color(QColor("red"), None).isValid()


def test_enter_accepts_and_escape_rejects(app: QApplication) -> None:
    accepted = ColorPickerDialog(QColor("red"))
    rejected = ColorPickerDialog(QColor("blue"))
    try:
        accepted.show()
        app.processEvents()
        QTest.keyClick(accepted, Qt.Key.Key_Return)
        assert accepted.result() == QDialog.DialogCode.Accepted

        rejected.show()
        app.processEvents()
        QTest.keyClick(rejected, Qt.Key.Key_Escape)
        assert rejected.result() == QDialog.DialogCode.Rejected
    finally:
        accepted.close()
        rejected.close()
        accepted.deleteLater()
        rejected.deleteLater()
        app.processEvents()


def test_translation_is_retained_and_fallback_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FakeApp:
        def __init__(self) -> None:
            self.installed = []

        def installTranslator(self, translator) -> bool:
            self.installed.append(translator)
            return True

    class LoadedTranslator:
        def __init__(self, parent) -> None:
            self.parent = parent

        def load(self, name, path) -> bool:
            return name == "qtbase_ja" and path == "translations"

    app = FakeApp()
    monkeypatch.setattr(color_picker_module, "QTranslator", LoadedTranslator)
    monkeypatch.setattr(color_picker_module.QLibraryInfo, "path", lambda *args: "translations")
    assert install_japanese_qt_translations(app)
    assert install_japanese_qt_translations(app)
    assert len(app.installed) == 1
    assert getattr(app, color_picker_module._TRANSLATOR_ATTRIBUTE) == app.installed

    class MissingTranslator(LoadedTranslator):
        def load(self, name, path) -> bool:
            return False

    missing_app = FakeApp()
    monkeypatch.setattr(color_picker_module, "QTranslator", MissingTranslator)
    with caplog.at_level(logging.WARNING):
        assert not install_japanese_qt_translations(missing_app)
    assert "Qt日本語翻訳を読み込めません" in caplog.text
    assert missing_app.installed == []


def test_edit_cancel_is_non_mutating_and_rgba_accept_updates_settings(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = QuickEditPage()
    try:
        initial = page.settings()
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor())
        page.choose_text_color()
        assert page.settings() == initial

        calls = []

        def accepted(initial, parent, title, *, show_alpha):
            calls.append((title, show_alpha))
            return QColor(10, 20, 30, 64)

        monkeypatch.setattr(edit_ui_module, "choose_color", accepted)
        page.choose_text_color()
        assert page.settings().text.color == (10, 20, 30, 64)
        assert page.text_color_button.text() == "#400A141E"
        assert calls == [("文字色を選ぶ", True)]
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_palette_accept_updates_rgb_and_cancel_keeps_value(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = QuickEditPage()
    page._palette_values = ((1, 2, 3), (4, 5, 6))
    page._palette_replacements = ((1, 2, 3), (4, 5, 6))
    page._selected_palette_index = 0
    try:
        monkeypatch.setattr(edit_ui_module, "choose_color", lambda *a, **k: QColor())
        page._replace_palette_color(1)
        assert page._palette_replacements == ((1, 2, 3), (4, 5, 6))
        assert page._selected_palette_index == 0

        calls = []

        def accepted(initial, parent, title, *, show_alpha):
            calls.append(show_alpha)
            return QColor(101, 102, 103, 7)

        monkeypatch.setattr(edit_ui_module, "choose_color", accepted)
        page._replace_palette_color(1)
        assert page._palette_replacements == ((1, 2, 3), (101, 102, 103))
        assert page._selected_palette_index == 1
        assert calls == [False]
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
