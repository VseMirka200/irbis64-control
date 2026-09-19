import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from irbis_control.infrastructure.atomic_io import atomic_write_text, atomic_write_via_path


class AtomicIoTests(unittest.TestCase):
    def test_failed_replace_preserves_existing_file_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "settings.json"
            target.write_text("old", encoding="utf-8")

            with patch(
                "irbis_control.infrastructure.atomic_io.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    atomic_write_text(target, "new")

            self.assertEqual("old", target.read_text(encoding="utf-8"))
            self.assertEqual([target], list(Path(temp_dir).iterdir()))

    def test_library_writer_failure_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "report.xlsx"
            target.write_bytes(b"old report")

            def failing_writer(temporary: Path) -> None:
                temporary.write_bytes(b"incomplete report")
                raise RuntimeError("export failed")

            with self.assertRaisesRegex(RuntimeError, "export failed"):
                atomic_write_via_path(target, failing_writer)

            self.assertEqual(b"old report", target.read_bytes())
            self.assertEqual([target], list(Path(temp_dir).iterdir()))


if __name__ == "__main__":
    unittest.main()
