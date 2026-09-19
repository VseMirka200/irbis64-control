from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    """Return the source checkout root when the application is not frozen."""
    return Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """Return the directory containing bundled resources or source assets."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else project_root()


def resource_path(*parts: str) -> str:
    return str(runtime_root().joinpath(*parts))
