"""
Home Page - Dashboard showing game status, recent laps, and submission status.

No login required - detects user from game logs automatically.
"""

import flet as ft
import os
import sys
from typing import Optional, Callable
from collections import deque

from ..components.lap_card import LapCard, LapCardData, LapCardStatus
from ..components.status_bar import StatusBar, ConnectionStatus
from ..components.telemetry_status import TelemetryStatusIndicator, TelemetryStatus
from ...models import SessionData, LapData
from ...core.api_client import SubmissionStatus
from ...utils.config import AppConfig
from ...version import GAME_DISPLAY_NAME


UPDATE_DOWNLOAD_URL = "https://simlaps.racing/download"


def get_icon_path() -> Optional[str]:
    """Get the path to the app icon (PNG for ft.Image)."""
    if getattr(sys, 'frozen', False):
        # Running as compiled executable - check _MEIPASS for bundled files
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, "assets", "icon.png")
            if os.path.exists(icon_path):
                return icon_path
        # Fallback to executable directory
        base_path = os.path.dirname(sys.executable)
    else:
        # Running as script - go up from src/ui/pages to project root
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    # Try assets/icon.png (PNG works better with ft.Image)
    icon_path = os.path.join(base_path, "assets", "icon.png")
    if os.path.exists(icon_path):
        return icon_path
    
    return None


