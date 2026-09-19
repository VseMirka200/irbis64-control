import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QStyle, QStyleOptionViewItem

from irbis_control.application.settings import THEME_DARK, THEME_SYSTEM, ApplicationSettings
from irbis_control.core.matcher import SOURCE_SUBSTANCES
from irbis_control.core.models import ComparisonOptions, DatabaseRecord, ExcelEntry, MatchResult
from irbis_control.ui import db_connector_window, main_window
from irbis_control.ui.components import dialogs, widgets
from irbis_control.ui.services import workers
from irbis_control.ui.windows import main_run
from irbis_control.ui.windows.connection_dialog import IrbisConnectionDialog


# Проверяем сборку окон и сигналы Qt без сети и без доступа к настройкам пользователя.
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in (
            "window_state_path",
            "run_journal_path",
            "database_connector_config_path",
            "_marker_settings_path",
            "application_settings_path",
        ):
            self.stack.enter_context(patch.object(main_window, name, return_value=self.folder / name))
        self.stack.enter_context(patch.object(main_window, "app_data_dir", return_value=self.folder))
        self.stack.enter_context(
            patch.object(
                main_window, "load_application_settings", return_value=ApplicationSettings(check_updates_on_start=False)
            )
        )
        self.stack.enter_context(patch.object(main_window.MainWindow, "_auto_check_irbis_connection"))
        self.stack.enter_context(
            patch.object(db_connector_window, "config_path", return_value=self.folder / "connector.json")
        )
        self.stack.enter_context(
            patch.object(dialogs.UsefulLinksDialog, "_storage_path", return_value=self.folder / "links.json")
        )

    def test_main_window_tabs_and_settings_navigation(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.workflow_tabs.count(), 4)
        self.assertFalse(hasattr(window, "section_cards"))
        self.assertEqual(
            [window.workflow_tabs.tabText(index) for index in range(3)],
            ["Данные", "Параметры", "Результат"],
        )
        for index in range(3):
            window.workflow_tabs.setCurrentIndex(index)
            window.resize(800, 600)
            self.app.processEvents()
            self.assertIsNotNone(window.workflow_tabs.currentWidget().layout())
        window.open_application_settings()
        self.assertIs(window.workflow_tabs.currentWidget(), window.application_settings_page)
        window._close_application_settings()
        self.assertIs(window.workflow_tabs.currentWidget(), window.results_tab)

        window.maintenance_dialog.show()
        self.app.processEvents()
        self.assertTrue(window.cleanup_button.isVisible())
        self.assertGreaterEqual(window.cleanup_button.width(), 190)
        window.maintenance_dialog.close()

    def test_simplified_workflow_controls_existing_settings(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)

        window.source_mode_combo.setCurrentIndex(window.source_mode_combo.findData("txt"))
        self.assertFalse(window.direct_irbis_checkbox.isChecked())
        self.assertIn("TXT", window.source_mode_hint.text())
        self.assertFalse(hasattr(window, "download_registries_label"))
        self.assertEqual("Полезные ссылки", window.source_useful_links_button.text())
        self.assertIs(window.source_mode_combo.parentWidget(), window.source_useful_links_button.parentWidget())

        window.output_mode_combo.setCurrentIndex(window.output_mode_combo.findData("report"))
        self.assertFalse(hasattr(window, "create_excel_report_check"))
        self.assertFalse(hasattr(window, "report_only_check"))
        values = window._marker_values_from_ui()
        self.assertTrue(values["create_excel_report"])
        self.assertTrue(values["report_only"])

        self.assertFalse(window.advanced_options_toggle.isCheckable())
        self.assertFalse(window.advanced_settings_dialog.isVisible())
        self.assertFalse(window.match_rules_editor.isHidden())
        self.assertFalse(window.fuzzy_match_check.isHidden())

        self.assertFalse(window.connection_settings_button.isCheckable())
        self.assertFalse(hasattr(window, "connection_card"))
        self.assertFalse(hasattr(window, "base_card"))
        self.assertTrue(window._irbis_state_container.isHidden())

        settings_page = window.application_settings_page
        settings_page.backup_check.setChecked(False)
        settings_page.auto_updates_check.setChecked(False)
        settings_page.theme_combo.setCurrentIndex(settings_page.theme_combo.findData(THEME_DARK))
        settings_page.reset_settings_button.click()
        defaults = ApplicationSettings()
        self.assertEqual(settings_page.backup_check.isChecked(), defaults.create_database_backup)
        self.assertEqual(settings_page.auto_updates_check.isChecked(), defaults.check_updates_on_start)
        self.assertEqual(settings_page.theme_combo.currentData(), THEME_SYSTEM)

    def test_match_rules_editor_is_managed_by_card_layout(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)

        # Регрессия: редактор правил раньше был только дочерним виджетом карточки,
        # но отсутствовал в layout. После show() он появлялся в (0, 0) и
        # перекрывал заголовок карточки.
        body_widgets = [
            window.match_settings_card.body.itemAt(index).widget()
            for index in range(window.match_settings_card.body.count())
        ]
        self.assertIn(window.match_rules_editor, body_widgets)
        self.assertIs(window.match_rules_editor.parentWidget(), window.match_settings_card)

        window.open_advanced_settings()
        self.app.processEvents()
        title_rect = window.match_settings_card.title_label.geometry()
        editor_rect = window.match_rules_editor.geometry()
        self.assertGreaterEqual(editor_rect.top(), title_rect.bottom())
        window.advanced_settings_dialog.close()

    def test_comparison_options_include_saved_match_rules(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.marker_settings["use_isbn_matching"] = False
        window.marker_settings["use_title_fallback"] = True
        window.marker_settings["use_fuzzy"] = True
        window.marker_settings["fuzzy_threshold"] = 94
        window.marker_settings["title_year"] = True

        options = window._comparison_options()

        self.assertFalse(options.use_isbn_matching)
        self.assertTrue(options.use_title_fallback)
        self.assertTrue(options.use_fuzzy)
        self.assertEqual(94, options.fuzzy_threshold)
        self.assertTrue(options.match_rules["title_year"])

    def test_window_geometry_is_restored_without_disabling_resize(self) -> None:
        first = main_window.MainWindow()
        self.addCleanup(first.deleteLater)
        first.show()
        self.app.processEvents()
        first.resize(700, 600)
        first.move(40, 50)
        self.app.processEvents()
        saved_state: list[str] = []
        with patch.object(
            main_window,
            "atomic_write_text",
            side_effect=lambda _path, text, **_kwargs: saved_state.append(text),
        ):
            first._save_window_state()
        first.close()

        state_path = Mock()
        state_path.read_text.return_value = saved_state[0]
        with patch.object(main_window, "window_state_path", return_value=state_path):
            second = main_window.MainWindow()
        self.addCleanup(second.deleteLater)
        self.addCleanup(second.close)
        second.show()
        self.app.processEvents()

        self.assertEqual(second.size().width(), 700)
        self.assertEqual(second.size().height(), 600)
        self.assertGreater(second.maximumWidth(), second.width())
        self.assertGreater(second.maximumHeight(), second.height())

    def test_source_registries_have_stable_height_without_manual_resize_handles(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()

        window._update_foreign_agents_summary()
        window._update_excel_summary()
        self.app.processEvents()
        window._fit_scroll_content()
        self.app.processEvents()

        self.assertEqual(window.foreign_agents_list.height(), 84)
        self.assertEqual(window.excel_list.height(), 84)
        self.assertFalse(hasattr(window, "foreign_agents_resize_handle"))
        self.assertFalse(hasattr(window, "excel_resize_handle"))
        self.assertFalse(hasattr(window, "download_registries_label"))
        self.assertFalse(hasattr(window, "clear_all_button"))

        initial_size = window.size()
        window._update_excel_summary()
        window._update_foreign_agents_summary()
        self.app.processEvents()
        self.assertEqual(window.size(), initial_size)
        self.assertGreater(window.maximumWidth(), window.width())
        self.assertGreater(window.maximumHeight(), window.height())

    def test_connection_dialog_returns_edited_values(self) -> None:
        dialog = IrbisConnectionDialog(
            {
                "host": "127.0.0.1",
                "port": 6666,
                "login": "reader",
                "password": "secret",
                "database": "IBIS",
                "query": "I=$",
                "page_size": 500,
            },
            [("IBIS — Основной каталог", "IBIS")],
        )
        self.addCleanup(dialog.deleteLater)
        dialog.host_edit.setText("10.0.0.5")
        dialog.database_combo.setEditText("BOOKS")
        dialog.page_size_spin.setValue(800)

        values = dialog.values()
        self.assertEqual(values["host"], "10.0.0.5")
        self.assertEqual(values["database"], "BOOKS")
        self.assertEqual(values["page_size"], 800)

    def test_irbis_connection_settings_persist_between_windows(self) -> None:
        first = main_window.MainWindow()
        self.addCleanup(first.deleteLater)
        self.addCleanup(first.close)
        first.irbis_host_edit.setText("10.20.30.40")
        first.irbis_port_spin.setValue(7777)
        first.irbis_login_edit.setText("cataloger")
        first.irbis_password_edit.setText("saved-password")
        first.irbis_db_combo.addItem("BOOKS", "BOOKS")
        first.irbis_db_combo.setCurrentIndex(first.irbis_db_combo.findData("BOOKS"))
        first.irbis_query_edit.setText("A=SMITH$")
        first.irbis_page_size_spin.setValue(900)
        first._save_irbis_config()

        second = main_window.MainWindow()
        self.addCleanup(second.deleteLater)
        self.addCleanup(second.close)
        self.assertEqual(second.irbis_host_edit.text(), "10.20.30.40")
        self.assertEqual(second.irbis_port_spin.value(), 7777)
        self.assertEqual(second.irbis_login_edit.text(), "cataloger")
        self.assertEqual(second.irbis_password_edit.text(), "saved-password")
        self.assertEqual(second._current_irbis_database(), "BOOKS")
        self.assertEqual(second.irbis_query_edit.text(), "A=SMITH$")
        self.assertEqual(second.irbis_page_size_spin.value(), 900)

    def test_auxiliary_windows_construct(self) -> None:
        for factory in (
            db_connector_window.ConnectorWindow,
            dialogs.UsefulLinksDialog,
            dialogs.ResultComparisonDialog,
            dialogs.TextComparisonDialog,
        ):
            with self.subTest(window=factory.__name__):
                window = factory()
                try:
                    window.show()
                    self.app.processEvents()
                    self.assertTrue(window.isVisible())
                finally:
                    window.close()
                    window.deleteLater()

    def test_review_memory_dialog_lists_approved_and_rejected_decisions(self) -> None:
        memory_path = self.folder / "review_memory.json"
        common = {
            "database_value": "Фаулз Джон",
            "registry_value": "Джон Фаулз",
            "registry_number": "1",
            "method": "Автор",
        }
        memory_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "approved": [
                        {"key": "approved", "confirmed_at": "2026-09-18T10:00:00+00:00", **common}
                    ],
                    "rejected": [
                        {"key": "rejected", "rejected_at": "2026-09-18T11:00:00+00:00", **common}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        dialog = dialogs.ConfirmationMemoryDialog(memory_path)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        decisions = {dialog.table.item(row, 4).text() for row in range(dialog.table.rowCount())}
        self.assertEqual({"Подтверждено", "Отклонено"}, decisions)
        self.assertEqual(7, dialog.table.columnCount())

    def test_progress_dialog_shows_percent_and_locks_close_while_running(self) -> None:
        dialog = dialogs.ProgressDialog("Выполнение", None)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        dialog.start("Подготовка…")
        self.app.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assertEqual("Выполнено: %p%", dialog.progress.format())
        self.assertFalse(dialog.progress.isTextVisible())
        self.assertEqual("Выполнено: 0%", dialog.progress_label.text())
        self.assertFalse(dialog.close_button.isEnabled())

        dialog.set_progress(42, "Обработано 42 записи")
        self.assertEqual(42, dialog.progress.value())
        self.assertEqual("Выполнено: 42%", dialog.progress_label.text())
        self.assertEqual("Обработано 42 записи", dialog.status_label.text())

        dialog.finish("Готово", 100)
        self.assertEqual(100, dialog.progress.value())
        self.assertTrue(dialog.close_button.isEnabled())

    def test_manual_review_table_does_not_use_blue_selection(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        dialog = dialogs.ManualMatchReviewDialog([], window)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        self.assertEqual("manualReviewTable", dialog.table.objectName())
        self.assertIn("QTableWidget#manualReviewTable::item:selected", window.styleSheet())
        self.assertNotIn("QTableWidget#manualReviewTable::item:hover", window.styleSheet())
        self.assertIn("selection-background-color:", window.styleSheet())
        option = QStyleOptionViewItem()
        option.state |= QStyle.StateFlag.State_MouseOver
        clean_option = dialog.table.itemDelegate()._without_hover(option)
        self.assertFalse(clean_option.state & QStyle.StateFlag.State_MouseOver)

    def test_manual_review_confirms_all_copies_of_the_same_book(self) -> None:
        entry = ExcelEntry(1, "substances.xlsx", "Книги", 2, author="Прилепин З. Захар", title="Обитель")
        rows = []
        for index, mfn in enumerate((3604, 5283)):
            record = DatabaseRecord(
                record_number=mfn,
                titles=["Обитель"],
                authors=["Прилепин З. Захар"],
                primary_authors=["Прилепин З. Захар"],
            )
            rows.append(
                (
                    index,
                    MatchResult(
                        status="Возможное совпадение",
                        method="Название и неполные данные автора",
                        confidence=90.0,
                        excel=entry,
                        database=record,
                        source_type=SOURCE_SUBSTANCES,
                        matched_value=entry.title,
                    ),
                )
            )
        dialog = dialogs.ManualMatchReviewDialog(rows)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        dialog._set_decision(0, True)

        self.assertEqual({0: True, 1: True}, dialog._decisions)
        self.assertEqual("✓ Подтверждено", dialog._action_buttons[1][0].text())
        self.assertIn("background-color", dialog._action_containers[0].styleSheet())
        self.assertGreater(dialog.table.item(0, 1).background().color().alpha(), 0)
        self.assertNotIn("border: 2px", dialog._action_containers[0].styleSheet())

        dialog._set_decision(1, False)

        self.assertEqual(1, dialog.table.rowCount())
        self.assertEqual(False, dialog._decisions[1])
        self.assertNotIn(1, dialog._result_table_rows)

    def test_manual_review_temporarily_releases_progress_dialog_modality(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        worker = workers.ComparisonWorker(
            [],
            "",
            [],
            "",
            "",
            ComparisonOptions(),
            {},
            "",
            "",
            "",
            333,
            333,
            900,
        )
        window.worker = worker
        window.progress_dialog.start("Требуется ручная проверка…")
        visible_during_prompt: list[bool] = []
        review_dialog = Mock()
        review_dialog.DialogCode = QDialog.DialogCode
        review_dialog.exec.side_effect = lambda: (
            visible_during_prompt.append(window.progress_dialog.isVisible()),
            QDialog.DialogCode.Rejected,
        )[1]

        with patch.object(main_run, "ManualMatchReviewDialog", return_value=review_dialog):
            window._review_suspicious_matches([(0, Mock())])

        self.assertEqual([False], visible_during_prompt)
        self.assertTrue(window.progress_dialog.isVisible())
        self.assertTrue(worker.review_event.is_set())
        window.progress_dialog.finish("Готово")

    def test_match_rules_reject_duplicates_and_keep_last_rule(self) -> None:
        editor = widgets.MatchRulesEditor()
        self.addCleanup(editor.deleteLater)
        editor.set_values({"use_isbn_matching": True})
        changes = []
        editor.changed.connect(lambda: changes.append(True))
        combo = editor.fields_combo
        for index in range(combo.count()):
            if combo.itemData(index) == "isbn":
                combo.model().item(index).setCheckState(Qt.CheckState.Checked)
        editor.add_rule()
        self.assertEqual(editor.rules.count(), 1)
        self.assertEqual(changes, [])
        editor.rules.setCurrentRow(0)
        editor.remove_rule()
        self.assertEqual(editor.rules.count(), 1)

    def test_marker_settings_ignore_boolean_field_number(self) -> None:
        path = main_window._marker_settings_path()
        path.write_text('{"age_marker_field": true}', encoding="utf-8")
        settings = main_window.load_marker_settings()
        self.assertEqual(settings["age_marker_field"], main_window.DEFAULT_MARKER_SETTINGS["age_marker_field"])
        self.assertIs(type(settings["age_marker_field"]), int)

    def test_legacy_class_imports_are_preserved(self) -> None:
        self.assertIs(main_window.ComparisonWorker, workers.ComparisonWorker)
        self.assertIs(main_window.ResultComparisonDialog, dialogs.ResultComparisonDialog)
        self.assertIs(main_window.MatchRulesEditor, widgets.MatchRulesEditor)


if __name__ == "__main__":
    unittest.main()
