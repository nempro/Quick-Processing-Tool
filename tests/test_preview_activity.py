from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quick_processing_tool.preview_activity import PreviewActivityIndicator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate, timeout: float = 0.75) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def test_preview_activity_delay_elapsed_and_completion(qapp):
    now = [10.0]
    indicator = PreviewActivityIndicator(clock=lambda: now[0], delay_ms=30, tick_ms=10)
    indicator.show()
    token = indicator.begin("プレビューを更新しています")
    assert not indicator.isVisible()
    wait_until(qapp, indicator.isVisible)
    assert indicator.status_label.text() == "プレビューを更新しています"
    now[0] = 11.24
    wait_until(qapp, lambda: "1.2秒" in indicator.status_label.text())
    indicator.complete(token)
    assert indicator.isVisible()
    assert indicator.status_label.text() == "✓ 完了しました"
    QTest.qWait(1050)
    assert not indicator.isVisible()


def test_preview_activity_fast_completion_is_never_shown(qapp):
    indicator = PreviewActivityIndicator(delay_ms=80)
    token = indicator.begin("準備中")
    indicator.complete(token)
    QTest.qWait(100)
    assert not indicator.isVisible()
    assert indicator.status_label.text() == ""


def test_preview_activity_cancel_fail_and_stale_tokens(qapp):
    indicator = PreviewActivityIndicator(delay_ms=10)
    first = indicator.begin("最初")
    second = indicator.begin("次")
    indicator.fail(first)
    wait_until(qapp, lambda: indicator.status_label.text() == "次")
    indicator.cancel(second)
    assert indicator.status_label.text() == "キャンセルしました"
    third = indicator.begin("最新")
    wait_until(qapp, lambda: indicator.status_label.text() == "最新")
    QTest.qWait(1050)
    assert indicator.isVisible(), "古い完了hide timerが新しい状態を隠してはならない"
    indicator.fail(third)
    assert indicator.status_label.text() == "処理に失敗しました"
    indicator.invalidate()
    assert not indicator.isVisible()


def test_preview_activity_is_mouse_transparent_and_accessible(qapp):
    indicator = PreviewActivityIndicator()
    assert indicator.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert indicator.accessibleName() == "プレビュー処理の状態"
