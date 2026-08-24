from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageChops
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from quick_processing_tool.thumbnail_models import (
    CANVAS_PRESETS,
    TextAlignment,
    ThumbnailFormat,
    ThumbnailSettings,
    TitleRecord,
    parse_title_records,
)
from quick_processing_tool.thumbnail_renderer import (
    ThumbnailLayoutError,
    encode_thumbnail,
    layout_title,
    render_record,
    render_thumbnail,
    sanitize_filename_component,
    thumbnail_output_path,
    write_thumbnail_output,
)
from quick_processing_tool.thumbnail_ui import ThumbnailPage, ThumbnailWorker


@pytest.fixture(scope="module", autouse=True)
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    fonts_folder = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    for filename in ("arial.ttf", "segoeui.ttf", "meiryo.ttc"):
        path = fonts_folder / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    return app


def settings(**changes) -> ThumbnailSettings:
    families = QFontDatabase.families()
    family = next(
        (name for name in ("Arial", "Segoe UI", "Meiryo") if name in families),
        QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family(),
    )
    base = ThumbnailSettings(font_family=family, margin=20)
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def test_title_input_trims_and_ignores_blank_lines() -> None:
    records = parse_title_records("  first  \n\n second\n   \nthird ")
    assert [(record.index, record.title) for record in records] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def test_japanese_titles_are_preserved_by_parser() -> None:
    records = parse_title_records(" 眠れない夜にずっと囁かれて \n今すぐ俺のところに来て")
    assert [record.title for record in records] == [
        "眠れない夜にずっと囁かれて",
        "今すぐ俺のところに来て",
    ]


def test_japanese_title_wraps_using_font_layout() -> None:
    result = layout_title(
        "夜中寝てたら年下彼氏に急に襲われて眠れない夜にずっと囁かれて",
        settings(width=420, height=420, margin=50, font_size=64, min_font_size=24, max_lines=6),
    )
    assert result.line_count > 1
    assert result.font_size >= 24
    assert result.total_height <= 320


def test_auto_fit_reduces_font_size() -> None:
    result = layout_title(
        "Wide title text " * 5,
        settings(width=500, height=300, margin=40, font_size=90, min_font_size=18, max_lines=4),
    )
    assert result.font_size < 90
    assert result.line_count <= 4


def test_auto_fit_keeps_requested_size_when_title_already_fits() -> None:
    result = layout_title(
        "短いタイトル",
        settings(width=800, height=450, font_size=64, min_font_size=20),
    )
    assert result.font_size == 64


def test_wrap_uses_glyph_metrics_not_character_count() -> None:
    options = settings(
        width=300,
        height=500,
        margin=20,
        font_size=40,
        min_font_size=40,
        max_lines=20,
    )
    wide = layout_title("W" * 30, options)
    narrow = layout_title("i" * 30, options)
    assert wide.line_count > narrow.line_count


def test_title_is_not_truncated_when_it_cannot_fit() -> None:
    with pytest.raises(ThumbnailLayoutError, match="収まりません"):
        layout_title(
            "長すぎるタイトル" * 40,
            settings(width=240, height=160, margin=40, font_size=48, min_font_size=48, max_lines=1),
        )


def test_preview_and_export_share_the_same_renderer() -> None:
    options = settings(width=360, height=240, output_format=ThumbnailFormat.PNG)
    preview = render_thumbnail("同じRendererで描画", options)
    data, exported = render_record(TitleRecord(1, "同じRendererで描画"), options)
    assert encode_thumbnail(preview.image, ThumbnailFormat.PNG, 90) == data
    assert preview.font_size == exported.font_size
    with Image.open(BytesIO(data)) as image:
        assert image.size == (360, 240)
        assert image.format == "PNG"


@pytest.mark.parametrize("output_format", [ThumbnailFormat.PNG, ThumbnailFormat.JPEG])
def test_png_and_jpeg_output(output_format: ThumbnailFormat) -> None:
    options = settings(width=320, height=180, output_format=output_format, quality=90)
    data, _ = render_record(TitleRecord(1, "日本語タイトル"), options)
    with Image.open(BytesIO(data)) as image:
        assert image.size == (320, 180)
        assert image.format == output_format.value


def test_alignment_changes_rendered_pixels() -> None:
    left = settings(width=400, height=220, alignment=TextAlignment.LEFT, output_format=ThumbnailFormat.PNG)
    right = settings(width=400, height=220, alignment=TextAlignment.RIGHT, output_format=ThumbnailFormat.PNG)
    left_data, _ = render_record(TitleRecord(1, "Alignment"), left)
    right_data, _ = render_record(TitleRecord(1, "Alignment"), right)
    assert left_data != right_data


