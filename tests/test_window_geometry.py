from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication

from quick_processing_tool.ui import MainWindow
from quick_processing_tool.window_geometry import (
    adaptive_minimum_size,
    centered_window_geometry,
    clamp_window_geometry,
)


def _inside(inner: QRect, outer: QRect) -> bool:
    return outer.contains(inner.topLeft()) and outer.contains(inner.bottomRight())


def test_default_window_fits_1920x1080_available_geometry() -> None:
    available = QRect(0, 0, 1920, 1040)
    result = centered_window_geometry(available)
    assert result.size() == QSize(1180, 760)
    assert _inside(result, available)


def test_default_window_scales_for_1280x800_and_small_display() -> None:
    medium = centered_window_geometry(QRect(0, 0, 1280, 760))
    small_available = QRect(100, 50, 800, 600)
    small = centered_window_geometry(small_available)
    assert medium.size() == QSize(1152, 684)
    assert small.size() == QSize(720, 540)
    assert adaptive_minimum_size(small_available) == QSize(720, 540)
    assert _inside(small, small_available)


def test_125_percent_logical_geometry_uses_available_pixels() -> None:
    available = QRect(0, 0, 1536, 824)
    result = centered_window_geometry(available)
    assert result.size() == QSize(1180, 742)
    assert _inside(result, available)


def test_restored_oversized_and_offscreen_geometry_clamps() -> None:
    available = QRect(0, 0, 1280, 800)
    oversized = clamp_window_geometry(QRect(10, 20, 2400, 1600), [available])
    offscreen = clamp_window_geometry(QRect(5000, 4000, 1000, 700), [available])
    assert oversized.size() == QSize(1152, 720)
    assert _inside(oversized, available)
    assert _inside(offscreen, available)
    assert offscreen.top() >= available.top()


def test_large_screen_restore_and_multi_screen_selection() -> None:
    primary = QRect(0, 0, 1920, 1040)
    large = QRect(1920, 0, 2560, 1400)
    restored = QRect(2050, 100, 1600, 900)
    assert clamp_window_geometry(restored, [primary, large]) == restored

    left = QRect(-1280, 0, 1280, 760)
    on_left = QRect(-1200, 40, 900, 620)
    assert clamp_window_geometry(on_left, [primary, left]) == on_left


def test_main_window_initial_geometry_fits_its_startup_screen() -> None:
    app = QApplication.instance() or QApplication([])
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    assert screen is not None
    window = MainWindow()
    assert screen.availableGeometry().contains(window.geometry())
    assert window.minimumWidth() <= window.width()
    assert window.minimumHeight() <= window.height()
    window.close()
