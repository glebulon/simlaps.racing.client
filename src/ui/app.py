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
from .components.lap_card import LapCardStatus
from .components.status_bar import ConnectionStatus
from .components.telemetry_status import TelemetryStatus, TelemetryButton
from src.core.log_parser import LogParser
from src.models import SessionData, LapData
from src.core.api_client import APIClient, SubmissionStatus
from src.core.security import get_steam_user, is_game_running
from src.core.discord_notifier import DiscordNotifier, LapData as DiscordLapData
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
        
        # Discord and PB services
        log_info(Component.APP, "Initializing Discord and PB services")
        self._discord_notifier: Optional[DiscordNotifier] = None
        self._pb_cache = get_pb_cache(self._config.server_url)
        log_info(Component.APP, "PB cache initialized", initialized=self._pb_cache is not None)
        
        # Parser task
        log_info(Component.APP, "Initializing parser task")
        self._parser_task: Optional[asyncio.Task] = None
        self._game_monitor_task: Optional[asyncio.Task] = None
        
        # Telemetry services
        self._telemetry_capture: Optional[TelemetryCapture] = None
        self._telemetry_analyzer: Optional[TelemetryAnalyzer] = None
        self._telemetry_button: Optional[TelemetryButton] = None
        self._current_track_name: Optional[str] = None
        
        # Pages
        print("[APP] Initializing UI pages...")
        self._home_page: Optional[HomePage] = None
        self._settings_page: Optional[SettingsPage] = None
        self._history_page: Optional[HistoryPage] = None
        self._current_page = AppPage.HOME
        
        # History tracking
        print("[APP] Setting up history tracking...")
        self._history_entries: list[HistoryEntry] = []
        
        # Initialize
        print("[APP] Starting initialization...")
        self._init_services()
        self._init_pages()
        self._attach_telemetry_ui()
        self._show_page(AppPage.HOME)
        print("[APP] Initialization complete!")
    
    def _setup_page(self):
        """Configure Flet page."""
        print("[APP] Setting up Flet page...")
        try:
            self.page.title = "SimLaps Telemetry"
            self.page.width = 500
            self.page.height = 700
            self.page.bgcolor = "#0f0f1a"
            self.page.padding = 0
            self.page.spacing = 0
            print("[APP] Flet page properties set")
        except Exception as e:
            print(f"[APP] Error setting up Flet page: {e}")
            import traceback
            traceback.print_exc()
        
        # Set window close handler
        print("[APP] Setting up window close handler...")
        try:
            self.page.on_close = self._on_window_close
            print("[APP] Window close handler set")
        except Exception as e:
            print(f"[APP] Error setting window close handler: {e}")
        
        # Set window icon
        print("[APP] Setting up window icon...")
        try:
            icon_path = self._get_icon_path()
            if icon_path:
                self.page.window.icon = icon_path
                print(f"[APP] Window icon set: {icon_path}")
            else:
                print("[APP] No icon file found")
        except Exception as e:
            print(f"[APP] Error setting window icon: {e}")
        
        # Dark theme
        print("[APP] Setting up dark theme...")
        try:
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.theme = ft.Theme(
                color_scheme_seed="#7c3aed",
            )
            print("[APP] Dark theme applied")
        except Exception as e:
            print(f"[APP] Error setting up theme: {e}")
        
        print("[APP] Flet page setup complete!")
        
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
        )
        
        # Initialize telemetry if enabled
        self._init_telemetry_services()
    
    def _init_telemetry_services(self):
        """Initialize telemetry capture and analyzer services."""
        if not self._config.telemetry_enabled:
            print("[APP] Telemetry disabled in settings")
            return
        
        try:
            self._telemetry_capture = TelemetryCapture(
                hz=10.0,
                output_dir=self._config.telemetry_output_path,
                debug_logs=self._config.telemetry_debug_logs,
            )
            # Set up auto-stop callback to trigger analysis
            self._telemetry_capture.set_on_stop_callback(self._on_telemetry_auto_stop)
            self._telemetry_analyzer = TelemetryAnalyzer(
                output_dir=self._config.telemetry_output_path,
                track_catalog=TRACK_CATALOG,
            )
            
            # Create telemetry button
            print(f"[APP] Creating TelemetryButton with on_click={self._open_telemetry_location}")
            self._telemetry_button = TelemetryButton(
                on_click=self._open_telemetry_location,
                output_path=self._config.telemetry_output_path,
            )
            print(f"[APP] TelemetryButton created, on_click={self._telemetry_button.on_click}")
            
            # Set button on home page
            if self._home_page:
                print(f"[APP] Home page exists, calling set_telemetry_button directly from _init_telemetry_services")
                self._home_page.set_telemetry_button(
                    self._telemetry_button,
                    self._config.telemetry_output_path,
                )
            else:
                print(f"[APP] Home page doesn't exist yet, will attach later")
            
            print(f"[APP] Telemetry services initialized: output={self._config.telemetry_output_path}")
        except Exception as e:
            print(f"[APP] Failed to initialize telemetry: {e}")
            self._telemetry_capture = None
            self._telemetry_analyzer = None

    def _attach_telemetry_ui(self):
        """Attach telemetry UI controls after the home page exists."""
        print(f"[APP] _attach_telemetry_ui called: home_page={self._home_page is not None}, button={self._telemetry_button is not None}")
        if self._telemetry_button:
            print(f"[APP] Telemetry button on_click: {self._telemetry_button.on_click}")
        if self._home_page and self._telemetry_button:
            print(f"[APP] Calling set_telemetry_button...")
            self._home_page.set_telemetry_button(
                self._telemetry_button,
                self._config.telemetry_output_path,
            )
        else:
            print(f"[APP] NOT calling set_telemetry_button - missing home_page or button")

    def _open_telemetry_location(self, e, output_path):
        """Open the telemetry output folder in file explorer."""
        import subprocess
        import os
        
        print(f"[APP] _open_telemetry_location called with output_path={output_path}")
        
        try:
            if not output_path:
                print("[APP] No telemetry output path configured")
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
            
            print(f"[APP] Opening telemetry location: {output_path}")
            print(f"[APP] Directory exists: {os.path.exists(output_path)}")
            print(f"[APP] Is directory: {os.path.isdir(output_path)}")
            
            if sys.platform == "win32":
                # Use os.startfile which is more reliable for opening folders on Windows
                os.startfile(output_path)
                print(f"[APP] Called os.startfile successfully")
            else:
                subprocess.Popen(["open", output_path])
                print(f"[APP] Called subprocess.Popen successfully")
        except Exception as ex:
            import traceback
            print(f"[APP] Failed to open telemetry location: {ex}")
            traceback.print_exc()
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
    
    async def _on_lap_complete(self, session: SessionData, lap: LapData):
        """Handle completed lap from parser."""
        print(f"[APP] _on_lap_complete called: {lap.lap_time_str} on {session.track}")
        try:
            # Update detected user in UI
            if session.player_id:
                print(f"[APP] Updating detected user: {session.player_id}")
                self._home_page.set_detected_user(session.player_id, session.player_name)
            
            # Update current track name for telemetry
            if session.track and session.track != "Unknown":
                self._current_track_name = session.track

            # Record lap boundary in telemetry capture and get fuel consumption
            if self._telemetry_capture and self._telemetry_capture.is_capturing():
                fuel_used = self._telemetry_capture.record_lap_boundary(lap.lap_time_ms)
                
                # Update lap data with telemetry-calculated fuel if available
                if fuel_used is not None:
                    lap.fuel_used = fuel_used
                    lap.fuel_reliable = True
                    print(f"[APP] Telemetry fuel: {fuel_used:.3f}L")

            # Fallback: if parser missed a game-status transition (e.g. app
            # attached mid-session), a lap-complete event proves we're in an
            # active session. Start telemetry capture now.
            if (
                self._config.telemetry_enabled
                and self._telemetry_capture
                and not self._telemetry_capture.is_capturing()
            ):
                print("[APP] Triggering telemetry capture start (lap-complete fallback)")
                await self._start_telemetry_capture()
            
            # Determine if we should submit this lap
            should_submit = self._config.auto_submit and (lap.is_valid or self._config.submit_invalid_laps)
            print(f"[APP] should_submit={should_submit}, is_valid={lap.is_valid}")
            print(
                "[APP] lap diagnostics: "
                f"state={getattr(lap, 'lap_state', 'UNKNOWN')} "
                f"type={getattr(lap, 'lap_type', 'UNKNOWN')} "
                f"phys_lap={getattr(lap, 'physics_lap_number', None)} "
                f"sectors=({lap.sector1_ms},{lap.sector2_ms},{lap.sector3_ms}) "
                f"consistent={getattr(lap, 'sectors_consistent', None)}"
            )
            if not lap.is_valid:
                print(
                    "[APP] invalid reason: "
                    f"state={getattr(lap, 'lap_state', 'UNKNOWN')}"
                )

            # Update local PB cache for every valid lap (independent of Discord posting)
            if lap.is_valid and lap.lap_time_ms > 0:
                if session.track and session.track != "Unknown" and session.car and session.car != "Unknown":
                    is_pb = self._pb_cache.check_and_update_pb(
                        session.track,
                        session.car,
                        lap.lap_time_ms,
                    )
                    print(f"[APP] PB cache update (valid lap): {is_pb}")
                else:
                    print("[APP] Skipping PB cache update: missing track/car")
            
            # Determine initial status
            if not lap.is_valid and not self._config.submit_invalid_laps:
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
            print(f"[APP] Adding lap card to home page...")
            try:
                card = self._home_page.add_lap(session, lap, status)
                print(f"[APP] Lap card added successfully")
            except Exception as e:
                # If home page add fails, remove the history entry to maintain sync
                print(f"[ERROR] Failed to add lap card to home page: {e}")
                self._history_entries.pop()  # Remove the entry we just added
                raise
            
            # Debug: Check synchronization
            print(f"[DEBUG] Home lap count: {self._home_page._lap_count}")
            print(f"[DEBUG] History entries: {len(self._history_entries)}")
            print(f"[DEBUG] History entry added - was_submitted: {history_entry.was_submitted}, was_valid: {history_entry.was_valid}")
            
            # Verify synchronization
            if self._home_page._lap_count != len(self._history_entries):
                print(f"[ERROR] Synchronization mismatch! Home: {self._home_page._lap_count}, History: {len(self._history_entries)}")
                # This should never happen now, but if it does, we have a serious issue
            
            # Auto-submit if enabled
            if should_submit:
                print(f"[APP] Auto-submitting lap...")
                await self._submit_lap(card, session, lap, history_entry)
                print(f"[APP] Auto-submit complete")
        except Exception as e:
            print(f"[ERROR] _on_lap_complete failed: {e}")
            import traceback
            traceback.print_exc()
    
    async def _submit_lap(
        self,
        card,
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
    ):
        """Submit a lap to the server."""
        print(f"[SUBMIT] Starting lap submission: {lap.lap_time_str} on {session.track}")
        print(f"[SUBMIT] Lap valid: {lap.is_valid}, submit_invalid: {self._config.submit_invalid_laps}")
        print(f"[SUBMIT] Server URL: {self._config.server_url}")
        
        card.update_status(LapCardStatus.SUBMITTING)
        
        try:
            print(f"[SUBMIT] Sending API request...")
            result = await self._api_client.submit_lap(
                session=session,
                lap=lap,
                submit_invalid=self._config.submit_invalid_laps,
            )
            print(f"[SUBMIT] API response received: {result}")
        except Exception as e:
            print(f"[SUBMIT] Submit error: {e}")
            card.update_status(LapCardStatus.FAILED, f"Submit error: {str(e)}")
            return
        
        if result is None:
            print(f"[SUBMIT] No response from server")
            card.update_status(LapCardStatus.FAILED, "No response from server")
            return
        
        if result.status == SubmissionStatus.SUCCESS:
            print(f"[SUBMIT] ✅ Lap submitted successfully!")
            card.update_status(LapCardStatus.SUBMITTED)
            history_entry.was_submitted = True
            
            # Post to Discord if configured
            print(f"[SUBMIT] Checking Discord posting...")
            await self._post_to_discord(session, lap, steam_id=session.player_id, steam_name=session.player_name)
        elif result.status == SubmissionStatus.INVALID_LAP:
            print(f"[SUBMIT] ❌ Lap rejected as invalid: {result.message}")
            card.update_status(LapCardStatus.INVALID, result.message)
        elif result.status == SubmissionStatus.GAME_NOT_RUNNING:
            print(f"[SUBMIT] ❌ Game not running: {result.message}")
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.SIGNATURE_ERROR:
            print(f"[SUBMIT] ❌ Signature error: {result.message}")
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.RATE_LIMITED:
            print(f"[SUBMIT] ❌ Rate limited: {result.message}")
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.PLAUSIBILITY_FAILED:
            print(f"[SUBMIT] ❌ Plausibility check failed: {result.message}")
            card.update_status(LapCardStatus.FAILED, result.message)
        else:
            print(f"[SUBMIT] ❌ Unknown error: {result.message}")
            card.update_status(LapCardStatus.FAILED, result.message)
    
    async def _post_to_discord(
        self,
        session: SessionData,
        lap: LapData,
        steam_id: str,
        steam_name: Optional[str] = None,
    ):
        """Post lap to Discord if configured and meets criteria."""
        try:
            print(f"[DISCORD] Starting Discord post check...")
            
            # Check if Discord is properly configured
            if not self._config.discord_enabled:
                print(f"[DISCORD] ❌ Discord disabled in settings")
                return
            
            # Validate webhook URL
            if not self._config.discord_webhook_url or not self._config.discord_webhook_url.strip():
                print(f"[DISCORD] ❌ No webhook URL configured")
                return
            
            # Check if Discord notifier is initialized
            if not self._discord_notifier:
                print("[DISCORD] ❌ Discord notifier not initialized - skipping post")
                return
            
            print(f"[DISCORD] ✅ Discord configured, checking PB criteria...")
            
            # Check personal best criteria
            is_pb = False
            print(f"[DISCORD] PB-only mode: {self._config.discord_pb_only}")
            if self._config.discord_pb_only:
                is_pb = self._pb_cache.check_and_update_pb(session.track, session.car, lap.lap_time_ms)
                print(f"[DISCORD] PB check result: {is_pb}")
                if not is_pb:
                    print(f"[DISCORD] ❌ Skipping Discord post: not a personal best")
                    return  # Not a personal best, skip posting
            else:
                # Not PB-only mode, post all valid laps (or invalid if enabled)
                is_pb = self._pb_cache.check_and_update_pb(session.track, session.car, lap.lap_time_ms)
                print(f"[DISCORD] PB check result (non-PB-only mode): {is_pb}")
            
            print(f"[DISCORD] ✅ Creating Discord lap data...")
            # Create Discord lap data
            sector_times = None
            if lap.sector1_ms is not None and lap.sector2_ms is not None and lap.sector3_ms is not None:
                sector_times = [lap.sector1_ms, lap.sector2_ms, lap.sector3_ms]
            
            discord_lap = DiscordLapData(
                track_name=session.track,
                car_name=session.car,
                lap_time_ms=lap.lap_time_ms,
                valid=lap.is_valid,
                steam_id=steam_id,
                steam_name=steam_name,
                is_personal_best=is_pb,
                created_at=lap.timestamp,
                sector_times_ms=sector_times,
                fuel_used_liters=lap.fuel_used,
                tire_compound=lap.tyre_compound if lap.tyre_compound != "Unknown" else None,
            )
            
            print(f"[DISCORD] 📤 Posting to Discord webhook...")
            # Post to Discord (non-blocking, failure-safe)
            success = await self._discord_notifier.post_lap(discord_lap)
            if success:
                print(f"[DISCORD] ✅ Discord post successful: {lap.lap_time_str} on {session.track}")
            else:
                print(f"[DISCORD] ❌ Discord post failed: {lap.lap_time_str} on {session.track}")
                # Note: Error details are already logged in DiscordNotifier.post_lap()
                
        except Exception as e:
            print(f"[DISCORD] 💥 Error posting to Discord: {e}")
            import traceback
            traceback.print_exc()
            # Discord failures should never block lap submission
    
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
        print("[APP] Session restart detected — restarting telemetry capture")
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
                # Start telemetry capture
                print("[APP] Triggering telemetry capture start (session active)")
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
        log_debug(Component.APP, "Telemetry start requested", 
                  enabled=self._config.telemetry_enabled, 
                  capture_exists=self._telemetry_capture is not None)
        
        if not self._telemetry_capture or not self._config.telemetry_enabled:
            log_info(Component.APP, "Telemetry start skipped: disabled or unavailable")
            return
        if self._telemetry_capture.is_capturing():
            log_info(Component.APP, "Telemetry start skipped: already capturing")
            return
        
        try:
            log_info(Component.APP, "Starting telemetry capture from UI")
            if self._home_page:
                self._home_page.set_telemetry_status(TelemetryStatus.CAPTURING, 0)
            
            # Start capture synchronously
            success = await self._telemetry_capture.start_capture()
            if not success:
                log_error(Component.APP, "Telemetry capture failed to start")
                if self._home_page:
                    self._home_page.set_telemetry_status(TelemetryStatus.ERROR)
            
        except Exception as e:
            log_exception(Component.APP, "Telemetry start error", e)
            if self._home_page:
                self._home_page.set_telemetry_status(TelemetryStatus.ERROR)
    
    async def _on_telemetry_auto_stop(self, reason: str):
        """Handle automatic stop of telemetry capture (game crash/quit detected)."""
        log_info(Component.APP, "Telemetry auto-stop", reason=reason)
        
        # Update UI to show stopped status
        if self._home_page:
            self._home_page.set_connection_status(
                ConnectionStatus.CONNECTED,
                f"Session ended ({reason})",
            )
        
        # Run analysis on captured frames
        if self._telemetry_capture and self._telemetry_analyzer:
            frames = self._telemetry_capture.get_frames()
            frame_count = len(frames)
            
            if frame_count > 0:
                log_info(Component.APP, "Starting analysis", frames=frame_count)
                try:
                    self._home_page.set_telemetry_status(TelemetryStatus.ANALYZING, frame_count)
                    
                    metadata = self._telemetry_capture.get_metadata()
                    lap_boundaries = self._telemetry_capture.get_lap_boundaries()
                    result = await self._telemetry_analyzer.analyze(
                        frames,
                        hz=10.0,
                        metadata=metadata,
                        track_name=self._current_track_name,
                        output_prefix=self._telemetry_capture.get_output_prefix(),
                        game_lap_boundaries=lap_boundaries,
                    )
                    
                    log_info(Component.APP, "Analysis complete", 
                            laps=result.laps_detected, 
                            best_lap_time=f"{result.best_lap_time:.1f}s")
                    self._home_page.set_telemetry_status(
                        TelemetryStatus.COMPLETE,
                        frame_count,
                        result.html_path,
                    )
                except Exception as e:
                    log_exception(Component.APP, "Analysis error", e)
                    self._home_page.set_telemetry_status(TelemetryStatus.ERROR)
            else:
                self._home_page.set_telemetry_status(TelemetryStatus.IDLE)
    
    async def _stop_telemetry_capture(self, reason: str = "session_end", discard: bool = False):
        """Stop telemetry capture and generate analysis when game session ends.
        
        Args:
            reason: Reason for stopping (session_end, manual, heartbeat_timeout, etc.)
            discard: If True, drop captured frames without running analysis.
                Used when the buffer is known to be contaminated (e.g. session
                restart while a previous run was still being recorded).
        """
        if not self._telemetry_capture or not self._telemetry_analyzer:
            return
        if not self._telemetry_capture.is_capturing():
            stop_reason = self._telemetry_capture.get_stop_reason()
            if stop_reason is not None:
                print(f"[APP] Telemetry already stopped (reason: {stop_reason}), skipping duplicate stop")
                return
        
        try:
            print(f"[APP] Stopping telemetry capture (reason: {reason})...")
            frames = await self._telemetry_capture.stop_capture(reason)
            frame_count = len(frames)
            print(f"[APP] Captured {frame_count} telemetry frames")

            if discard:
                print(f"[APP] Discarding {frame_count} frames (no analysis on contaminated buffer)")
                self._home_page.set_telemetry_status(TelemetryStatus.IDLE)
                return

            if frame_count > 0:
                # Show analyzing status
                self._home_page.set_telemetry_status(TelemetryStatus.ANALYZING, frame_count)
                
                # Run analysis with track name
                metadata = self._telemetry_capture.get_metadata()
                lap_boundaries = self._telemetry_capture.get_lap_boundaries()
                result = await self._telemetry_analyzer.analyze(
                    frames, 
                    hz=10.0,
                    metadata=metadata,
                    track_name=self._current_track_name,
                    output_prefix=self._telemetry_capture.get_output_prefix(),
                    game_lap_boundaries=lap_boundaries,
                )
                
                print(f"[APP] Analysis complete: {result.laps_detected} laps, best: {result.best_lap_time:.2f}s")
                self._home_page.set_telemetry_status(
                    TelemetryStatus.COMPLETE,
                    frame_count,
                    result.html_path,
                )
            else:
                self._home_page.set_telemetry_status(TelemetryStatus.IDLE)
                
        except Exception as e:
            print(f"[APP] Error during telemetry analysis: {e}")
            self._home_page.set_telemetry_status(TelemetryStatus.ERROR)
    
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
            print(f"[APP] Preloading personal bests from server: {server_url}")
            print(f"[APP] Steam ID: {steam_id}")
            success = await self._pb_cache.preload_from_api(steam_id)
            if success:
                stats = self._pb_cache.get_cache_stats()
                print(f"[APP] PB cache loaded successfully: {stats['combo_count']} combos")
                print(f"[APP] Cache stats: {stats}")
            else:
                print(f"[APP] Failed to preload PB cache from server")
                print(f"[APP] Discord PB detection may be unreliable")
    
    async def _on_game_version(self, version: str):
        """Handle game version detection from log parser."""
        if self._home_page:
            self._home_page.set_game_version(version)
    
    def _on_window_close(self, e):
        """Handle window close."""
        self._cleanup()
    
    async def start_monitoring(self):
        """Start monitoring the log file."""
        if self._parser_task and not self._parser_task.done():
            return
        
        # Try to get game version from existing log file
        game_version = self._get_game_version_from_log()
        if game_version:
            self._home_page.set_game_version(game_version)
        
        # Set initial status - monitoring but not necessarily game running
        self._home_page.set_game_running(False)  # Will be set to True when session starts
        self._home_page.set_connection_status(
            ConnectionStatus.CONNECTED,
            "Monitoring log file...",
        )
        
        # Use page.run_task() for proper Flet background task handling
        self._parser_task = self.page.run_task(self._run_parser)
        self._game_monitor_task = self.page.run_task(self._run_game_monitor)
    
    async def _run_game_monitor(self):
        """Poll is_game_running() and stop telemetry if the process disappears."""
        POLL_INTERVAL = 5.0
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL)
                if (
                    self._telemetry_capture
                    and self._telemetry_capture.is_capturing()
                    and not is_game_running()
                ):
                    log_info(Component.APP, "Game process gone (monitor) — stopping telemetry")
                    await self._on_game_status_change(False)
                    break
        except asyncio.CancelledError:
            pass

    async def _run_parser(self):
        """Run the log parser in background."""
        try:
            await self._log_parser.follow()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._home_page.set_connection_status(
                ConnectionStatus.ERROR,
                f"Error: {str(e)}",
            )
    
    def stop_monitoring(self):
        """Stop monitoring the log file."""
        if self._log_parser:
            self._log_parser.stop()
        
        if self._parser_task:
            self._parser_task.cancel()
            self._parser_task = None
        
        if self._game_monitor_task:
            self._game_monitor_task.cancel()
            self._game_monitor_task = None
        
        self._home_page.set_connection_status(
            ConnectionStatus.DISCONNECTED,
            "Monitoring stopped",
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
        self._api_client = APIClient(server_url=config.server_url)
        
        # Update services with new settings
        self._api_client.set_server_url(config.server_url)
        
        # Re-initialize telemetry if settings changed
        if config.telemetry_enabled and not self._telemetry_capture:
            print("[APP] Telemetry enabled - initializing services...")
            self._init_telemetry_services()
            self._attach_telemetry_ui()
            self._start_telemetry_on_startup()
        elif not config.telemetry_enabled and self._telemetry_capture:
            print("[APP] Telemetry disabled - stopping services...")
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
        print(f"[APP] PB cache viewer called! PB cache: {self._pb_cache}")
        print(f"[APP] PB cache type: {type(self._pb_cache)}")
        print(f"[APP] PB cache loaded: {self._pb_cache.is_loaded() if self._pb_cache else 'None'}")
        show_pb_cache_dialog(self.page, self._pb_cache)
    
    async def _test_connection(self, server_url: str) -> tuple[bool, str]:
        """Test connection to server."""
        test_client = APIClient(server_url=server_url)
        return await test_client.test_connection()
    
    def _get_game_version_from_log(self) -> Optional[str]:
        """Read game version from the first few lines of the log file."""
        import re
        try:
            log_path = self._config.log_path
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    # Only read first 10 lines - version is at the top
                    for _ in range(10):
                        line = f.readline()
                        if not line:
                            break
                        if "Build release" in line:
                            match = re.search(r"Build release ([^,]+),", line)
                            if match:
                                return match.group(1)
        except Exception:
            pass
        return None
    
    def _cleanup(self):
        """Cleanup resources before exit."""
        # Stop telemetry capture first if running
        if self._telemetry_capture and self._telemetry_capture.is_capturing():
            print("[APP] Cleanup: stopping active telemetry capture...")
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
    print(f"[APP] Server URL: {app._config.server_url}")
    print(f"[APP] Discord enabled: {app._config.discord_enabled}")
    print(f"[APP] Discord webhook configured: {bool(app._config.discord_webhook_url)}")
    print(f"[APP] PB-only mode: {app._config.discord_pb_only}")
    print(f"[APP] PB cache loaded: {app._pb_cache.is_loaded()}")
    
    # Try to detect Steam user immediately from registry
    steam_id, steam_name = get_steam_user()
    if steam_id:
        print(f"[APP] Steam user detected on startup: {steam_id} ({steam_name})")
        app._home_page.set_detected_user(steam_id, steam_name)
        
        # Trigger Discord initialization immediately
        if app._config.discord_webhook_url and app._config.discord_enabled:
            app._discord_notifier = DiscordNotifier(app._config.discord_webhook_url)
            print(f"[APP] Discord notifier initialized for user {steam_id}")
        
        # Preload personal bests immediately
        print(f"[APP] Triggering PB preload for Steam user: {steam_id}")
        success = await app._pb_cache.preload_from_api(steam_id)
        if success:
            stats = app._pb_cache.get_cache_stats()
            print(f"[APP] PB cache loaded on startup: {stats['combo_count']} combos")
        else:
            print(f"[APP] Failed to preload PB cache on startup")
    else:
        print("[APP] No Steam user detected - PB preload will wait for log detection")
    
    # Start monitoring after PB preload
    await app.start_monitoring()


def run_app():
    """Run the Flet application."""
    ft.run(main)
