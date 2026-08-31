from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage, QMouseEvent, QPainter, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import quick_processing_tool.edit_ui as edit_ui
from quick_processing_tool.edit_ui import EditPreview, QuickEditPage
from quick_processing_tool.editing import HandDrawSettings, HandPoint, HandStroke, HandTool
from quick_processing_tool.image_workspace import read_source_image


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait(qt_app: QApplication, predicate, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.002)
    qt_app.processEvents()
    assert predicate()


def _loaded_page(qt_app: QApplication, tmp_path: Path, size=(64, 48)) -> tuple[QuickEditPage, Path]:
    source = tmp_path / "hand-source.png"
    Image.new("RGBA", size, (240, 245, 250, 255)).save(source)
    page = QuickEditPage()
    page.resize(1180, 760)
    page.show()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    page.hand_section.toggle.setChecked(True)
    page.hand_mode_enabled.setChecked(True)
    qt_app.processEvents()
    assert page.drop_zone.preview.geometry_generation() == page._preview_geometry_key()
    return page, source


def _viewport_position(page: QuickEditPage, canvas_x: float, canvas_y: float):
    preview = page.drop_zone.preview
    full_width, full_height = page._final_canvas_size()
    item = QPointF(
        canvas_x * preview._image.width() / full_width,
        canvas_y * preview._image.height() / full_height,
    )
    return preview.mapFromScene(preview._item.mapToScene(item))


