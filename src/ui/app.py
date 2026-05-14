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
from .services.lap_submission_service import LapSubmissionService
from .services.monitoring_service import MonitoringService
from .services.telemetry_lifecycle_service import TelemetryLifecycleService
from src.core.log_parser import LogParser
from src.models import SessionData, LapData, SharedSessionManager
from src.core.api_client import APIClient
from src.core.security import get_steam_user
from src.core.discord_notifier import DiscordNotifier
from src.core.pb_cache import get_pb_cache
from src.core.telemetry_capture import TelemetryCapture
from src.core.track_catalog import TRACK_CATALOG
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.utils.structured_logger import log_debug, log_info, log_warning, log_error, log_exception, Component
from src.utils.config import ConfigManager, AppConfig, get_config_manager


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
        self._telemetry_lifecycle_service = TelemetryLifecycleService()
        self._lap_submission_service = LapSubmissionService()
        
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
        
        # Window close handler
        self.page.on_close = self._on_window_close
    
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
        self._log_parser = LogParser(
            log_path=self._config.log_path,
            on_lap_complete=self._on_lap_complete,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
            on_session_end=self._on_car_removed,
            on_session_restart=self._on_session_restart,
            session_manager=self._session_manager,
        )
        
        # Initialize telemetry if enabled
        self._init_telemetry_services()
    
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
            # Update detected user in UI
            if session.player_id:
                log_debug(Component.APP, "Updating detected user", steam_id=session.player_id)
                self._home_page.set_detected_user(session.player_id, session.player_name)
            
            # Update current track name for telemetry
            if session.track and session.track != "Unknown":
                self._current_track_name = session.track

            # Record lap boundary so the analyzer can use authoritative lap splits.
            # Fuel per lap is owned entirely by the log parser (Physics SHM + spike
            # detection) and is already set on lap.fuel_used before this point.
            if self._telemetry_capture and self._telemetry_capture.is_capturing():
                self._telemetry_capture.record_lap_boundary(
                    lap.lap_time_ms,
                    lap.lap_number,
                )

            elif self._config.telemetry_enabled and self._telemetry_capture:
                # A lap-complete event is too late to begin a useful capture
                # for that lap and can fire during post-session shutdown.
                log_debug(
                    Component.APP,
                    "Telemetry missed lap boundary; not starting capture from lap-complete",
                    lap_number=lap.lap_number,
                )
            
            # Determine if we should submit this lap (prefer authoritative shared validity)
            shared_lap_validity = self._session_manager.get_lap_validity_data(lap.lap_number)
            effective_is_valid = (
                shared_lap_validity.is_valid
                if shared_lap_validity is not None
                else lap.is_valid
            )
            should_submit = self._config.auto_submit and (
                effective_is_valid or self._config.submit_invalid_laps
            )
            log_debug(
                Component.APP,
                "Lap submission decision",
                should_submit=should_submit,
                parser_is_valid=lap.is_valid,
                effective_is_valid=effective_is_valid,
                lap_number=lap.lap_number,
            )
            log_debug(
                Component.APP,
                "Lap diagnostics",
                lap_state=getattr(lap, "lap_state", "UNKNOWN"),
                lap_type=getattr(lap, "lap_type", "UNKNOWN"),
                physics_lap_number=getattr(lap, "physics_lap_number", None),
                sector1_ms=lap.sector1_ms,
                sector2_ms=lap.sector2_ms,
                sector3_ms=lap.sector3_ms,
                sectors_consistent=getattr(lap, "sectors_consistent", None),
            )
            if not effective_is_valid:
                log_debug(
                    Component.APP,
                    "Invalid lap diagnostics",
                    lap_state=getattr(lap, "lap_state", "UNKNOWN"),
                    lap_number=lap.lap_number,
                )

            # Update local PB cache for every valid lap (independent of Discord posting)
            pb_was_new: Optional[bool] = None
            if effective_is_valid and lap.lap_time_ms > 0:
                if session.track and session.track != "Unknown" and session.car and session.car != "Unknown":
                    pb_was_new = self._pb_cache.check_and_update_pb(
                        session.track,
                        session.car,
                        lap.lap_time_ms,
                    )
                    log_debug(
                        Component.APP,
                        "PB cache update",
                        pb_was_new=pb_was_new,
                        track=session.track,
                        car=session.car,
                        lap_time_ms=lap.lap_time_ms,
                    )
                else:
                    log_debug(Component.APP, "Skipping PB cache update: missing track/car")
            
            # Determine initial status
            if not effective_is_valid and not self._config.submit_invalid_laps:
                status = LapCardStatus.INVALID
            else:
                status = LapCardStatus.SUBMITTING if should_submit else LapCardStatus.PENDING
            
            # Add to history FIRST (before home page to ensure synchronization)
            history_entry = HistoryEntry(
                track=session.track,
                car=session.car,
                lap_time_ms=lap.lap_time_ms,
                timestamp=lap.timestamp,
                was_submitted=False,
                was_valid=lap.is_valid,
            )
            self._history_entries.append(history_entry)
            
            # Add to home page (this increments the counter)
            try:
                card = self._home_page.add_lap(session, lap, status)
                log_debug(Component.APP, "Lap card added", lap_number=lap.lap_number)
            except Exception as e:
                # If home page add fails, remove the history entry to maintain sync
                log_exception(Component.APP, "Failed to add lap card to home page", e)
                self._history_entries.pop()  # Remove the entry we just added
                raise
            
            # Debug: Check synchronization
            log_debug(
                Component.APP,
                "Lap/history synchronization state",
                home_lap_count=self._home_page._lap_count,
                history_entries=len(self._history_entries),
                was_submitted=history_entry.was_submitted,
                was_valid=history_entry.was_valid,
            )
            
            # Verify synchronization
            if self._home_page._lap_count != len(self._history_entries):
                log_error(
                    Component.APP,
                    "Synchronization mismatch",
                    home_lap_count=self._home_page._lap_count,
                    history_entries=len(self._history_entries),
                )
                # This should never happen now, but if it does, we have a serious issue
            
            # Auto-submit if enabled
            if should_submit:
                log_debug(Component.APP, "Auto-submitting lap", lap_number=lap.lap_number)
                await self._submit_lap(card, session, lap, history_entry, pb_was_new=pb_was_new)
                log_debug(Component.APP, "Auto-submit complete", lap_number=lap.lap_number)
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
        submission_service = getattr(self, "_lap_submission_service", None)
        if submission_service is None:
            submission_service = LapSubmissionService()
            self._lap_submission_service = submission_service

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
        submission_service = getattr(self, "_lap_submission_service", None)
        if submission_service is None:
            submission_service = LapSubmissionService()
            self._lap_submission_service = submission_service

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
        lifecycle_service = getattr(self, "_telemetry_lifecycle_service", None)
        if lifecycle_service is None:
            lifecycle_service = TelemetryLifecycleService()
            self._telemetry_lifecycle_service = lifecycle_service

        await lifecycle_service.start_capture(
            telemetry_capture=self._telemetry_capture,
            home_page=self._home_page,
            telemetry_enabled=self._config.telemetry_enabled,
        )
    
    async def _on_telemetry_auto_stop(self, reason: str):
        """Handle automatic stop of telemetry capture (game crash/quit detected)."""
        lifecycle_service = getattr(self, "_telemetry_lifecycle_service", None)
        if lifecycle_service is None:
            lifecycle_service = TelemetryLifecycleService()
            self._telemetry_lifecycle_service = lifecycle_service

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
        lifecycle_service = getattr(self, "_telemetry_lifecycle_service", None)
        if lifecycle_service is None:
            lifecycle_service = TelemetryLifecycleService()
            self._telemetry_lifecycle_service = lifecycle_service

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
        if self._home_page:
            self._home_page.set_detected_user(steam_id, player_name)
        
        # Initialize Discord notifier if configured
        if self._config.discord_webhook_url and self._config.discord_enabled:
            self._discord_notifier = DiscordNotifier(self._config.discord_webhook_url)
        
        # Preload personal bests for PB detection
        if not self._pb_cache.is_loaded() or self._pb_cache.get_steam_id() != steam_id:
            server_url = self._config.server_url
            log_info(Component.APP, "Preloading personal bests", server_url=server_url, steam_id=steam_id)
            success = await self._pb_cache.preload_from_api(steam_id)
            if success:
                stats = self._pb_cache.get_cache_stats()
                log_info(
                    Component.APP,
                    "PB cache loaded successfully",
                    combo_count=stats["combo_count"],
                    stats=stats,
                )
            else:
                log_warning(Component.APP, "Failed to preload PB cache from server")
                log_warning(Component.APP, "Discord PB detection may be unreliable")
    
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
        self._config = config
        self._config_manager.save()
        
        # Update Discord notifier
        if config.discord_webhook_url and config.discord_enabled:
            self._discord_notifier = DiscordNotifier(config.discord_webhook_url)
        else:
            self._discord_notifier = None
        
        # Update PB cache if server URL changed
        if self._pb_cache.server_url != config.server_url:
            self._pb_cache = get_pb_cache(config.server_url)
        
        # Update API client
        self._api_client = APIClient(
            server_url=config.server_url,
            session_manager=self._session_manager,
        )
        
        # Update services with new settings
        self._api_client.set_server_url(config.server_url)
        
        # Re-initialize telemetry if settings changed
        if config.telemetry_enabled and not self._telemetry_capture:
            log_info(Component.APP, "Telemetry enabled - initializing services")
            self._init_telemetry_services()
            self._attach_telemetry_ui()
            if self._telemetry_capture:
                self.page.run_task(self._start_telemetry_capture)
        elif not config.telemetry_enabled and self._telemetry_capture:
            log_info(Component.APP, "Telemetry disabled - stopping services")
            if self._telemetry_capture.is_capturing():
                self.page.run_task(self._telemetry_capture.stop_capture, "disabled")
            self._telemetry_capture = None
            self._telemetry_analyzer = None
            # Remove button from home page
            if self._home_page:
                self._home_page.set_telemetry_button(None, "")
            self._telemetry_button = None
        
        # Restart parser if log path changed
        was_running = self._log_parser.is_running if self._log_parser else False
        
        if was_running:
            self.stop_monitoring()
        
        self._log_parser = LogParser(
            log_path=config.log_path,
            on_lap_complete=self._on_lap_complete,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
            on_session_end=self._on_car_removed,
            on_session_restart=self._on_session_restart,
            session_manager=self._session_manager,
        )
        
        if was_running:
            self.page.run_task(self.start_monitoring)
        
        # Update home page
        self._home_page.update_config(self._config)
    
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
        # Stop telemetry capture first if running
        if self._telemetry_capture and self._telemetry_capture.is_capturing():
            log_info(Component.APP, "Cleanup: stopping active telemetry capture")
            # Use run_task to properly await async stop
            if self.page:
                self.page.run_task(self._telemetry_capture.stop_capture, "app_close")
        
        self.stop_monitoring()
        
        if self._api_client:
            self.page.run_task(self._api_client.close)


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
    if steam_id:
        log_info(Component.APP, "Steam user detected on startup", steam_id=steam_id, steam_name=steam_name)
        app._home_page.set_detected_user(steam_id, steam_name)
        
        # Trigger Discord initialization immediately
        if app._config.discord_webhook_url and app._config.discord_enabled:
            app._discord_notifier = DiscordNotifier(app._config.discord_webhook_url)
            log_info(Component.APP, "Discord notifier initialized", steam_id=steam_id)
        
        # Preload personal bests immediately
        log_info(Component.APP, "Triggering PB preload for Steam user", steam_id=steam_id)
        success = await app._pb_cache.preload_from_api(steam_id)
        if success:
            stats = app._pb_cache.get_cache_stats()
            log_info(Component.APP, "PB cache loaded on startup", combo_count=stats["combo_count"])
        else:
            log_warning(Component.APP, "Failed to preload PB cache on startup")
    else:
        log_debug(Component.APP, "No Steam user detected - PB preload will wait for log detection")
    
    # Start monitoring after PB preload
    await app.start_monitoring()


def run_app():
    """Run the Flet application."""
    ft.run(main)
