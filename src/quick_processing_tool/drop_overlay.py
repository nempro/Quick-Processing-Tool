from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame


class RoundedDropOverlay(QFrame):
    """Paint a clipped rounded dashed border without Qt stylesheet dash joins."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active = False
        self.setStyleSheet("background: transparent; border: none;")

    def set_drop_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor("#eaf1ff") if self._active else QColor("#f7f9fc")
        painter.fillRect(self.rect(), background)
        width = 3.0 if self._active else 2.0
        rect = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 16.0, 16.0)
        pen = QPen(QColor("#315fbd") if self._active else QColor("#8f9bad"))
        pen.setWidthF(width)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPath(path)
