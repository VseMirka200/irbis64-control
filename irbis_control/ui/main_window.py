"""Обратимо совместимый импорт главного окна."""

import sys

from irbis_control.ui.windows import main as _implementation

sys.modules[__name__] = _implementation
