from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from quick_processing_tool import __version__
from quick_processing_tool.app import configure_logging
from quick_processing_tool.app_icon import REQUIRED_ICON_SIZES, validate_icon
from quick_processing_tool.app_identity import apply_application_icon


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


def test_formal_icon_validator_requires_all_windows_sizes(tmp_path: Path) -> None:
    icon_path = tmp_path / "formal.ico"
    image = Image.new("RGBA", (256, 256), (95, 155, 220, 255))
    image.save(icon_path, sizes=sorted(REQUIRED_ICON_SIZES))

    assert REQUIRED_ICON_SIZES <= validate_icon(icon_path)


def test_formal_icon_validator_rejects_incomplete_ico(tmp_path: Path) -> None:
    icon_path = tmp_path / "incomplete.ico"
    Image.new("RGBA", (32, 32), (95, 155, 220, 255)).save(icon_path)

    with pytest.raises(ValueError, match="missing sizes"):
        validate_icon(icon_path)


def test_application_icon_is_shared_by_qt_windows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    icon_path = tmp_path / "formal.ico"
    Image.new("RGBA", (256, 256), (95, 155, 220, 255)).save(
        icon_path, sizes=sorted(REQUIRED_ICON_SIZES)
    )

    assert apply_application_icon(app, icon_path)
    assert not app.windowIcon().isNull()
    app.setWindowIcon(QIcon())
