"""App lifecycle/shutdown service extracted from SimLapsApp.

Owns app close-path orchestration: telemetry stop trigger, monitor shutdown,
and API client closure scheduling.
"""

from typing import Any

from src.utils.structured_logger import Component, log_debug, log_info


class AppLifecycleService:
    """Encapsulates idempotent app cleanup sequencing."""

    def __init__(self) -> None:
        self._cleanup_started = False

    def cleanup(self, *, app: Any) -> None:
        """Run app shutdown sequence once in a safe, repeatable manner."""
        if self._cleanup_started:
            log_debug(Component.APP, "Cleanup already executed; skipping duplicate call")
            return

        self._cleanup_started = True

        if app._telemetry_capture and app._telemetry_capture.is_capturing():
            log_info(Component.APP, "Cleanup: stopping active telemetry capture")
            if app.page:
                app.page.run_task(app._telemetry_capture.stop_capture, "app_close")

        app.stop_monitoring()

        if app._api_client and app.page:
            app.page.run_task(app._api_client.close)
