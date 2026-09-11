"""Analyzer package — extracted from telemetry_analyzer.py monolith.

Re-exports :class:`TelemetryAnalyzer` via lazy ``__getattr__`` (PEP 562)
to avoid the circular-import chain:

    analyzer.__init__ → telemetry_analyzer → analyzer.ai_prompt → …
"""

from typing import Any

__all__ = ["TelemetryAnalyzer"]


def __getattr__(name: str) -> Any:
    """Lazily resolve ``TelemetryAnalyzer``."""
    if name == "TelemetryAnalyzer":
        from src.core.telemetry_analyzer import TelemetryAnalyzer  # noqa: PLC0415

        return TelemetryAnalyzer
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
