from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatabaseRecord:
    record_number: int
    source_file: str = ""
    source_record_number: int = 0
    isbns: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    primary_authors: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)
    inventory_numbers: list[str] = field(default_factory=list)
    publication: list[str] = field(default_factory=list)
    raw_record: str = ""

    @property
    def main_title(self) -> str:
        return self.titles[0] if self.titles else ""


@dataclass
class ExcelEntry:
    entry_id: int
    source_file: str
    sheet_name: str
    row_number: int
    author: str = ""
    title: str = ""
    isbn: str = ""
    registration_number: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    publisher: str = ""
    year: str = ""


@dataclass
class ForeignAgentEntry:
    entry_id: int
    source_file: str
    sheet_name: str
    row_number: int
    registry_number: str = ""
    name: str = ""
    participants: list[str] = field(default_factory=list)
    agent_type: str = ""
    inclusion_date: str = ""
    exclusion_date: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    # Строка книжной выгрузки описывает конкретное издание, поэтому ISBN и заглавие
    # нельзя терять при группировке записей по агенту.
    title: str = ""
    isbn: str = ""
    role: str = ""
    publication: str = ""

    @property
    def is_active(self) -> bool:
        return not bool(self.exclusion_date.strip())


@dataclass(frozen=True, slots=True)
class ComparisonOptions:
    use_isbn_matching: bool = True
    use_title_fallback: bool = True
    use_fuzzy: bool = False
    fuzzy_threshold: int = 90
    match_rules: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fuzzy_threshold", max(0, min(100, int(self.fuzzy_threshold))))
        object.__setattr__(self, "match_rules", dict(self.match_rules))


@dataclass
class MatchResult:
    status: str
    method: str
    confidence: float
    excel: ExcelEntry
    database: DatabaseRecord | None = None
    note: str = ""
    source_type: str = "Вещества"
    matched_value: str = ""
    foreign_agent: ForeignAgentEntry | None = None
    # Точное поле ИРБИС отличает одноимённых авторов в ручной проверке и памяти решений.
    database_matched_value: str = ""


@dataclass
class ComparisonSummary:
    database_file: str
    excel_files: list[str]
    database_records: int
    database_records_with_isbn: int
    excel_rows: int
    matched_excel_rows: int
    unmatched_excel_rows: int
    result_rows: int
    exact_isbn_rows: int
    exact_title_rows: int
    probable_rows: int
    foreign_agents_file: str = ""
    foreign_agent_rows: int = 0
    matched_foreign_agent_rows: int = 0
    foreign_agent_result_rows: int = 0
    substance_matched_records: int = 0
    foreign_agent_matched_records: int = 0
    review_rows: int = 0
    review_records: int = 0
    output_file: str = ""
    modified_database_file: str = ""
    database_copy_file: str = ""
    modified_database_records: int = 0
    markers_already_present: int = 0
    markers_added: int = 0
    marker_duplicates_repaired: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class MarkerApplicationStats:
    already_present: int = 0
    added: int = 0
    duplicates_repaired: int = 0
