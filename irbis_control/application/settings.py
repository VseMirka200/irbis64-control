from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from irbis_control.infrastructure.atomic_io import atomic_write_text

ThemeName = Literal["system", "light", "dark"]


@dataclass
class ApplicationSettings:
    theme: ThemeName = "system"
    create_database_backup: bool = True
    check_updates_on_start: bool = True


def load_application_settings(path: str | Path) -> ApplicationSettings:
    source = Path(path)
    if not source.is_file():
        return ApplicationSettings()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ApplicationSettings()

    theme = payload.get("theme", "system")
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    create_backup = payload.get("create_database_backup", True)
    if not isinstance(create_backup, bool):
        create_backup = True
    check_updates = payload.get("check_updates_on_start", True)
    if not isinstance(check_updates, bool):
        check_updates = True
    return ApplicationSettings(
        theme=theme,
        create_database_backup=create_backup,
        check_updates_on_start=check_updates,
    )


def save_application_settings(path: str | Path, settings: ApplicationSettings) -> Path:
    return atomic_write_text(
        path,
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
