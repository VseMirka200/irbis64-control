import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, QEvent, QMimeData, QObject, Qt, QUrl
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from irbis_control.application.settings import THEME_DARK, ApplicationSettings
from irbis_control.core.matcher import SOURCE_SUBSTANCES
from irbis_control.core.models import ComparisonOptions, DatabaseRecord, ExcelEntry, MatchResult
from irbis_control.infrastructure.irbis_models import IrbisField, IrbisRecord
from irbis_control.ui import context_menu, db_connector_window, main_window
from irbis_control.ui.components import dialogs, manual_review, widgets
from irbis_control.ui.locale import _RussianButtonTranslator
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

    def test_fallback_translator_does_not_erase_standard_qt_labels(self) -> None:
        translator = _RussianButtonTranslator()

        self.assertEqual("Открыть", translator.translate("QPlatformTheme", "Open"))
        self.assertIsNone(translator.translate("QFileDialog", "File name:"))

    def test_update_download_and_install_are_separate_actions(self) -> None:
        available = dialogs.UpdateAvailableDialog(
            current_version="1.0.0",
            release_version="1.1.0",
            release_notes="Исправления",
            page_url="https://github.com/example/release",
            asset_name="IRBIS64Control-Setup-1.1.0.exe",
            can_download=True,
        )
        self.addCleanup(available.deleteLater)
        self.addCleanup(available.close)
        available_buttons = {button.text(): button for button in available.findChildren(QPushButton)}
        self.assertIn("Скачать новую версию", available_buttons)
        self.assertNotIn("Установить", available_buttons)
        available_buttons["Скачать новую версию"].click()
        self.assertTrue(available.download_requested)

        package = self.folder / "IRBIS64Control-Setup-1.1.0.exe"
        package.write_bytes(b"installer")
        ready = dialogs.UpdateReadyDialog(release_version="1.1.0", package_path=package)
        self.addCleanup(ready.deleteLater)
        self.addCleanup(ready.close)
        ready_buttons = {button.text(): button for button in ready.findChildren(QPushButton)}
        self.assertIn("Установить", ready_buttons)
        self.assertNotIn("Скачать новую версию", ready_buttons)
        ready_buttons["Установить"].click()
        self.assertTrue(ready.install_requested)

    def test_context_menu_owner_stops_at_parentless_object(self) -> None:
        parentless = QObject()
        self.assertIsNone(context_menu._context_owner(parentless))
        manager = context_menu.ContextMenuManager()
        event = QEvent(QEvent.Type.ContextMenu)
        self.assertFalse(manager.eventFilter(parentless, event))

    def test_context_menu_has_transparent_corners_and_hidden_shortcuts(self) -> None:
        menu = context_menu.new_context_menu()
        self.addCleanup(menu.deleteLater)
        action = menu.add_app_action(
            "Копировать",
            lambda: None,
        )

        self.assertTrue(menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(action.shortcut().isEmpty())

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
        window.workflow_tabs.setCurrentWidget(window.results_tab)
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
        self.assertFalse(window.database_card.isHidden())
        self.assertFalse(window.database_list.isHidden())
        self.assertFalse(window.database_button.isHidden())
        window.source_mode_combo.setCurrentIndex(window.source_mode_combo.findData("irbis"))
        self.assertFalse(window.open_modified_database_button.isHidden())
        self.assertEqual("Открыть копию базы данных", window.open_modified_database_button.text())
        window.source_mode_combo.setCurrentIndex(window.source_mode_combo.findData("txt"))
        self.assertFalse(hasattr(window, "download_registries_label"))
        self.assertEqual("Полезные ссылки", window.source_useful_links_button.text())
        self.assertIs(window.source_mode_combo.parentWidget(), window.source_useful_links_button.parentWidget())
        window.resize(900, 650)
        window.show()
        window.workflow_tabs.setCurrentWidget(window.data_tab)
        self.app.processEvents()
        self.assertGreater(window.source_mode_combo.width(), window.source_mode_combo.minimumWidth())
        self.assertEqual(
            window.source_mode_combo.geometry().right() + 8,
            window.source_useful_links_button.geometry().left() - 1,
        )

        window.output_mode_combo.setCurrentIndex(window.output_mode_combo.findData("report"))
        self.assertFalse(hasattr(window, "create_excel_report_check"))
        self.assertFalse(hasattr(window, "report_only_check"))
        values = window._marker_values_from_ui()
        self.assertTrue(values["create_excel_report"])
        self.assertTrue(values["report_only"])
        window.workflow_tabs.setCurrentWidget(window.parameters_tab)
        self.app.processEvents()
        self.assertGreater(window.output_mode_combo.width(), window.output_mode_combo.minimumWidth())

        self.assertFalse(window.advanced_options_toggle.isCheckable())
        self.assertFalse(window.advanced_settings_dialog.isVisible())
        self.assertFalse(window.match_rules_editor.isHidden())
        self.assertFalse(window.fuzzy_match_check.isHidden())

        self.assertFalse(window.connection_settings_button.isCheckable())
        self.assertFalse(hasattr(window, "connection_card"))
        self.assertFalse(hasattr(window, "base_card"))
        self.assertFalse(hasattr(window, "reset_workspace_button"))
        self.assertTrue(window._irbis_state_container.isHidden())

        settings_page = window.application_settings_page
        self.assertFalse(hasattr(settings_page, "content_scroll"))
        self.assertFalse(hasattr(settings_page, "reset_settings_button"))
        self.assertFalse(hasattr(settings_page, "save_settings_button"))
        self.assertFalse(hasattr(settings_page, "cancel_settings_button"))
        self.assertFalse(hasattr(window, "advanced_footer"))
        self.assertFalse(hasattr(window, "reset_advanced_button"))
        self.assertFalse(hasattr(window, "save_advanced_button"))
        self.assertFalse(hasattr(window, "cancel_advanced_button"))
        settings_page.auto_updates_check.setChecked(False)
        settings_page.theme_combo.setCurrentIndex(settings_page.theme_combo.findData(THEME_DARK))
        self.app.processEvents()
        self.assertFalse(window.app_settings.check_updates_on_start)
        self.assertEqual(window.app_settings.theme, THEME_DARK)

    def test_source_paths_are_added_without_duplicates(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        source = self.folder / "registry.xlsx"

        self.assertTrue(window._add_source_paths(window.excel_list, [str(source)]))
        equivalent_source = source.parent / "unused" / ".." / source.name
        self.assertFalse(window._add_source_paths(window.excel_list, [str(equivalent_source)]))
        self.assertEqual(window._excel_paths(), [str(source)])

    def test_direct_worker_saves_full_database_copy(self) -> None:
        worker = workers.DirectIrbisComparisonWorker(
            host="127.0.0.1",
            port=6666,
            login="user",
            password="password",
            database="IBIS",
            query="I=$",
            page_size=500,
            foreign_agents_path="",
            excel_paths=[],
            output_path="",
            comparison_options=ComparisonOptions(),
            report_options={},
            substance_marker="^AIII",
            foreign_agent_marker_template="^AI^@{name}",
            age_marker="^Z18+",
            substance_marker_field=333,
            foreign_agent_marker_field=333,
            age_marker_field=900,
            backup_dir=str(self.folder / "backups"),
        )
        self.addCleanup(worker.deleteLater)

        saved = worker._save_database_copy(
            [IrbisRecord(7, 0, 1, [IrbisField(200, "^AКнига"), IrbisField(910, "^BИНВ-7")])]
        )

        self.assertIsNotNone(saved)
        self.assertTrue(saved.is_file())
        self.assertIn("#200: ^AКнига", saved.read_text(encoding="utf-8"))
        self.assertIn("#910: ^BИНВ-7", saved.read_text(encoding="utf-8"))

    def test_all_main_window_dropdowns_use_the_shared_popup(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()

        combos = (
            window.source_mode_combo,
            window.output_mode_combo,
            window.report_sort_combo,
            window.journal_filter_combo,
            window.application_settings_page.theme_combo,
            window.irbis_db_combo,
            window.match_rules_editor.fields_combo,
        )
        self.assertTrue(all(isinstance(combo, widgets.AppComboBox) for combo in combos))

        combo = window.source_mode_combo
        combo.setCurrentIndex(combo.findData("irbis"))
        combo.showPopup()
        self.app.processEvents()
        popup = combo._app_popup
        self.assertIsNotNone(popup)
        self.assertTrue(popup.isVisible())
        self.assertTrue(popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay))
        self.assertEqual(popup.width(), combo.width())
        margins = popup.layout().contentsMargins()
        self.assertEqual((0, 0, 0, 0), (margins.left(), margins.top(), margins.right(), margins.bottom()))
        visible_rows = combo.count() - 1
        self.assertEqual(visible_rows * combo.height() + 2, popup.height())
        self.assertTrue(popup.list_view.isRowHidden(combo.currentIndex()))
        self.assertFalse(popup.list_view.currentIndex().isValid())
        self.assertIsInstance(popup.list_view.itemDelegate(), widgets.ComboPopupItemDelegate)
        self.assertEqual(0, popup.list_view.viewport().palette().color(QPalette.ColorRole.Base).alpha())
        self.assertFalse(popup.list_view.viewport().autoFillBackground())
        self.assertTrue(popup._border_overlay.isVisible())
        self.assertEqual(popup.rect(), popup._border_overlay.geometry())
        combo.showPopup()
        self.assertFalse(popup.isVisible())
        combo.showPopup()
        self.app.processEvents()
        self.assertTrue(popup.isVisible())
        txt_index = combo.findData("txt")
        popup.list_view.clicked.emit(combo.model().index(txt_index, combo.modelColumn()))
        self.assertEqual(combo.currentData(), "txt")
        self.assertFalse(popup.isVisible())

    def test_confirmation_memory_button_is_regular_size_and_centred_on_full_text_block(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        window.workflow_tabs.setCurrentWidget(window.parameters_tab)
        self.app.processEvents()

        title = window.confirmation_memory_card.title_label.geometry()
        hint = window.confirmation_memory_hint.geometry()
        button = window.confirmation_memory_button.geometry()
        text_center = (title.top() + hint.bottom()) // 2
        self.assertLess(window.confirmation_memory_button.height(), hint.height() + title.height())
        self.assertLessEqual(abs(button.center().y() - text_center), 2)

    def test_settings_use_only_the_main_scroll_area(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.resize(560, 420)
        window.show()
        window.open_application_settings()
        self.app.processEvents()
        window._fit_scroll_content()
        self.app.processEvents()

        self.assertFalse(hasattr(window.application_settings_page, "content_scroll"))
        self.assertEqual(
            window.scroll_area.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertEqual(
            window.advanced_scroll.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertGreater(window.scroll_area.verticalScrollBar().maximum(), 0)

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

    def test_match_rules_editor_can_be_collapsed_without_losing_rules(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.advanced_settings_dialog.show()
        self.app.processEvents()
        initial_values = window.match_rules_editor.values()

        window.match_rules_toggle.click()
        self.app.processEvents()

        self.assertTrue(window.match_rules_editor.isHidden())
        self.assertEqual(window.match_rules_toggle.text(), "Развернуть")
        self.assertEqual(window.match_rules_editor.values(), initial_values)

        window.match_rules_toggle.click()
        self.app.processEvents()
        self.assertTrue(window.match_rules_editor.isVisible())
        self.assertEqual(window.match_rules_toggle.text(), "Свернуть")
        window.advanced_settings_dialog.close()

    def test_advanced_settings_are_saved_automatically_and_not_reverted_on_close(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.open_advanced_settings()
        self.app.processEvents()

        new_fuzzy_value = not window.fuzzy_match_check.isChecked()
        with patch.object(main_window, "save_marker_settings") as save_settings:
            window.fuzzy_match_check.setChecked(new_fuzzy_value)
            window._marker_autosave_timer.stop()
            window._autosave_marker_settings()

        save_settings.assert_called_once()
        self.assertEqual(save_settings.call_args.args[0]["use_fuzzy"], new_fuzzy_value)
        window.advanced_settings_dialog.reject()
        self.assertEqual(window.marker_settings["use_fuzzy"], new_fuzzy_value)

        window.output_edit.setText(str(self.folder / "report.xlsx"))
        self.assertTrue(window._window_state_autosave_timer.isActive())

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

    def test_window_size_follows_content_without_disabling_resize(self) -> None:
        first = main_window.MainWindow()
        self.addCleanup(first.deleteLater)
        first.show()
        self.app.processEvents()
        first.resize(700, 600)
        first.move(40, 50)
        first.foreign_agents_resize_handle.set_target_height(61)
        first.excel_resize_handle.set_target_height(67)
        first.match_rules_editor.resize_handle.set_target_height(73)
        first.foreign_agents_upload_toggle.setChecked(True)
        first.excel_upload_toggle.setChecked(True)
        first.match_rules_toggle.setChecked(True)
        first.workflow_tabs.setCurrentWidget(first.parameters_tab)
        first.journal_search_edit.setText("ошибка подключения")
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

        page_hint = second.workflow_tabs.currentWidget().sizeHint()
        self.assertEqual(second.size().width(), max(second.minimumWidth(), page_hint.width() + 2))
        self.assertEqual(second.size().height(), max(second.minimumHeight(), page_hint.height() + 28))
        self.assertEqual(second.database_list.height(), second.source_mode_combo.sizeHint().height())
        self.assertEqual(second.foreign_agents_list.height(), 61)
        self.assertEqual(second.excel_list.height(), 67)
        self.assertEqual(second.match_rules_editor.rules.height(), 73)
        self.assertTrue(second.foreign_agents_upload_toggle.isChecked())
        self.assertTrue(second.foreign_agents_upload.isHidden())
        self.assertTrue(second.excel_upload_toggle.isChecked())
        self.assertTrue(second.excel_upload.isHidden())
        self.assertTrue(second.match_rules_toggle.isChecked())
        self.assertTrue(second.match_rules_editor.isHidden())
        self.assertIs(second.workflow_tabs.currentWidget(), second.parameters_tab)
        self.assertEqual("ошибка подключения", second.journal_search_edit.text())
        self.assertGreater(second.maximumWidth(), second.width())
        self.assertGreater(second.maximumHeight(), second.height())

        for page in (second.parameters_tab, second.results_tab):
            second.workflow_tabs.setCurrentWidget(page)
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(second.scroll_area.verticalScrollBar().maximum(), 0)

    def test_txt_source_is_single_compact_path_and_other_lists_remain_resizable(self) -> None:
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
        window.foreign_agents_resize_handle.set_target_height(48)
        window.excel_resize_handle.set_target_height(54)
        window.match_rules_editor.resize_handle.set_target_height(60)
        self.assertEqual(window.database_list.height(), window.source_mode_combo.sizeHint().height())
        self.assertEqual(window.foreign_agents_list.height(), 48)
        self.assertEqual(window.excel_list.height(), 54)
        self.assertEqual(window.match_rules_editor.rules.height(), 60)
        self.assertFalse(hasattr(window, "download_registries_label"))
        self.assertFalse(hasattr(window, "clear_all_button"))
        self.assertFalse(hasattr(window, "clear_database_button"))
        self.assertFalse(hasattr(window, "database_resize_handle"))
        placeholder = window.database_list.item(0)
        self.assertTrue(placeholder.textAlignment() & Qt.AlignmentFlag.AlignVCenter)
        self.assertTrue(placeholder.textAlignment() & Qt.AlignmentFlag.AlignLeft)
        control_height = window.source_mode_combo.sizeHint().height()
        for button in (
            window.database_button,
            window.source_useful_links_button,
            window.start_button,
            window.open_button,
            window.maintenance_toggle,
        ):
            self.assertEqual(button.sizeHint().height(), control_height)

        initial_size = window.size()
        window._update_excel_summary()
        window._update_foreign_agents_summary()
        self.app.processEvents()
        self.assertEqual(window.foreign_agents_list.height(), 48)
        self.assertEqual(window.excel_list.height(), 54)
        self.assertEqual(window.size(), initial_size)
        self.assertGreater(window.maximumWidth(), window.width())
        self.assertGreater(window.maximumHeight(), window.height())

    def test_txt_source_accepts_one_dropped_txt_file(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        first = self.folder / "first.txt"
        second = self.folder / "second.txt"
        rejected = self.folder / "registry.xlsx"
        first.touch()
        second.touch()
        rejected.touch()
        mime = QMimeData()
        mime.setUrls(
            [
                QUrl.fromLocalFile(str(first)),
                QUrl.fromLocalFile(str(second)),
                QUrl.fromLocalFile(str(rejected)),
            ]
        )

        paths = window.database_list._dropped_paths(mime)

        self.assertEqual(paths, [str(first)])
        window._drop_database_files(paths)
        self.assertEqual(window._database_paths(), [str(first)])

    def test_source_registries_order_and_excel_upload_collapse(self) -> None:
        window = main_window.MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()

        window.resize(1000, 700)
        window._apply_responsive_layout(force=True)
        self.app.processEvents()
        self.assertIs(window.sources_grid.itemAtPosition(0, 0).widget(), window.excel_card)
        self.assertIs(window.sources_grid.itemAtPosition(0, 1).widget(), window.foreign_agents_card)

        window.resize(700, 700)
        window._apply_responsive_layout(force=True)
        self.app.processEvents()
        self.assertIs(window.sources_grid.itemAtPosition(0, 0).widget(), window.excel_card)
        self.assertIs(window.sources_grid.itemAtPosition(1, 0).widget(), window.foreign_agents_card)

        for toggle, upload_widget, list_panel in (
            (window.excel_upload_toggle, window.excel_upload, window.excel_list_panel),
            (
                window.foreign_agents_upload_toggle,
                window.foreign_agents_upload,
                window.foreign_agents_list_panel,
            ),
        ):
            self.assertEqual(toggle.objectName(), "linkButton")
            self.assertTrue(upload_widget.isVisible())
            self.assertIs(list_panel.parentWidget(), upload_widget)
            toggle.click()
            self.app.processEvents()
            self.assertTrue(upload_widget.isHidden())
            self.assertEqual(toggle.text(), "Развернуть")
            toggle.click()
            self.app.processEvents()
            self.assertTrue(upload_widget.isVisible())
            self.assertEqual(toggle.text(), "Свернуть")

        for card, toggle in (
            (window.excel_card, window.excel_upload_toggle),
            (window.foreign_agents_card, window.foreign_agents_upload_toggle),
        ):
            link_gap = toggle.geometry().left() - card.title_label.geometry().right()
            self.assertGreaterEqual(link_gap, 0)
            self.assertLessEqual(link_gap, 12)

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

    def test_connection_dialog_keeps_header_compact(self) -> None:
        dialog = IrbisConnectionDialog({}, [("IBIS — Основной каталог", "IBIS")])
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()

        layout = dialog.layout()
        title_geometry = layout.itemAt(0).geometry()
        hint_geometry = layout.itemAt(1).geometry()
        card_geometry = layout.itemAt(2).geometry()
        self.assertLessEqual(hint_geometry.top() - title_geometry.bottom(), 12)
        self.assertLessEqual(card_geometry.top() - hint_geometry.bottom(), 12)

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
                    "approved": [{"key": "approved", "confirmed_at": "2026-09-18T10:00:00+00:00", **common}],
                    "rejected": [{"key": "rejected", "rejected_at": "2026-09-18T11:00:00+00:00", **common}],
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
        self.assertEqual(6, dialog.table.columnCount())
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertTrue(dialog.import_button.isEnabled())
        self.assertNotIn(
            "Действие",
            [dialog.table.horizontalHeaderItem(column).text() for column in range(6)],
        )

    def test_review_memory_right_click_menu_opens_without_nested_event_loop(self) -> None:
        memory_path = self.folder / "review_memory.json"
        memory_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "approved": [
                        {
                            "key": "approved",
                            "database_value": "Фаулз Джон",
                            "registry_value": "Джон Фаулз",
                            "registry_number": "1",
                            "method": "Автор",
                            "confirmed_at": "2026-09-18T10:00:00+00:00",
                        }
                    ],
                    "rejected": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        dialog = dialogs.ConfirmationMemoryDialog(memory_path)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()

        position = dialog.table.visualItemRect(dialog.table.item(0, 0)).center()
        dialog.table.customContextMenuRequested.emit(position)
        self.app.processEvents()

        menu = dialog.table._copy_menu
        self.assertIsNotNone(menu)
        self.assertTrue(menu.isVisible())
        self.assertEqual(
            [action.text() for action in menu.actions() if not action.isSeparator()],
            ["Копировать", "Удалить выбранные"],
        )
        self.assertEqual({index.row() for index in dialog.table.selectedIndexes()}, {0})
        delete_action = next(action for action in menu.actions() if action.text() == "Удалить выбранные")
        delete_action.trigger()
        self.app.processEvents()
        self.assertIsNone(dialog.table._copy_menu)
        self.assertEqual(0, dialog.table.rowCount())
        saved_memory = json.loads(memory_path.read_text(encoding="utf-8"))
        self.assertEqual([], saved_memory["approved"])
        self.assertEqual([], saved_memory["rejected"])

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

    def test_manual_review_cards_filter_search_and_change_decisions(self) -> None:
        entry = ExcelEntry(1, "substances.xlsx", "Книги", 2, author="Прилепин", title="Обитель")
        rows = [
            (
                i,
                MatchResult(
                    "Возможное совпадение",
                    "Название",
                    90,
                    entry,
                    DatabaseRecord(mfn, titles=["Обитель"], authors=["Прилепин"]),
                    source_type=SOURCE_SUBSTANCES,
                    matched_value="Обитель",
                ),
            )
            for i, mfn in enumerate((3604, 5283))
        ]
        dialog = manual_review.ManualMatchReviewDialog(rows)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        self.assertIsInstance(dialog.sort_combo, widgets.AppComboBox)
        self.assertTrue(dialog.sort_combo.property("showCurrentInPopup"))
        dialog.sort_combo.showPopup()
        self.assertIsNotNone(dialog.sort_combo._app_popup)
        self.assertTrue(dialog.sort_combo._app_popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        dialog.sort_combo.hidePopup()
        self.assertEqual(2, dialog.group_list.count())
        self.assertTrue(dialog._apply_group_button.isHidden())
        self.assertTrue(dialog._reject_group_button.isHidden())
        self.assertIs(dialog.scroll.widget(), dialog._apply_group_button.parentWidget())
        self.assertEqual("Подтвердить всю группу (1)", dialog._apply_group_button.text())
        self.assertEqual("Убрать всю группу (1)", dialog._reject_group_button.text())
        self.assertFalse(dialog.continue_button.isEnabled())
        dialog.accept()
        self.assertEqual({}, dialog.decisions())
        dialog.search.setText("5283")
        self.assertEqual(1, dialog.group_list.count())
        self.assertIn(1, dialog._cards)
        dialog._action_buttons[1][0].click()
        self.assertEqual({0: None, 1: True}, dialog._decisions)
        dialog.search.clear()
        dialog._set_filter("approved")
        self.assertEqual(1, dialog.group_list.count())
        dialog._set_filter("pending")
        self.assertEqual(1, dialog.group_list.count())
        dialog._set_decision(0, True)
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        self.assertEqual(0, dialog.group_list.count())
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog._set_filter("all")
        dialog._set_decision(0, False)
        dialog._set_filter("rejected")
        self.assertEqual(1, dialog.group_list.count())
        dialog._action_buttons[0][0].click()
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        dialog.accept()
        self.assertEqual({0: True, 1: True}, dialog.decisions())

    def test_manual_review_shows_compact_clean_irbis_record(self) -> None:
        raw_record = "#010: ^A978-5-17-000000-0\n#200: ^AОбитель\n#210: ^CАСТ^D2024"
        result = MatchResult(
            "Возможное совпадение",
            "Название",
            90,
            ExcelEntry(1, "books.xlsx", "Книги", 2, title="Обитель"),
            DatabaseRecord(
                4467,
                source_record_number=4467,
                titles=["^AОбитель"],
                authors=["^AПрилепин^BЗахар"],
                organizations=["^AИРБИС"],
                isbns=["^A978-5-17-000000-0"],
                inventory_numbers=["^AИНВ-004467"],
                raw_record=raw_record,
            ),
            source_type=SOURCE_SUBSTANCES,
        )
        dialog = manual_review.ManualMatchReviewDialog([(0, result)])
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        captions = [label.text() for label in dialog.findChildren(QLabel, "reviewIrbisCaption")]
        values = [label.text() for label in dialog.findChildren(QLabel, "reviewIrbisValue")]
        self.assertEqual(["Название", "Источник", "Авторы", "ISBN", "Инвентарный номер книги"], captions)
        self.assertEqual(["Обитель", SOURCE_SUBSTANCES, "Прилепин Захар", "978-5-17-000000-0", "ИНВ-004467"], values)
        self.assertNotIn("^", " ".join(values))
        self.assertNotIn("#010", " ".join(label.text() for label in dialog.findChildren(QLabel)))
        self.assertFalse(any(button.text() == "Копировать" for button in dialog.findChildren(QPushButton)))

    def test_manual_review_candidates_remain_available_after_choice(self) -> None:
        record = DatabaseRecord(4156, titles=["Эпоха мёртвых"], authors=["Круз"])
        rows = [
            (
                i,
                MatchResult(
                    "Возможное совпадение",
                    "Название",
                    90,
                    ExcelEntry(i, "books.xlsx", "Книги", i + 2, title=title),
                    record,
                    matched_value=title,
                ),
            )
            for i, title in enumerate(("Начало", "Москва"))
        ]
        dialog = manual_review.ManualMatchReviewDialog(rows)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        self.assertEqual(1, len(dialog._record_groups))
        self.assertEqual(2, len(dialog._cards))
        self.assertFalse(dialog._apply_group_button.isHidden())
        self.assertFalse(dialog._reject_group_button.isHidden())
        self.assertEqual("Подтвердить всю группу (2)", dialog._apply_group_button.text())
        self.assertEqual("Убрать всю группу (2)", dialog._reject_group_button.text())
        dialog._reject_group_button.click()
        self.assertEqual({0: False, 1: False}, dialog._decisions)
        dialog._apply_group_button.click()
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        from PyQt6.QtTest import QTest

        QTest.keyClick(dialog.group_list, Qt.Key.Key_Down)
        self.assertEqual(1, dialog._active_index)
        QTest.keyClick(dialog.group_list, Qt.Key.Key_Return)
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        dialog._set_decision(1, True)
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        self.assertEqual(2, len(dialog._cards))
        dialog._set_decision(0, True)
        self.assertEqual({0: True, 1: True}, dialog._decisions)
        dialog._set_filter("approved")
        self.assertEqual(1, dialog.group_list.count())
        dialog._toggle_details()
        self.assertTrue(dialog._collapsed)
        self.assertEqual(2, len(dialog._cards))
        with patch.object(dialogs, "_open_excel_at_source") as open_source:
            dialog._open_source(1)
        open_source.assert_called_once_with("books.xlsx", "Книги", 3, dialog)

    def test_manual_review_details_expand_down_without_scroll_jump(self) -> None:
        from PyQt6.QtTest import QTest

        record = DatabaseRecord(4156, titles=["Книга"])
        rows = [
            (
                i,
                MatchResult(
                    "Возможное совпадение",
                    "Название",
                    90,
                    ExcelEntry(i, "books.xlsx", "Книги", i + 2, title="Вариант"),
                    record,
                    note="Подробности проверки\nВторая строка\nТретья строка",
                ),
            )
            for i in range(8)
        ]
        dialog = manual_review.ManualMatchReviewDialog(rows)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.resize(1100, 720)
        dialog.show()
        for _ in range(3):
            self.app.processEvents()
        page = dialog.scroll.widget()
        card = dialog._cards[2]
        button = dialog._detail_buttons[2]
        number = card.findChild(QPushButton, "reviewNumber")
        bar = dialog.scroll.verticalScrollBar()
        bar.setValue(card.y() - 20)
        self.app.processEvents()
        scroll_position = bar.value()
        card_top = card.y()
        button_top = button.mapTo(page, button.rect().topLeft()).y()
        following_top = dialog._cards[3].y()
        for _ in range(2):
            button.click()
            self.assertIn(2, dialog._detail_animations)
            self.assertEqual(QAbstractAnimation.State.Running, dialog._detail_animations[2].state())
            QTest.qWait(220)
            for _ in range(3):
                self.app.processEvents()
            self.assertIs(page, dialog.scroll.widget())
            self.assertIs(card, dialog._cards[2])
            self.assertEqual(scroll_position, bar.value())
            self.assertEqual(card_top, card.y())
            self.assertEqual(button_top, button.mapTo(page, button.rect().topLeft()).y())
            number_center = number.mapTo(card, number.rect().topLeft()).y() + number.height() // 2
            self.assertLessEqual(abs(number_center - card.height() // 2), 2)
            self.assertGreater(dialog._cards[3].y(), following_top)
            button.click()
            self.assertIn(2, dialog._detail_animations)
            QTest.qWait(220)
            for _ in range(3):
                self.app.processEvents()
            self.assertEqual(scroll_position, bar.value())
            self.assertEqual(following_top, dialog._cards[3].y())

    def test_manual_review_single_candidate_keeps_content_height(self) -> None:
        result = MatchResult(
            "Возможное совпадение",
            "Название",
            90,
            ExcelEntry(1, "books.xlsx", "Книги", 2, title="Книга"),
            DatabaseRecord(4156, titles=["Книга"]),
            matched_value="Книга",
        )
        dialog = manual_review.ManualMatchReviewDialog([(0, result)])
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.findChild(QLabel, "reviewTitle").wordWrap())
        dialog.resize(1100, 720)
        dialog.show()
        for _ in range(3):
            self.app.processEvents()

        card = dialog._cards[0]
        self.assertLess(card.height(), dialog.scroll.viewport().height() // 2)
        self.assertLessEqual(card.height(), card.sizeHint().height() + 2)
        approve, reject = dialog._action_buttons[0]
        self.assertEqual(approve.width(), reject.width())

    def test_manual_review_bulk_reject_preserves_approved_choice(self) -> None:
        rows = [
            (
                i,
                MatchResult(
                    "Возможное совпадение",
                    "Название",
                    90,
                    ExcelEntry(i, "books.xlsx", "Книги", i + 2, title=str(i)),
                    DatabaseRecord(i + 10, titles=[str(i)]),
                ),
            )
            for i in range(3)
        ]
        dialog = manual_review.ManualMatchReviewDialog(rows)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog._set_decision(0, True)
        dialog._reject_unresolved()
        self.assertEqual({0: True, 1: False, 2: False}, dialog._decisions)
        self.assertEqual(100, dialog.progress_bar.value())
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog.reject()
        self.assertEqual({}, dialog.decisions())

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
