from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


APP_ICON_FILENAME = "quick-processing-tool.ico"


def application_icon_path() -> Path | None:
    """Return the formal icon path for source and PyInstaller executions."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = (
            Path(frozen_root)
            / "quick_processing_tool"
            / "assets"
            / APP_ICON_FILENAME
        )
    else:
        candidate = (
            Path(__file__).resolve().parents[2]
            / "assets"
            / "windows"
            / APP_ICON_FILENAME
        )
    return candidate if candidate.is_file() else None


def apply_application_icon(
    app: QApplication, icon_path: Path | None = None
) -> bool:
    """Apply one icon to all Qt top-level windows when a formal asset exists."""
    path = icon_path or application_icon_path()
    if path is None or not path.is_file():
        return False
    icon = QIcon(str(path))
    if icon.isNull():
        return False
    app.setWindowIcon(icon)
    return True
