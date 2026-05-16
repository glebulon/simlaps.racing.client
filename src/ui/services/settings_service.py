"""Settings apply/reconfigure service extracted from SimLapsApp.

Owns apply-settings orchestration: runtime service refresh, telemetry toggles,
and parser restart behavior.
"""

from typing import Any, Callable

from src.utils.structured_logger import Component, log_info


class SettingsService:
    """Encapsulates settings persistence and runtime reconfiguration flow."""

    def apply(
        self,
        *,
        app: Any,
        config: Any,
        create_discord_notifier: Callable[[str], Any],
        get_pb_cache_for_server: Callable[[str], Any],
        create_api_client: Callable[..., Any],
        create_log_parser: Callable[[str], Any],
    ) -> None:
        """Apply new config and reconcile dependent runtime services."""
        app._config = config
        app._config_manager.save()

        # Update Discord notifier
        if config.discord_webhook_url and config.discord_enabled:
            app._discord_notifier = create_discord_notifier(config.discord_webhook_url)
        else:
            app._discord_notifier = None

        # Update PB cache if server URL changed
        if app._pb_cache.server_url != config.server_url:
            app._pb_cache = get_pb_cache_for_server(config.server_url)

        # Update API client
        app._api_client = create_api_client(
            server_url=config.server_url,
            session_manager=app._session_manager,
        )

        # Update services with new settings
        app._api_client.set_server_url(config.server_url)

        # Re-initialize telemetry if settings changed
        if config.telemetry_enabled and not app._telemetry_capture:
            log_info(Component.APP, "Telemetry enabled - initializing services")
            app._init_telemetry_services()
            app._attach_telemetry_ui()
            if app._telemetry_capture:
                app.page.run_task(app._start_telemetry_capture)
        elif not config.telemetry_enabled and app._telemetry_capture:
            log_info(Component.APP, "Telemetry disabled - stopping services")
            if app._telemetry_capture.is_capturing():
                app.page.run_task(app._telemetry_capture.stop_capture, "disabled")
            app._telemetry_capture = None
            app._telemetry_analyzer = None
            # Remove button from home page
            if app._home_page:
                app._home_page.set_telemetry_button(None, "")
            app._telemetry_button = None

        # Restart parser if log path changed
        was_running = app._log_parser.is_running if app._log_parser else False

        if was_running:
            app.stop_monitoring()

        app._log_parser = create_log_parser(config.log_path)

        if was_running:
            app.page.run_task(app.start_monitoring)

        # Update home page
        app._home_page.update_config(app._config)