class HomePage(ft.Column):
    """
    Home page displaying game status, detected user, and recent laps.
    
    No authentication required - Steam ID is detected from game logs.
    """
    
    MAX_VISIBLE_LAPS = 10
    
    def __init__(
        self,
        config: AppConfig,
        on_settings_click: Optional[Callable] = None,
        on_history_click: Optional[Callable] = None,
        on_pb_cache_click: Optional[Callable] = None,
        on_retry_lap: Optional[Callable[[LapCard], None]] = None,
    ):
        self.config = config
        self.on_settings_click = on_settings_click
        self.on_history_click = on_history_click
        self.on_pb_cache_click = on_pb_cache_click
        self.on_retry_lap = on_retry_lap
        
        # Game state
        self._game_running = False
        self._detected_steam_id: Optional[str] = None
        self._detected_player_name: Optional[str] = None
        self._game_version: Optional[str] = None
        
        # Lap cards storage (most recent first)
        self._lap_cards: deque[LapCard] = deque(maxlen=self.MAX_VISIBLE_LAPS)
        self._lap_count = 0
        
        # UI Components - create them first
        self._game_version_text = ft.Text(
            GAME_DISPLAY_NAME,
            size=10,
            color="#666666",
        )
        self._status_text = ft.Text(
            "Waiting for game to start...",
            size=13,
            color="#888888",
        )
        self._laps_column = ft.Column(
            controls=[],
            scroll=ft.ScrollMode.AUTO,
            spacing=8,
            expand=True,
        )
        self._status_bar = StatusBar()
        self._game_status_container = ft.Container()
        
        # Telemetry components
        self._telemetry_status = TelemetryStatusIndicator()
        self._telemetry_button = None  # Will be set by app
        
        # Build initial game status
        self._update_game_status_ui()
        self._update_laps_ui()
        
        # Initialize Column with all controls
        super().__init__(
            controls=self._build_controls(),
            expand=True,
            spacing=0,
        )

    def did_mount(self):
        """Called when added to page."""
        super().did_mount()
        # Check for updates once mounted
        self._check_for_updates()
    
    def _update_game_status_ui(self):
        """Update the game status card content."""
        if self._game_running:
            # Game is running - show detected user info
            user_info_controls = []
            if self._detected_player_name:
                user_info_controls.append(
                    ft.Text(self._detected_player_name, size=16, weight=ft.FontWeight.W_600, color="#ffffff")
                )
            if self._detected_steam_id:
                user_info_controls.append(
                    ft.Text(f"Steam ID: {self._detected_steam_id}", size=12, color="#888888")
                )
            if not user_info_controls:
                user_info_controls = [ft.Text("Detecting player...", size=14, color="#888888")]
            
            self._game_status_container.content = ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.PLAY_CIRCLE, color="#51cf66", size=32),
                        width=56, height=56, border_radius=28, bgcolor="#1f3d1f",
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column(
                        controls=[
                            ft.Row([
                                ft.Container(width=10, height=10, border_radius=5, bgcolor="#51cf66"),
                                ft.Text("Game Running", size=12, color="#51cf66", weight=ft.FontWeight.W_600),
                            ], spacing=6),
                            *user_info_controls,
                        ],
                        spacing=4,
                        expand=True,
                    ),
                ],
                spacing=16,
            )
            self._game_status_container.padding = 16
            self._game_status_container.bgcolor = "#1e1e2e"
            self._game_status_container.border_radius = 12
            self._game_status_container.border = ft.border.all(1, "#51cf66")
        else:
            # Monitoring - show detected user if available
            if self._detected_steam_id:
                # We have user info - show monitoring state with user
                user_info_controls = []
                if self._detected_player_name:
                    user_info_controls.append(
                        ft.Text(self._detected_player_name, size=16, weight=ft.FontWeight.W_600, color="#ffffff")
                    )
                user_info_controls.append(
                    ft.Text(f"Steam ID: {self._detected_steam_id}", size=12, color="#888888")
                )
                
                self._game_status_container.content = ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Icon(ft.Icons.MONITOR, color="#ffd43b", size=32),
                            width=56, height=56, border_radius=28, bgcolor="#3d3d1f",
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Column(
                            controls=[
                                *user_info_controls,
                            ],
                            spacing=4,
                            expand=True,
                        ),
                    ],
                    spacing=16,
                )
                self._game_status_container.padding = 16
                self._game_status_container.bgcolor = "#1e1e2e"
                self._game_status_container.border_radius = 12
                self._game_status_container.border = ft.border.all(1, "#ffd43b")
            else:
                # No user info yet - show waiting state
                self._game_status_container.content = ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.MONITOR, color="#666666", size=48),
                        ft.Text("Monitoring Log File", size=16, weight=ft.FontWeight.W_600, color="#ffffff"),
                        ft.Text("Waiting for session to start...", size=12, color="#888888", text_align=ft.TextAlign.CENTER),
                        ft.Container(height=8),
                        ft.Row([
                            ft.Container(width=8, height=8, border_radius=4, bgcolor="#ffd43b"),
                            ft.Text("Ready", size=11, color="#ffd43b"),
                        ], spacing=8, alignment=ft.MainAxisAlignment.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                )
                self._game_status_container.padding = 24
                self._game_status_container.border = ft.border.all(1, "#3d3d5c")
            self._game_status_container.bgcolor = "#1e1e2e"
            self._game_status_container.border_radius = 12
            self._game_status_container.alignment = ft.Alignment(0, 0)

    def _update_laps_ui(self):
        """Update the laps column content."""
        if not self._lap_cards:
            self._laps_column.controls = [
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.SPEED, color="#444444", size=48),
                        ft.Text("No laps recorded yet", size=14, color="#666666"),
                        ft.Text("Complete a lap in-game to see it here", size=12, color="#444444"),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                    padding=32,
                    alignment=ft.Alignment(0, 0),
                ),
            ]
        else:
            self._laps_column.controls = list(self._lap_cards)
            
        # Update counter text
        if hasattr(self, '_lap_count_text'):
            self._lap_count_text.value = f"({self._lap_count} total)"
            if self.page:
                self._lap_count_text.update()
    
    def _build_controls(self) -> list:
        """Build the page controls."""
        # Header with custom icon
        icon_path = get_icon_path()
        if icon_path:
            header_icon = ft.Image(src=icon_path, width=32, height=32)
        else:
            header_icon = ft.Icon(ft.Icons.TIMER, color="#7c3aed", size=32)
        
        header = ft.Container(
            content=ft.Row([
                header_icon,
                ft.Column([
                    ft.Text("SimLaps", size=24, weight=ft.FontWeight.W_700, color="#ffffff"),
                    self._game_version_text,
                ], spacing=0),
                ft.Container(expand=True),
            ], spacing=12),
            padding=ft.padding.only(left=20, right=20, top=20, bottom=16),
            bgcolor="#0f0f1a",
        )
        
        # Update notification banner
        self._update_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.NEW_RELEASES, color="#ffffff", size=20),
                ft.Column([
                    ft.Text("Update Available", size=14, weight=ft.FontWeight.W_600, color="#ffffff"),
                    ft.Text("Get the latest version at:", size=12, color="#ffffff"),
                    ft.Text(UPDATE_DOWNLOAD_URL, size=12, color="#a5b4fc", selectable=True),
                ], spacing=2, expand=True),
                ft.TextButton(
                    "Download",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=self._open_update_url,
                    style=ft.ButtonStyle(color="#ffffff"),
                ),
            ], alignment=ft.MainAxisAlignment.START),
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            bgcolor="#7c3aed",
            border_radius=8,
            margin=ft.margin.only(left=20, right=20, top=0, bottom=16),
            visible=False,  # Hidden by default
        )
        
        # Status section
        status_section = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.INFO, color="#888888", size=16),
                    self._status_text,
                ], spacing=8),
                self._telemetry_status,
            ], spacing=8),
            padding=ft.padding.symmetric(vertical=12, horizontal=16),
            bgcolor="#16162a",
            border_radius=8,
            margin=ft.margin.only(left=20, right=20, top=0, bottom=16),
        )
        
        # Laps header
        self._lap_count_text = ft.Text(f"({self._lap_count} total)", size=12, color="#888888")
        
        laps_header = ft.Container(
            content=ft.Row([
                ft.Text("Recent Laps", size=16, weight=ft.FontWeight.W_600, color="#ffffff"),
                self._lap_count_text,
            ], spacing=8),
            padding=ft.padding.only(left=20, right=20, bottom=8),
            bgcolor="#0f0f1a",
        )
        
        # Laps list container
        laps_container = ft.Container(
            content=self._laps_column,
            expand=True,
            padding=ft.padding.only(left=20, right=20),
            bgcolor="#0f0f1a",
        )
        
        # Buttons
        buttons = ft.Container(
            content=ft.Row([
                ft.OutlinedButton(
                    "Settings",
                    icon=ft.Icons.SETTINGS,
                    on_click=self._handle_settings_click,
                    style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
                ),
                ft.OutlinedButton(
                    "Submission History",
                    icon=ft.Icons.HISTORY,
                    on_click=self._handle_history_click,
                    style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
                ),
                ft.OutlinedButton(
                    "View PB Cache",
                    icon=ft.Icons.LIST_ALT,
                    on_click=self._handle_pb_cache_click,
                    style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
                ),
                ft.OutlinedButton(
                    "Logs",
                    icon=ft.Icons.BUG_REPORT,
                    on_click=self._handle_logs_click,
                    style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.padding.only(left=20, right=20, top=16, bottom=16),
            bgcolor="#0f0f1a",
        )
        
        # Telemetry button (added dynamically)
        self._telemetry_button_container = ft.Container()
        
        # Game status container wrapper
        game_status_wrapper = ft.Container(
            content=self._game_status_container,
            padding=ft.padding.only(left=20, right=20),
            bgcolor="#0f0f1a",
        )
        
        return [
            header,
            game_status_wrapper,
            ft.Container(height=16),
            self._update_banner,
            status_section,
            laps_header,
            laps_container,
            self._telemetry_button_container,
            buttons,
            self._status_bar,
        ]

    def _check_for_updates(self):
        """Check for updates in background."""
        import asyncio
        async def check():
            from ...core.api_client import APIClient
            try:
                async with APIClient(server_url=self.config.server_url) as client:
                    result = await client.check_for_updates()
                    if result.get("available"):
                        self._update_banner.visible = True
                        if self.page:
                            self._update_banner.update()
            except Exception as e:
                print(f"Update check failed: {e}")

        # Run in background if page is available
        if self.page:
            self.page.run_task(check)

    def _open_update_url(self, _=None):
        """Open browser to download update."""
        if self.page:
            self.page.launch_url(UPDATE_DOWNLOAD_URL)
    
    def _handle_settings_click(self, e):
        """Handle Settings button click."""
        print(f"Settings button clicked! Callback exists: {self.on_settings_click is not None}")
        if self.on_settings_click:
            self.on_settings_click()
        else:
            print("No callback registered for Settings")
    
    def _handle_history_click(self, e):
        """Handle Submission History button click."""
        print(f"History button clicked! Callback exists: {self.on_history_click is not None}")
        if self.on_history_click:
            self.on_history_click()
        else:
            print("No callback registered for History")
    
    def _handle_pb_cache_click(self, e):
        """Handle View PB Cache button click."""
        print(f"PB Cache button clicked! Callback exists: {self.on_pb_cache_click is not None}")
        if self.on_pb_cache_click:
            print("Calling callback...")
            self.on_pb_cache_click()
        else:
            print("No callback registered for PB Cache")
            # Show a message to user if no callback is set
            if self.page:
                self.page.show_snack_bar(
                    ft.SnackBar(
                        content=ft.Text("PB Cache view is not configured yet"),
                        action="OK"
                    )
                )
    
    def _handle_logs_click(self, e):
        """Handle Logs button click."""
        from ...utils.structured_logger import log_info, log_exception, Component
        
        log_info(Component.HOME, "Logs button clicked")
        try:
            from ..components.debug_logs import show_debug_logs
            show_debug_logs(self.page)
            log_info(Component.HOME, "Debug logs dialog shown")
        except Exception as ex:
            log_exception(Component.HOME, "Error showing debug logs", ex)
    
    def update_config(self, config: AppConfig):
        """Update with new config and refresh UI."""
        self.config = config
    
    def set_status(self, message: str):
        """Update the status message."""
        self._status_text.value = message
        if self.page:
            self._status_text.update()
    
    def set_connection_status(self, status: ConnectionStatus, message: str):
        """Update the connection status bar."""
        self._status_bar.set_status(status, message)
    
    def set_game_running(self, is_running: bool):
        """Update game running status and refresh UI."""
        if self._game_running != is_running:
            self._game_running = is_running
            self._update_game_status_ui()
            if self.page:
                self._game_status_container.update()
    
    def set_detected_user(self, steam_id: Optional[str], player_name: Optional[str] = None):
        """Update detected user information."""
        changed = (self._detected_steam_id != steam_id or self._detected_player_name != player_name)
        if changed:
            self._detected_steam_id = steam_id
            self._detected_player_name = player_name
            self._update_game_status_ui()
            if self.page:
                self._game_status_container.update()
    
    def set_game_version(self, version: str):
        """Update the detected game version."""
        if self._game_version != version:
            self._game_version = version
            self._game_version_text.value = f"{GAME_DISPLAY_NAME} {version}"
            if self.page:
                self._game_version_text.update()
    
    def add_lap(
        self,
        session: SessionData,
        lap: LapData,
        status: LapCardStatus = LapCardStatus.PENDING,
    ) -> LapCard:
        """Add a new lap card to the display."""
        self._lap_count += 1
        
        if session.player_id and not self._detected_steam_id:
            self.set_detected_user(session.player_id, session.player_name)
        
        card_data = LapCardData(
            session=session,
            lap=lap,
            lap_number=self._lap_count,
            status=status,
        )
        
        card = LapCard(data=card_data, on_retry=self._on_retry_lap)
        self._lap_cards.appendleft(card)
        
        self._update_laps_ui()
        if self.page:
            self._laps_column.update()
        
        return card
    
    def update_lap_status(self, card: LapCard, status: LapCardStatus, error_message: Optional[str] = None):
        """Update a lap card's status."""
        card.update_status(status, error_message)
    
    def _on_retry_lap(self, card: LapCard):
        """Handle retry button click on failed lap."""
        if not card.data.lap.is_valid and not self.config.submit_invalid_laps:
            return

        if self.on_retry_lap:
            self.on_retry_lap(card)
    
    def clear_laps(self):
        """Clear all lap cards."""
        self._lap_cards.clear()
        self._lap_count = 0
        self._update_laps_ui()
        if self.page:
            self._laps_column.update()
    
    def get_status_bar(self) -> StatusBar:
        """Get the status bar component."""
        return self._status_bar
    
    def set_telemetry_status(
        self,
        status: TelemetryStatus,
        frame_count: int = 0,
        result_path: str = None,
    ):
        """Update the telemetry status indicator."""
        self._telemetry_status.set_status(status, frame_count, result_path)
    
    def set_telemetry_button(self, button, output_path: str):
        """Set the telemetry button and update its path."""
        print(f"[HOME] set_telemetry_button called: button={button}, output_path={output_path}")
        if button is not None:
            print(f"[HOME] Button on_click before setting: {button.on_click}")
        
        self._telemetry_button = button
        if button is None:
            self._telemetry_button_container.content = None
        else:
            self._telemetry_button.update_path(output_path)
            print(f"[HOME] Button on_click after update_path: {button.on_click}")
            self._telemetry_button_container.content = ft.Container(
                content=button,
                padding=ft.padding.only(left=20, right=20, bottom=8),
                bgcolor="#0f0f1a",
            )
        try:
            if self.page:
                self._telemetry_button_container.update()
        except RuntimeError:
            pass
