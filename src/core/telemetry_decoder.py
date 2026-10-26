"""Compatibility shim — implementation lives in acevo-sdk (``acevo.decoder``).

Kept so existing ``src.core.telemetry_decoder`` import paths, PyInstaller
hidden imports, and tests (including private offset constants) keep
working while callers migrate to ``acevo.*``.
"""

import sys

from acevo import decoder as _impl

sys.modules[__name__] = _impl
