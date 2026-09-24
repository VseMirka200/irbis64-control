from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from irbis_control.core.manual_review_memory import (
    apply_remembered_decisions,
    export_review_decision_memory,
    import_review_decision_memory,
    load_review_decision_rows,
    remember_review_decisions,
    review_identity,
)
from irbis_control.core.matcher import SOURCE_FOREIGN_AGENTS, SOURCE_SUBSTANCES
from irbis_control.core.models import (
    ComparisonSummary,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MatchResult,
)


def _summary(review_rows: int) -> ComparisonSummary:
    return ComparisonSummary(
        database_file="db",
        excel_files=[],
        database_records=review_rows,
        database_records_with_isbn=0,
        excel_rows=0,
        matched_excel_rows=0,
        unmatched_excel_rows=0,
        result_rows=review_rows,
        exact_isbn_rows=0,
        exact_title_rows=0,
        probable_rows=0,
        foreign_agent_rows=1,
        foreign_agent_result_rows=review_rows,
        review_rows=review_rows,
    )


def _result(mfn: int, title: str, *, registry_number: str = "276") -> MatchResult:
    author = "Акунин Б. Борис"
    record = DatabaseRecord(
        record_number=mfn,
        source_record_number=mfn,
        titles=[title],
        authors=[author],
    )
    foreign = ForeignAgentEntry(
        entry_id=1 if registry_number == "276" else 2,
        source_file="export.xlsx",
        sheet_name="Лист1",
        row_number=20,
        registry_number=registry_number,
        name="Чхартишвили Григорий Шалвович",
        participants=["Акунин Борис"],
        agent_type="Физическое лицо",
    )
    excel = ExcelEntry(
        entry_id=foreign.entry_id,
        source_file=foreign.source_file,
        sheet_name=foreign.sheet_name,
        row_number=foreign.row_number,
        author=foreign.name,
        title="Акунин Борис",
        registration_number=registry_number,
    )
    return MatchResult(
        status="Возможное совпадение",
        method="Реестр иностранных агентов: Автор",
        confidence=80.0,
        excel=excel,
        database=record,
        note="Фамилия + один инициал",
        source_type=SOURCE_FOREIGN_AGENTS,
        matched_value="Акунин Борис",
        foreign_agent=foreign,
        database_matched_value=author,
    )


def test_review_identity_groups_same_author_and_same_registry_candidate() -> None:
    first = review_identity(_result(394, "Шпионский роман"))
    second = review_identity(_result(395, "Нефритовые четки"))
    other_candidate = review_identity(_result(396, "Другая книга", registry_number="999"))

    assert first is not None
    assert second is not None
    assert other_candidate is not None
    assert first.key == second.key
    assert first.key != other_candidate.key


def test_review_identity_groups_repeated_initial_and_full_name() -> None:
    repeated_initial = _result(394, "Шпионский роман")
    repeated_initial.database_matched_value = "Фаулз Д. Джон"
    repeated_initial.matched_value = "Фаулз, Джон"
    plain_name = _result(395, "Нефритовые четки")
    plain_name.database_matched_value = "Фаулз, Джон"
    plain_name.matched_value = "Фаулз Джон"

    first = review_identity(repeated_initial)
    second = review_identity(plain_name)

    assert first is not None
    assert second is not None
    assert first.key == second.key


