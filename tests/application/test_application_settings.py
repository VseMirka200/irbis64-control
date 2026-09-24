import tempfile
import unittest
from pathlib import Path

from irbis_control.application.settings import (
    THEME_DARK,
    THEME_SYSTEM,
    ApplicationSettings,
    load_application_settings,
    save_application_settings,
)


class ApplicationSettingsTests(unittest.TestCase):
    def test_invalid_payload_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            for payload in ("[]", "null", "42", '"text"', "{broken", '{"create_database_backup": 0}'):
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    self.assertEqual(load_application_settings(path), ApplicationSettings())

    def test_defaults_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_application_settings(Path(temp_dir) / "missing.json")

        self.assertTrue(settings.create_database_backup)
        self.assertTrue(settings.check_updates_on_start)
        self.assertTrue(settings.use_nkp_drug_registry)
        self.assertTrue(settings.use_nkp_foreign_agents_registry)
        self.assertEqual(settings.theme, THEME_SYSTEM)

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            expected = ApplicationSettings(
                create_database_backup=False,
                check_updates_on_start=False,
                theme=THEME_DARK,
                use_nkp_drug_registry=False,
                use_nkp_foreign_agents_registry=False,
            )
            save_application_settings(path, expected)

            self.assertEqual(expected, load_application_settings(path))

    def test_theme_setting_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                '{"theme": "dark", "create_database_backup": false}',
                encoding="utf-8",
            )

            settings = load_application_settings(path)

        self.assertFalse(settings.create_database_backup)
        self.assertEqual(settings.theme, THEME_DARK)

    def test_saved_settings_contain_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_application_settings(path, ApplicationSettings())

            payload = path.read_text(encoding="utf-8")

        self.assertIn('"theme": "system"', payload)

    def test_invalid_theme_uses_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text('{"theme": "blue"}', encoding="utf-8")

            settings = load_application_settings(path)

        self.assertEqual(settings.theme, THEME_SYSTEM)

    def test_invalid_nkp_registry_flags_use_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                '{"use_nkp_drug_registry": 1, "use_nkp_foreign_agents_registry": "yes"}',
                encoding="utf-8",
            )

            settings = load_application_settings(path)

        self.assertTrue(settings.use_nkp_drug_registry)
        self.assertTrue(settings.use_nkp_foreign_agents_registry)

    def test_saved_settings_contain_nkp_registry_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_application_settings(
                path,
                ApplicationSettings(
                    use_nkp_drug_registry=False,
                    use_nkp_foreign_agents_registry=True,
                ),
            )

            payload = path.read_text(encoding="utf-8")

        self.assertIn('"use_nkp_drug_registry": false', payload)
        self.assertIn('"use_nkp_foreign_agents_registry": true', payload)
        self.assertIn('"schema_version": 3', payload)


if __name__ == "__main__":
    unittest.main()
