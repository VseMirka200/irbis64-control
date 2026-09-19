import json
import tempfile
import unittest
from pathlib import Path

from irbis_control.infrastructure.irbis_bridge import (
    IrbisError,
    IrbisField,
    IrbisRecord,
    SnapshotManifest,
    apply_modified_snapshot,
    save_manifest,
    write_snapshot_txt,
)


class FakeIrbisClient:
    def __init__(self, records: dict[int, IrbisRecord], *, fail_on_write: int | None = None) -> None:
        self.records = records
        self.fail_on_write = fail_on_write
        self.write_calls = 0
        self.written_mfns: list[int] = []

    def read_record(self, _database: str, mfn: int) -> IrbisRecord:
        record = self.records[mfn]
        return IrbisRecord(record.mfn, record.status, record.version, list(record.fields))

    def write_record(self, _database: str, record: IrbisRecord, *, actualize: int = 1) -> int:
        self.write_calls += 1
        if self.fail_on_write == self.write_calls:
            raise IrbisError("simulated write failure")
        self.written_mfns.append(record.mfn)
        self.records[record.mfn] = IrbisRecord(
            record.mfn,
            record.status,
            record.version + 1,
            list(record.fields),
        )
        return record.mfn


class SnapshotWriteTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, list[IrbisRecord]]:
        original = [
            IrbisRecord(10, version=3, fields=[IrbisField(200, "first")]),
            IrbisRecord(20, version=7, fields=[IrbisField(200, "second")]),
        ]
        snapshot = root / "snapshot.txt"
        entries = write_snapshot_txt(original, snapshot)
        manifest = SnapshotManifest(
            created_at="2026-01-01T00:00:00",
            host="127.0.0.1",
            port=6666,
            database="IBIS",
            query="I=$",
            snapshot_file=str(snapshot),
            records=entries,
        )
        manifest_path = root / "snapshot.map.json"
        save_manifest(manifest, manifest_path)
        modified = root / "modified.txt"
        write_snapshot_txt(
            [
                IrbisRecord(10, version=3, fields=[IrbisField(200, "first changed")]),
                IrbisRecord(20, version=7, fields=[IrbisField(200, "second changed")]),
            ],
            modified,
        )
        return snapshot, manifest_path, modified, original

    def test_version_conflict_is_skipped_without_overwriting_live_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _snapshot, manifest, modified, original = self._fixture(root)
            live = {
                10: original[0],
                20: IrbisRecord(20, version=8, fields=[IrbisField(200, "edited elsewhere")]),
            }
            client = FakeIrbisClient(live)

            written, conflicts, backup = apply_modified_snapshot(
                client, manifest, modified, root / "backups"
            )

            self.assertEqual((1, 1), (written, conflicts))
            self.assertEqual([10], client.written_mfns)
            self.assertEqual("edited elsewhere", client.records[20].fields[0].value)
            self.assertIsNotNone(backup)

    def test_mid_batch_failure_keeps_rollback_for_every_writable_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _snapshot, manifest, modified, original = self._fixture(root)
            client = FakeIrbisClient({record.mfn: record for record in original}, fail_on_write=2)

            with self.assertRaisesRegex(IrbisError, "simulated write failure"):
                apply_modified_snapshot(client, manifest, modified, root / "backups")

            self.assertEqual([10], client.written_mfns)
            backups = list((root / "backups").glob("irbis_rollback_*.json"))
            self.assertEqual(1, len(backups))
            payload = json.loads(backups[0].read_text(encoding="utf-8"))
            self.assertEqual([10, 20], [record["mfn"] for record in payload["records"]])

    def test_backup_can_be_disabled_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _snapshot, manifest, modified, original = self._fixture(root)
            client = FakeIrbisClient({record.mfn: record for record in original})

            written, conflicts, backup = apply_modified_snapshot(
                client,
                manifest,
                modified,
                root / "backups",
                create_backup=False,
            )

            self.assertEqual((2, 0), (written, conflicts))
            self.assertIsNone(backup)
            self.assertFalse((root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