def test_renderer_uses_selected_background_and_draws_text() -> None:
    options = settings(
        width=400,
        height=240,
        background_color="#123456",
        font_color="#f5df4d",
        output_format=ThumbnailFormat.PNG,
    )
    data, _ = render_record(TitleRecord(1, "文字色を確認"), options)
    with Image.open(BytesIO(data)).convert("RGB") as image:
        assert image.getpixel((0, 0)) == (18, 52, 86)
        assert ImageChops.difference(
            image, Image.new("RGB", image.size, (18, 52, 86))
        ).getbbox() is not None


def test_windows_filename_sanitization_and_collision(tmp_path: Path) -> None:
    assert sanitize_filename_component("CON") == "CON_"
    assert sanitize_filename_component("CON.txt") == "CON.txt_"
    safe = sanitize_filename_component('bad<>:"/\\|?* title. ')
    assert not any(character in safe for character in '<>:"/\\|?*')
    assert not safe.endswith((" ", "."))
    assert len(sanitize_filename_component("a" * 300)) == 80

    record = TitleRecord(1, 'bad<>:"/\\|?* title')
    first = thumbnail_output_path(tmp_path, record, 1, ThumbnailFormat.JPEG)
    first.write_bytes(b"existing")
    second = thumbnail_output_path(tmp_path, record, 1, ThumbnailFormat.JPEG)
    assert second != first
    assert second.name.endswith("_2.jpg")
    written = write_thumbnail_output(
        tmp_path, record, 1, ThumbnailFormat.JPEG, b"new image bytes"
    )
    assert written == second
    assert first.read_bytes() == b"existing"
    assert written.read_bytes() == b"new image bytes"


def test_fifty_title_batch_finishes_without_collisions(tmp_path: Path) -> None:
    records = [TitleRecord(index, f"過去作品タイトル {index}") for index in range(1, 51)]
    options = settings(width=320, height=180, font_size=32, min_font_size=18)
    progress: list[int] = []
    summary: list[tuple[int, int]] = []
    worker = ThumbnailWorker(records, options, tmp_path)
    worker.progress.connect(progress.append)
    worker.finished.connect(lambda succeeded, failed: summary.append((succeeded, failed)))
    worker.run()

    outputs = list(tmp_path.glob("*.jpg"))
    assert summary == [(50, 0)]
    assert progress[-1] == 100
    assert progress == sorted(progress)
    assert len(outputs) == 50
    assert len({path.name for path in outputs}) == 50


def test_thumbnail_batch_continues_after_layout_failure(tmp_path: Path) -> None:
    records = [
        TitleRecord(1, "Short"),
        TitleRecord(2, "長すぎるタイトル" * 40),
        TitleRecord(3, "Last"),
    ]
    options = settings(
        width=300,
        height=180,
        margin=30,
        font_size=40,
        min_font_size=40,
        max_lines=1,
    )
    statuses: list[tuple[int, str]] = []
    summary: list[tuple[int, int]] = []
    worker = ThumbnailWorker(records, options, tmp_path)
    worker.item_status.connect(lambda row, status, detail: statuses.append((row, status)))
    worker.finished.connect(lambda succeeded, failed: summary.append((succeeded, failed)))
    worker.run()

    assert summary == [(2, 1)]
    assert (1, "Error") in statuses
    assert (2, "Done") in statuses
    assert len(list(tmp_path.glob("*.jpg"))) == 2


def test_thumbnail_page_counts_titles_and_has_navigation() -> None:
    page = ThumbnailPage()
    page.titles_edit.setPlainText("最初のタイトル\n\n 次のタイトル \n最後のタイトル")
    page.update_preview()
    assert page.title_count.text() == "3枚生成予定"
    assert page.generate_button.text() == "3枚まとめて生成"
    assert len(page.records()) == 3
    assert page.preview_position.text() == "1 / 3"
    first_title = page.preview_title.text()
    first_pixmap = page.preview._item.pixmap().cacheKey()
    page.next_button.click()
    assert page.preview_position.text() == "2 / 3"
    assert page.preview_title.text() != first_title
    assert page.preview._item.pixmap().cacheKey() != first_pixmap
    page.close()


