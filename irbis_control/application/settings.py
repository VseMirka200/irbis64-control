from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from irbis_control.infrastructure.atomic_io import atomic_write_text

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
VALID_THEMES = {THEME_SYSTEM, THEME_LIGHT, THEME_DARK}


# Хранит общие настройки с безопасными значениями для первого запуска.
@dataclass
class ApplicationSettings:
    create_database_backup: bool = True
    check_updates_on_start: bool = True
    theme: str = THEME_SYSTEM


# Читает настройки по пути и возвращает безопасные значения, если файл повреждён или имеет неверную структуру.
def load_application_settings(path: str | Path) -> ApplicationSettings:
    source = Path(path)
    if not source.is_file():
        return ApplicationSettings()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ApplicationSettings()

    if not isinstance(payload, dict):
        return ApplicationSettings()

    create_backup = payload.get("create_database_backup", True)
    if not isinstance(create_backup, bool):
        create_backup = True
    check_updates = payload.get("check_updates_on_start", True)
    if not isinstance(check_updates, bool):
        check_updates = True
    theme = payload.get("theme", THEME_SYSTEM)
    if theme not in VALID_THEMES:
        theme = THEME_SYSTEM
    return ApplicationSettings(
        create_database_backup=create_backup,
        check_updates_on_start=check_updates,
        theme=theme,
    )


# Принимает путь и настройки, сохраняет их целиком и возвращает путь к готовому файлу.
def save_application_settings(path: str | Path, settings: ApplicationSettings) -> Path:
    payload = asdict(settings)
    payload["schema_version"] = 2
    return atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