class ReviewDecisionMemoryTests(unittest.TestCase):
    @staticmethod
    def _book(title="От первого лица", author="Автор", isbn="123"):
        return MatchResult(
            status="Возможное совпадение",
            method="Приблизительно по названию",
            confidence=90,
            excel=ExcelEntry(1, "registry.xlsx", "Книги", 2, title=title, author=author, isbn=isbn),
            database=DatabaseRecord(4467, titles=["От первого лица: сборник"], authors=["Автор"]),
            source_type=SOURCE_SUBSTANCES,
            matched_value=title,
        )

    def test_book_decisions_survive_new_file_row_and_mfn(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.json"
            first = [self._book(), self._book(title="Другой кандидат")]
            self.assertEqual((1, 1), remember_review_decisions(path, first, {0: True, 1: False}))
            later = [self._book(), self._book(title="Другой кандидат")]
            for result in later:
                result.excel.source_file = "new-folder/new-registry.xlsx"
                result.excel.row_number = 999
                result.excel.entry_id = 999
                result.database.record_number = 5000
                result.database.source_file = "new-database"
            self.assertEqual((1, 1), apply_remembered_decisions(later, _summary(2), path))
            self.assertEqual("Совпадение", later[0].status)
            self.assertEqual("Отклонено вручную", later[1].status)
            self.assertIn("сохранённому решению", later[0].note)

    def test_book_memory_does_not_confirm_other_authors_or_editions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.json"
            remember_review_decisions(path, [self._book()], {0: True})
            later = [self._book(author="Другой автор"), self._book(isbn="456"), self._book()]
            later[2].database.titles = ["Другое название"]
            self.assertEqual((0, 0), apply_remembered_decisions(later, _summary(3), path))
            self.assertTrue(all(row.status == "Возможное совпадение" for row in later))
            remember_review_decisions(path, [self._book()], {0: False})
            self.assertEqual((0, 1), apply_remembered_decisions([self._book()], _summary(1), path))

    def test_approved_and_rejected_decisions_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            memory_path = Path(folder) / "manual_review_confirmations.json"
            first_run = [
                _result(394, "Шпионский роман", registry_number="276"),
                _result(395, "Другая книга", registry_number="999"),
            ]

            self.assertEqual((1, 1), remember_review_decisions(memory_path, first_run, {0: True, 1: False}))
            self.assertEqual(
                {"Подтверждено", "Отклонено"},
                {row["decision"] for row in load_review_decision_rows(memory_path)},
            )

            later_results = [
                _result(401, "Новая книга", registry_number="276"),
                _result(402, "Ещё одна книга", registry_number="999"),
            ]
            summary = _summary(2)

            self.assertEqual((1, 1), apply_remembered_decisions(later_results, summary, memory_path))
            self.assertEqual("Совпадение", later_results[0].status)
            self.assertEqual("Отклонено вручную", later_results[1].status)
            self.assertIn("сохранённому решению", later_results[1].note)
            self.assertEqual(0, summary.review_rows)

    def test_new_decision_replaces_opposite_saved_decision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            memory_path = Path(folder) / "manual_review_confirmations.json"
            result = _result(394, "Шпионский роман")

            remember_review_decisions(memory_path, [result], {0: False})
            remember_review_decisions(memory_path, [result], {0: True})

            rows = load_review_decision_rows(memory_path)
            self.assertEqual(1, len(rows))
            self.assertEqual("Подтверждено", rows[0]["decision"])

    def test_memory_can_be_exported_and_imported_with_conflict_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.json"
            exported = root / "exported.json"
            destination = root / "destination.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "approved": [{"key": "replace", "database_value": "Новое решение"}],
                        "rejected": [{"key": "added", "database_value": "Добавленное решение"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            destination.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "approved": [{"key": "keep", "database_value": "Сохранить"}],
                        "rejected": [{"key": "replace", "database_value": "Старое решение"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertEqual(2, export_review_decision_memory(source, exported))
            self.assertEqual((1, 1), import_review_decision_memory(exported, destination))

            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual({"keep", "replace"}, {row["key"] for row in payload["approved"]})
            self.assertEqual({"added"}, {row["key"] for row in payload["rejected"]})

    def test_invalid_import_does_not_change_local_memory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "memory.json"
            source = root / "invalid.json"
            original = '{"schema_version": 2, "approved": [], "rejected": []}'
            destination.write_text(original, encoding="utf-8")
            source.write_text('{"schema_version": 99, "approved": [], "rejected": []}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "не поддерживается"):
                import_review_decision_memory(source, destination)
            self.assertEqual(original, destination.read_text(encoding="utf-8"))
