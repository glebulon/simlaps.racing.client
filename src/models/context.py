"""Compatibility shim — implementation lives in acevo-sdk (``acevo.context``).

Kept so existing ``src.models.context`` import paths keep working while
callers migrate to ``acevo.*``.
"""

import sys

from acevo import context as _impl

sys.modules[__name__] = _impl