def test_thumbnail_page_empty_state_and_canvas_presets() -> None:
    page = ThumbnailPage()
    page.update_preview()
    assert page.title_count.text() == "0枚生成予定"
    assert not page.generate_button.isEnabled()
    assert "タイトルをここへ貼り付け" in page.titles_edit.placeholderText()
    assert page.preview_position.text() == "0 / 0"
    assert page.canvas_size_label.text() == "1200 × 1200 px"
    assert [(item.name, item.width, item.height) for item in CANVAS_PRESETS] == [
        ("1:1", 1200, 1200),
        ("16:9", 1920, 1080),
        ("4:3", 1200, 900),
        ("3:4", 900, 1200),
        ("9:16", 1080, 1920),
    ]
    page.canvas_preset_combo.setCurrentIndex(1)
    assert (page.width_spin.value(), page.height_spin.value()) == (1920, 1080)
    page.canvas_preset_combo.setCurrentIndex(page.canvas_preset_combo.count() - 1)
    assert page.width_spin.isEnabled()
    assert page.height_spin.isEnabled()
    page.close()


def test_thumbnail_layout_has_no_horizontal_scroll_and_balanced_columns() -> None:
    app = QApplication.instance()
    assert app is not None
    page = ThumbnailPage()
    page.resize(1180, 760)
    page.show()
    app.processEvents()

    assert page.settings_scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert not page.settings_scroll.horizontalScrollBar().isVisible()
    assert page.settings_content.width() <= page.settings_scroll.viewport().width()

    sizes = page.workspace_splitter.sizes()
    total = sum(sizes)
    ratios = [size / total for size in sizes]
    assert ratios[0] == pytest.approx(0.30, abs=0.02)
    assert ratios[1] == pytest.approx(0.43, abs=0.02)
    assert ratios[2] == pytest.approx(0.27, abs=0.02)

    page.resize(900, 700)
    app.processEvents()
    narrow_sizes = page.workspace_splitter.sizes()
    assert narrow_sizes[0] >= 280
    assert narrow_sizes[1] >= 360
    assert narrow_sizes[2] >= 250
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert page.settings_content.width() <= page.settings_scroll.viewport().width()

    page.resize(1440, 800)
    app.processEvents()
    wide_sizes = page.workspace_splitter.sizes()
    assert wide_sizes[1] > wide_sizes[0] > wide_sizes[2]
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert page.settings_content.width() <= page.settings_scroll.viewport().width()
    page.close()


def test_thumbnail_headings_are_role_based_without_step_numbers() -> None:
    page = ThumbnailPage()
    assert page.settings_heading.text() == "サムネイル設定"
    assert page.canvas_group.title() == "サイズ"
    assert page.appearance_group.title() == "見た目"
    assert page.export_group.title() == "保存"
    assert page.titles_heading.text() == "タイトル"
    visible_headings = [
        page.settings_heading.text(),
        page.canvas_group.title(),
        page.appearance_group.title(),
        page.export_group.title(),
        page.titles_heading.text(),
    ]
    assert not any(label[:2] in {"1.", "2.", "3.", "4."} for label in visible_headings)
    page.close()


def test_thumbnail_inputs_define_clear_interaction_states() -> None:
    page = ThumbnailPage()
    style = page.workspace_splitter.styleSheet()
    for selector in (
        "QComboBox:hover",
        "QComboBox:focus",
        "QComboBox:disabled",
        "QSpinBox:hover",
        "QSpinBox:focus",
        "QSpinBox:disabled",
        "QPlainTextEdit:hover",
        "QPlainTextEdit:focus",
        "QPlainTextEdit:disabled",
        "QPushButton:hover",
        "QPushButton:focus",
        "QPushButton:disabled",
        "QCheckBox:hover",
        "QCheckBox:focus",
        "QCheckBox:disabled",
    ):
        assert selector in style
    assert "background-color: #dce5ef" in style
    assert "border: 2px solid #2457b2" in style
    assert "background-color: #f3f4f6" in style
    toggle_style = page.details_section.toggle.styleSheet()
    assert "QToolButton:hover" in toggle_style
    assert "QToolButton:focus" in toggle_style
    assert "QToolButton:disabled" in toggle_style
    page.close()


def test_png_disables_jpeg_quality_and_jpeg_enables_it() -> None:
    page = ThumbnailPage()
    png_index = page.format_combo.findData(ThumbnailFormat.PNG.value)
    jpeg_index = page.format_combo.findData(ThumbnailFormat.JPEG.value)
    page.format_combo.setCurrentIndex(png_index)
    assert not page.quality_spin.isEnabled()
    page.format_combo.setCurrentIndex(jpeg_index)
    assert page.quality_spin.isEnabled()
    page.close()


