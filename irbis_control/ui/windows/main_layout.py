from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from irbis_control.ui.components.widgets import AppComboBox, SectionCard
from irbis_control.ui.theme import main_window_stylesheet


class MainWindowLayoutMixin:
    def _compose_simplified_workflow(self) -> None:
        """Перекомпоновывает рабочие элементы в короткий сценарий из трёх шагов."""

        data_layout = self.irbis_tab.layout()
        self._take_all(data_layout)
        # Снизу достаточно компактного зазора: после сжатия списков до одной
        # строки большой внешний отступ выглядит как пустая полоса у края окна.
        data_layout.setContentsMargins(8, 8, 8, 4)
        data_layout.setSpacing(8)

        mode_card = SectionCard("Исходные данные")
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QLabel("Источник")
        mode_label.setObjectName("fieldLabel")
        self.source_mode_combo = AppComboBox()
        self.source_mode_combo.addItem("Сервер ИРБИС", "irbis")
        self.source_mode_combo.addItem("TXT-файл", "txt")
        self.source_mode_combo.setMinimumWidth(190)
        self.source_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.source_mode_combo, 1)
        self.source_useful_links_button = QPushButton("Полезные ссылки")
        self.source_useful_links_button.setObjectName("mutedButton")
        self.source_useful_links_button.setToolTip("Открыть ссылки на официальные реестры и другие полезные ресурсы")
        self.source_useful_links_button.clicked.connect(self.open_useful_links)
        mode_row.addWidget(self.source_useful_links_button)
        mode_card.body.addLayout(mode_row)
        self.source_mode_hint = QLabel()
        self.source_mode_hint.setObjectName("cardDescription")
        self.source_mode_hint.setWordWrap(True)
        mode_card.body.addWidget(self.source_mode_hint)
        # Локальный файл выбирается рядом с переключателем режима, чтобы поле
        # не оказывалось ниже видимой области на небольших экранах.
        mode_card.body.addWidget(self.database_card)
        data_layout.addWidget(mode_card)

        connection_overview = QFrame()
        self.connection_overview = connection_overview
        connection_overview.setObjectName("sectionCard")
        overview_layout = QHBoxLayout(connection_overview)
        overview_layout.setContentsMargins(4, 4, 4, 4)
        overview_layout.setSpacing(4)
        overview_text = QVBoxLayout()
        overview_text.setContentsMargins(0, 0, 0, 0)
        overview_text.setSpacing(6)
        overview_title = QLabel("Подключение к ИРБИС")
        overview_title.setObjectName("cardTitle")
        overview_text.addWidget(overview_title)
        self.connection_overview_label = QLabel("Подключение ещё не проверено")
        self.connection_overview_label.setObjectName("statusLabel")
        self.connection_overview_label.setWordWrap(True)
        overview_text.addWidget(self.connection_overview_label)
        overview_layout.addLayout(overview_text, 1)
        self.connection_settings_button = QPushButton("Настроить")
        self.connection_settings_button.setObjectName("mutedButton")
        self.connection_settings_button.setToolTip("Открыть параметры подключения в отдельном окне")
        self.connection_settings_button.clicked.connect(self.open_connection_settings)
        overview_layout.addWidget(self.connection_settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # Скрытые контролы хранят параметры операций ИРБИС.
        data_layout.addWidget(connection_overview)

        records_header = QHBoxLayout()
        records_header.setSpacing(7)
        records_title = QLabel("Реестры для проверки")
        records_title.setObjectName("pageSectionTitle")
        records_header.addWidget(records_title)
        records_header.addStretch()
        data_layout.addLayout(records_header)
        self.sources_grid = QGridLayout()
        self.sources_grid.setHorizontalSpacing(7)
        self.sources_grid.setVerticalSpacing(7)
        self.sources_grid.addWidget(self.excel_card, 0, 0)
        self.sources_grid.addWidget(self.foreign_agents_card, 0, 1)
        self.sources_grid.setColumnStretch(0, 1)
        self.sources_grid.setColumnStretch(1, 1)
        for card in (self.foreign_agents_card, self.excel_card):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        data_layout.addLayout(self.sources_grid)
        data_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.data_tab = self.irbis_tab
        self.workflow_tabs.addTab(self.data_tab, "Данные")

        self.source_mode_combo.currentIndexChanged.connect(self._source_mode_changed)
        self.direct_irbis_checkbox.toggled.connect(self._sync_source_mode)
        self._sync_source_mode(self.direct_irbis_checkbox.isChecked())

        parameters_layout = self.lists_tab.layout()
        self._take_all(parameters_layout)
        parameters_layout.setContentsMargins(8, 8, 8, 8)
        parameters_layout.setSpacing(8)

        outcome_card = SectionCard("Что получить после проверки")
        outcome_row = QHBoxLayout()
        outcome_row.setSpacing(8)
        outcome_label = QLabel("Результат")
        outcome_label.setObjectName("fieldLabel")
        self.output_mode_combo = AppComboBox()
        self.output_mode_combo.addItem("Excel-отчёт и метки", "full")
        self.output_mode_combo.addItem("Только Excel-отчёт", "report")
        self.output_mode_combo.addItem("Только добавить метки", "markers")
        self.output_mode_combo.setMinimumWidth(230)
        self.output_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outcome_row.addWidget(outcome_label)
        outcome_row.addWidget(self.output_mode_combo, 1)
        outcome_card.body.addLayout(outcome_row)
        self.output_mode_hint = QLabel()
        self.output_mode_hint.setObjectName("cardDescription")
        self.output_mode_hint.setWordWrap(True)
        outcome_card.body.addWidget(self.output_mode_hint)
        parameters_layout.addWidget(outcome_card)

        defaults_note = QLabel(
            "Используются сохранённые правила совпадения и рекомендуемые параметры отчёта. "
            "Откройте дополнительные настройки, только если их нужно изменить."
        )
        defaults_note.setObjectName("statusLabel")
        defaults_note.setWordWrap(True)
        parameters_layout.addWidget(defaults_note)

        confirmation_memory_card = SectionCard("Память решений")
        self.confirmation_memory_card = confirmation_memory_card
        # Заголовок и описание образуют единую левую колонку, относительно
        # которой обычная кнопка центрируется по всей высоте карточки.
        confirmation_memory_card.outer_layout.removeItem(confirmation_memory_card.title_row)
        confirmation_memory_grid = QGridLayout()
        confirmation_memory_grid.setContentsMargins(0, 0, 0, 0)
        confirmation_memory_grid.setHorizontalSpacing(8)
        confirmation_memory_grid.setVerticalSpacing(0)
        confirmation_memory_hint = QLabel(
            "Сохранённые ручные подтверждения и отклонения автоматически применяются к таким же совпадениям. "
            "Память хранится отдельно от программы, не удаляется при обновлении; её можно просмотреть, "
            "экспортировать и импортировать."
        )
        confirmation_memory_hint.setObjectName("cardDescription")
        confirmation_memory_hint.setWordWrap(True)
        self.confirmation_memory_hint = confirmation_memory_hint
        confirmation_memory_text = QVBoxLayout()
        confirmation_memory_text.setContentsMargins(0, 0, 0, 0)
        confirmation_memory_text.setSpacing(4)
        confirmation_memory_text.addLayout(confirmation_memory_card.title_row)
        confirmation_memory_text.addWidget(confirmation_memory_hint)
        confirmation_memory_grid.addLayout(confirmation_memory_text, 0, 0)
        confirmation_memory_grid.setColumnStretch(0, 1)
        self.confirmation_memory_button = QPushButton("Открыть память")
        self.confirmation_memory_button.setObjectName("mutedButton")
        self.confirmation_memory_button.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.confirmation_memory_button.setToolTip("Просмотреть или удалить сохранённые решения")
        self.confirmation_memory_button.clicked.connect(self.open_confirmation_memory)
        confirmation_memory_grid.addWidget(
            self.confirmation_memory_button,
            0,
            1,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )
        confirmation_memory_card.body.addLayout(confirmation_memory_grid)
        parameters_layout.addWidget(confirmation_memory_card)

        self.advanced_options_toggle = QPushButton("Открыть дополнительные настройки")
        self.advanced_options_toggle.setObjectName("mutedButton")
        self.advanced_options_toggle.setCheckable(False)
        self.advanced_options_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        parameters_layout.addWidget(self.advanced_options_toggle)

        self.advanced_settings_dialog = QDialog(self)
        self.advanced_settings_dialog.setWindowTitle("Дополнительные настройки")
        self.advanced_settings_dialog.setModal(True)
        self.advanced_settings_dialog.resize(620, 580)
        self.advanced_settings_dialog.setMinimumSize(520, 420)
        dialog_layout = QVBoxLayout(self.advanced_settings_dialog)
        dialog_layout.setContentsMargins(8, 8, 8, 8)
        dialog_layout.setSpacing(7)
        self.advanced_scroll = QScrollArea()
        self.advanced_scroll.setObjectName("mainScroll")
        self.advanced_scroll.setWidgetResizable(True)
        self.advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.advanced_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.advanced_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.advanced_options = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_options)
        advanced_layout.setContentsMargins(0, 0, 4, 0)
        advanced_layout.setSpacing(7)
        self.match_settings_card.title_label.setText("Порядок сравнения")
        self.match_rules_editor.show()
        self.fuzzy_match_check.show()
        advanced_layout.addWidget(self.match_settings_card)
        self.report_lists_card.title_label.setText("Состав Excel-отчёта")
        advanced_layout.addWidget(self.report_lists_card)
        self.compare_card.title_label.setText("Файлы результата")
        advanced_layout.addWidget(self.compare_card)
        self.marker_card.title_label.setText("Служебные метки")
        advanced_layout.addWidget(self.marker_card)
        advanced_layout.addStretch()
        self.advanced_scroll.setWidget(self.advanced_options)
        dialog_layout.addWidget(self.advanced_scroll, 1)
        self.advanced_settings_dialog.finished.connect(self._advanced_settings_closed)
        self.advanced_options_toggle.clicked.connect(self.open_advanced_settings)
        parameters_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.parameters_tab = self.lists_tab
        self.workflow_tabs.addTab(self.parameters_tab, "Параметры")
        self.output_mode_combo.currentIndexChanged.connect(self._output_mode_changed)

        results_layout = self.results_tab.layout()
        self._take_all(results_layout)
        results_layout.setContentsMargins(8, 8, 8, 8)
        results_layout.setSpacing(8)
        self._take_all(self.actions_layout)
        self.actions_layout.setContentsMargins(4, 4, 4, 4)
        self.actions_layout.setSpacing(7)
        self.actions_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.actions_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        result_title = QLabel("Проверка готова к запуску")
        result_title.setObjectName("cardTitle")
        self.actions_layout.addWidget(result_title)
        self.result_summary_label = QLabel(
            "Проверьте выбранные данные и нажмите «Запустить проверку». Результаты сохраняются автоматически."
        )
        self.result_summary_label.setObjectName("statusLabel")
        self.result_summary_label.setWordWrap(True)
        self.actions_layout.addWidget(self.result_summary_label)
        run_buttons = QHBoxLayout()
        run_buttons.setSpacing(6)
        self.start_button.setText("Запустить проверку")
        self.start_button.setObjectName("primaryButton")
        self.start_button.show()
        run_buttons.addWidget(self.start_button, 1)
        self.actions_layout.addLayout(run_buttons)
        self.actions_layout.addWidget(self.progress)
        self.actions_layout.addLayout(self.status_row)
        result_files = QGridLayout()
        self.result_files_layout = result_files
        result_files.setHorizontalSpacing(6)
        result_files.setVerticalSpacing(6)
        self.open_button.show()
        self.result_file_buttons = (
            self.open_button,
            self.open_modified_database_button,
            self.write_irbis_button,
            self.compare_reports_button,
        )
        self._reflow_result_files()
        self.actions_layout.addLayout(result_files)
        results_layout.addWidget(self.actions_card)

        self.log_toggle = QPushButton("Технический журнал")
        self.log_toggle.setObjectName("mutedButton")
        self.log_toggle.setCheckable(False)
        self.log_dialog = QDialog(self)
        self.log_dialog.setWindowTitle("Технический журнал")
        self.log_dialog.setModal(True)
        self.log_dialog.resize(700, 500)
        self.log_dialog.setMinimumSize(520, 320)
        log_dialog_layout = QVBoxLayout(self.log_dialog)
        log_dialog_layout.setContentsMargins(8, 8, 8, 8)
        log_dialog_layout.setSpacing(7)
        self.log_header.removeWidget(self.export_journal_button)
        log_dialog_layout.addWidget(self.log_card, 1)
        log_dialog_buttons = QHBoxLayout()
        log_dialog_buttons.addStretch()
        self.export_journal_button.setObjectName("primaryButton")
        log_dialog_buttons.addWidget(self.export_journal_button)
        close_log_button = QPushButton("Закрыть")
        close_log_button.setObjectName("mutedButton")
        close_log_button.clicked.connect(self.log_dialog.accept)
        log_dialog_buttons.addWidget(close_log_button)
        log_dialog_layout.addLayout(log_dialog_buttons)
        self.log_toggle.clicked.connect(self.open_log_window)

        self.maintenance_toggle = QPushButton("Обслуживание базы")
        self.maintenance_toggle.setObjectName("mutedButton")
        self.maintenance_toggle.setCheckable(False)
        self.maintenance_panel = QFrame()
        self.maintenance_panel.setObjectName("dangerCard")
        maintenance_layout = QVBoxLayout(self.maintenance_panel)
        maintenance_layout.setContentsMargins(4, 4, 4, 4)
        maintenance_hint = QLabel(
            "Если метки уже есть, повторная проверка не добавит их второй раз. "
            "Здесь можно отдельно удалить стандартные и настроенные метки программы; "
            "другие значения в тех же полях сохраняются."
        )
        maintenance_hint.setObjectName("cardDescription")
        maintenance_hint.setWordWrap(True)
        maintenance_layout.addWidget(maintenance_hint)
        self.maintenance_dialog = QDialog(self)
        self.maintenance_dialog.setWindowTitle("Обслуживание базы")
        self.maintenance_dialog.setModal(True)
        self.maintenance_dialog.resize(520, 220)
        self.maintenance_dialog.setMinimumSize(460, 180)
        maintenance_dialog_layout = QVBoxLayout(self.maintenance_dialog)
        maintenance_dialog_layout.setContentsMargins(8, 8, 8, 8)
        maintenance_dialog_layout.setSpacing(7)
        maintenance_dialog_layout.addWidget(self.maintenance_panel, 1)
        maintenance_dialog_buttons = QHBoxLayout()
        self.cleanup_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.cleanup_button.setMinimumWidth(190)
        maintenance_dialog_buttons.addWidget(self.cleanup_button)
        maintenance_dialog_buttons.addStretch()
        close_maintenance_button = QPushButton("Закрыть")
        close_maintenance_button.setObjectName("mutedButton")
        close_maintenance_button.clicked.connect(self.maintenance_dialog.accept)
        maintenance_dialog_buttons.addWidget(close_maintenance_button)
        maintenance_dialog_layout.addLayout(maintenance_dialog_buttons)
        self.maintenance_toggle.clicked.connect(self.open_maintenance_window)

        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(7)
        secondary_actions.addWidget(self.log_toggle, 1)
        secondary_actions.addWidget(self.maintenance_toggle, 1)
        results_layout.addLayout(secondary_actions)
        results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.results_tab, "Результат")

        self.application_settings_page = self.settings_page_class(self.app_settings, self)
        self.update_button = self.application_settings_page.check_updates_button
        self.application_settings_page.settings_changed.connect(self._save_application_settings)
        settings_index = self.workflow_tabs.addTab(self.application_settings_page, "Настройки")
        self.workflow_tabs.setTabVisible(settings_index, False)
        self.workflow_tabs.currentChanged.connect(self._settings_navigation_changed)
        self.workflow_tabs.currentChanged.connect(self._workflow_page_changed)

        self.root_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._workflow_page_changed(self.workflow_tabs.currentIndex())

    def open_advanced_settings(self) -> None:
        QTimer.singleShot(0, self._restore_advanced_scroll_position)
        self.advanced_settings_dialog.show()
        self.advanced_settings_dialog.raise_()
        self.advanced_settings_dialog.activateWindow()

    def _advanced_settings_closed(self, _result: int) -> None:
        self._advanced_scroll_position = self.advanced_scroll.verticalScrollBar().value()
        self._queue_window_state_autosave()

    def _restore_advanced_scroll_position(self) -> None:
        if not hasattr(self, "advanced_scroll"):
            return
        bar = self.advanced_scroll.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(int(self._advanced_scroll_position), bar.maximum())))

    def open_log_window(self) -> None:
        self.log_dialog.exec()

    def open_maintenance_window(self) -> None:
        self.maintenance_dialog.exec()

    def _workflow_page_key(self, widget=None) -> str:
        widget = self.workflow_tabs.currentWidget() if widget is None else widget
        if widget is getattr(self, "data_tab", None):
            return "data"
        if widget is getattr(self, "parameters_tab", None):
            return "parameters"
        if widget is getattr(self, "results_tab", None):
            return "results"
        if widget is getattr(self, "application_settings_page", None):
            return "settings"
        return "unknown"

    def _workflow_page_changed(self, _index: int) -> None:
        if not hasattr(self, "scroll_area"):
            return
        bar = self.scroll_area.verticalScrollBar()
        previous_key = getattr(self, "_active_workflow_page_key", None)
        if previous_key:
            self._workflow_scroll_positions[previous_key] = bar.value()
        current_key = self._workflow_page_key()
        self._active_workflow_page_key = current_key
        target = int(self._workflow_scroll_positions.get(current_key, 0))

        def restore() -> None:
            if self._workflow_page_key() != current_key:
                return
            current_bar = self.scroll_area.verticalScrollBar()
            current_bar.setValue(max(current_bar.minimum(), min(target, current_bar.maximum())))

        QTimer.singleShot(0, restore)
        QTimer.singleShot(0, self._fit_scroll_content)
        self._queue_window_state_autosave()

    def _source_mode_changed(self, _index: int) -> None:
        direct = self.source_mode_combo.currentData() == "irbis"
        if self.direct_irbis_checkbox.isChecked() != direct:
            self.direct_irbis_checkbox.setChecked(direct)
        self.source_mode_hint.setText(
            "Записи будут прочитаны с сервера и безопасно обновлены по одной MFN."
            if direct
            else "Выберите TXT-файл ниже. Исходный файл останется без изменений."
        )
        self.connection_overview.setVisible(direct)
        self.database_card.setVisible(not direct)
        self._refresh_connection_overview()
        QTimer.singleShot(0, self._fit_scroll_content)

    def _sync_source_mode(self, direct: bool) -> None:
        if not hasattr(self, "source_mode_combo"):
            return
        index = self.source_mode_combo.findData("irbis" if direct else "txt")
        if index >= 0 and index != self.source_mode_combo.currentIndex():
            self.source_mode_combo.setCurrentIndex(index)
        self._source_mode_changed(self.source_mode_combo.currentIndex())

    def _refresh_connection_overview(self) -> None:
        if not hasattr(self, "connection_overview_label"):
            return
        if not self.direct_irbis_checkbox.isChecked():
            self.connection_overview_label.setText("Подключение не требуется: выбран режим TXT-файла")
            return
        host = self.irbis_host_edit.text().strip() or "сервер не указан"
        database = self._current_irbis_database() or "база не выбрана"
        self.connection_overview_label.setText(
            f"{self._irbis_connection_status_text} · {host}:{self.irbis_port_spin.value()} · {database}"
        )

    def _output_mode_changed(self, _index: int) -> None:
        mode = str(self.output_mode_combo.currentData() or "full")
        descriptions = {
            "full": "Будет создан Excel-отчёт, а подтверждённые совпадения получат служебные метки.",
            "report": "Будет создан только Excel-отчёт. Записи ИРБИС и TXT-файлы не изменятся.",
            "markers": "Подтверждённые совпадения получат метки; Excel-отчёт создаваться не будет.",
        }
        self.output_mode_hint.setText(descriptions.get(mode, descriptions["full"]))
        self._update_report_controls(mode in {"full", "report"})
        self._update_output_target_controls(mode == "report")
        if not getattr(self, "_syncing_output_mode", False):
            self._queue_report_settings_autosave()

    def _sync_output_mode_from_settings(self) -> None:
        if not hasattr(self, "output_mode_combo"):
            return
        if bool(self.marker_settings.get("report_only")):
            mode = "report"
        elif bool(self.marker_settings.get("create_excel_report", True)):
            mode = "full"
        else:
            mode = "markers"
        self._syncing_output_mode = True
        try:
            index = self.output_mode_combo.findData(mode)
            self.output_mode_combo.setCurrentIndex(max(0, index))
            self._output_mode_changed(self.output_mode_combo.currentIndex())
        finally:
            self._syncing_output_mode = False

    @staticmethod
    def _take_all(layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _reflow_result_files(self) -> None:
        """Перестраивает видимые кнопки результата без старой скрытой панели.

        Кнопки этого блока меняют видимость при переключении TXT/ИРБИС.
        Поэтому раскладка собирается заново только в актуальном
        ``result_files_layout``. Старый ``actions_buttons_layout`` намеренно
        не используется: после упрощения интерфейса он больше не является
        частью видимого дерева и мог перетянуть кнопки из блока результата.
        """
        if not hasattr(self, "result_files_layout"):
            return
        self._take_all(self.result_files_layout)
        for column in range(2):
            self.result_files_layout.setColumnStretch(column, 0)

        visible_buttons = [button for button in getattr(self, "result_file_buttons", ()) if not button.isHidden()]
        columns = 2 if len(visible_buttons) > 1 else 1
        for index, button in enumerate(visible_buttons):
            self.result_files_layout.addWidget(button, index // columns, index % columns)
        for column in range(columns if visible_buttons else 0):
            self.result_files_layout.setColumnStretch(column, 1)

    def _reflow_source_controls(self, _narrow: bool, very_narrow: bool) -> None:
        self._take_all(self.database_controls)
        for column in range(2):
            self.database_controls.setColumnStretch(column, 0)
            self.database_controls.setColumnMinimumWidth(column, 0)
        self.database_controls.addWidget(self.database_list, 0, 0)
        self.database_controls.addWidget(self.database_button, 0, 1)
        self.database_controls.setColumnStretch(0, 1)

        groups = (
            (
                self.foreign_agents_controls,
                self.foreign_agents_list_panel,
                self.foreign_agents_button,
                self.clear_foreign_agents_button,
            ),
            (self.excel_controls, self.excel_list_panel, self.add_excel_button, self.clear_excel_button),
        )
        for layout, list_widget, primary, clear in groups:
            self._take_all(layout)
            for column in range(3):
                layout.setColumnStretch(column, 0)
                layout.setColumnMinimumWidth(column, 0)
            if very_narrow:
                layout.addWidget(list_widget, 0, 0, 1, 2)
                layout.addWidget(primary, 1, 0)
                layout.addWidget(clear, 1, 1)
                layout.setColumnStretch(0, 1)
                layout.setColumnStretch(1, 1)
            else:
                layout.addWidget(list_widget, 0, 0, 2, 1)
                layout.addWidget(primary, 0, 1)
                layout.addWidget(clear, 1, 1)
                layout.setColumnStretch(0, 1)

    def _reflow_report_lists(self, narrow: bool) -> None:
        self._take_all(self.report_lists_grid)
        for column in range(2):
            self.report_lists_grid.setColumnStretch(column, 0)
        columns = 1 if narrow else 2
        for index, checkbox in enumerate(self.report_list_checks):
            self.report_lists_grid.addWidget(
                checkbox,
                index // columns,
                index % columns,
            )
        for column in range(columns):
            self.report_lists_grid.setColumnStretch(column, 1)

        if hasattr(self, "report_format_layout"):
            self._take_all(self.report_format_layout)
            for column in range(3):
                self.report_format_layout.setColumnStretch(column, 0)
            if narrow:
                self.report_format_layout.addWidget(self.report_deduplicate_check, 0, 0, 1, 2)
                self.report_format_layout.addWidget(self.report_sort_label, 1, 0)
                self.report_format_layout.addWidget(self.report_sort_combo, 1, 1)
                self.report_format_layout.setColumnStretch(1, 1)
            else:
                self.report_format_layout.addWidget(self.report_deduplicate_check, 0, 0)
                self.report_format_layout.addWidget(self.report_sort_label, 0, 1)
                self.report_format_layout.addWidget(self.report_sort_combo, 0, 2)
                self.report_format_layout.setColumnStretch(2, 1)

    def _apply_responsive_layout(self, force: bool = False) -> None:
        """Перестраивает только реально видимые части интерфейса.

        Старые скрытые вкладки подключения и шапка больше не участвуют в
        адаптивной раскладке: работа с невидимыми виджетами была источником
        случайных изменений геометрии главного окна.
        """
        if not hasattr(self, "scroll_area"):
            return
        width = max(1, self.scroll_area.viewport().width())
        compact = width < 900
        very_compact = width < 760
        mode = (compact, very_compact)
        if not force and mode == self._responsive_mode:
            return
        self._responsive_mode = mode

        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.marker_settings_button.setToolTip("Открыть настройки приложения")

        if hasattr(self, "sources_grid"):
            self._take_all(self.sources_grid)
            if compact:
                self.sources_grid.addWidget(self.excel_card, 0, 0)
                self.sources_grid.addWidget(self.foreign_agents_card, 1, 0)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 0)
            else:
                self.sources_grid.addWidget(self.excel_card, 0, 0)
                self.sources_grid.addWidget(self.foreign_agents_card, 0, 1)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 1)

        if hasattr(self, "report_lists_grid"):
            self._reflow_report_lists(compact)

        if hasattr(self, "database_controls"):
            self._reflow_source_controls(compact, very_compact)

        QTimer.singleShot(0, self._fit_scroll_content)

    def _fit_scroll_content(self) -> None:
        if not hasattr(self, "scroll_area") or self.scroll_area.widget() is None:
            return
        bar = self.scroll_area.verticalScrollBar()
        current_key = self._workflow_page_key() if hasattr(self, "workflow_tabs") else "unknown"
        preserved = int(self._workflow_scroll_positions.get(current_key, bar.value()))
        content = self.scroll_area.widget()
        content.setMinimumHeight(0)
        content.setMaximumHeight(16777215)
        current_page = self.workflow_tabs.currentWidget()
        if current_page is not None and current_page.layout() is not None:
            current_page.layout().invalidate()
            current_page.layout().activate()
            current_page.updateGeometry()
        if content.layout() is not None:
            content.layout().invalidate()
            content.layout().activate()
        content.updateGeometry()
        self._queue_content_window_fit()

        # Пересчёт sizeHint не должен прокручивать страницу в начало.
        def restore() -> None:
            if hasattr(self, "workflow_tabs") and self._workflow_page_key() != current_key:
                return
            current_bar = self.scroll_area.verticalScrollBar()
            current_bar.setValue(max(current_bar.minimum(), min(preserved, current_bar.maximum())))

        QTimer.singleShot(0, restore)

    def _queue_content_window_fit(self) -> None:
        """Сводит несколько изменений layout к одному изменению размера окна."""
        if getattr(self, "_content_window_fit_pending", False):
            return
        self._content_window_fit_pending = True
        QTimer.singleShot(0, self._resize_to_current_content)

    def _resize_to_current_content(self) -> None:
        self._content_window_fit_pending = False
        if self.isMaximized() or self.isFullScreen() or not hasattr(self, "workflow_tabs"):
            return
        page = self.workflow_tabs.currentWidget()
        if page is None:
            return
        if page.layout() is not None:
            page.layout().invalidate()
            page.layout().activate()
        page_hint = page.sizeHint()
        tab_height = self.workflow_tabs.tabBar().sizeHint().height()
        frame_width = max(0, self.frameGeometry().width() - self.geometry().width())
        frame_height = max(0, self.frameGeometry().height() - self.geometry().height())
        target_width = max(self.minimumWidth(), page_hint.width() + 2)
        target_height = max(self.minimumHeight(), page_hint.height() + tab_height + 2)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_width = min(target_width, max(self.minimumWidth(), available.width() - frame_width))
            target_height = min(target_height, max(self.minimumHeight(), available.height() - frame_height))
        if self.width() != target_width or self.height() != target_height:
            self.resize(target_width, target_height)
        QTimer.singleShot(0, self._sync_scroll_content_geometry)

    def _sync_scroll_content_geometry(self) -> None:
        """Убирает остаточный размер предыдущей, более высокой вкладки."""
        if not hasattr(self, "scroll_area") or self.scroll_area.widget() is None:
            return
        content = self.scroll_area.widget()
        viewport = self.scroll_area.viewport()
        hint = content.sizeHint()
        content.setFixedHeight(max(viewport.height(), hint.height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "workflow_tabs"):
            self._apply_responsive_layout()
        if hasattr(self, "_window_state_autosave_timer"):
            self._queue_window_state_autosave()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "_window_state_autosave_timer"):
            self._queue_window_state_autosave()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._apply_responsive_layout(force=True))

    def _apply_style(self) -> None:
        self.setStyleSheet(main_window_stylesheet())
