from __future__ import annotations

import json
from pathlib import Path

from irbis_control.core.matcher import (
    DEFAULT_AGE_MARKER,
    DEFAULT_AGE_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    DEFAULT_SUBSTANCE_MARKER,
    DEFAULT_SUBSTANCE_MARKER_FIELD,
    EXTRA_MATCH_RULES,
)
from irbis_control.infrastructure.atomic_io import atomic_write_text

MarkerSetting = str | int | bool

DEFAULT_MARKER_SETTINGS: dict[str, MarkerSetting] = {
    **{key: False for key in EXTRA_MATCH_RULES},
    "use_isbn_matching": True,
    "use_title_fallback": True,
    "use_fuzzy": False,
    "fuzzy_threshold": 90,
    "create_excel_report": True,
    "report_substances": True,
    "report_foreign_agents": True,
    "report_combined": False,
    "report_summary": False,
    "report_deduplicate": True,
    "report_sort": "record",
    "report_only": False,
    "substance_marker": DEFAULT_SUBSTANCE_MARKER,
    "foreign_agent_marker_template": DEFAULT_FOREIGN_AGENT_MARKER_TEMPLATE,
    "age_marker": DEFAULT_AGE_MARKER,
    "substance_marker_field": DEFAULT_SUBSTANCE_MARKER_FIELD,
    "foreign_agent_marker_field": DEFAULT_FOREIGN_AGENT_MARKER_FIELD,
    "age_marker_field": DEFAULT_AGE_MARKER_FIELD,
}


# Читает настройки меток из указанного файла и отбрасывает значения неверного типа или диапазона.
def load_marker_settings(path: str | Path) -> dict[str, MarkerSetting]:
    settings = dict(DEFAULT_MARKER_SETTINGS)
    source = Path(path)
    if not source.is_file():
        return settings

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return settings
    if not isinstance(data, dict):
        return settings

    for key, default in settings.items():
        value = data.get(key)
        if isinstance(default, bool) and isinstance(value, bool):
            settings[key] = value
        elif isinstance(default, str) and isinstance(value, str):
            settings[key] = value
        elif isinstance(default, int) and type(value) is int:
            if key == "fuzzy_threshold" and 70 <= value <= 100:
                settings[key] = value
            elif key.endswith("_field") and 1 <= value <= 999:
                settings[key] = value

    # Приблизительный поиск пока не меняет базу: интерфейс использует только точные совпадения.
    settings["use_fuzzy"] = False
    settings["fuzzy_threshold"] = 90
    return settings


# Сохраняет переданный набор настроек атомарно и возвращает путь к готовому JSON-файлу.
def save_marker_settings(path: str | Path, settings: dict[str, MarkerSetting]) -> Path:
    return atomic_write_text(
        path,
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
