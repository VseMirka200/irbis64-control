from dataclasses import dataclass, field


# Хранит изменённые поля и прежние значения для объяснения различий отчётов.
@dataclass
class ResultDiffRow:
    change_type: str
    key: str
    values: dict[str, str]
    changed_fields: list[str] = field(default_factory=list)
    previous_values: dict[str, str] = field(default_factory=dict)


# Собирает итоги сравнения Excel для интерфейса и файла изменений.
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


# Сохраняет обе версии записи для отчёта об изменениях TXT.
@dataclass
class TextDiffRow:
    change_type: str
    record_number: int
    old_text: str = ""
    new_text: str = ""


# Собирает количество добавленных, удалённых и изменённых записей TXT.
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
