from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
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

from irbis_control.paths import icon_path
from irbis_control.ui.components.widgets import SectionCard
from irbis_control.ui.theme import common_button_stylesheet


class MainWindowLayoutMixin:
    def _compose_simplified_workflow(self) -> None:
        """Перекомпоновывает рабочие элементы в короткий сценарий из трёх шагов."""
        self._simplified_workflow = True

        for widget in (
            self.irbis_intro,
            self.files_intro,
            self.lists_intro,
            self.markers_intro,
            self.results_intro,
            self.irbis_next_button,
            self.next_lists_button,
            self.next_marks_button,
            self.next_run_button,
            self.run_tab_start_button,
            self.create_matches_excel_button,
            self.report_create_card,
            self.action_title,
            self.action_hint,
        ):
            widget.hide()

        while self.workflow_tabs.count():
            self.workflow_tabs.removeTab(0)

        # Шаг 1. Источник и проверочные реестры находятся на одной странице.
        data_layout = self.irbis_tab.layout()
        self._take_all(data_layout)
        data_layout.setContentsMargins(8, 8, 8, 8)
        data_layout.setSpacing(8)

        mode_card = SectionCard("Где находятся записи", "")
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QLabel("Источник")
        mode_label.setObjectName("fieldLabel")
        self.source_mode_combo = QComboBox()
        self.source_mode_combo.addItem("Сервер ИРБИС", "irbis")
        self.source_mode_combo.addItem("TXT-файл", "txt")
        self.source_mode_combo.setMinimumWidth(190)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.source_mode_combo)
        mode_row.addStretch()
        mode_card.body.addLayout(mode_row)
        self.source_mode_hint = QLabel()
        self.source_mode_hint.setObjectName("cardDescription")
        self.source_mode_hint.setWordWrap(True)
        mode_card.body.addWidget(self.source_mode_hint)
        data_layout.addWidget(mode_card)

        connection_overview = QFrame()
        self.connection_overview = connection_overview
        connection_overview.setObjectName("sectionCard")
        overview_layout = QHBoxLayout(connection_overview)
        overview_layout.setContentsMargins(8, 7, 8, 8)
        overview_layout.setSpacing(6)
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
        # Старое имя оставлено как совместимый псевдоним для внешних интеграций.
        self.connection_details_toggle = self.connection_settings_button
        overview_layout.addWidget(self.connection_settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # Исходные контролы хранят состояние и используются операциями ИРБИС, но больше
        # не встраиваются в тесную карточку главного окна.
        self.connection_card.hide()
        self.base_card.hide()
        self.irbis_actions.hide()
        self.direct_irbis_box.hide()
        self.direct_note.hide()
        self.irbis_local_hint.hide()
        data_layout.addWidget(connection_overview)

        records_title = QLabel("Данные для проверки")
        records_title.setObjectName("pageSectionTitle")
        data_layout.addWidget(records_title)
        data_layout.addWidget(self.database_card)
        self.sources_grid = QGridLayout()
        self.sources_grid.setHorizontalSpacing(7)
        self.sources_grid.setVerticalSpacing(7)
        self.sources_grid.addWidget(self.foreign_agents_card, 0, 0)
        self.sources_grid.addWidget(self.excel_card, 0, 1)
        self.sources_grid.setColumnStretch(0, 1)
        self.sources_grid.setColumnStretch(1, 1)
        for card, list_widget in (
            (self.foreign_agents_card, self.foreign_agents_list),
            (self.excel_card, self.excel_list),
        ):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            list_widget.setMaximumHeight(58)
        data_layout.addLayout(self.sources_grid)
        data_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.data_tab = self.irbis_tab
        self.workflow_tabs.addTab(self.data_tab, "1  Данные")

        self.source_mode_combo.currentIndexChanged.connect(self._source_mode_changed)
        self.direct_irbis_checkbox.toggled.connect(self._sync_source_mode)
        self._sync_source_mode(self.direct_irbis_checkbox.isChecked())

        # Шаг 2. На виду остаётся только ожидаемый результат, детали раскрываются по запросу.
        parameters_layout = self.lists_tab.layout()
        self._take_all(parameters_layout)
        parameters_layout.setContentsMargins(8, 8, 8, 8)
        parameters_layout.setSpacing(8)

        outcome_card = SectionCard("Что получить после проверки", "")
        outcome_row = QHBoxLayout()
        outcome_row.setSpacing(8)
        outcome_label = QLabel("Результат")
        outcome_label.setObjectName("fieldLabel")
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem("Excel-отчёт и метки", "full")
        self.output_mode_combo.addItem("Только Excel-отчёт", "report")
        self.output_mode_combo.addItem("Только добавить метки", "markers")
        self.output_mode_combo.setMinimumWidth(230)
        outcome_row.addWidget(outcome_label)
        outcome_row.addWidget(self.output_mode_combo)
        outcome_row.addStretch()
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

        self.advanced_options_toggle = QPushButton("Открыть дополнительные настройки")
        self.advanced_options_toggle.setObjectName("mutedButton")
        self.advanced_options_toggle.setCheckable(False)
        parameters_layout.addWidget(self.advanced_options_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.advanced_settings_dialog = QDialog(self)
        self.advanced_settings_dialog.setWindowTitle("Дополнительные настройки")
        self.advanced_settings_dialog.setModal(True)
        self.advanced_settings_dialog.setFixedSize(540, 520)
        dialog_layout = QVBoxLayout(self.advanced_settings_dialog)
        dialog_layout.setContentsMargins(8, 8, 8, 8)
        dialog_layout.setSpacing(7)
        advanced_scroll = QScrollArea()
        advanced_scroll.setObjectName("mainScroll")
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        advanced_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        advanced_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.advanced_options = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_options)
        advanced_layout.setContentsMargins(0, 0, 4, 0)
        advanced_layout.setSpacing(7)
        self.match_settings_card.title_label.setText("Правила совпадения")
        advanced_layout.addWidget(self.match_settings_card)
        self.report_lists_card.title_label.setText("Состав Excel-отчёта")
        advanced_layout.addWidget(self.report_lists_card)
        self.compare_card.title_label.setText("Файлы результата")
        advanced_layout.addWidget(self.compare_card)
        self.marker_card.title_label.setText("Служебные метки")
        advanced_layout.addWidget(self.marker_card)
        advanced_layout.addStretch()
        self.create_excel_report_check.hide()
        self.report_only_check.hide()
        advanced_scroll.setWidget(self.advanced_options)
        dialog_layout.addWidget(advanced_scroll, 1)
        dialog_buttons = QHBoxLayout()
        dialog_buttons.addStretch()
        close_advanced_button = QPushButton("Закрыть")
        close_advanced_button.setObjectName("mutedButton")
        close_advanced_button.clicked.connect(self.advanced_settings_dialog.accept)
        dialog_buttons.addWidget(close_advanced_button)
        dialog_layout.addLayout(dialog_buttons)
        self.advanced_options_toggle.clicked.connect(self.open_advanced_settings)
        parameters_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.parameters_tab = self.lists_tab
        self.workflow_tabs.addTab(self.parameters_tab, "2  Параметры")
        self.output_mode_combo.currentIndexChanged.connect(self._output_mode_changed)

        # Шаг 3. Один основной запуск, краткий итог и раскрываемый технический журнал.
        results_layout = self.results_tab.layout()
        self._take_all(results_layout)
        results_layout.setContentsMargins(8, 8, 8, 8)
        results_layout.setSpacing(8)
        self._take_all(self.actions_layout)
        self.actions_layout.setContentsMargins(10, 9, 10, 10)
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
        self.header_actions.removeWidget(self.start_button)
        self.start_button.setText("Запустить проверку")
        self.start_button.setObjectName("primaryButton")
        self.start_button.show()
        run_buttons.addWidget(self.start_button, 1)
        self.cancel_button.show()
        run_buttons.addWidget(self.cancel_button)
        self.actions_layout.addLayout(run_buttons)
        self.actions_layout.addWidget(self.progress)
        self.actions_layout.addLayout(self.status_row)
        result_files = QGridLayout()
        result_files.setHorizontalSpacing(6)
        result_files.setVerticalSpacing(6)
        self.open_button.show()
        result_files.addWidget(self.open_button, 0, 0)
        result_files.addWidget(self.open_modified_database_button, 0, 1)
        result_files.addWidget(self.write_irbis_button, 1, 0)
        result_files.addWidget(self.compare_reports_button, 1, 1)
        result_files.setColumnStretch(0, 1)
        result_files.setColumnStretch(1, 1)
        self.actions_layout.addLayout(result_files)
        results_layout.addWidget(self.actions_card)

        self.log_toggle = QPushButton("Технический журнал")
        self.log_toggle.setObjectName("mutedButton")
        self.log_toggle.setCheckable(False)
        self.log_dialog = QDialog(self)
        self.log_dialog.setWindowTitle("Технический журнал")
        self.log_dialog.setModal(True)
        self.log_dialog.setFixedSize(540, 420)
        log_dialog_layout = QVBoxLayout(self.log_dialog)
        log_dialog_layout.setContentsMargins(8, 8, 8, 8)
        log_dialog_layout.setSpacing(7)
        log_dialog_layout.addWidget(self.log_card, 1)
        log_dialog_buttons = QHBoxLayout()
        log_dialog_buttons.addStretch()
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
        maintenance_layout.setContentsMargins(8, 7, 8, 8)
        maintenance_hint = QLabel(
            "Удаление ранее добавленных меток — отдельная операция, не связанная с текущей проверкой."
        )
        maintenance_hint.setObjectName("cardDescription")
        maintenance_hint.setWordWrap(True)
        maintenance_layout.addWidget(maintenance_hint)
        maintenance_layout.addWidget(self.cleanup_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.maintenance_dialog = QDialog(self)
        self.maintenance_dialog.setWindowTitle("Обслуживание базы")
        self.maintenance_dialog.setModal(True)
        self.maintenance_dialog.setFixedSize(460, 180)
        maintenance_dialog_layout = QVBoxLayout(self.maintenance_dialog)
        maintenance_dialog_layout.setContentsMargins(8, 8, 8, 8)
        maintenance_dialog_layout.setSpacing(7)
        maintenance_dialog_layout.addWidget(self.maintenance_panel, 1)
        maintenance_dialog_buttons = QHBoxLayout()
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
        self.workflow_tabs.addTab(self.results_tab, "3  Результат")

        self.application_settings_page = self.settings_page_class(self.app_settings, self)
        self.application_settings_page.saved.connect(self._save_application_settings)
        self.application_settings_page.cancelled.connect(self._close_application_settings)
        settings_index = self.workflow_tabs.addTab(self.application_settings_page, "Настройки")
        self.workflow_tabs.setTabVisible(settings_index, False)
        self._settings_return_page = self.data_tab
        self.workflow_tabs.currentChanged.connect(self._settings_navigation_changed)
        self.workflow_tabs.currentChanged.connect(self._workflow_page_changed)
        self.workflow_tabs.currentChanged.connect(lambda _index: QTimer.singleShot(0, self._fit_scroll_content))

        self.workflow_footer = QFrame()
        self.workflow_footer.setObjectName("workflowFooter")
        footer_layout = QHBoxLayout(self.workflow_footer)
        footer_layout.setContentsMargins(8, 4, 8, 8)
        footer_layout.setSpacing(6)
        self.back_step_button = QPushButton("← Назад")
        self.back_step_button.setObjectName("mutedButton")
        self.back_step_button.clicked.connect(lambda: self._move_workflow(-1))
        footer_layout.addWidget(self.back_step_button)
        footer_layout.addStretch()
        self.next_step_button = QPushButton("Далее →")
        self.next_step_button.setObjectName("primaryButton")
        self.next_step_button.clicked.connect(lambda: self._move_workflow(1))
        footer_layout.addWidget(self.next_step_button)
        self.root_layout.addWidget(self.workflow_footer)
        self._workflow_page_changed(self.workflow_tabs.currentIndex())

    def _set_disclosure(
        self,
        button: QPushButton,
        content: QWidget,
        expanded: bool,
        expanded_text: str,
        collapsed_text: str,
    ) -> None:
        content.setVisible(expanded)
        button.setText(expanded_text if expanded else collapsed_text)
        content.updateGeometry()
        if hasattr(self, "scroll_area") and self.scroll_area.widget() is not None:
            scroll_content = self.scroll_area.widget()
            scroll_content.setMinimumHeight(0)
            scroll_content.setMaximumHeight(16777215)
            scroll_content.updateGeometry()
        QTimer.singleShot(0, self._fit_scroll_content)

    def _move_workflow(self, offset: int) -> None:
        index = self.workflow_tabs.currentIndex()
        if 0 <= index <= 2:
            self.workflow_tabs.setCurrentIndex(max(0, min(2, index + offset)))

    def open_advanced_settings(self) -> None:
        self.advanced_settings_dialog.exec()

    def open_log_window(self) -> None:
        self.log_dialog.exec()

    def open_maintenance_window(self) -> None:
        self.maintenance_dialog.exec()

    def _workflow_page_changed(self, _index: int) -> None:
        current = self.workflow_tabs.currentWidget()
        QTimer.singleShot(0, lambda: self.scroll_area.verticalScrollBar().setValue(0))
        in_workflow = current in (self.data_tab, self.parameters_tab, self.results_tab)
        self.workflow_footer.setVisible(in_workflow)
        if not in_workflow:
            return
        step = (self.data_tab, self.parameters_tab, self.results_tab).index(current)
        self.back_step_button.setVisible(step > 0)
        self.next_step_button.setVisible(step < 2)
        if step == 0:
            self.next_step_button.setText("Далее: параметры →")
        elif step == 1:
            self.next_step_button.setText("Далее: результат →")

    def _source_mode_changed(self, _index: int) -> None:
        direct = self.source_mode_combo.currentData() == "irbis"
        if self.direct_irbis_checkbox.isChecked() != direct:
            self.direct_irbis_checkbox.setChecked(direct)
        self.source_mode_hint.setText(
            "Записи будут прочитаны с сервера и безопасно обновлены по одной MFN."
            if direct
            else "Выберите одну или несколько TXT-баз ниже. Исходные файлы останутся без изменений."
        )
        self.connection_overview.setVisible(direct)
        self.database_card.setVisible(not direct)
        self._refresh_connection_overview()

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
        if getattr(self, "_syncing_output_mode", False):
            return
        mode = self.output_mode_combo.currentData()
        self._syncing_output_mode = True
        try:
            self.create_excel_report_check.setChecked(mode in {"full", "report"})
            self.report_only_check.setChecked(mode == "report")
        finally:
            self._syncing_output_mode = False
        descriptions = {
            "full": "Будет создан Excel-отчёт, а подтверждённые совпадения получат служебные метки.",
            "report": "Будет создан только Excel-отчёт. Записи ИРБИС и TXT-файлы не изменятся.",
            "markers": "Подтверждённые совпадения получат метки; Excel-отчёт создаваться не будет.",
        }
        self.output_mode_hint.setText(descriptions[str(mode)])

    def _sync_output_mode_from_checks(self) -> None:
        if not hasattr(self, "output_mode_combo"):
            return
        if self.report_only_check.isChecked():
            mode = "report"
        elif self.create_excel_report_check.isChecked():
            mode = "full"
        else:
            mode = "markers"
        self._syncing_output_mode = True
        try:
            index = self.output_mode_combo.findData(mode)
            self.output_mode_combo.setCurrentIndex(max(0, index))
        finally:
            self._syncing_output_mode = False
        self._output_mode_changed(self.output_mode_combo.currentIndex())

    @staticmethod
    def _take_all(layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _reflow_irbis_connection_form(self, _narrow: bool) -> None:
        if not hasattr(self, "irbis_connection_form"):
            return
        form = self.irbis_connection_form
        self._take_all(form)
        for column in range(4):
            form.setColumnStretch(column, 0)
            form.setColumnMinimumWidth(column, 0)

        labels = self.irbis_field_labels
        fields = {
            "host": self.irbis_host_edit,
            "port": self.irbis_port_spin,
            "login": self.irbis_login_edit,
            "password": self.irbis_password_box,
            "database": self.irbis_database_box,
            "query": self.irbis_query_edit,
        }

        # Макет подключения всегда состоит из двух равных колонок:
        # слева сервер/логин/база, справа порт/пароль/запрос.
        pairs = (("host", "port"), ("login", "password"), ("database", "query"))
        for pair_row, (left, right) in enumerate(pairs):
            label_row = pair_row * 2
            field_row = label_row + 1
            form.addWidget(labels[left], label_row, 0)
            form.addWidget(labels[right], label_row, 1)
            form.addWidget(fields[left], field_row, 0)
            form.addWidget(fields[right], field_row, 1)
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 1)
        self.irbis_db_combo.setMinimumContentsLength(6)
        self.irbis_db_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def _reflow_actions(self, columns: int) -> None:
        self._take_all(self.actions_buttons_layout)
        for column in range(8):
            self.actions_buttons_layout.setColumnStretch(column, 0)
        visible_buttons = [button for button in self.action_buttons if not button.isHidden()]
        for index, button in enumerate(visible_buttons):
            self.actions_buttons_layout.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.actions_buttons_layout.setColumnStretch(column, 1)

    def _reflow_source_controls(self, narrow: bool, very_narrow: bool) -> None:
        groups = (
            (self.database_controls, self.database_list, self.database_button, self.clear_database_button),
            (
                self.foreign_agents_controls,
                self.foreign_agents_list,
                self.foreign_agents_button,
                self.clear_foreign_agents_button,
            ),
            (self.excel_controls, self.excel_list, self.add_excel_button, self.clear_excel_button),
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

    def _reflow_file_controls(self, narrow: bool) -> None:
        self._take_all(self.compare_controls)
        self._take_all(self.utility_controls)
        for column in range(4):
            self.compare_controls.setColumnStretch(column, 0)
            self.utility_controls.setColumnStretch(column, 0)

        if narrow:
            self.compare_controls.addWidget(self.create_excel_report_check, 0, 0, 1, 4)
            self.compare_controls.addWidget(self.report_only_check, 1, 0, 1, 4)
            self.compare_controls.setColumnStretch(0, 1)
            self.utility_controls.addWidget(self.next_marks_button, 0, 0, 1, 2)
            self.utility_controls.setColumnStretch(0, 1)
            self.utility_controls.setColumnStretch(1, 1)
        else:
            self.compare_controls.addWidget(self.create_excel_report_check, 0, 0)
            self.compare_controls.addWidget(self.report_only_check, 0, 1)
            self.compare_controls.setColumnStretch(0, 1)
            self.compare_controls.setColumnStretch(1, 1)
            self.utility_controls.addWidget(self.next_marks_button, 0, 0)
            self.utility_controls.setColumnStretch(0, 1)

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
        if not hasattr(self, "scroll_area"):
            return
        width = max(1, self.scroll_area.viewport().width())
        mode = tuple(width < breakpoint for breakpoint in (1500, 720, 900, 800, 760, 700))
        if not force and mode == self._responsive_mode:
            QTimer.singleShot(0, self._fit_scroll_content)
            return
        self._responsive_mode = mode
        (
            compact_header,
            stack_irbis,
            compact,
            short_start,
            very_compact,
            hide_logo,
        ) = mode

        if hasattr(self, "irbis_connection_form"):
            self._reflow_irbis_connection_form(very_compact)

        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(4 if very_compact else (6 if compact else 8))

        logo_size = 22 if compact else 26
        self.header_logo.setVisible(not hide_logo)
        if self.header_logo.isVisible():
            self.header_logo.setPixmap(QIcon(icon_path("irbis64_control_icon.png")).pixmap(logo_size, logo_size))
            self.header_logo.setFixedSize(logo_size + 2, logo_size + 2)
        title_font = self.main_title.font()
        title_font.setPointSize(10 if compact else 11)
        title_font.setBold(True)
        self.main_title.setFont(title_font)
        self.subtitle_primary.setVisible(width >= 900)

        self.start_button.setText("Запуск" if short_start else "Запустить проверку")
        self.start_button.setMinimumWidth(0)
        self.marker_settings_button.setText("Настройки")
        self.marker_settings_button.setToolTip("Открыть настройки приложения")
        self.marker_settings_button.setMinimumWidth(0)
        self.marker_settings_button.setMaximumWidth(16777215)
        header_buttons = (
            (self.useful_links_button, "Полезные ссылки"),
            (self.update_button, "Проверить обновление"),
        )
        for button, full_text in header_buttons:
            button.setVisible(True)
            button.setText("" if compact_header else full_text)
            button.setToolTip(full_text)
            button.setMinimumWidth(32 if compact_header else 0)
            button.setMaximumWidth(32 if compact_header else 16777215)

        tab_titles = (
            ("1  Данные", "2  Параметры", "3  Результат")
            if getattr(self, "_simplified_workflow", False)
            else ("Подключение", "Источники", "Списки", "Метки", "Запуск")
        )
        for index, title in enumerate(tab_titles):
            self.workflow_tabs.setTabText(index, title)

        for intro_label in (
            self.irbis_intro,
            self.files_intro,
            self.lists_intro,
            self.markers_intro,
            self.results_intro,
        ):
            intro_label.hide()

        # Блоки подключения располагаются рядом только когда для обоих хватает
        # места; на меньшей ширине они складываются вертикально без обрезания.
        if hasattr(self, "irbis_columns") and not getattr(self, "_simplified_workflow", False):
            self._take_all(self.irbis_columns)
            for column in range(2):
                self.irbis_columns.setColumnStretch(column, 0)
            if stack_irbis:
                self.irbis_columns.addWidget(self.connection_card, 0, 0)
                self.irbis_columns.addWidget(self.base_card, 1, 0)
                self.irbis_columns.setAlignment(self.connection_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setAlignment(self.base_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setColumnStretch(0, 1)
                self.irbis_columns.setColumnStretch(1, 0)
            else:
                self.irbis_columns.addWidget(self.connection_card, 0, 0)
                self.irbis_columns.addWidget(self.base_card, 0, 1)
                self.irbis_columns.setAlignment(self.connection_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setAlignment(self.base_card, Qt.AlignmentFlag.AlignTop)
                self.irbis_columns.setColumnStretch(0, 3)
                self.irbis_columns.setColumnStretch(1, 2)

        if hasattr(self, "irbis_action_layout"):
            self._take_all(self.irbis_action_layout)
            for column in range(3):
                self.irbis_action_layout.setColumnStretch(column, 0)
            if very_compact:
                self.irbis_test_button.setMaximumWidth(16777215)
                self.irbis_action_layout.addWidget(self.irbis_test_button, 0, 0)
                self.irbis_action_layout.setColumnStretch(0, 1)
            else:
                self.irbis_test_button.setMaximumWidth(240)
                self.irbis_action_layout.addWidget(self.irbis_test_button, 0, 1)
                self.irbis_action_layout.setColumnStretch(0, 1)
            self.irbis_action_layout.addWidget(self.irbis_progress, 1, 0, 1, 3)
            self.irbis_action_layout.addWidget(self.irbis_status_box, 2, 0, 1, 3)

        if hasattr(self, "sources_grid"):
            self._take_all(self.sources_grid)
            if compact:
                self.sources_grid.addWidget(self.foreign_agents_card, 0, 0)
                self.sources_grid.addWidget(self.excel_card, 1, 0)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 0)
            else:
                self.sources_grid.addWidget(self.foreign_agents_card, 0, 0)
                self.sources_grid.addWidget(self.excel_card, 0, 1)
                self.sources_grid.setColumnStretch(0, 1)
                self.sources_grid.setColumnStretch(1, 1)

        if hasattr(self, "report_lists_grid"):
            self._reflow_report_lists(compact)

        if hasattr(self, "database_controls"):
            self._reflow_source_controls(compact, very_compact)
            self._reflow_file_controls(compact)

        if hasattr(self, "action_buttons"):
            self._reflow_actions(2)

        for card in self.section_cards:
            card.set_compact(True, very_compact)

        QTimer.singleShot(0, self._fit_scroll_content)

    def _fit_scroll_content(self) -> None:
        if not hasattr(self, "scroll_area") or self.scroll_area.widget() is None:
            return
        if self.workflow_tabs.currentWidget() is self.data_tab:
            lists = (self.foreign_agents_list, self.excel_list)
            for list_widget in lists:
                list_widget.setMaximumHeight(58)
            data_layout = self.data_tab.layout()
            if data_layout is not None:
                data_layout.activate()
                footer_height = self.workflow_footer.sizeHint().height() if self.workflow_footer.isVisible() else 0
                available_page_height = max(
                    0,
                    self.scroll_area.viewport().height()
                    - self.workflow_tabs.tabBar().sizeHint().height()
                    - footer_height
                    - self.root_layout.spacing()
                    - 6,
                )
                free_height = max(0, available_page_height - data_layout.sizeHint().height())
                list_height = min(120, 58 + free_height // len(lists))
                for list_widget in lists:
                    list_widget.setMaximumHeight(list_height)
                data_layout.activate()
        content = self.scroll_area.widget()
        content.setMinimumHeight(0)
        content.setMaximumHeight(16777215)
        if content.layout() is not None:
            content.layout().activate()
        desired_height = max(240, content.sizeHint().height())
        viewport = self.scroll_area.viewport()
        content.setMinimumHeight(max(viewport.height(), desired_height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "section_cards"):
            self._apply_responsive_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._apply_responsive_layout(force=True))

    def _apply_style(self) -> None:
        # Основные элементы оставляем системному стилю Qt/Windows.
        # Здесь только небольшие типографические настройки, без карточек,
        # скруглений, цветных рамок и нестандартных кнопок.
        base_style = common_button_stylesheet() + """
            QMainWindow, QDialog, QWidget#centralPage, QWidget#tabPage { background: #f8fafc; color: #202020; }
            QScrollArea#mainScroll { border: none; background: #f8fafc; }
            QFrame#headerCard, QFrame#irbisActions { border: none; background: transparent; }
            QFrame#sectionCard, QFrame#actionCard {
                border: 1px solid #cbd6e2;
                border-radius: 5px;
                background: #ffffff;
            }
            QFrame#dangerCard { border: 1px solid #dfcaca; border-radius: 5px; background: #ffffff; }
            QLabel { color: #202020; }
            QLabel#mainTitle { font-weight: 600; color: #151515; }
            QLabel#cardTitle { font-size: 13px; font-weight: 600; color: #006bd6; }
            QLabel#pageSectionTitle { font-size: 14px; font-weight: 600; color: #202020; margin-top: 4px; }
            QLabel#irbisStateDot { background: #d94a4a; border-radius: 5px; }
            QLabel#irbisStateDot[state="success"] { background: #35b85f; }
            QLabel#irbisStateDot[state="running"], QLabel#irbisStateDot[state="warning"] { background: #e4a11b; }
            QLabel#irbisStateDot[state="error"] { background: #d94a4a; }
            QLabel#directSourceDot { background: #d94a4a; border-radius: 4px; }
            QLabel#directSourceDot[state="success"] { background: #35b85f; }
            QLabel#directSourceDot[state="running"], QLabel#directSourceDot[state="warning"] { background: #e4a11b; }
            QLabel#directSourceDot[state="error"] { background: #d94a4a; }
            QLabel#directSourceDot[state="local"] { background: #6f7f8f; }
            QLabel#sourceStatusTitle { color: #202020; font-weight: 600; }
            QLabel#sourceStateLabel { color: #d94a4a; }
            QLabel#sourceStateLabel[state="success"] { color: #218b45; }
            QLabel#sourceStateLabel[state="running"], QLabel#sourceStateLabel[state="warning"] { color: #9a6800; }
            QLabel#sourceStateLabel[state="error"] { color: #b52f2f; }
            QLabel#sourceStateLabel[state="local"] { color: #5a5a5a; }
            QLabel#fieldLabel { color: #202020; }
            QLabel#tabIntro, QLabel#cardDescription, QLabel#statusLabel { color: #5a5a5a; }
            QLabel:disabled { color: #6b6b6b; }
            QTextEdit#logEdit, QTextEdit#plainLogEdit { font-family: Consolas, monospace; }
            QLineEdit, QComboBox, QSpinBox { min-height: 23px; }
            QComboBox#databaseCombo::drop-down { width: 0; border: none; }
            QComboBox#databaseCombo::down-arrow { image: none; }
            QPushButton#primaryButton[compact="true"] { margin: 1px; }
            QFrame#workflowFooter { border-top: 1px solid #d9e1e8; background: #f8fafc; }
            QListWidget#compactList::item { min-height: 21px; }
            QListWidget#matchRulesList::item {
                padding: 0px 8px;
                border-left: 2px solid transparent;
                color: #202020;
            }
            QListWidget#matchRulesList::item:selected {
                border-left-color: #0878e3;
                background: #eef6ff;
                color: #202020;
            }
            QProgressBar { min-height: 12px; max-height: 12px; }
            QProgressBar#mainProgress {
                min-height: 4px;
                max-height: 4px;
                border: none;
                border-radius: 2px;
                background: #c4c9cf;
                padding: 0;
                margin: 0;
                text-align: center;
            }
            QProgressBar#mainProgress::chunk {
                border: none;
                border-radius: 2px;
                background: #0078d4;
                margin: 0;
            }

            /* Вкладки и их содержимое образуют одну общую поверхность. */
            QTabWidget#workflowTabs::pane {
                border: none;
                background: palette(window);
                top: 0px;
            }
            QTabWidget#workflowTabs QTabBar {
                background: transparent;
                qproperty-drawBase: 0;
            }
            QTabWidget#workflowTabs QTabBar::tab {
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                padding: 5px 10px;
                margin: 0px;
            }
            QTabWidget#workflowTabs QTabBar::tab:selected {
                color: palette(highlight);
                border: none;
                border-bottom: 2px solid palette(highlight);
            }
            QTabWidget#workflowTabs QTabBar::tab:hover:!selected {
                background: palette(alternate-base);
                border: none;
                border-bottom: 2px solid transparent;
            }
            QPushButton#settingsTab {
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                border-radius: 0;
                padding: 5px 10px;
                margin: 0;
                min-height: 0;
            }
            QPushButton#settingsTab:checked {
                color: palette(highlight);
                border-bottom: 2px solid palette(highlight);
            }
            QPushButton#settingsTab:hover:!checked {
                background: palette(alternate-base);
            }
            """
        self.setStyleSheet(base_style)
