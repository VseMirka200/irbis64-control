from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from irbis_control.application.settings import load_application_settings
from irbis_control.infrastructure.atomic_io import atomic_write_text

from PyQt6.QtCore import QSize, QStandardPaths, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from irbis_control.infrastructure.irbis_bridge import (
    IrbisClient,
    IrbisError,
    apply_modified_snapshot,
    create_irbis_snapshot,
    replace_txt_storage,
)
from irbis_control.paths import resource_path
from irbis_control.ui.locale import install_russian_ui
from irbis_control.ui.storage_paths import application_settings_path
from irbis_control.ui.theme import apply_theme_palette

APP_TITLE = "ИРБИС64 Контроль — подключение к базе"

def app_data_dir() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def config_path() -> Path:
    return app_data_dir() / "database_connector.json"


class ConnectorWindow(QMainWindow):
    def __init__(self, initial_database: str = "", initial_modified: str = "") -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(resource_path("assets", "irbis64_control.ico")))
        self.resize(720, 600)
        self.setMinimumSize(520, 430)
        self._config = self._load_config()
        self._build_ui()
        self._apply_style()
        self._restore_config()
        if initial_database:
            self.local_target_edit.setText(initial_database)
        if initial_modified:
            self.modified_edit.setText(initial_modified)
            self.local_modified_edit.setText(initial_modified)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("logo")
        logo.setPixmap(QIcon(resource_path("assets", "irbis64_control_icon.png")).pixmap(26, 26))
        logo.setFixedSize(28, 28)
        header.addWidget(logo)
        titles = QVBoxLayout(); titles.setSpacing(1)
        title = QLabel("Прямое подключение к данным")
        title.setObjectName("title")
        subtitle = QLabel("Подключитесь к серверу ИРБИС64 или укажите TXT-хранилище.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        titles.addWidget(title); titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("connectorTabs")
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_irbis_tab(), "ИРБИС64 сервер")
        self.tabs.addTab(self._build_txt_tab(), "TXT-хранилище")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("Журнал подключения и синхронизации…")
        layout.addWidget(self.log)

    def _row_with_button(self, edit: QLineEdit, button_text: str, callback) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        row.addWidget(edit, 1)
        button = QPushButton(button_text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return host

    def _build_irbis_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(6)

        columns = QHBoxLayout(); columns.setSpacing(6)

        form_card = QFrame(); form_card.setObjectName("card")
        form_outer = QVBoxLayout(form_card); form_outer.setContentsMargins(4, 3, 4, 4); form_outer.setSpacing(4)
        form_title = QLabel("1. Параметры подключения"); form_title.setObjectName("cardTitle"); form_outer.addWidget(form_title)
        form = QGridLayout(); form.setHorizontalSpacing(6); form.setVerticalSpacing(4)
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(6666)
        self.port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.login_edit = QLineEdit()
        self.password_edit = QLineEdit(); self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = self.password_edit.addAction(
            QIcon(resource_path("assets", "eye.svg")),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.show_password.setCheckable(True)
        self.show_password.setText("Показать пароль")
        self.show_password.setToolTip("Показать пароль")

        def toggle_password(visible: bool) -> None:
            self.password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
            self.show_password.setIcon(
                QIcon(resource_path("assets", "eye-crossed.svg" if visible else "eye.svg"))
            )
            hint = "Скрыть пароль" if visible else "Показать пароль"
            self.show_password.setText(hint)
            self.show_password.setToolTip(hint)

        self.show_password.toggled.connect(toggle_password)
        self.db_combo = QComboBox()
        self.db_combo.setToolTip("Список существующих баз загружается с сервера ИРБИС.")
        self.db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.db_combo.setMinimumContentsLength(18)
        self.refresh_db_button = QPushButton()
        self.refresh_db_button.setObjectName("secondary")
        self.refresh_db_button.setIcon(QIcon(resource_path("assets", "refresh.svg")))
        self.refresh_db_button.setIconSize(QSize(16, 16))
        self.refresh_db_button.setFixedWidth(32)
        self.refresh_db_button.setToolTip("Обновить список баз")
        self.refresh_db_button.clicked.connect(lambda: self.refresh_databases())
        self.query_edit = QLineEdit("I=$")
        self.query_edit.setToolTip("Поисковое выражение ИРБИС, которое должно охватывать нужные записи.")
        fields = [("Сервер", self.host_edit), ("Порт", self.port_spin), ("Логин каталогизатора", self.login_edit)]
        for row, (text, widget) in enumerate(fields):
            form.addWidget(QLabel(text), row, 0); form.addWidget(widget, row, 1)
        form.addWidget(QLabel("Пароль"), 3, 0)
        form.addWidget(self.password_edit, 3, 1)
        form.addWidget(QLabel("База данных"), 4, 0)
        db_row = QHBoxLayout(); db_row.setSpacing(4); db_row.addWidget(self.db_combo, 1); db_row.addWidget(self.refresh_db_button)
        form.addLayout(db_row, 4, 1)
        form.addWidget(QLabel("Запрос выборки"), 5, 0); form.addWidget(self.query_edit, 5, 1)
        form.setColumnStretch(1, 1); form_outer.addLayout(form)
        columns.addWidget(form_card, 1)

        work_box = QFrame(); work_box.setObjectName("card")
        work_layout = QVBoxLayout(work_box); work_layout.setContentsMargins(4, 3, 4, 4); work_layout.setSpacing(4)
        work_title = QLabel("2. Файлы"); work_title.setObjectName("cardTitle"); work_layout.addWidget(work_title)
        self.snapshot_edit = QLineEdit(str(app_data_dir() / "direct_database.txt"))
        self.manifest_edit = QLineEdit(str(app_data_dir() / "direct_database.map.json"))
        self.manifest_edit.setReadOnly(True)
        self.modified_edit = QLineEdit()
        work_layout.addWidget(QLabel("Рабочая TXT-копия")); work_layout.addWidget(self._row_with_button(self.snapshot_edit, "Выбрать…", self._pick_snapshot))
        work_layout.addWidget(QLabel("Карта MFN (создаётся автоматически)")); work_layout.addWidget(self.manifest_edit)
        work_layout.addWidget(QLabel("Изменённая TXT-копия из ИРБИС64 Контроль")); work_layout.addWidget(self._row_with_button(self.modified_edit, "Выбрать…", self._pick_modified))
        columns.addWidget(work_box, 1)
        layout.addLayout(columns)

        action_card = QFrame(); action_card.setObjectName("card")
        action_layout = QVBoxLayout(action_card); action_layout.setContentsMargins(4, 3, 4, 4); action_layout.setSpacing(4)
        action_title = QLabel("3. Действия"); action_title.setObjectName("cardTitle"); action_layout.addWidget(action_title)
        buttons = QHBoxLayout(); buttons.setSpacing(4)
        test = QPushButton("Проверить подключение"); test.setObjectName("secondary"); test.clicked.connect(self.test_connection)
        sync = QPushButton("1. Получить базу"); sync.setObjectName("primary"); sync.clicked.connect(self.sync_from_irbis)
        apply = QPushButton("2. Отправить TXT в ИРБИС"); apply.setObjectName("primary"); apply.clicked.connect(self.apply_to_irbis)
        buttons.addWidget(test); buttons.addWidget(sync, 1); buttons.addWidget(apply, 1)
        action_layout.addLayout(buttons)
        note = QLabel("Схема: получить базу → проверить TXT в ИРБИС64 Контроль → записать готовую TXT-копию обратно в ИРБИС.")
        note.setWordWrap(True); note.setObjectName("hint"); action_layout.addWidget(note)
        layout.addWidget(action_card)
        layout.addStretch(1)
        return page

    def _build_txt_tab(self) -> QWidget:
        page = QWidget(); page.setObjectName("tabPage")
        layout = QVBoxLayout(page); layout.setContentsMargins(5, 5, 5, 5); layout.setSpacing(6)
        card = QFrame(); card.setObjectName("card")
        card_layout = QVBoxLayout(card); card_layout.setContentsMargins(4, 3, 4, 4); card_layout.setSpacing(4)
        title = QLabel("TXT-хранилище"); title.setObjectName("cardTitle"); card_layout.addWidget(title)
        info = QLabel("Режим для обычного TXT-файла. Перед заменой автоматически создаётся резервная копия.")
        info.setWordWrap(True); info.setObjectName("hint"); card_layout.addWidget(info)
        self.local_target_edit = QLineEdit(); self.local_modified_edit = QLineEdit()
        card_layout.addWidget(QLabel("Исходный TXT-файл")); card_layout.addWidget(self._row_with_button(self.local_target_edit, "Выбрать…", self._pick_local_target))
        card_layout.addWidget(QLabel("Новая TXT-копия из ИРБИС64 Контроль")); card_layout.addWidget(self._row_with_button(self.local_modified_edit, "Выбрать…", self._pick_local_modified))
        apply = QPushButton("Заменить с резервной копией"); apply.setObjectName("primary"); apply.clicked.connect(self.apply_to_txt)
        card_layout.addWidget(apply)
        layout.addWidget(card); layout.addStretch(1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#root, QWidget#tabPage { background: palette(window); color: palette(window-text); }
            QFrame#card { border: none; background: transparent; }
            QLabel#title, QLabel#cardTitle { font-weight: 600; }
            QLabel#subtitle, QLabel#hint { color: palette(mid); }
            QTextEdit#log { font-family: Consolas, monospace; }
            QProgressBar { min-height: 10px; max-height: 10px; }
            """
        )

    def _load_config(self) -> dict:
        try:
            return json.loads(config_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _current_database(self) -> str:
        data = self.db_combo.currentData()
        return str(data if data is not None else self.db_combo.currentText()).strip()

    def _populate_databases(self, databases: object, preferred: str = "") -> None:
        current = preferred.strip() or self._current_database()
        self.db_combo.blockSignals(True)
        self.db_combo.clear()
        if isinstance(databases, list):
            for entry in databases:
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    description = str(entry.get("description", "")).strip()
                else:
                    name = str(entry).strip()
                    description = ""
                if not name:
                    continue
                title = f"{name} — {description}" if description and description.casefold() != name.casefold() else name
                self.db_combo.addItem(title, name)
        if self.db_combo.count() == 0:
            if current:
                self.db_combo.addItem(current, current)
            else:
                self.db_combo.addItem("Нет доступных баз", "")
        if current:
            index = self.db_combo.findData(current)
            if index >= 0:
                self.db_combo.setCurrentIndex(index)
        self.db_combo.blockSignals(False)
        self.db_combo.setEnabled(bool(self._current_database()))

    def refresh_databases(self, *, show_message: bool = True) -> None:
        self.progress.setValue(0)
        self.log.clear()
        try:
            self._progress(20, "Подключение к серверу ИРБИС…")
            with self._client() as client:
                self._progress(55, "Получение списка существующих баз…")
                databases = client.list_databases()
            previous = self._current_database()
            self._populate_databases(databases, previous)
            self._progress(100, f"Список баз обновлён: {len(databases)}")
            self._save_config()
            if show_message:
                QMessageBox.information(self, APP_TITLE, f"Список баз обновлён. Доступно: {len(databases)}.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))

    def _restore_config(self) -> None:
        self.host_edit.setText(str(self._config.get("host", "127.0.0.1")))
        self.port_spin.setValue(int(self._config.get("port", 6666)))
        self.login_edit.setText(str(self._config.get("login", "")))
        self._populate_databases([], str(self._config.get("database", "IBIS")))
        self.query_edit.setText(str(self._config.get("query", "I=$")))
        if self._config.get("snapshot"):
            self.snapshot_edit.setText(str(self._config["snapshot"]))
        if self._config.get("manifest"):
            self.manifest_edit.setText(str(self._config["manifest"]))
        if self._config.get("modified"):
            self.modified_edit.setText(str(self._config["modified"]))
        # Пароль специально не сохраняется на диск.

    def _save_config(self) -> None:
        data = {
            "host": self.host_edit.text().strip(), "port": self.port_spin.value(),
            "login": self.login_edit.text().strip(), "database": self._current_database(),
            "query": self.query_edit.text().strip(), "snapshot": self.snapshot_edit.text().strip(),
            "manifest": self.manifest_edit.text().strip(), "modified": self.modified_edit.text().strip(),
        }
        atomic_write_text(
            config_path(),
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _client(self) -> IrbisClient:
        return IrbisClient(
            self.host_edit.text().strip(), self.port_spin.value(), self.login_edit.text().strip(),
            self.password_edit.text(), "C", timeout=20,
        )

    def _progress(self, percent: int, text: str) -> None:
        self.progress.setValue(percent); self.log.append(text); QApplication.processEvents()

    def _pick_snapshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Рабочая TXT-копия", self.snapshot_edit.text(), "TXT (*.txt)")
        if path:
            self.snapshot_edit.setText(path if path.lower().endswith(".txt") else path + ".txt")
            self.manifest_edit.setText(str(Path(self.snapshot_edit.text()).with_suffix(".map.json")))

    def _pick_modified(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Изменённая TXT-копия", "", "TXT (*.txt);;Все файлы (*)")
        if path: self.modified_edit.setText(path)

    def _pick_local_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "TXT-хранилище", "", "TXT (*.txt);;Все файлы (*)")
        if path: self.local_target_edit.setText(path)

    def _pick_local_modified(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Новая TXT-копия", "", "TXT (*.txt);;Все файлы (*)")
        if path: self.local_modified_edit.setText(path)

    def test_connection(self) -> None:
        self.progress.setValue(0); self.log.clear()
        try:
            self._progress(20, "Подключение к серверу ИРБИС…")
            with self._client() as client:
                self._progress(55, "Авторизация принята. Получение списка баз…")
                databases = client.list_databases()
            previous = self._current_database()
            self._populate_databases(databases, previous)
            self._progress(100, f"Подключение успешно. Доступных баз: {len(databases)}")
            self._save_config()
            QMessageBox.information(self, APP_TITLE, f"Подключение работает. Доступных баз: {len(databases)}.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))

    def sync_from_irbis(self) -> None:
        database = self._current_database(); query = self.query_edit.text().strip()
        if not database or not query:
            QMessageBox.warning(self, APP_TITLE, "Обновите список, выберите базу и укажите запрос выборки."); return
        self.progress.setValue(0); self.log.clear()
        try:
            with self._client() as client:
                manifest = create_irbis_snapshot(
                    client, database, query, self.snapshot_edit.text().strip(), self.manifest_edit.text().strip(), progress_cb=self._progress
                )
            self._save_config()
            QMessageBox.information(self, APP_TITLE, f"Готово. Получено записей: {len(manifest.records)}\n\nВыберите файл:\n{manifest.snapshot_file}\nв разделе «База данных» ИРБИС64 Контроль.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))

    def apply_to_irbis(self) -> None:
        modified = Path(self.modified_edit.text().strip())
        manifest = Path(self.manifest_edit.text().strip())
        if not modified.is_file():
            snapshot = Path(self.snapshot_edit.text().strip())
            if snapshot.is_file():
                modified = snapshot
        if not modified.is_file() or not manifest.is_file():
            QMessageBox.warning(self, APP_TITLE, "Сначала создайте рабочую базу. Можно отправить исходную, очищенную или изменённую TXT-копию."); return
        answer = QMessageBox.question(
            self, APP_TITLE,
            "TXT-копия будет отправлена в живую базу ИРБИС полностью — даже если она не менялась. Перед записью программа проверит версии записей и создаст rollback-копию. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return
        self.progress.setValue(0); self.log.clear()
        try:
            with self._client() as client:
                written, conflicts, backup = apply_modified_snapshot(
                    client, manifest, modified, app_data_dir() / "backups", progress_cb=self._progress
                )
            self._save_config()
            text = f"Записано в ИРБИС: {written}."
            if conflicts: text += f"\nПропущено из-за изменения записей другим пользователем: {conflicts}."
            if backup: text += f"\nRollback-копия: {backup}"
            QMessageBox.information(self, APP_TITLE, text)
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))

    def apply_to_txt(self) -> None:
        target = Path(self.local_target_edit.text().strip()); modified = Path(self.local_modified_edit.text().strip())
        if not target.is_file() or not modified.is_file():
            QMessageBox.warning(self, APP_TITLE, "Выберите существующий исходный TXT и изменённую TXT-копию."); return
        try:
            backup = replace_txt_storage(target, modified, app_data_dir() / "backups")
            QMessageBox.information(self, APP_TITLE, f"TXT-хранилище обновлено.\nРезервная копия: {backup}")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))

    def closeEvent(self, event) -> None:
        try: self._save_config()
        except Exception: pass
        event.accept()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--database", default="")
    parser.add_argument("--modified", default="")
    return parser.parse_known_args(argv)[0]


def main() -> int:
    args = parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    install_russian_ui(app)
    app.setApplicationName("IRBIS64ControlDB")
    app.setOrganizationName("IRBIS64Control")
    settings = load_application_settings(application_settings_path())
    apply_theme_palette(app, settings.theme)
    window = ConnectorWindow(args.database, args.modified)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
