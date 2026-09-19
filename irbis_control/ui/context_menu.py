from __future__ import annotations

"""Единые контекстные меню приложения.

Все меню по ПКМ для текстовых контролов и таблиц создаются здесь.  Это
предотвращает расхождение подписей/горячих клавиш между окнами и обходит
ошибку Qt/Windows, при которой QMenu с QSS иногда получает слишком маленькую
ширину и обрезает подписи действий до последних букв сочетаний клавиш.
"""

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtGui import QAction, QContextMenuEvent, QKeySequence
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
    """QMenu с общей ролью темы и гарантированной шириной действий."""

    MINIMUM_WIDTH = 176
    # Запас включает левую область пункта, промежуток между подписью и
    # shortcut, правый padding и рамку.  QMenu.sizeHint() под Windows не всегда
    # учитывает эти столбцы после применения QSS.
    HORIZONTAL_CHROME = 54
    SHORTCUT_GAP = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appContextMenu")
        self.aboutToShow.connect(self.ensure_action_width)

    @staticmethod
    def _clean_action_text(text: str) -> str:
        # '&' используется QMenu как mnemonic и не должен увеличивать расчёт.
        return text.replace("&&", "\0").replace("&", "").replace("\0", "&")

    def ensure_action_width(self) -> None:
        """Не даёт Qt обрезать подпись действия или отображаемый shortcut."""
        metrics = self.fontMetrics()
        label_width = 0
        shortcut_width = 0
        has_shortcut = False
        for action in self.actions():
            if action.isSeparator():
                continue
            label_width = max(
                label_width,
                metrics.horizontalAdvance(self._clean_action_text(action.text())),
            )
            shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if shortcut:
                has_shortcut = True
                shortcut_width = max(shortcut_width, metrics.horizontalAdvance(shortcut))

        required = label_width + self.HORIZONTAL_CHROME
        if has_shortcut:
            required += self.SHORTCUT_GAP + shortcut_width
        self.setMinimumWidth(max(self.MINIMUM_WIDTH, required))

    def add_app_action(
        self,
        text: str,
        callback: Callable[[], None],
        *,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
        enabled: bool = True,
    ) -> QAction:
        action = self.addAction(text)
        action.triggered.connect(callback)
        action.setEnabled(enabled)
        if shortcut is not None:
            sequence = shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut)
            action.setShortcut(sequence)
            # На некоторых платформах shortcut контекстного действия скрыт по
            # умолчанию. Здесь он является частью UI и всегда должен быть виден.
            try:
                action.setShortcutVisibleInContextMenu(True)
            except AttributeError:
                pass
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
        shortcut=QKeySequence.StandardKey.Undo,
        enabled=not read_only and widget.isUndoAvailable(),
    )
    menu.add_app_action(
        REDO_TEXT,
        widget.redo,
        shortcut=QKeySequence.StandardKey.Redo,
        enabled=not read_only and widget.isRedoAvailable(),
    )
    menu.addSeparator()
    menu.add_app_action(
        CUT_TEXT,
        widget.cut,
        shortcut=QKeySequence.StandardKey.Cut,
        enabled=not read_only and selected,
    )
    menu.add_app_action(
        COPY_TEXT,
        widget.copy,
        shortcut=QKeySequence.StandardKey.Copy,
        enabled=selected,
    )
    menu.add_app_action(
        PASTE_TEXT,
        widget.paste,
        shortcut=QKeySequence.StandardKey.Paste,
        enabled=not read_only and clipboard_has_text,
    )

    def delete_selection() -> None:
        if widget.hasSelectedText() and not widget.isReadOnly():
            widget.insert("")

    menu.add_app_action(
        DELETE_TEXT,
        delete_selection,
        shortcut=QKeySequence(Qt.Key.Key_Delete),
        enabled=not read_only and selected,
    )
    menu.addSeparator()
    menu.add_app_action(
        SELECT_ALL_TEXT,
        widget.selectAll,
        shortcut=QKeySequence.StandardKey.SelectAll,
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
        shortcut=QKeySequence.StandardKey.Undo,
        enabled=not read_only and document.isUndoAvailable(),
    )
    menu.add_app_action(
        REDO_TEXT,
        widget.redo,
        shortcut=QKeySequence.StandardKey.Redo,
        enabled=not read_only and document.isRedoAvailable(),
    )
    menu.addSeparator()
    menu.add_app_action(
        CUT_TEXT,
        widget.cut,
        shortcut=QKeySequence.StandardKey.Cut,
        enabled=not read_only and selected,
    )
    menu.add_app_action(
        COPY_TEXT,
        widget.copy,
        shortcut=QKeySequence.StandardKey.Copy,
        enabled=selected,
    )
    menu.add_app_action(
        PASTE_TEXT,
        widget.paste,
        shortcut=QKeySequence.StandardKey.Paste,
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
        shortcut=QKeySequence(Qt.Key.Key_Delete),
        enabled=not read_only and selected,
    )
    menu.addSeparator()
    menu.add_app_action(
        SELECT_ALL_TEXT,
        widget.selectAll,
        shortcut=QKeySequence.StandardKey.SelectAll,
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
        shortcut=QKeySequence.StandardKey.Copy,
        enabled=has_selection,
    )
    menu.add_app_action(
        SELECT_ALL_TEXT,
        select_all,
        shortcut=QKeySequence.StandardKey.SelectAll,
        enabled=bool(widget.text()),
    )
    return menu


def _label_supports_context_menu(widget: QLabel) -> bool:
    flags = widget.textInteractionFlags()
    selectable = (
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
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
        if isinstance(current, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return current
        if isinstance(current, QLabel) and _label_supports_context_menu(current):
            return current
        current = current.parent()
    return None


class ContextMenuManager(QObject):
    """Глобально направляет стандартные ПКМ-меню в одну фабрику."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
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
    setattr(app, "_irbis_context_menu_manager", manager)
    return manager
