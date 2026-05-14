"""Monitoring service extracted from SimLapsApp.

Owns parser/game-monitor background task lifecycle and status transitions.
"""

import asyncio
import os
import re
from typing import Awaitable, Callable, Optional, Any

from src.core.security import is_game_running, GameProcessStatus
from src.utils.structured_logger import log_info, Component
from ..components.status_bar import ConnectionStatus


class MonitoringService:
    """Encapsulates log monitoring lifecycle for the UI app."""

    def __init__(self, page: Any):
        self._page = page
        self._parser_task: Optional[asyncio.Task] = None
        self._game_monitor_task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        return self._parser_task is not None and not self._parser_task.done()

    async def start(
        self,
        *,
        log_parser: Any,
        home_page: Any,
        log_path: str,
        on_game_status_change: Callable[[bool], Awaitable[None]],
        is_telemetry_capturing: Callable[[], bool],
    ) -> None:
        """Start parser + game monitor tasks if not already running."""
        if self.is_active:
            return

        game_version = self._get_game_version_from_log(log_path)
        if game_version:
            home_page.set_game_version(game_version)

        home_page.set_game_running(False)
        home_page.set_connection_status(
            ConnectionStatus.CONNECTED,
            "Monitoring log file...",
        )

        self._parser_task = self._page.run_task(self._run_parser, log_parser, home_page)
        self._game_monitor_task = self._page.run_task(
            self._run_game_monitor,
            on_game_status_change,
            is_telemetry_capturing,
        )

    def stop(self, *, log_parser: Any, home_page: Any) -> None:
        """Stop parser + game monitor tasks and update UI status."""
        if log_parser:
            log_parser.stop()

        if self._parser_task:
            self._parser_task.cancel()
            self._parser_task = None

        if self._game_monitor_task:
            self._game_monitor_task.cancel()
            self._game_monitor_task = None

        home_page.set_connection_status(
            ConnectionStatus.DISCONNECTED,
            "Monitoring stopped",
        )

    async def _run_game_monitor(
        self,
        on_game_status_change: Callable[[bool], Awaitable[None]],
        is_telemetry_capturing: Callable[[], bool],
    ) -> None:
        """Poll process state and emit session-end transition when needed."""
        poll_interval = 5.0
        try:
            while True:
                await asyncio.sleep(poll_interval)
                if is_telemetry_capturing() and is_game_running() != GameProcessStatus.RUNNING:
                    log_info(Component.APP, "Game process gone (monitor) - stopping telemetry")
                    await on_game_status_change(False)
                    break
        except asyncio.CancelledError:
            pass

    async def _run_parser(self, log_parser: Any, home_page: Any) -> None:
        """Run parser task and project errors to UI status."""
        try:
            await log_parser.follow()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            home_page.set_connection_status(
                ConnectionStatus.ERROR,
                f"Error: {str(exc)}",
            )

    @staticmethod
    def _get_game_version_from_log(log_path: str) -> Optional[str]:
        """Read game version from the first few lines of the configured log file."""
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                    for _ in range(10):
                        line = file_obj.readline()
                        if not line:
                            break
                        if "Build release" in line:
                            match = re.search(r"Build release ([^,]+),", line)
                            if match:
                                return match.group(1)
        except Exception:
            pass
        return None
