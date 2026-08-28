from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication

from quick_processing_tool import __version__
from quick_processing_tool.app import configure_logging


def test_release_version_contract() -> None:
    assert __version__ == "0.2.0"


def test_configure_logging_uses_bounded_utf8_appdata_log(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    log_path = configure_logging()
    assert log_path == tmp_path / "QuickProcessingTool" / "quick_processing_tool.log"
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 3
    logging.getLogger(__name__).info("日本語ログ")
    handler.flush()
    assert "日本語ログ" in log_path.read_text(encoding="utf-8")
    handler.close()
    logging.getLogger().handlers.clear()


def test_application_metadata_can_be_applied() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Quick Processing Tool")
    app.setApplicationVersion(__version__)
    assert app.applicationName() == "Quick Processing Tool"
    assert app.applicationVersion() == "0.2.0"
