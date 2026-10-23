"""Compatibility shim — implementation lives in acevo-sdk (``acevo.shared_session``).

Kept so existing ``src.models.shared_session`` import paths keep working
while callers migrate to ``acevo.*``.
"""

import sys

from acevo import shared_session as _impl

sys.modules[__name__] = _impl