def test_hand_section_is_compact_ordered_and_processing_safe(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    assert page.sections.index(page.hand_section) == page.sections.index(page.text_section) + 1
    assert page.sections.index(page.transparency_section) == page.sections.index(page.hand_section) + 1
    assert page.hand_pen_button.isChecked()
    assert page.hand_pen_button.minimumHeight() >= 28
    assert page.hand_eraser_button.minimumHeight() >= 28
    assert page.hand_size_spin.value() == 8
    assert page.hand_size_spin.maximum() == 100
    page._set_processing(True)
    for widget in (
        page.hand_mode_enabled,
        page.hand_pen_button,
        page.hand_eraser_button,
        page.hand_eyedropper_button,
        page.hand_color_button,
        page.hand_size_spin,
        page.hand_opacity_spin,
        page.hand_visible_check,
        page.hand_clear_button,
        page.preview_undo_button,
        page.preview_redo_button,
    ):
        assert not widget.isEnabled()
    assert not page.drop_zone.preview._drawing_enabled
    page._set_processing(False)
    page.close()


def test_real_mouse_click_drag_eraser_and_one_gesture_history(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    base_key = preview._item.pixmap().toImage().cacheKey()
    initial_history = len(page._history)
    center = _viewport_position(page, 24, 20)
    QTest.mouseClick(preview.viewport(), Qt.MouseButton.LeftButton, pos=center)
    qt_app.processEvents()
    assert len(page.settings().hand_draw.strokes) == 1
    assert len(page._history) == initial_history + 1
    assert preview._item.pixmap().toImage().cacheKey() == base_key
    assert not preview._overlay_item.pixmap().isNull()

    start = _viewport_position(page, 5, 8)
    end = _viewport_position(page, 55, 35)
    history_before_drag = len(page._history)
    QTest.mousePress(preview.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(preview.viewport(), end, delay=1)
    QTest.mouseRelease(preview.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qt_app.processEvents()
    assert len(page._history) == history_before_drag + 1
    drag = page.settings().hand_draw.strokes[-1]
    assert len(drag.points) == 2
    assert drag.tool is HandTool.PEN

    page.hand_eraser_button.click()
    history_before_erase = len(page._history)
    QTest.mouseClick(preview.viewport(), Qt.MouseButton.LeftButton, pos=center)
    assert len(page._history) == history_before_erase + 1
    assert page.settings().hand_draw.strokes[-1].tool is HandTool.ERASER
    page.undo()
    assert page.settings().hand_draw.strokes[-1].tool is HandTool.PEN
    page.redo()
    assert page.settings().hand_draw.strokes[-1].tool is HandTool.ERASER
    page.close()


def test_hand_eyedropper_samples_visible_composite_returns_to_pen_without_history(
    qt_app, tmp_path: Path
) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    point = HandPoint(24, 20)
    page._hand_draw_settings = HandDrawSettings(
        strokes=(
            HandStroke(
                points=(point,),
                color=(20, 90, 220, 160),
                width=18,
            ),
        ),
        base_width=64,
        base_height=48,
    )
    page._refresh_hand_overlay()
    qt_app.processEvents()
    position = _viewport_position(page, point.x, point.y)
    item_point = preview._item.mapFromScene(preview.mapToScene(position))
    x, y = int(item_point.x()), int(item_point.y())
    base = preview._image.pixelColor(x, y)
    overlay = preview._committed_overlay_image.pixelColor(x, y)
    overlay_alpha = overlay.alphaF()
    base_alpha = base.alphaF()
    output_alpha = overlay_alpha + base_alpha * (1.0 - overlay_alpha)
    expected = QColor(
        *(
            round(
                (
                    overlay_channel * overlay_alpha
                    + base_channel * base_alpha * (1.0 - overlay_alpha)
                )
                / output_alpha
            )
            for base_channel, overlay_channel in zip(
                (base.red(), base.green(), base.blue()),
                (overlay.red(), overlay.green(), overlay.blue()),
            )
        ),
        round(output_alpha * 255),
    )
    assert preview.visible_color_at(QPointF(position)) == expected

    page.hand_opacity_spin.setValue(40)
    history_before = list(page._history)
    strokes_before = page.settings().hand_draw.strokes
    page.hand_eyedropper_button.click()
    assert page.hand_eyedropper_button.isChecked()
    assert preview._hand_color_picking
    assert not preview._drawing_enabled
    QTest.mouseClick(preview.viewport(), Qt.MouseButton.LeftButton, pos=position)
    qt_app.processEvents()

    assert page.hand_pen_button.isChecked()
    assert page._hand_tool is HandTool.PEN
    assert not preview._hand_color_picking
    assert page._hand_color.getRgb() == (
        expected.red(),
        expected.green(),
        expected.blue(),
        102,
    )
    assert page.hand_color_button.text() == expected.name().upper()
    assert page._history == history_before
    assert page.settings().hand_draw.strokes == strokes_before

    draw_position = _viewport_position(page, 45, 35)
    QTest.mouseClick(preview.viewport(), Qt.MouseButton.LeftButton, pos=draw_position)
    qt_app.processEvents()
    drawn = page.settings().hand_draw.strokes[-1]
    assert drawn.color[:3] == (expected.red(), expected.green(), expected.blue())
    assert len(page._history) == len(history_before) + 1
    page.close()


@pytest.mark.parametrize("zoom", ["fit", "100", "200"])
def test_alt_click_is_temporary_zoom_safe_eyedropper(
    qt_app, tmp_path: Path, zoom: str
) -> None:
    source = tmp_path / f"alt-pick-{zoom}.png"
    image = Image.new("RGBA", (80, 60), (200, 20, 30, 255))
    for y in range(60):
        for x in range(40, 80):
            image.putpixel((x, y), (10, 180, 70, 255))
    image.save(source)
    page = QuickEditPage()
    page.resize(900, 620)
    page.show()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    page.hand_section.toggle.setChecked(True)
    page.hand_mode_enabled.setChecked(True)
    page.hand_eraser_button.click()
    page.drop_zone.preview.set_zoom_mode(zoom)
    qt_app.processEvents()

    history_before = list(page._history)
    position = _viewport_position(page, 60, 30)
    QTest.mouseClick(
        page.drop_zone.preview.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        position,
    )
    qt_app.processEvents()
    assert page._hand_color.getRgb() == (10, 180, 70, 255)
    assert page.hand_eraser_button.isChecked()
    assert page._hand_tool is HandTool.ERASER
    assert page._history == history_before
    assert page.settings().hand_draw.strokes == ()
    page.close()


def test_hand_eyedropper_is_safe_on_fully_transparent_pixels(
    qt_app, tmp_path: Path
) -> None:
    source = tmp_path / "transparent-pick.png"
    Image.new("RGBA", (32, 24), (70, 80, 90, 0)).save(source)
    page = QuickEditPage()
    page.resize(900, 620)
    page.show()
    assert page.load_image(source)
    _wait(qt_app, lambda: page._preview_thread is None)
    page.hand_section.toggle.setChecked(True)
    page.hand_mode_enabled.setChecked(True)
    page.hand_opacity_spin.setValue(25)
    history_before = list(page._history)
    page.hand_eyedropper_button.click()
    position = _viewport_position(page, 12, 10)
    QTest.mouseClick(page.drop_zone.preview.viewport(), Qt.MouseButton.LeftButton, pos=position)
    qt_app.processEvents()
    assert page.hand_pen_button.isChecked()
    assert page.drop_zone.preview.visible_color_at(QPointF(position)).alpha() == 0
    assert page._hand_color.getRgb() == (0, 0, 0, 64)
    assert page._history == history_before
    assert page.settings().hand_draw.strokes == ()
    page.close()


def test_tablet_event_path_uses_same_fixed_width_adapter(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    positions = [
        _viewport_position(page, 8, 8),
        _viewport_position(page, 28, 20),
        _viewport_position(page, 48, 35),
    ]

    history_before = len(page._history)
    device = QPointingDevice.primaryPointingDevice()
    for event_type, position, buttons in zip(
        (QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease),
        positions,
        (Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    ):
        global_position = preview.viewport().mapToGlobal(position)
        event = QTabletEvent(
            event_type,
            device,
            QPointF(position),
            QPointF(global_position),
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            Qt.KeyboardModifier.NoModifier,
            Qt.MouseButton.LeftButton,
            buttons,
        )
        QApplication.sendEvent(preview.viewport(), event)
        assert event.isAccepted()
    assert len(page._history) == history_before + 1
    stroke = page.settings().hand_draw.strokes[-1]
    assert stroke.width == page.hand_size_spin.value()
    assert len(stroke.points) == 3
    page.close()


def test_incremental_moves_do_not_rerender_committed_history(qt_app, tmp_path: Path, monkeypatch) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    committed = tuple(
        HandStroke(
            points=(HandPoint(2, 2 + index % 40), HandPoint(60, 2 + index % 40)),
            width=2,
        )
        for index in range(100)
    )
    page._hand_draw_settings = HandDrawSettings(
        strokes=committed,
        base_width=64,
        base_height=48,
    )
    calls: list[tuple[int, int]] = []
    real_render = edit_ui.render_hand_overlay

    def tracked(settings, target):
        calls.append((len(settings.strokes), target[0]))
        return real_render(settings, target)

    monkeypatch.setattr(edit_ui, "render_hand_overlay", tracked)
    page._refresh_hand_overlay()
    assert calls == [(100, page.drop_zone.preview._image.width())]
    page._begin_hand_stroke(HandPoint(1, 1))
    active_begin_elements = page.drop_zone.preview._active_path_item.path().elementCount()
    for index in range(1, 81):
        page._append_hand_point(HandPoint(index * 0.75, 1 + index * 0.4))
    assert calls == [(100, page.drop_zone.preview._image.width())]
    assert page.drop_zone.preview._active_path_item.path().elementCount() > active_begin_elements
    assert len(page._active_hand_points) <= 81
    assert page._preview_thread is None
    assert page.preview_activity._token is None
    page._finish_hand_stroke()
    assert calls[-1][0] == 101
    assert len(calls) == 2
    page.close()


def test_same_geometry_preview_completion_preserves_active_stroke(qt_app, tmp_path: Path, monkeypatch) -> None:
    started = Event()
    release_worker = Event()
    render_calls = 0

    def controlled_render(_path, _settings, _max_dimension):
        nonlocal render_calls
        render_calls += 1
        if render_calls == 2:
            started.set()
            release_worker.wait(2)
        return Image.new("RGBA", (64, 48), "white")

    monkeypatch.setattr(edit_ui, "render_path_preview", controlled_render)
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    viewport = preview.viewport()
    start = _viewport_position(page, 8, 10)
    end = _viewport_position(page, 48, 34)
    history_before = len(page._history)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, end, delay=1)
    assert preview._pointer_active
    assert len(page._active_hand_points) == 2
    active_path = preview._active_path_item.path()
    page.update_preview()
    _wait(qt_app, started.is_set)
    release_worker.set()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert preview._pointer_active
    assert len(page._active_hand_points) == 2
    assert preview._active_path_item.isVisible()
    assert preview._active_path_item.path() == active_path
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    assert len(page._history) == history_before + 1
    stroke = page.settings().hand_draw.strokes[-1]
    assert len(stroke.points) == 2
    assert stroke.points[0].x == pytest.approx(8, abs=0.6)
    assert stroke.points[-1].x == pytest.approx(48, abs=0.6)
    page.close()


def test_transient_semtransparent_pen_matches_committed_alpha_at_join(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    page._hand_color = QColor(200, 20, 30, 64)
    start = HandPoint(8, 20)
    join = HandPoint(30, 20)
    end = HandPoint(52, 32)
    page._begin_hand_stroke(start)
    page._append_hand_point(join)
    page._append_hand_point(end)

    active = QImage(preview._image.size(), QImage.Format.Format_RGBA8888)
    active.fill(QColor(0, 0, 0, 0))
    painter = QPainter(active)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(preview._active_path_item.pen())
    painter.drawPath(preview._active_path_item.path())
    painter.end()
    display_join = preview._canvas_to_display(join)
    active_alpha = active.pixelColor(round(display_join.x()), round(display_join.y())).alpha()
    assert active_alpha == pytest.approx(64, abs=1)

    page._finish_hand_stroke()
    committed_alpha = preview._committed_overlay_image.pixelColor(
        round(display_join.x()), round(display_join.y())
    ).alpha()
    assert committed_alpha == pytest.approx(active_alpha, abs=1)
    page.close()


def test_lost_release_cancels_but_grabbed_outside_release_commits(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    preview = page.drop_zone.preview
    viewport = preview.viewport()
    start = _viewport_position(page, 10, 10)
    history_before = len(page._history)

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    no_button_move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(start),
        QPointF(viewport.mapToGlobal(start)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    preview.mouseMoveEvent(no_button_move)
    assert not preview._pointer_active
    assert page._active_hand_points == []
    assert len(page._history) == history_before
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=start)

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QApplication.sendEvent(preview, QFocusEvent(QEvent.Type.FocusOut))
    assert not preview._pointer_active
    assert page._active_hand_points == []
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=start)

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    outside = QPointF(-20, -20)
    outside_global = QPointF(viewport.mapToGlobal(outside.toPoint()))
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        outside,
        outside_global,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        outside,
        outside_global,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    preview.mouseMoveEvent(move)
    preview.mouseReleaseEvent(release)
    assert len(page._history) == history_before + 1
    assert not preview._pointer_active
    page.close()


def test_hand_color_is_alpha_aware_preference_and_original_hides_layer(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    history_before = tuple(page._history)
    monkeypatch.setattr(edit_ui, "choose_color", lambda *_args, **_kwargs: QColor())
    page.choose_hand_color()
    assert page._hand_color == QColor("#000000")
    monkeypatch.setattr(
        edit_ui,
        "choose_color",
        lambda *_args, **_kwargs: QColor(10, 20, 30, 40),
    )
    page.choose_hand_color()
    assert page._hand_color.getRgb() == (10, 20, 30, 40)
    assert page.hand_color_button.text() == "#0A141E"
    assert page.hand_opacity_spin.value() == 16
    assert "不透明度 16%" in page.hand_color_button.toolTip()
    assert tuple(page._history) == history_before

    page._begin_hand_stroke(HandPoint(20, 20))
    page._finish_hand_stroke()
    strokes = page.settings().hand_draw.strokes
    assert strokes[-1].color == (10, 20, 30, 40)
    page.show_original()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert page.drop_zone.preview._overlay_item.pixmap().isNull()
    assert not page.drop_zone.preview._drawing_enabled
    assert page.settings().hand_draw.strokes == strokes
    page.show_edited()
    _wait(qt_app, lambda: page._preview_thread is None)
    assert not page.drop_zone.preview._overlay_item.pixmap().isNull()
    page.close()


def test_hand_opacity_percent_mapping_and_picker_exact_alpha_contract(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    assert page.hand_opacity_spin.minimum() == 0
    assert page.hand_opacity_spin.maximum() == 100
    assert page.hand_opacity_spin.value() == 100
    history_before = tuple(page._history)

    for percent, alpha in ((25, 64), (50, 128), (100, 255), (0, 0)):
        page.hand_opacity_spin.setValue(percent)
        assert page._hand_color.alpha() == alpha
        assert tuple(page._history) == history_before

    page.hand_opacity_spin.setValue(25)
    picker_initial_alphas: list[int] = []

    def accept_alpha_127(initial, *_args, **_kwargs):
        picker_initial_alphas.append(initial.alpha())
        return QColor(10, 20, 30, 127)

    monkeypatch.setattr(edit_ui, "choose_color", accept_alpha_127)
    page.choose_hand_color()
    assert picker_initial_alphas == [64]
    assert page._hand_color.getRgb() == (10, 20, 30, 127)
    assert page.hand_opacity_spin.value() == 50
    assert page.hand_color_button.text() == "#0A141E"
    assert "rgba(" not in page.hand_color_button.styleSheet().lower()
    assert "#0a141e" in page.hand_color_button.styleSheet().lower()
    assert "#0A141E" in page.hand_color_button.accessibleName()
    assert "不透明度 50%" in page.hand_color_button.accessibleName()

    def cancel_picker(initial, *_args, **_kwargs):
        picker_initial_alphas.append(initial.alpha())
        return QColor()

    monkeypatch.setattr(edit_ui, "choose_color", cancel_picker)
    page.choose_hand_color()
    assert picker_initial_alphas == [64, 127]
    assert page._hand_color.getRgb() == (10, 20, 30, 127)
    assert page.hand_opacity_spin.value() == 50
    assert tuple(page._history) == history_before
    page.close()


def test_hand_opacity_applies_only_to_new_strokes_and_preferences_survive_reset_source(
    qt_app, tmp_path: Path
) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    page._begin_hand_stroke(HandPoint(8, 8))
    page._finish_hand_stroke()
    first = page.settings().hand_draw.strokes[0]
    assert first.color[3] == 255

    history_before_preference = len(page._history)
    page.hand_opacity_spin.setValue(25)
    assert len(page._history) == history_before_preference
    page._begin_hand_stroke(HandPoint(18, 8))
    assert page._active_hand_color.alpha() == 64
    page.hand_opacity_spin.setValue(50)
    page._finish_hand_stroke()
    assert page.settings().hand_draw.strokes[0] == first
    assert page.settings().hand_draw.strokes[1].color[3] == 64

    page._begin_hand_stroke(HandPoint(28, 8))
    page._finish_hand_stroke()
    assert page.settings().hand_draw.strokes[2].color[3] == 128
    page.hand_opacity_spin.setValue(0)
    page._begin_hand_stroke(HandPoint(38, 8))
    page._finish_hand_stroke()
    assert page.settings().hand_draw.strokes[3].color[3] == 0

    page.reset_edits()
    assert page.settings().hand_draw.strokes == ()
    assert page.hand_opacity_spin.value() == 0
    assert page._hand_color.alpha() == 0
    replacement = tmp_path / "opacity-preference-source.png"
    Image.new("RGB", (32, 24), "blue").save(replacement)
    assert page.load_image(replacement)
    _wait(qt_app, lambda: page._preview_thread is None)
    assert page.hand_opacity_spin.value() == 0
    assert page._hand_color.alpha() == 0
    page.close()


def test_hand_layer_visibility_and_clear_remain_available_with_draw_mode_off(
    qt_app, tmp_path: Path
) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    page._begin_hand_stroke(HandPoint(12, 12))
    page._finish_hand_stroke()
    strokes = page.settings().hand_draw.strokes
    page.hand_mode_enabled.setChecked(False)
    qt_app.processEvents()

    assert not page.hand_details.isVisible()
    assert page.hand_visible_check.isEnabled()
    assert page.hand_clear_button.isEnabled()

    history_before_visibility = len(page._history)
    page.hand_visible_check.click()
    assert not page.settings().hand_draw.visible
    assert page.settings().hand_draw.strokes == strokes
    assert len(page._history) == history_before_visibility + 1
    page.undo()
    assert page.settings().hand_draw.visible
    assert page.settings().hand_draw.strokes == strokes

    history_before_clear = page._history_index
    page.hand_clear_button.click()
    assert page.settings().hand_draw.strokes == ()
    assert page._history_index == history_before_clear + 1
    page.undo()
    assert page.settings().hand_draw.strokes == strokes
    page.close()


def test_large_final_canvas_overlay_uses_only_display_sized_raster(qt_app, monkeypatch) -> None:
    del qt_app
    preview = EditPreview()
    preview.set_image(QImage(1000, 100, QImage.Format.Format_RGBA8888), (10000, 1000), 1)
    settings = HandDrawSettings(
        strokes=(
            HandStroke(
                points=(HandPoint(0, 0), HandPoint(9999, 999)),
                width=8,
            ),
        ),
        base_width=10000,
        base_height=1000,
    )
    targets: list[tuple[int, int]] = []
    real_render = edit_ui.render_hand_overlay

    def tracked(current, target):
        targets.append(target)
        return real_render(current, target)

    monkeypatch.setattr(edit_ui, "render_hand_overlay", tracked)
    preview.set_hand_overlay(settings)
    assert targets == [(1000, 100)]
    preview.deleteLater()


def test_transient_overlay_has_no_worker_and_visibility_clear_share_history(qt_app, tmp_path: Path, monkeypatch) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    scheduled: list[bool] = []
    monkeypatch.setattr(page, "schedule_preview", lambda: scheduled.append(True))
    history_before = len(page._history)
    page._begin_hand_stroke(HandPoint(2, 2))
    page._append_hand_point(HandPoint(50, 40))
    assert len(page._history) == history_before
    assert page._preview_thread is None
    assert page.preview_activity._token is None
    page.undo()
    assert len(page._history) == history_before
    page._finish_hand_stroke()
    assert len(page._history) == history_before + 1
    assert scheduled == []

    page.hand_visible_check.setChecked(False)
    assert not page.settings().hand_draw.visible
    assert page.drop_zone.preview._overlay_item.pixmap().isNull()
    page.undo()
    assert page.settings().hand_draw.visible
    page.clear_hand_draw()
    assert page.settings().hand_draw.strokes == ()
    page.undo()
    assert page.settings().hand_draw.strokes
    page.undo()
    page._begin_hand_stroke(HandPoint(10, 10))
    page._finish_hand_stroke()
    assert page._history_index == len(page._history) - 1
    assert not page.redo_button.isEnabled()
    page.close()


def test_zoom_scroll_mapping_and_canvas_resize_scale_one_action(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path, (100, 50))
    preview = page.drop_zone.preview
    for mode in ("fit", "100", "200"):
        page.preview_zoom_combo.setCurrentIndex(page.preview_zoom_combo.findData(mode))
        qt_app.processEvents()
        viewport = _viewport_position(page, 50, 25)
        point = preview.viewport_to_canvas(QPointF(viewport))
        assert point is not None
        assert point.x == pytest.approx(50, abs=0.6)
        assert point.y == pytest.approx(25, abs=0.6)
    preview.horizontalScrollBar().setValue(preview.horizontalScrollBar().maximum())
    preview.verticalScrollBar().setValue(preview.verticalScrollBar().maximum())
    viewport = _viewport_position(page, 80, 35)
    mapped = preview.viewport_to_canvas(QPointF(viewport))
    assert mapped is not None
    assert mapped.x == pytest.approx(80, abs=0.6)
    assert mapped.y == pytest.approx(35, abs=0.6)

    page._begin_hand_stroke(HandPoint(20, 10))
    page._finish_hand_stroke()
    history_before_resize = len(page._history)
    page.canvas_preset_combo.setCurrentIndex(1)
    assert len(page._history) == history_before_resize + 1
    scaled = page.settings().hand_draw
    assert (scaled.base_width, scaled.base_height) == (128, 128)
    assert scaled.strokes[0].points[0] == HandPoint(25.6, 25.6)
    assert scaled.strokes[0].width == pytest.approx(8 * 1.28)
    page.undo()
    restored = page.settings().hand_draw
    assert (restored.base_width, restored.base_height) == (100, 50)
    assert restored.strokes[0].points[0] == HandPoint(20, 10)
    page.close()


def test_source_same_preserves_replace_resets_and_failed_replace_is_atomic(qt_app, tmp_path: Path, monkeypatch) -> None:
    page, source = _loaded_page(qt_app, tmp_path)
    page._begin_hand_stroke(HandPoint(12, 12))
    page._finish_hand_stroke()
    before = page.settings()
    page.set_current_source(read_source_image(source, 1))
    assert page.settings() == before

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: warnings.append((title, text)))
    assert not page.load_image(corrupt)
    assert page.settings() == before
    assert warnings[-1][0] == "画像を開けません"

    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (32, 24), "blue").save(replacement)
    assert page.load_image(replacement)
    _wait(qt_app, lambda: page._preview_thread is None)
    assert page.settings().hand_draw.strokes == ()
    assert page._history == [page.settings()]
    page.close()


def test_preview_history_buttons_share_history_and_stay_outside_canvas(qt_app, tmp_path: Path) -> None:
    page, _source = _loaded_page(qt_app, tmp_path)
    assert page.preview_history_bar.isVisible()
    assert page.preview_undo_button.text() == "↶"
    assert page.preview_redo_button.text() == "↷"
    assert not page.preview_undo_button.isEnabled()
    assert not page.preview_redo_button.isEnabled()
    assert page.preview_history_bar.parentWidget() is page.drop_zone.parentWidget()
    assert page.preview_history_bar.geometry().top() >= page.drop_zone.geometry().bottom()

    bar_position = page.preview_history_bar.pos()
    for mode in ("fit", "100", "200"):
        page.preview_zoom_combo.setCurrentIndex(page.preview_zoom_combo.findData(mode))
        qt_app.processEvents()
        assert page.preview_history_bar.pos() == bar_position

    page._begin_hand_stroke(HandPoint(12, 12))
    page._finish_hand_stroke()
    assert page.undo_button.isEnabled() and page.preview_undo_button.isEnabled()
    page.preview_undo_button.click()
    assert page.settings().hand_draw.strokes == ()
    assert page.redo_button.isEnabled() and page.preview_redo_button.isEnabled()
    page.preview_redo_button.click()
    assert len(page.settings().hand_draw.strokes) == 1

    page.hand_mode_enabled.setChecked(False)
    assert page.preview_history_bar.isHidden()
    page.hand_mode_enabled.setChecked(True)
    assert page.preview_history_bar.isVisible()
    page.close()


@pytest.mark.parametrize("width,minimum_preview", [(720, 240), (900, 380), (1180, 560), (1440, 701)])
def test_hand_layout_has_no_horizontal_scroll_or_preview_regression(qt_app, width: int, minimum_preview: int) -> None:
    page = QuickEditPage()
    page.resize(width, 760)
    page.show()
    page.hand_section.toggle.setChecked(True)
    page.hand_mode_enabled.setChecked(True)
    qt_app.processEvents()
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    assert page.settings_scroll.widget().width() <= page.settings_scroll.viewport().width()
    assert page.drop_zone.width() >= minimum_preview
    assert page.hand_pen_button.width() >= 30
    assert page.hand_eraser_button.width() >= 30
    assert page.hand_eyedropper_button.width() >= 30
    assert page.preview_history_bar.width() == page.drop_zone.width()
    assert page.preview_undo_button.geometry().right() < page.preview_redo_button.geometry().left()
    page.close()
