from pathlib import Path
from unittest.mock import patch

from irbis_control.ui import storage_paths


def test_review_memory_is_migrated_to_separate_persistent_directory(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    legacy_data = tmp_path / "legacy"
    legacy_data.mkdir()
    legacy = legacy_data / "manual_review_confirmations.json"
    legacy.write_text('{"schema_version": 2, "approved": [], "rejected": []}', encoding="utf-8")

    with (
        patch.object(storage_paths, "shared_application_data_dir", return_value=shared),
        patch.object(storage_paths, "app_data_dir", return_value=legacy_data),
    ):
        target = storage_paths.manual_review_memory_path()

    assert target == shared / "decision_memory" / "manual_review_decisions.json"
    assert target.read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
    assert legacy.is_file()
