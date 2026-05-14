"""
Debug Logger for SimLaps Client.

Singleton debug logger (disabled in production).
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def _get_debug_log_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent.parent.parent
    return base / "simlaps_debug.log"


class DebugLogger:
    _instance: Optional["DebugLogger"] = None
    _file = None
    _log_path: Optional[Path] = None
    _started: bool = False

    def __new__(cls) -> "DebugLogger":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if DebugLogger._log_path is None:
            DebugLogger._log_path = _get_debug_log_path()
        if not DebugLogger._started:
            self.start()

    def start(self) -> None:
        ENABLE_DEBUG = False  # Flip to True only for internal debugging
        if DebugLogger._started or not ENABLE_DEBUG:
            return
        try:
            DebugLogger._file = open(DebugLogger._log_path, "a", encoding="utf-8")
            DebugLogger._started = True
            self._write_raw(f"\n{'=' * 60}")
            self._write_raw("SimLaps Debug Log Started")
            self._write_raw(f"Log file: {DebugLogger._log_path}")
            self._write_raw(f"{'=' * 60}\n")
        except Exception as exc:
            print(f"Failed to open debug log: {exc}")

    def _write_raw(self, message: str) -> None:
        if DebugLogger._file:
            try:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                DebugLogger._file.write(f"[{ts}] {message}\n")
                DebugLogger._file.flush()
            except (OSError, IOError):
                # Expected: file closed during shutdown or disk full
                # Silently ignore - debug log is non-critical
                pass

    def log(self, message: str) -> None:
        if not DebugLogger._started or not DebugLogger._file:
            return
        self._write_raw(message)

    def close(self) -> None:
        if DebugLogger._file:
            try:
                DebugLogger._file.close()
            except Exception:
                # Expected: file already closed or handle invalid during shutdown
                # Silently ignore - debug log is non-critical, state reset in finally
                pass
            finally:
                DebugLogger._file = None
                DebugLogger._started = False


# Global instance for convenience
_debug = DebugLogger()
