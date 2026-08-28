from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QSize


DEFAULT_WINDOW_SIZE = QSize(1180, 760)
BASE_MINIMUM_SIZE = QSize(900, 620)
SCREEN_USAGE_RATIO = 0.90


def fitted_window_size(
    requested: QSize,
    available: QRect,
    *,
    ratio: float = SCREEN_USAGE_RATIO,
) -> QSize:
    max_width = max(1, round(available.width() * ratio))
    max_height = max(1, round(available.height() * ratio))
    return QSize(max(1, min(requested.width(), max_width)), max(1, min(requested.height(), max_height)))


def centered_window_geometry(
    available: QRect,
    requested: QSize = DEFAULT_WINDOW_SIZE,
) -> QRect:
    size = fitted_window_size(requested, available)
    x = available.x() + (available.width() - size.width()) // 2
    y = available.y() + (available.height() - size.height()) // 2
    return QRect(x, y, size.width(), size.height())


def clamp_window_geometry(
    requested: QRect,
    available_geometries: Sequence[QRect],
    *,
    fallback_index: int = 0,
) -> QRect:
    if not available_geometries:
        return QRect(requested)
    fallback_index = max(0, min(fallback_index, len(available_geometries) - 1))
    intersections = [requested.intersected(geometry) for geometry in available_geometries]
    areas = [max(0, rect.width()) * max(0, rect.height()) for rect in intersections]
    target = available_geometries[areas.index(max(areas))] if max(areas) > 0 else available_geometries[fallback_index]
    size = fitted_window_size(requested.size(), target)
    max_x = target.x() + target.width() - size.width()
    max_y = target.y() + target.height() - size.height()
    x = max(target.x(), min(requested.x(), max_x))
    y = max(target.y(), min(requested.y(), max_y))
    return QRect(x, y, size.width(), size.height())


def adaptive_minimum_size(available: QRect) -> QSize:
    return fitted_window_size(BASE_MINIMUM_SIZE, available)
