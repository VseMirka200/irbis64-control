from __future__ import annotations

import warnings
from pathlib import Path


# Открывает книгу по пути с указанными параметрами openpyxl и возвращает её вызывающему коду.
# У некоторых выгрузок нет стандартного стиля. Подавляем только это предупреждение.
# Закрыть книгу после чтения должен вызывающий код.
def load_workbook_quiet(path: str | Path, **kwargs):
    from openpyxl import load_workbook

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Workbook contains no default style, apply openpyxl's default",
            category=UserWarning,
            module=r"openpyxl\.styles\.stylesheet",
        )
        return load_workbook(path, **kwargs)
