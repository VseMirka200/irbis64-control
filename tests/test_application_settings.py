import tempfile
import unittest
from pathlib import Path

from irbis_control.application.settings import (
    ApplicationSettings,
    load_application_settings,
    save_application_settings,
)


class ApplicationSettingsTests(unittest.TestCase):
    def test_defaults_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_application_settings(Path(temp_dir) / "missing.json")

        self.assertTrue(settings.create_database_backup)
        self.assertTrue(settings.check_updates_on_start)

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            expected = ApplicationSettings(
                create_database_backup=False,
                check_updates_on_start=False,
            )
            save_application_settings(path, expected)

            self.assertEqual(expected, load_application_settings(path))

    def test_legacy_theme_setting_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                '{"theme": "dark", "create_database_backup": false}',
                encoding="utf-8",
            )

            settings = load_application_settings(path)

        self.assertFalse(settings.create_database_backup)
        self.assertFalse(hasattr(settings, "theme"))

    def test_saved_settings_do_not_contain_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_application_settings(path, ApplicationSettings())

            payload = path.read_text(encoding="utf-8")

        self.assertNotIn('"theme"', payload)


if __name__ == "__main__":
    unittest.main()
