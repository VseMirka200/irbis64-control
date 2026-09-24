from dataclasses import dataclass, field


@dataclass
class ResultDiffRow:
    change_type: str
    key: str
    values: dict[str, str]
    changed_fields: list[str] = field(default_factory=list)
    previous_values: dict[str, str] = field(default_factory=dict)


@dataclass
class ResultDiffSummary:
    old_file: str
    new_file: str
    output_file: str
    added: int
    removed: int
    changed: int
    unchanged: int
    total_changes: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class TextDiffRow:
    change_type: str
    record_number: int
    old_text: str = ""
    new_text: str = ""


@dataclass
class TextDiffSummary:
    old_file: str
    new_file: str
    output_file: str
    added: int
    removed: int
    changed: int
    unchanged: int
    total_changes: int
