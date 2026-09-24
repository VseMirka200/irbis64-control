from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QMessageBox, QWidget

from irbis_control import APP_TITLE
from irbis_control.paths import icon_path


class AppMessageBox(QMessageBox):
    """Единое окно уведомлений приложения.

    Сохраняет интерфейс стандартного ``QMessageBox`` для существующего кода,
    но всегда использует общий стиль приложения, копируемый текст и безопасный
    выбор кнопки по умолчанию для подтверждений.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appMessageBox")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

    @classmethod
    def _show(
        cls,
        icon: QMessageBox.Icon,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title or APP_TITLE)
        box.setText(str(text))
        box.setStandardButtons(buttons)

        if default_button != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        elif buttons & QMessageBox.StandardButton.No:
            # Для потенциально опасных действий Enter не должен означать «Да».
            box.setDefaultButton(QMessageBox.StandardButton.No)
        elif buttons & QMessageBox.StandardButton.Cancel:
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        elif buttons & QMessageBox.StandardButton.Ok:
            box.setDefaultButton(QMessageBox.StandardButton.Ok)

        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setWordWrap(True)
            label.setMinimumWidth(320)
            label.setMaximumWidth(680)
        box.adjustSize()
        result = box.exec()
        try:
            return QMessageBox.StandardButton(result)
        except ValueError:
            return QMessageBox.StandardButton.NoButton

    @classmethod
    def information(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        return cls._show(QMessageBox.Icon.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        return cls._show(QMessageBox.Icon.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        return cls._show(QMessageBox.Icon.Critical, parent, title, text, buttons, defaultButton)

    @classmethod
    def question(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        return cls._show(QMessageBox.Icon.Question, parent, title, text, buttons, defaultButton)
