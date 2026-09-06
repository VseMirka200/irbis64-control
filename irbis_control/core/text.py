from typing import Any


# Превращает значение ячейки в текст: пустая ячейка даёт пустую строку, целое число не получает суффикс .0.
def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
