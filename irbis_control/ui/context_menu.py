"""Единые контекстные меню приложения.

Все меню по ПКМ для текстовых контролов и таблиц создаются здесь. Это
предотвращает расхождение подписей между окнами и обходит ошибку Qt/Windows,
при которой QMenu с QSS иногда получает слишком маленькую ширину.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtGui import QAction, QContextMenuEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

# Тексты находятся в одном месте, чтобы любое окно использовало одинаковые
# формулировки независимо от системной локали Qt.
UNDO_TEXT = "Отменить"
REDO_TEXT = "Повторить"
CUT_TEXT = "Вырезать"
COPY_TEXT = "Копировать"
PASTE_TEXT = "Вставить"
DELETE_TEXT = "Удалить"
SELECT_ALL_TEXT = "Выделить всё"


class AppContextMenu(QMenu):
    """QMenu со скруглённым прозрачным окном и общей ролью темы."""

    MINIMUM_WIDTH = 176
    # Запас включает левую область пункта, правый padding и рамку. QMenu.sizeHint()
    # под Windows не всегда учитывает эти области после применения QSS.
    HORIZONTAL_CHROME = 54

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appContextMenu")
        # QSS умеет рисовать border-radius, но без прозрачного top-level окна
        # Windows оставляет под скруглённой рамкой квадратную системную заливку.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.aboutToShow.connect(self.ensure_action_width)

    @staticmethod
    def _clean_action_text(text: str) -> str:
        # '&' используется QMenu как mnemonic и не должен увеличивать расчёт.
        return text.replace("&&", "\0").replace("&", "").replace("\0", "&")

    def ensure_action_width(self) -> None:
        """Не даёт Qt обрезать подпись действия."""
        metrics = self.fontMetrics()
        label_width = 0
        for action in self.actions():
            if action.isSeparator():
                continue
            label_width = max(
                label_width,
                metrics.horizontalAdvance(self._clean_action_text(action.text())),
            )

        required = label_width + self.HORIZONTAL_CHROME
        self.setMinimumWidth(max(self.MINIMUM_WIDTH, required))

    def add_app_action(
        self,
        text: str,
        callback: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> QAction:
        action = self.addAction(text)
        action.triggered.connect(callback)
        action.setEnabled(enabled)
        return action


def new_context_menu(parent: QWidget | None = None) -> AppContextMenu:
    """Фабрика для нестандартных меню приложения (таблицы и т. п.)."""
    return AppContextMenu(parent)


def _line_edit_menu(widget: QLineEdit) -> AppContextMenu:
    menu = new_context_menu(widget)
    read_only = widget.isReadOnly()
    selected = widget.hasSelectedText()
    clipboard_has_text = bool(QApplication.clipboard().text())

    menu.add_app_action(
        UNDO_TEXT,
        widget.undo,
        enabled=not read_only and widget.isUndoAvailable(),
    )
    menu.add_app_action(
        REDO_TEXT,
        widget.redo,
        enabled=not read_only and widget.isRedoAvailable(),
    )
    menu.addSeparator()
    menu.add_app_action(
        CUT_TEXT,
        widget.cut,
        enabled=not read_only and selected,
    )
    menu.add_app_action(
        COPY_TEXT,
        widget.copy,
        enabled=selected,
    )
    menu.add_app_action(
        PASTE_TEXT,
        widget.paste,
        enabled=not read_only and clipboard_has_text,
    )

    def delete_selection() -> None:
        if widget.hasSelectedText() and not widget.isReadOnly():
            widget.insert("")

    menu.add_app_action(
        DELETE_TEXT,
        delete_selection,
        enabled=not read_only and selected,
    )
    menu.addSeparator()
    menu.add_app_action(
        SELECT_ALL_TEXT,
        widget.selectAll,
        enabled=bool(widget.text()),
    )
    return menu


def _text_edit_menu(widget: QTextEdit | QPlainTextEdit) -> AppContextMenu:
    menu = new_context_menu(widget)
    read_only = widget.isReadOnly()
    cursor = widget.textCursor()
    selected = cursor.hasSelection()
    clipboard_has_text = bool(QApplication.clipboard().text())
    document = widget.document()

    menu.add_app_action(
        UNDO_TEXT,
        widget.undo,
        enabled=not read_only and document.isUndoAvailable(),
    )
    menu.add_app_action(
        REDO_TEXT,
        widget.redo,
        enabled=not read_only and document.isRedoAvailable(),
    )
    menu.addSeparator()
    menu.add_app_action(
        CUT_TEXT,
        widget.cut,
        enabled=not read_only and selected,
    )
    menu.add_app_action(
        COPY_TEXT,
        widget.copy,
        enabled=selected,
    )
    menu.add_app_action(
        PASTE_TEXT,
        widget.paste,
        enabled=not read_only and clipboard_has_text,
    )

    def delete_selection() -> None:
        if widget.isReadOnly():
            return
        local_cursor = widget.textCursor()
        if local_cursor.hasSelection():
            local_cursor.removeSelectedText()
            widget.setTextCursor(local_cursor)

    menu.add_app_action(
        DELETE_TEXT,
        delete_selection,
        enabled=not read_only and selected,
    )
    menu.addSeparator()
    menu.add_app_action(
        SELECT_ALL_TEXT,
        widget.selectAll,
        enabled=bool(widget.toPlainText()),
    )
    return menu


def _selectable_label_menu(widget: QLabel) -> AppContextMenu:
    menu = new_context_menu(widget)
    has_selection = widget.hasSelectedText()

    def copy_selection() -> None:
        text = widget.selectedText()
        if text:
            QApplication.clipboard().setText(text)

    def select_all() -> None:
        # QLabel использует позиции в отображаемом тексте. Для обычных
        # информационных сообщений это совпадает с len(text()).
        text = widget.text()
        if text:
            widget.setSelection(0, len(text))

    menu.add_app_action(
        COPY_TEXT,
        copy_selection,
        enabled=has_selection,
    )
    menu.add_app_action(
        SELECT_ALL_TEXT,
        select_all,
        enabled=bool(widget.text()),
    )
    return menu


def _label_supports_context_menu(widget: QLabel) -> bool:
    flags = widget.textInteractionFlags()
    selectable = Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
    return bool(flags & selectable)


def context_menu_for_widget(widget: QWidget) -> AppContextMenu | None:
    """Возвращает единое меню для поддерживаемого текстового виджета."""
    if isinstance(widget, QLineEdit):
        return _line_edit_menu(widget)
    if isinstance(widget, (QTextEdit, QPlainTextEdit)):
        return _text_edit_menu(widget)
    if isinstance(widget, QLabel) and _label_supports_context_menu(widget):
        return _selectable_label_menu(widget)
    return None


def _context_owner(obj: QObject) -> QWidget | None:
    """Находит контрол, даже если событие пришло в viewport QTextEdit."""
    current: QObject | None = obj
    for _ in range(4):
        if current is None:
            break
        if isinstance(current, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return current
        if isinstance(current, QLabel) and _label_supports_context_menu(current):
            return current
        try:
            current = current.parent()
        except RuntimeError:
            # Qt мог удалить объект между доставкой события и обработчиком.
            break
    return None


class ContextMenuManager(QObject):
    """Глобально направляет стандартные ПКМ-меню в одну фабрику."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ContextMenu:
            return super().eventFilter(watched, event)
        owner = _context_owner(watched)
        if owner is None:
            return super().eventFilter(watched, event)

        menu = context_menu_for_widget(owner)
        if menu is None:
            return super().eventFilter(watched, event)

        if isinstance(event, QContextMenuEvent):
            global_pos = event.globalPos()
        else:
            global_pos = owner.mapToGlobal(QPoint(0, owner.height()))
        menu.ensure_action_width()
        menu.exec(global_pos)
        event.accept()
        return True


def install_context_menu_manager(app: QApplication) -> ContextMenuManager:
    """Устанавливает единый обработчик ПКМ один раз на всё приложение."""
    existing = getattr(app, "_irbis_context_menu_manager", None)
    if isinstance(existing, ContextMenuManager):
        return existing
    manager = ContextMenuManager(app)
    app.installEventFilter(manager)
    # Явная ссылка полезна и для тестов, и для биндингов Qt с агрессивным GC.
    app._irbis_context_menu_manager = manager
    return manager
