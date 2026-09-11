"""
Main Application UI - Coordinates all pages and core functionality.

Simplified: No authentication required. Uses signed payloads and
detects user from game logs automatically.
"""

import asyncio
import os
import sys
import weakref
from enum import Enum
from typing import Optional

import flet as ft

from src.core.analyzer import TelemetryAnalyzer
from src.core.api_client import APIClient
from src.core.discord_notifier import DiscordNotifier, create_discord_notifier
from src.core.log_parser import LogParser
from src.core.pb_cache import PBCache
from src.core.security import get_steam_user
from src.core.telemetry_capture import TelemetryCapture
from src.core.track_catalog import TRACK_CATALOG
from src.models import LapData, SessionData, SharedSessionManager
from src.utils.config import AppConfig, ConfigManager
from src.utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
    log_info,
    log_warning,
)

from .components.feedback import show_snackbar
from .components.lap_card import LapCard, LapCardStatus
from .components.pb_cache_viewer import show_pb_cache_dialog
from .components.telemetry_status import TelemetryButton
from .pages.history import HistoryEntry, HistoryPage
from .pages.home import HomePage
from .pages.settings import SettingsPage
from .services.app_lifecycle_service import AppLifecycleService
from .services.lap_processing_service import LapProcessingService
from .services.lap_submission_service import LapSubmissionService
from .services.monitoring_service import MonitoringService
from .services.session_lifecycle_service import SessionLifecycleService
from .services.settings_service import SettingsService
from .services.telemetry_lifecycle_service import TelemetryLifecycleService
from .services.user_bootstrap_service import UserBootstrapService


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

        # Core services — load config BEFORE page setup so it can use settings
        log_info(Component.APP, "Loading configuration")
        self._config_manager = ConfigManager()
        self._config = self._config_manager.load()
        log_info(Component.APP, "Configuration loaded", server=self._config.server_url)

        # Page setup (uses self._config for window size)
        self._setup_page()

        # Store app instance reference for components
        page._app_instance = self

        self._api_client: Optional[APIClient] = None
        self._log_parser: Optional[LogParser] = None
        self._session_manager = SharedSessionManager()

        # Discord and PB services
        log_info(Component.APP, "Initializing Discord and PB services")
        self._discord_notifier: Optional[DiscordNotifier] = None
        self._pb_cache = PBCache(self._config.server_url)
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
        # ACE lap numbers are scoped to a game session and can therefore be
        # reused.  Keep the application-history association by object
        # identity instead of treating ``LapData.lap_number`` as a global
        # ordinal.  Weak references keep delayed-callback bookkeeping from
        # retaining completed sessions or trimmed entries.
        self._history_entry_by_lap_id: dict[int, tuple[object, object]] = {}

        # Initialize
        log_info(Component.APP, "Starting initialization")
        self._init_services()
        self._init_pages()
        self._session_lifecycle_service = SessionLifecycleService(
            home_page=self._home_page,
            session_manager=self._session_manager,
            telemetry_capture=self._telemetry_capture,
            start_capture=self._start_telemetry_capture,
            stop_capture=self._stop_telemetry_capture,
        )
        self._attach_telemetry_ui()
        self._show_page(AppPage.HOME)
        log_info(Component.APP, "Initialization complete")

    def _setup_page(self):
        """Configure Flet page.

        Uses config values for window size (user-configurable via Settings).
        Fails fast on errors — a broken page config should surface immediately
        rather than letting the app limp along in a partially-configured state.
        """
        log_debug(Component.APP, "Setting up Flet page")

        self.page.title = "SimLaps Telemetry"
        self.page.width = self._config.window_width
        self.page.height = self._config.window_height
        self.page.bgcolor = "#0f0f1a"
        self.page.padding = 0
        self.page.spacing = 0
        log_debug(Component.APP, "Flet page properties set")

        # Flet 0.86.5 reports native desktop close requests through the
        # Window control.  Intercept the request until asynchronous cleanup
        # (including telemetry analysis) has completed.
        self.page.window.prevent_close = True
        self.page.window.on_event = self._on_window_event
        # Page callbacks are best-effort fallbacks. Flet awaits disconnect
        # dispatch, but schedules close dispatch after session expiration;
        # neither replaces the native close interception above.
        self.page.on_disconnect = self._on_page_disconnect
        self.page.on_close = self._on_page_close
        log_debug(Component.APP, "Window and page lifecycle handlers set")

        # Window icon (best-effort — icon file is optional)
        icon_path = self._get_icon_path()
        if icon_path:
            self.page.window.icon = icon_path
            log_debug(Component.APP, "Window icon set", icon_path=icon_path)
        else:
            log_debug(Component.APP, "No icon file found")

        # Dark theme
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.theme = ft.Theme(
            color_scheme_seed="#7c3aed",
        )
        log_debug(Component.APP, "Dark theme applied")

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
            on_lap_update=self._on_lap_update,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
            on_session_end=self._on_car_removed,
            on_session_restart=self._on_session_restart,
            session_manager=self._session_manager,
        )

    def _init_telemetry_services(self):
        """Initialize telemetry capture and analyzer services.

        The SHM capture loop is always created so that real-time lap
        validity, timing, and fuel data flow to the shared session
        regardless of the user's telemetry-recording preference.
        When ``telemetry_enabled`` is False the capture runs in
        *validity-only* mode: it reads all SHM regions and pushes data
        to the shared session but does NOT accumulate frames in memory
        and does NOT produce HTML/AI-prompt analysis outputs.
        """
        try:
            # Always create the capture service so SHM validity data
            # reaches the shared session even when telemetry is off.
            self._telemetry_capture = TelemetryCapture(
                hz=10.0,
                output_dir=self._config.telemetry_output_path,
                debug_logs=self._config.telemetry_debug_logs,
                session_manager=self._session_manager,
                record_frames=self._config.telemetry_enabled,
            )
            # Set up auto-stop callback to trigger analysis
            self._telemetry_capture.set_on_stop_callback(self._on_telemetry_auto_stop)

            if self._config.telemetry_enabled:
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
                    "Telemetry services initialized (full recording)",
                    output=self._config.telemetry_output_path,
                )
            else:
                log_info(
                    Component.APP,
                    "Telemetry services initialized (validity-only mode — "
                    "SHM lap validity active, frame recording disabled)",
                )
        except Exception as e:
            log_exception(Component.APP, "Failed to initialize telemetry", e)
            self._telemetry_capture = None
            self._telemetry_analyzer = None
        finally:
            session_lifecycle = getattr(self, "_session_lifecycle_service", None)
            if session_lifecycle is not None:
                session_lifecycle.set_telemetry_capture(self._telemetry_capture)

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
        import os
        import subprocess

        log_debug(Component.APP, "Open telemetry location requested", output_path=output_path)

        try:
            if not output_path:
                log_warning(Component.APP, "No telemetry output path configured")
                if self.page:
                    show_snackbar(
                        self.page,
                        "Telemetry output path not configured",
                        "#dc2626",
                    )
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
                show_snackbar(self.page, f"Failed to open folder: {ex}", "#dc2626")

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
            # The page instance is reused across navigation. Reload controls
            # from the active config so edits abandoned without Save never
            # reappear when Settings is opened again.
            self._settings_page.update_config(self._config)
            self.page.add(self._settings_page)
        elif page == AppPage.HISTORY:
            self._history_page.set_entries(self._history_entries)
            self.page.add(self._history_page)

    def _prune_history_entry_bindings(self) -> None:
        """Drop bindings whose history entries are no longer retained."""
        bindings = getattr(self, "_history_entry_by_lap_id", None)
        if not bindings:
            return

        retained_entry_ids = {id(entry) for entry in self._history_entries}
        for lap_id, (_lap_ref, entry_ref) in list(bindings.items()):
            entry = entry_ref() if callable(entry_ref) else entry_ref
            if entry is None or id(entry) not in retained_entry_ids:
                bindings.pop(lap_id, None)

    def _bind_history_entry_to_lap(self, lap: LapData, entry: HistoryEntry) -> None:
        """Remember the exact history entry created for ``lap``.

        The weak references are paired with an identity check when resolving,
        so an ``id`` reused by Python cannot accidentally target a different
        lap object.
        """
        bindings = getattr(self, "_history_entry_by_lap_id", None)
        if bindings is None:
            bindings = self._history_entry_by_lap_id = {}

        lap_id = id(lap)

        def _remove_if_current(_ref) -> None:
            current = bindings.get(lap_id)
            if current is not None and (
                current[0] is _ref or current[1] is _ref
            ):
                bindings.pop(lap_id, None)

        try:
            lap_ref = weakref.ref(lap, _remove_if_current)
        except TypeError:
            # LapData is weak-referenceable in production.  Keep this small
            # fallback for test doubles/custom callbacks that are not.
            lap_ref = lambda: lap

        try:
            entry_ref = weakref.ref(entry, _remove_if_current)
        except TypeError:
            entry_ref = lambda: entry

        bindings[lap_id] = (lap_ref, entry_ref)

    def _get_history_entry_for_lap(self, lap: LapData) -> Optional[HistoryEntry]:
        """Resolve the retained history entry originating from ``lap``."""
        self._prune_history_entry_bindings()
        bindings = getattr(self, "_history_entry_by_lap_id", None) or {}
        binding = bindings.get(id(lap))
        if binding is None:
            return None

        lap_ref, entry_ref = binding
        bound_lap = lap_ref() if callable(lap_ref) else lap_ref
        entry = entry_ref() if callable(entry_ref) else entry_ref
        if bound_lap is not lap or entry is None or not any(
            retained is entry for retained in self._history_entries
        ):
            bindings.pop(id(lap), None)
            return None
        return entry

    def _on_retry_lap(self, card: LapCard):
        """Retry submission for a failed lap card."""
        if not card.data.lap.is_valid and not self._config.submit_invalid_laps:
            return

        history_entry = self._get_history_entry_for_lap(card.data.lap)
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
            self._prune_history_entry_bindings()
            existing_entry_ids = {id(entry) for entry in self._history_entries}
            updated_track = await self._lap_processing_service.handle_lap_complete(
                session=session,
                lap=lap,
                home_page=self._home_page,
                telemetry_capture=self._telemetry_capture,
                config=self._config,
                session_manager=self._session_manager,
                pb_cache=self._pb_cache,
                history_entries=self._history_entries,
                schedule_submission=self._schedule_lap_submission,
                create_history_entry=HistoryEntry,
            )
            # LapProcessingService appends exactly one entry for a presented
            # timed lap.  Capture the object it appended before any later
            # session can reuse ACE's lap number.
            new_entries = [
                entry
                for entry in self._history_entries
                if id(entry) not in existing_entry_ids
            ]
            if len(new_entries) == 1:
                self._bind_history_entry_to_lap(lap, new_entries[0])
            self._prune_history_entry_bindings()
            if updated_track is not None:
                self._current_track_name = updated_track
        except Exception as e:
            log_exception(Component.APP, "_on_lap_complete failed", e)

    async def _on_lap_update(self, session: SessionData, lap: LapData):
        """Refresh a SHM-first lap after ACE eventually flushes its log data."""
        if self._home_page:
            self._home_page.refresh_lap(lap)
        history_entry = self._get_history_entry_for_lap(lap)
        if history_entry is not None:
            history_entry.lap_time_ms = lap.lap_time_ms
            history_entry.timestamp = lap.timestamp
            history_entry.was_valid = lap.is_valid

    def _schedule_lap_submission(
        self,
        card,
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
        pb_was_new: Optional[bool] = None,
    ) -> None:
        """Hand submission off so log parsing can enrich the shared lap first."""
        self.page.run_task(
            self._submit_lap,
            card,
            session,
            lap,
            history_entry,
            pb_was_new,
        )

    async def _submit_lap(
        self,
        card,
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
        pb_was_new: Optional[bool] = None,
    ):
        """Submit a lap to the server."""
        await self._lap_submission_service.submit_lap(
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
        await self._lap_submission_service.post_to_discord(
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
        """Delegate the player-car removal boundary."""
        try:
            await self._session_lifecycle_service.handle_car_removed()
        finally:
            self._prune_history_entry_bindings()

    async def _on_session_restart(self):
        """Delegate the pause-menu restart boundary."""
        try:
            await self._session_lifecycle_service.handle_session_restart()
        finally:
            self._prune_history_entry_bindings()

    async def _on_game_status_change(self, is_running: bool):
        """Handle game running status change."""
        try:
            await self._session_lifecycle_service.handle_game_status_change(is_running)
        finally:
            self._prune_history_entry_bindings()

    async def _start_telemetry_capture(self):
        """Start telemetry capture when game session begins."""
        await self._telemetry_lifecycle_service.start_capture(
            telemetry_capture=self._telemetry_capture,
            home_page=self._home_page,
            telemetry_enabled=self._config.telemetry_enabled,
        )

    async def _on_telemetry_auto_stop(self, reason: str):
        """Handle automatic stop of telemetry capture (game crash/quit detected)."""
        await self._telemetry_lifecycle_service.handle_auto_stop(
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
        await self._telemetry_lifecycle_service.stop_capture(
            reason=reason,
            discard=discard,
            telemetry_capture=self._telemetry_capture,
            telemetry_analyzer=self._telemetry_analyzer,
            home_page=self._home_page,
            current_track_name=self._current_track_name,
        )

    async def _on_user_detected(self, steam_id: str, player_name: Optional[str]):
        """Handle user detection from log parser."""
        await self._user_bootstrap_service.handle_detected_user(
            app=self,
            steam_id=steam_id,
            player_name=player_name,
            create_discord_notifier=create_discord_notifier,
        )

    async def _bootstrap_startup_user(self, steam_id: Optional[str], steam_name: Optional[str]) -> None:
        """Handle startup-time user bootstrap from registry detection."""
        await self._user_bootstrap_service.handle_startup_user(
            app=self,
            steam_id=steam_id,
            steam_name=steam_name,
            create_discord_notifier=create_discord_notifier,
        )

    async def _on_game_version(self, version: str):
        """Handle game version detection from log parser."""
        if self._home_page:
            self._home_page.set_game_version(version)

    async def _on_window_event(self, e):
        """Handle native desktop window events."""
        event_type = getattr(e, "type", None)
        if event_type in (ft.WindowEventType.CLOSE, ft.WindowEventType.CLOSE.value):
            await self._cleanup()

    async def _on_page_disconnect(self, e=None):
        """Best-effort cleanup when the page session disconnects."""
        await self._cleanup()

    async def _on_page_close(self, e=None):
        """Best-effort cleanup scheduled when a page session expires."""
        await self._cleanup()

    async def _on_window_close(self, e=None):
        """Backward-compatible alias for the native close callback."""
        await self._cleanup()

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
        self._settings_service.apply(
            app=self,
            config=config,
            create_discord_notifier=create_discord_notifier,
            get_pb_cache_for_server=lambda url: PBCache(url),
            create_api_client=APIClient,
            create_log_parser=self._create_log_parser,
        )

    async def _test_discord_webhook(self, webhook_url: str) -> tuple[bool, str]:
        """Test Discord webhook connection."""
        # Settings fields are intentionally transactional. Build a short-lived
        # notifier from the value currently in the form rather than using the
        # saved runtime notifier, which may point at an entirely different
        # webhook (or not exist yet).
        try:
            notifier = create_discord_notifier(webhook_url)
        except Exception:
            # Keep malformed/custom factory failures generic too. In
            # particular, never put a webhook token into an error string.
            log_error(Component.DISCORD, "Discord webhook test failed")
            return False, "Invalid Discord webhook URL"
        if notifier is None:
            return False, "Invalid Discord webhook URL"

        try:
            success = await notifier.send_test_message()
        except Exception:
            # Never surface exception text: HTTP errors can contain the full
            # webhook URL and its token.
            log_error(Component.DISCORD, "Discord webhook test failed")
            success = False

        if success:
            show_snackbar(self.page, "Test message sent successfully!", "#51cf66")
            return True, "Test message sent successfully"

        show_snackbar(self.page, "Failed to send test message", "#ff6b6b")
        return False, "Failed to send test message"

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
        async with APIClient(server_url=server_url) as test_client:
            return await test_client.test_connection()

    async def _cleanup(self):
        """Cleanup resources before exit."""
        await self._app_lifecycle_service.cleanup(app=self)


async def main(page: ft.Page):
    """Application entry point for Flet."""
    # Flet owns the event loop used by this callback. Install the handler on
    # that running loop so exceptions from application background tasks are
    # routed to the structured logger.
    loop = asyncio.get_running_loop()

    def handle_asyncio_exception(loop, context):
        msg = context.get("message", "Unhandled exception in asyncio task")
        exception = context.get("exception")
        if exception:
            log_exception(Component.APP, msg, exception)
        else:
            log_error(Component.APP, msg)

    loop.set_exception_handler(handle_asyncio_exception)

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
