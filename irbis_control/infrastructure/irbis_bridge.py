from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Lock

from irbis_control.infrastructure.atomic_io import atomic_write_bytes, atomic_write_text
from irbis_control.infrastructure.irbis_client import IrbisClient
from irbis_control.infrastructure.irbis_models import (
    IrbisError,
    IrbisField,
    IrbisRecord,
    SnapshotEntry,
    SnapshotManifest,
)

ProgressCallback = Callable[[int, str], None]


def _record_text(fields: Iterable[IrbisField], newline: str = "\r\n") -> str:
    return newline.join(f"#{field.tag:03d}: {field.value}" for field in fields)


def record_hash(fields: Iterable[IrbisField]) -> str:
    canonical = "\n".join(f"{field.tag}#{field.value}" for field in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_txt_records(text: str) -> list[list[IrbisField]]:
    raw_records = [part for part in re.split(r"\r?\n\*{5}\s*(?:\r?\n|$)", text) if part.strip()]
    result: list[list[IrbisField]] = []

    for raw in raw_records:
        fields: list[IrbisField] = []
        for line in re.split(r"\r\n|\n|\r", raw):
            # Формат рабочей копии: "#TAG: <значение>". После двоеточия
            # функция записи добавляет ровно один служебный пробел ASCII. Важно снять
            # только его, а не `\s*`: начальные пробелы/табуляции могут быть
            # частью реального значения поля ИРБИС и участвуют в контрольном
            # хэше снимка.
            match = re.match(r"^\s*#(\d+): ?(.*)$", line)
            if match:
                fields.append(IrbisField(int(match.group(1)), match.group(2)))
        result.append(fields)
    return result


def write_snapshot_txt(records: Iterable[IrbisRecord], path: str | Path) -> list[SnapshotEntry]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    entries: list[SnapshotEntry] = []
    for index, record in enumerate(records, start=1):
        chunks.append(_record_text(record.fields))
        entries.append(SnapshotEntry(index, record.mfn, record.version, record_hash(record.fields)))
    payload = ("\r\n*****\r\n").join(chunks)
    if payload:
        payload += "\r\n*****\r\n"
    atomic_write_text(path, payload, encoding="utf-8", newline="")
    return entries


def load_manifest(path: str | Path) -> SnapshotManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SnapshotManifest(
        created_at=str(data["created_at"]),
        host=str(data["host"]),
        port=int(data["port"]),
        database=str(data["database"]),
        query=str(data.get("query", "")),
        snapshot_file=str(data["snapshot_file"]),
        records=[SnapshotEntry(**item) for item in data.get("records", [])],
    )


def save_manifest(manifest: SnapshotManifest, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_records_parallel(
    client: IrbisClient,
    database: str,
    mfns: list[int],
    *,
    workers: int = 4,
    progress_cb: ProgressCallback | None = None,
) -> list[IrbisRecord]:
    """Читает записи через несколько независимых сеансов ИРБИС.

    Команда C ИРБИС возвращает одну запись за запрос. При работе с удалённым сервером
    основное время занимает передача данных, поэтому несколько параллельных
    зарегистрированных сеансов заметно ускоряют создание снимка. Первый поток
    использует уже зарегистрированный клиент, остальные — его копии с независимыми
    идентификаторами процесса и команды.
    """
    total = len(mfns)
    if total == 0:
        return []

    workers = max(1, min(int(workers or 1), 8, total))
    if workers == 1:
        records: list[IrbisRecord] = []
        for index, mfn in enumerate(mfns, start=1):
            records.append(client.read_record(database, mfn))
            if progress_cb and (index == total or index % 25 == 0):
                progress_cb(20 + int(index / total * 75), f"Чтение записей: {index:,} из {total:,}")
        return records

    indexed_chunks: list[list[tuple[int, int]]] = [[] for _ in range(workers)]
    for index, mfn in enumerate(mfns):
        indexed_chunks[index % workers].append((index, mfn))

    results: list[IrbisRecord | None] = [None] * total
    progress_lock = Lock()
    completed = 0
    last_reported = 0

    def read_chunk(worker_index: int, items: list[tuple[int, int]]) -> None:
        nonlocal completed, last_reported

        def consume(active_client: IrbisClient) -> None:
            nonlocal completed, last_reported
            for result_index, mfn in items:
                record = active_client.read_record(database, mfn)
                results[result_index] = record
                report: tuple[int, str] | None = None
                with progress_lock:
                    completed += 1
                    if completed == total or completed - last_reported >= 25:
                        last_reported = completed
                        report = (
                            20 + int(completed / total * 75),
                            f"Чтение записей: {completed:,} из {total:,} • потоков: {workers}",
                        )
                if report and progress_cb:
                    progress_cb(*report)

        if worker_index == 0:
            consume(client)
        else:
            clone = client.clone()
            with clone as connected:
                consume(connected)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="irbis-read") as executor:
        futures = [
            executor.submit(read_chunk, worker_index, items)
            for worker_index, items in enumerate(indexed_chunks)
            if items
        ]
        for future in futures:
            future.result()

    missing = [mfns[index] for index, record in enumerate(results) if record is None]
    if missing:
        raise IrbisError(f"Не удалось получить {len(missing)} записей ИРБИС.")
    return [record for record in results if record is not None]


def create_irbis_snapshot(
    client: IrbisClient,
    database: str,
    query: str,
    snapshot_path: str | Path,
    manifest_path: str | Path,
    *,
    progress_cb: ProgressCallback | None = None,
    read_workers: int = 4,
) -> SnapshotManifest:
    mfns = client.search_all_mfns(database, query, progress_cb=progress_cb)
    if not mfns:
        raise IrbisError("По заданному запросу не найдено ни одной записи. Проверьте имя базы и запрос выборки.")
    records = read_records_parallel(
        client,
        database,
        mfns,
        workers=read_workers,
        progress_cb=progress_cb,
    )
    entries = write_snapshot_txt(records, snapshot_path)
    manifest = SnapshotManifest(
        created_at=datetime.now().isoformat(timespec="seconds"),
        host=client.host,
        port=client.port,
        database=database,
        query=query,
        snapshot_file=str(Path(snapshot_path).resolve()),
        records=entries,
    )
    save_manifest(manifest, manifest_path)
    if progress_cb:
        progress_cb(100, f"Рабочая база готова: {len(entries):,} записей")
    return manifest


def _records_to_dicts(records: Iterable[IrbisRecord]) -> list[dict]:
    return [
        {
            "mfn": record.mfn,
            "status": record.status,
            "version": record.version,
            "fields": [asdict(field) for field in record.fields],
        }
        for record in records
    ]


def apply_modified_snapshot(
    client: IrbisClient,
    manifest_path: str | Path,
    modified_txt_path: str | Path,
    backup_dir: str | Path,
    *,
    progress_cb: ProgressCallback | None = None,
    create_backup: bool = True,
) -> tuple[int, int, Path | None]:
    manifest = load_manifest(manifest_path)
    snapshot_path = Path(manifest.snapshot_file)
    if not snapshot_path.is_file():
        raise IrbisError(f"Не найдена исходная рабочая копия: {snapshot_path}")
    original_records = parse_txt_records(snapshot_path.read_text(encoding="utf-8-sig"))
    modified_records = parse_txt_records(Path(modified_txt_path).read_text(encoding="utf-8-sig"))
    if len(original_records) != len(manifest.records):
        raise IrbisError("Рабочая TXT-копия не соответствует карте MFN. Создайте новую синхронизацию.")
    if len(modified_records) != len(original_records):
        raise IrbisError(
            "Количество записей в изменённой TXT отличается от исходной копии. "
            "Автоприменение остановлено, чтобы не записать данные не в те MFN."
        )

    # Проверяем целостность исходного снимка, но отправляем в ИРБИС всю
    # выбранную TXT-копию, а не только записи, отличающиеся от снимка.
    # Это позволяет явно повторно отправить неизменённую базу или очищенную
    # от меток копию. Защита от перезаписи чужих изменений остаётся: перед
    # записью каждой записи сверяется версия на сервере.
    upload_records: list[tuple[SnapshotEntry, list[IrbisField]]] = []
    for meta, original, modified in zip(manifest.records, original_records, modified_records, strict=True):
        if record_hash(original) != meta.sha256:
            raise IrbisError("Исходная TXT-копия была изменена после синхронизации. Создайте её заново.")
        upload_records.append((meta, modified))

    live_before: list[IrbisRecord] = []
    conflicts = 0
    writable: list[tuple[SnapshotEntry, list[IrbisField], IrbisRecord]] = []
    for index, (meta, modified) in enumerate(upload_records, start=1):
        live = client.read_record(manifest.database, meta.mfn)
        if live.version != meta.version:
            conflicts += 1
        else:
            writable.append((meta, modified, live))
            live_before.append(live)
        if progress_cb:
            progress_cb(
                int(index / max(len(upload_records), 1) * 35),
                f"Проверка версий: {index:,} из {len(upload_records):,}",
            )

    backup_path: Path | None = None
    if live_before and create_backup:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"irbis_rollback_{manifest.database}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        atomic_write_text(
            backup_path,
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "database": manifest.database,
                    "records": _records_to_dicts(live_before),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    written = 0
    for index, (_meta, modified, live) in enumerate(writable, start=1):
        try:
            client.write_record(
                manifest.database,
                IrbisRecord(live.mfn, live.status, live.version, modified),
                actualize=1,
            )
        except Exception as exc:
            rollback_hint = f" Rollback-копия: {backup_path}." if backup_path else ""
            raise IrbisError(
                f"Запись прервана на MFN {live.mfn}; до сбоя записано: {written}.{rollback_hint} Причина: {exc}"
            ) from exc
        written += 1
        if progress_cb:
            progress_cb(
                35 + int(index / max(len(writable), 1) * 60),
                f"Запись в ИРБИС: {index:,} из {len(writable):,}",
            )

    # Если всё записалось без конфликтов, сразу обновляем локальный снимок и
    # карту MFN по фактическому состоянию сервера. Иначе версии в манифесте
    # устареют после первой же записи и повторная отправка даст ложные конфликты.
    if written and conflicts == 0:
        refreshed: list[IrbisRecord] = []
        for index, meta in enumerate(manifest.records, start=1):
            refreshed.append(client.read_record(manifest.database, meta.mfn))
            if progress_cb and (index == len(manifest.records) or index % 25 == 0):
                progress_cb(
                    95 + int(index / max(len(manifest.records), 1) * 5),
                    f"Обновление локального снимка: {index:,} из {len(manifest.records):,}",
                )
        manifest.records = write_snapshot_txt(refreshed, snapshot_path)
        manifest.created_at = datetime.now().isoformat(timespec="seconds")
        save_manifest(manifest, manifest_path)

    return written, conflicts, backup_path


def replace_txt_storage(target_path: str | Path, modified_path: str | Path, backup_dir: str | Path) -> Path:
    target = Path(target_path)
    modified = Path(modified_path)
    if not target.is_file():
        raise FileNotFoundError(target)
    if not modified.is_file():
        raise FileNotFoundError(modified)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{target.stem}_backup_{datetime.now():%Y%m%d_%H%M%S_%f}{target.suffix or '.txt'}"
    atomic_write_bytes(backup, target.read_bytes())
    atomic_write_bytes(target, modified.read_bytes())
    return backup
