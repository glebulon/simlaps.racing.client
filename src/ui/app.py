"""
Main Application UI - Coordinates all pages and core functionality.

Simplified: No authentication required. Uses signed payloads and
detects user from game logs automatically.
"""

import flet as ft
import asyncio
import os
import sys
from typing import Optional
from enum import Enum

from .pages.home import HomePage
from .pages.settings import SettingsPage
from .components.pb_cache_viewer import show_pb_cache_dialog
from .pages.history import HistoryPage, HistoryEntry
from .components.lap_card import LapCard, LapCardStatus
from .components.status_bar import ConnectionStatus
from .components.telemetry_status import TelemetryButton
from .services.app_lifecycle_service import AppLifecycleService
from .services.lap_processing_service import LapProcessingService
from .services.lap_submission_service import LapSubmissionService
from .services.monitoring_service import MonitoringService
from .services.settings_service import SettingsService
from .services.telemetry_lifecycle_service import TelemetryLifecycleService
from .services.user_bootstrap_service import UserBootstrapService
from src.core.log_parser import LogParser
from src.models import SessionData, LapData, SharedSessionManager
from src.core.api_client import APIClient
from src.core.security import get_steam_user
from src.core.discord_notifier import DiscordNotifier
from src.core.pb_cache import get_pb_cache
from src.core.telemetry_capture import TelemetryCapture
from src.core.track_catalog import TRACK_CATALOG
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.utils.structured_logger import log_debug, log_info, log_warning, log_exception, Component
from src.utils.config import AppConfig, get_config_manager


class AppPage(Enum):
    """Application pages."""
    HOME = "home"
    SETTINGS = "settings"
    HISTORY = "history"


