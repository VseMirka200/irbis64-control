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

        self.assertEqual("system", settings.theme)
        self.assertTrue(settings.create_database_backup)
        self.assertTrue(settings.check_updates_on_start)

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            expected = ApplicationSettings(
                theme="dark",
                create_database_backup=False,
                check_updates_on_start=False,
            )
            save_application_settings(path, expected)

            self.assertEqual(expected, load_application_settings(path))

    def test_unknown_theme_falls_back_to_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text('{"theme": "neon"}', encoding="utf-8")

            settings = load_application_settings(path)

        self.assertEqual("system", settings.theme)


if __name__ == "__main__":
    unittest.main()
