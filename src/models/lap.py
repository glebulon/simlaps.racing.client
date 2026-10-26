"""Compatibility shim — implementation lives in acevo-sdk (``acevo.models``).

Kept so existing ``src.models.lap`` import paths, PyInstaller hidden
imports, and tests keep working while callers migrate to ``acevo.*``.
"""

import sys

from acevo import models as _impl

sys.modules[__name__] = _impl
