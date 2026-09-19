from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QListWidgetItem

from irbis_control import APP_TITLE
from irbis_control.ui.message_box import AppMessageBox as QMessageBox
from irbis_control.core.matcher import EXTRA_MATCH_RULES
from irbis_control.core.models import ComparisonOptions, ComparisonSummary, MatchResult
from irbis_control.infrastructure.atomic_io import atomic_write_text
from irbis_control.ui.components.dialogs import ManualMatchReviewDialog
from irbis_control.ui.services.workers import ComparisonWorker, DirectIrbisComparisonWorker


class MainWindowRunMixin:
    def _comparison_options(self) -> ComparisonOptions:
        return ComparisonOptions(
            use_isbn_matching=bool(self.marker_settings.get("use_isbn_matching", True)),
            use_title_fallback=bool(self.marker_settings.get("use_title_fallback", True)),
            use_fuzzy=bool(self.marker_settings.get("use_fuzzy", True)),
            fuzzy_threshold=int(self.marker_settings.get("fuzzy_threshold", 92)),
            match_rules={key: bool(self.marker_settings.get(key, False)) for key in EXTRA_MATCH_RULES},
        )

    def _hide_progress_for_prompt(self) -> bool:
        """Временно освобождает модальность для диалога, требующего ответа пользователя."""
        was_visible = self.progress_dialog.isVisible()
        if was_visible:
            self.progress_dialog.hide()
        return was_visible

    def _restore_progress_after_prompt(self, was_visible: bool) -> None:
        if not was_visible:
            return
        self.progress_dialog.show()
        self.progress_dialog.raise_()
        self.progress_dialog.activateWindow()

    def _adjust_source_list_height(self, list_widget) -> None:
        if list_widget.property("sourceHeightManuallySet"):
            return
        selected_count = sum(
            bool(list_widget.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(list_widget.count())
        )
        if list_widget.property("resizableSourceList"):
            target_height = 84
        else:
            target_height = 58 if selected_count > 1 else 27
        if list_widget.height() == target_height:
            return
        list_widget.setFixedHeight(target_height)
        list_widget.updateGeometry()
        QTimer.singleShot(0, self._resize_height_to_current_page)

    def _source_list_resized(self, list_widget, _height: int) -> None:
        list_widget.setProperty("sourceHeightManuallySet", True)
        list_widget.updateGeometry()
        QTimer.singleShot(0, self._resize_height_to_current_page)

    def _set_irbis_status(self, text: str, state: str = "success") -> None:
        self.irbis_status.setText(text)
        visual_state = state if state in {"success", "running", "warning", "error"} else "error"
        self._irbis_connection_state = visual_state
        self._irbis_connection_status_text = text
        self.irbis_status_dot.setProperty("state", visual_state)
        self.irbis_status_dot.style().unpolish(self.irbis_status_dot)
        self.irbis_status_dot.style().polish(self.irbis_status_dot)
        self._refresh_connection_overview()
        self.irbis_status_dot.update()
        self._sync_direct_source_status()

    def _set_status(self, text: str, state: str = "idle") -> None:
        self.status_label.setText(text)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)
        self.status_dot.update()

    def _update_excel_summary(self) -> None:
        paths = self._excel_paths()
        if not paths:
            self.excel_list.clear()
            placeholder = QListWidgetItem("Перетащите Excel сюда или нажмите «Добавить Excel»")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.excel_list.addItem(placeholder)
            self.excel_summary_edit.clear()
            self.excel_summary_edit.setPlaceholderText("Файлы не выбраны")
            self.excel_summary_edit.setToolTip("Перетащите Excel сюда или нажмите «Добавить Excel»")
            self._adjust_source_list_height(self.excel_list)
            return
        names = [Path(path).name for path in paths]
        if len(names) == 1:
            summary = names[0]
        else:
            summary = f"Выбрано файлов: {len(names)} — {names[0]}"
        self.excel_summary_edit.setText(summary)
        self.excel_summary_edit.setToolTip("\n".join(paths))
        self._adjust_source_list_height(self.excel_list)

    def _update_foreign_agents_summary(self) -> None:
        self.foreign_agents_list.clear()
        path = self.foreign_agents_edit.text().strip()
        if hasattr(self, "clear_foreign_agents_button"):
            self.clear_foreign_agents_button.setEnabled(bool(path))
        if path:
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, True)
            self.foreign_agents_list.addItem(item)
            self.foreign_agents_list.setToolTip(path)
        else:
            placeholder = QListWidgetItem("Перетащите реестр Excel сюда")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.foreign_agents_list.addItem(placeholder)
            self.foreign_agents_list.setToolTip("Перетащите сюда реестр .xlsx/.xlsm или нажмите «Добавить Excel»")
        self._adjust_source_list_height(self.foreign_agents_list)

    def _update_database_summary(self) -> None:
        paths = self._database_paths()
        if not paths:
            self.database_list.clear()
            placeholder = QListWidgetItem("Файлы не выбраны")
            placeholder.setData(Qt.ItemDataRole.UserRole, False)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.database_list.addItem(placeholder)
            self.database_list.setToolTip("Файлы не выбраны")
            self._adjust_source_list_height(self.database_list)
            return
        self.database_list.setToolTip("\n".join(paths))
        self._adjust_source_list_height(self.database_list)

    def _clear_excel_files(self) -> None:
        self.excel_list.clear()
        self._update_excel_summary()
        self._set_default_outputs(force=True)

    def _clear_foreign_agents(self) -> None:
        self.foreign_agents_edit.clear()
        self._update_foreign_agents_summary()
        self._set_default_outputs(force=True)

    def _clear_database_files(self) -> None:
        self.database_list.clear()
        self.database_edit.clear()
        self._update_database_summary()
        self._set_default_outputs(force=True)

    def select_database(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите TXT-базы данных",
            "",
            "Текстовые файлы (*.txt);;Все файлы (*)",
        )
        if self.database_list.count() == 1 and not self.database_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.database_list.clear()
        existing = set(self._database_paths())
        for path in paths:
            if path not in existing:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole, True)
                self.database_list.addItem(item)
                existing.add(path)
        if paths:
            self.database_edit.setText(paths[0])
            self._update_database_summary()
            self._set_default_outputs(force=True)

    def _drop_foreign_agents_files(self, paths: list[str]) -> None:
        """Принимает реестр иноагентов, перетащенный из Проводника."""
        if not paths:
            return
        self.foreign_agents_edit.setText(paths[0])
        self._update_foreign_agents_summary()
        self._set_default_outputs(force=True)

    def _drop_excel_files(self, paths: list[str]) -> None:
        """Добавляет перетащенные Excel-файлы в реестр по веществам без дублей."""
        if not paths:
            return
        if self.excel_list.count() == 1 and not self.excel_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.excel_list.clear()
        existing = {str(Path(path).resolve()).casefold() for path in self._excel_paths()}
        added = False
        for path in paths:
            key = str(Path(path).resolve()).casefold()
            if key in existing:
                continue
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, True)
            self.excel_list.addItem(item)
            existing.add(key)
            added = True
        if added:
            self._update_excel_summary()
            self._set_default_outputs(force=True)

    def select_foreign_agents(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите реестр иностранных агентов",
            "",
            "Excel (*.xlsx *.xlsm);;Все файлы (*)",
        )
        if path:
            self.foreign_agents_edit.setText(path)
            self._update_foreign_agents_summary()
            self._set_default_outputs(force=True)

    def add_excel_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите Excel-файлы",
            "",
            "Excel (*.xlsx *.xlsm *.xls);;Все файлы (*)",
        )
        if self.excel_list.count() == 1 and not self.excel_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.excel_list.clear()
        existing = set(self._excel_paths())
        for path in paths:
            if path not in existing:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole, True)
                self.excel_list.addItem(item)
                existing.add(path)
        if paths:
            self._update_excel_summary()
            self._set_default_outputs(force=True)

    def clear_all(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, APP_TITLE, "Сначала дождитесь завершения операции.")
            return
        self.database_edit.clear()
        self.database_list.clear()
        self._update_database_summary()
        self.foreign_agents_edit.clear()
        self._update_foreign_agents_summary()
        self.excel_list.clear()
        self._update_excel_summary()
        self.output_edit.clear()
        self.modified_database_edit.clear()
        self.progress.setValue(0)
        self._set_status("Готово к работе", "idle")
        self.progress_dialog.clear()
        self.open_button.setEnabled(False)
        self.open_modified_database_button.setEnabled(False)
        if hasattr(self, "write_irbis_button"):
            self.write_irbis_button.setEnabled(False)
        self.last_results = []
        self.last_summary = None
        self.last_output_path = ""
        self.last_modified_database_path = ""
        self.result_summary_label.setText(
            "Проверьте выбранные данные и нажмите «Запустить проверку». Результаты сохраняются автоматически."
        )

    def reset_workspace(self) -> None:
        """Очищает выбор и результаты текущего запуска, не меняя сохранённые настройки."""
        self.clear_all()
        if not (self.thread and self.thread.isRunning()):
            if hasattr(self, "workflow_tabs") and hasattr(self, "data_tab"):
                self.workflow_tabs.setCurrentWidget(self.data_tab)
            self._set_default_outputs(force=True)

    def select_output(self) -> None:
        initial = self.output_edit.text() or self._default_output_path()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", initial, "Excel (*.xlsx)")
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            self.output_edit.setText(path)

    def _default_output_folder(self) -> Path:
        database_paths = self._database_paths()
        excel_paths = self._excel_paths()
        if excel_paths:
            return Path(excel_paths[0]).parent
        if self.foreign_agents_edit.text():
            return Path(self.foreign_agents_edit.text()).parent
        if database_paths:
            return Path(database_paths[0]).parent
        return Path.home() / "Documents"

    def _default_output_path(self) -> str:
        name = f"ИРБИС64 Контроль_совпадения_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        return str(self._default_output_folder() / name)

    def _default_modified_database_path(self) -> str:
        database_paths = self._database_paths()
        source = Path(database_paths[0]) if database_paths else Path("База.TXT")
        name = f"{source.stem}_с_метками_18+_{datetime.now():%Y%m%d_%H%M%S}{source.suffix or '.TXT'}"
        return str(self._default_output_folder() / name)

    def _set_default_outputs(self, force: bool = False) -> None:
        if force or not self.output_edit.text().strip():
            self.output_edit.setText(self._default_output_path())
        if force or not self.modified_database_edit.text().strip():
            self.modified_database_edit.setText(self._default_modified_database_path())

    def _excel_paths(self) -> list[str]:
        return [
            self.excel_list.item(index).text()
            for index in range(self.excel_list.count())
            if self.excel_list.item(index).data(Qt.ItemDataRole.UserRole) is not False
        ]

    def _database_paths(self) -> list[str]:
        return [
            self.database_list.item(index).text()
            for index in range(self.database_list.count())
            if self.database_list.item(index).data(Qt.ItemDataRole.UserRole) is not False
        ]

    def _validate_inputs(self) -> tuple[list[str], str, list[str], str, str] | None:
        database_paths = self._database_paths()
        foreign_agents_path = self.foreign_agents_edit.text().strip()
        excel_paths = self._excel_paths()
        create_report = bool(self.marker_settings["create_excel_report"])
        report_only = bool(self.marker_settings["report_only"])
        output_path = (self.output_edit.text().strip() or self._default_output_path()) if create_report else ""
        modified_database_path = (
            ""
            if report_only
            else (self.modified_database_edit.text().strip() or self._default_modified_database_path())
        )

        if not database_paths:
            QMessageBox.warning(self, APP_TITLE, "Выберите хотя бы одну TXT-базу данных.")
            self.workflow_tabs.setCurrentIndex(0)
            return None
        missing_databases = [path for path in database_paths if not Path(path).is_file()]
        if missing_databases:
            QMessageBox.warning(self, APP_TITLE, "Некоторые TXT-базы не найдены:\n" + "\n".join(missing_databases))
            return None
        use_nkp_live = bool(getattr(self, "nkp_live_check", None) and self.nkp_live_check.isChecked())
        use_nkp_foreign_live = bool(
            getattr(self, "nkp_foreign_live_check", None) and self.nkp_foreign_live_check.isChecked()
        )
        if not excel_paths and not foreign_agents_path and not use_nkp_live and not use_nkp_foreign_live:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Выберите хотя бы один источник проверки: актуальный список НКП РГБ или локальный Excel-реестр.",
            )
            self.workflow_tabs.setCurrentIndex(0)
            return None
        missing = [path for path in excel_paths if not Path(path).is_file()]
        if missing:
            QMessageBox.warning(self, APP_TITLE, "Некоторые Excel-файлы не найдены:\n" + "\n".join(missing))
            return None
        if foreign_agents_path and not Path(foreign_agents_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Файл реестра иностранных агентов не найден.")
            return None
        if foreign_agents_path and Path(foreign_agents_path).suffix.lower() not in {".xlsx", ".xlsm"}:
            QMessageBox.warning(self, APP_TITLE, "Реестр иностранных агентов должен быть файлом .xlsx или .xlsm.")
            return None
        if create_report:
            if not output_path.lower().endswith(".xlsx"):
                output_path += ".xlsx"
                self.output_edit.setText(output_path)
            source_paths = {Path(path).resolve() for path in excel_paths}
            if foreign_agents_path:
                source_paths.add(Path(foreign_agents_path).resolve())
            if Path(output_path).resolve() in source_paths:
                QMessageBox.warning(self, APP_TITLE, "Файл отчёта не должен совпадать с исходным Excel-файлом.")
                return None
        if not report_only and not modified_database_path.lower().endswith(".txt"):
            modified_database_path += ".txt"
            self.modified_database_edit.setText(modified_database_path)
        if not report_only and Path(modified_database_path).resolve() in {
            Path(path).resolve() for path in database_paths
        }:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "TXT-копия должна сохраняться в новый файл, а не поверх исходной базы.",
            )
            return None
        if not report_only and create_report and Path(modified_database_path).resolve() == Path(output_path).resolve():
            QMessageBox.warning(self, APP_TITLE, "Пути Excel-отчёта и TXT-копии должны отличаться.")
            return None
        return database_paths, foreign_agents_path, excel_paths, output_path, modified_database_path

    def _validate_direct_inputs(self) -> tuple[str, list[str], str, dict[str, object]] | None:
        foreign_agents_path = self.foreign_agents_edit.text().strip()
        excel_paths = self._excel_paths()
        create_report = bool(self.marker_settings["create_excel_report"])
        output_path = (self.output_edit.text().strip() or self._default_output_path()) if create_report else ""
        params = self._irbis_params()

        if not str(params.get("login", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Введите логин каталогизатора ИРБИС.")
            self.workflow_tabs.setCurrentIndex(0)
            return None
        if not str(params.get("database", "")).strip():
            QMessageBox.warning(self, APP_TITLE, "Выберите базу ИРБИС из списка.")
            self.workflow_tabs.setCurrentIndex(0)
            return None
        use_nkp_live = bool(getattr(self, "nkp_live_check", None) and self.nkp_live_check.isChecked())
        use_nkp_foreign_live = bool(
            getattr(self, "nkp_foreign_live_check", None) and self.nkp_foreign_live_check.isChecked()
        )
        if not excel_paths and not foreign_agents_path and not use_nkp_live and not use_nkp_foreign_live:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Выберите хотя бы один источник проверки: актуальный список НКП РГБ или локальный Excel-реестр.",
            )
            self.workflow_tabs.setCurrentIndex(0)
            return None
        missing = [path for path in excel_paths if not Path(path).is_file()]
        if missing:
            QMessageBox.warning(self, APP_TITLE, "Некоторые Excel-файлы не найдены:\n" + "\n".join(missing))
            return None
        if foreign_agents_path and not Path(foreign_agents_path).is_file():
            QMessageBox.warning(self, APP_TITLE, "Файл реестра иностранных агентов не найден.")
            return None
        if foreign_agents_path and Path(foreign_agents_path).suffix.lower() not in {".xlsx", ".xlsm"}:
            QMessageBox.warning(self, APP_TITLE, "Реестр иностранных агентов должен быть файлом .xlsx или .xlsm.")
            return None
        if create_report:
            if not output_path.lower().endswith(".xlsx"):
                output_path += ".xlsx"
                self.output_edit.setText(output_path)
            source_paths = {Path(path).resolve() for path in excel_paths}
            if foreign_agents_path:
                source_paths.add(Path(foreign_agents_path).resolve())
            if Path(output_path).resolve() in source_paths:
                QMessageBox.warning(self, APP_TITLE, "Файл отчёта не должен совпадать с исходным Excel-файлом.")
                return None
        return foreign_agents_path, excel_paths, output_path, params

    def start_comparison(self) -> None:
        if not self._sync_marker_settings_from_ui(save=True, show_message=False):
            return

        direct_mode = self.direct_irbis_checkbox.isChecked()
        if direct_mode:
            validated_direct = self._validate_direct_inputs()
            if not validated_direct:
                return
            foreign_agents_path, excel_paths, output_path, irbis_params = validated_direct
            if self._irbis_connection_state != "success":
                current_status = self._irbis_connection_status_text
                self._auto_check_irbis_connection()
                QMessageBox.warning(
                    self,
                    APP_TITLE,
                    "Запуск остановлен: соединение с ИРБИС не подтверждено.\n\n"
                    f"Текущий статус: {current_status}\n\n"
                    "Программа уже запустила проверку подключения. После появления "
                    "зелёного статуса повторите запуск.",
                )
                self.workflow_tabs.setCurrentIndex(0)
                return
            database_paths: list[str] = []
            modified_database_path = ""
        else:
            validated = self._validate_inputs()
            if not validated:
                return
            database_paths, foreign_agents_path, excel_paths, output_path, modified_database_path = validated
            irbis_params = {}

        self.last_run_direct = direct_mode
        self.last_run_report_only = bool(self.marker_settings.get("report_only", False))
        selected_sources = []
        use_nkp_live = bool(getattr(self, "nkp_live_check", None) and self.nkp_live_check.isChecked())
        use_nkp_foreign_live = bool(
            getattr(self, "nkp_foreign_live_check", None) and self.nkp_foreign_live_check.isChecked()
        )
        if use_nkp_live:
            selected_sources.append("актуальный реестр по наркотическим веществам НКП РГБ")
        if use_nkp_foreign_live:
            selected_sources.append("актуальный реестр изданий иностранных агентов НКП РГБ")
        if excel_paths:
            selected_sources.append(f"локальный реестр по наркотическим веществам ({len(excel_paths)} файл.)")
        if foreign_agents_path and not use_nkp_foreign_live:
            selected_sources.append("локальный реестр иностранных агентов")
        sources_text = ", ".join(selected_sources)

        self.workflow_tabs.setCurrentIndex(2)
        self.result_summary_label.setText(
            "Проверка выполняется. Можно следить за общим состоянием здесь или открыть технический журнал."
        )
        self.progress_dialog.setWindowTitle("Выполнение проверки")
        self.progress_dialog.start("Подготовка к проверке выбранных реестров…", cancellable=True)
        self._append_progress("Подготовка к проверке всех выбранных реестров…")
        self.progress.setValue(0)
        self.progress.show()
        self.open_button.setEnabled(False)
        self.open_modified_database_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.run_tab_start_button.setEnabled(False)
        self.create_matches_excel_button.setEnabled(False)
        self.marker_settings_button.setEnabled(False)
        self.write_irbis_button.setEnabled(False)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(False)
        self._set_status("Подготовка к проверке выбранных реестров…", "running")
        self.last_output_path = output_path
        self.last_modified_database_path = modified_database_path

        if direct_mode:
            self._set_irbis_status("Проверка записей ИРБИС…", "running")
            self._append_progress(
                f"Прямой режим ИРБИС: {irbis_params['host']}:{irbis_params['port']} / {irbis_params['database']}"
            )
            self._append_progress(
                f"Полная TXT-копия не создаётся • пакет чтения: {irbis_params.get('page_size', 500)} записей"
            )
            self._append_progress(f"Запрос выборки: {irbis_params.get('query', 'I=$')}")
        else:
            self._append_progress(f"TXT-баз: {len(database_paths)}")
            for path in database_paths:
                self._append_progress(f"База: {path}")
            self._append_progress(f"TXT-копия результата: {modified_database_path}")

        self._append_progress(f"Будут проверены источники: {sources_text}")
        self._append_progress(
            "НКП РГБ — наркотические вещества: включён (будет загружен перед сравнением)"
            if use_nkp_live
            else "НКП РГБ — наркотические вещества: отключён"
        )
        self._append_progress(
            "НКП РГБ — иностранные агенты: включён (будет загружен перед сравнением)"
            if use_nkp_foreign_live
            else "НКП РГБ — иностранные агенты: отключён"
        )
        self._append_progress(f"Локальных Excel-файлов реестра по наркотическим веществам: {len(excel_paths)}")
        if use_nkp_foreign_live:
            self._append_progress("Локальный реестр иностранных агентов: не используется, выбран актуальный список НКП РГБ")
        else:
            self._append_progress(f"Реестр иностранных агентов: {foreign_agents_path or 'не выбран'}")
        self._append_progress(f"Excel-отчёт: {output_path}" if output_path else "Excel-отчёт: не создаётся")
        substance_marker = (
            str(self.marker_settings["substance_marker"]) if self.marker_settings["substance_marker_enabled"] else ""
        )
        foreign_marker = (
            str(self.marker_settings["foreign_agent_marker_template"])
            if self.marker_settings["foreign_agent_marker_enabled"]
            else ""
        )
        organization_marker = (
            str(self.marker_settings["foreign_organization_marker_template"])
            if self.marker_settings["foreign_organization_marker_enabled"]
            else ""
        )
        age_marker = str(self.marker_settings["age_marker"]) if self.marker_settings["age_marker_enabled"] else ""
        self._append_progress(
            f"Метки: вещества #{int(self.marker_settings['substance_marker_field']):03d} — "
            f"{substance_marker or 'отключена'}; "
            f"иноагенты-авторы #{int(self.marker_settings['foreign_agent_marker_field']):03d} — "
            f"{foreign_marker or 'отключена'}; "
            f"иноагенты-организации #{int(self.marker_settings['foreign_organization_marker_field']):03d} — "
            f"{organization_marker or 'отключена'}; "
            f"#{int(self.marker_settings['age_marker_field']):03d} — "
            f"{age_marker or 'отключена'}."
        )

        report_options = {
            "enabled": bool(self.marker_settings["create_excel_report"]),
            "substances": bool(self.marker_settings["report_substances"]),
            "foreign_agents": bool(self.marker_settings["report_foreign_agents"]),
            "combined": bool(self.marker_settings["report_combined"]),
            "summary": bool(self.marker_settings["report_summary"]),
            "deduplicate": bool(self.marker_settings["report_deduplicate"]),
            "sort": str(self.marker_settings["report_sort"]),
            "report_only": bool(self.marker_settings["report_only"]),
        }
        comparison_options = self._comparison_options()

        self.thread = QThread(self)
        if direct_mode:
            self.worker = DirectIrbisComparisonWorker(
                host=str(irbis_params["host"]),
                port=int(irbis_params["port"]),
                login=str(irbis_params["login"]),
                password=str(irbis_params["password"]),
                database=str(irbis_params["database"]),
                query=str(irbis_params["query"]),
                page_size=int(irbis_params.get("page_size", 500)),
                foreign_agents_path=foreign_agents_path,
                excel_paths=excel_paths,
                use_nkp_drug_registry=use_nkp_live,
                use_nkp_foreign_agents_registry=use_nkp_foreign_live,
                output_path=output_path,
                comparison_options=comparison_options,
                report_options=report_options,
                substance_marker=substance_marker,
                foreign_agent_marker_template=foreign_marker,
                foreign_organization_marker_template=organization_marker,
                age_marker=age_marker,
                substance_marker_field=int(self.marker_settings["substance_marker_field"]),
                foreign_agent_marker_field=int(self.marker_settings["foreign_agent_marker_field"]),
                foreign_organization_marker_field=int(self.marker_settings["foreign_organization_marker_field"]),
                age_marker_field=int(self.marker_settings["age_marker_field"]),
                backup_dir=str(self._app_data_dir() / "backups"),
                create_backup=self.app_settings.create_database_backup,
            )
        else:
            self.worker = ComparisonWorker(
                database_paths,
                foreign_agents_path,
                excel_paths,
                output_path,
                modified_database_path,
                comparison_options,
                report_options,
                substance_marker,
                foreign_marker,
                age_marker,
                int(self.marker_settings["substance_marker_field"]),
                int(self.marker_settings["foreign_agent_marker_field"]),
                int(self.marker_settings["age_marker_field"]),
                organization_marker,
                int(self.marker_settings["foreign_organization_marker_field"]),
                use_nkp_drug_registry=use_nkp_live,
                use_nkp_foreign_agents_registry=use_nkp_foreign_live,
            )
        self._save_irbis_config()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.review_requested.connect(self._review_suspicious_matches)
        if isinstance(self.worker, DirectIrbisComparisonWorker):
            self.worker.preview_requested.connect(self._confirm_direct_changes)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.cancelled.connect(self.thread.quit)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

    @pyqtSlot(object)
    def _review_suspicious_matches(self, payload: object) -> None:
        worker = self.worker
        if not isinstance(worker, (ComparisonWorker, DirectIrbisComparisonWorker)):
            return
        rows = payload if isinstance(payload, list) else []
        valid_rows = [item for item in rows if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int)]
        if not valid_rows:
            worker.confirm_review({})
            return

        self._set_status(f"Ручная проверка: {len(valid_rows):,} подозрительных совпадений", "warning")
        self._append_progress(
            f"Автоматическое решение невозможно для {len(valid_rows):,} совпадений — открыта ручная проверка."
        )
        progress_was_visible = self._hide_progress_for_prompt()
        try:
            dialog = ManualMatchReviewDialog(valid_rows, self)
            accepted = dialog.exec() == dialog.DialogCode.Accepted
        finally:
            self._restore_progress_after_prompt(progress_was_visible)
        if accepted:
            decisions = dialog.decisions()
            approved = sum(decisions.values())
            removed = len(decisions) - approved
            self._append_progress(f"Ручная проверка: подтверждено {approved:,}, убрано {removed:,}.")
            worker.confirm_review(decisions)
        else:
            self._append_progress("Ручная проверка отменена пользователем.")
            worker.confirm_review(None)

    @pyqtSlot(object)
    def _confirm_direct_changes(self, payload: object) -> None:
        """Автоматически разрешает подготовленную запись в ИРБИС без диалога подтверждения."""
        worker = self.worker
        if not isinstance(worker, DirectIrbisComparisonWorker):
            return

        data = payload if isinstance(payload, dict) else {}
        record_count = int(data.get("record_count", 0))
        markers_added = int(data.get("markers_added", 0))
        duplicates_repaired = int(data.get("duplicates_repaired", 0))
        self._append_progress(
            f"Запись в ИРБИС подтверждена автоматически: {record_count:,} записей, "
            f"новых меток: {markers_added:,}, исправлено дублей: {duplicates_repaired:,}."
        )
        worker.confirm_preview(True)

    @pyqtSlot(int, str)
    def on_progress(self, percent: int, text: str) -> None:
        self.progress.show()
        self.progress.setValue(max(0, min(100, percent)))
        self._set_status(text, "running")
        self.progress_dialog.set_progress(percent, text)
        self._append_progress(text)

    @pyqtSlot(object, object)
    def on_finished(self, results: list[MatchResult], summary: ComparisonSummary) -> None:
        self.progress.hide()
        self._refresh_registry_cache_status()
        self.last_results = results
        self.last_summary = summary
        self._log_match_summary(results)
        self.open_button.setEnabled(Path(self.last_output_path).is_file())
        self._save_irbis_config()

        unique_records = len(
            {
                result.database.record_number
                for result in results
                if result.status == "Совпадение" and result.database is not None
            }
        )
        self.result_summary_label.setText(
            f"Проверено записей: {summary.database_records:,} · найдено: {unique_records:,} · "
            f"требуют ручной проверки: {summary.review_rows:,}"
        )

        report_only = self.last_run_report_only
        if self.last_run_direct:
            self._set_irbis_status("ИРБИС доступен • проверка завершена", "success")
        if report_only:
            self.last_modified_database_path = ""
            self.open_modified_database_button.setEnabled(False)
            self.write_irbis_button.setEnabled(False)
            target_name = "ИРБИС" if self.last_run_direct else "TXT-базы"
            self._set_status(
                f"Готово. Отчёт создан; {target_name} не изменялись; проверить вручную: {summary.review_rows}",
                "idle",
            )
            self._append_progress(
                f"Завершено в режиме только отчёта: найдено уникальных записей {unique_records}; "
                f"{target_name} не изменялись."
            )
        elif self.last_run_direct:
            self.last_modified_database_path = ""
            self.open_modified_database_button.setEnabled(False)
            self.write_irbis_button.setEnabled(False)
            self._set_status(
                f"Готово. ИРБИС изменён: {summary.modified_database_records}; "
                f"добавлено меток: {summary.markers_added}; "
                f"уже было: {summary.markers_already_present}; "
                f"исправлено дублей: {summary.marker_duplicates_repaired}",
                "idle",
            )
            self._append_progress(
                f"Завершено: прочитано записей ИРБИС {summary.database_records:,}; "
                f"по веществам совпало {summary.matched_excel_rows} из {summary.excel_rows}; "
                f"по иноагентам найдено {summary.matched_foreign_agent_rows} из {summary.foreign_agent_rows}; "
                f"уникальных найденных MFN: {unique_records}; "
                f"изменено записей непосредственно на сервере: {summary.modified_database_records}."
            )
            self._append_progress(
                f"Метки ИРБИС: добавлено {summary.markers_added}; "
                f"уже существовало {summary.markers_already_present}; "
                f"исправлено дублей {summary.marker_duplicates_repaired}."
            )
        else:
            self.last_modified_database_path = summary.modified_database_file or self.last_modified_database_path
            modified_paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
            self.open_modified_database_button.setEnabled(any(Path(path).is_file() for path in modified_paths))
            self.write_irbis_button.setEnabled(
                bool(
                    len(modified_paths) == 1
                    and Path(modified_paths[0]).is_file()
                    and Path(self.irbis_manifest_edit.text().strip()).is_file()
                )
            )
            self._set_status(
                f"Готово. Вещества: {summary.substance_matched_records}; "
                f"иноагенты: {summary.foreign_agent_matched_records}; "
                f"помечено TXT: {summary.modified_database_records}",
                "idle",
            )
            self._append_progress(
                f"Завершено: по веществам совпало {summary.matched_excel_rows} из {summary.excel_rows}; "
                f"по иноагентам найдено {summary.matched_foreign_agent_rows} из {summary.foreign_agent_rows}; "
                f"всего уникальных записей TXT: {unique_records}; "
                f"в TXT-копии помечено {summary.modified_database_records}."
            )

        if summary.review_rows:
            self._append_progress(
                f"Требуют ручной проверки: {summary.review_rows}. "
                "Они не получили метки и вынесены на отдельный лист Excel."
            )
        if summary.warnings:
            self._append_progress("Предупреждения:")
            for warning in summary.warnings:
                self._append_progress("• " + warning)
        self.progress_dialog.finish("Готово.", 100)

        substance_marker = self.marker_settings["substance_marker"] or "не добавляется"
        foreign_marker = (
            self.marker_settings["foreign_agent_marker_template"].replace("{name}", "АВТОР") or "не добавляется"
        )
        age_marker = self.marker_settings["age_marker"] or "не добавляется"
        substance_field = int(self.marker_settings["substance_marker_field"])
        foreign_field = int(self.marker_settings["foreign_agent_marker_field"])
        age_field = int(self.marker_settings["age_marker_field"])
        report_result = self.last_output_path or "не создавался"

        if report_only:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено в режиме «Только отчёт».\n"
                "Метки не добавлялись, исходные записи не изменялись.\n\n"
                f"Найдено уникальных записей: {unique_records}\n"
                f"Требуют ручной проверки: {summary.review_rows}\n"
                f"Excel-отчёт: {report_result}",
            )
        elif self.last_run_direct:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено. Изменения записаны непосредственно в ИРБИС.\n"
                "Полная TXT-копия базы не создавалась.\n\n"
                "Метки для найденных записей:\n"
                f"• по веществам #{substance_field:03d}: {substance_marker};\n"
                f"• по иноагентам #{foreign_field:03d}: {foreign_marker};\n"
                f"• поле #{age_field:03d}: {age_marker}.\n\n"
                f"Прочитано записей ИРБИС: {summary.database_records}\n"
                f"Изменено записей ИРБИС: {summary.modified_database_records}\n"
                f"Добавлено меток: {summary.markers_added}\n"
                f"Уже существовало: {summary.markers_already_present}\n"
                f"Исправлено дублей: {summary.marker_duplicates_repaired}\n"
                f"Совпавших строк по веществам: {summary.matched_excel_rows}\n"
                f"Иноагентов с совпадениями: {summary.matched_foreign_agent_rows}\n\n"
                f"Требуют ручной проверки: {summary.review_rows}\n\n"
                f"Excel-отчёт: {report_result}",
            )
        else:
            QMessageBox.information(
                self,
                APP_TITLE,
                "Сравнение завершено. Результаты сохранены автоматически.\n\n"
                f"Помечено записей TXT: {summary.modified_database_records}\n"
                f"Требуют ручной проверки: {summary.review_rows}\n"
                f"Excel-отчёт: {report_result}\n"
                f"TXT-копия: {self.last_modified_database_path}",
            )

    @pyqtSlot(str)
    def on_failed(self, error_text: str) -> None:
        self.progress.hide()
        if self.last_run_direct:
            self._set_irbis_status("Ошибка подключения/обмена с ИРБИС", "error")
        self._set_status("Ошибка", "error")
        self.progress_dialog.finish("Ошибка. Подробности показаны ниже.", 0)
        self._append_progress(error_text)
        self.workflow_tabs.setCurrentIndex(2)
        self.log_toggle.setChecked(True)
        QMessageBox.critical(
            self,
            APP_TITLE,
            "Во время сравнения произошла ошибка. Подробности показаны в техническом журнале.",
        )

    @pyqtSlot(str)
    def on_cancelled(self, message: str) -> None:
        self.progress.hide()
        if self.last_run_direct:
            self._set_irbis_status("Операция с ИРБИС отменена", "warning")
        self._set_status(message, "warning")
        self.progress_dialog.finish(message, self.progress.value())

    @pyqtSlot()
    def _cleanup_worker(self) -> None:
        self.progress.hide()
        if self.thread:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.start_button.setEnabled(True)
        self.run_tab_start_button.setEnabled(True)
        self.create_matches_excel_button.setEnabled(True)
        self.marker_settings_button.setEnabled(True)
        if hasattr(self, "cleanup_button"):
            self.cleanup_button.setEnabled(True)

    def _log_match_summary(self, results: list[MatchResult]) -> None:
        unique_records = {
            result.database.record_number
            for result in results
            if result.status == "Совпадение" and result.database is not None
        }
        self._append_progress(f"Подтверждённых записей TXT для отчёта: {len(unique_records)}")
        review_rows = sum(1 for result in results if result.status == "Возможное совпадение")
        if review_rows:
            self._append_progress(f"Пограничных совпадений для ручной проверки: {review_rows}")

    def _append_progress(self, text: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {text}"
        self.progress_dialog.append_line(line)
        if hasattr(self, "run_log") and self._journal_line_matches(line):
            self.run_log.append(line)
        self._journal_lines.append(line)
        self._journal_save_timer.start(250)

    def _journal_line_matches(self, line: str) -> bool:
        normalized = line.casefold()
        search = self.journal_search_edit.text().strip().casefold() if hasattr(self, "journal_search_edit") else ""
        if search and search not in normalized:
            return False
        category = (
            str(self.journal_filter_combo.currentData() or "all") if hasattr(self, "journal_filter_combo") else "all"
        )
        keywords = {
            "errors": ("ошиб", "не выполн", "отмен", "конфликт"),
            "matches": ("совпад", "найден", "подтвержд"),
            "changes": ("измен", "метк", "запис", "отправ", "очистк", "rollback"),
        }
        selected = keywords.get(category)
        return selected is None or any(keyword in normalized for keyword in selected)

    def _filtered_journal_lines(self) -> list[str]:
        return [line for line in self._journal_lines if self._journal_line_matches(line)]

    def _refresh_run_log_view(self, *_args) -> None:
        if not hasattr(self, "run_log"):
            return
        self.run_log.setPlainText("\n".join(self._filtered_journal_lines()))
        scrollbar = self.run_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _load_run_journal(self) -> None:
        try:
            lines = self._run_journal_path().read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        self._journal_lines.extend(lines[-self.RUN_JOURNAL_MAX_LINES :])
        self._refresh_run_log_view()

    def _save_run_journal(self) -> None:
        try:
            path = self._run_journal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = "\n".join(self._journal_lines)
            atomic_write_text(path, payload + ("\n" if payload else ""), encoding="utf-8")
        except OSError:
            # Ошибка журнала не должна прерывать основную проверку базы.
            pass

    def export_run_journal(self) -> None:
        lines = self._filtered_journal_lines()
        if not lines:
            QMessageBox.information(self, APP_TITLE, "Нет строк журнала для сохранения.")
            return
        initial = Path.home() / "Documents" / f"ИРБИС64_журнал_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить журнал",
            str(initial),
            "Текстовые файлы (*.txt)",
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            atomic_write_text(path, "\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить журнал:\n{exc}")
            return
        QMessageBox.information(
            self,
            APP_TITLE,
            f"Сохранено строк журнала: {len(lines)}\n{path}",
        )

    def open_report(self) -> None:
        if self.last_output_path and Path(self.last_output_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output_path))
        else:
            QMessageBox.warning(self, APP_TITLE, "Файл отчёта не найден.")

    def open_modified_database(self) -> None:
        paths = [path.strip() for path in self.last_modified_database_path.split(";") if path.strip()]
        if paths and Path(paths[0]).is_file():
            target = Path(paths[0]).parent if len(paths) > 1 else Path(paths[0])
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        else:
            QMessageBox.warning(self, APP_TITLE, "TXT-копия не найдена.")

    def closeEvent(self, event) -> None:
        if getattr(self, "nkp_refresh_thread", None) is not None and self.nkp_refresh_thread.isRunning():
            QMessageBox.information(self, APP_TITLE, "Дождитесь завершения обновления реестра НКП РГБ.")
            event.ignore()
            return
        if self.update_thread and self.update_thread.isRunning() and not self._installing_update:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "Дождитесь завершения проверки или загрузки обновления.",
            )
            event.ignore()
            return
        if self.worker and self.thread and self.thread.isRunning():
            answer = QMessageBox.question(
                self,
                APP_TITLE,
                "Сравнение ещё выполняется. Отменить его и закрыть программу?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_cancel()
            if not self.thread.wait(10000):
                QMessageBox.warning(
                    self,
                    APP_TITLE,
                    "Операция ещё завершается. Закройте программу после появления сообщения об отмене.",
                )
                event.ignore()
                return
        if self.irbis_thread and self.irbis_thread.isRunning():
            QMessageBox.warning(self, APP_TITLE, "Дождитесь завершения операции с ИРБИС перед закрытием программы.")
            event.ignore()
            return
        try:
            self._save_irbis_config()
        except Exception:
            # Ошибка сохранения настроек не должна мешать закрытию приложения.
            pass
        try:
            self._save_window_state()
        except Exception:
            # Геометрию можно безопасно восстановить значениями по умолчанию.
            pass
        self._save_run_journal()
        event.accept()
