import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from irbis_control.core.matcher import (
    SOURCE_FOREIGN_AGENTS,
    SOURCE_SUBSTANCES,
    ComparisonSummary,
    DatabaseIndex,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MatchResult,
    build_markers_by_record,
    compare_foreign_agents,
    export_results,
)


class BoundaryMatchTests(unittest.TestCase):
    @staticmethod
    def _foreign_entry(entry_id: int, name: str) -> ForeignAgentEntry:
        return ForeignAgentEntry(
            entry_id=entry_id,
            source_file="foreign.xlsx",
            sheet_name="Реестр",
            row_number=entry_id + 1,
            registry_number=str(entry_id),
            name=name,
            agent_type="Физическое лицо",
        )

    def test_single_initial_with_multiple_people_requires_review(self) -> None:
        record = DatabaseRecord(record_number=10, authors=["Иванов И."])
        entries = [
            self._foreign_entry(1, "Иванов Иван Петрович"),
            self._foreign_entry(2, "Иванов Илья Сергеевич"),
        ]

        results = compare_foreign_agents([record], entries)

        self.assertEqual(2, len(results))
        self.assertTrue(all(result.status == "Возможное совпадение" for result in results))
        self.assertTrue(all(result.confidence == 90.0 for result in results))
        self.assertEqual({}, build_markers_by_record(results))

    def test_single_initial_is_not_automatic_even_for_one_candidate(self) -> None:
        record = DatabaseRecord(record_number=11, authors=["Иванов И."])

        results = compare_foreign_agents(
            [record], [self._foreign_entry(1, "Иванов Иван Петрович")]
        )

        self.assertEqual("Возможное совпадение", results[0].status)
        self.assertEqual({}, build_markers_by_record(results))

    def test_exact_title_without_author_requires_review(self) -> None:
        record = DatabaseRecord(
            record_number=20,
            titles=["Принцип сперматозоида"],
            authors=["Петров П.П."],
        )
        entry = ExcelEntry(
            entry_id=1,
            source_file="substances.xlsx",
            sheet_name="Книги",
            row_number=2,
            title="Принцип сперматозоида",
        )

        results = DatabaseIndex([record]).match(entry, True, True, False, 90)

        self.assertEqual("Возможное совпадение", results[0].status)
        self.assertEqual("Только название", results[0].method)
        self.assertEqual({}, build_markers_by_record(results))

    def test_exact_title_and_author_remain_confirmed(self) -> None:
        record = DatabaseRecord(
            record_number=21,
            titles=["Принцип сперматозоида"],
            authors=["Петров П.П."],
        )
        entry = ExcelEntry(
            entry_id=1,
            source_file="substances.xlsx",
            sheet_name="Книги",
            row_number=2,
            author="Петров П.П.",
            title="Принцип сперматозоида",
        )

        results = DatabaseIndex([record]).match(entry, True, True, False, 90)

        self.assertEqual("Совпадение", results[0].status)
        self.assertEqual(100.0, results[0].confidence)
        self.assertIn(21, build_markers_by_record(results))

    def test_confidence_below_100_is_never_eligible_for_marker(self) -> None:
        result = MatchResult(
            status="Совпадение",
            method="Название и автор",
            confidence=99.0,
            excel=ExcelEntry(1, "source.xlsx", "Лист1", 2),
            database=DatabaseRecord(record_number=30),
            source_type=SOURCE_SUBSTANCES,
        )

        self.assertEqual({}, build_markers_by_record([result]))

    def test_excel_report_contains_review_sheet_with_reason_and_source(self) -> None:
        result = MatchResult(
            status="Возможное совпадение",
            method="Только название",
            confidence=75.0,
            excel=ExcelEntry(1, "source.xlsx", "Книги", 12, title="Книга"),
            database=DatabaseRecord(record_number=40, titles=["Книга"]),
            note="Точно совпало только название",
            source_type=SOURCE_SUBSTANCES,
            matched_value="Книга",
        )
        summary = ComparisonSummary(
            database_file="Тест",
            excel_files=["source.xlsx"],
            database_records=1,
            database_records_with_isbn=0,
            excel_rows=1,
            matched_excel_rows=0,
            unmatched_excel_rows=1,
            result_rows=1,
            exact_isbn_rows=0,
            exact_title_rows=0,
            probable_rows=1,
            review_rows=1,
        )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output = Path(temp_dir) / "report.xlsx"
            export_results(
                output,
                [result],
                summary,
                report_options={"substances": True},
            )
            workbook = load_workbook(output, read_only=True)
            try:
                self.assertIn("Требует проверки", workbook.sheetnames)
                sheet = workbook["Требует проверки"]
                self.assertEqual("Точно совпало только название", sheet["D2"].value)
                self.assertEqual("source.xlsx", sheet["I2"].value)
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
