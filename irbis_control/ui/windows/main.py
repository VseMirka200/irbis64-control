from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, QStandardPaths, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from irbis_control import APP_TITLE as APP_TITLE
from irbis_control.ui.message_box import AppMessageBox as QMessageBox
from irbis_control import __version__
from irbis_control.application.marker_settings import (
    DEFAULT_MARKER_SETTINGS as DEFAULT_MARKER_SETTINGS,
)
from irbis_control.application.marker_settings import (
    load_marker_settings as _load_marker_settings,
)
from irbis_control.application.marker_settings import (
    save_marker_settings as _save_marker_settings,
)
from irbis_control.application.settings import (
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    ApplicationSettings,
    load_application_settings,
    save_application_settings,
)
from irbis_control.application.updater import (
    GITHUB_REPOSITORY_URL,
    ReleaseAsset,
)
from irbis_control.core.matcher import (
    DEFAULT_AGE_MARKER,
    DEFAULT_AGE_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD,
    DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE,
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    EXTRA_MATCH_RULES,
)
from irbis_control.core.models import ComparisonSummary, MatchResult
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.infrastructure.nkp_source import (
    DRUG_CACHE_FILENAME,
    DRUG_META_FILENAME,
    FOREIGN_AGENTS_CACHE_FILENAME,
    FOREIGN_AGENTS_META_FILENAME,
    NKP_DRUG_PAGE_URL,
    NKP_FOREIGN_AGENTS_PAGE_URL,
)
from irbis_control.paths import icon_path, project_root
from irbis_control.ui.components.dialogs import (
    DEFAULT_USEFUL_LINKS as DEFAULT_USEFUL_LINKS,
)
from irbis_control.ui.components.dialogs import (
    ConfirmationMemoryDialog as ConfirmationMemoryDialog,
)
from irbis_control.ui.components.dialogs import (
    ProgressDialog as ProgressDialog,
)
from irbis_control.ui.components.dialogs import (
    ResultComparisonDialog as ResultComparisonDialog,
)
from irbis_control.ui.components.dialogs import (
    TextComparisonDialog as TextComparisonDialog,
)
from irbis_control.ui.components.dialogs import (
    UsefulLinksDialog as UsefulLinksDialog,
)
from irbis_control.ui.components.widgets import (
    CompactTabWidget as CompactTabWidget,
)
from irbis_control.ui.components.widgets import (
    DatabaseComboBox as DatabaseComboBox,
)
from irbis_control.ui.components.widgets import (
    LayoutHintWidget as LayoutHintWidget,
)
from irbis_control.ui.components.widgets import (
    ListResizeHandle as ListResizeHandle,
)
from irbis_control.ui.components.widgets import (
    MatchFieldsComboBox as MatchFieldsComboBox,
)
from irbis_control.ui.components.widgets import (
    MatchRulesEditor as MatchRulesEditor,
)
from irbis_control.ui.components.widgets import (
    SectionCard as SectionCard,
)
from irbis_control.ui.context_menu import install_context_menu_manager
from irbis_control.ui.locale import install_russian_ui
from irbis_control.ui.services.workers import (
    ComparisonWorker as ComparisonWorker,
)
from irbis_control.ui.services.workers import (
    DirectIrbisComparisonWorker as DirectIrbisComparisonWorker,
)
from irbis_control.ui.services.workers import (
    IrbisOperationWorker as IrbisOperationWorker,
)
from irbis_control.ui.services.workers import (
    UpdateWorker as UpdateWorker,
)
from irbis_control.ui.services.workers import (
    NkpRegistryRefreshWorker as NkpRegistryRefreshWorker,
)
from irbis_control.ui.storage_paths import app_data_dir as app_data_dir
from irbis_control.ui.storage_paths import application_settings_path, manual_review_memory_path
from irbis_control.ui.storage_paths import database_connector_config_path as database_connector_config_path
from irbis_control.ui.theme import (
    about_document_stylesheet,
    about_page_stylesheet,
    apply_about_title_font,
    apply_application_theme,
    prepare_application_ui,
)
from irbis_control.ui.windows.main_build import MainWindowBuildMixin
from irbis_control.ui.windows.main_layout import MainWindowLayoutMixin
from irbis_control.ui.windows.main_operations import MainWindowOperationsMixin
from irbis_control.ui.windows.main_run import MainWindowRunMixin

APP_VERSION = __version__


def window_state_path() -> Path:
    return app_data_dir() / "window_state.json"


def run_journal_path() -> Path:
    return app_data_dir() / "run_journal.log"


def _marker_settings_path() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    return folder / "marker_settings.json"


# Возвращает настройки из каталога данных текущего пользователя.
def load_marker_settings() -> dict[str, str | int | bool]:
    return _load_marker_settings(_marker_settings_path())


# Сохраняет настройки в каталоге данных текущего пользователя.
def save_marker_settings(settings: dict[str, str | int | bool]) -> None:
    path = _marker_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _save_marker_settings(path, settings)


