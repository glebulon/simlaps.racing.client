"""Compatibility shim — implementation lives in acevo-sdk (``acevo.logs``).

Kept so existing ``src.core.log_parser`` import paths, PyInstaller
hidden imports, and tests keep working while callers migrate to
``acevo.*``.
"""

import sys

from acevo import logs as _impl

sys.modules[__name__] = _impl
