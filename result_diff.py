"""Backward-compatible alias for the report comparison service."""

import sys

from irbis_control.reporting import result_diff as _implementation

sys.modules[__name__] = _implementation
