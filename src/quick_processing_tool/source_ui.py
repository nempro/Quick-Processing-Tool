from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

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

    def __init__(
        self,
        *,
        show_change_button: bool = True,
        title_text: str = "画像：",
    ) -> None:
        super().__init__()
        self.setObjectName("currentSourceCard")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._source: SourceImage | None = None
        self.setStyleSheet(
            "QFrame#currentSourceCard { background: #f5f8fc; border: 1px solid #c8d4e3; "
            "border-radius: 8px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(1)
        first_row = QHBoxLayout()
        first_row.setContentsMargins(0, 0, 0, 0)
        first_row.setSpacing(4)
        self.title_label = QLabel(title_text)
        self.title_label.setStyleSheet("font-weight: 700; color: #182230; border: 0;")
        first_row.addWidget(self.title_label)
        self.name_label = QLabel("未選択")
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.setStyleSheet("font-weight: 650; color: #273142; border: 0;")
        first_row.addWidget(self.name_label, 1)
        self.change_button = QPushButton("変更")
        self.change_button.setObjectName("currentSourceChange")
        self.change_button.setMinimumWidth(0)
        self.change_button.setMaximumHeight(32)
        self.change_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.change_button.setToolTip("別の画像を選びます")
        self.change_button.setAccessibleName("別の画像を選ぶ")
        self.change_button.setStyleSheet(
            "QPushButton#currentSourceChange { min-height: 26px; max-height: 30px; padding: 1px 8px; "
            "background: #f5f7fa; color: #273142; border: 1px solid #7b899a; border-radius: 5px; }"
            "QPushButton#currentSourceChange:hover { background: #e5edf6; border-color: #405b79; }"
            "QPushButton#currentSourceChange:focus { border: 2px solid #2457b2; padding: 0 7px; }"
            "QPushButton#currentSourceChange:disabled { background: #f3f4f6; color: #9aa1aa; border-color: #d4d8de; }"
        )
        self.change_button.clicked.connect(self.change_requested)
        self.change_button.setVisible(show_change_button)
        first_row.addWidget(self.change_button)
        layout.addLayout(first_row)
        self.meta_label = QLabel("画像を選択してください")
        self.meta_label.setMinimumWidth(0)
        self.meta_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta_label.setStyleSheet("color: #667085; border: 0;")
        layout.addWidget(self.meta_label)

    def set_source(self, source: SourceImage | None) -> None:
        self._source = source
        if source is None:
            self.name_label.setText("未選択")
            self.name_label.setToolTip("")
            self.meta_label.setText("画像を選択してください")
            self.meta_label.setToolTip("")
            return
        self._update_elision()
        alpha = "あり" if source.has_alpha else "なし"
        details = (
            f"{source.filename}\n{source.path}\n{source.width} × {source.height} / {source.format} / "
            f"{human_file_size(source.size_bytes)} / 透過: {alpha}"
        )
        self.name_label.setToolTip(details)
        self.meta_label.setText(f"{source.width} × {source.height} / {source.format}")
        self.meta_label.setToolTip(details)

    def _update_elision(self) -> None:
        if self._source is None:
            return
        width = max(40, self.name_label.width() - 2)
        self.name_label.setText(
            self.name_label.fontMetrics().elidedText(
                self._source.filename, Qt.TextElideMode.ElideMiddle, width
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_elision()
