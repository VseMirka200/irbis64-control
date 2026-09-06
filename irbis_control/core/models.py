from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Хранит данные записи и её положение в источнике, чтобы вернуть метки в нужную запись.
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
    def main_isbn(self) -> str:
        return self.isbns[0] if self.isbns else ""

    @property
    def main_title(self) -> str:
        return self.titles[0] if self.titles else ""

    @property
    def main_author(self) -> str:
        return self.authors[0] if self.authors else ""


# Хранит строку перечня вместе с исходными значениями для отчёта и проверки совпадения.
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


# Хранит запись реестра и даты, по которым определяется её актуальность.
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

    @property
    def is_active(self) -> bool:
        return not bool(self.exclusion_date.strip())


# Связывает строку источника с записью базы и основанием совпадения.
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


# Собирает статистику запуска для интерфейса и экспортируемого отчёта.
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
    output_file: str = ""
    modified_database_file: str = ""
    modified_database_records: int = 0
    markers_already_present: int = 0
    markers_added: int = 0
    marker_duplicates_repaired: int = 0
    warnings: list[str] = field(default_factory=list)


# Разделяет добавленные метки, уже существующие метки и исправленные дубли.
@dataclass
class MarkerApplicationStats:
    already_present: int = 0
    added: int = 0
    duplicates_repaired: int = 0
