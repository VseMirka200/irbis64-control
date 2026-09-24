"""Карточки ручной проверки, сгруппированные по записи ИРБИС."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from irbis_control.core.models import MatchResult
from irbis_control.core.review_groups import review_record_group_key
from irbis_control.paths import icon_path
from irbis_control.ui.components.widgets import AppComboBox
from irbis_control.ui.theme import manual_review_stylesheet


class ManualMatchReviewDialog(QDialog):
    def __init__(self, rows: list[tuple[int, MatchResult]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("manualReviewDialog")
        self.setWindowTitle("Ручная проверка подозрительных совпадений")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.resize(1500, 940)
        self.setMinimumSize(1000, 620)
        self.setStyleSheet(manual_review_stylesheet())
        self._rows = dict(rows)
        self._decisions = dict.fromkeys(self._rows)
        self._record_groups: dict[str, list[int]] = {}
        for index, result in rows:
            key = review_record_group_key(result)
            self._record_groups.setdefault(key, []).append(index)
        self._group_numbers = {key: n for n, key in enumerate(self._record_groups, 1)}
        self._filter = "all"
        self._active_key = None
        self._active_index = None
        self._collapsed = False
        self._details = set()
        self._detail_animations = {}
        self._action_buttons = {}
        self._cards = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)
        heading = QHBoxLayout()
        heading.setSpacing(22)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = self._label("Ручная проверка совпадений", "reviewTitle")
        title_label.setWordWrap(False)
        title_row.addWidget(title_label)
        title_row.addStretch()
        titles.addLayout(title_row)
        titles.addWidget(
            self._label("Проверьте найденные варианты и укажите, какие записи являются одинаковыми.", "cardDescription")
        )
        heading.addLayout(titles, 3)

        progress = QVBoxLayout()
        progress.setSpacing(7)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("reviewProgressLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("reviewProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        progress.addWidget(self.progress_label)
        progress.addWidget(self.progress_bar)
        heading.addLayout(progress, 2)

        root.addLayout(heading)
        toolbar = QHBoxLayout()
        self.filter_buttons = {}
        for key, caption in (
            ("all", "Все"),
            ("pending", "Нерешённые"),
            ("approved", "Подтверждённые"),
            ("rejected", "Отклонённые"),
        ):
            button = self._button(caption, "reviewFilter", lambda _=False, k=key: self._set_filter(k))
            button.setCheckable(True)
            self.filter_buttons[key] = button
            toolbar.addWidget(button)
        toolbar.addSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, автору, ISBN, MFN…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_list)
        toolbar.addWidget(self.search, 1)
        self.sort_combo = AppComboBox()
        self.sort_combo.setProperty("showCurrentInPopup", True)
        self.sort_combo.addItems(["По группам", "По названию", "По MFN"])
        self.sort_combo.setToolTip("Сортировка групп")
        self.sort_combo.currentIndexChanged.connect(self._refresh_list)
        toolbar.addWidget(self.sort_combo)
        root.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.group_list = QListWidget()
        self.group_list.setObjectName("reviewGroupList")
        self.group_list.setMinimumWidth(280)
        self.group_list.setWordWrap(True)
        self.group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_list.installEventFilter(self)
        self.group_list.currentItemChanged.connect(self._select_group)
        splitter.addWidget(self.group_list)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        splitter.addWidget(self.scroll)
        splitter.setSizes([340, 1160])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        footer = QHBoxLayout()
        footer.addWidget(self._button("Убрать все нерешённые", "dangerButton", self._reject_unresolved))
        self.footer_label = self._label("", "cardDescription")
        footer.addWidget(self.footer_label, 1)
        footer.addWidget(self._button("Отменить запуск", "mutedButton", self.reject))
        self.continue_button = self._button("Сохранить и продолжить  →", "primaryButton", self.accept)
        footer.addWidget(self.continue_button)
        root.addLayout(footer)
        self._refresh_progress()

    @staticmethod
    def _label(text, name=""):
        label = QLabel(str(text) if text else "—")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _button(text, name, callback):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _display_mfn(record) -> int | str:
        if record is None:
            return "—"
        return record.source_record_number or record.record_number

    @staticmethod
    def _clean_irbis_value(value) -> str:
        cleaned = re.sub(r"\^[0-9A-Za-zА-Яа-я@]", " ", str(value or ""))
        return " ".join(cleaned.split()).strip(" ,;")

    @classmethod
    def _join_irbis_values(cls, values) -> str:
        cleaned = [cls._clean_irbis_value(value) for value in values]
        return ", ".join(value for value in cleaned if value) or "—"

    @classmethod
    def _irbis_display_fields(cls, record, source_type: str) -> list[tuple[str, str]]:
        if record is None:
            return []
        return [
            ("Название", cls._join_irbis_values(record.titles)),
            ("Источник", cls._clean_irbis_value(source_type) or "—"),
            ("Авторы", cls._join_irbis_values(record.authors)),
            ("ISBN", cls._join_irbis_values(record.isbns)),
            ("Инвентарный номер книги", cls._join_irbis_values(record.inventory_numbers)),
        ]

    def _state(self, key):
        values = [self._decisions[i] for i in self._record_groups[key]]
        if any(value is None for value in values):
            return "pending"
        return "approved" if any(values) else "rejected"

    def _set_filter(self, key):
        self._filter = key
        self._refresh_list()

    def _search_text(self, index):
        result = self._rows[index]
        record = result.database
        return " ".join(
            (
                result.excel.title,
                result.excel.author,
                result.excel.isbn,
                result.matched_value,
                result.foreign_agent.name if result.foreign_agent else "",
                " ".join(record.titles + record.authors + record.isbns) if record else "",
                str(self._display_mfn(record)) if record else "",
            )
        ).casefold()

    def _refresh_list(self, *_):
        query = self.search.text().strip().casefold()
        keys = [
            key
            for key, members in self._record_groups.items()
            if (self._filter == "all" or self._state(key) == self._filter)
            and (not query or any(query in self._search_text(i) for i in members))
        ]

        def title(key):
            result = self._rows[self._record_groups[key][0]]
            return result.database.main_title if result.database else result.excel.title

        if self.sort_combo.currentIndex() == 1:
            keys.sort(key=lambda k: title(k).casefold())
        elif self.sort_combo.currentIndex() == 2:
            keys.sort(
                key=lambda k: (
                    self._display_mfn(self._rows[self._record_groups[k][0]].database)
                    if self._rows[self._record_groups[k][0]].database
                    else 0
                )
            )
        self.group_list.blockSignals(True)
        self.group_list.clear()
        selected = 0
        captions = {"pending": "Нерешено", "approved": "✓ Подтверждено", "rejected": "✕ Отклонено"}
        for position, key in enumerate(keys):
            result = self._rows[self._record_groups[key][0]]
            mfn = self._display_mfn(result.database)
            group_title = title(key) or "Без названия"
            compact_title = group_title if len(group_title) <= 45 else group_title[:44] + "…"
            item = QListWidgetItem(
                f"Группа {self._group_numbers[key]}\n"
                f"Вариантов: {len(self._record_groups[key])} · {captions[self._state(key)]}\n"
                f"{compact_title}\nИсточник: {result.source_type} · MFN: {mfn}"
            )
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(f"{group_title}\n{item.text()}")
            item.setSizeHint(QSize(0, self.group_list.fontMetrics().lineSpacing() * 6 + 20))
            self.group_list.addItem(item)
            if key == self._active_key:
                selected = position
        self.group_list.setCurrentRow(selected if keys else -1)
        self.group_list.blockSignals(False)
        for key, button in self.filter_buttons.items():
            button.setChecked(key == self._filter)
        self._select_group(self.group_list.currentItem())

    def _select_group(self, item, *_):
        self._active_key = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._render_group()

    def _render_group(self):
        for animation in getattr(self, "_detail_animations", {}).values():
            animation.stop()
        previous = self.scroll.takeWidget()
        if previous:
            previous.deleteLater()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 0, 4, 4)
        layout.setSpacing(8)
        self._cards = {}
        self._action_buttons = {}
        self._detail_panels = {}
        self._detail_buttons = {}
        self._detail_animations = {}
        self._match_labels = {}
        self.scroll.setWidget(page)
        if self._active_key is None:
            layout.addWidget(
                self._label("Нет групп для отображения. Измените фильтр или поисковый запрос.", "cardDescription")
            )
            layout.addStretch()
            self.footer_label.setText("Групп не найдено")
            return
        members = self._record_groups[self._active_key]
        if self._active_index not in members:
            self._active_index = next((i for i in members if self._decisions[i] is None), members[0])
        number = self._group_numbers[self._active_key]
        heading = QHBoxLayout()
        group_titles = QVBoxLayout()
        group_titles.setSpacing(2)
        group_titles.addWidget(self._label(f"Группа {number} из {len(self._record_groups)}", "reviewHeading"))
        group_titles.addWidget(self._label(f"Найдено возможных совпадений: {len(members)}", "cardDescription"))
        heading.addLayout(group_titles, 1)
        self._apply_group_button = self._button(
            "Подтвердить всю группу",
            "groupActionButton",
            lambda _=False: self._set_active_group_decision(True),
        )
        self._apply_group_button.setToolTip("Подтвердить все варианты текущей группы")
        heading.addWidget(self._apply_group_button)
        self._reject_group_button = self._button(
            "Убрать всю группу",
            "groupRejectButton",
            lambda _=False: self._set_active_group_decision(False),
        )
        self._reject_group_button.setToolTip("Отклонить все варианты текущей группы")
        heading.addWidget(self._reject_group_button)
        self._collapse_button = self._button(
            "Развернуть всё" if self._collapsed else "Свернуть всё", "mutedButton", self._toggle_details
        )
        heading.addWidget(self._collapse_button)
        layout.addLayout(heading)
        first = self._rows[members[0]]
        record = first.database
        source = QFrame()
        source.setObjectName("reviewSource")
        source_layout = QVBoxLayout(source)
        source_layout.setContentsMargins(10, 8, 10, 8)
        source_layout.setSpacing(5)
        source_heading = QHBoxLayout()
        source_heading.addWidget(self._label("Исходная запись (для сравнения)", "reviewAccent"), 1)
        source_layout.addLayout(source_heading)
        irbis_grid = QGridLayout()
        irbis_grid.setHorizontalSpacing(8)
        irbis_grid.setVerticalSpacing(8)
        for column, (caption, value) in enumerate(self._irbis_display_fields(record, first.source_type)):
            irbis_grid.addWidget(self._label(caption, "reviewIrbisCaption"), 0, column)
            irbis_grid.addWidget(self._label(value, "reviewIrbisValue"), 1, column)
            irbis_grid.setColumnStretch(column, 3 if column == 0 else 1)
        source_layout.addLayout(irbis_grid)
        layout.addWidget(source)
        layout.addWidget(self._label("Варианты совпадений", "reviewHeading"))
        for position, index in enumerate(members, 1):
            layout.addWidget(self._build_candidate_card(index, position))
        self._refresh_group_actions()
        layout.addStretch()
        self.footer_label.setText(
            f"Текущая группа: {number} из {len(self._record_groups)}   |   Вариантов в группе: {len(members)}"
        )

    def _build_candidate_card(self, index: int, position: int) -> QFrame:
        """Создаёт одну карточку варианта и регистрирует её интерактивные элементы."""
        result = self._rows[index]
        excel = result.excel
        foreign = result.foreign_agent
        card = QFrame()
        card.setObjectName("reviewCandidate")
        card.setProperty("active", index == self._active_index)
        # Если вариант один, область прокрутки не должна растягивать его
        # на всю доступную высоту. При раскрытии подробностей sizeHint
        # увеличится, и карточка по-прежнему раскроется вниз.
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_root = QGridLayout(card)
        card_root.setContentsMargins(10, 8, 10, 8)
        card_root.setHorizontalSpacing(10)
        card_root.setVerticalSpacing(5)
        card_layout = QHBoxLayout()
        card_layout.setSpacing(10)
        number_button = self._button(str(position), "reviewNumber", lambda _=False, i=index: self._activate(i))
        number_button.setFixedSize(28, 28)
        number_button.setToolTip("Выбрать вариант для Enter / Delete")
        card_root.addWidget(number_button, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        card_root.addLayout(card_layout, 0, 1)
        card_root.setColumnStretch(1, 1)
        info = QVBoxLayout()
        info.setSpacing(7)
        info.addWidget(
            self._label(
                excel.title or (foreign.name if foreign else excel.author) or result.matched_value,
                "reviewCandidateTitle",
            )
        )

        meta = QGridLayout()
        meta.setHorizontalSpacing(24)
        meta.setVerticalSpacing(2)
        meta_values = [
            ("Источник", result.source_type or "—"),
            ("Автор", excel.author or "—"),
            (
                "Организация ИРБИС",
                " | ".join(result.database.organizations) if result.database and result.database.organizations else "—",
            ),
            ("ISBN", excel.isbn or "—"),
        ]
        for meta_col, (caption, value) in enumerate(meta_values):
            meta.addWidget(self._label(caption, "reviewMetaCaption"), 0, meta_col)
            meta.addWidget(self._label(value, "reviewMetaValue"), 1, meta_col)
            meta.setColumnStretch(meta_col, 1)
        meta.setColumnStretch(0, 2)
        info.addLayout(meta)

        matched = self._label(f"✓  Совпадающее значение\n{result.matched_value or '—'}", "reviewMatch")
        info.addWidget(matched)
        matched.setVisible(not self._collapsed)
        self._match_labels[index] = matched
        detail_button = self._button(
            "Скрыть подробности" if index in self._details else "Подробности",
            "linkButton",
            lambda _=False, i=index: self._toggle_candidate_details(i),
        )
        # Одинаковая минимальная ширина для обеих подписей исключает
        # перераспределение ширины колонок при раскрытии.
        detail_button.setMinimumWidth(detail_button.fontMetrics().horizontalAdvance("Скрыть подробности") + 8)
        info.addWidget(detail_button)
        detail_button.setVisible(not self._collapsed)
        self._detail_buttons[index] = detail_button
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 5, 0, 0)
        details_layout.setSpacing(5)
        details_layout.addWidget(
            self._label(
                f"{result.method} · {result.confidence:g}%\n{result.note or 'Требуется проверка оператора'}",
                "cardDescription",
            )
        )
        if foreign:
            details_layout.addWidget(
                self._label(f"{foreign.name} · {foreign.agent_type} · № {foreign.registry_number}")
            )
        if excel.publisher or excel.year:
            details_layout.addWidget(self._label(f"{excel.publisher} {excel.year}"))
        if excel.raw_data:
            raw_button = self._button(
                "Копировать исходные данные",
                "linkButton",
                lambda _=False, data=excel.raw_data: QApplication.clipboard().setText(str(data)),
            )
            details_layout.addWidget(raw_button)
        card_root.addWidget(details, 1, 1)
        details.setVisible(not self._collapsed and index in self._details)
        self._detail_panels[index] = details
        card_layout.addLayout(info, 1)
        actions = QVBoxLayout()
        actions.setSpacing(4)
        pair = QHBoxLayout()
        decision = self._decisions[index]
        approve = self._button(
            "✓  Подтверждено" if decision is True else "✓  Подтвердить",
            "reviewDecisionApprove",
            lambda _=False, i=index: self._set_decision(i, True),
        )
        reject = self._button(
            "✕  Убрано" if decision is False else "✕  Убрать",
            "reviewDecisionReject",
            lambda _=False, i=index: self._set_decision(i, False),
        )
        approve.setProperty("selected", decision is True)
        reject.setProperty("selected", decision is False)
        approve.setMinimumWidth(118)
        reject.setMinimumWidth(118)
        pair.addWidget(approve, 1)
        pair.addWidget(reject, 1)
        actions.addLayout(pair)
        source_button = self._button(
            "Показать строку в Excel", "mutedButton", lambda _=False, i=index: self._open_source(i)
        )
        source_button.setToolTip(f"{excel.source_file}\nЛист: {excel.sheet_name} · Строка: {excel.row_number}")
        source_button.setAccessibleName(
            f"Открыть в Excel: {Path(excel.source_file).name}, {excel.sheet_name}, строка {excel.row_number}"
        )
        actions.addWidget(source_button)
        actions.addStretch()
        card_layout.addLayout(actions)
        self._cards[index] = card
        self._action_buttons[index] = (approve, reject)
        return card

    def _open_source(self, index):
        from irbis_control.ui.components.dialogs import _open_excel_at_source

        excel = self._rows[index].excel
        _open_excel_at_source(excel.source_file, excel.sheet_name, excel.row_number, self)

    def _toggle_details(self):
        self._collapsed = not self._collapsed
        self._collapse_button.setText("Развернуть всё" if self._collapsed else "Свернуть всё")
        for index in self._cards:
            animation = self._detail_animations.pop(index, None)
            if animation is not None:
                animation.stop()
            self._match_labels[index].setVisible(not self._collapsed)
            self._detail_buttons[index].setVisible(not self._collapsed)
            panel = self._detail_panels[index]
            panel.setMaximumHeight(16_777_215)
            panel.setVisible(not self._collapsed and index in self._details)

    def _toggle_candidate_details(self, index):
        expanded = index not in self._details
        if not expanded:
            self._details.remove(index)
        else:
            self._details.add(index)
        panel = self._detail_panels.get(index)
        if panel is not None:
            self._detail_buttons[index].setText("Скрыть подробности" if expanded else "Подробности")
            self._animate_candidate_details(index, expanded)

    def _animate_candidate_details(self, index: int, expanded: bool) -> None:
        """Плавно меняет высоту подробностей, не сдвигая текущую позицию списка."""
        panel = self._detail_panels.get(index)
        if panel is None or self._collapsed:
            return

        previous = self._detail_animations.pop(index, None)
        if previous is not None:
            previous.stop()

        scrollbar = self.scroll.verticalScrollBar()
        scroll_position = scrollbar.value()
        if expanded:
            target_height = panel.sizeHint().height()
            if not panel.isVisible():
                panel.setMaximumHeight(0)
                panel.setVisible(True)
            start_height = panel.height()
        else:
            if not panel.isVisible():
                return
            start_height = panel.height()
            panel.setMaximumHeight(start_height)
            target_height = 0

        animation = QPropertyAnimation(panel, b"maximumHeight", panel)
        animation.setDuration(180)
        animation.setStartValue(start_height)
        animation.setEndValue(target_height)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(lambda _value, bar=scrollbar, value=scroll_position: bar.setValue(value))
        animation.finished.connect(
            lambda i=index, current=animation, opening=expanded: self._finish_detail_animation(i, current, opening)
        )
        self._detail_animations[index] = animation
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _finish_detail_animation(
        self,
        index: int,
        animation: QPropertyAnimation,
        expanded: bool,
    ) -> None:
        if self._detail_animations.get(index) is not animation:
            return
        self._detail_animations.pop(index, None)
        panel = self._detail_panels.get(index)
        if panel is None:
            return
        is_expanded = expanded and index in self._details and not self._collapsed
        panel.setVisible(is_expanded)
        panel.setMaximumHeight(16_777_215)

    def eventFilter(self, watched, event):
        if (
            watched is self.group_list
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Delete, Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            self.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _activate(self, index):
        self._active_index = index
        for i, card in self._cards.items():
            card.setProperty("active", i == index)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()
        if index in self._cards:
            self.scroll.ensureWidgetVisible(self._cards[index])

    def _refresh_group_actions(self):
        if not hasattr(self, "_apply_group_button"):
            return
        members = self._record_groups.get(self._active_key, [])
        count = len(members)
        self._apply_group_button.setText(f"Подтвердить всю группу ({count})")
        self._apply_group_button.setVisible(count > 1)
        self._apply_group_button.setToolTip("Подтвердить все варианты текущей группы")
        self._reject_group_button.setText(f"Убрать всю группу ({count})")
        self._reject_group_button.setVisible(count > 1)
        self._reject_group_button.setToolTip("Отклонить все варианты текущей группы")

    def _set_active_group_decision(self, approved: bool) -> None:
        """Применяет одно решение только к вариантам текущей операторской группы."""
        if self._active_key is None:
            return
        value = bool(approved)
        for index in self._record_groups.get(self._active_key, []):
            self._decisions[index] = value
        self._refresh_progress()

    def keyPressEvent(self, event):
        if isinstance(self.focusWidget(), (QLineEdit, QComboBox, QPushButton)):
            super().keyPressEvent(event)
            return
        key = event.key()
        members = self._record_groups.get(self._active_key, [])
        if self._active_index not in members:
            super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Delete):
            self._set_decision(self._active_index, key != Qt.Key.Key_Delete)
            event.accept()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            position = members.index(self._active_index)
            step = 1 if key == Qt.Key.Key_Down else -1
            self._activate(members[(position + step) % len(members)])
            event.accept()
        else:
            super().keyPressEvent(event)

    def _set_decision(self, index: int, approved: bool) -> None:
        if index not in self._decisions:
            return
        self._active_index = index
        self._decisions[index] = bool(approved)
        self._refresh_progress()

    def _reject_unresolved(self):
        for index, decision in self._decisions.items():
            if decision is None:
                self._decisions[index] = False
        self._refresh_progress()

    def _refresh_progress(self):
        counts = {
            state: sum(self._state(k) == state for k in self._record_groups)
            for state in ("pending", "approved", "rejected")
        }
        total = len(self._record_groups)
        resolved = total - counts["pending"]
        percent = round(100 * resolved / total) if total else 100
        self.progress_label.setText(f"Решено: {resolved} из {total} ({percent}%)")
        self.progress_bar.setValue(percent)
        for key, caption in (
            ("all", "Все"),
            ("pending", "Нерешённые"),
            ("approved", "Подтверждённые"),
            ("rejected", "Отклонённые"),
        ):
            self.filter_buttons[key].setText(f"{caption}  {total if key == 'all' else counts[key]}")
        self.continue_button.setEnabled(counts["pending"] == 0)
        self._refresh_list()

    def accept(self):
        if all(value is not None for value in self._decisions.values()):
            super().accept()

    def decisions(self):
        if self.result() != QDialog.DialogCode.Accepted:
            return {}
        return {i: bool(value) for i, value in self._decisions.items() if value is not None}
