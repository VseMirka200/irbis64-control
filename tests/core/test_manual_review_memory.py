from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from irbis_control.core.manual_review_memory import (
    apply_remembered_confirmations,
    apply_remembered_decisions,
    load_approved_review_keys,
    load_review_decision_rows,
    remember_approved_results,
    remember_review_decisions,
    review_identity,
)
from irbis_control.core.matcher import SOURCE_FOREIGN_AGENTS
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


def test_saved_confirmation_auto_confirms_same_mapping_on_later_records(tmp_path: Path) -> None:
    memory_path = tmp_path / "manual_review_confirmations.json"
    first_run = [_result(394, "Шпионский роман")]

    assert remember_approved_results(memory_path, first_run, {0}) == 1
    assert len(load_approved_review_keys(memory_path)) == 1

    later_results = [
        _result(395, "Нефритовые четки"),
        _result(396, "Смерть на брудершафт"),
    ]
    summary = _summary(2)

    applied = apply_remembered_confirmations(later_results, summary, memory_path)

    assert applied == 2
    assert all(result.status == "Совпадение" for result in later_results)
    assert all(result.confidence == 100.0 for result in later_results)
    assert all("сохранённому решению" in result.note for result in later_results)
    assert summary.review_rows == 0


def test_memory_rows_can_be_listed_and_removed(tmp_path: Path) -> None:
    from irbis_control.core.manual_review_memory import (
        clear_approved_review_memory,
        load_approved_review_rows,
        remove_approved_review_keys,
    )

    memory_path = tmp_path / "manual_review_confirmations.json"
    results = [
        _result(394, "Шпионский роман", registry_number="276"),
        _result(395, "Другая книга", registry_number="999"),
    ]
    assert remember_approved_results(memory_path, results, {0, 1}) == 2

    rows = load_approved_review_rows(memory_path)
    assert len(rows) == 2
    first_key = rows[0]["key"]
    assert remove_approved_review_keys(memory_path, {first_key}) == 1
    rows = load_approved_review_rows(memory_path)
    assert len(rows) == 1
    assert rows[0]["key"] != first_key

    assert clear_approved_review_memory(memory_path) == 1
    assert load_approved_review_rows(memory_path) == []
    assert load_approved_review_keys(memory_path) == set()


class ReviewDecisionMemoryTests(unittest.TestCase):
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
