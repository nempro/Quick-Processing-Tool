from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageChops
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QScrollArea

from quick_processing_tool.edit_ui import FontPickerButton, FontPickerDialog, QuickEditPage
from quick_processing_tool.editing.models import EditSettings, TextSettings
from quick_processing_tool.editing.renderer import load_normalized, render_path, render_path_preview
from quick_processing_tool.font_catalog import FontCatalog
from quick_processing_tool.thumbnail_ui import ThumbnailPage
from quick_processing_tool.ui import MainWindow
from quick_processing_tool.upscale_ui import UpscalePage


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_font_catalog_enumerates_preferred_installed_families(qt_app: QApplication) -> None:
    catalog = FontCatalog()
    assert catalog.families() == tuple(sorted(catalog.families(), key=str.casefold))
    assert set(catalog.preferred_families()).issubset(set(catalog.families()))
    assert catalog.default_family()


def test_font_catalog_registration_accepts_ttf_otf_and_rejects_invalid(
    qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = FontCatalog()
    valid = tmp_path / "external.ttf"
    valid.write_bytes(b"not-a-real-font")
    calls: list[str] = []
    monkeypatch.setattr(QFontDatabase, "addApplicationFont", lambda path: calls.append(path) or 17)
    monkeypatch.setattr(QFontDatabase, "applicationFontFamilies", lambda _font_id: ["Mock JP"])
    registered = catalog.register_file(valid)
    duplicate = catalog.register_file(valid)
    assert registered.succeeded and registered.families == ("Mock JP",)
    assert duplicate.succeeded and duplicate.families == ("Mock JP",)
    assert len(calls) == 1
    assert not catalog.register_file(tmp_path / "external.ttf.txt").succeeded
    assert not catalog.register_file(tmp_path / "missing.otf").succeeded


def test_font_picker_is_noneditable_and_search_filters_both_sections(qt_app: QApplication) -> None:
    catalog = FontCatalog()
    catalog.families = lambda: ("Arial", "Meiryo", "Noto Sans JP")  # type: ignore[method-assign]
    catalog.preferred_families = lambda: ("Meiryo",)  # type: ignore[method-assign]
    button = FontPickerButton(catalog)
    assert not hasattr(button, "lineEdit")
    assert button.family() == "Meiryo"
    dialog = FontPickerDialog(catalog, "Meiryo")
    dialog.search_edit.setText("noto")
    qt_app.processEvents()
    assert dialog.preferred_list.item(0).isHidden()
    assert not dialog.all_list.item(2).isHidden()
    dialog.all_list.setCurrentRow(2)
    dialog._item_selected(dialog.all_list.currentItem())
    assert dialog.selected_family == "Noto Sans JP"
    dialog.close()
    button.close()


def test_font_picker_accept_uses_last_explicit_list_selection(qt_app: QApplication) -> None:
    catalog = FontCatalog()
    catalog.families = lambda: ("Arial", "Meiryo", "Noto Sans JP")  # type: ignore[method-assign]
    catalog.preferred_families = lambda: ("Meiryo",)  # type: ignore[method-assign]

    picker = FontPickerDialog(catalog, "Meiryo")
    full_item = picker.all_list.item(2)
    picker.all_list.setCurrentItem(full_item)
    picker._item_selected(full_item)
    picker.accept()
    assert picker.selected_family == "Noto Sans JP"

    reverse = FontPickerDialog(catalog, "Noto Sans JP")
    preferred_item = reverse.preferred_list.item(0)
    reverse.preferred_list.setCurrentItem(preferred_item)
    reverse._item_selected(preferred_item)
    reverse.accept()
    assert reverse.selected_family == "Meiryo"

    filtered = FontPickerDialog(catalog, "Meiryo")
    filtered.search_edit.setText("noto")
    filtered.all_list.setCurrentRow(2)
    filtered._item_selected(filtered.all_list.currentItem())
    filtered.accept()
    assert filtered.selected_family == "Noto Sans JP"


def test_quick_edit_font_settings_reset_new_image_and_preview_export_parity(
    qt_app: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (160, 120), (40, 80, 120, 255)).save(first)
    Image.new("RGBA", (80, 60), (120, 40, 80, 255)).save(second)
    page = QuickEditPage()
    page.load_image(first)
    selected = page.font_catalog.default_family()
    page.font_combo.set_family(selected)
    page.text_enabled.setChecked(True)
    page.text_edit.setPlainText("日本語\n二行")
    settings = page.settings()
    assert settings.text.font_family == selected
    preview = render_path_preview(first, settings, max_dimension=1000)
    exported_render = render_path(first, settings)
    assert ImageChops.difference(preview, exported_render).getbbox() is None
    page.reset_edits()
    reset = page.settings()
    assert reset.filter_preset is EditSettings().filter_preset
    assert reset.text.font_family == page.font_catalog.default_family()
    assert not reset.text.enabled
    page.load_image(second)
    assert page.source_path == second.resolve()
    new_settings = page.settings()
    assert not new_settings.text.enabled
    assert new_settings.text.font_family == page.font_catalog.default_family()
    assert new_settings.text.text == TextSettings().text
    page.close()


@pytest.mark.parametrize("size", [(900, 620), (1180, 760), (1440, 900)])
def test_all_page_left_panes_have_no_horizontal_overflow(
    qt_app: QApplication, size: tuple[int, int]
) -> None:
    window = MainWindow()
    window.resize(*size)
    window.show()
    qt_app.processEvents()
    pages = [window.navigation.widget(0), window.thumbnail_page, window.edit_page, window.upscale_page]
    for page in pages:
        page.resize(*size)
        page.show()
        qt_app.processEvents()
        for scroll in page.findChildren(QScrollArea):
            assert scroll.horizontalScrollBar().maximum() == 0
            assert scroll.widget() is not None
            content = scroll.widget()
            assert content.width() <= scroll.viewport().width()
            for child in content.findChildren(type(content)):
                if not child.isVisible():
                    continue
                position = child.mapTo(content, QPoint(0, 0))
                assert position.x() >= 0
                assert position.x() + child.width() <= content.width()
    assert window.upscale_page.scale_2.geometry().right() <= window.upscale_page.scale_2.parentWidget().width()
    assert window.upscale_page.scale_4.geometry().right() <= window.upscale_page.scale_4.parentWidget().width()
    window.close()
