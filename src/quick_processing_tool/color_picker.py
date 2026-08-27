from __future__ import annotations

import logging

from PySide6.QtCore import QLibraryInfo, QTranslator, Qt
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)


LOGGER = logging.getLogger(__name__)
_TRANSLATOR_ATTRIBUTE = "_quick_processing_qt_translators"


def install_japanese_qt_translations(app: QApplication | None = None) -> bool:
    """Install Qt's bundled Japanese strings once and retain the translator."""
    application = app or QApplication.instance()
    if application is None:
        LOGGER.warning("Qt日本語翻訳を読み込めません: QApplicationがありません")
        return False
    retained = getattr(application, _TRANSLATOR_ATTRIBUTE, None)
    if retained:
        return True
    translator = QTranslator(application)
    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load("qtbase_ja", translations_path):
        LOGGER.warning("Qt日本語翻訳を読み込めません: %s", translations_path)
        return False
    if not application.installTranslator(translator):
        LOGGER.warning("Qt日本語翻訳を適用できません: %s", translations_path)
        return False
    setattr(application, _TRANSLATOR_ATTRIBUTE, [translator])
    return True


class ColorPickerDialog(QColorDialog):
    def __init__(
        self,
        initial: QColor,
        parent: QWidget | None = None,
        title: str = "色を選ぶ",
        *,
        show_alpha: bool = False,
    ) -> None:
        install_japanese_qt_translations()
        super().__init__(parent)
        options = (
            QColorDialog.ColorDialogOption.DontUseNativeDialog
            | QColorDialog.ColorDialogOption.NoButtons
        )
        if show_alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        self.setOptions(options)
        self.setWindowTitle(title)
        self.setCurrentColor(QColor(initial))

        footer = QWidget(self)
        footer.setObjectName("colorPickerFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(8)
        self.cancel_button = QPushButton("キャンセル", footer)
        self.apply_button = QPushButton("この色に変更", footer)
        for button in (self.cancel_button, self.apply_button):
            button.setMinimumWidth(0)
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            footer_layout.addWidget(button, 1)
        self.cancel_button.setAccessibleName("色の変更をキャンセル")
        self.apply_button.setAccessibleName("選択した色に変更")
        self.apply_button.setDefault(True)
        self.apply_button.setAutoDefault(True)
        self.apply_button.setStyleSheet(
            "QPushButton { background: #315fbd; color: white; border: 2px solid #174a9c; "
            "border-radius: 7px; font-weight: 700; padding: 6px 12px; }"
            "QPushButton:hover { background: #284fa1; border-color: #102f6b; }"
            "QPushButton:focus { border-color: #081f52; }"
        )
        self.cancel_button.setStyleSheet(
            "QPushButton { background: #f5f7fa; color: #182230; border: 1px solid #7b899a; "
            "border-radius: 7px; padding: 7px 12px; }"
            "QPushButton:hover { background: #e5edf6; border-color: #405b79; }"
            "QPushButton:focus { border: 2px solid #2457b2; padding: 6px 11px; }"
        )
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        self.layout().addWidget(footer)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)


def choose_color(
    initial: QColor,
    parent: QWidget | None,
    title: str = "色を選ぶ",
    *,
    show_alpha: bool = False,
) -> QColor:
    dialog = ColorPickerDialog(initial, parent, title, show_alpha=show_alpha)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.currentColor()
            if not selected.isValid():
                return QColor()
            result = QColor(selected)
            if not show_alpha:
                result.setAlpha(255)
            return result
        return QColor()
    finally:
        dialog.deleteLater()
