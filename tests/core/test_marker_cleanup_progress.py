import tempfile
import unittest
from pathlib import Path

from irbis_control.core.marker_updates import remove_database_markers


class MarkerCleanupProgressTests(unittest.TestCase):
    def test_reports_progress_while_removing_markers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.txt"
            output = Path(folder) / "cleaned.txt"
            source.write_text(
                "#010: ^A1\n#333: ^AIII\n*****\n"
                "#010: ^A2\n#200: ^AОбычная запись\n*****\n",
                encoding="utf-8",
            )
            updates: list[tuple[int, str]] = []

            written, cleaned_records = remove_database_markers(
                source,
                output,
                progress_cb=lambda percent, text: updates.append((percent, text)),
            )

            self.assertEqual(output, written)
            self.assertEqual(1, cleaned_records)
            self.assertEqual(5, updates[0][0])
            self.assertEqual(95, updates[-1][0])
            self.assertEqual(sorted(percent for percent, _text in updates), [percent for percent, _text in updates])
            self.assertIn("2 из 2", updates[-1][1])
            self.assertNotIn("#333: ^AIII", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
