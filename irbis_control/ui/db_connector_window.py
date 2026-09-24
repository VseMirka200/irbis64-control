"""Обратимо совместимый импорт окна подключения к базе данных."""

import sys

from irbis_control.ui.windows import database_connector as _implementation

sys.modules[__name__] = _implementation
