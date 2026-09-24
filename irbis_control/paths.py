from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    """Возвращает корень исходников для запуска без сборки EXE."""
    return Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """Возвращает каталог ресурсов с учётом распаковки сборки PyInstaller."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else project_root()


def resource_path(*parts: str) -> str:
    return str(runtime_root().joinpath(*parts))


def icon_path(filename: str) -> str:
    return resource_path("assets", "icons", filename)
