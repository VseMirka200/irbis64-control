from __future__ import annotations

from collections.abc import Iterable

from irbis_control.core.models import MatchResult


def review_record_group_key(result: MatchResult) -> str:
    """Ключ одной операторской задачи: запись ИРБИС и тип реестра."""

    record = result.database
    if record is None:
        return f"candidate\x1f{id(result)}"
    source_record_number = record.source_record_number or record.record_number
    return "\x1f".join(
        (
            str(record.source_file).strip().casefold(),
            str(source_record_number),
            result.source_type.strip().casefold(),
        )
    )


def count_review_record_groups(results: Iterable[MatchResult]) -> int:
    return len(
        {
            review_record_group_key(result)
            for result in results
            if result.status == "Возможное совпадение" and result.database is not None
        }
    )
