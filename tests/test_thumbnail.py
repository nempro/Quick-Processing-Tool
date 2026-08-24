from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from quick_processing_tool.thumbnail_models import (
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


def test_windows_filename_sanitization_and_collision(tmp_path: Path) -> None:
    assert sanitize_filename_component("CON") == "CON_"
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
    page.titles_edit.setPlainText("one\n\n two \nthree")
    assert page.title_count.text() == "3 titles"
    assert len(page.records()) == 3
    page.close()

def test_batch_locks_all_design_controls() -> None:
    page = ThumbnailPage()
    page._set_processing(True)
    assert not page.template_combo.isEnabled()
    assert not page.background_button.isEnabled()
    assert not page.font_color_button.isEnabled()
    assert not page.titles_edit.isEnabled()
    page._set_processing(False)
    assert page.template_combo.isEnabled()
    assert page.background_button.isEnabled()
    assert page.font_color_button.isEnabled()
    page.close()


def test_fifty_title_ui_batch_runs_on_worker_thread(tmp_path: Path) -> None:
    app = QApplication.instance()
    assert app is not None
    page = ThumbnailPage()
    page.output_folder = tmp_path
    page.folder_label.setText(str(tmp_path))
    page.width_spin.setValue(320)
    page.height_spin.setValue(180)
    page.margin_spin.setValue(20)
    page.font_size_spin.setValue(32)
    page.min_font_size_spin.setValue(18)
    page.titles_edit.setPlainText("\n".join(f"Title {index}" for index in range(1, 51)))

    page.generate_all()
    thread = page._thread
    assert thread is not None
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(15_000, loop.quit)
    loop.exec()
    app.processEvents()

    assert page._thread is None
    assert page.result_label.text() == "50 generated · 0 errors"
    assert page.progress.value() == 100
    assert len(list(tmp_path.glob("*.jpg"))) == 50
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
    for path in outputs:
        with Image.open(path) as image:
            assert image.size == (480, 480)
            assert image.format == "JPEG"
            corner_colors.append(image.getpixel((0, 0)))
    assert len(set(corner_colors)) == 1
