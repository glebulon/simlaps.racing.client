"""In-session lifecycle orchestration for parser callbacks."""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from src.utils.structured_logger import Component, log_debug, log_error, log_info

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
        self._pending_game_stop = False
        self._pending_game_stop_task: Optional[asyncio.Task] = None
        self._game_stop_tasks: set[asyncio.Task] = set()
        self._game_stop_finalizing_tasks: set[asyncio.Task] = set()

    def set_telemetry_capture(self, telemetry_capture: Optional[Any]) -> None:
        """Refresh the capture dependency if Settings recreates it."""
        self._invalidate_pending_game_stop()
        self._telemetry_capture = telemetry_capture
        self._lifecycle_generation += 1

    def _begin_new_session(self) -> None:
        """Invalidate delayed callbacks from the previous session."""
        self._invalidate_pending_game_stop()
        self._lifecycle_generation += 1

    def _invalidate_pending_game_stop(self) -> None:
        """Invalidate a grace worker and cancel it while it is still waiting."""
        self._pending_game_stop = False
        for task in tuple(self._game_stop_tasks):
            if not task.done() and task not in self._game_stop_finalizing_tasks:
                task.cancel()

    def _schedule_game_stop(self, generation: int, capture: Any) -> None:
        """Schedule the delayed stop without blocking parser callbacks."""
        existing = self._pending_game_stop_task
        if existing and not existing.done() and self._pending_game_stop:
            return
        self._pending_game_stop = True
        task = asyncio.create_task(
            self._finish_delayed_game_stop(generation, capture),
            name="simlaps-delayed-game-stop",
        )
        self._pending_game_stop_task = task
        self._game_stop_tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            if self._pending_game_stop_task is done:
                self._pending_game_stop_task = None
            self._game_stop_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log_error(Component.APP, "Delayed game-stop worker failed", error=str(done.exception()))

        task.add_done_callback(finished)

    async def _finish_delayed_game_stop(self, generation: int, capture: Any) -> None:
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return
        if not self._delayed_stop_is_current(generation, capture):
            return
        task = asyncio.current_task()
        if task is not None:
            self._game_stop_finalizing_tasks.add(task)
        try:
            # The ownership check immediately before finalization is required:
            # a replacement session may have arrived during the grace period.
            if self._delayed_stop_is_current(generation, capture):
                await self._stop_capture("session_end")
        finally:
            if task is not None:
                self._game_stop_finalizing_tasks.discard(task)
                if self._pending_game_stop_task is task:
                    self._pending_game_stop = False

    async def cancel_pending_game_stop(self) -> None:
        """Wait for/cancel the grace worker before shutdown finalization."""
        tasks = [task for task in self._game_stop_tasks if not task.done()]
        if not tasks:
            return
        for task in tasks:
            if task not in self._game_stop_finalizing_tasks:
                task.cancel()
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def _await_game_stop_finalization(self) -> None:
        """Serialize a new lifecycle transition behind an active finalizer."""
        tasks = list(self._game_stop_finalizing_tasks)
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    def _delayed_stop_is_current(self, generation: int, capture: Any) -> bool:
        """Return whether a delayed stop still belongs to the active run."""
        return (
            generation == self._lifecycle_generation and capture is self._telemetry_capture and capture.is_capturing()
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
        await self._await_game_stop_finalization()
        # LogParser establishes the replacement owner before this callback.
        # A second manager reset would erase that new metadata and graphics
        # baseline while retaining the old queue under the wrong epoch.
        if not isinstance(self._session_manager.get_active_session_id(), str):
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
            supersedes_pending_stop = self._pending_game_stop
            self._pending_game_stop = False
            self._begin_new_session()
            await self._await_game_stop_finalization()
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
            active_id = self._session_manager.get_active_session_id()
            if not isinstance(active_id, str):
                # Preserve the historical service contract for embedders that
                # do not expose manager ownership yet.
                self._session_manager.reset()
            if (
                self._telemetry_capture
                and self._telemetry_capture.is_capturing()
                and (
                    not supersedes_pending_stop
                    or isinstance(active_id, str)
                )
            ):
                await self._stop_capture("session_rollover", discard=False)
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
        self._session_manager.end_session()
        capture = self._telemetry_capture
        generation = self._lifecycle_generation
        if capture and capture.is_capturing():
            self._schedule_game_stop(generation, capture)
            return
        await self._stop_capture("session_end")
