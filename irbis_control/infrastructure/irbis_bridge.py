from __future__ import annotations

import hashlib
import json
import random
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from irbis_control.infrastructure.atomic_io import atomic_write_bytes, atomic_write_text

RECORD_SEPARATOR = "\x1f\x1e"
TXT_SEPARATOR = "*****"
ALL_RECORD_FORMAT = "!&uf('+0')"


class IrbisError(RuntimeError):
    pass


@dataclass
class IrbisField:
    tag: int
    value: str


@dataclass
class IrbisRecord:
    mfn: int
    status: int = 0
    version: int = 0
    fields: list[IrbisField] = field(default_factory=list)


@dataclass
class SnapshotEntry:
    index: int
    mfn: int
    version: int
    sha256: str


@dataclass
class SnapshotManifest:
    created_at: str
    host: str
    port: int
    database: str
    query: str
    snapshot_file: str
    records: list[SnapshotEntry]


ProgressCallback = Callable[[int, str], None]


def _record_text(fields: Iterable[IrbisField], newline: str = "\r\n") -> str:
    return newline.join(f"#{field.tag:03d}: {field.value}" for field in fields)


def record_hash(fields: Iterable[IrbisField]) -> str:
    canonical = "\n".join(f"{field.tag}#{field.value}" for field in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_txt_records(text: str) -> list[list[IrbisField]]:
    raw_records = [part for part in __import__("re").split(r"\r?\n\*{5}\s*(?:\r?\n|$)", text) if part.strip()]
    result: list[list[IrbisField]] = []
    import re

    for raw in raw_records:
        fields: list[IrbisField] = []
        for line in re.split(r"\r\n|\n|\r", raw):
            # Формат рабочей копии: "#TAG: <значение>". После двоеточия
            # writer добавляет ровно один служебный ASCII-пробел. Важно снять
            # только его, а не `\s*`: начальные пробелы/табуляции могут быть
            # частью реального значения поля ИРБИС и участвуют в контрольном
            # хэше снимка.
            match = re.match(r"^\s*#(\d+): ?(.*)$", line)
            if match:
                fields.append(IrbisField(int(match.group(1)), match.group(2)))
        result.append(fields)
    return result




def parse_all_format_record(payload: str, fallback_mfn: int = 0) -> IrbisRecord:
    """Decode the IRBIS ``&uf('+0')`` representation returned by search+format.

    The server packs the record into control-character separated protocol lines.
    This is the same logical representation used by command C, but it arrives
    inside a single search result line (``MFN#<formatted record>``).
    """
    if payload is None:
        raise IrbisError("Пустое представление записи ИРБИС.")

    normalized = str(payload).replace("\x1f\x1e", "\n")
    normalized = normalized.replace("\x1f", "\n").replace("\x1e", "\n")
    parts = [part.strip("\r") for part in normalized.split("\n") if part.strip("\r") != ""]

    # &uf('+0') may put a service fragment before the actual protocol record.
    start = -1
    for index, part in enumerate(parts):
        if __import__("re").match(r"^\d+#-?\d+$", part.strip()):
            start = index
            break
    if start < 0:
        raise IrbisError(f"Не удалось разобрать запись ИРБИС MFN {fallback_mfn or '?'}.")

    lines = [part.strip() if i < 2 else part for i, part in enumerate(parts[start:])]
    first = lines[0].split("#", 1)
    second = lines[1].split("#", 1) if len(lines) > 1 else ["0", "0"]
    try:
        mfn = int(first[0])
    except (TypeError, ValueError):
        mfn = int(fallback_mfn or 0)
    try:
        status = int(first[1]) if len(first) > 1 else 0
    except ValueError:
        status = 0
    try:
        version = int(second[1]) if len(second) > 1 else 0
    except ValueError:
        version = 0

    fields: list[IrbisField] = []
    for line in lines[2:]:
        if not line or "#" not in line:
            continue
        tag_text, value = line.split("#", 1)
        try:
            fields.append(IrbisField(int(tag_text), value))
        except ValueError:
            continue
    return IrbisRecord(mfn or int(fallback_mfn or 0), status, version, fields)


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


class IrbisClient:
    """Small IRBIS64 TCP client for the commands needed by IRBIS64 Control.

    The packet layout follows the public IRBIS64 TCP client examples: A/B for
    registration, K for search+format, C for record reading and D for update.
    """

    def __init__(
        self,
        host: str,
        port: int = 6666,
        login: str = "",
        password: str = "",
        arm: str = "C",
        timeout: float = 20.0,
        connection_attempts: int = 3,
        retry_delay: float = 0.5,
    ) -> None:
        self.host = host.strip() or "127.0.0.1"
        self.port = int(port)
        self.login = login
        self.password = password
        self.arm = arm or "C"
        self.timeout = timeout
        self.connection_attempts = max(1, int(connection_attempts))
        self.retry_delay = max(0.0, float(retry_delay))
        self.process_id = random.randint(10_000_000, 99_999_999)
        self.command_number = 1
        self.registered = False

    def clone(self) -> "IrbisClient":
        """Create an independent logical IRBIS client for parallel reads."""
        return IrbisClient(
            self.host,
            self.port,
            self.login,
            self.password,
            self.arm,
            self.timeout,
            self.connection_attempts,
            self.retry_delay,
        )

    def _packet(self, command: str, extra: Iterable[str], *, auth: bool = False) -> bytes:
        header = [
            command,
            self.arm,
            command,
            str(self.process_id),
            str(self.command_number),
            self.password if auth else "",
            self.login if auth else "",
            "",
            "",
            "",
        ]
        body = "\n".join([*header, *[str(item) for item in extra]])
        encoded = body.encode("utf-8")
        return str(len(encoded)).encode("ascii") + b"\n" + encoded

    def _send(self, packet: bytes) -> bytes:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(packet)
                chunks: list[bytes] = []
                while True:
                    try:
                        block = sock.recv(65536)
                    except socket.timeout:
                        break
                    if not block:
                        break
                    chunks.append(block)
        except OSError as exc:
            raise IrbisError(f"Не удалось подключиться к {self.host}:{self.port}: {exc}") from exc
        finally:
            self.command_number += 1
        data = b"".join(chunks)
        if not data:
            raise IrbisError("Сервер ИРБИС не вернул ответ.")
        return data

    @staticmethod
    def _decode(data: bytes, *, registration: bool = False) -> list[str]:
        encodings = ("cp1251", "utf-8") if registration else ("utf-8", "cp1251")
        for encoding in encodings:
            try:
                return data.decode(encoding).split("\r\n")
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace").split("\r\n")

    @staticmethod
    def _status(lines: list[str]) -> int:
        if len(lines) <= 10:
            raise IrbisError("Некорректный ответ сервера ИРБИС.")
        try:
            return int(lines[10].strip())
        except ValueError as exc:
            raise IrbisError(f"Некорректный код ответа ИРБИС: {lines[10]!r}") from exc

    def _register_once(self) -> None:
        packet = self._packet("A", [self.login, self.password])
        lines = self._decode(self._send(packet), registration=True)
        status = self._status(lines)
        if status != 0:
            raise IrbisError(f"ИРБИС отклонил вход. Код: {status}")
        self.registered = True

    def register(self) -> None:
        last_error: IrbisError | None = None
        for attempt in range(1, self.connection_attempts + 1):
            try:
                self._register_once()
                return
            except IrbisError as exc:
                last_error = exc
                # Неверные учётные данные не являются временным сетевым сбоем.
                if "отклонил вход" in str(exc).casefold():
                    raise
                if attempt < self.connection_attempts and self.retry_delay:
                    time.sleep(self.retry_delay * attempt)
        if last_error is not None:
            raise IrbisError(
                f"Не удалось подключиться к ИРБИС после {self.connection_attempts} попыток: "
                f"{last_error}"
            ) from last_error

    def unregister(self) -> None:
        if not self.registered:
            return
        try:
            lines = self._decode(self._send(self._packet("B", [self.login])))
            self._status(lines)
        finally:
            self.registered = False

    def __enter__(self) -> "IrbisClient":
        self.register()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.unregister()
        except Exception:
            pass

    @staticmethod
    def _payload_text(data: bytes, *, ansi: bool = False) -> str:
        """Return the IRBIS response payload after the 10-line service header."""
        encodings = ("cp1251", "utf-8") if ansi else ("utf-8", "cp1251")
        text = ""
        for encoding in encodings:
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = data.decode("cp1251" if ansi else "utf-8", errors="replace")
        # IRBIS answers use CRLF for the 10 service header lines.  Split only
        # ten times so text files can keep their own line structure intact.
        parts = text.split("\r\n", 10)
        return parts[10] if len(parts) > 10 else ""

    def read_text_file(self, specification: str) -> str:
        """Read a text resource from the IRBIS server (command L)."""
        if not specification.strip():
            return ""
        payload = self._payload_text(
            self._send(self._packet("L", [specification.strip()])), ansi=True
        )
        return payload.replace("\x1f\x1e", "\r\n").replace("\x1f", "\r\n")

    def list_files(self, specification: str) -> list[str]:
        """List server files matching an IRBIS file specification (command !)."""
        if not specification.strip():
            return []
        payload = self._payload_text(
            self._send(self._packet("!", [specification.strip()])), ansi=True
        )
        payload = payload.replace("\x1f\x1e", "\r\n").replace("\x1f", "\r\n")
        result: list[str] = []
        for line in payload.replace("\x00", "").splitlines():
            value = line.strip()
            if value and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _parse_database_menu(text: str) -> list[dict[str, str]]:
        """Parse DBNAM2.MNU into [{name, description}] for the Cataloger ARM."""
        normalized = text.replace("\x1f\x1e", "\n").replace("\x1f", "\n")
        lines = [line.strip() for line in normalized.replace("\r", "").split("\n") if line.strip()]
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        index = 0
        while index < len(lines):
            raw_name = lines[index]
            description = lines[index + 1] if index + 1 < len(lines) else ""
            index += 2
            # In DBNAM2.MNU a leading '-' means the database is unavailable
            # for data input in the Cataloger ARM, therefore do not offer it
            # in a write-capable selector.
            if raw_name.startswith("-"):
                continue
            name = raw_name.strip().upper()
            if not name or name.startswith("*") or name in seen:
                continue
            seen.add(name)
            result.append({"name": name, "description": description})
        return result

    def list_databases(self) -> list[dict[str, str]]:
        """Return databases available to the Cataloger as a display-ready list.

        Primary source is DATAI/DBNAM2.MNU.  If that menu is unavailable on a
        particular installation, fall back to existing *.PAR descriptors in
        the server database-information directory.
        """
        try:
            menu = self.read_text_file("1..DBNAM2.MNU")
            databases = self._parse_database_menu(menu)
            if databases:
                return databases
        except Exception:
            pass

        names: list[str] = []
        try:
            files = self.list_files("1..*.PAR")
        except Exception:
            files = []
        for item in files:
            filename = item.replace("/", "\\").rsplit("\\", 1)[-1]
            if not filename.lower().endswith(".par"):
                continue
            name = filename[:-4].strip().upper()
            if name and name not in names:
                names.append(name)
        return [{"name": name, "description": ""} for name in sorted(names)]

    def search(self, database: str, expression: str, number: int, first: int, format_pft: str = "@brief") -> tuple[int, list[int]]:
        packet = self._packet(
            "K",
            [database, expression, str(number), str(first), format_pft, "", "", ""],
        )
        lines = self._decode(self._send(packet))
        status = self._status(lines)
        if status != 0:
            raise IrbisError(f"Ошибка поиска ИРБИС. Код: {status}")
        if len(lines) < 12:
            return 0, []
        try:
            total = int(lines[11].strip() or "0")
        except ValueError:
            total = 0
        mfns: list[int] = []
        for line in lines[12:]:
            if not line or "#" not in line:
                continue
            prefix = line.split("#", 1)[0].strip()
            try:
                mfns.append(int(prefix))
            except ValueError:
                continue
        return total, mfns

    def search_all_mfns(
        self,
        database: str,
        expression: str,
        *,
        page_size: int = 500,
        progress_cb: ProgressCallback | None = None,
    ) -> list[int]:
        first = 1
        all_mfns: list[int] = []
        total = None
        while total is None or len(all_mfns) < total:
            current_total, page = self.search(database, expression, page_size, first, "@brief")
            if total is None:
                total = current_total
            if not page:
                break
            all_mfns.extend(page)
            first += len(page)
            if progress_cb and total:
                progress_cb(min(20, int(len(all_mfns) / total * 20)), f"Найдено MFN: {len(all_mfns):,} из {total:,}")
            if len(page) < page_size:
                break
        return list(dict.fromkeys(all_mfns))

    def search_read_page(
        self,
        database: str,
        expression: str,
        *,
        number: int = 500,
        first: int = 1,
    ) -> tuple[int, list[IrbisRecord]]:
        """Search and receive complete records in one server round trip.

        Command K can format every found record. ``&uf('+0')`` asks IRBIS to
        return the full protocol representation, so we avoid a separate command
        C request for every MFN and do not create a local TXT snapshot.
        """
        number = max(1, min(int(number or 500), 2000))
        packet = self._packet(
            "K",
            [database, expression, str(number), str(max(1, int(first))), ALL_RECORD_FORMAT, "", "", ""],
        )
        lines = self._decode(self._send(packet))
        status = self._status(lines)
        if status != 0:
            raise IrbisError(f"Ошибка пакетного чтения ИРБИС. Код: {status}")
        if len(lines) < 12:
            return 0, []
        try:
            total = int(lines[11].strip() or "0")
        except ValueError:
            total = 0

        records: list[IrbisRecord] = []
        for line in lines[12:]:
            if not line or "#" not in line:
                continue
            mfn_text, payload = line.split("#", 1)
            try:
                mfn = int(mfn_text.strip())
            except ValueError:
                continue
            try:
                records.append(parse_all_format_record(payload, mfn))
            except IrbisError:
                # Do not silently invent a record: a malformed page should be
                # visible to the caller, because writing by the wrong MFN is
                # worse than stopping the operation.
                raise
        return total, records

    def search_read_all(
        self,
        database: str,
        expression: str,
        *,
        page_size: int = 500,
        progress_cb: ProgressCallback | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ) -> list[IrbisRecord]:
        """Read a search result in large formatted pages without a TXT copy."""
        first = 1
        total: int | None = None
        records: list[IrbisRecord] = []
        while total is None or len(records) < total:
            if cancel_cb and cancel_cb():
                raise IrbisError("Операция отменена пользователем.")
            current_total, page = self.search_read_page(
                database, expression, number=page_size, first=first
            )
            if total is None:
                total = current_total
                if total <= 0:
                    return []
            if not page:
                break
            records.extend(page)
            first += len(page)
            if progress_cb:
                progress_cb(
                    min(48, 5 + int(len(records) / max(total, 1) * 43)),
                    f"Пакетное чтение ИРБИС: {len(records):,} из {total:,}",
                )
            if len(page) < max(1, int(page_size)):
                break
        return records

    def tune_read_page_size(
        self,
        database: str,
        expression: str,
        *,
        candidates: Iterable[int] = (100, 500, 1000, 1500, 2000),
        progress_cb: ProgressCallback | None = None,
    ) -> tuple[int, int]:
        """Return the largest page size successfully handled by this server.

        The probe is read-only. IRBIS command D writes exactly one record, so
        write throughput must not be tested by modifying a production record.
        """
        safe_size = 0
        total = 0
        normalized = sorted({max(1, min(int(value), 2000)) for value in candidates})
        for index, size in enumerate(normalized, start=1):
            if progress_cb:
                progress_cb(
                    55 + int(index / max(len(normalized), 1) * 40),
                    f"Проверка пакета чтения: {size} записей…",
                )
            try:
                current_total, page = self.search_read_page(
                    database, expression or "I=$", number=size, first=1
                )
            except IrbisError:
                if safe_size == 0:
                    raise
                break
            total = current_total
            expected = min(size, current_total)
            if current_total > 0 and len(page) < expected:
                break
            safe_size = size
        if safe_size == 0:
            raise IrbisError("Сервер не вернул полный тестовый пакет чтения.")
        return safe_size, total

    def read_record(self, database: str, mfn: int, *, lock: int = 0) -> IrbisRecord:
        lines = self._decode(self._send(self._packet("C", [database, str(mfn), str(lock)])))
        status = self._status(lines)
        if status != 0:
            raise IrbisError(f"Не удалось прочитать MFN {mfn}. Код: {status}")
        if len(lines) < 13:
            raise IrbisError(f"Сервер вернул неполную запись MFN {mfn}.")
        mfn_part = lines[11].split("#", 1)
        ver_part = lines[12].split("#", 1)
        try:
            real_mfn = int(mfn_part[0])
        except ValueError:
            real_mfn = int(mfn)
        try:
            record_status = int(mfn_part[1]) if len(mfn_part) > 1 else 0
        except ValueError:
            record_status = 0
        try:
            version = int(ver_part[1]) if len(ver_part) > 1 else 0
        except ValueError:
            version = 0
        fields: list[IrbisField] = []
        for line in lines[13:]:
            if not line or "#" not in line:
                continue
            tag_text, value = line.split("#", 1)
            try:
                fields.append(IrbisField(int(tag_text), value))
            except ValueError:
                continue
        return IrbisRecord(real_mfn, record_status, version, fields)

    def write_record(self, database: str, record: IrbisRecord, *, lock: int = 0, actualize: int = 1) -> int:
        record_text = f"{record.mfn}#{record.status}{RECORD_SEPARATOR}0#{record.version}"
        for field in record.fields:
            record_text += f"{RECORD_SEPARATOR}{field.tag}#{field.value}"
        record_text += RECORD_SEPARATOR
        lines = self._decode(
            self._send(
                self._packet(
                    "D",
                    [database, str(lock), str(actualize), record_text],
                    auth=True,
                )
            )
        )
        status = self._status(lines)
        if status <= 0:
            raise IrbisError(f"Не удалось сохранить MFN {record.mfn}. Код: {status}")
        return status


def read_records_parallel(
    client: IrbisClient,
    database: str,
    mfns: list[int],
    *,
    workers: int = 4,
    progress_cb: ProgressCallback | None = None,
) -> list[IrbisRecord]:
    """Read records using several independent IRBIS sessions.

    IRBIS command C returns one record per request. On remote/server installs the
    round-trip latency dominates, so a small number of parallel registered
    sessions considerably reduces snapshot time. The first worker reuses the
    already registered client; remaining workers use clones with independent
    process/command identifiers.
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
        raise IrbisError(
            "По заданному запросу не найдено ни одной записи. Проверьте имя базы и запрос выборки."
        )
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
    for meta, original, modified in zip(manifest.records, original_records, modified_records):
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
                f"Запись прервана на MFN {live.mfn}; до сбоя записано: {written}."
                f"{rollback_hint} Причина: {exc}"
            ) from exc
        written += 1
        if progress_cb:
            progress_cb(
                35 + int(index / max(len(writable), 1) * 60),
                f"Запись в ИРБИС: {index:,} из {len(writable):,}",
            )

    # Если всё записалось без конфликтов, сразу обновляем локальный снимок и
    # карту MFN по фактическому состоянию сервера. Иначе версии в manifest
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
