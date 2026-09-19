from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable


def atomic_write_bytes(path: str | Path, payload: bytes) -> Path:
    """Replace *path* only after the complete payload is durable on disk."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Path:
    if newline not in (None, "", "\n"):
        text = text.replace("\n", newline)
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_via_path(path: str | Path, writer: Callable[[Path], None]) -> Path:
    """Atomically replace a file produced by a library that requires a path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        writer(temporary)
        # Windows requires a writable descriptor for ``fsync``.
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
