import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from irbis_control.application.settings import THEME_DARK, THEME_SYSTEM, ApplicationSettings
from irbis_control.core.models import ComparisonOptions
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
        self.assertEqual(len(window.section_cards), 8)
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

        window.output_mode_combo.setCurrentIndex(window.output_mode_combo.findData("report"))
        self.assertTrue(window.create_excel_report_check.isChecked())
        self.assertTrue(window.report_only_check.isChecked())

        self.assertFalse(window.advanced_options_toggle.isCheckable())
        self.assertFalse(window.advanced_settings_dialog.isVisible())

        self.assertFalse(window.connection_settings_button.isCheckable())
        self.assertTrue(window.connection_card.isHidden())
        self.assertTrue(window.base_card.isHidden())

        settings_page = window.application_settings_page
        settings_page.backup_check.setChecked(False)
        settings_page.auto_updates_check.setChecked(False)
        settings_page.theme_combo.setCurrentIndex(settings_page.theme_combo.findData(THEME_DARK))
        settings_page.reset_settings_button.click()
        defaults = ApplicationSettings()
        self.assertEqual(settings_page.backup_check.isChecked(), defaults.create_database_backup)
        self.assertEqual(settings_page.auto_updates_check.isChecked(), defaults.check_updates_on_start)
        self.assertEqual(settings_page.theme_combo.currentData(), THEME_SYSTEM)

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

    def test_source_registries_have_room_and_do_not_leave_stale_window_space(self) -> None:
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
        for card in (window.foreign_agents_card, window.excel_card):
            self.assertEqual(card.height(), card.sizeHint().height())
            self.assertLessEqual(
                card.height() - card.body.geometry().bottom(),
                card.outer_layout.contentsMargins().bottom() + 2,
            )
        page_layout = window.data_tab.layout()
        last_card_bottom = max(
            window.foreign_agents_card.geometry().bottom(),
            window.excel_card.geometry().bottom(),
        )
        self.assertLessEqual(
            window.data_tab.height() - last_card_bottom,
            page_layout.contentsMargins().bottom() + 3,
        )

        self.assertGreater(window.maximumWidth(), window.width())
        self.assertGreater(window.maximumHeight(), window.height())

        old_window_height = window.height()
        window.excel_resize_handle._set_height(144)
        self.app.processEvents()
        window._fit_scroll_content()
        self.app.processEvents()
        self.assertEqual(window.excel_list.height(), 144)
        self.assertGreater(window.height(), old_window_height)

        window._update_excel_summary()
        self.assertEqual(window.excel_list.height(), 144)

        window.excel_resize_handle._set_height(58)
        window.foreign_agents_resize_handle._set_height(58)
        self.app.processEvents()
        window._resize_height_to_current_page()
        self.app.processEvents()
        last_card_bottom = max(
            window.foreign_agents_card.geometry().bottom(),
            window.excel_card.geometry().bottom(),
        )
        self.assertLessEqual(
            window.data_tab.height() - last_card_bottom,
            page_layout.contentsMargins().bottom() + 3,
        )

        window.resize(900, 700)
        self.app.processEvents()
        window.excel_resize_handle._set_height(164)
        self.app.processEvents()
        self.assertEqual(window.size().width(), 900)
        self.assertEqual(window.size().height(), 700)

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

    def test_progress_dialog_shows_percent_and_locks_close_while_running(self) -> None:
        dialog = dialogs.ProgressDialog("Выполнение", None)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        dialog.start("Подготовка…")
        self.app.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assertEqual("Выполнено: %p%", dialog.progress.format())
        self.assertFalse(dialog.close_button.isEnabled())

        dialog.set_progress(42, "Обработано 42 записи")
        self.assertEqual(42, dialog.progress.value())
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
        self.assertIn("QTableWidget#manualReviewTable::item:hover", window.styleSheet())
        self.assertIn("selection-background-color:", window.styleSheet())

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
