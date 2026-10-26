"""Compatibility shim — implementation lives in acevo-sdk (``acevo.catalogs.cars``).

Kept so existing ``src.core.car_tuning_catalog`` import paths keep
working while callers migrate to ``acevo.catalogs.*``.
"""

import sys

from acevo.catalogs import cars as _impl

sys.modules[__name__] = _impl
