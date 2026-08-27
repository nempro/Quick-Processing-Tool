from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from .image_workspace import SourceImage


def human_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class CurrentSourceCard(QFrame):
    change_requested = Signal()

    def __init__(self, *, show_change_button: bool = True) -> None:
        super().__init__()
        self.setObjectName("currentSourceCard")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(
            "QFrame#currentSourceCard { background: #f5f8fc; border: 1px solid #c8d4e3; "
            "border-radius: 8px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(2)
        title = QLabel("現在の画像")
        title.setStyleSheet("font-weight: 700; color: #182230; border: 0;")
        layout.addWidget(title)
        self.name_label = QLabel("未選択")
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.setStyleSheet("font-weight: 650; color: #273142; border: 0;")
        layout.addWidget(self.name_label)
        self.meta_label = QLabel("画像を選択してください")
        self.meta_label.setMinimumWidth(0)
        self.meta_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta_label.setStyleSheet("color: #667085; border: 0;")
        layout.addWidget(self.meta_label)
        self.change_button = QPushButton("画像を変更")
        self.change_button.setMinimumWidth(0)
        self.change_button.clicked.connect(self.change_requested)
        self.change_button.setVisible(show_change_button)
        layout.addWidget(self.change_button)

    def set_source(self, source: SourceImage | None) -> None:
        if source is None:
            self.name_label.setText("未選択")
            self.name_label.setToolTip("")
            self.meta_label.setText("画像を選択してください")
            return
        width = max(80, self.width() - 22)
        name = self.fontMetrics().elidedText(source.filename, Qt.TextElideMode.ElideMiddle, width)
        self.name_label.setText(name)
        self.name_label.setToolTip(str(source.path))
        self.meta_label.setText(
            f"{source.width} × {source.height} / {source.format} / {human_file_size(source.size_bytes)}"
        )
