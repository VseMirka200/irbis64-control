from __future__ import annotations

from irbis_control.core.matcher import (
    SOURCE_FOREIGN_AGENTS,
    apply_manual_review_decisions,
    build_markers_by_record,
)
from irbis_control.core.models import (
    ComparisonSummary,
    DatabaseRecord,
    ExcelEntry,
    ForeignAgentEntry,
    MatchResult,
)


def _summary() -> ComparisonSummary:
    return ComparisonSummary(
        database_file="db",
        excel_files=[],
        database_records=1,
        database_records_with_isbn=0,
        excel_rows=0,
        matched_excel_rows=0,
        unmatched_excel_rows=0,
        result_rows=2,
        exact_isbn_rows=0,
        exact_title_rows=0,
        probable_rows=0,
        foreign_agent_rows=1,
        foreign_agent_result_rows=2,
        review_rows=2,
    )


def _result() -> MatchResult:
    record = DatabaseRecord(record_number=10, source_record_number=10, authors=["Иванов И."])
    foreign = ForeignAgentEntry(
        entry_id=1,
        source_file="export.xlsx",
        sheet_name="Лист1",
        row_number=2,
        registry_number="1",
        name="Иванов Иван Петрович",
        agent_type="Физическое лицо",
    )
    excel = ExcelEntry(
        entry_id=1,
        source_file="export.xlsx",
        sheet_name="Лист1",
        row_number=2,
        author=foreign.name,
        title=foreign.name,
    )
    return MatchResult(
        status="Возможное совпадение",
        method="Реестр иностранных агентов: Автор",
        confidence=80.0,
        excel=excel,
        database=record,
        note="Фамилия + один инициал",
        source_type=SOURCE_FOREIGN_AGENTS,
        matched_value=foreign.name,
        foreign_agent=foreign,
    )


def test_manual_confirmation_allows_marker_and_rejection_blocks_it() -> None:
    approved = _result()
    rejected = _result()
    results = [approved, rejected]
    summary = _summary()

    apply_manual_review_decisions(results, summary, {0: True, 1: False})

    assert approved.status == "Совпадение"
    assert approved.confidence == 100.0
    assert "Подтверждено оператором вручную" in approved.note
    assert rejected.status == "Отклонено вручную"
    assert "Отклонено оператором вручную" in rejected.note
    assert summary.review_rows == 0
    assert summary.matched_foreign_agent_rows == 1
    assert build_markers_by_record(results) == {10: [(333, "^AI^@ИВАНОВ ИВАН ПЕТРОВИЧ")]}
