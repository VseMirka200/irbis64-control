"""Backward-compatible alias for the IRBIS infrastructure adapter."""

import sys

from irbis_control.infrastructure import irbis_bridge as _implementation

sys.modules[__name__] = _implementation