def test_batch_locks_all_design_controls() -> None:
    page = ThumbnailPage()
    page._set_processing(True)
    assert not page.canvas_preset_combo.isEnabled()
    assert not page.background_button.isEnabled()
    assert not page.font_color_button.isEnabled()
    assert not page.titles_edit.isEnabled()
    page._set_processing(False)
    assert page.canvas_preset_combo.isEnabled()
    assert page.background_button.isEnabled()
    assert page.font_color_button.isEnabled()
    page.close()


def test_fifty_title_ui_batch_runs_on_worker_thread(tmp_path: Path) -> None:
    app = QApplication.instance()
    assert app is not None
    page = ThumbnailPage()
    page.output_folder = tmp_path
    page.folder_label.setText(str(tmp_path))
    page.canvas_preset_combo.setCurrentIndex(page.canvas_preset_combo.count() - 1)
    page.width_spin.setValue(320)
    page.height_spin.setValue(180)
    page.margin_spin.setValue(20)
    page.font_size_spin.setValue(32)
    page.min_font_size_spin.setValue(18)
    page.titles_edit.setPlainText("\n".join(f"Title {index}" for index in range(1, 51)))

    heartbeat: list[int] = []
    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(lambda: heartbeat.append(1))
    timer.start()
    page.generate_all()
    thread = page._thread
    assert thread is not None
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(15_000, loop.quit)
    loop.exec()
    timer.stop()
    app.processEvents()

    assert page._thread is None
    assert page.result_label.text() == "50枚生成 / 0件失敗"
    assert page.progress_count.text() == "50 / 50"
    assert page.progress.value() == 100
    assert len(list(tmp_path.glob("*.jpg"))) == 50
    assert heartbeat
    page.close()


def test_page_reports_partial_failure_and_continues(tmp_path: Path) -> None:
    app = QApplication.instance()
    assert app is not None
    page = ThumbnailPage()
    page.output_folder = tmp_path
    page.folder_label.setText(str(tmp_path))
    page.canvas_preset_combo.setCurrentIndex(page.canvas_preset_combo.count() - 1)
    page.width_spin.setValue(300)
    page.height_spin.setValue(180)
    page.margin_spin.setValue(30)
    page.font_size_spin.setValue(40)
    page.min_font_size_spin.setValue(40)
    page.max_lines_spin.setValue(1)
    page.titles_edit.setPlainText(
        "A\n" + "長すぎるタイトル" * 40 + "\nB"
    )

    page.generate_all()
    thread = page._thread
    assert thread is not None
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(15_000, loop.quit)
    loop.exec()
    app.processEvents()

    assert page._thread is None
    assert page.result_label.text().startswith("2枚生成 / 1件失敗")
    failed_item = page.result_tree.topLevelItem(1)
    assert failed_item.text(2) == "エラー"
    assert "収まりません" in failed_item.toolTip(2)
    assert page.result_tree.topLevelItem(2).text(2) == "完了"
    assert len(list(tmp_path.glob("*.jpg"))) == 2
    page.close()


def test_phase2a_four_title_acceptance_scenario(tmp_path: Path) -> None:
    titles = [
        "夜中寝てたら年下彼氏に急に襲われて",
        "嫉妬した彼に問い詰められて",
        "眠れない夜にずっと囁かれて",
        "今すぐ俺のところに来て",
    ]
    records = [TitleRecord(index, title) for index, title in enumerate(titles, start=1)]
    options = settings(
        width=480,
        height=480,
        margin=50,
        font_size=54,
        min_font_size=24,
        max_lines=5,
        output_format=ThumbnailFormat.JPEG,
        quality=90,
    )
    summary: list[tuple[int, int]] = []
    worker = ThumbnailWorker(records, options, tmp_path)
    worker.finished.connect(lambda succeeded, failed: summary.append((succeeded, failed)))
    worker.run()

    outputs = sorted(tmp_path.glob("*.jpg"))
    assert summary == [(4, 0)]
    assert len(outputs) == 4
    assert [path.name[:3] for path in outputs] == ["001", "002", "003", "004"]
    corner_colors = []
    digests = []
    for path in outputs:
        with Image.open(path) as image:
            assert image.size == (480, 480)
            assert image.format == "JPEG"
            corner_colors.append(image.getpixel((0, 0)))
            rgb = image.convert("RGB")
            corner = rgb.getpixel((0, 0))
            assert ImageChops.difference(
                rgb, Image.new("RGB", rgb.size, corner)
            ).getbbox() is not None
            digests.append(hash(rgb.tobytes()))
    assert len(set(corner_colors)) == 1
    assert len(set(digests)) == 4
