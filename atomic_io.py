"""Backward-compatible alias for atomic file operations."""

import sys

from irbis_control.infrastructure import atomic_io as _implementation

sys.modules[__name__] = _implementation
