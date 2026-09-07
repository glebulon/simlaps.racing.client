"""In-session lifecycle orchestration for parser callbacks."""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from src.utils.structured_logger import Component, log_debug, log_info
from ..components.status_bar import ConnectionStatus


StartCaptureCallback = Callable[[], Awaitable[None]]
StopCaptureCallback = Callable[..., Awaitable[None]]


class SessionLifecycleService:
    """Coordinate session boundaries without depending on ``SimLapsApp``."""

    def __init__(
        self,
        *,
        home_page: Optional[Any],
        session_manager: Any,
        telemetry_capture: Optional[Any],
        start_capture: StartCaptureCallback,
        stop_capture: StopCaptureCallback,
    ) -> None:
        self._home_page = home_page
        self._session_manager = session_manager
        self._telemetry_capture = telemetry_capture
        self._start_capture = start_capture
        self._stop_capture = stop_capture
        # Delayed stop callbacks can overlap a later session-start callback.
        # A generation identifies the lifecycle transition that scheduled a
        # delayed stop, while the capture identity prevents a stop for an old
        # capture instance from reaching a replacement created by Settings.
        self._lifecycle_generation = 0

    def set_telemetry_capture(self, telemetry_capture: Optional[Any]) -> None:
        """Refresh the capture dependency if Settings recreates it."""
        self._telemetry_capture = telemetry_capture
        self._lifecycle_generation += 1

    def _begin_new_session(self) -> None:
        """Invalidate delayed callbacks from the previous session."""
        self._lifecycle_generation += 1

    def _delayed_stop_is_current(self, generation: int, capture: Any) -> bool:
        """Return whether a delayed stop still belongs to the active run."""
        return (
            generation == self._lifecycle_generation
            and capture is self._telemetry_capture
            and capture.is_capturing()
        )

    async def handle_car_removed(self) -> None:
        """Stop an active capture after ACE removes the player's car."""
        log_info(Component.APP, "Car removed from session — stopping telemetry capture")
        capture = self._telemetry_capture
        generation = self._lifecycle_generation
        if capture and capture.is_capturing():
            await asyncio.sleep(1.0)
            if self._delayed_stop_is_current(generation, capture):
                await self._stop_capture("car_removed")

    async def handle_session_restart(self) -> None:
        """Discard an aborted run and unconditionally start fresh capture."""
        log_info(Component.APP, "Session restart — discarding telemetry buffer and restarting")
        log_debug(Component.APP, "Session restart detected; restarting telemetry capture")
        self._begin_new_session()
        self._session_manager.reset()
        if self._telemetry_capture and self._telemetry_capture.is_capturing():
            await self._stop_capture("session_restart", discard=True)
        await self._start_capture()

    async def handle_game_status_change(self, is_running: bool) -> None:
        """Update UI and capture state for an ACE session transition."""
        if not self._home_page:
            return

        self._home_page.set_game_running(is_running)
        if is_running:
            self._begin_new_session()
            self._home_page.set_connection_status(
                ConnectionStatus.CONNECTED,
                "Session active - recording laps",
            )
            best_before = self._session_manager.get_best_lap_time()
            all_times_before = self._session_manager.get_all_lap_times()
            log_info(
                Component.APP,
                f"[GAME_STATUS] True — resetting shared session. "
                f"Best lap before reset: {best_before}, "
                f"timing entries: {len(all_times_before)}",
            )
            self._session_manager.reset()
            best_after = self._session_manager.get_best_lap_time()
            all_times_after = self._session_manager.get_all_lap_times()
            log_info(
                Component.APP,
                f"[GAME_STATUS] Reset complete. "
                f"Best lap after reset: {best_after}, "
                f"timing entries: {len(all_times_after)}",
            )
            log_info(Component.APP, "Triggering telemetry capture start (session active)")
            await self._start_capture()
            return

        self._home_page.set_connection_status(
            ConnectionStatus.CONNECTED,
            "Monitoring - waiting for session...",
        )
        capture = self._telemetry_capture
        generation = self._lifecycle_generation
        if capture and capture.is_capturing():
            await asyncio.sleep(2.0)
            if not self._delayed_stop_is_current(generation, capture):
                return
        await self._stop_capture("session_end")
