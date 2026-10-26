"""Compatibility shim — implementation lives in acevo-sdk (``acevo.tyre_state``).

Kept so existing ``src.models.tyre_state`` import paths keep working while
callers migrate to ``acevo.*``.
"""

import sys

from acevo import tyre_state as _impl

sys.modules[__name__] = _impl
