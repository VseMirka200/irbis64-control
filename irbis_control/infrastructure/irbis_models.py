from __future__ import annotations

from dataclasses import dataclass, field


# Отделяет ошибки протокола и сервера от ошибок обработки локальных данных.
class IrbisError(RuntimeError):
    pass


# Сохраняет номер и содержимое повторения поля без потери исходного текста.
@dataclass
class IrbisField:
    tag: int
    value: str


# Хранит поля, MFN и версию записи для безопасного обновления сервера.
@dataclass
class IrbisRecord:
    mfn: int
    status: int = 0
    version: int = 0
    fields: list[IrbisField] = field(default_factory=list)


# Связывает запись TXT с MFN, версией и контрольной суммой оригинала.
@dataclass
class SnapshotEntry:
    index: int
    mfn: int
    version: int
    sha256: str


# Хранит происхождение снимка, чтобы проверить его перед обратной записью.
@dataclass
class SnapshotManifest:
    created_at: str
    host: str
    port: int
    database: str
    query: str
    snapshot_file: str
    records: list[SnapshotEntry]
