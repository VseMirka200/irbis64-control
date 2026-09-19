from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from irbis_control.ui.components.widgets import (
    CompactTabWidget,
    DatabaseComboBox,
    FileDropListWidget,
    LayoutHintWidget,
    MatchRulesEditor,
    SectionCard,
)


class MainWindowBuildMixin:
    def _build_ui(self) -> None:
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("mainScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setCentralWidget(self.scroll_area)

        central = LayoutHintWidget()
        central.setObjectName("centralPage")
        self.scroll_area.setWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(8)
        self._workflow_scroll_positions: dict[str, int] = {}
        self._active_workflow_page_key: str | None = None
        self._advanced_scroll_position = 0

        # Настройки остаются отдельным действием рядом с вкладками.
        # Раньше перед вкладками создавалась полностью скрытая шапка с дублирующими
        # кнопками запуска, ссылок и обновления. Скрытые контролы продолжали жить,
        # получать сигналы и участвовать в адаптивной раскладке, из-за чего появлялись
        # лишние зависимости и трудно воспроизводимые UI-баги.
        self.marker_settings_button = QPushButton("Настройки")
        self.marker_settings_button.setObjectName("settingsTab")
        self.marker_settings_button.setCheckable(True)
        self.marker_settings_button.clicked.connect(self.open_application_settings)

        self.workflow_tabs = CompactTabWidget()
        self.workflow_tabs.setObjectName("workflowTabs")
        self.workflow_tabs.setDocumentMode(True)
        self.workflow_tabs.setUsesScrollButtons(True)
        self.workflow_tabs.tabBar().setExpanding(False)
        self.workflow_tabs.tabBar().setDrawBase(False)
        self.workflow_tabs.tabBar().setMovable(False)
        self.workflow_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.workflow_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        # Кнопка настроек остаётся справа от вкладок и доступна с любой страницы.
        self.workflow_tabs.setCornerWidget(
            self.marker_settings_button,
            Qt.Corner.TopRightCorner,
        )
        self.root_layout.addWidget(self.workflow_tabs)

        self._build_connection_tab()
        self._build_sources_tab()
        self._build_lists_tab()
        self._build_markers_tab()
        self._build_results_tab()
        self._compose_simplified_workflow()

        self._responsive_mode = None

        self._restore_irbis_config()
        self.irbis_host_edit.textChanged.connect(self._invalidate_irbis_connection_status)
        self.irbis_port_spin.valueChanged.connect(self._invalidate_irbis_connection_status)
        self.irbis_login_edit.textChanged.connect(self._invalidate_irbis_connection_status)
        self.irbis_password_edit.textChanged.connect(self._invalidate_irbis_connection_status)
        self._apply_marker_settings_to_ui()
        self._refresh_connection_overview()
        for checkbox in (
            *self.report_list_checks,
            self.report_deduplicate_check,
        ):
            checkbox.toggled.connect(self._queue_report_settings_autosave)
        self.report_sort_combo.currentIndexChanged.connect(self._queue_report_settings_autosave)
        self._marker_autosave_timer = QTimer(self)
        self._marker_autosave_timer.setSingleShot(True)
        self._marker_autosave_timer.timeout.connect(self._autosave_marker_settings)
        self.match_rules_editor.changed.connect(self._queue_marker_settings_autosave)
        self.fuzzy_match_check.toggled.connect(self._queue_marker_settings_autosave)
        for widget in (
            self.substance_marker_check,
            self.foreign_marker_check,
            self.foreign_organization_marker_check,
            self.age_marker_check,
            self.substance_marker_edit,
            self.foreign_marker_edit,
            self.foreign_organization_marker_edit,
            self.age_marker_edit,
            self.substance_field_spin,
            self.foreign_field_spin,
            self.foreign_organization_field_spin,
            self.age_field_spin,
        ):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._queue_marker_settings_autosave)
                widget.toggled.connect(self._update_marker_control_states)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._queue_marker_settings_autosave)
            else:
                widget.valueChanged.connect(self._queue_marker_settings_autosave)
        self.nkp_live_check.toggled.connect(self._nkp_source_preference_changed)
        self.nkp_foreign_live_check.toggled.connect(self._nkp_source_preference_changed)
        self.nkp_foreign_live_check.toggled.connect(self._update_nkp_source_controls)
        self._update_nkp_source_controls()
        self._refresh_registry_cache_status()
        self._update_database_summary()
        self._update_foreign_agents_summary()
        self._update_excel_summary()
        self._set_default_outputs(force=False)
        self._apply_responsive_layout(force=True)

    # Собирает параметры подключения и состояние сервера на одной вкладке.
    def _build_connection_tab(self) -> None:
        """Создаёт состояние подключения без второй, скрытой формы.

        Пользователь редактирует подключение только через IrbisConnectionDialog.
        Эти Qt-поля остаются внутренним адаптером для существующей логики и
        сигналов, но находятся в одном скрытом контейнере и не участвуют в layout.
        """
        self.irbis_tab = QWidget()
        self.irbis_tab.setObjectName("tabPage")
        irbis_root = QVBoxLayout(self.irbis_tab)
        irbis_root.setContentsMargins(8, 8, 8, 4)
        irbis_root.setSpacing(8)

        self._irbis_state_container = QWidget(self.irbis_tab)
        self._irbis_state_container.hide()

        self.irbis_host_edit = QLineEdit(self._irbis_state_container)
        self.irbis_port_spin = QSpinBox(self._irbis_state_container)
        self.irbis_port_spin.setRange(1, 65535)
        self.irbis_port_spin.setValue(6666)
        self.irbis_port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.irbis_login_edit = QLineEdit(self._irbis_state_container)
        self.irbis_password_edit = QLineEdit(self._irbis_state_container)
        self.irbis_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.irbis_query_edit = QLineEdit("I=$", self._irbis_state_container)

        self.irbis_db_combo = DatabaseComboBox()
        self.irbis_db_combo.setParent(self._irbis_state_container)
        self.irbis_db_combo.setToolTip("Список доступных баз АРМ Каталогизатор.")
        self.irbis_db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.irbis_db_combo.setMinimumContentsLength(18)
        self.irbis_db_combo.currentIndexChanged.connect(
            lambda _index: self._update_direct_mode_ui() if hasattr(self, "direct_source_label") else None
        )

        self.direct_irbis_checkbox = QCheckBox(self._irbis_state_container)
        self.direct_irbis_checkbox.setAccessibleName("Работать напрямую с ИРБИС")
        self.direct_irbis_checkbox.setChecked(True)

        self.irbis_read_workers = 4
        self.irbis_page_size = 500
        self.irbis_snapshot_path = str(self._app_data_dir() / "direct_database.txt")
        self.irbis_manifest_path = str(self._app_data_dir() / "direct_database.map.json")

    # Собирает источники данных и правила, по которым будут сверяться записи.
    def _build_sources_tab(self) -> None:
        self.database_card = SectionCard("Источник записей")
        self.direct_source_dot = QLabel()
        self.direct_source_dot.setObjectName("directSourceDot")
        self.direct_source_dot.setProperty("state", "error")
        self.direct_source_dot.setFixedSize(9, 9)
        self.direct_source_label = QLabel("ИРБИС")
        self.direct_source_label.setObjectName("sourceStatusTitle")
        self.direct_source_state_label = QLabel("Нет подключения")
        self.direct_source_state_label.setObjectName("sourceStateLabel")
        self.direct_source_state_label.setProperty("state", "error")
        source_text = QVBoxLayout()
        source_text.setContentsMargins(0, 0, 0, 0)
        source_text.setSpacing(0)
        source_title_row = QHBoxLayout()
        source_title_row.setContentsMargins(0, 0, 0, 0)
        source_title_row.setSpacing(7)
        source_title_row.addWidget(self.direct_source_label)
        source_title_row.addWidget(self.direct_source_state_label)
        source_title_row.addStretch()
        source_text.addLayout(source_title_row)
        source_status_row = QHBoxLayout()
        source_status_row.setContentsMargins(0, 0, 0, 0)
        source_status_row.setSpacing(7)
        source_status_row.addWidget(
            self.direct_source_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        source_status_row.addLayout(source_text, 1)

        # Заголовок и состояние образуют единую левую колонку. Благодаря этому
        # кнопка справа центрируется относительно всей высоты карточки.
        self.database_card.outer_layout.removeItem(self.database_card.title_row)
        source_left = QVBoxLayout()
        source_left.setContentsMargins(0, 0, 0, 0)
        source_left.setSpacing(4)
        source_left.addLayout(self.database_card.title_row)
        source_left.addLayout(source_status_row)
        source_summary_row = QHBoxLayout()
        source_summary_row.setContentsMargins(0, 0, 0, 0)
        source_summary_row.setSpacing(7)
        source_summary_row.addLayout(source_left, 1)
        self.database_card.body.addLayout(source_summary_row)
        self.database_list = QListWidget()
        self.database_list.setObjectName("compactList")
        self.database_list.setMaximumHeight(58)
        self.database_list.setMinimumHeight(38)
        self.database_button = QPushButton("Добавить TXT-базы")
        self.database_button.setObjectName("secondaryButton")
        self.database_button.clicked.connect(self.select_database)
        self.clear_database_button = QPushButton("Очистить")
        self.clear_database_button.setObjectName("mutedButton")
        self.clear_database_button.clicked.connect(self._clear_database_files)
        database_row = QGridLayout()
        database_row.setHorizontalSpacing(4)
        database_row.setVerticalSpacing(4)
        self.database_controls = database_row
        self.database_card.body.addLayout(database_row)
        self.direct_irbis_checkbox.toggled.connect(self._update_direct_mode_ui)
        self.foreign_agents_card = SectionCard("Реестр иностранных агентов")
        self.nkp_foreign_live_check = QCheckBox("Брать актуальный список с НКП РГБ при каждом запуске")
        self.nkp_foreign_live_check.setChecked(bool(self.app_settings.use_nkp_foreign_agents_registry))
        self.nkp_foreign_live_check.setToolTip(
            "Список загружается с https://nkp.rsl.ru/foreign-agents-registry. "
            "При временной недоступности сайта используется последняя сохранённая копия."
        )
        self.foreign_agents_card.body.addWidget(self.nkp_foreign_live_check)
        self.nkp_foreign_status_label = QLabel("Кэш НКП ещё не загружен")
        self.nkp_foreign_status_label.setObjectName("cardDescription")
        self.nkp_foreign_status_label.setWordWrap(True)
        self.foreign_agents_card.body.addWidget(self.nkp_foreign_status_label)
        foreign_online_actions = QHBoxLayout()
        foreign_online_actions.setSpacing(4)
        self.refresh_nkp_foreign_button = QPushButton("Обновить")
        self.refresh_nkp_foreign_button.setObjectName("mutedButton")
        self.refresh_nkp_foreign_button.clicked.connect(lambda: self.refresh_nkp_registry("foreign"))
        self.open_nkp_foreign_button = QPushButton("Открыть сайт")
        self.open_nkp_foreign_button.setObjectName("mutedButton")
        self.open_nkp_foreign_button.clicked.connect(lambda: self.open_nkp_registry_page("foreign"))
        self.download_nkp_foreign_button = QPushButton("Скачать файл")
        self.download_nkp_foreign_button.setObjectName("mutedButton")
        self.download_nkp_foreign_button.clicked.connect(lambda: self.download_nkp_registry_file("foreign"))
        for button in (
            self.refresh_nkp_foreign_button,
            self.open_nkp_foreign_button,
            self.download_nkp_foreign_button,
        ):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            foreign_online_actions.addWidget(button, 1)
        self.foreign_agents_card.body.addLayout(foreign_online_actions)
        self.foreign_agents_path = ""
        self.foreign_agents_list = FileDropListWidget(extensions=(".xlsx", ".xlsm"), allow_multiple=False)
        self.foreign_agents_list.setObjectName("compactList")
        self.foreign_agents_list.setFixedHeight(84)
        self.foreign_agents_list.setToolTip("Перетащите сюда реестр .xlsx/.xlsm или нажмите «Добавить Excel»")
        self.foreign_agents_list.filesDropped.connect(self._drop_foreign_agents_files)
        self.foreign_agents_button = QPushButton("Добавить Excel")
        self.foreign_agents_button.setObjectName("secondaryButton")
        self.foreign_agents_button.clicked.connect(self.select_foreign_agents)
        self.clear_foreign_agents_button = QPushButton("Очистить")
        self.clear_foreign_agents_button.setObjectName("mutedButton")
        self.clear_foreign_agents_button.clicked.connect(self._clear_foreign_agents)
        fa_actions = QGridLayout()
        fa_actions.setHorizontalSpacing(4)
        fa_actions.setVerticalSpacing(4)
        self.foreign_agents_controls = fa_actions
        self.foreign_agents_card.body.addLayout(fa_actions)
        self.excel_card = SectionCard("Реестр по наркотическим веществам")
        self.nkp_live_check = QCheckBox("Брать актуальный список с НКП РГБ при каждом запуске")
        self.nkp_live_check.setChecked(bool(self.app_settings.use_nkp_drug_registry))
        self.nkp_live_check.setToolTip(
            "Список загружается с https://nkp.rsl.ru/drug-literature-recommendations. "
            "При временной недоступности сайта используется последняя сохранённая копия."
        )
        self.excel_card.body.addWidget(self.nkp_live_check)
        self.nkp_drug_status_label = QLabel("Кэш НКП ещё не загружен")
        self.nkp_drug_status_label.setObjectName("cardDescription")
        self.nkp_drug_status_label.setWordWrap(True)
        self.excel_card.body.addWidget(self.nkp_drug_status_label)
        drug_online_actions = QHBoxLayout()
        drug_online_actions.setSpacing(4)
        self.refresh_nkp_drug_button = QPushButton("Обновить")
        self.refresh_nkp_drug_button.setObjectName("mutedButton")
        self.refresh_nkp_drug_button.clicked.connect(lambda: self.refresh_nkp_registry("drug"))
        self.open_nkp_drug_button = QPushButton("Открыть сайт")
        self.open_nkp_drug_button.setObjectName("mutedButton")
        self.open_nkp_drug_button.clicked.connect(lambda: self.open_nkp_registry_page("drug"))
        self.download_nkp_drug_button = QPushButton("Скачать файл")
        self.download_nkp_drug_button.setObjectName("mutedButton")
        self.download_nkp_drug_button.clicked.connect(lambda: self.download_nkp_registry_file("drug"))
        for button in (
            self.refresh_nkp_drug_button,
            self.open_nkp_drug_button,
            self.download_nkp_drug_button,
        ):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            drug_online_actions.addWidget(button, 1)
        self.excel_card.body.addLayout(drug_online_actions)
        self.excel_list = FileDropListWidget(extensions=(".xlsx", ".xlsm", ".xls"), allow_multiple=True)
        self.excel_list.setObjectName("compactList")
        self.excel_list.setFixedHeight(84)
        self.excel_list.setToolTip("Перетащите сюда один или несколько Excel-файлов или нажмите «Добавить Excel»")
        self.excel_list.filesDropped.connect(self._drop_excel_files)
        self.add_excel_button = QPushButton("Добавить Excel")
        self.add_excel_button.setObjectName("secondaryButton")
        self.add_excel_button.clicked.connect(self.add_excel_files)
        self.clear_excel_button = QPushButton("Очистить")
        self.clear_excel_button.setObjectName("mutedButton")
        self.clear_excel_button.clicked.connect(self._clear_excel_files)
        ex_actions = QGridLayout()
        ex_actions.setHorizontalSpacing(4)
        ex_actions.setVerticalSpacing(4)
        self.excel_controls = ex_actions
        self.excel_card.body.addLayout(ex_actions)
        match_settings_card = SectionCard("Порядок сравнения")
        self.match_settings_card = match_settings_card
        # Редактор правил обязан находиться в layout карточки. Раньше он
        # создавался дочерним виджетом SectionCard, но не добавлялся в body:
        # после show() Qt оставлял ему геометрию (0, 0, ...), и поле выбора
        # перекрывало заголовок «Порядок сравнения».
        self.match_rules_editor = MatchRulesEditor()
        match_settings_card.body.addWidget(self.match_rules_editor)
        self.fuzzy_match_check = QCheckBox("Искать похожие названия с опечатками")
        match_settings_card.body.addWidget(self.fuzzy_match_check)
        compare_card = SectionCard("Результаты")
        self.compare_card = compare_card
        output_grid = QGridLayout()
        output_grid.setHorizontalSpacing(5)
        output_grid.setVerticalSpacing(4)
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("filePath")
        self.output_edit.setReadOnly(True)
        self.modified_database_edit = QLineEdit()
        self.modified_database_edit.setObjectName("filePath")
        self.modified_database_edit.setReadOnly(True)
        self.output_label = QLabel("Excel-отчёт")
        output_grid.addWidget(self.output_label, 0, 0)
        output_grid.addWidget(self.output_edit, 0, 1)
        self.report_path_button = QPushButton("Изменить…")
        self.report_path_button.setObjectName("mutedButton")
        self.report_path_button.clicked.connect(self.select_output)
        output_grid.addWidget(self.report_path_button, 0, 2)
        self.modified_database_label = QLabel("TXT-копия с метками")
        output_grid.addWidget(self.modified_database_label, 1, 0)
        output_grid.addWidget(self.modified_database_edit, 1, 1)
        self.txt_path_button = QPushButton("Изменить…")
        self.txt_path_button.setObjectName("mutedButton")
        self.txt_path_button.clicked.connect(self.select_modified_database_output)
        output_grid.addWidget(self.txt_path_button, 1, 2)
        output_grid.setColumnStretch(1, 1)
        compare_card.body.addLayout(output_grid)

        self.compare_reports_button = QPushButton("Сравнить Excel-отчёты")
        self.compare_reports_button.setObjectName("mutedButton")
        self.compare_reports_button.clicked.connect(self.open_result_comparison)

    # Собирает настройки состава отчёта, чтобы сохранять только выбранные разделы.
    def _build_lists_tab(self) -> None:
        self.lists_tab = QWidget()
        self.lists_tab.setObjectName("tabPage")
        lists_root = QVBoxLayout(self.lists_tab)
        lists_root.setContentsMargins(6, 6, 6, 6)
        lists_root.setSpacing(7)
        report_lists_card = SectionCard("Списки точных совпадений")
        self.report_lists_card = report_lists_card
        report_lists_hint = QLabel("Выберите, какие листы будут созданы в итоговом Excel-отчёте.")
        report_lists_hint.setObjectName("cardDescription")
        report_lists_hint.setWordWrap(True)
        report_lists_card.body.addWidget(report_lists_hint)
        report_lists_grid = QGridLayout()
        report_lists_grid.setHorizontalSpacing(12)
        report_lists_grid.setVerticalSpacing(5)
        self.report_lists_grid = report_lists_grid
        self.report_substances_check = QCheckBox("Вещества — отдельный список")
        self.report_foreign_agents_check = QCheckBox("Иностранные агенты — отдельный список")
        self.report_combined_check = QCheckBox("Все совпадения — общий список")
        self.report_summary_check = QCheckBox("Сводка по результатам проверки")
        self.report_list_checks = (
            self.report_substances_check,
            self.report_foreign_agents_check,
            self.report_combined_check,
            self.report_summary_check,
        )
        report_lists_grid.addWidget(self.report_substances_check, 0, 0)
        report_lists_grid.addWidget(self.report_foreign_agents_check, 0, 1)
        report_lists_grid.addWidget(self.report_combined_check, 1, 0)
        report_lists_grid.addWidget(self.report_summary_check, 1, 1)
        report_lists_grid.setColumnStretch(0, 1)
        report_lists_grid.setColumnStretch(1, 1)
        report_lists_card.body.addLayout(report_lists_grid)
        report_format_row = QGridLayout()
        report_format_row.setHorizontalSpacing(7)
        report_format_row.setVerticalSpacing(4)
        self.report_format_layout = report_format_row
        self.report_deduplicate_check = QCheckBox("Объединять дубли записей")
        report_format_row.addWidget(self.report_deduplicate_check, 0, 0)
        self.report_sort_label = QLabel("Сортировка:")
        self.report_sort_label.setObjectName("fieldLabel")
        report_format_row.addWidget(self.report_sort_label, 0, 1)
        self.report_sort_combo = QComboBox()
        self.report_sort_combo.addItem("По номеру записи", "record")
        self.report_sort_combo.addItem("По названию", "title")
        self.report_sort_combo.addItem("По автору", "author")
        self.report_sort_combo.addItem("По ISBN", "isbn")
        report_format_row.addWidget(self.report_sort_combo, 0, 2)
        report_format_row.setColumnStretch(2, 1)
        report_lists_card.body.addLayout(report_format_row)
        lists_root.setAlignment(Qt.AlignmentFlag.AlignTop)


    # Собирает настройки служебных полей для записи меток.
    def _build_markers_tab(self) -> None:
        marker_card = SectionCard("Метки в ИРБИС")
        self.marker_card = marker_card
        marker_grid = QGridLayout()
        marker_grid.setHorizontalSpacing(6)
        marker_grid.setVerticalSpacing(4)
        marker_grid.addWidget(QLabel("Тип метки"), 0, 0)
        marker_grid.addWidget(QLabel("Поле"), 0, 1)
        marker_grid.addWidget(QLabel("Содержимое метки"), 0, 2)
        self.substance_marker_check = QCheckBox("Вещества")
        self.substance_field_spin = self._make_field_spin()
        self.substance_marker_edit = QLineEdit()
        self.substance_marker_edit.setObjectName("settingsField")
        self.foreign_marker_check = QCheckBox("Иноагенты — авторы")
        self.foreign_field_spin = self._make_field_spin()
        self.foreign_marker_edit = QLineEdit()
        self.foreign_marker_edit.setObjectName("settingsField")
        self.foreign_marker_edit.setToolTip(
            "{name} будет заменено на совпавшего автора; названия организаций и проектов не подставляются"
        )
        self.foreign_organization_marker_check = QCheckBox("Иноагенты — организации")
        self.foreign_organization_field_spin = self._make_field_spin()
        self.foreign_organization_marker_edit = QLineEdit()
        self.foreign_organization_marker_edit.setObjectName("settingsField")
        self.foreign_organization_marker_edit.setToolTip(
            "{name} будет заменено на название организации или проекта из реестра"
        )
        self.age_marker_check = QCheckBox("Все найденные записи")
        self.age_field_spin = self._make_field_spin()
        self.age_marker_edit = QLineEdit()
        self.age_marker_edit.setObjectName("settingsField")
        rows = [
            (self.substance_marker_check, self.substance_field_spin, self.substance_marker_edit),
            (self.foreign_marker_check, self.foreign_field_spin, self.foreign_marker_edit),
            (
                self.foreign_organization_marker_check,
                self.foreign_organization_field_spin,
                self.foreign_organization_marker_edit,
            ),
            (self.age_marker_check, self.age_field_spin, self.age_marker_edit),
        ]
        for row, (check, spin, edit) in enumerate(rows, start=1):
            marker_grid.addWidget(check, row, 0)
            marker_grid.addWidget(spin, row, 1)
            marker_grid.addWidget(edit, row, 2)
        marker_grid.setColumnStretch(2, 1)
        marker_card.body.addLayout(marker_grid)


    # Собирает запуск, журнал и страницу настроек приложения.
    def _build_results_tab(self) -> None:
        self.results_tab = QWidget()
        self.results_tab.setObjectName("tabPage")
        results_root = QVBoxLayout(self.results_tab)
        results_root.setContentsMargins(6, 6, 6, 6)
        results_root.setSpacing(7)
        self.actions_card = QFrame()
        self.actions_card.setObjectName("actionCard")
        self.actions_layout = QVBoxLayout(self.actions_card)
        self.actions_layout.setContentsMargins(4, 4, 4, 4)
        self.actions_layout.setSpacing(4)
        local_start_row = QHBoxLayout()
        local_start_row.setSpacing(4)
        self.start_button = QPushButton("Запустить проверку")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_comparison)
        local_start_row.addWidget(self.start_button, 1)
        self.cleanup_button = QPushButton("Удалить метки из ИРБИС")
        self.cleanup_button.setObjectName("dangerButton")
        self.cleanup_button.clicked.connect(self.clean_markers)
        for button in (self.start_button, self.cleanup_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        local_start_row.addWidget(self.cleanup_button, 1)
        self.actions_layout.addLayout(local_start_row)
        self.actions_buttons_layout = QGridLayout()
        self.actions_buttons_layout.setHorizontalSpacing(4)
        self.actions_buttons_layout.setVerticalSpacing(4)
        # Служебные объекты остаются для логики состояния, но эти действия
        # больше не выводятся в интерфейсе блока запуска.
        self.open_button = QPushButton("Открыть Excel-отчёт", self.actions_card)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_report)
        self.open_modified_database_button = QPushButton("Открыть TXT-копию")
        self.open_modified_database_button.setObjectName("mutedButton")
        self.open_modified_database_button.setEnabled(False)
        self.open_modified_database_button.clicked.connect(self.open_modified_database)
        self.write_irbis_button = QPushButton("Отправить TXT в ИРБИС")
        self.write_irbis_button.setObjectName("secondaryButton")
        self.write_irbis_button.setEnabled(False)
        self.write_irbis_button.clicked.connect(self.apply_results_to_irbis)
        action_buttons = [self.open_modified_database_button, self.write_irbis_button]
        for i, button in enumerate(action_buttons):
            self.actions_buttons_layout.addWidget(button, i // 2, i % 2)
        for column in range(2):
            self.actions_buttons_layout.setColumnStretch(column, 1)
        self.action_buttons = action_buttons
        self.actions_layout.addLayout(self.actions_buttons_layout)

        self.progress = QProgressBar()
        self.progress.setObjectName("mainProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumWidth(0)
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.progress.hide()
        self.actions_layout.addWidget(self.progress)
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(4)
        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_row.addWidget(self.status_label, 1)
        self.actions_layout.addLayout(self.status_row)
        results_root.addWidget(self.actions_card)

        log_card = QFrame()
        self.log_card = log_card
        log_card.setObjectName("sectionCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(4, 4, 4, 4)
        log_layout.setSpacing(4)
        log_header = QHBoxLayout()
        self.log_header = log_header
        log_header.setSpacing(6)
        log_title = QLabel("Журнал")
        log_title.setObjectName("cardTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.export_journal_button = QPushButton("Сохранить журнал")
        self.export_journal_button.setObjectName("mutedButton")
        self.export_journal_button.setToolTip("Сохранить отображаемые строки журнала в TXT-файл")
        self.export_journal_button.clicked.connect(self.export_run_journal)
        log_header.addWidget(self.export_journal_button)
        log_layout.addLayout(log_header)
        log_filters = QHBoxLayout()
        log_filters.setSpacing(4)
        self.journal_filter_combo = QComboBox()
        self.journal_filter_combo.addItem("Все записи", "all")
        self.journal_filter_combo.addItem("Ошибки", "errors")
        self.journal_filter_combo.addItem("Совпадения", "matches")
        self.journal_filter_combo.addItem("Изменения ИРБИС", "changes")
        self.journal_filter_combo.setMinimumWidth(150)
        self.journal_filter_combo.currentIndexChanged.connect(self._refresh_run_log_view)
        self.journal_search_edit = QLineEdit()
        self.journal_search_edit.setClearButtonEnabled(True)
        self.journal_search_edit.setPlaceholderText("Поиск по журналу или MFN")
        self.journal_search_edit.textChanged.connect(self._refresh_run_log_view)
        log_filters.addWidget(self.journal_filter_combo)
        log_filters.addWidget(self.journal_search_edit, 1)
        log_layout.addLayout(log_filters)
        self.run_log = QTextEdit()
        self.run_log.setObjectName("logEdit")
        self.run_log.setReadOnly(True)
        self.run_log.setMinimumHeight(100)
        self.run_log.setPlaceholderText("Здесь будет отображаться ход подключения, загрузки и сравнения…")
        self.run_log.document().setMaximumBlockCount(self.RUN_JOURNAL_MAX_LINES)
        self._load_run_journal()
        log_layout.addWidget(self.run_log, 1)
        results_root.addWidget(log_card, 1)
