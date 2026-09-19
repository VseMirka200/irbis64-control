import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from irbis_control.application.settings import ApplicationSettings
from irbis_control.ui import db_connector_window, dialogs, main_window, widgets, workers


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
        self.assertEqual(window.workflow_tabs.count(), 6)
        self.assertEqual(len(window.section_cards), 8)
        for index in range(5):
            window.workflow_tabs.setCurrentIndex(index)
            window.resize(800, 600)
            self.app.processEvents()
            self.assertIsNotNone(window.workflow_tabs.currentWidget().layout())
        window.open_application_settings()
        self.assertIs(window.workflow_tabs.currentWidget(), window.application_settings_page)
        window._close_application_settings()
        self.assertIs(window.workflow_tabs.currentWidget(), window.results_tab)

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