class SimLapsApp:
    """
    Main application controller.
    
    No authentication required - uses signed payloads with embedded secret.
    User identity is detected from game logs (Steam ID).
    """
    
    def __init__(self, page: ft.Page):
        self.page = page
        log_info(Component.APP, "Initializing SimLapsApp")
        self._setup_page()
        
        # Store app instance reference for components
        page._app_instance = self
        
        # Core services
        log_info(Component.APP, "Loading configuration")
        self._config_manager = get_config_manager()
        self._config = self._config_manager.load()
        log_info(Component.APP, "Configuration loaded", server=self._config.server_url)
        
        self._api_client: Optional[APIClient] = None
        self._log_parser: Optional[LogParser] = None
        self._session_manager = SharedSessionManager()
        
        # Discord and PB services
        log_info(Component.APP, "Initializing Discord and PB services")
        self._discord_notifier: Optional[DiscordNotifier] = None
        self._pb_cache = get_pb_cache(self._config.server_url)
        log_info(Component.APP, "PB cache initialized", initialized=self._pb_cache is not None)
        
        # Monitoring lifecycle service
        self._monitoring_service = MonitoringService(self.page)
        self._app_lifecycle_service = AppLifecycleService()
        self._lap_processing_service = LapProcessingService()
        self._settings_service = SettingsService()
        self._telemetry_lifecycle_service = TelemetryLifecycleService()
        self._lap_submission_service = LapSubmissionService()
        self._user_bootstrap_service = UserBootstrapService()
        
        # Telemetry services
        self._telemetry_capture: Optional[TelemetryCapture] = None
        self._telemetry_analyzer: Optional[TelemetryAnalyzer] = None
        self._telemetry_button: Optional[TelemetryButton] = None
        self._current_track_name: Optional[str] = None
        
        # Pages
        log_debug(Component.APP, "Initializing UI pages")
        self._home_page: Optional[HomePage] = None
        self._settings_page: Optional[SettingsPage] = None
        self._history_page: Optional[HistoryPage] = None
        self._current_page = AppPage.HOME
        
        # History tracking
        log_debug(Component.APP, "Setting up history tracking")
        self._history_entries: list[HistoryEntry] = []
        
        # Initialize
        log_info(Component.APP, "Starting initialization")
        self._init_services()
        self._init_pages()
        self._attach_telemetry_ui()
        self._show_page(AppPage.HOME)
        log_info(Component.APP, "Initialization complete")
    
    def _setup_page(self):
        """Configure Flet page."""
        log_debug(Component.APP, "Setting up Flet page")
        try:
            self.page.title = "SimLaps Telemetry"
            self.page.width = 500
            self.page.height = 700
            self.page.bgcolor = "#0f0f1a"
            self.page.padding = 0
            self.page.spacing = 0
            log_debug(Component.APP, "Flet page properties set")
        except Exception as e:
            log_exception(Component.APP, "Error setting up Flet page", e)
        
        # Set window close handler
        log_debug(Component.APP, "Setting up window close handler")
        try:
            self.page.on_close = self._on_window_close
            log_debug(Component.APP, "Window close handler set")
        except Exception as e:
            log_exception(Component.APP, "Error setting window close handler", e)
        
        # Set window icon
        log_debug(Component.APP, "Setting up window icon")
        try:
            icon_path = self._get_icon_path()
            if icon_path:
                self.page.window.icon = icon_path
                log_debug(Component.APP, "Window icon set", icon_path=icon_path)
            else:
                log_debug(Component.APP, "No icon file found")
        except Exception as e:
            log_exception(Component.APP, "Error setting window icon", e)
        
        # Dark theme
        log_debug(Component.APP, "Setting up dark theme")
        try:
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.theme = ft.Theme(
                color_scheme_seed="#7c3aed",
            )
            log_debug(Component.APP, "Dark theme applied")
        except Exception as e:
            log_exception(Component.APP, "Error setting up theme", e)
        
        log_info(Component.APP, "Flet page setup complete")
    
    def _get_icon_path(self) -> Optional[str]:
        """Get the path to the app icon (ICO for window icon)."""
        if getattr(sys, 'frozen', False):
            # Running as compiled executable - check _MEIPASS for bundled files
            if hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(sys._MEIPASS, "assets", "icon.ico")
                if os.path.exists(icon_path):
                    return icon_path
            # Fallback to executable directory
            base_path = os.path.dirname(sys.executable)
        else:
            # Running as script - go up from src/ui to project root
            base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Try assets/icon.ico
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            return icon_path
        
        return None
    
    def _init_services(self):
        """Initialize core services."""
        # API client (no API key needed - uses signed payloads)
        self._api_client = APIClient(
            server_url=self._config.server_url,
            session_manager=self._session_manager,
        )
        
        # Log parser with callbacks
        self._log_parser = self._create_log_parser(self._config.log_path)
        
        # Initialize telemetry if enabled
        self._init_telemetry_services()

    def _create_log_parser(self, log_path: str) -> LogParser:
        """Create a log parser wired to current app callbacks."""
        return LogParser(
            log_path=log_path,
            on_lap_complete=self._on_lap_complete,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
            on_session_end=self._on_car_removed,
            on_session_restart=self._on_session_restart,
            session_manager=self._session_manager,
        )
    
    def _init_telemetry_services(self):
        """Initialize telemetry capture and analyzer services."""
        if not self._config.telemetry_enabled:
            log_debug(Component.APP, "Telemetry disabled in settings")
            return
        
        try:
            self._telemetry_capture = TelemetryCapture(
                hz=10.0,
                output_dir=self._config.telemetry_output_path,
                debug_logs=self._config.telemetry_debug_logs,
                session_manager=self._session_manager,
            )
            # Set up auto-stop callback to trigger analysis
            self._telemetry_capture.set_on_stop_callback(self._on_telemetry_auto_stop)
            self._telemetry_analyzer = TelemetryAnalyzer(
                output_dir=self._config.telemetry_output_path,
                track_catalog=TRACK_CATALOG,
                session_manager=self._session_manager,
            )
            
            # Create telemetry button
            log_debug(Component.APP, "Creating TelemetryButton", callback=self._open_telemetry_location)
            self._telemetry_button = TelemetryButton(
                on_click=self._open_telemetry_location,
                output_path=self._config.telemetry_output_path,
            )
            log_debug(Component.APP, "TelemetryButton created", callback=self._telemetry_button.on_click)
            
            # Set button on home page
            if self._home_page:
                log_debug(Component.APP, "Home page exists, attaching telemetry button")
                self._home_page.set_telemetry_button(
                    self._telemetry_button,
                    self._config.telemetry_output_path,
                )
            else:
                log_debug(Component.APP, "Home page not initialized yet; telemetry UI attach deferred")
            
            log_info(
                Component.APP,
                "Telemetry services initialized",
                output=self._config.telemetry_output_path,
            )
        except Exception as e:
            log_exception(Component.APP, "Failed to initialize telemetry", e)
            self._telemetry_capture = None
            self._telemetry_analyzer = None

    def _attach_telemetry_ui(self):
        """Attach telemetry UI controls after the home page exists."""
        log_debug(
            Component.APP,
            "Attach telemetry UI requested",
            home_page_exists=self._home_page is not None,
            button_exists=self._telemetry_button is not None,
        )
        if self._telemetry_button:
            log_debug(Component.APP, "Telemetry button callback", callback=self._telemetry_button.on_click)
        if self._home_page and self._telemetry_button:
            log_debug(Component.APP, "Attaching telemetry button to home page")
            self._home_page.set_telemetry_button(
                self._telemetry_button,
                self._config.telemetry_output_path,
            )
        else:
            log_debug(Component.APP, "Skipped telemetry button attach; missing home_page or button")

    def _open_telemetry_location(self, e, output_path):
        """Open the telemetry output folder in file explorer."""
        import subprocess
        import os
        
        log_debug(Component.APP, "Open telemetry location requested", output_path=output_path)
        
        try:
            if not output_path:
                log_warning(Component.APP, "No telemetry output path configured")
                if self.page:
                    self.page.snack_bar = ft.SnackBar(
                        content=ft.Text("Telemetry output path not configured"),
                        bgcolor="#dc2626",
                    )
                    self.page.snack_bar.open = True
                    self.page.update()
                return
            
            # Create directory if it doesn't exist
            os.makedirs(output_path, exist_ok=True)
            
            # Verify directory exists
            if not os.path.exists(output_path):
                raise FileNotFoundError(f"Directory does not exist: {output_path}")
            
            log_debug(
                Component.APP,
                "Opening telemetry location",
                output_path=output_path,
                exists=os.path.exists(output_path),
                is_directory=os.path.isdir(output_path),
            )
            
            if sys.platform == "win32":
                # Use os.startfile which is more reliable for opening folders on Windows
                os.startfile(output_path)
                log_debug(Component.APP, "Opened telemetry directory via os.startfile")
            else:
                subprocess.Popen(["open", output_path])
                log_debug(Component.APP, "Opened telemetry directory via subprocess")
        except Exception as ex:
            log_exception(Component.APP, "Failed to open telemetry location", ex, output_path=output_path)
            if self.page:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Failed to open folder: {ex}"),
                    bgcolor="#dc2626",
                )
                self.page.snack_bar.open = True
                self.page.update()
    
    def _init_pages(self):
        """Initialize page components."""
        self._home_page = HomePage(
            config=self._config,
            on_settings_click=lambda: self._show_page(AppPage.SETTINGS),
            on_history_click=lambda: self._show_page(AppPage.HISTORY),
            on_pb_cache_click=self._show_pb_cache_viewer,
            on_retry_lap=self._on_retry_lap,
        )
        
        self._settings_page = SettingsPage(
            config=self._config,
            on_back=lambda: self._show_page(AppPage.HOME),
            on_save=self._save_settings,
            on_test_connection=self._test_connection,
            on_test_discord=self._test_discord_webhook,
        )
        
        self._history_page = HistoryPage(
            on_back=lambda: self._show_page(AppPage.HOME),
        )
    
    def _show_page(self, page: AppPage):
        """Navigate to a page."""
        self._current_page = page
        
        # Clear existing controls
        self.page.clean()
        
        if page == AppPage.HOME:
            self.page.add(self._home_page)
        elif page == AppPage.SETTINGS:
            self.page.add(self._settings_page)
        elif page == AppPage.HISTORY:
            self._history_page.set_entries(self._history_entries)
            self.page.add(self._history_page)

    def _get_history_entry_for_lap_number(self, lap_number: int) -> Optional[HistoryEntry]:
        """Resolve a history entry from a lap card's absolute lap number."""
        index = lap_number - 1
        if 0 <= index < len(self._history_entries):
            return self._history_entries[index]
        return None

    def _get_or_create_service(self, attr_name: str, factory):
        """Get a lazily initialized service instance (supports __new__-based tests)."""
        service = getattr(self, attr_name, None)
        if service is None:
            service = factory()
            setattr(self, attr_name, service)
        return service

    def _on_retry_lap(self, card: LapCard):
        """Retry submission for a failed lap card."""
        if not card.data.lap.is_valid and not self._config.submit_invalid_laps:
            return

        history_entry = self._get_history_entry_for_lap_number(card.data.lap_number)
        if history_entry is None:
            card.update_status(LapCardStatus.FAILED, "Retry unavailable: history entry missing")
            return

        self.page.run_task(
            self._submit_lap,
            card,
            card.data.session,
            card.data.lap,
            history_entry,
        )

    async def _on_lap_complete(self, session: SessionData, lap: LapData):
        """Handle completed lap from parser."""
        log_debug(
            Component.APP,
            "Lap complete event",
            lap_time=lap.lap_time_str,
            track=session.track,
            lap_number=lap.lap_number,
        )
        try:
            processing_service = self._get_or_create_service(
                "_lap_processing_service",
                LapProcessingService,
            )

            await processing_service.handle_lap_complete(
                app=self,
                session=session,
                lap=lap,
                create_history_entry=HistoryEntry,
            )
        except Exception as e:
            log_exception(Component.APP, "_on_lap_complete failed", e)
    
    async def _submit_lap(
        self,
        card,
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
        pb_was_new: Optional[bool] = None,
    ):
        """Submit a lap to the server."""
        submission_service = self._get_or_create_service(
            "_lap_submission_service",
            LapSubmissionService,
        )

        await submission_service.submit_lap(
            api_client=self._api_client,
            config=self._config,
            card=card,
            session=session,
            lap=lap,
            history_entry=history_entry,
            pb_was_new=pb_was_new,
            post_to_discord=self._post_to_discord,
        )
    
    async def _post_to_discord(
        self,
        session: SessionData,
        lap: LapData,
        steam_id: str,
        steam_name: Optional[str] = None,
        pb_was_new: Optional[bool] = None,
    ):
        """Post lap to Discord if configured and meets criteria."""
        submission_service = self._get_or_create_service(
            "_lap_submission_service",
            LapSubmissionService,
        )

        await submission_service.post_to_discord(
            config=self._config,
            discord_notifier=self._discord_notifier,
            session=session,
            lap=lap,
            steam_id=steam_id,
            steam_name=steam_name,
            pb_was_new=pb_was_new,
        )
    
    async def _on_parser_status(self, status: str):
        """Handle status update from parser."""
        if self._home_page:
            self._home_page.set_status(status)
    
    async def _on_car_removed(self):
        """Handle player car removal — authoritative session-end signal from game log."""
        log_info(Component.APP, "Car removed from session — stopping telemetry capture")
        if self._telemetry_capture and self._telemetry_capture.is_capturing():
            await asyncio.sleep(1.0)  # Brief pause to capture any final frames
            await self._stop_telemetry_capture("car_removed")

    async def _on_session_restart(self):
        """Handle pause-menu Restart Session.

        AC Evo restarts the same session in place without emitting a fresh
        ``Game Started!`` line, so any telemetry buffer accumulated during
        the aborted run would otherwise contaminate the restarted run's
        analysis (and pollute fuel-per-lap deltas). Drop the buffer and
        immediately spin up a fresh capture so the first lap of the
        restarted session is fully recorded.
        """
        log_info(Component.APP, "Session restart — discarding telemetry buffer and restarting")
        log_debug(Component.APP, "Session restart detected; restarting telemetry capture")
        self._session_manager.reset()
        if self._telemetry_capture and self._telemetry_capture.is_capturing():
            await self._stop_telemetry_capture("session_restart", discard=True)
        await self._start_telemetry_capture()

    async def _on_game_status_change(self, is_running: bool):
        """Handle game running status change."""
        if self._home_page:
            self._home_page.set_game_running(is_running)
            
            if is_running:
                self._home_page.set_connection_status(
                    ConnectionStatus.CONNECTED,
                    "Session active - recording laps",
                )
                # Clear stale lap validity / timing data from the previous session.
                self._session_manager.reset()
                # Start telemetry capture
                log_info(Component.APP, "Triggering telemetry capture start (session active)")
                await self._start_telemetry_capture()
            else:
                # Still connected/monitoring, just no active session
                self._home_page.set_connection_status(
                    ConnectionStatus.CONNECTED,
                    "Monitoring - waiting for session...",
                )
                # Stop telemetry capture and analyze
                # Add a small delay to allow final frames to be captured
                if self._telemetry_capture and self._telemetry_capture.is_capturing():
                    await asyncio.sleep(2.0)
                await self._stop_telemetry_capture("session_end")
    
    async def _start_telemetry_capture(self):
        """Start telemetry capture when game session begins."""
        lifecycle_service = self._get_or_create_service(
            "_telemetry_lifecycle_service",
            TelemetryLifecycleService,
        )

        await lifecycle_service.start_capture(
            telemetry_capture=self._telemetry_capture,
            home_page=self._home_page,
            telemetry_enabled=self._config.telemetry_enabled,
        )
    
    async def _on_telemetry_auto_stop(self, reason: str):
        """Handle automatic stop of telemetry capture (game crash/quit detected)."""
        lifecycle_service = self._get_or_create_service(
            "_telemetry_lifecycle_service",
            TelemetryLifecycleService,
        )

        await lifecycle_service.handle_auto_stop(
            reason=reason,
            telemetry_capture=self._telemetry_capture,
            telemetry_analyzer=self._telemetry_analyzer,
            home_page=self._home_page,
            current_track_name=self._current_track_name,
        )
    
    async def _stop_telemetry_capture(self, reason: str = "session_end", discard: bool = False):
        """Stop telemetry capture and generate analysis when game session ends.
        
        Args:
            reason: Reason for stopping (session_end, manual, heartbeat_timeout, etc.)
            discard: If True, drop captured frames without running analysis.
                Used when the buffer is known to be contaminated (e.g. session
                restart while a previous run was still being recorded).
        """
        lifecycle_service = self._get_or_create_service(
            "_telemetry_lifecycle_service",
            TelemetryLifecycleService,
        )

        await lifecycle_service.stop_capture(
            reason=reason,
            discard=discard,
            telemetry_capture=self._telemetry_capture,
            telemetry_analyzer=self._telemetry_analyzer,
            home_page=self._home_page,
            current_track_name=self._current_track_name,
        )
    
    async def _on_user_detected(self, steam_id: str, player_name: Optional[str]):
        """Handle user detection from log parser."""
        bootstrap_service = self._get_or_create_service(
            "_user_bootstrap_service",
            UserBootstrapService,
        )

        await bootstrap_service.handle_detected_user(
            app=self,
            steam_id=steam_id,
            player_name=player_name,
            create_discord_notifier=DiscordNotifier,
        )

    async def _bootstrap_startup_user(self, steam_id: Optional[str], steam_name: Optional[str]) -> None:
        """Handle startup-time user bootstrap from registry detection."""
        bootstrap_service = self._get_or_create_service(
            "_user_bootstrap_service",
            UserBootstrapService,
        )

        await bootstrap_service.handle_startup_user(
            app=self,
            steam_id=steam_id,
            steam_name=steam_name,
            create_discord_notifier=DiscordNotifier,
        )
    
    async def _on_game_version(self, version: str):
        """Handle game version detection from log parser."""
        if self._home_page:
            self._home_page.set_game_version(version)
    
    def _on_window_close(self, e):
        """Handle window close."""
        self._cleanup()
    
    async def start_monitoring(self):
        """Start monitoring the log file."""
        await self._monitoring_service.start(
            log_parser=self._log_parser,
            home_page=self._home_page,
            log_path=self._config.log_path,
            on_game_status_change=self._on_game_status_change,
            is_telemetry_capturing=lambda: bool(
                self._telemetry_capture and self._telemetry_capture.is_capturing()
            ),
        )
    
    def stop_monitoring(self):
        """Stop monitoring the log file."""
        self._monitoring_service.stop(
            log_parser=self._log_parser,
            home_page=self._home_page,
        )
    
    def _save_settings(self, config: AppConfig):
        """Save settings and apply changes."""
        settings_service = self._get_or_create_service(
            "_settings_service",
            SettingsService,
        )

        settings_service.apply(
            app=self,
            config=config,
            create_discord_notifier=DiscordNotifier,
            get_pb_cache_for_server=get_pb_cache,
            create_api_client=APIClient,
            create_log_parser=self._create_log_parser,
        )
    
    async def _test_discord_webhook(self, webhook_url: str) -> tuple[bool, str]:
        """Test Discord webhook connection."""
        if self._discord_notifier:
            success = await self._discord_notifier.send_test_message()
            if success:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Test message sent successfully!", color="#ffffff"),
                    bgcolor="#51cf66",
                )
                return True, "Test message sent successfully"
            else:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Failed to send test message", color="#ffffff"),
                    bgcolor="#ff6b6b",
                )
                return False, "Failed to send test message"
        else:
            return False, "Discord notifier not initialized"
    
    def _show_pb_cache_viewer(self, e=None):
        """Show the PB cache viewer dialog."""
        log_debug(
            Component.APP,
            "PB cache viewer requested",
            pb_cache=self._pb_cache,
            pb_cache_type=type(self._pb_cache).__name__,
            is_loaded=self._pb_cache.is_loaded() if self._pb_cache else None,
        )
        show_pb_cache_dialog(self.page, self._pb_cache)
    
    async def _test_connection(self, server_url: str) -> tuple[bool, str]:
        """Test connection to server."""
        test_client = APIClient(server_url=server_url)
        return await test_client.test_connection()
    
    def _cleanup(self):
        """Cleanup resources before exit."""
        lifecycle_service = self._get_or_create_service(
            "_app_lifecycle_service",
            AppLifecycleService,
        )

        lifecycle_service.cleanup(app=self)


async def main(page: ft.Page):
    """Application entry point for Flet."""
    # Start log capture early
    from .components.debug_logs import start_log_capture
    start_log_capture()
    
    app = SimLapsApp(page)
    
    # Log initial configuration status
    log_info(
        Component.APP,
        "Initial configuration",
        server_url=app._config.server_url,
        discord_enabled=app._config.discord_enabled,
        discord_webhook_configured=bool(app._config.discord_webhook_url),
        discord_pb_only=app._config.discord_pb_only,
        pb_cache_loaded=app._pb_cache.is_loaded(),
    )
    
    # Try to detect Steam user immediately from registry
    steam_id, steam_name = get_steam_user()
    await app._bootstrap_startup_user(steam_id, steam_name)
    
    # Start monitoring after PB preload
    await app.start_monitoring()


def run_app():
    """Run the Flet application."""
    ft.run(main)
