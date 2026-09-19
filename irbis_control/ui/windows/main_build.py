from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
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

from irbis_control import APP_TITLE
from irbis_control.paths import icon_path
from irbis_control.ui.components.widgets import (
    CompactTabWidget,
    DatabaseComboBox,
    LayoutHintWidget,
    MatchRulesEditor,
    SectionCard,
)
from irbis_control.ui.storage_paths import app_data_dir


class MainWindowBuildMixin:
    def _build_ui(self) -> None:
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("mainScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(self.scroll_area)

        central = LayoutHintWidget()
        central.setObjectName("centralPage")
        self.scroll_area.setWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(8)

        # Компактная шапка в стиле нового макета.
        self.header_card = QFrame()
        self.header_card.setObjectName("headerCard")
        self.header_layout = QHBoxLayout(self.header_card)
        self.header_layout.setContentsMargins(4, 3, 4, 3)
        self.header_layout.setSpacing(8)

        self.header_logo = QLabel()
        self.header_logo.setObjectName("appLogo")
        self.header_logo.setPixmap(QIcon(icon_path("irbis64_control_icon.png")).pixmap(28, 28))
        self.header_logo.setFixedSize(30, 30)
        self.header_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_layout.addWidget(self.header_logo)

        header_text = QVBoxLayout()
        header_text.setContentsMargins(2, 0, 0, 0)
        header_text.setSpacing(2)
        self.main_title = QLabel(APP_TITLE)
        self.main_title.setObjectName("mainTitle")
        self.subtitle_primary = QLabel("Проверка и контроль библиотечных баз")
        self.subtitle_primary.setObjectName("subtitle")
        header_text.addWidget(self.main_title)
        header_text.addWidget(self.subtitle_primary)
        self.header_layout.addLayout(header_text, 1)

        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(4)

        self.start_button = QPushButton("Запустить проверку")
        self.start_button.setObjectName("headerStartButton")
        self.start_button.setMinimumWidth(0)
        self.start_button.clicked.connect(self.start_comparison)
        self.header_actions.addWidget(self.start_button)

        self.marker_settings_button = QPushButton("Настройки")
        self.marker_settings_button.setObjectName("settingsTab")
        self.marker_settings_button.setCheckable(True)
        self.marker_settings_button.clicked.connect(self.open_application_settings)
        self.header_actions.addWidget(self.marker_settings_button)

        self.useful_links_button = QPushButton("Полезные ссылки")
        self.useful_links_button.setObjectName("headerButton")
        self.useful_links_button.clicked.connect(self.open_useful_links)
        self.header_actions.addWidget(self.useful_links_button)

        self.update_button = QPushButton("Проверить обновление")
        self.update_button.setObjectName("headerButton")
        self.update_button.clicked.connect(self.check_updates)
        self.header_actions.addWidget(self.update_button)

        self.header_layout.addLayout(self.header_actions)
        self.root_layout.addWidget(self.header_card)
        self.header_card.hide()

        self.workflow_tabs = CompactTabWidget()
        self.workflow_tabs.setObjectName("workflowTabs")
        self.workflow_tabs.setDocumentMode(True)
        self.workflow_tabs.setUsesScrollButtons(True)
        self.workflow_tabs.tabBar().setExpanding(False)
        self.workflow_tabs.tabBar().setDrawBase(False)
        self.workflow_tabs.tabBar().setMovable(False)
        self.workflow_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.workflow_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        # Кнопка настроек остаётся справа от вкладок и доступна с любой страницы.
        self.workflow_tabs.setCornerWidget(
            self.marker_settings_button,
            Qt.Corner.TopRightCorner,
        )
        self.root_layout.addWidget(self.workflow_tabs, 1)

        self.section_cards: list[SectionCard] = []
        self._build_connection_tab()
        compare_card, utility_row = self._build_sources_tab()
        self._build_lists_tab(compare_card, utility_row)
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
            self.create_excel_report_check,
            self.report_only_check,
            *self.report_list_checks,
            self.report_deduplicate_check,
        ):
            checkbox.toggled.connect(self._queue_report_settings_autosave)
        self.report_sort_combo.currentIndexChanged.connect(self._queue_report_settings_autosave)
        self._marker_autosave_timer = QTimer(self)
        self._marker_autosave_timer.setSingleShot(True)
        self._marker_autosave_timer.timeout.connect(self._autosave_marker_settings)
        self.match_rules_editor.changed.connect(self._queue_marker_settings_autosave)
        for widget in (
            self.substance_marker_edit,
            self.foreign_marker_edit,
            self.age_marker_edit,
            self.substance_field_spin,
            self.foreign_field_spin,
            self.age_field_spin,
        ):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._queue_marker_settings_autosave)
            else:
                widget.valueChanged.connect(self._queue_marker_settings_autosave)
        self._update_database_summary()
        self._update_foreign_agents_summary()
        self._update_excel_summary()
        self._set_default_outputs(force=False)
        self._apply_responsive_layout(force=True)

    # Собирает параметры подключения и состояние сервера на одной вкладке.
    def _build_connection_tab(self) -> None:
        self.irbis_tab = QWidget()
        self.irbis_tab.setObjectName("tabPage")
        irbis_root = QVBoxLayout(self.irbis_tab)
        irbis_root.setContentsMargins(6, 6, 6, 0)
        irbis_root.setSpacing(7)

        intro = QLabel("Подключитесь к ИРБИС и выберите базу.")
        self.irbis_intro = intro
        intro.setObjectName("tabIntro")
        intro.setWordWrap(True)
        irbis_root.addWidget(intro)

        irbis_columns = QGridLayout()
        irbis_columns.setHorizontalSpacing(6)
        irbis_columns.setVerticalSpacing(5)
        self.irbis_columns = irbis_columns

        connection_card = SectionCard("Параметры подключения", "")
        form = QGridLayout()
        form.setHorizontalSpacing(7)
        form.setVerticalSpacing(4)
        self.irbis_host_edit = QLineEdit()
        self.irbis_port_spin = QSpinBox()
        self.irbis_port_spin.setRange(1, 65535)
        self.irbis_port_spin.setValue(6666)
        self.irbis_port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.irbis_login_edit = QLineEdit()
        self.irbis_password_edit = QLineEdit()
        self.irbis_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.irbis_show_password = self.irbis_password_edit.addAction(
            self._asset_icon("eye.svg"),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.irbis_show_password.setCheckable(True)
        self.irbis_show_password.setText("Показать пароль")
        self.irbis_show_password.setToolTip("Показать пароль")

        def toggle_irbis_password(visible: bool) -> None:
            self.irbis_password_edit.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
            self.irbis_show_password.setIcon(self._asset_icon("eye-crossed.svg" if visible else "eye.svg"))
            hint = "Скрыть пароль" if visible else "Показать пароль"
            self.irbis_show_password.setText(hint)
            self.irbis_show_password.setToolTip(hint)

        self.irbis_show_password.toggled.connect(toggle_irbis_password)
        self.irbis_db_combo = DatabaseComboBox()
        self.irbis_db_combo.setToolTip("Список загружается с сервера ИРБИС из доступных баз АРМ Каталогизатор.")
        self.irbis_db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.irbis_db_combo.setMinimumContentsLength(18)
        self.irbis_db_combo.currentIndexChanged.connect(
            lambda _index: self._update_direct_mode_ui() if hasattr(self, "direct_source_label") else None
        )
        self.irbis_refresh_databases_action = self.irbis_db_combo.refresh_action
        self.irbis_refresh_databases_action.setText("Обновить список баз")
        self.irbis_refresh_databases_action.setToolTip("Заново получить список существующих баз с сервера ИРБИС")
        self.irbis_refresh_databases_action.triggered.connect(lambda: self._start_irbis_operation("databases"))
        self.irbis_query_edit = QLineEdit("I=$")
        self.irbis_query_edit.setToolTip("Поисковое выражение ИРБИС. По умолчанию используется I=$.")
        self.irbis_read_workers_spin = QSpinBox()
        self.irbis_read_workers_spin.setRange(1, 8)
        self.irbis_read_workers_spin.setValue(4)
        self.irbis_read_workers_spin.setSuffix(" пот.")
        self.irbis_read_workers_spin.setToolTip(
            "Количество параллельных сеансов для чтения записей. "
            "4 — рекомендуется; 1 — режим совместимости; 6–8 — быстрее на мощном сервере."
        )
        self.irbis_read_workers_spin.setMaximumWidth(120)
        for edit in (self.irbis_host_edit, self.irbis_login_edit, self.irbis_password_edit, self.irbis_query_edit):
            edit.setObjectName("settingsField")

        self.irbis_read_workers_spin.hide()  # используется только старым режимом получения TXT-снимка

        # На обычной ширине параметры собраны попарно: это экономит высоту,
        # но не уменьшает сами поля. На узком окне раскладка автоматически
        # возвращается к привычной форме «подпись — поле».
        self.irbis_connection_form = form
        self.irbis_field_labels = {}
        for key, text in (
            ("host", "Сервер"),
            ("port", "Порт"),
            ("login", "Логин"),
            ("password", "Пароль"),
            ("database", "База"),
            ("query", "Запрос"),
        ):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            self.irbis_field_labels[key] = label

        self.irbis_password_box = QWidget()
        password_row = QHBoxLayout(self.irbis_password_box)
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(self.irbis_password_edit, 1)

        self.irbis_database_box = QWidget()
        database_row = QHBoxLayout(self.irbis_database_box)
        database_row.setContentsMargins(0, 0, 0, 0)
        database_row.setSpacing(0)
        database_row.addWidget(self.irbis_db_combo, 1)

        self._reflow_irbis_connection_form(False)
        connection_card.body.addLayout(form)
        self.connection_card = connection_card
        irbis_columns.addWidget(connection_card, 0, 0)

        base_card = SectionCard("Режим работы", "")
        self.direct_irbis_checkbox = QCheckBox()
        self.direct_irbis_checkbox.setAccessibleName("Работать напрямую с ИРБИС")
        self.direct_irbis_checkbox.setChecked(True)
        self.direct_irbis_checkbox.setToolTip(
            "Без создания полной TXT-копии: чтение пакетами с сервера и запись найденных меток сразу в ИРБИС."
        )
        self.direct_irbis_label = QLabel("Работать напрямую с ИРБИС")
        self.direct_irbis_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.direct_irbis_label.setBuddy(self.direct_irbis_checkbox)
        self.direct_irbis_label.setToolTip(self.direct_irbis_checkbox.toolTip())
        self.direct_irbis_box = QWidget()
        direct_mode_row = QHBoxLayout(self.direct_irbis_box)
        direct_mode_row.setContentsMargins(0, 0, 0, 0)
        direct_mode_row.setSpacing(5)
        direct_mode_row.addWidget(
            self.direct_irbis_checkbox,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        direct_mode_row.addWidget(
            self.direct_irbis_label,
            1,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        base_card.body.addWidget(self.direct_irbis_box)

        direct_note = QLabel("Чтение настраивается автоматически; запись выполняется безопасно по одной MFN.")
        self.direct_note = direct_note
        direct_note.setObjectName("cardDescription")
        direct_note.setWordWrap(True)
        base_card.body.addWidget(direct_note)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(4)
        batch_label = QLabel("Пакет чтения")
        batch_label.setObjectName("fieldLabel")
        batch_row.addWidget(batch_label)
        self.irbis_page_size_spin = QSpinBox()
        self.irbis_page_size_spin.setRange(100, 2000)
        self.irbis_page_size_spin.setSingleStep(100)
        self.irbis_page_size_spin.setValue(500)
        self.irbis_page_size_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.irbis_page_size_spin.setSuffix(" зап.")
        self.irbis_page_size_spin.setToolTip(
            "Подбирается кнопкой «Тест»; при необходимости значение можно изменить вручную."
        )
        batch_row.addWidget(self.irbis_page_size_spin)
        self.irbis_tune_read_button = QPushButton("Тест")
        self.irbis_tune_read_button.setObjectName("mutedButton")
        self.irbis_tune_read_button.setToolTip(
            "Проверить доступные размеры пакета чтения и автоматически выбрать максимальный стабильный"
        )
        self.irbis_tune_read_button.clicked.connect(lambda: self._start_irbis_operation("tune_read"))
        batch_row.addWidget(self.irbis_tune_read_button)
        batch_row.addStretch()
        base_card.body.addLayout(batch_row)

        self.irbis_base_status = QLabel("Прямой режим: TXT-копия не требуется")
        self.irbis_base_status.setObjectName("statusLabel")
        self.irbis_base_status.setWordWrap(True)
        base_card.body.addWidget(self.irbis_base_status)

        self.irbis_local_hint = QLabel("Для локальной работы выберите «TXT-файл» в поле источника.")
        self.irbis_local_hint.setObjectName("cardDescription")
        self.irbis_local_hint.setWordWrap(True)
        base_card.body.addWidget(self.irbis_local_hint)

        # Служебные поля старого режима оставлены скрытыми для совместимости с
        # существующими настройками и ручной отправкой старых снимков.
        self.irbis_snapshot_edit = QLineEdit(str(app_data_dir() / "direct_database.txt"))
        self.irbis_snapshot_edit.hide()
        self.irbis_manifest_edit = QLineEdit(str(app_data_dir() / "direct_database.map.json"))
        self.irbis_manifest_edit.hide()
        self.base_card = base_card
        irbis_columns.addWidget(base_card, 0, 1)
        irbis_columns.setColumnStretch(0, 3)
        irbis_columns.setColumnStretch(1, 2)
        irbis_root.addLayout(irbis_columns)

        irbis_actions = QFrame()
        self.irbis_actions = irbis_actions
        irbis_actions.setObjectName("irbisActions")
        ia = QVBoxLayout(irbis_actions)
        ia.setContentsMargins(0, 0, 0, 0)
        ia.setSpacing(4)
        action_box = QFrame()
        action_box.setObjectName("actionCard")
        action_row = QGridLayout(action_box)
        action_row.setContentsMargins(5, 5, 5, 5)
        action_row.setHorizontalSpacing(4)
        action_row.setVerticalSpacing(4)
        self.irbis_action_layout = action_row
        self.irbis_test_button = QPushButton("Подключиться")
        self.irbis_test_button.setObjectName("primaryButton")
        self.irbis_test_button.setToolTip("Подключиться к ИРБИС и загрузить список доступных баз")
        self.irbis_test_button.clicked.connect(lambda: self._start_irbis_operation("test"))
        self.irbis_fetch_button = QPushButton("Получить TXT-копию")
        self.irbis_fetch_button.setObjectName("mutedButton")
        self.irbis_fetch_button.clicked.connect(lambda: self._start_irbis_operation("fetch"))
        self.irbis_fetch_button.hide()
        self.irbis_next_button = QPushButton("Далее: источники →")
        self.irbis_next_button.setObjectName("primaryButton")
        self.irbis_next_button.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(1))
        self.irbis_next_button.hide()
        action_row.addWidget(self.irbis_test_button, 0, 0)
        action_row.setColumnStretch(0, 1)
        ia.addWidget(action_box)
        self.irbis_progress = QProgressBar()
        self.irbis_progress.setRange(0, 100)
        self.irbis_progress.setValue(0)
        self.irbis_progress.hide()
        action_row.addWidget(self.irbis_progress, 1, 0)
        self.irbis_status_box = QWidget()
        self.irbis_status_box.setObjectName("irbisStateRow")
        irbis_status_layout = QHBoxLayout(self.irbis_status_box)
        irbis_status_layout.setContentsMargins(0, 0, 0, 0)
        irbis_status_layout.setSpacing(6)
        self.irbis_status_dot = QLabel()
        self.irbis_status_dot.setObjectName("irbisStateDot")
        self.irbis_status_dot.setProperty("state", "error")
        self.irbis_status_dot.setFixedSize(10, 10)
        self.irbis_status = QLabel("Готово к подключению")
        self.irbis_status.setObjectName("cardTitle")
        irbis_status_layout.addWidget(self.irbis_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        irbis_status_layout.addWidget(self.irbis_status, 1)
        action_row.addWidget(self.irbis_status_box, 2, 0)
        irbis_root.addWidget(irbis_actions)
        # Не растягиваем вкладку пустым spacer-ом: после статуса не должно быть
        # искусственного большого нижнего отступа. Свободная высота остаётся
        # только если пользователь сам увеличит окно.
        irbis_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.irbis_tab, "Подключение")

    # Собирает источники данных и правила, по которым будут сверяться записи.
    def _build_sources_tab(self) -> tuple[SectionCard, QHBoxLayout]:
        self.files_tab = QWidget()
        self.files_tab.setObjectName("tabPage")
        files_root = QVBoxLayout(self.files_tab)
        files_root.setContentsMargins(6, 6, 6, 6)
        files_root.setSpacing(7)
        files_intro = QLabel("Выберите источник библиографических записей. В прямом режиме TXT-база не требуется.")
        self.files_intro = files_intro
        files_intro.setObjectName("tabIntro")
        files_intro.setWordWrap(True)
        files_root.addWidget(files_intro)

        self.database_card = SectionCard("Источник записей", "")
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
        self.source_useful_links_button = QPushButton("Справочные сайты")
        self.source_useful_links_button.setObjectName("mutedButton")
        self.source_useful_links_button.setToolTip("Открыть полезные ссылки на справочные и экспертные материалы")
        self.source_useful_links_button.clicked.connect(self.open_useful_links)
        source_summary_row.addWidget(
            self.source_useful_links_button,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        self.database_card.body.addLayout(source_summary_row)
        self.database_edit = QLineEdit()
        self.database_edit.hide()
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
        files_root.addWidget(self.database_card)

        sources_row = QGridLayout()
        sources_row.setHorizontalSpacing(6)
        sources_row.setVerticalSpacing(5)
        self.sources_grid = sources_row
        self.foreign_agents_card = SectionCard("Реестр иностранных агентов", "")
        self.foreign_agents_edit = QLineEdit()
        self.foreign_agents_edit.hide()
        self.foreign_agents_list = QListWidget()
        self.foreign_agents_list.setObjectName("compactList")
        self.foreign_agents_list.setMaximumHeight(58)
        self.foreign_agents_list.setMinimumHeight(38)
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
        sources_row.addWidget(self.foreign_agents_card, 0, 0)

        self.excel_card = SectionCard("Реестр по наркотическим веществам", "")
        self.excel_list = QListWidget()
        self.excel_list.setObjectName("compactList")
        self.excel_list.setMaximumHeight(58)
        self.excel_list.setMinimumHeight(38)
        self.excel_summary_edit = QLineEdit()
        self.excel_summary_edit.hide()
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
        sources_row.addWidget(self.excel_card, 0, 1)
        sources_row.setColumnStretch(0, 1)
        sources_row.setColumnStretch(1, 1)
        files_root.addLayout(sources_row)

        match_settings_card = SectionCard("Настройка совпадений", "")
        self.match_settings_card = match_settings_card
        self.match_rules_editor = MatchRulesEditor()
        match_settings_card.body.addWidget(self.match_rules_editor)
        files_root.addWidget(match_settings_card)

        compare_card = SectionCard("Результаты", "")
        self.compare_card = compare_card
        compare_row = QGridLayout()
        compare_row.setHorizontalSpacing(7)
        compare_row.setVerticalSpacing(4)
        self.compare_controls = compare_row
        self.create_excel_report_check = QCheckBox("Создавать Excel-отчёт")
        self.create_excel_report_check.toggled.connect(self._update_report_controls)
        self.report_only_check = QCheckBox("Только отчёт — не добавлять метки")
        self.report_only_check.toggled.connect(self._update_report_only)
        compare_card.body.addLayout(compare_row)

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

        utility_row = QGridLayout()
        utility_row.setHorizontalSpacing(4)
        utility_row.setVerticalSpacing(4)
        self.utility_controls = utility_row
        self.compare_reports_button = QPushButton("Сравнить Excel-отчёты")
        self.compare_reports_button.setObjectName("mutedButton")
        self.compare_reports_button.clicked.connect(self.open_result_comparison)
        next_lists = QPushButton("Далее: списки →")
        self.next_lists_button = next_lists
        next_lists.setObjectName("primaryButton")
        next_lists.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(2))
        files_root.addWidget(next_lists)
        files_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.files_tab, "Источники")

        self.section_cards.extend(
            [self.database_card, self.foreign_agents_card, self.excel_card, match_settings_card, compare_card]
        )
        return compare_card, utility_row

    # Собирает настройки состава отчёта, чтобы сохранять только выбранные разделы.
    def _build_lists_tab(self, compare_card: SectionCard, utility_row: QHBoxLayout) -> None:
        self.lists_tab = QWidget()
        self.lists_tab.setObjectName("tabPage")
        lists_root = QVBoxLayout(self.lists_tab)
        lists_root.setContentsMargins(6, 6, 6, 6)
        lists_root.setSpacing(7)
        self.lists_intro = QLabel("Настройте состав листов в итоговом Excel-отчёте.")
        self.lists_intro.setObjectName("tabIntro")
        self.lists_intro.setWordWrap(True)
        lists_root.addWidget(self.lists_intro)
        lists_root.addWidget(compare_card)

        report_lists_card = SectionCard("Списки точных совпадений", "")
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
        lists_root.addWidget(report_lists_card)

        report_create_card = SectionCard("Создание Excel-файла с совпадениями", "")
        self.report_create_card = report_create_card
        report_create_hint = QLabel("Создать выбранные списки совпадений без добавления меток в ИРБИС или TXT-базу.")
        report_create_hint.setObjectName("cardDescription")
        report_create_hint.setWordWrap(True)
        report_create_card.body.addWidget(report_create_hint)
        self.create_matches_excel_button = QPushButton("Создать Excel-файл с совпадениями")
        self.create_matches_excel_button.setObjectName("primaryButton")
        self.create_matches_excel_button.setProperty("compact", True)
        self.create_matches_excel_button.clicked.connect(self.create_matches_excel)
        for button in (self.create_matches_excel_button, self.compare_reports_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        report_create_actions = QGridLayout()
        report_create_actions.setHorizontalSpacing(4)
        report_create_actions.setVerticalSpacing(4)
        report_create_actions.addWidget(self.create_matches_excel_button, 0, 0)
        report_create_actions.addWidget(self.compare_reports_button, 0, 1)
        report_create_actions.setColumnStretch(0, 1)
        report_create_actions.setColumnStretch(1, 1)
        report_create_card.body.addLayout(report_create_actions)
        lists_root.addWidget(report_create_card)

        next_marks = QPushButton("Далее: метки →")
        next_marks.setObjectName("primaryButton")
        next_marks.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(3))
        self.next_marks_button = next_marks
        lists_root.addLayout(utility_row)
        lists_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.lists_tab, "Списки")

        self.section_cards.extend([report_lists_card, report_create_card])

    # Собирает настройки служебных полей для записи меток.
    def _build_markers_tab(self) -> None:
        self.run_tab = QWidget()
        self.run_tab.setObjectName("tabPage")
        run_root = QVBoxLayout(self.run_tab)
        run_root.setContentsMargins(6, 6, 6, 6)
        run_root.setSpacing(7)
        run_intro = QLabel("Настройте поля и содержимое меток.")
        self.markers_intro = run_intro
        run_intro.setObjectName("tabIntro")
        run_intro.setWordWrap(True)
        run_root.addWidget(run_intro)

        marker_card = SectionCard("Метки в ИРБИС", "")
        self.marker_card = marker_card
        marker_grid = QGridLayout()
        marker_grid.setHorizontalSpacing(6)
        marker_grid.setVerticalSpacing(4)
        marker_grid.addWidget(QLabel("Тип совпадения"), 0, 0)
        marker_grid.addWidget(QLabel("Поле"), 0, 1)
        marker_grid.addWidget(QLabel("Содержимое метки"), 0, 2)
        self.substance_field_spin = self._make_field_spin()
        self.substance_marker_edit = QLineEdit()
        self.substance_marker_edit.setObjectName("settingsField")
        self.foreign_field_spin = self._make_field_spin()
        self.foreign_marker_edit = QLineEdit()
        self.foreign_marker_edit.setObjectName("settingsField")
        self.foreign_marker_edit.setToolTip(
            "{name} будет заменено на совпавшего автора; названия организаций и проектов не подставляются"
        )
        self.age_field_spin = self._make_field_spin()
        self.age_marker_edit = QLineEdit()
        self.age_marker_edit.setObjectName("settingsField")
        rows = [
            ("Вещества", self.substance_field_spin, self.substance_marker_edit),
            ("Иностранные агенты", self.foreign_field_spin, self.foreign_marker_edit),
            ("Все найденные записи", self.age_field_spin, self.age_marker_edit),
        ]
        for row, (name, spin, edit) in enumerate(rows, start=1):
            marker_grid.addWidget(QLabel(name), row, 0)
            marker_grid.addWidget(spin, row, 1)
            marker_grid.addWidget(edit, row, 2)
        marker_grid.setColumnStretch(2, 1)
        marker_card.body.addLayout(marker_grid)
        run_root.addWidget(marker_card)

        next_run_tab = QPushButton("Далее: запуск и журнал →")
        self.next_run_button = next_run_tab
        next_run_tab.setObjectName("primaryButton")
        next_run_tab.clicked.connect(lambda: self.workflow_tabs.setCurrentIndex(4))
        run_root.addWidget(next_run_tab)
        run_root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.workflow_tabs.addTab(self.run_tab, "Метки")

        self.section_cards.append(marker_card)

    # Собирает запуск, журнал и страницу настроек приложения.
    def _build_results_tab(self) -> None:
        self.results_tab = QWidget()
        self.results_tab.setObjectName("tabPage")
        results_root = QVBoxLayout(self.results_tab)
        results_root.setContentsMargins(6, 6, 6, 6)
        results_root.setSpacing(7)
        results_intro = QLabel("Запуск, прогресс, результаты и журнал работы.")
        self.results_intro = results_intro
        results_intro.setObjectName("tabIntro")
        results_intro.setWordWrap(True)
        results_root.addWidget(results_intro)

        self.actions_card = QFrame()
        self.actions_card.setObjectName("actionCard")
        self.actions_layout = QVBoxLayout(self.actions_card)
        self.actions_layout.setContentsMargins(4, 3, 4, 4)
        self.actions_layout.setSpacing(4)
        action_title_row = QHBoxLayout()
        action_title_row.setSpacing(6)
        action_title = QLabel("Управление запуском и результатами")
        self.action_title = action_title
        action_title.setObjectName("cardTitle")
        action_title_row.addWidget(action_title, 1)
        self.actions_layout.addLayout(action_title_row)
        action_hint = QLabel("Запустите проверку или удалите ранее добавленные метки из выбранной базы.")
        self.action_hint = action_hint
        action_hint.setObjectName("cardDescription")
        action_hint.setWordWrap(True)
        self.actions_layout.addWidget(action_hint)
        local_start_row = QHBoxLayout()
        local_start_row.setSpacing(4)
        self.run_tab_start_button = QPushButton("Запустить действие")
        self.run_tab_start_button.setObjectName("primaryButton")
        self.run_tab_start_button.clicked.connect(self.start_comparison)
        local_start_row.addWidget(self.run_tab_start_button, 1)
        self.cleanup_button = QPushButton("Удалить метки из ИРБИС")
        self.cleanup_button.setObjectName("dangerButton")
        self.cleanup_button.clicked.connect(self.clean_markers)
        for button in (self.run_tab_start_button, self.cleanup_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        local_start_row.addWidget(self.cleanup_button, 1)
        self.actions_layout.addLayout(local_start_row)
        self.actions_buttons_layout = QGridLayout()
        self.actions_buttons_layout.setHorizontalSpacing(4)
        self.actions_buttons_layout.setVerticalSpacing(4)
        # Служебные объекты остаются для логики состояния, но эти действия
        # больше не выводятся в интерфейсе блока запуска.
        self.cancel_button = QPushButton("Отменить", self.actions_card)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_comparison)
        self.cancel_button.hide()
        self.open_button = QPushButton("Открыть Excel-отчёт", self.actions_card)
        self.open_button.setEnabled(False)
        self.open_button.hide()
        self.open_modified_database_button = QPushButton("Открыть TXT-копию")
        self.open_modified_database_button.setObjectName("mutedButton")
        self.open_modified_database_button.setEnabled(False)
        self.open_modified_database_button.clicked.connect(self.open_modified_database)
        self.write_irbis_button = QPushButton("Отправить TXT в ИРБИС")
        self.write_irbis_button.setObjectName("secondaryButton")
        self.write_irbis_button.setEnabled(False)
        self.write_irbis_button.clicked.connect(self.apply_results_to_irbis)
        self.clear_all_button = QPushButton("Очистить всё", self.actions_card)
        self.clear_all_button.hide()
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
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setProperty("state", "idle")
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.hide()
        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_row.addWidget(self.status_dot)
        self.status_row.addWidget(self.status_label, 1)
        self.actions_layout.addLayout(self.status_row)
        results_root.addWidget(self.actions_card)

        log_card = QFrame()
        self.log_card = log_card
        log_card.setObjectName("sectionCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(4, 3, 4, 4)
        log_layout.setSpacing(4)
        log_header = QHBoxLayout()
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
        self.journal_filter_combo.setMaximumWidth(175)
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
        self.workflow_tabs.addTab(self.results_tab, "Запуск")
