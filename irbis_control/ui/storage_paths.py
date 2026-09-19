from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def shared_application_data_dir() -> Path:
    root = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericDataLocation
        )
    )
    folder = root / "IRBIS64Control"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def application_settings_path() -> Path:
    return shared_application_data_dir() / "application_settings.json"
