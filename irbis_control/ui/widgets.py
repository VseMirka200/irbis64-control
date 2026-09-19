from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
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
from irbis_control.paths import resource_path


# Оставляет выбор базы и обновление списка доступными в одном поле.
class DatabaseComboBox(QComboBox):
    """A database field with adjacent dropdown and refresh icons on the right."""

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
        self.refresh_action = QAction(QIcon(resource_path("assets", "refresh.svg")), "Обновить список баз", self)
        self.refresh_button = QToolButton(self)
        self.refresh_button.setDefaultAction(self.refresh_action)
        for button in (self.dropdown_button, self.refresh_button):
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            button.setStyleSheet("QToolButton { border: none; padding: 0; background: transparent; }")

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
    """A tab container that does not inherit the widest page as its minimum."""

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
    """Expose the current layout hint to a resizable scroll area."""

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
        horizontal = 3 if very_compact else 4
        vertical = 3 if compact else 4
        self.outer_layout.setContentsMargins(horizontal, vertical, horizontal, vertical)
        self.outer_layout.setSpacing(4)
        self.body.setSpacing(4)


# Позволяет выбрать несколько полей правила без закрытия списка после каждого щелчка.
class MatchFieldsComboBox(QComboBox):
    """Select several rule fields while keeping the dropdown open."""

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
    def __init__(self, target: QListWidget) -> None:
        super().__init__()
        self.target = target
        self._drag_y: float | None = None
        self._start_height = target.height()
        self.setFixedHeight(10)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Изменить высоту списка правил")
        self.setToolTip(
            "Потяните вверх или вниз, чтобы изменить высоту списка. Также можно использовать стрелки ↑ и ↓."
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        grip = QFrame()
        grip.setFixedSize(32, 3)
        grip.setStyleSheet("background: #aebdcd; border-radius: 1px;")
        grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignCenter)

    def _set_height(self, height: int) -> None:
        self.target.setFixedHeight(max(76, min(600, height)))

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
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b52f2f;")
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
