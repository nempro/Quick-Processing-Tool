from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .app_identity import apply_application_icon
from .ui import MainWindow


def configure_logging() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "QuickProcessingTool"
    base.mkdir(parents=True, exist_ok=True)
    log_path = base / "quick_processing_tool.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )
    return log_path


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Meiryo UI", 9))
    app.setApplicationName("Quick Processing Tool")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Quick Processing Tool")
    try:
        log_path = configure_logging()
    except OSError as exc:
        QMessageBox.critical(
            None,
            "Quick Processing Toolを起動できません",
            f"ログ保存先を準備できませんでした。\n{exc}",
        )
        return 1

    logger = logging.getLogger(__name__)
    if apply_application_icon(app):
        logger.info("Formal application icon applied")
    else:
        logger.warning("Formal application icon is not installed")
    logger.info("App start; version=%s; log=%s", __version__, log_path)

    def report_unhandled(exc_type, exc, traceback) -> None:
        logger.critical("Unhandled application error", exc_info=(exc_type, exc, traceback))
        QMessageBox.critical(
            None,
            "予期しないエラーが発生しました",
            f"処理を続けられませんでした。ログを確認してください。\n\n{log_path}",
        )

    sys.excepthook = report_unhandled
    try:
        window = MainWindow()
    except Exception as exc:
        logger.exception("Application startup failed")
        QMessageBox.critical(
            None,
            "Quick Processing Toolを起動できません",
            f"起動処理に失敗しました。\n{exc}\n\nログ: {log_path}",
        )
        return 1
    window.show()
    return app.exec()
