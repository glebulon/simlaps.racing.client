"""Compatibility shim — implementation lives in acevo-sdk (``acevo.capture``).

Two pieces of client behaviour are preserved on top of the SDK module:

* ``GameProcessStatus``/``is_game_running`` are re-exported from
  ``src.core.security`` for callers that historically imported them here.
* ``TelemetryCapture`` defaults ``game_running_callback`` to the
  module-level ``is_game_running`` check (resolved at call time), matching
  the pre-SDK unconditional check and keeping ``patch(
  "src.core.telemetry_capture.is_game_running", ...)`` test seams alive.
"""

import sys

from acevo import capture as _impl

from src.core.security import GameProcessStatus, is_game_running

_impl.GameProcessStatus = GameProcessStatus
_impl.is_game_running = is_game_running


def _default_game_running() -> bool:
    return _impl.is_game_running() == GameProcessStatus.RUNNING


class _CompatTelemetryCapture(_impl.TelemetryCapture):
    """TelemetryCapture that keeps the old implicit game-process check."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("game_running_callback", _default_game_running)
        super().__init__(*args, **kwargs)


_impl.TelemetryCapture = _CompatTelemetryCapture

sys.modules[__name__] = _impl
