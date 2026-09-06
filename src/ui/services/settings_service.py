"""Settings apply/reconfigure service extracted from SimLapsApp.

Owns apply-settings orchestration: runtime service refresh, telemetry toggles,
and parser restart behavior.
"""

from typing import Callable, TYPE_CHECKING

from src.core.analyzer import TelemetryAnalyzer
from src.core.discord_notifier import DiscordNotifier
from src.core.track_catalog import TRACK_CATALOG
from src.utils.structured_logger import Component, log_info
from src.utils.config import AppConfig
from ..components.telemetry_status import TelemetryButton

if TYPE_CHECKING:
    from ..app import SimLapsApp
    from src.core.discord_notifier import DiscordNotifier
    from src.core.pb_cache import PBCache
    from src.core.api_client import APIClient
    from src.core.log_parser import LogParser


class SettingsService:
    """Encapsulates settings persistence and runtime reconfiguration flow."""

    def apply(
        self,
        *,
        app: "SimLapsApp",
        config: AppConfig,
        create_discord_notifier: "Callable[[str], DiscordNotifier]",
        get_pb_cache_for_server: "Callable[[str], PBCache]",
        create_api_client: "Callable[..., APIClient]",
        create_log_parser: "Callable[[str], LogParser]",
    ) -> None:
        """Apply new config and reconcile dependent runtime services."""
        previous = app._config

        # Validate the enabled webhook before constructing or persisting any
        # replacement state. Disabled integrations may retain an old value,
        # but enabling Discord must never save a URL the notifier would reject.
        if config.discord_enabled and not DiscordNotifier.validate_webhook_url(
            config.discord_webhook_url
        ):
            raise ValueError("Invalid Discord webhook URL")

        server_changed = previous.server_url != config.server_url
        log_path_changed = previous.log_path != config.log_path
        telemetry_output_changed = (
            previous.telemetry_output_path != config.telemetry_output_path
        )
        telemetry_debug_changed = (
            previous.telemetry_debug_logs != config.telemetry_debug_logs
        )

        # Construct every replacement before mutating live application state.
        # This prevents a bad import or constructor from leaving Settings half
        # applied and persisted, which was the cause of the telemetry checkbox
        # crash reported by users.
        current_discord = getattr(app, "_discord_notifier", None)
        discord_changed = (
            previous.discord_enabled != config.discord_enabled
            or previous.discord_webhook_url != config.discord_webhook_url
        )
        if config.discord_webhook_url and config.discord_enabled:
            staged_discord = (
                create_discord_notifier(config.discord_webhook_url)
                if discord_changed or current_discord is None
                else current_discord
            )
        else:
            staged_discord = None

        staged_pb_cache = app._pb_cache
        if server_changed:
            staged_pb_cache = get_pb_cache_for_server(config.server_url)

        current_api_client = getattr(app, "_api_client", None)
        staged_api_client = current_api_client
        if current_api_client is None or server_changed:
            staged_api_client = create_api_client(
                server_url=config.server_url,
                session_manager=app._session_manager,
            )

        current_parser = getattr(app, "_log_parser", None)
        staged_parser = current_parser
        if current_parser is None or log_path_changed:
            staged_parser = create_log_parser(config.log_path)

        staged_analyzer = getattr(app, "_telemetry_analyzer", None)
        staged_button = getattr(app, "_telemetry_button", None)
        if config.telemetry_enabled:
            if staged_analyzer is None or telemetry_output_changed:
                staged_analyzer = TelemetryAnalyzer(
                    output_dir=config.telemetry_output_path,
                    track_catalog=TRACK_CATALOG,
                    session_manager=app._session_manager,
                )
            if staged_button is None:
                staged_button = TelemetryButton(
                    on_click=getattr(app, "_open_telemetry_location", None),
                    output_path=config.telemetry_output_path,
                )

        # Persist only after all required imports and constructors succeeded.
        if app._config_manager.set(config) is False:
            raise OSError("Could not save application settings")

        app._config = config
        was_running = bool(current_parser and current_parser.is_running)
        if log_path_changed and was_running:
            app.stop_monitoring()

        app._discord_notifier = staged_discord
        app._pb_cache = staged_pb_cache
        app._api_client = staged_api_client
        app._log_parser = staged_parser

        if current_api_client is not None and current_api_client is not staged_api_client:
            app.page.run_task(current_api_client.close)

        # Reconcile telemetry capture mode with the new setting.
        # The capture loop always runs (needed for SHM lap validity), but
        # frame recording and analysis are gated on telemetry_enabled.
        if config.telemetry_enabled and not app._telemetry_capture:
            # First time enabling — full init (should not happen now that
            # _init_telemetry_services always creates the capture, but
            # keep as a safety net).
            log_info(Component.APP, "Telemetry enabled - initializing services")
            app._init_telemetry_services()
            app._attach_telemetry_ui()
            if app._telemetry_capture:
                app.page.run_task(app._start_telemetry_capture)
        elif config.telemetry_enabled and app._telemetry_capture:
            # Telemetry was already enabled or was in validity-only mode;
            # switch to full recording and ensure analyzer/button exist.
            if not app._telemetry_capture.record_frames:
                app._telemetry_capture.set_record_frames(True)
                log_info(Component.APP, "Telemetry recording enabled")
            if telemetry_output_changed or telemetry_debug_changed:
                app._telemetry_capture.configure(
                    output_dir=config.telemetry_output_path,
                    debug_logs=config.telemetry_debug_logs,
                )
            app._telemetry_analyzer = staged_analyzer
            app._telemetry_button = staged_button
            assert app._telemetry_button is not None
            app._telemetry_button.update_path(config.telemetry_output_path)
            app._attach_telemetry_ui()
        elif not config.telemetry_enabled and app._telemetry_capture:
            # User disabled telemetry recording — switch to validity-only
            # mode.  The capture loop stays alive so SHM validity data
            # continues to flow to the shared session.
            if app._telemetry_capture.record_frames:
                app._telemetry_capture.set_record_frames(False)
                log_info(Component.APP, "Telemetry recording disabled — validity-only mode active")
            if telemetry_output_changed or telemetry_debug_changed:
                app._telemetry_capture.configure(
                    output_dir=config.telemetry_output_path,
                    debug_logs=config.telemetry_debug_logs,
                )
            # Drop analyzer and UI button (no frames to analyze).
            app._telemetry_analyzer = None
            if app._home_page:
                app._home_page.set_telemetry_button(None, "")
            app._telemetry_button = None
        # Restart the parser only when its input path actually changed.
        if log_path_changed and was_running:
            app.page.run_task(app.start_monitoring)

        # Update home page
        if app._home_page:
            app._home_page.update_config(app._config)

        if previous.window_width != config.window_width:
            app.page.width = config.window_width
        if previous.window_height != config.window_height:
            app.page.height = config.window_height
