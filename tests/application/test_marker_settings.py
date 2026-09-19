import tempfile
import unittest
from pathlib import Path

from irbis_control.application.marker_settings import (
    DEFAULT_MARKER_SETTINGS,
    load_marker_settings,
    save_marker_settings,
)


class MarkerSettingsTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marker_settings.json"
            expected = dict(DEFAULT_MARKER_SETTINGS)
            expected["age_marker_field"] = 951
            expected["report_summary"] = True
            save_marker_settings(path, expected)

            self.assertEqual(load_marker_settings(path), expected)

    def test_invalid_structure_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marker_settings.json"
            path.write_text("[]", encoding="utf-8")

            self.assertEqual(load_marker_settings(path), DEFAULT_MARKER_SETTINGS)


if __name__ == "__main__":
    unittest.main()
