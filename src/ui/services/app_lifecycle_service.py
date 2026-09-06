"""App lifecycle/shutdown service extracted from SimLapsApp.

Owns app close-path orchestration and keeps all asynchronous cleanup in one
ordered, idempotent path.
"""

import asyncio
import inspect
from typing import TYPE_CHECKING

from src.utils.structured_logger import Component, log_debug, log_exception

if TYPE_CHECKING:
    from ..app import SimLapsApp


class AppLifecycleService:
    """Encapsulates idempotent app cleanup sequencing."""

    def __init__(self) -> None:
        # Keep the actual cleanup work in one task.  Flet can deliver native
        # close, disconnect, and page-close callbacks concurrently, and the
        # callback that arrives first may itself be cancelled while awaiting
        # shutdown.
        self._cleanup_task = None

    async def cleanup(self, *, app: "SimLapsApp") -> None:
        """Run app shutdown once and let every callback await that same task.

        ``asyncio.shield`` keeps the shared task alive if an individual Flet
        callback is cancelled.  A later callback can then await the task and
        still guarantee that producers are stopped before the window is
        destroyed.
        """
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_impl(app),
                name="simlaps-app-cleanup",
            )
        else:
            log_debug(Component.APP, "Cleanup already scheduled; awaiting shared task")

        await asyncio.shield(self._cleanup_task)

    async def _cleanup_impl(self, app: "SimLapsApp") -> None:
        """Perform the ordered shutdown sequence exactly once."""

        # Keep this order deliberate: stop producers first, then finalize any
        # recorded telemetry (including analysis/report generation), close the
        # HTTP client, and only then let the native window be destroyed.
        await self._run_step(
            "monitor shutdown",
            app.stop_monitoring,
        )
        telemetry_stop = getattr(app, "_stop_telemetry_capture", None)
        if telemetry_stop:
            await self._run_step(
                "telemetry shutdown",
                telemetry_stop,
                reason="app_close",
            )

        api_client = getattr(app, "_api_client", None)
        if api_client:
            await self._run_step("API client close", api_client.close)

        page = getattr(app, "page", None)
        window = getattr(page, "window", None) if page else None
        if window:
            await self._run_step("window destroy", window.destroy)

    @staticmethod
    async def _run_step(label: str, callback, **kwargs) -> None:
        """Run one cleanup step and continue if it fails."""
        try:
            result = callback(**kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            log_exception(Component.APP, f"Cleanup failed during {label}", exc)
