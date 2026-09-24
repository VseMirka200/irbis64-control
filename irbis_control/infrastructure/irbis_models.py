from __future__ import annotations

from dataclasses import dataclass, field


class IrbisError(RuntimeError):
    """Ошибка протокола, соединения или ответа сервера ИРБИС."""


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
