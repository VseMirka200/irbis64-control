"""Backward-compatible alias for :mod:`irbis_control.core.matcher`."""

import sys

from irbis_control.core import matcher as _implementation

sys.modules[__name__] = _implementation
