from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from irbis_control.core.matcher import (
    EXTRA_MATCH_RULES,
    MATCH_FIELDS,
    MATCH_RULE_LABELS,
    match_rule_needs_review,
    parse_match_rule,
)
from irbis_control.paths import icon_path


class FileDropListWidget(QListWidget):
    """Список файлов, принимающий локальные файлы перетаскиванием из Проводника."""

    filesDropped = pyqtSignal(list)

    def __init__(
        self,
        *,
        extensions: tuple[str, ...] = (),
        allow_multiple: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._drop_extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
        self._allow_multiple = allow_multiple
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def _dropped_paths(self, mime_data) -> list[str]:
        if not mime_data or not mime_data.hasUrls():
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if not path.is_file():
                continue
            if self._drop_extensions and path.suffix.lower() not in self._drop_extensions:
                continue
            value = str(path)
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(value)
            if not self._allow_multiple:
                break
        return paths

    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.filesDropped.emit(paths)


# Оставляет выбор базы и обновление списка доступными в одном поле.
class DatabaseComboBox(QComboBox):
    """Поле базы данных с кнопками выбора и обновления справа."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("databaseCombo")
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setTextMargins(0, 0, 44, 0)
        self.dropdown_button = QToolButton(self)
        self.dropdown_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.dropdown_button.setToolTip("Выбрать базу")
        self.dropdown_button.setAccessibleName("Выбрать базу")
        self.dropdown_button.clicked.connect(self.showPopup)
        self.refresh_action = QAction(QIcon(icon_path("refresh.svg")), "Обновить список баз", self)
        self.refresh_button = QToolButton(self)
        self.refresh_button.setDefaultAction(self.refresh_action)
        for button in (self.dropdown_button, self.refresh_button):
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            button.setObjectName("embeddedToolButton")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        icon_width = 20
        right = self.width() - 3
        height = max(0, self.height() - 4)
        self.refresh_button.setGeometry(right - icon_width, 2, icon_width, height)
        self.dropdown_button.setGeometry(right - 2 * icon_width, 2, icon_width, height)
        self.dropdown_button.raise_()
        self.refresh_button.raise_()


# Согласует размер окна с текущей вкладкой, а не с самой большой страницей.
class CompactTabWidget(QTabWidget):
    """Контейнер вкладок, минимальная ширина которого не зависит от самой широкой страницы."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._current_page_changed)

    def minimumSizeHint(self) -> QSize:
        return QSize(320, 240)

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is None:
            return super().sizeHint()
        page_hint = current.sizeHint()
        tab_height = self.tabBar().sizeHint().height()
        return QSize(max(320, page_hint.width()), max(240, page_hint.height() + tab_height + 2))

    def _current_page_changed(self, _index: int) -> None:
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()


# Передаёт размеры макета прокручиваемой странице без лишнего запаса.
class LayoutHintWidget(QWidget):
    """Передаёт текущий рекомендуемый размер макета изменяемой области прокрутки."""

    def sizeHint(self) -> QSize:
        layout = self.layout()
        return layout.sizeHint() if layout is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        layout = self.layout()
        return layout.minimumSize() if layout is not None else super().minimumSizeHint()


# Задаёт единые отступы и заголовки для блоков настроек.
class SectionCard(QFrame):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("sectionCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(4, 4, 4, 4)
        self.outer_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title_row = title_row

        # В компактном стандартном интерфейсе декоративные иконки карточек
        # не показываем: остаётся обычный заголовок секции и системные контролы.
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        title_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.outer_layout.addLayout(title_row)

        self.description_label: QLabel | None = None
        if description:
            self.description_label = QLabel(description)
            self.description_label.setObjectName("cardDescription")
            self.description_label.setWordWrap(True)
            self.description_label.hide()
            self.outer_layout.addWidget(self.description_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 1, 0, 0)
        self.body.setSpacing(4)
        self.outer_layout.addLayout(self.body)

    def set_compact(self, compact: bool, very_compact: bool = False) -> None:
        self.outer_layout.setContentsMargins(4, 4, 4, 4)
        self.outer_layout.setSpacing(4)
        self.body.setSpacing(4)


# Позволяет выбрать несколько полей правила без закрытия списка после каждого щелчка.
class MatchFieldsComboBox(QComboBox):
    """Позволяет выбрать несколько полей правила, не закрывая выпадающий список."""

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("Поля правила совпадения")
        for key, label in MATCH_FIELDS.items():
            self.addItem(label if key == "isbn" else label.capitalize(), key)
            self.model().item(self.count() - 1).setCheckable(True)
            self.setItemData(self.count() - 1, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
        self.view().viewport().installEventFilter(self)
        self.view().installEventFilter(self)
        self._update_summary()

    def rule_text(self) -> str:
        return " + ".join(
            MATCH_FIELDS[self.itemData(index)]
            for index in range(self.count())
            if self.itemData(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        )

    def reset_fields(self) -> None:
        for index in range(self.count()):
            self.setItemData(index, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
        self._update_summary()

    def _toggle_field(self, index: int) -> None:
        checked = self.itemData(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        self.setItemData(index, state.value, Qt.ItemDataRole.CheckStateRole)
        self._update_summary()

    def _update_summary(self) -> None:
        self.setToolTip(self.rule_text() or "Отметьте нужные поля, затем нажмите «Добавить».")
        self.setAccessibleDescription(self.toolTip())
        self.update()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                index = self.view().indexAt(event.position().toPoint())
                if index.isValid():
                    self._toggle_field(index.row())
                    return True
        if watched is self.view() and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                index = self.view().currentIndex()
                if index.isValid():
                    self._toggle_field(index.row())
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.hidePopup()
                return True
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        option.currentText = self.fontMetrics().elidedText(
            self.rule_text() or "Выберите поля…",
            Qt.TextElideMode.ElideRight,
            max(0, field.width() - 4),
        )
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


# Позволяет менять высоту списка мышью и клавиатурой.
class ListResizeHandle(QFrame):
    heightChanged = pyqtSignal(int)

    def __init__(
        self,
        target: QListWidget,
        *,
        minimum_height: int = 76,
        maximum_height: int = 600,
        accessible_name: str = "Изменить высоту списка правил",
    ) -> None:
        super().__init__()
        self.target = target
        self.minimum_height = minimum_height
        self.maximum_height = maximum_height
        self._drag_y: float | None = None
        self._start_height = target.height()
        self.setFixedHeight(10)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(accessible_name)
        self.setToolTip(
            "Потяните вверх или вниз, чтобы изменить высоту списка. Также можно использовать стрелки ↑ и ↓."
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        grip = QFrame()
        grip.setObjectName("listResizeGrip")
        grip.setFixedSize(32, 3)
        grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignCenter)

    def _set_height(self, height: int) -> None:
        bounded_height = max(self.minimum_height, min(self.maximum_height, height))
        self.target.setFixedHeight(bounded_height)
        self.heightChanged.emit(bounded_height)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = event.globalPosition().y()
            self._start_height = self.target.height()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_y is not None:
            self._set_height(self._start_height + round(event.globalPosition().y() - self._drag_y))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._set_height(self.target.height() + (20 if event.key() == Qt.Key.Key_Down else -20))
            event.accept()
        else:
            super().keyPressEvent(event)


# Управляет правилами сравнения и не допускает дубли и пустой набор правил.
class MatchRulesEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.fields_combo = MatchFieldsComboBox()
        row.addWidget(self.fields_combo, 1)
        add = QPushButton("Добавить")
        add.clicked.connect(self.add_rule)
        row.addWidget(add)
        root.addLayout(row)
        self.rules = QListWidget()
        self.rules.setObjectName("matchRulesList")
        self.rules.setFixedHeight(76)
        self.rules.setWordWrap(True)
        self.rules.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.rules.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.rules.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.rules)
        self.resize_handle = ListResizeHandle(self.rules)
        root.addWidget(self.resize_handle)
        self.error = QLabel()
        self.error.setObjectName("errorLabel")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)
        footer = QHBoxLayout()
        hint = QLabel(
            "Поля: ISBN, название, автор, издательство, год. Правила работают независимо: "
            "запись считается совпавшей, если выполнено хотя бы одно из них."
        )
        hint.setWordWrap(True)
        hint.setObjectName("tabIntro")
        footer.addWidget(hint, 1)
        self.remove = QPushButton("Удалить")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self.remove_rule)
        footer.addWidget(self.remove)
        root.addLayout(footer)
        self.rules.currentRowChanged.connect(lambda row: self.remove.setEnabled(row >= 0))

    def values(self) -> dict[str, bool]:
        active = {self.rules.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.rules.count())}
        return {key: key in active for key in MATCH_RULE_LABELS}

    @staticmethod
    def _rule_item(key: str) -> QListWidgetItem:
        label = MATCH_RULE_LABELS[key]
        if key in EXTRA_MATCH_RULES and match_rule_needs_review(EXTRA_MATCH_RULES[key][1]):
            label += " — ручная проверка"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(label)
        return item

    def set_values(self, settings: dict[str, str | int | bool]) -> None:
        self.rules.clear()
        for key, label in MATCH_RULE_LABELS.items():
            if settings.get(key, False):
                self.rules.addItem(self._rule_item(key))
        self.error.hide()

    def add_rule(self) -> None:
        try:
            text = self.fields_combo.rule_text()
            if not text:
                raise ValueError("Выберите поля в выпадающем списке.")
            key = parse_match_rule(text)
            if self.values()[key]:
                raise ValueError("Такое правило уже добавлено, в том числе с другим порядком полей.")
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        item = self._rule_item(key)
        self.rules.addItem(item)
        self.rules.setCurrentItem(item)
        self.rules.scrollToItem(item)
        self.fields_combo.reset_fields()
        self.error.hide()
        self.changed.emit()

    def remove_rule(self) -> None:
        if self.rules.currentRow() < 0:
            return
        if self.rules.count() == 1:
            self.error.setText("Оставьте хотя бы одно правило. Сначала добавьте новое, затем удалите старое.")
            self.error.show()
            return
        self.rules.takeItem(self.rules.currentRow())
        self.error.hide()
        self.changed.emit()
