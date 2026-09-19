import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

from irbis_control.core.matcher import compare_and_export
from irbis_control.reporting.result_diff import _iter_xlsx_rows, compare_result_files, compare_text_files


class ReportingTests(unittest.TestCase):
    def test_xlsx_is_closed_when_reading_fails(self) -> None:
        workbook = Mock()
        workbook.sheetnames = ["Вещества"]
        workbook.__getitem__ = Mock(side_effect=ValueError("broken sheet"))
        with patch("irbis_control.reporting.result_diff._load_workbook_quiet", return_value=workbook):
            with self.assertRaisesRegex(ValueError, "broken sheet"):
                _iter_xlsx_rows(Path("report.xlsx"))
        workbook.close.assert_called_once()

    def test_local_comparison_exports_reports_and_modified_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            database = folder / "database.txt"
            original = "#010: ^A9780306406157\n#200: ^AТестовая книга\n#700: ^AИванов^BИ.И.\n*****\n"
            database.write_text(original, encoding="utf-8")
            source = folder / "books.xlsx"
            workbook = Workbook()
            workbook.active.append(["ISBN", "Название", "Автор"])
            workbook.active.append(["9780306406157", "Тестовая книга", "Иванов И.И."])
            workbook.save(source)
            workbook.close()

            reports = []
            for number in range(2):
                report = folder / f"report{number}.xlsx"
                modified = folder / f"modified{number}.txt"
                results, summary = compare_and_export(database, [source], report, modified)
                self.assertEqual(summary.modified_database_records, 1)
                self.assertTrue(any(result.status == "Совпадение" for result in results))
                self.assertTrue(modified.is_file())
                book = load_workbook(report, read_only=True)
                try:
                    self.assertIn("Вещества", book.sheetnames)
                    self.assertGreater(book["Вещества"].max_row, 1)
                finally:
                    book.close()
                reports.append(report)

            differences, summary = compare_result_files(*reports, folder / "changes.xlsx")
            self.assertEqual(differences, [])
            self.assertEqual(summary.total_changes, 0)
            differences, summary = compare_text_files(database, modified, folder / "changes.txt")
            self.assertEqual(summary.changed, 1)
            self.assertEqual(len(differences), 1)
            self.assertEqual(database.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
