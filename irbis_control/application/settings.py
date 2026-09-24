from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from irbis_control.infrastructure.atomic_io import atomic_write_text

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
VALID_THEMES = {THEME_SYSTEM, THEME_LIGHT, THEME_DARK}


@dataclass
class ApplicationSettings:
    create_database_backup: bool = True
    check_updates_on_start: bool = True
    theme: str = THEME_SYSTEM
    use_nkp_drug_registry: bool = True
    use_nkp_foreign_agents_registry: bool = True


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
    use_nkp_drug = payload.get("use_nkp_drug_registry", True)
    if not isinstance(use_nkp_drug, bool):
        use_nkp_drug = True
    use_nkp_foreign = payload.get("use_nkp_foreign_agents_registry", True)
    if not isinstance(use_nkp_foreign, bool):
        use_nkp_foreign = True
    return ApplicationSettings(
        create_database_backup=create_backup,
        check_updates_on_start=check_updates,
        theme=theme,
        use_nkp_drug_registry=use_nkp_drug,
        use_nkp_foreign_agents_registry=use_nkp_foreign,
    )


def save_application_settings(path: str | Path, settings: ApplicationSettings) -> Path:
    payload = asdict(settings)
    payload["schema_version"] = 3
    return atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
