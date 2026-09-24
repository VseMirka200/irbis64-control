from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from irbis_control.infrastructure.atomic_io import atomic_write_text


def shared_application_data_dir() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
    folder = root / "IRBIS64Control"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def application_settings_path() -> Path:
    return shared_application_data_dir() / "application_settings.json"


# Qt добавляет имя приложения к пользовательскому каталогу данных.
def app_data_dir() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def database_connector_config_path() -> Path:
    return app_data_dir() / "database_connector.json"


def manual_review_memory_path() -> Path:
    """Память решений в отдельном пользовательском каталоге вне установки программы."""
    target = shared_application_data_dir() / "decision_memory" / "manual_review_decisions.json"
    legacy = app_data_dir() / "manual_review_confirmations.json"
    if not target.is_file() and legacy.is_file():
        try:
            atomic_write_text(target, legacy.read_text(encoding="utf-8"), encoding="utf-8")
        except (OSError, UnicodeError):
            return legacy
    return target
