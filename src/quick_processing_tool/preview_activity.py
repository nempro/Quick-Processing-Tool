from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy


class PreviewActivityIndicator(QFrame):
    """Compact, latest-request-only status for background preview work."""

    def __init__(
        self,
        parent=None,
        *,
        clock: Callable[[], float] = perf_counter,
        delay_ms: int = 250,
        tick_ms: int = 100,
    ) -> None:
        super().__init__(parent)
        self._clock = clock
        self._delay_ms = delay_ms
        self._tick_ms = tick_ms
        self._serial = 0
        self._token: int | None = None
        self._started_at = 0.0
        self._label = ""
        self.setObjectName("previewActivityIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("プレビュー処理の状態")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self.status_label = QLabel()
        self.status_label.setAccessibleName("プレビュー処理状態")
        layout.addWidget(self.status_label, 1)
        self.setStyleSheet(
            "QFrame#previewActivityIndicator { background: #eef4ff; border: 1px solid #a9bfdf; "
            "border-radius: 6px; } QLabel { color: #315f7d; font-weight: 600; }"
        )
        self._delay_timer = QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(self._show_active)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(tick_ms)
        self._tick_timer.timeout.connect(self._update_elapsed)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_finished)
        self._finishing_token: int | None = None
        self._finishing_serial = 0
        self.hide()

    def begin(self, label: str) -> int:
        self._serial += 1
        self._token = self._serial
        self._started_at = self._clock()
        self._label = label
        self._delay_timer.stop()
        self._tick_timer.stop()
        self._hide_timer.stop()
        self._finishing_token = None
        self.hide()
        self.status_label.setText("")
        self._delay_timer.start(self._delay_ms)
        return self._serial

    def complete(self, token: int) -> None:
        self._finish(token, "✓ 完了しました", allow_fast_hide=True)

    def cancel(self, token: int) -> None:
        self._finish(token, "キャンセルしました")

    def fail(self, token: int) -> None:
        self._finish(token, "処理に失敗しました")

    def invalidate(self) -> None:
        self._serial += 1
        self._token = None
        self._delay_timer.stop()
        self._tick_timer.stop()
        self._hide_timer.stop()
        self._finishing_token = None
        self.hide()
        self.status_label.setText("")

    def _show_active(self) -> None:
        if self._token is None:
            return
        self.status_label.setText(self._label)
        self.show()
        self._tick_timer.start()

    def _update_elapsed(self) -> None:
        if self._token is None or not self.isVisible():
            return
        elapsed = self._clock() - self._started_at
        self.status_label.setText(
            f"{self._label}（{elapsed:.1f}秒）" if elapsed >= 1.0 else self._label
        )

    def _finish(self, token: int, message: str, *, allow_fast_hide: bool = False) -> None:
        if token != self._token:
            return
        was_visible = self.isVisible()
        self._delay_timer.stop()
        self._tick_timer.stop()
        if allow_fast_hide and not was_visible:
            self._token = None
            self.status_label.setText("")
            self.hide()
            return
        self.status_label.setText(message)
        self.show()
        self._finishing_serial = self._serial
        self._finishing_token = token
        self._hide_timer.start(1000)

    def _hide_finished(self) -> None:
        if self._serial != self._finishing_serial or self._token != self._finishing_token:
            return
        self._token = None
        self._finishing_token = None
        self.status_label.setText("")
        self.hide()
