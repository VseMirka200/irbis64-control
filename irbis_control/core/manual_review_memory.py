from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from irbis_control.core.matcher import SOURCE_FOREIGN_AGENTS, normalize_author, normalize_title
from irbis_control.core.models import ComparisonSummary, MatchResult
from irbis_control.infrastructure.atomic_io import atomic_write_text


SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReviewIdentity:
    """Устойчивая идентичность ручного решения без привязки к конкретной книге/MFN."""

    key: str
    database_value: str
    registry_value: str
    registry_number: str
    source_type: str
    method: str


def _normalized_database_value(result: MatchResult) -> str:
    value = result.database_matched_value.strip()
    if value:
        if "Название" in result.method:
            return normalize_title(value)
        return normalize_author(value)

    # Совместимость со старыми MatchResult, где точное поле базы ещё не сохранялось.
    record = result.database
    if record is None:
        return ""
    if "Автор" in result.method:
        values = record.authors
        normalizer = normalize_author
    elif "Организация" in result.method:
        values = record.organizations
        normalizer = normalize_author
    elif "Название" in result.method:
        values = record.titles
        normalizer = normalize_title
    else:
        values = []
        normalizer = normalize_author

    normalized = [normalizer(item) for item in values if normalizer(item)]
    if len(normalized) == 1:
        return normalized[0]
    return ""


def review_identity(result: MatchResult) -> ReviewIdentity | None:
    """Возвращает ключ решения, пригодный для группировки и долговременной памяти.

    В ключ входят конкретное значение из поля ИРБИС и конкретный кандидат реестра.
    Поэтому подтверждение одинакового автора не может случайно подтвердить другого
    однофамильца/другую запись реестра.
    """
    if result.source_type != SOURCE_FOREIGN_AGENTS:
        return None

    database_value = _normalized_database_value(result)
    registry_value = normalize_author(result.matched_value)
    if not database_value or not registry_value:
        return None

    foreign = result.foreign_agent
    registry_number = foreign.registry_number.strip() if foreign is not None else ""
    method = result.method.rsplit(":", 1)[-1].strip().lower()
    key = "\x1f".join(
        (
            result.source_type.strip().lower(),
            method,
            database_value,
            registry_number.lower(),
            registry_value,
        )
    )
    return ReviewIdentity(
        key=key,
        database_value=(result.database_matched_value or database_value).strip(),
        registry_value=result.matched_value.strip(),
        registry_number=registry_number,
        source_type=result.source_type,
        method=result.method,
    )


def load_approved_review_keys(path: str | Path) -> set[str]:
    source = Path(path)
    if not source.is_file():
        return set()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    approved = payload.get("approved", [])
    if not isinstance(approved, list):
        return set()
    keys: set[str] = set()
    for item in approved:
        if isinstance(item, dict):
            key = item.get("key")
            if isinstance(key, str) and key:
                keys.add(key)
    return keys


def _load_approved_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    approved = payload.get("approved", [])
    if not isinstance(approved, list):
        return {}
    rows: dict[str, dict[str, str]] = {}
    for item in approved:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            rows[key] = {str(k): str(v) for k, v in item.items() if v is not None}
    return rows


def load_approved_review_rows(path: str | Path) -> list[dict[str, str]]:
    """Возвращает сохранённые подтверждения в виде строк для интерфейса управления памятью."""
    rows = _load_approved_rows(Path(path))
    return sorted(
        (dict(row) for row in rows.values()),
        key=lambda item: (
            item.get("database_value", "").casefold(),
            item.get("registry_value", "").casefold(),
            item.get("registry_number", "").casefold(),
        ),
    )


def remove_approved_review_keys(path: str | Path, keys: set[str] | list[str] | tuple[str, ...]) -> int:
    """Удаляет выбранные сохранённые подтверждения и возвращает число удалённых строк."""
    target = Path(path)
    rows = _load_approved_rows(target)
    requested = {str(key) for key in keys if str(key)}
    if not requested or not rows:
        return 0
    removed = 0
    for key in requested:
        if rows.pop(key, None) is not None:
            removed += 1
    if not removed:
        return 0
    payload = {
        "schema_version": SCHEMA_VERSION,
        "approved": sorted(rows.values(), key=lambda item: item.get("key", "")),
    }
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def clear_approved_review_memory(path: str | Path) -> int:
    """Очищает всю память подтверждений и возвращает число удалённых решений."""
    target = Path(path)
    rows = _load_approved_rows(target)
    if not rows:
        return 0
    payload = {"schema_version": SCHEMA_VERSION, "approved": []}
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def remember_approved_results(
    path: str | Path,
    results: list[MatchResult],
    approved_indices: set[int] | list[int] | tuple[int, ...],
) -> int:
    """Сохраняет только подтверждения оператора; отклонения намеренно не запоминаются."""
    target = Path(path)
    rows = _load_approved_rows(target)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    for result_index in approved_indices:
        if result_index < 0 or result_index >= len(results):
            continue
        identity = review_identity(results[result_index])
        if identity is None:
            continue
        if identity.key not in rows:
            added += 1
        rows[identity.key] = {
            "key": identity.key,
            "database_value": identity.database_value,
            "registry_value": identity.registry_value,
            "registry_number": identity.registry_number,
            "source_type": identity.source_type,
            "method": identity.method,
            "confirmed_at": now,
        }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "approved": sorted(rows.values(), key=lambda item: item.get("key", "")),
    }
    try:
        atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Невозможность сохранить удобную память решений не должна прерывать
        # саму проверку и постановку меток.
        return 0
    return added


def apply_remembered_confirmations(
    results: list[MatchResult],
    summary: ComparisonSummary,
    path: str | Path,
) -> int:
    """Автоматически подтверждает пограничные совпадения, уже одобренные оператором ранее."""
    keys = load_approved_review_keys(path)
    if not keys:
        return 0
    decisions: dict[int, bool] = {}
    for index, result in enumerate(results):
        if result.status != "Возможное совпадение":
            continue
        identity = review_identity(result)
        if identity is not None and identity.key in keys:
            decisions[index] = True
    if not decisions:
        return 0

    # Локальный импорт исключает цикл импорта matcher -> memory -> matcher на старте.
    from irbis_control.core.matcher import apply_manual_review_decisions

    apply_manual_review_decisions(
        results,
        summary,
        decisions,
        confirmation_note="Подтверждено автоматически по сохранённому решению оператора",
    )
    return len(decisions)
