from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSlot
from PyQt6.QtWidgets import QApplication, QFileDialog, QListWidgetItem

from irbis_control import APP_TITLE, __version__
from irbis_control.application.updater import (
    GitHubRelease,
    ReleaseAsset,
    is_newer_version,
    schedule_install,
    select_windows_asset,
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
    remove_database_markers,
)
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.infrastructure.irbis_bridge import load_manifest
from irbis_control.ui.components.dialogs import ResultComparisonDialog, UpdateAvailableDialog, UpdateReadyDialog
from irbis_control.ui.message_box import AppMessageBox as QMessageBox
from irbis_control.ui.services.workers import IrbisOperationWorker, UpdateWorker
from irbis_control.ui.windows.connection_dialog import IrbisConnectionDialog

APP_VERSION = __version__


class MainWindowOperationsMixin:
    def open_connection_settings(self) -> None:
        databases = [
            (
                self.irbis_db_combo.itemText(index),
                str(self.irbis_db_combo.itemData(index) or self.irbis_db_combo.itemText(index)).strip(),
            )
            for index in range(self.irbis_db_combo.count())
        ]
        dialog = IrbisConnectionDialog(
            {
                "host": self.irbis_host_edit.text(),
                "port": self.irbis_port_spin.value(),
                "login": self.irbis_login_edit.text(),
                "password": self.irbis_password_edit.text(),
                "database": self._current_irbis_database(),
                "query": self.irbis_query_edit.text(),
                "page_size": self.irbis_page_size_spin.value(),
            },
            databases,
            self,
        )
        dialog.connection_requested.connect(self._connect_from_settings_dialog)
        dialog.reading_test_requested.connect(self._test_reading_from_settings_dialog)
        self._active_connection_dialog = dialog
        try:
            result = dialog.exec()
        finally:
            self._active_connection_dialog = None
        if result != dialog.DialogCode.Accepted:
            return

        values = dialog.values()
        self._apply_connection_settings(values)
        self._set_irbis_status("Настройки подключения сохранены", "warning")

    def _apply_connection_settings(self, values: dict[str, object]) -> None:
        self.irbis_host_edit.setText(str(values["host"]))
        self.irbis_port_spin.setValue(int(values["port"]))
        self.irbis_login_edit.setText(str(values["login"]))
        self.irbis_password_edit.setText(str(values["password"]))
        self.irbis_query_edit.setText(str(values["query"]))
        self.irbis_page_size_spin.setValue(int(values["page_size"]))
        database = str(values["database"])
        database_index = self.irbis_db_combo.findData(database)
        if database_index < 0:
            self.irbis_db_combo.addItem(database, database)
            database_index = self.irbis_db_combo.count() - 1
        self.irbis_db_combo.setCurrentIndex(database_index)
        self._save_irbis_config()
        self._refresh_connection_overview()

    def _connect_from_settings_dialog(self, values: dict[str, object]) -> None:
        self._apply_connection_settings(values)
        self._start_irbis_operation("test")

    def _test_reading_from_settings_dialog(self, values: dict[str, object]) -> None:
        self._apply_connection_settings(values)
        self._start_irbis_operation("tune_read")

    def _current_irbis_database(self) -> str:
        data = self.irbis_db_combo.currentData()
        return str(data if data is not None else self.irbis_db_combo.currentText()).strip()

    def _populate_irbis_databases(self, databases: object, preferred: str = "") -> None:
        current = preferred.strip() or self._current_irbis_database()
        self.irbis_db_combo.blockSignals(True)
        self.irbis_db_combo.clear()
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
                self.irbis_db_combo.addItem(title, name)
        if self.irbis_db_combo.count() == 0:
            if current:
                self.irbis_db_combo.addItem(current, current)
            else:
                self.irbis_db_combo.addItem("Нет доступных баз", "")
        if current:
            index = self.irbis_db_combo.findData(current)
            if index < 0:
                index = self.irbis_db_combo.findText(current)
            if index >= 0:
                self.irbis_db_combo.setCurrentIndex(index)
        self.irbis_db_combo.blockSignals(False)
        # Обновление списка должно быть доступно и до получения первой базы.
        self.irbis_db_combo.setEnabled(True)

    def _update_direct_mode_ui(self, checked: bool | None = None) -> None:
        direct = self.direct_irbis_checkbox.isChecked() if checked is None else bool(checked)
        if hasattr(self, "database_list"):
            self.database_list.setVisible(not direct)
            self.database_button.setVisible(not direct)
            database = self._current_irbis_database() or "не выбрана"
            self.direct_source_label.setText(f"ИРБИС · {database}" if direct else "Локальная TXT-база")
            self._sync_direct_source_status()
        if hasattr(self, "write_irbis_button"):
            self.write_irbis_button.setVisible(not direct)
        for name in ("modified_database_label", "modified_database_edit", "txt_path_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not direct)
                mode = str(self.output_mode_combo.currentData()) if hasattr(self, "output_mode_combo") else "full"
                widget.setEnabled(not direct and mode != "report")
        if hasattr(self, "result_files_layout"):
            self._reflow_result_files()
        if hasattr(self, "marker_card"):
            self.marker_card.title_label.setText("Метки в ИРБИС" if direct else "Метки в TXT-копии")
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setText("Удалить метки из ИРБИС" if direct else "Удалить все метки из TXT")
        if direct and hasattr(self, "_irbis_health_debounce"):
            self._set_irbis_status("Проверка подключения к ИРБИС…", "running")
            self._irbis_health_debounce.start(250)

    def _sync_direct_source_status(self) -> None:
        if not hasattr(self, "direct_source_dot"):
            return
        direct = self.direct_irbis_checkbox.isChecked()
        state = self._irbis_connection_state if direct else "local"
        status_text = self._irbis_connection_status_text if direct else "Используется локальная TXT-база"
        self.direct_source_dot.setProperty("state", state)
        short_status = {
            "success": "Подключено",
            "running": "Проверка…",
            "warning": "Внимание",
            "error": "Нет подключения",
            "local": "Локальный файл",
        }.get(state, "Нет подключения")
        if state == "success" and self._irbis_response_ms is not None:
            short_status += f" · {self._irbis_response_ms} мс"
        self.direct_source_state_label.setText(short_status)
        self.direct_source_state_label.setProperty("state", state)
        self.direct_source_dot.setToolTip(status_text)
        self.direct_source_label.setToolTip(status_text)
        self.direct_source_state_label.setToolTip(status_text)
        self.direct_source_dot.style().unpolish(self.direct_source_dot)
        self.direct_source_dot.style().polish(self.direct_source_dot)
        self.direct_source_state_label.style().unpolish(self.direct_source_state_label)
        self.direct_source_state_label.style().polish(self.direct_source_state_label)
        self.direct_source_dot.update()
        self.direct_source_state_label.update()

    def _invalidate_irbis_connection_status(self) -> None:
        if self.irbis_thread and self.irbis_thread.isRunning():
            return
        self._set_irbis_status("Параметры подключения изменены", "error")
        if hasattr(self, "_irbis_health_debounce") and self.direct_irbis_checkbox.isChecked():
            self._irbis_health_debounce.start(1_200)

    def _auto_check_irbis_connection(self) -> None:
        if not self.direct_irbis_checkbox.isChecked():
            return
        if self.irbis_thread and self.irbis_thread.isRunning():
            return
        if self.thread and self.thread.isRunning():
            return
        self._start_irbis_operation("health", silent=True)

    def _restore_irbis_config(self) -> None:
        try:
            config = json.loads(self._database_connector_config_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            config = {}
        self.irbis_host_edit.setText(str(config.get("host", "127.0.0.1")))
        try:
            self.irbis_port_spin.setValue(int(config.get("port", 6666)))
        except Exception:
            self.irbis_port_spin.setValue(6666)
        self.irbis_login_edit.setText(str(config.get("login", "")))
        self.irbis_password_edit.setText(str(config.get("password", "")))
        saved_database = str(config.get("database", "IBIS")).strip()
        self._populate_irbis_databases([], saved_database)
        self.irbis_query_edit.setText(str(config.get("query", "I=$")))
        try:
            self.irbis_read_workers = max(1, min(8, int(config.get("read_workers", 4))))
        except Exception:
            self.irbis_read_workers = 4
        try:
            self.irbis_page_size_spin.setValue(int(config.get("page_size", 500)))
        except (TypeError, ValueError):
            self.irbis_page_size_spin.setValue(500)
        self.direct_irbis_checkbox.setChecked(bool(config.get("direct_mode", True)))
        if config.get("snapshot"):
            self.irbis_snapshot_path = str(config["snapshot"])
        if config.get("manifest"):
            self.irbis_manifest_path = str(config["manifest"])
        self._update_direct_mode_ui()

    def _save_irbis_config(self) -> None:
        data = {
            "host": self.irbis_host_edit.text().strip(),
            "port": self.irbis_port_spin.value(),
            "login": self.irbis_login_edit.text().strip(),
            "password": self.irbis_password_edit.text(),
            "database": self._current_irbis_database(),
            "query": self.irbis_query_edit.text().strip(),
            "read_workers": self.irbis_read_workers,
            "page_size": self.irbis_page_size_spin.value(),
            "direct_mode": self.direct_irbis_checkbox.isChecked(),
            "snapshot": self.irbis_snapshot_path.strip(),
            "manifest": self.irbis_manifest_path.strip(),
            "modified": self.last_modified_database_path or self.modified_database_edit.text().strip(),
        }
        atomic_write_text(
            self._database_connector_config_path(), json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _irbis_params(self) -> dict[str, object]:
        return {
            "host": self.irbis_host_edit.text().strip() or "127.0.0.1",
            "port": self.irbis_port_spin.value(),
            "login": self.irbis_login_edit.text().strip(),
            "password": self.irbis_password_edit.text(),
            "database": self._current_irbis_database(),
            "query": self.irbis_query_edit.text().strip() or "I=$",
            "read_workers": self.irbis_read_workers,
            "page_size": self.irbis_page_size_spin.value(),
            "direct_mode": self.direct_irbis_checkbox.isChecked(),
            "snapshot": self.irbis_snapshot_path.strip(),
            "manifest": self.irbis_manifest_path.strip(),
            "modified": self.last_modified_database_path or self.modified_database_edit.text().strip(),
            "backup_dir": str(self._app_data_dir() / "backups"),
            "create_backup": self.app_settings.create_database_backup,
            "substance_marker": self.marker_settings.get("substance_marker", DEFAULT_SUBSTANCE_MARKER),
            "foreign_agent_marker_template": self.marker_settings.get(
                "foreign_agent_marker_template", DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE
            ),
            "foreign_organization_marker_template": self.marker_settings.get(
                "foreign_organization_marker_template", DEFAULT_FOREIGN_ORGANIZATION_MARKER_TEMPLATE
            ),
            "age_marker": self.marker_settings.get("age_marker", DEFAULT_AGE_MARKER),
            "substance_marker_field": int(
                self.marker_settings.get("substance_marker_field", DEFAULT_SUBSTANCE_MARKER_FIELD)
            ),
            "foreign_agent_marker_field": int(
                self.marker_settings.get("foreign_agent_marker_field", DEFAULT_FOREIGN_AGENT_MARKER_FIELD)
            ),
            "foreign_organization_marker_field": int(
                self.marker_settings.get("foreign_organization_marker_field", DEFAULT_FOREIGN_ORGANIZATION_MARKER_FIELD)
            ),
            "age_marker_field": int(self.marker_settings.get("age_marker_field", DEFAULT_AGE_MARKER_FIELD)),
        }

    def _start_irbis_operation(self, mode: str, *, silent: bool = False) -> None:
        if self.irbis_thread and self.irbis_thread.isRunning():
            if not silent:
                QMessageBox.information(self, APP_TITLE, "Операция с ИРБИС уже выполняется.")
            return
        params = self._irbis_params()
        if mode in {"fetch", "apply", "clean_markers", "tune_read"} and not str(params["database"]).strip():
            QMessageBox.warning(self, APP_TITLE, "Сначала обновите список и выберите базу ИРБИС.")
            return
        if mode == "fetch" and not str(params["snapshot"]).strip():
            QMessageBox.warning(self, APP_TITLE, "Укажите путь для рабочей TXT-копии.")
            return
        if mode == "apply" and (
            not Path(str(params["manifest"])).is_file() or not Path(str(params["modified"])).is_file()
        ):
            QMessageBox.warning(self, APP_TITLE, "Нет карты MFN или готовой TXT-копии с изменениями.")
            return

        if not silent:
            self._save_irbis_config()
        if not silent:
            self.write_irbis_button.setEnabled(False)
            if hasattr(self, "cleanup_button"):
                self.cleanup_button.setEnabled(False)
        captions = {
            "health": "Проверка подключения к ИРБИС…",
            "test": "Подключение к ИРБИС…",
            "tune_read": "Тест пакета чтения…",
            "databases": "Обновление списка баз ИРБИС…",
            "fetch": "Получение базы ИРБИС…",
            "apply": "Запись изменений в ИРБИС…",
            "clean_markers": "Очистка меток прямо в ИРБИС…",
        }
        caption = captions.get(mode, "Выполнение операции…")
        self._set_irbis_status(caption, "running")
        if not silent:
            if mode == "clean_markers":
                self.progress_dialog.setWindowTitle("Удаление меток")
                self.progress_dialog.start(caption)
            self._append_progress(caption)

        self.irbis_thread = QThread(self)
        self.irbis_worker = IrbisOperationWorker(mode, params)
        self.irbis_worker.moveToThread(self.irbis_thread)
        self.irbis_thread.started.connect(self.irbis_worker.run)
        self.irbis_worker.progress.connect(self._on_irbis_progress)
        self.irbis_worker.finished.connect(self.irbis_thread.quit)
        self.irbis_worker.failed.connect(self.irbis_thread.quit)
        self.irbis_worker.finished.connect(self._on_irbis_finished)
        self.irbis_worker.failed.connect(self._on_irbis_failed)
        self.irbis_thread.finished.connect(self.irbis_worker.deleteLater)
        self.irbis_thread.finished.connect(self._cleanup_irbis_worker)
        self.irbis_thread.start()

    @pyqtSlot(int, str)
    def _on_irbis_progress(self, percent: int, text: str) -> None:
        percent = max(0, min(100, percent))
        self._set_irbis_status(text, "running")
        if self.irbis_worker is not None and self.irbis_worker.mode == "clean_markers":
            self.progress_dialog.set_progress(percent, text)
        self._append_progress(text)

    @pyqtSlot(str, object)
    def _on_irbis_finished(self, mode: str, result: object) -> None:
        if mode == "health":
            checked_at = datetime.now().strftime("%H:%M:%S")
            data = result if isinstance(result, dict) else {}
            self._irbis_response_ms = max(1, int(data.get("response_ms", 1)))
            self._set_irbis_status(
                f"Подключено • отклик {self._irbis_response_ms} мс • проверено в {checked_at}",
                "success",
            )
            return
        if mode == "tune_read":
            data = result if isinstance(result, dict) else {}
            page_size = max(100, min(2000, int(data.get("page_size", 500))))
            probe_total = int(data.get("probe_total", 0))
            self.irbis_page_size_spin.setValue(page_size)
            active_dialog = getattr(self, "_active_connection_dialog", None)
            if active_dialog is not None and active_dialog.isVisible():
                active_dialog.page_size_spin.setValue(page_size)
            self._set_irbis_status(
                f"Пакет чтения: {page_size} записей • найдено по запросу: {probe_total:,}",
                "success",
            )
            self._append_progress(
                f"Тест чтения завершён. Автоматически выбран пакет: {page_size}; записей по запросу: {probe_total:,}."
            )
            self._save_irbis_config()
            return
        if mode in {"test", "databases"}:
            data = result if isinstance(result, dict) else {}
            self._irbis_response_ms = max(1, int(data.get("response_ms", 1)))
            databases = data.get("databases", [])
            previous = self._current_irbis_database()
            self._populate_irbis_databases(databases, previous)
            count = self.irbis_db_combo.count() if self._current_irbis_database() else 0
            if mode == "test":
                checked_at = datetime.now().strftime("%H:%M:%S")
                self._set_irbis_status(
                    f"Подключено к ИРБИС • доступно баз: {count} • "
                    f"отклик {self._irbis_response_ms} мс • проверено в {checked_at}",
                    "success",
                )
                self._append_progress(f"Подключение к ИРБИС выполнено. Загружено баз: {count}.")
            else:
                self._set_irbis_status(f"Список баз обновлён • доступно: {count}", "success")
                self._append_progress(f"Список баз ИРБИС обновлён: {count}.")
            self._save_irbis_config()
            return
        if mode == "fetch":
            manifest = result
            snapshot = Path(str(manifest.snapshot_file))
            self.database_list.clear()
            item = QListWidgetItem(str(snapshot))
            item.setData(Qt.ItemDataRole.UserRole, True)
            self.database_list.addItem(item)
            self.irbis_snapshot_path = str(snapshot)
            self.irbis_manifest_path = str(Path(self.irbis_manifest_path.strip()))
            self._update_database_summary()
            self._set_default_outputs(force=True)
            self._set_irbis_status(f"Рабочая база готова: {len(manifest.records)} записей", "success")
            self._append_progress(
                f"Рабочая база готова: {len(manifest.records)} записей. Файл автоматически выбран для проверки."
            )
            self.last_modified_database_path = ""
            self.write_irbis_button.setEnabled(snapshot.is_file() and Path(self.irbis_manifest_path.strip()).is_file())
            self._save_irbis_config()
            self.workflow_tabs.setCurrentWidget(self.data_tab)
            return
        if mode == "clean_markers":
            data = result if isinstance(result, dict) else {}
            scanned = int(data.get("scanned", 0))
            found = int(data.get("found", 0))
            written = int(data.get("written", 0))
            backup = str(data.get("backup", ""))
            self._set_irbis_status(f"Очистка завершена • изменено: {written}", "success")
            self._append_progress(
                f"Очистка меток завершена: просмотрено {scanned:,}, найдено {found:,}, изменено {written:,}."
            )
            if backup:
                self._append_progress(f"Rollback-копия перед очисткой: {backup}")
            self.progress_dialog.finish("Удаление меток завершено.", 100)
            QMessageBox.information(
                self,
                APP_TITLE,
                f"Очистка меток в ИРБИС завершена.\n\n"
                f"Просмотрено записей: {scanned:,}\n"
                f"Записей с метками: {found:,}\n"
                f"Очищено записей: {written:,}" + (f"\n\nRollback-копия: {backup}" if backup else ""),
            )
            return
        if mode == "apply":
            data = result if isinstance(result, dict) else {}
            written = int(data.get("written", 0))
            conflicts = int(data.get("conflicts", 0))
            self._set_irbis_status(
                f"Отправлено в ИРБИС: {written}; конфликтов: {conflicts}",
                "warning" if conflicts else "success",
            )
            self._append_progress(f"Отправка в ИРБИС завершена: записано {written}, конфликтов {conflicts}.")
            if conflicts:
                self._append_progress(
                    "Часть записей была изменена на сервере другим пользователем. Перед следующей отправкой получите базу заново."
                )
            else:
                self._append_progress("Локальный снимок и карта MFN обновлены по состоянию сервера.")
            backup = str(data.get("backup", ""))
            if backup:
                self._append_progress(f"Rollback-копия: {backup}")

    @pyqtSlot(str, str)
    def _on_irbis_failed(self, mode: str, error: str) -> None:
        if mode == "health":
            checked_at = datetime.now().strftime("%H:%M:%S")
            self._irbis_response_ms = None
            self._set_irbis_status(f"Нет подключения • проверено в {checked_at}", "error")
            return
        QTimer.singleShot(0, self._fit_scroll_content)
        self._set_irbis_status("Ошибка подключения/обмена с ИРБИС", "error")
        self._append_progress(f"Ошибка ИРБИС: {error}")
        if mode == "clean_markers":
            self.progress_dialog.finish("Ошибка удаления меток.", 0)
        QMessageBox.critical(self, APP_TITLE, f"Операция ИРБИС не выполнена:\n{error}")

    @pyqtSlot()
    def _cleanup_irbis_worker(self) -> None:
        if self.irbis_thread:
            self.irbis_thread.deleteLater()
        self.irbis_thread = None
        self.irbis_worker = None
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(True)
        modified_candidates = [item.strip() for item in self.last_modified_database_path.split(";") if item.strip()]
        if not modified_candidates:
            output_candidate = self.modified_database_edit.text().strip()
            if output_candidate and Path(output_candidate).is_file():
                modified_candidates = [output_candidate]
        if not modified_candidates:
            snapshot_candidate = self.irbis_snapshot_path.strip()
            if snapshot_candidate and Path(snapshot_candidate).is_file():
                modified_candidates = [snapshot_candidate]
        can_write = bool(
            len(modified_candidates) == 1
            and Path(modified_candidates[0]).is_file()
            and Path(self.irbis_manifest_path.strip()).is_file()
        )
        self.write_irbis_button.setEnabled(can_write)

    def apply_results_to_irbis(self) -> None:
        # Можно отправить результат сравнения, очищенную копию или исходный
        # снимок без изменений. Приоритет — последняя созданная копия, затем
        # существующий выходной TXT, затем сам снимок ИРБИС.
        modified_paths = [item.strip() for item in self.last_modified_database_path.split(";") if item.strip()]
        if not modified_paths:
            candidate = self.modified_database_edit.text().strip()
            if candidate and Path(candidate).is_file():
                modified_paths = [candidate]
        if not modified_paths:
            snapshot_candidate = self.irbis_snapshot_path.strip()
            if snapshot_candidate and Path(snapshot_candidate).is_file():
                modified_paths = [snapshot_candidate]
        if len(modified_paths) != 1 or not Path(modified_paths[0]).is_file():
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Не найдена TXT-копия для отправки в ИРБИС. Сначала получите базу или создайте очищенную/изменённую копию.",
            )
            return
        modified = modified_paths[0]
        manifest_path = Path(self.irbis_manifest_path.strip())
        if not manifest_path.is_file():
            QMessageBox.warning(self, APP_TITLE, "Карта MFN отсутствует. Сначала получите базу через вкладку ИРБИС.")
            return
        try:
            manifest = load_manifest(manifest_path)
            selected_databases = [Path(item).resolve() for item in self._database_paths()]
            snapshot = Path(manifest.snapshot_file).resolve()
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось проверить карту MFN:\n{exc}")
            return
        if len(selected_databases) != 1 or selected_databases[0] != snapshot:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Текущая выбранная TXT-база не совпадает со снимком, полученным из ИРБИС. Для безопасности запись отменена.",
            )
            return
        source_name = Path(modified).name
        backup_text = (
            "Rollback-копия будет создана до записи."
            if self.app_settings.create_database_backup
            else "ВНИМАНИЕ: rollback-копия отключена в настройках."
        )
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "TXT-копия будет отправлена в живую базу ИРБИС полностью — даже если в ней нет изменений. "
            "Перед записью каждой записи проверяется версия на сервере. "
            f"{backup_text}\n\n"
            f"Файл: {source_name}\n\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.last_modified_database_path = modified
        self._start_irbis_operation("apply")

    def check_updates(self, _checked: bool = False, *, manual: bool = True) -> None:
        if self._downloaded_update_path is not None:
            if self._downloaded_update_path.is_file():
                self._offer_install_downloaded_update()
                return
            self._downloaded_update_path = None
            self._downloaded_update_version = ""
        if self.update_thread and self.update_thread.isRunning():
            if manual:
                QMessageBox.information(self, APP_TITLE, "Проверка обновления уже выполняется.")
            return
        self._update_manual = manual
        self._start_update_worker("check")
        if manual:
            self._set_status("Проверка обновления на GitHub…", "running")

    def _start_update_worker(
        self,
        mode: str,
        asset: ReleaseAsset | None = None,
    ) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("Загрузка обновления…" if mode == "download" else "Проверка обновления…")
        self.update_thread = QThread(self)
        self.update_worker = UpdateWorker(mode, asset)
        self.update_worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(self.update_worker.run)
        self.update_worker.progress.connect(self._on_update_progress)
        self.update_worker.finished.connect(self.update_thread.quit)
        self.update_worker.failed.connect(self.update_thread.quit)
        self.update_worker.finished.connect(self._on_update_finished)
        self.update_worker.failed.connect(self._on_update_failed)
        self.update_thread.finished.connect(self.update_worker.deleteLater)
        self.update_thread.finished.connect(self._cleanup_update_worker)
        self.update_thread.start()

    @pyqtSlot(int)
    def _on_update_progress(self, percent: int) -> None:
        self._set_status(f"Загрузка обновления: {max(0, min(100, percent))}%", "running")

    @pyqtSlot(object)
    def _on_update_finished(self, result: object) -> None:
        if result is None:
            if self._update_manual:
                QMessageBox.information(
                    self,
                    APP_TITLE,
                    f"Обновлений нет.\nТекущая версия: {APP_VERSION}",
                )
            return
        if isinstance(result, GitHubRelease):
            self._handle_available_release(result)
            return
        if isinstance(result, Path):
            self._downloaded_update_path = result
            self._downloaded_update_version = self._pending_update_version
            self.update_button.setText("Установить обновление")
            self._set_status("Новая версия скачана и готова к установке.", "success")
            self._offer_install_downloaded_update()

    def _offer_install_downloaded_update(self) -> None:
        package = self._downloaded_update_path
        if package is None or not package.is_file():
            self._downloaded_update_path = None
            self._downloaded_update_version = ""
            self.update_button.setText("Проверить обновления")
            return
        dialog = UpdateReadyDialog(
            release_version=self._downloaded_update_version,
            package_path=package,
            parent=self,
        )
        dialog.exec()
        if not dialog.install_requested:
            self.update_button.setText("Установить обновление")
            return
        try:
            schedule_install(package, sys.executable)
        except Exception as exc:
            QMessageBox.warning(
                self,
                APP_TITLE,
                f"Не удалось запустить установку:\n{exc}\n\nФайл сохранён здесь:\n{package}",
            )
            self.update_button.setText("Установить обновление")
            return
        self._installing_update = True
        QApplication.quit()

    def _handle_available_release(self, release: GitHubRelease) -> None:
        if not is_newer_version(release.version, APP_VERSION):
            if self._update_manual:
                QMessageBox.information(
                    self,
                    APP_TITLE,
                    f"Обновлений нет.\nТекущая версия: {APP_VERSION}",
                )
            return

        asset = select_windows_asset(release)
        can_download = asset is not None and getattr(sys, "frozen", False)
        dialog = UpdateAvailableDialog(
            current_version=APP_VERSION,
            release_version=release.version,
            release_notes=release.notes,
            page_url=release.page_url,
            asset_name=asset.name if asset is not None else "",
            asset_size=asset.size if asset is not None else 0,
            can_download=can_download,
            parent=self,
        )
        dialog.exec()
        if dialog.download_requested and asset is not None:
            self._pending_update_asset = asset
            self._pending_update_version = release.version

    @pyqtSlot(str)
    def _on_update_failed(self, error: str) -> None:
        if self._update_manual:
            checking = self.update_worker is not None and self.update_worker.mode == "check"
            action = "проверить наличие обновлений" if checking else "загрузить обновление"
            QMessageBox.warning(self, APP_TITLE, f"Не удалось {action}:\n{error}")
        else:
            self._append_progress(f"Автопроверка обновлений не выполнена: {error}")

    def _cleanup_update_worker(self) -> None:
        self.update_thread = None
        self.update_worker = None
        self.update_button.setEnabled(True)
        pending = self._pending_update_asset
        self._pending_update_asset = None
        if pending is not None:
            QTimer.singleShot(0, lambda: self._start_update_worker("download", pending))
            return
        if self._downloaded_update_path is not None and self._downloaded_update_path.is_file():
            self.update_button.setText("Установить обновление")
        else:
            self.update_button.setText("Проверить обновления")

    def open_result_comparison(self) -> None:
        dialog = ResultComparisonDialog(self)
        if self.last_output_path and Path(self.last_output_path).is_file():
            dialog.new_edit.setText(self.last_output_path)
            dialog._set_default_output()
        dialog.exec()

    def clean_markers(self) -> None:
        if not self._sync_marker_settings_from_ui(save=True, show_message=False):
            return
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Сначала дождитесь завершения текущей проверки.")
            return
        if not self.direct_irbis_checkbox.isChecked():
            self.clean_txt_markers()
            return

        params = self._irbis_params()
        database = str(params.get("database", "")).strip()
        if not str(params.get("login", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Введите логин каталогизатора ИРБИС.")
            self.workflow_tabs.setCurrentWidget(self.data_tab)
            return
        if not database:
            QMessageBox.warning(self, APP_TITLE, "Выберите базу ИРБИС из списка.")
            self.workflow_tabs.setCurrentWidget(self.data_tab)
            return

        answer = QMessageBox.warning(
            self,
            APP_TITLE,
            "Из выбранной живой базы ИРБИС будут удалены стандартные и текущие "
            "настроенные метки ИРБИС64 Контроль. Другие значения в тех же полях сохраняются.\n\n"
            f"База: {database}\n"
            f"Сервер: {params.get('host')}:{params.get('port')}\n\n"
            + (
                "Перед изменениями будет создана rollback-копия найденных записей. Продолжить?"
                if self.app_settings.create_database_backup
                else "ВНИМАНИЕ: rollback-копия отключена в настройках. Продолжить без неё?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_irbis_operation("clean_markers")

    def clean_txt_markers(self) -> None:
        initial_folder = str(self._default_output_folder())
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите TXT-базу с метками",
            initial_folder,
            "TXT-базы (*.txt);;Все файлы (*.*)",
        )
        if not source_path:
            return
        source = Path(source_path)
        default_output = source.with_name(
            f"{source.stem}_без_меток_{datetime.now():%Y%m%d_%H%M%S}{source.suffix or '.txt'}"
        )
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить очищенную TXT-базу",
            str(default_output),
            "TXT-базы (*.txt)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".txt"):
            output_path += ".txt"
        self.progress_dialog.setWindowTitle("Удаление меток")
        self.progress_dialog.start("Подготовка к очистке TXT-базы…")
        self._append_progress(f"Удаление меток из TXT-базы: {source_path}")

        def update_cleanup_progress(percent: int, text: str) -> None:
            self.progress_dialog.set_progress(percent, text)
            QApplication.processEvents()

        try:
            written, cleaned_records = remove_database_markers(
                source_path,
                output_path,
                substance_marker=str(self.marker_settings["substance_marker"]),
                foreign_agent_marker_template=str(self.marker_settings["foreign_agent_marker_template"]),
                foreign_organization_marker_template=str(self.marker_settings["foreign_organization_marker_template"]),
                age_marker=str(self.marker_settings["age_marker"]),
                substance_marker_field=int(self.marker_settings["substance_marker_field"]),
                foreign_agent_marker_field=int(self.marker_settings["foreign_agent_marker_field"]),
                foreign_organization_marker_field=int(self.marker_settings["foreign_organization_marker_field"]),
                age_marker_field=int(self.marker_settings["age_marker_field"]),
                progress_cb=update_cleanup_progress,
            )
        except Exception as exc:
            self.progress_dialog.finish("Ошибка удаления меток.", 0)
            self._append_progress(f"Ошибка очистки TXT-базы: {exc}")
            QMessageBox.warning(self, APP_TITLE, f"Не удалось очистить TXT-базу:\n{exc}")
            return
        cleaned_path = str(Path(written))
        self.last_modified_database_path = cleaned_path
        self.modified_database_edit.setText(cleaned_path)
        self.open_modified_database_button.setEnabled(Path(cleaned_path).is_file())
        self.write_irbis_button.setEnabled(
            Path(cleaned_path).is_file()
            and Path(self.irbis_manifest_path.strip()).is_file()
            and Path(self.irbis_snapshot_path.strip()).is_file()
        )
        self._save_irbis_config()
        self._append_progress(f"Очищенная TXT-копия выбрана для отправки в ИРБИС: {cleaned_path}")
        self.progress_dialog.finish("Удаление меток завершено.", 100)
        QMessageBox.information(
            self,
            APP_TITLE,
            f"Очищенная копия сохранена и выбрана для отправки в ИРБИС.\n"
            f"Записей с удалёнными метками: {cleaned_records}\n\n"
            f"Файл: {written}",
        )
