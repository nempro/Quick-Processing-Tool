from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def configure_logging() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "QuickProcessingTool"
    base.mkdir(parents=True, exist_ok=True)
    log_path = base / "quick_processing_tool.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    return log_path


def main() -> int:
    log_path = configure_logging()
    logging.getLogger(__name__).info("App start; log=%s", log_path)
    app = QApplication(sys.argv)
    app.setFont(QFont("Meiryo UI", 9))
    app.setApplicationName("Quick Processing Tool")
    app.setOrganizationName("Quick Processing Tool")
    window = MainWindow()
    window.show()
    return app.exec()