# Проверяет значения служебных полей перед сохранением настроек меток.
class MarkerSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, str | int | bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        self.resize(620, 440)
        self.setMinimumSize(500, 380)
        if parent is not None:
            # Отдельные окна верхнего уровня не всегда наследуют таблицу стилей
            # главного окна на всех платформах и системных темах.
            self.setStyleSheet(parent.styleSheet())
        self.settings = dict(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Настройки проверки")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        search_title = QLabel("Параметры поиска")
        search_title.setObjectName("cardTitle")
        layout.addWidget(search_title)

        self.isbn_match_check = QCheckBox("Точные совпадения по ISBN")
        self.isbn_match_check.setChecked(bool(settings["use_isbn_matching"]))
        layout.addWidget(self.isbn_match_check)

        self.title_fallback_check = QCheckBox("Точные совпадения по названию и автору")
        self.title_fallback_check.setChecked(bool(settings["use_title_fallback"]))
        layout.addWidget(self.title_fallback_check)

        marker_title = QLabel("Пометки в TXT-копии")
        marker_title.setObjectName("cardTitle")
        layout.addWidget(marker_title)

        description = QLabel(
            "Для каждой пометки выберите номер поля и задайте его содержимое. "
            "Пустое содержимое отключает соответствующую пометку."
        )
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QGridLayout()
        form.setHorizontalSpacing(7)
        form.setVerticalSpacing(5)

        field_header = QLabel("Поле")
        field_header.setObjectName("fieldLabel")
        marker_header = QLabel("Содержимое")
        marker_header.setObjectName("fieldLabel")
        form.addWidget(field_header, 0, 1)
        form.addWidget(marker_header, 0, 2)

        self.substance_enabled_check = QCheckBox("Вещества")
        self.substance_enabled_check.setChecked(bool(settings["substance_marker_enabled"]))
        self.substance_field_spin = self._field_spin(int(settings["substance_marker_field"]))
        self.substance_edit = QLineEdit(str(settings["substance_marker"]))
        self.substance_edit.setObjectName("settingsField")
        self.substance_edit.setPlaceholderText(DEFAULT_SUBSTANCE_MARKER)
        form.addWidget(self.substance_enabled_check, 1, 0)
        form.addWidget(self.substance_field_spin, 1, 1)
        form.addWidget(self.substance_edit, 1, 2)

        self.foreign_enabled_check = QCheckBox("Иноагенты — авторы")
        self.foreign_enabled_check.setChecked(bool(settings["foreign_agent_marker_enabled"]))
        self.foreign_field_spin = self._field_spin(int(settings["foreign_agent_marker_field"]))
        self.foreign_edit = QLineEdit(str(settings["foreign_agent_marker_template"]))
        self.foreign_edit.setObjectName("settingsField")
        self.foreign_edit.setPlaceholderText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.foreign_edit.setToolTip("{name} будет заменено на совпавшего автора")
        form.addWidget(self.foreign_enabled_check, 2, 0)
        form.addWidget(self.foreign_field_spin, 2, 1)
        form.addWidget(self.foreign_edit, 2, 2)

        self.organization_enabled_check = QCheckBox("Иноагенты — организации")
        self.organization_enabled_check.setChecked(bool(settings["foreign_organization_marker_enabled"]))
        self.organization_field_spin = self._field_spin(int(settings["foreign_organization_marker_field"]))
        self.organization_edit = QLineEdit(str(settings["foreign_organization_marker_template"]))
        self.organization_edit.setObjectName("settingsField")
        self.organization_edit.setPlaceholderText(DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE)
        form.addWidget(self.organization_enabled_check, 3, 0)
        form.addWidget(self.organization_field_spin, 3, 1)
        form.addWidget(self.organization_edit, 3, 2)

        foreign_hint = QLabel("Используйте {name}, чтобы подставить совпавшего автора или название организации.")
        foreign_hint.setObjectName("cardDescription")
        foreign_hint.setWordWrap(True)
        form.addWidget(foreign_hint, 4, 2)

        self.age_enabled_check = QCheckBox("Все найденные записи")
        self.age_enabled_check.setChecked(bool(settings["age_marker_enabled"]))
        self.age_field_spin = self._field_spin(int(settings["age_marker_field"]))
        self.age_edit = QLineEdit(str(settings["age_marker"]))
        self.age_edit.setObjectName("settingsField")
        self.age_edit.setPlaceholderText(DEFAULT_AGE_MARKER)
        form.addWidget(self.age_enabled_check, 5, 0)
        form.addWidget(self.age_field_spin, 5, 1)
        form.addWidget(self.age_edit, 5, 2)
        form.setColumnStretch(2, 1)
        layout.addLayout(form)

        self.preview = QLabel()
        self.preview.setObjectName("cardDescription")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        for edit in (self.substance_edit, self.foreign_edit, self.organization_edit, self.age_edit):
            edit.textChanged.connect(self._update_preview)
        for spin in (
            self.substance_field_spin,
            self.foreign_field_spin,
            self.organization_field_spin,
            self.age_field_spin,
        ):
            spin.valueChanged.connect(self._update_preview)
        for check in (
            self.substance_enabled_check,
            self.foreign_enabled_check,
            self.organization_enabled_check,
            self.age_enabled_check,
        ):
            check.toggled.connect(self._update_preview)
            check.toggled.connect(self._update_control_states)
        self._update_control_states()
        self._update_preview()

        layout.addStretch()
        buttons = QHBoxLayout()
        reset_button = QPushButton("По умолчанию")
        reset_button.setObjectName("mutedButton")
        reset_button.clicked.connect(self._reset_defaults)
        buttons.addWidget(reset_button)
        buttons.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("mutedButton")
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton("Сохранить")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _field_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(value)
        spin.setPrefix("#")
        spin.setMinimumWidth(68)
        spin.setMaximumWidth(88)
        return spin

    def _values(self) -> dict[str, str | int | bool]:
        return {
            **{key: bool(self.settings.get(key, False)) for key in EXTRA_MATCH_RULES},
            "use_isbn_matching": self.isbn_match_check.isChecked(),
            "use_title_fallback": self.title_fallback_check.isChecked(),
            "use_fuzzy": bool(self.settings.get("use_fuzzy", True)),
            "fuzzy_threshold": int(self.settings.get("fuzzy_threshold", 92)),
            "create_excel_report": bool(self.settings.get("create_excel_report", True)),
            "report_substances": bool(self.settings.get("report_substances", True)),
            "report_foreign_agents": bool(self.settings.get("report_foreign_agents", True)),
            "report_combined": bool(self.settings.get("report_combined", False)),
            "report_summary": bool(self.settings.get("report_summary", False)),
            "report_deduplicate": bool(self.settings.get("report_deduplicate", True)),
            "report_sort": str(self.settings.get("report_sort", "record")),
            "report_only": bool(self.settings.get("report_only", False)),
            "substance_marker": self.substance_edit.text().strip(),
            "foreign_agent_marker_template": self.foreign_edit.text().strip(),
            "foreign_organization_marker_template": self.organization_edit.text().strip(),
            "age_marker": self.age_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "foreign_organization_marker_field": self.organization_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
            "substance_marker_enabled": self.substance_enabled_check.isChecked(),
            "foreign_agent_marker_enabled": self.foreign_enabled_check.isChecked(),
            "foreign_organization_marker_enabled": self.organization_enabled_check.isChecked(),
            "age_marker_enabled": self.age_enabled_check.isChecked(),
        }

    def _update_preview(self) -> None:
        values = self._values()
        foreign_preview = str(values["foreign_agent_marker_template"]).replace("{name}", "ИВАНОВ ИВАН ИВАНОВИЧ")
        organization_preview = str(values["foreign_organization_marker_template"]).replace(
            "{name}", "НАЗВАНИЕ ОРГАНИЗАЦИИ"
        )
        self.preview.setText(
            f"Пример: #{int(values['substance_marker_field']):03d}: "
            f"{values['substance_marker'] if values['substance_marker_enabled'] else 'отключена'}; "
            f"для иноагента — #{int(values['foreign_agent_marker_field']):03d}: "
            f"{foreign_preview if values['foreign_agent_marker_enabled'] else 'отключена'}; "
            f"организация — #{int(values['foreign_organization_marker_field']):03d}: "
            f"{organization_preview if values['foreign_organization_marker_enabled'] else 'отключена'}; "
            f"#{int(values['age_marker_field']):03d}: "
            f"{values['age_marker'] if values['age_marker_enabled'] else 'отключена'}."
        )

    def _update_control_states(self, *_args) -> None:
        groups = (
            (self.substance_enabled_check, self.substance_field_spin, self.substance_edit),
            (self.foreign_enabled_check, self.foreign_field_spin, self.foreign_edit),
            (self.organization_enabled_check, self.organization_field_spin, self.organization_edit),
            (self.age_enabled_check, self.age_field_spin, self.age_edit),
        )
        for check, field, edit in groups:
            field.setEnabled(check.isChecked())
            edit.setEnabled(check.isChecked())

    def _reset_defaults(self) -> None:
        self.isbn_match_check.setChecked(True)
        self.title_fallback_check.setChecked(True)
        self.substance_edit.setText(DEFAULT_SUBSTANCE_MARKER)
        self.foreign_edit.setText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.organization_edit.setText(DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE)
        self.age_edit.setText(DEFAULT_AGE_MARKER)
        self.substance_field_spin.setValue(DEFAULT_SUBSTANCE_MARKER_FIELD)
        self.foreign_field_spin.setValue(DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
        self.organization_field_spin.setValue(DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD)
        self.age_field_spin.setValue(DEFAULT_AGE_MARKER_FIELD)
        self.substance_enabled_check.setChecked(True)
        self.foreign_enabled_check.setChecked(True)
        self.organization_enabled_check.setChecked(True)
        self.age_enabled_check.setChecked(True)

    def _save(self) -> None:
        values = self._values()
        marker_values = [value for value in values.values() if isinstance(value, str)]
        if any("\n" in value or "\r" in value for value in marker_values):
            QMessageBox.warning(self, APP_TITLE, "Метка должна состоять из одной строки.")
            return
        if any(re.search(r"#\d{1,3}\s*:", value, re.IGNORECASE) for value in marker_values):
            QMessageBox.warning(self, APP_TITLE, "Введите содержимое метки без номера поля.")
            return
        try:
            save_marker_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить настройки:\n{exc}")
            return
        self.settings = values
        self.accept()


# Редактирует общие настройки и передаёт их окну только после сохранения.
class ApplicationSettingsPage(QWidget):
    saved = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(
        self,
        settings: ApplicationSettings,
        parent: MainWindow | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("tabPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(7)
        layout = QVBoxLayout()
        root.addLayout(layout, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        safety_card = SectionCard("Безопасность базы", "")
        layout.addWidget(safety_card)
        self.backup_check = QCheckBox("Создавать rollback-копию базы перед изменением записей")
        self.backup_check.setChecked(settings.create_database_backup)
        self.backup_check.setToolTip(
            "Рекомендуется оставить включённым: копия позволяет восстановить исходные поля MFN."
        )
        safety_card.body.addWidget(self.backup_check)

        appearance_card = SectionCard("Оформление", "")
        layout.addWidget(appearance_card)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(7)
        theme_label = QLabel("Тема")
        theme_label.setObjectName("fieldLabel")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Как в системе", THEME_SYSTEM)
        self.theme_combo.addItem("Светлая", THEME_LIGHT)
        self.theme_combo.addItem("Тёмная", THEME_DARK)
        theme_index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(max(0, theme_index))
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo, 1)
        appearance_card.body.addLayout(theme_row)

        sources_card = SectionCard("Онлайн-реестры НКП РГБ", "")
        layout.addWidget(sources_card)
        source_hint = QLabel(
            "Выберите, какие официальные реестры обновлять автоматически перед проверкой. "
            "Последняя успешно загруженная копия хранится локально и используется при недоступности сайта."
        )
        source_hint.setObjectName("cardDescription")
        source_hint.setWordWrap(True)
        sources_card.body.addWidget(source_hint)
        self.nkp_drug_check = QCheckBox("Реестр литературы по наркотическим веществам")
        self.nkp_drug_check.setChecked(settings.use_nkp_drug_registry)
        self.nkp_foreign_check = QCheckBox("Реестр изданий иностранных агентов")
        self.nkp_foreign_check.setChecked(settings.use_nkp_foreign_agents_registry)
        sources_card.body.addWidget(self.nkp_drug_check)
        sources_card.body.addWidget(self.nkp_foreign_check)
        source_actions = QHBoxLayout()
        source_actions.setSpacing(7)
        self.open_registry_cache_button = QPushButton("Открыть кэш")
        self.open_registry_cache_button.setObjectName("mutedButton")
        self.clear_registry_cache_button = QPushButton("Очистить кэш")
        self.clear_registry_cache_button.setObjectName("mutedButton")
        if parent is not None:
            self.open_registry_cache_button.clicked.connect(parent.open_registry_cache_folder)
            self.clear_registry_cache_button.clicked.connect(parent.clear_registry_cache)
        source_actions.addWidget(self.open_registry_cache_button)
        source_actions.addWidget(self.clear_registry_cache_button)
        source_actions.addStretch()
        sources_card.body.addLayout(source_actions)

        updates_card = SectionCard("Обновления", "")
        layout.addWidget(updates_card)
        self.auto_updates_check = QCheckBox("Проверять обновления на GitHub при запуске")
        self.auto_updates_check.setChecked(settings.check_updates_on_start)
        updates_card.body.addWidget(self.auto_updates_check)
        check_button = QPushButton("Проверить обновления")
        check_button.setObjectName("mutedButton")
        if parent is not None:
            check_button.clicked.connect(lambda: parent.check_updates(manual=True))

        about_button = QPushButton("О программе")
        about_button.setObjectName("mutedButton")
        about_button.clicked.connect(self._show_about)

        info_actions = QHBoxLayout()
        info_actions.setSpacing(7)
        info_actions.addWidget(about_button, 1)
        info_actions.addWidget(check_button, 1)
        layout.addLayout(info_actions)
        layout.addStretch()

        buttons = QHBoxLayout()
        self.reset_settings_button = QPushButton("По умолчанию")
        self.reset_settings_button.setObjectName("mutedButton")
        self.reset_settings_button.setToolTip("Вернуть рекомендуемые настройки; изменения применятся после сохранения")
        self.reset_settings_button.clicked.connect(self._reset_defaults)
        buttons.addWidget(self.reset_settings_button)
        buttons.addStretch()
        cancel_button = QPushButton("Отмена")
        cancel_button.clicked.connect(self.cancelled.emit)
        save_button = QPushButton("Сохранить")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        root.addLayout(buttons)

    def _reset_defaults(self) -> None:
        defaults = ApplicationSettings()
        self.backup_check.setChecked(defaults.create_database_backup)
        self.auto_updates_check.setChecked(defaults.check_updates_on_start)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(defaults.theme))
        self.nkp_drug_check.setChecked(defaults.use_nkp_drug_registry)
        self.nkp_foreign_check.setChecked(defaults.use_nkp_foreign_agents_registry)

    def _show_about(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"О программе — {APP_TITLE}")
        dialog.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        dialog.resize(620, 480)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(8)
        logo = QLabel()
        logo.setObjectName("aboutLogo")
        logo.setPixmap(QIcon(icon_path("irbis64_control_icon.png")).pixmap(40, 40))
        logo.setFixedSize(40, 40)
        header.addWidget(logo)
        title = QLabel(APP_TITLE)
        title.setObjectName("mainTitle")
        title.setWordWrap(True)
        apply_about_title_font(title)
        header.addWidget(title, 1)
        root.addLayout(header)
        about_page = QTextBrowser()
        about_page.setObjectName("aboutPage")
        about_page.setFrameShape(QFrame.Shape.NoFrame)
        about_page.setStyleSheet(about_page_stylesheet())
        about_page.document().setDocumentMargin(0)
        about_page.document().setDefaultStyleSheet(about_document_stylesheet())
        about_page.setOpenExternalLinks(True)
        about_page.setHtml(f"""
            <p>Версия {APP_VERSION} · Разработчик: VseMirka200</p>
            <p>Помощник библиотекаря для проверки каталога ИРБИС64:
            находит записи, совпадающие с загруженными перечнями,
            формирует отчёты и помогает проставлять служебные метки.</p>
            <h3>Проверка каталога</h3>
            <p>Работайте с базой на сервере ИРБИС64 или с её локальной TXT-копией.
            Сверяйте издания с перечнями из Excel по ISBN, названию, автору,
            издательству и году с помощью выбранных правил совпадения;
            проверяйте авторов по реестру иностранных агентов.</p>
            <h3>Результаты и метки</h3>
            <p>Просматривайте найденные совпадения и сохраняйте Excel-отчёты.
            Пограничные совпадения выносятся на отдельный лист для ручной проверки.
            Настраивайте поля и текст меток, создавайте только отчёт или применяйте
            метки к подтверждённым совпадениям. Сравнивайте отчёты и TXT-копии,
            чтобы увидеть изменения между проверками.</p>
            <h3>Изменение базы</h3>
            <p>Перед прямой записью в ИРБИС64 программа показывает план изменений
            и проверяет версии записей. Создание резервной копии исходных полей
            включено по умолчанию; ход выполнения и результат отражаются в журнале.</p>
            <h3>Проект и обратная связь</h3>
            <p><a href="{GITHUB_REPOSITORY_URL}">Описание и исходный код</a><br>
            <a href="{GITHUB_REPOSITORY_URL}/releases/latest">Скачать последнюю версию</a><br>
            <a href="{GITHUB_REPOSITORY_URL}/issues">Сообщить об ошибке или предложить улучшение</a></p>
            <p>Автоматическая и ручная проверка обновлений доступны в разделе «Настройки».</p>
        """)
        root.addWidget(about_page)
        dialog.exec()

    def _save(self) -> None:
        if not self.backup_check.isChecked():
            answer = QMessageBox.warning(
                self,
                APP_TITLE,
                "Без резервной копии частично выполненную запись нельзя будет безопасно откатить. "
                "Всё равно отключить создание копии?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        settings = ApplicationSettings(
            create_database_backup=self.backup_check.isChecked(),
            check_updates_on_start=self.auto_updates_check.isChecked(),
            theme=str(self.theme_combo.currentData()),
            use_nkp_drug_registry=self.nkp_drug_check.isChecked(),
            use_nkp_foreign_agents_registry=self.nkp_foreign_check.isChecked(),
        )
        self.saved.emit(settings)


# Связывает вкладки, настройки и фоновые операции, сохраняя состояние текущего запуска.
class MainWindow(
    MainWindowOperationsMixin,
    MainWindowRunMixin,
    MainWindowBuildMixin,
    MainWindowLayoutMixin,
    QMainWindow,
):
    settings_page_class = ApplicationSettingsPage
    RUN_JOURNAL_MAX_LINES = 10_000
    IRBIS_HEALTH_CHECK_INTERVAL_MS = 60_000

    def __init__(self) -> None:
        super().__init__()
        self._window_resize_tracking = False
        self._programmatic_window_resize = False
        self._window_manually_resized = False
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        # Размер окна подстраивается под текущую страницу. Если содержимое не
        # помещается на экране, оно остаётся прокручиваемым.
        self.setMinimumSize(520, 360)
        self.resize(560, 520)

        self.thread: QThread | None = None
        self.worker: QObject | None = None
        self.irbis_thread: QThread | None = None
        self.irbis_worker: IrbisOperationWorker | None = None
        self._irbis_connection_state = "error"
        self._irbis_connection_status_text = "Готово к подключению"
        self._irbis_response_ms: int | None = None
        self.last_results: list[MatchResult] = []
        self.last_summary: ComparisonSummary | None = None
        self.last_output_path = ""
        self.last_modified_database_path = ""
        self.last_run_direct = False
        self.last_run_report_only = False
        self.app_settings = load_application_settings(application_settings_path())
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, self.app_settings.theme)
        self.marker_settings = load_marker_settings()
        self.update_thread: QThread | None = None
        self.update_worker: UpdateWorker | None = None
        self._update_manual = False
        self._pending_update_asset: ReleaseAsset | None = None
        self._installing_update = False
        self.progress_dialog = ProgressDialog("Ход выполнения", self)
        self.progress_dialog.cancel_requested.connect(self._cancel_current_comparison)
        self.nkp_refresh_thread: QThread | None = None
        self.nkp_refresh_worker: NkpRegistryRefreshWorker | None = None
        self._journal_lines: deque[str] = deque(maxlen=self.RUN_JOURNAL_MAX_LINES)
        self._journal_save_timer = QTimer(self)
        self._journal_save_timer.setSingleShot(True)
        self._journal_save_timer.timeout.connect(self._save_run_journal)

        self._build_ui()
        self._apply_style()
        self._align_all_control_heights()
        self._restore_window_state()
        self._irbis_health_timer = QTimer(self)
        self._irbis_health_timer.setInterval(self.IRBIS_HEALTH_CHECK_INTERVAL_MS)
        self._irbis_health_timer.timeout.connect(self._auto_check_irbis_connection)
        self._irbis_health_timer.start()
        self._irbis_health_debounce = QTimer(self)
        self._irbis_health_debounce.setSingleShot(True)
        self._irbis_health_debounce.timeout.connect(self._auto_check_irbis_connection)
        QTimer.singleShot(1_500, self._auto_check_irbis_connection)
        if self.app_settings.check_updates_on_start:
            QTimer.singleShot(3_000, lambda: self.check_updates(manual=False))

    def _asset_icon(self, filename: str) -> QIcon:
        return QIcon(icon_path(filename))

    @staticmethod
    def _database_connector_config_path() -> Path:
        return database_connector_config_path()

    @staticmethod
    def _app_data_dir() -> Path:
        return app_data_dir()

    @staticmethod
    def _run_journal_path() -> Path:
        return run_journal_path()

    def _align_all_control_heights(self) -> None:
        """Согласует высоту полей и кнопок, чтобы форма оставалась компактной."""
        field_height = max(
            self.irbis_host_edit.minimumHeight(),
            self.irbis_host_edit.sizeHint().height(),
        )

        for field in self.findChildren(QLineEdit):
            if field.objectName() != "qt_spinbox_lineedit":
                field.setFixedHeight(field_height)
        for field in self.findChildren(QComboBox):
            field.setFixedHeight(field_height)
        for field in self.findChildren(QSpinBox):
            field.setFixedHeight(field_height)
        for button in self.findChildren(QPushButton):
            button.setFixedHeight(field_height)

        self.direct_irbis_box.setFixedHeight(field_height)
        self.irbis_password_box.setFixedHeight(field_height)
        self.irbis_database_box.setFixedHeight(field_height)

    def _restore_window_state(self) -> None:
        try:
            data = json.loads(window_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = {}

        try:
            saved_width = int(data["width"])
            saved_height = int(data["height"])
            has_saved_geometry = saved_width > 0 and saved_height > 0
        except (KeyError, TypeError, ValueError):
            saved_width = self.width()
            saved_height = self.height()
            has_saved_geometry = False
        self._window_manually_resized = has_saved_geometry

        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        default_area = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 720)

        width = max(self.minimumWidth(), saved_width)
        width = min(width, max(self.minimumWidth(), default_area.width()))
        height = max(self.minimumHeight(), saved_height)
        height = min(height, max(240, default_area.height()))
        self.resize(width, height)

        try:
            x = int(data["x"])
            y = int(data["y"])
            saved_rect = QRect(x, y, width, height)
        except (KeyError, TypeError, ValueError):
            saved_rect = QRect()

        visible = any(saved_rect.intersects(screen.availableGeometry()) for screen in screens)
        if visible:
            self.move(saved_rect.topLeft())
        else:
            area = default_area
            self.move(
                area.x() + max(0, (area.width() - width) // 2),
                area.y() + max(0, (area.height() - height) // 2),
            )

        if has_saved_geometry and bool(data.get("maximized", False)):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        elif not has_saved_geometry:
            QTimer.singleShot(0, lambda: self._resize_height_to_current_page(force=True))
        else:
            QTimer.singleShot(0, self._fit_scroll_content)

    def _save_window_state(self) -> None:
        if self.isMaximized():
            normal_geometry = self.normalGeometry()
            position = normal_geometry.topLeft()
            normal_size = normal_geometry.size()
        else:
            position = self.frameGeometry().topLeft()
            normal_size = self.size()
        payload = {
            "x": position.x(),
            "y": position.y(),
            "width": normal_size.width(),
            "height": normal_size.height(),
            "maximized": self.isMaximized(),
        }
        path = window_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def open_useful_links(self) -> None:
        UsefulLinksDialog(self).exec()

    def open_confirmation_memory(self) -> None:
        """Открывает сохранённые решения ручной проверки для просмотра и удаления."""
        ConfirmationMemoryDialog(manual_review_memory_path(), self).exec()

    def open_marker_settings(self) -> None:
        dialog = MarkerSettingsDialog(self.marker_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.marker_settings = dict(dialog.settings)
            self._apply_marker_settings_to_ui()

    def open_application_settings(self) -> None:
        page = self.application_settings_page
        page.backup_check.setChecked(self.app_settings.create_database_backup)
        page.auto_updates_check.setChecked(self.app_settings.check_updates_on_start)
        page.theme_combo.setCurrentIndex(page.theme_combo.findData(self.app_settings.theme))
        page.nkp_drug_check.setChecked(self.app_settings.use_nkp_drug_registry)
        page.nkp_foreign_check.setChecked(self.app_settings.use_nkp_foreign_agents_registry)
        self.workflow_tabs.setCurrentWidget(page)
        self.marker_settings_button.setChecked(True)

    def _settings_navigation_changed(self, _index: int) -> None:
        current = self.workflow_tabs.currentWidget()
        self.marker_settings_button.setChecked(current is self.application_settings_page)
        if current is not self.application_settings_page:
            self._settings_return_page = current

    def _save_application_settings(self, settings: ApplicationSettings) -> None:
        try:
            save_application_settings(application_settings_path(), settings)
        except Exception as exc:
            QMessageBox.warning(
                self,
                APP_TITLE,
                f"Не удалось сохранить настройки приложения:\n{exc}",
            )
            return
        self.app_settings = settings
        if hasattr(self, "nkp_live_check"):
            self.nkp_live_check.blockSignals(True)
            self.nkp_live_check.setChecked(settings.use_nkp_drug_registry)
            self.nkp_live_check.blockSignals(False)
        if hasattr(self, "nkp_foreign_live_check"):
            self.nkp_foreign_live_check.blockSignals(True)
            self.nkp_foreign_live_check.setChecked(settings.use_nkp_foreign_agents_registry)
            self.nkp_foreign_live_check.blockSignals(False)
        self._update_nkp_source_controls()
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, settings.theme)
        self._apply_style()
        self._set_status("Настройки приложения сохранены", "idle")
        self._close_application_settings()

    def _close_application_settings(self) -> None:
        page = self.application_settings_page
        page.backup_check.setChecked(self.app_settings.create_database_backup)
        page.auto_updates_check.setChecked(self.app_settings.check_updates_on_start)
        page.theme_combo.setCurrentIndex(page.theme_combo.findData(self.app_settings.theme))
        page.nkp_drug_check.setChecked(self.app_settings.use_nkp_drug_registry)
        page.nkp_foreign_check.setChecked(self.app_settings.use_nkp_foreign_agents_registry)
        self.workflow_tabs.setCurrentWidget(self._settings_return_page)

    def _cancel_current_comparison(self) -> None:
        worker = self.worker
        request_cancel = getattr(worker, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()
            self._set_status("Отмена операции запрошена…", "warning")
            self.progress_dialog.set_progress(self.progress.value(), "Отмена запрошена. Завершаем текущий этап…")

    def _registry_cache_dir(self) -> Path:
        path = self._app_data_dir() / "registries"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _format_registry_meta(path: Path, cache_path: Path) -> str:
        if not cache_path.is_file():
            return "Кэш НКП ещё не загружен"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return f"Кэш сохранён: {cache_path.name}"
        fetched = str(payload.get("fetched_at") or "").strip()
        try:
            fetched_text = datetime.fromisoformat(fetched.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
        except ValueError:
            fetched_text = fetched or "дата неизвестна"
        try:
            rows = int(payload.get("rows", 0))
        except (TypeError, ValueError):
            rows = 0
        rows_text = f" · {rows:,} строк".replace(",", " ") if rows else ""
        return f"Кэш обновлён {fetched_text}{rows_text}"

    def _refresh_registry_cache_status(self) -> None:
        cache = self._registry_cache_dir()
        if hasattr(self, "nkp_drug_status_label"):
            self.nkp_drug_status_label.setText(
                self._format_registry_meta(cache / DRUG_META_FILENAME, cache / DRUG_CACHE_FILENAME)
            )
        if hasattr(self, "nkp_foreign_status_label"):
            self.nkp_foreign_status_label.setText(
                self._format_registry_meta(
                    cache / FOREIGN_AGENTS_META_FILENAME,
                    cache / FOREIGN_AGENTS_CACHE_FILENAME,
                )
            )

    def _update_nkp_source_controls(self, *_args) -> None:
        if not hasattr(self, "nkp_foreign_live_check"):
            return
        foreign_online = self.nkp_foreign_live_check.isChecked()
        # Онлайн-реестр заменяет локальный файл иноагентов. Серый список явно показывает,
        # что выбранный локальный файл сейчас не участвует в сравнении.
        for widget in (self.foreign_agents_list, self.foreign_agents_button):
            widget.setEnabled(not foreign_online)
        self.clear_foreign_agents_button.setEnabled(bool(self.foreign_agents_edit.text().strip()))
        self.foreign_agents_list.setToolTip(
            "Онлайн-реестр НКП включён: локальный файл временно не используется."
            if foreign_online
            else "Перетащите сюда реестр .xlsx/.xlsm или нажмите «Добавить Excel»"
        )

    def _nkp_source_preference_changed(self, *_args) -> None:
        if not hasattr(self, "nkp_live_check"):
            return
        self.app_settings.use_nkp_drug_registry = self.nkp_live_check.isChecked()
        self.app_settings.use_nkp_foreign_agents_registry = self.nkp_foreign_live_check.isChecked()
        try:
            save_application_settings(application_settings_path(), self.app_settings)
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить выбор источников НКП:\n{exc}")
        page = getattr(self, "application_settings_page", None)
        if page is not None:
            page.nkp_drug_check.setChecked(self.app_settings.use_nkp_drug_registry)
            page.nkp_foreign_check.setChecked(self.app_settings.use_nkp_foreign_agents_registry)

    def open_nkp_registry_page(self, registry: str) -> None:
        url = NKP_FOREIGN_AGENTS_PAGE_URL if registry == "foreign" else NKP_DRUG_PAGE_URL
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, APP_TITLE, f"Не удалось открыть страницу НКП РГБ:\n{url}")

    def open_registry_cache_folder(self) -> None:
        folder = self._registry_cache_dir()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, APP_TITLE, f"Не удалось открыть папку кэша:\n{folder}")

    def clear_registry_cache(self) -> None:
        if self.nkp_refresh_thread is not None and self.nkp_refresh_thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Сначала дождитесь завершения обновления реестра НКП.")
            return
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "Удалить локальный кэш обоих реестров НКП?\n\n"
            "При следующей проверке программа заново загрузит данные с сайта. Встроенная резервная копия останется доступна.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        folder = self._registry_cache_dir()
        removed = 0
        for filename in (
            DRUG_CACHE_FILENAME,
            DRUG_META_FILENAME,
            FOREIGN_AGENTS_CACHE_FILENAME,
            FOREIGN_AGENTS_META_FILENAME,
        ):
            target = folder / filename
            if target.exists():
                target.unlink(missing_ok=True)
                removed += 1
        self._refresh_registry_cache_status()
        self._set_status("Кэш реестров НКП очищен", "idle")
        if removed == 0:
            QMessageBox.information(self, APP_TITLE, "Локальный кэш реестров уже пуст.")

    def refresh_nkp_registry(self, registry: str) -> None:
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Сначала дождитесь завершения текущей проверки.")
            return
        if self.nkp_refresh_thread is not None and self.nkp_refresh_thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Обновление реестра НКП уже выполняется.")
            return
        if registry not in {"drug", "foreign"}:
            return
        button = self.refresh_nkp_foreign_button if registry == "foreign" else self.refresh_nkp_drug_button
        label = self.nkp_foreign_status_label if registry == "foreign" else self.nkp_drug_status_label
        button.setEnabled(False)
        label.setText("Обновление с НКП РГБ…")
        self._set_status("Обновление реестра НКП РГБ…", "running")

        thread = QThread(self)
        worker = NkpRegistryRefreshWorker(registry)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._nkp_registry_refresh_finished)
        worker.failed.connect(self._nkp_registry_refresh_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._nkp_registry_refresh_thread_finished)
        self.nkp_refresh_thread = thread
        self.nkp_refresh_worker = worker
        thread.start()

    def _nkp_registry_refresh_finished(self, registry: str, result: object) -> None:
        self._refresh_registry_cache_status()
        detail = str(getattr(result, "detail", "")).strip()
        used_cache = bool(getattr(result, "used_cache", False))
        self._set_status(
            ("НКП: используется сохранённая копия" if used_cache else "Реестр НКП обновлён")
            + (f" · {detail}" if detail else ""),
            "warning" if used_cache else "success",
        )
        button = self.refresh_nkp_foreign_button if registry == "foreign" else self.refresh_nkp_drug_button
        button.setEnabled(True)

    def _nkp_registry_refresh_failed(self, registry: str, error: str) -> None:
        self._refresh_registry_cache_status()
        button = self.refresh_nkp_foreign_button if registry == "foreign" else self.refresh_nkp_drug_button
        button.setEnabled(True)
        self._set_status("Не удалось обновить реестр НКП", "error")
        QMessageBox.warning(self, APP_TITLE, f"Не удалось обновить реестр НКП РГБ:\n{error}")

    def _nkp_registry_refresh_thread_finished(self) -> None:
        self.nkp_refresh_thread = None
        self.nkp_refresh_worker = None

    def open_database_connector(self) -> None:
        """Запускает отдельную утилиту прямого подключения к хранилищу/ИРБИС."""
        database_paths = self._database_paths()
        database_path = database_paths[0] if database_paths else ""
        modified_paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
        modified_path = modified_paths[0] if modified_paths else self.modified_database_edit.text().strip()

        try:
            if getattr(sys, "frozen", False):
                base = Path(sys.executable).resolve().parent
                candidates = [
                    base / "IRBIS64ControlDB.exe",
                ]
                connector = next((path for path in candidates if path.is_file()), None)
                if connector is None:
                    raise FileNotFoundError(
                        "Рядом с ИРБИС64 Контроль не найден IRBIS64ControlDB.exe. "
                        "Пересоберите комплект через scripts\\build_exe.bat."
                    )
                command = [str(connector)]
            else:
                connector_script = project_root() / "db_connector.py"
                if not connector_script.is_file():
                    raise FileNotFoundError(f"Не найден файл {connector_script.name}")
                command = [sys.executable, str(connector_script)]

            if database_path:
                command.extend(["--database", database_path])
            if modified_path and Path(modified_path).is_file():
                command.extend(["--modified", modified_path])
            subprocess.Popen(
                command,
                cwd=str(Path(command[0]).resolve().parent if getattr(sys, "frozen", False) else project_root()),
            )
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось запустить подключение к базе:\n{exc}")

    @staticmethod
    def _make_field_spin(value: int = 1) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(value)
        spin.setPrefix("#")
        spin.setMinimumWidth(88)
        spin.setMaximumWidth(88)
        return spin

    def _apply_marker_settings_to_ui(self) -> None:
        if not hasattr(self, "substance_marker_edit"):
            return
        self.match_rules_editor.set_values(self.marker_settings)
        self.fuzzy_match_check.setChecked(bool(self.marker_settings["use_fuzzy"]))
        self.create_excel_report_check.setChecked(bool(self.marker_settings["create_excel_report"]))
        self.report_substances_check.setChecked(bool(self.marker_settings["report_substances"]))
        self.report_foreign_agents_check.setChecked(bool(self.marker_settings["report_foreign_agents"]))
        self.report_combined_check.setChecked(bool(self.marker_settings["report_combined"]))
        self.report_summary_check.setChecked(bool(self.marker_settings["report_summary"]))
        self.report_deduplicate_check.setChecked(bool(self.marker_settings["report_deduplicate"]))
        sort_index = self.report_sort_combo.findData(str(self.marker_settings["report_sort"]))
        self.report_sort_combo.setCurrentIndex(max(0, sort_index))
        self.report_only_check.setChecked(bool(self.marker_settings["report_only"]))
        self._update_report_controls(self.create_excel_report_check.isChecked())
        self.substance_marker_edit.setText(str(self.marker_settings["substance_marker"]))
        self.foreign_marker_edit.setText(str(self.marker_settings["foreign_agent_marker_template"]))
        self.foreign_organization_marker_edit.setText(str(self.marker_settings["foreign_organization_marker_template"]))
        self.age_marker_edit.setText(str(self.marker_settings["age_marker"]))
        self.substance_field_spin.setValue(int(self.marker_settings["substance_marker_field"]))
        self.foreign_field_spin.setValue(int(self.marker_settings["foreign_agent_marker_field"]))
        self.foreign_organization_field_spin.setValue(int(self.marker_settings["foreign_organization_marker_field"]))
        self.age_field_spin.setValue(int(self.marker_settings["age_marker_field"]))
        self.substance_marker_check.setChecked(bool(self.marker_settings["substance_marker_enabled"]))
        self.foreign_marker_check.setChecked(bool(self.marker_settings["foreign_agent_marker_enabled"]))
        self.foreign_organization_marker_check.setChecked(
            bool(self.marker_settings["foreign_organization_marker_enabled"])
        )
        self.age_marker_check.setChecked(bool(self.marker_settings["age_marker_enabled"]))
        self._update_marker_control_states()
        self._sync_output_mode_from_checks()

    def _update_marker_control_states(self, *_args) -> None:
        groups = (
            (self.substance_marker_check, self.substance_field_spin, self.substance_marker_edit),
            (self.foreign_marker_check, self.foreign_field_spin, self.foreign_marker_edit),
            (
                self.foreign_organization_marker_check,
                self.foreign_organization_field_spin,
                self.foreign_organization_marker_edit,
            ),
            (self.age_marker_check, self.age_field_spin, self.age_marker_edit),
        )
        for check, field, marker in groups:
            enabled = check.isChecked()
            field.setEnabled(enabled)
            marker.setEnabled(enabled)

    def _marker_values_from_ui(self) -> dict[str, str | int | bool]:
        return {
            **self.match_rules_editor.values(),
            "use_fuzzy": self.fuzzy_match_check.isChecked(),
            "fuzzy_threshold": 92,
            "create_excel_report": self.create_excel_report_check.isChecked(),
            "report_substances": self.report_substances_check.isChecked(),
            "report_foreign_agents": self.report_foreign_agents_check.isChecked(),
            "report_combined": self.report_combined_check.isChecked(),
            "report_summary": self.report_summary_check.isChecked(),
            "report_deduplicate": self.report_deduplicate_check.isChecked(),
            "report_sort": str(self.report_sort_combo.currentData()),
            "report_only": self.report_only_check.isChecked(),
            "substance_marker": self.substance_marker_edit.text().strip(),
            "foreign_agent_marker_template": self.foreign_marker_edit.text().strip(),
            "foreign_organization_marker_template": self.foreign_organization_marker_edit.text().strip(),
            "age_marker": self.age_marker_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "foreign_organization_marker_field": self.foreign_organization_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
            "substance_marker_enabled": self.substance_marker_check.isChecked(),
            "foreign_agent_marker_enabled": self.foreign_marker_check.isChecked(),
            "foreign_organization_marker_enabled": self.foreign_organization_marker_check.isChecked(),
            "age_marker_enabled": self.age_marker_check.isChecked(),
        }

    def _sync_marker_settings_from_ui(self, save: bool = True, show_message: bool = True) -> bool:
        values = self._marker_values_from_ui()
        if not any(bool(values[key]) for key in ("use_isbn_matching", "use_title_fallback", *EXTRA_MATCH_RULES)):
            QMessageBox.warning(self, APP_TITLE, "Включите хотя бы одно правило совпадения.")
            self.workflow_tabs.setCurrentIndex(1)
            return False
        if bool(values["report_only"]) and not bool(values["create_excel_report"]):
            QMessageBox.warning(self, APP_TITLE, "Для режима «Только отчёт» включите создание Excel-отчёта.")
            self.workflow_tabs.setCurrentIndex(1)
            return False
        report_keys = (
            "report_substances",
            "report_foreign_agents",
            "report_combined",
            "report_summary",
        )
        if bool(values["create_excel_report"]) and not any(bool(values[key]) for key in report_keys):
            QMessageBox.warning(self, APP_TITLE, "Выберите хотя бы один лист для Excel-отчёта.")
            self.workflow_tabs.setCurrentIndex(1)
            return False
        text_values = [value for value in values.values() if isinstance(value, str)]
        if any("\n" in value or "\r" in value for value in text_values):
            QMessageBox.warning(self, APP_TITLE, "Метка должна состоять из одной строки.")
            return False
        if any(re.search(r"#\d{1,3}\s*:", value, re.IGNORECASE) for value in text_values):
            QMessageBox.warning(self, APP_TITLE, "Введите только содержимое метки — номер поля задаётся отдельно.")
            return False
        self.marker_settings = values
        if save:
            try:
                save_marker_settings(values)
            except Exception as exc:
                QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить настройки:\n{exc}")
                return False
        if show_message:
            self._set_status("Настройки меток сохранены", "idle")
        return True

    def _queue_report_settings_autosave(self, *_args) -> None:
        if getattr(self, "_advanced_settings_editing", False):
            return
        if getattr(self, "_report_autosave_pending", False):
            return
        self._report_autosave_pending = True
        QTimer.singleShot(0, self._autosave_report_settings)

    def _autosave_report_settings(self) -> None:
        self._report_autosave_pending = False
        if getattr(self, "_advanced_settings_editing", False):
            return
        values = self._marker_values_from_ui()
        report_keys = (
            "report_substances",
            "report_foreign_agents",
            "report_combined",
            "report_summary",
        )
        # Во время переключения между листами кратковременно может не быть
        # выбрано ни одного варианта. Такое промежуточное состояние не сохраняем.
        if bool(values["create_excel_report"]) and not any(bool(values[key]) for key in report_keys):
            return
        self.marker_settings = values
        try:
            save_marker_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось автоматически сохранить настройки списков:\n{exc}")

    def _queue_marker_settings_autosave(self, *_args) -> None:
        if getattr(self, "_advanced_settings_editing", False):
            return
        self._marker_autosave_timer.start(350)

    def _autosave_marker_settings(self) -> None:
        if getattr(self, "_advanced_settings_editing", False):
            return
        self._sync_marker_settings_from_ui(save=True, show_message=False)

    def _update_report_controls(self, enabled: bool) -> None:
        if not enabled and self.report_only_check.isChecked():
            self.report_only_check.setChecked(False)
        for widget in (
            self.output_label,
            self.output_edit,
            self.report_path_button,
            *self.report_list_checks,
            self.report_deduplicate_check,
            self.report_sort_label,
            self.report_sort_combo,
        ):
            widget.setEnabled(enabled)

    def _update_report_only(self, enabled: bool) -> None:
        if enabled and not self.create_excel_report_check.isChecked():
            self.create_excel_report_check.setChecked(True)
        direct = self.direct_irbis_checkbox.isChecked() if hasattr(self, "direct_irbis_checkbox") else False
        for name in ("modified_database_label", "modified_database_edit", "txt_path_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not enabled and not direct)

    def create_matches_excel(self) -> None:
        """Запускает проверку из раздела отчёта без добавления меток."""
        create_report = self.create_excel_report_check.isChecked()
        report_only = self.report_only_check.isChecked()
        self.create_excel_report_check.setChecked(True)
        self.report_only_check.setChecked(True)
        try:
            self.start_comparison()
        finally:
            # Рабочий поток уже получил копию параметров. Возвращаем прежние
            # настройки интерфейса, чтобы обычный запуск не стал отчётным.
            self.create_excel_report_check.setChecked(create_report)
            self.report_only_check.setChecked(report_only)
            self._sync_marker_settings_from_ui(save=True, show_message=False)

    def select_modified_database_output(self) -> None:
        initial = self.modified_database_edit.text().strip() or self._default_modified_database_path()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить TXT-копию с метками", initial, "TXT (*.txt)")
        if path:
            if not path.lower().endswith(".txt"):
                path += ".txt"
            self.modified_database_edit.setText(path)


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IRBIS64Control.DesktopApp")
        except (AttributeError, OSError):
            # AppUserModelID поддерживается только подходящими версиями Windows.
            pass

    prepare_application_ui()
    app = QApplication(sys.argv)
    install_russian_ui(app)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    startup_settings = load_application_settings(application_settings_path())
    apply_application_theme(app, startup_settings.theme)
    install_context_menu_manager(app)
    app.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
