from __future__ import annotations

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
from PyQt6.QtWidgets import QApplication


class _RussianButtonTranslator(QTranslator):
    """Переводит стандартные кнопки, если файл перевода Qt недоступен."""

    _translations = {
        "Yes": "Да",
        "&Yes": "&Да",
        "No": "Нет",
        "&No": "&Нет",
        "OK": "ОК",
        "Cancel": "Отмена",
        "Close": "Закрыть",
        "Open": "Открыть",
        "Save": "Сохранить",
        "Apply": "Применить",
        "Reset": "Сбросить",
        "Retry": "Повторить",
        "Ignore": "Игнорировать",
        "Abort": "Прервать",
    }

    def translate(
        self,
        context: str | None,
        source_text: str | None,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str:
        return self._translations.get(source_text or "", "")


def install_russian_ui(app: QApplication) -> None:
    """Принимает приложение Qt и подключает русский перевод диалогов."""
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))

    qt_translator = QTranslator(app)
    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load("qtbase_ru", translations_path):
        app.installTranslator(qt_translator)

    fallback_translator = _RussianButtonTranslator(app)
    app.installTranslator(fallback_translator)

    # Retain the Python wrappers for the whole application lifetime.
    app._russian_ui_translators = (qt_translator, fallback_translator)  # type: ignore[attr-defined]
