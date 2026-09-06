"""Shared compact controls and values for image-preview zoom."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy


PREVIEW_ZOOM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("全体表示", "fit"),
    ("100%", "100"),
    ("200%", "200"),
    ("400%", "400"),
)


def zoom_factor_for_mode(mode: str) -> float | None:
    """Translate a shared UI mode into a QGraphicsView scale factor."""
    factors = {"fit": None, "100": 1.0, "200": 2.0, "400": 4.0}
    try:
        return factors[mode]
    except KeyError as exc:
        raise ValueError(f"Unsupported preview zoom mode: {mode}") from exc


def create_preview_zoom_row(
    parent: QObject,
    on_selected: Callable[[str], None],
) -> tuple[QHBoxLayout, QButtonGroup, dict[str, QPushButton]]:
    """Build the common Preview zoom selector used by image tools."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    label = QLabel("表示")
    label.setStyleSheet("color: #667085; font-weight: 600;")
    row.addWidget(label)

    group = QButtonGroup(parent)
    group.setExclusive(True)
    buttons: dict[str, QPushButton] = {}
    for label, mode in PREVIEW_ZOOM_OPTIONS:
        button = QPushButton(label)
        button.setCheckable(True)
        button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setAccessibleName(f"プレビュー倍率 {label}")
        button.setStyleSheet(
            "QPushButton { padding: 4px 6px; }"
            "QPushButton:checked { background: #dbeafe; color: #174ea6;"
            "border: 2px solid #315fbd; font-weight: 700; }"
        )
        button.clicked.connect(lambda _checked=False, selected=mode: on_selected(selected))
        group.addButton(button)
        buttons[label] = button
        row.addWidget(button, 1)
    buttons["全体表示"].setChecked(True)
    return row, group, buttons
