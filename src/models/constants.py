"""Compatibility shim — implementation lives in acevo-sdk (``acevo.constants``).

Kept so existing ``src.models.constants`` import paths keep working while
callers migrate to ``acevo.*``.
"""

import sys

from acevo import constants as _impl

sys.modules[__name__] = _impl
