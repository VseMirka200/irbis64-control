from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from irbis_control import APP_TITLE as APP_TITLE
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
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    EXTRA_MATCH_RULES,
)
from irbis_control.core.models import ComparisonSummary, MatchResult
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.paths import icon_path, project_root
from irbis_control.ui.components.dialogs import (
    DEFAULT_USEFUL_LINKS as DEFAULT_USEFUL_LINKS,
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
from irbis_control.ui.storage_paths import app_data_dir as app_data_dir
from irbis_control.ui.storage_paths import application_settings_path
from irbis_control.ui.storage_paths import database_connector_config_path as database_connector_config_path
from irbis_control.ui.theme import apply_light_palette
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

        substance_label = QLabel("Вещества:")
        substance_label.setObjectName("fieldLabel")
        self.substance_field_spin = self._field_spin(int(settings["substance_marker_field"]))
        self.substance_edit = QLineEdit(str(settings["substance_marker"]))
        self.substance_edit.setObjectName("settingsField")
        self.substance_edit.setPlaceholderText(DEFAULT_SUBSTANCE_MARKER)
        form.addWidget(substance_label, 1, 0)
        form.addWidget(self.substance_field_spin, 1, 1)
        form.addWidget(self.substance_edit, 1, 2)

        foreign_label = QLabel("Иноагенты:")
        foreign_label.setObjectName("fieldLabel")
        self.foreign_field_spin = self._field_spin(int(settings["foreign_agent_marker_field"]))
        self.foreign_edit = QLineEdit(str(settings["foreign_agent_marker_template"]))
        self.foreign_edit.setObjectName("settingsField")
        self.foreign_edit.setPlaceholderText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.foreign_edit.setToolTip(
            "{name} будет заменено на совпавшего автора. Организации и проекты в эту метку не записываются"
        )
        form.addWidget(foreign_label, 2, 0)
        form.addWidget(self.foreign_field_spin, 2, 1)
        form.addWidget(self.foreign_edit, 2, 2)

        foreign_hint = QLabel(
            "Используйте {name}, чтобы подставить совпавшего автора. Если у автора есть псевдоним, он будет оформлен как «ФИО (ПСЕВДОНИМ: ...)»."
        )
        foreign_hint.setObjectName("cardDescription")
        foreign_hint.setWordWrap(True)
        form.addWidget(foreign_hint, 3, 2)

        age_label = QLabel("Все совпадения:")
        age_label.setObjectName("fieldLabel")
        self.age_field_spin = self._field_spin(int(settings["age_marker_field"]))
        self.age_edit = QLineEdit(str(settings["age_marker"]))
        self.age_edit.setObjectName("settingsField")
        self.age_edit.setPlaceholderText(DEFAULT_AGE_MARKER)
        form.addWidget(age_label, 4, 0)
        form.addWidget(self.age_field_spin, 4, 1)
        form.addWidget(self.age_edit, 4, 2)
        form.setColumnStretch(2, 1)
        layout.addLayout(form)

        self.preview = QLabel()
        self.preview.setObjectName("cardDescription")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        for edit in (self.substance_edit, self.foreign_edit, self.age_edit):
            edit.textChanged.connect(self._update_preview)
        for spin in (self.substance_field_spin, self.foreign_field_spin, self.age_field_spin):
            spin.valueChanged.connect(self._update_preview)
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
        buttons.addWidget(cancel_button)

        save_button = QPushButton("Сохранить")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
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
            "use_fuzzy": False,
            "fuzzy_threshold": 90,
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
            "age_marker": self.age_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
        }

    def _update_preview(self) -> None:
        values = self._values()
        foreign_preview = str(values["foreign_agent_marker_template"]).replace("{name}", "ИВАНОВ ИВАН ИВАНОВИЧ")
        self.preview.setText(
            f"Пример: #{int(values['substance_marker_field']):03d}: "
            f"{values['substance_marker'] or 'не добавляется'}; "
            f"для иноагента — #{int(values['foreign_agent_marker_field']):03d}: "
            f"{foreign_preview or 'не добавляется'}; "
            f"#{int(values['age_marker_field']):03d}: "
            f"{values['age_marker'] or 'не добавляется'}."
        )

    def _reset_defaults(self) -> None:
        self.isbn_match_check.setChecked(True)
        self.title_fallback_check.setChecked(True)
        self.substance_edit.setText(DEFAULT_SUBSTANCE_MARKER)
        self.foreign_edit.setText(DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE)
        self.age_edit.setText(DEFAULT_AGE_MARKER)
        self.substance_field_spin.setValue(DEFAULT_SUBSTANCE_MARKER_FIELD)
        self.foreign_field_spin.setValue(DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
        self.age_field_spin.setValue(DEFAULT_AGE_MARKER_FIELD)

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

        updates_card = SectionCard("Обновления", "")
        layout.addWidget(updates_card)
        self.auto_updates_check = QCheckBox("Проверять обновления на GitHub при запуске")
        self.auto_updates_check.setChecked(settings.check_updates_on_start)
        updates_card.body.addWidget(self.auto_updates_check)
        check_button = QPushButton("Проверить обновление сейчас")
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
        buttons.addStretch()
        cancel_button = QPushButton("Отмена")
        cancel_button.clicked.connect(self.cancelled.emit)
        buttons.addWidget(cancel_button)
        save_button = QPushButton("Сохранить")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        root.addLayout(buttons)

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
        title_font = title.font()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title, 1)
        root.addLayout(header)
        about_page = QTextBrowser()
        about_page.setObjectName("aboutPage")
        about_page.setFrameShape(QFrame.Shape.NoFrame)
        about_page.setStyleSheet("QTextBrowser#aboutPage { border: none; background: #f8fafc; color: #202020; }")
        about_page.document().setDocumentMargin(0)
        about_page.document().setDefaultStyleSheet(
            "h3 { font-size: 13px; font-weight: 600; color: #006bd6; margin-top: 12px; margin-bottom: 3px; }"
            "p { margin-top: 0; margin-bottom: 7px; }"
            "a { color: #006bd6; }"
        )
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
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
        # Компактный размер един для всех запусков; длинные страницы прокручиваются.
        self.setFixedSize(560, 600)

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
        self.marker_settings = load_marker_settings()
        self.update_thread: QThread | None = None
        self.update_worker: UpdateWorker | None = None
        self._update_manual = False
        self._pending_update_asset: ReleaseAsset | None = None
        self._installing_update = False
        self.progress_dialog = ProgressDialog("Ход выполнения", self)
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
        except Exception:
            data = {}

        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        default_area = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 720)

        width = max(self.minimumWidth(), int(data.get("width", self.width())))
        width = min(width, max(self.minimumWidth(), default_area.width()))
        height = max(240, int(data.get("height", self.height())))
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

        QTimer.singleShot(0, self._fit_scroll_content)

    def _save_window_state(self) -> None:
        frame_position = self.frameGeometry().topLeft()
        normal_size = self.normalGeometry().size() if self.isMaximized() else self.size()
        payload = {
            "x": frame_position.x(),
            "y": frame_position.y(),
            "width": normal_size.width(),
            "height": normal_size.height(),
            "maximized": False,
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

    def open_marker_settings(self) -> None:
        dialog = MarkerSettingsDialog(self.marker_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.marker_settings = dict(dialog.settings)
            self._apply_marker_settings_to_ui()

    def open_application_settings(self) -> None:
        self.workflow_tabs.setCurrentWidget(self.application_settings_page)
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
        self._set_status("Настройки приложения сохранены", "idle")
        self._close_application_settings()

    def _close_application_settings(self) -> None:
        page = self.application_settings_page
        page.backup_check.setChecked(self.app_settings.create_database_backup)
        page.auto_updates_check.setChecked(self.app_settings.check_updates_on_start)
        self.workflow_tabs.setCurrentWidget(self._settings_return_page)

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
        self.age_marker_edit.setText(str(self.marker_settings["age_marker"]))
        self.substance_field_spin.setValue(int(self.marker_settings["substance_marker_field"]))
        self.foreign_field_spin.setValue(int(self.marker_settings["foreign_agent_marker_field"]))
        self.age_field_spin.setValue(int(self.marker_settings["age_marker_field"]))
        self._sync_output_mode_from_checks()

    def _marker_values_from_ui(self) -> dict[str, str | int | bool]:
        return {
            **self.match_rules_editor.values(),
            "use_fuzzy": False,
            "fuzzy_threshold": 90,
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
            "age_marker": self.age_marker_edit.text().strip(),
            "substance_marker_field": self.substance_field_spin.value(),
            "foreign_agent_marker_field": self.foreign_field_spin.value(),
            "age_marker_field": self.age_field_spin.value(),
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
        if getattr(self, "_report_autosave_pending", False):
            return
        self._report_autosave_pending = True
        QTimer.singleShot(0, self._autosave_report_settings)

    def _autosave_report_settings(self) -> None:
        self._report_autosave_pending = False
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
        self._marker_autosave_timer.start(350)

    def _autosave_marker_settings(self) -> None:
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
        except Exception:
            pass

    app = QApplication(sys.argv)
    install_russian_ui(app)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    apply_light_palette(app)
    app.setWindowIcon(QIcon(icon_path("irbis64_control.ico")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
