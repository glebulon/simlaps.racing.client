"""Compatibility shim — implementation lives in acevo-sdk (``acevo.catalogs.tracks``).

Kept so existing ``src.core.track_catalog`` import paths, PyInstaller
hidden imports, and tests keep working while callers migrate to
``acevo.catalogs.*``.
"""

import sys

from acevo.catalogs import tracks as _impl

sys.modules[__name__] = _impl
