"""Backward-compatible alias for Qt locale helpers."""

import sys

from irbis_control.ui import locale as _implementation

sys.modules[__name__] = _implementation
