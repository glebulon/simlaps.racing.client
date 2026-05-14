"""
Status Bar Component for displaying application status.
"""

import flet as ft
from typing import Optional
from enum import Enum
from ...version import get_version, GAME_NAME


class ConnectionStatus(Enum):
    """Connection status states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class StatusBar(ft.Container):
    """
    A status bar component showing connection status.
    """
    
    def __init__(self):
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._status_message = "Not connected"
        
        super().__init__(
            content=self._build_content(),
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            bgcolor="#1a1a2e",
            border=ft.border.only(top=ft.BorderSide(1, "#2d2d4a")),
        )
    
    def _get_status_color(self) -> str:
        """Get color based on connection status."""
        colors = {
            ConnectionStatus.DISCONNECTED: "#888888",
            ConnectionStatus.CONNECTING: "#ffd43b",
            ConnectionStatus.CONNECTED: "#51cf66",
            ConnectionStatus.ERROR: "#ff6b6b",
        }
        return colors.get(self._connection_status, "#888888")
    
    def _get_status_icon(self) -> ft.Control:
        """Get status indicator icon."""
        if self._connection_status == ConnectionStatus.CONNECTING:
            return ft.ProgressRing(
                width=12,
                height=12,
                stroke_width=2,
                color="#ffd43b",
            )
        else:
            return ft.Container(
                width=12,
                height=12,
                border_radius=6,
                bgcolor=self._get_status_color(),
            )
    
    def _build_content(self) -> ft.Control:
        """Build the status bar content."""
        return ft.Row(
            controls=[
                # Left side: Connection status only
                ft.Row(
                    controls=[
                        # Connection status
                        ft.Row(
                            controls=[
                                self._get_status_icon(),
                                ft.Text(
                                    self._status_message,
                                    size=12,
                                    color=self._get_status_color(),
                                ),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=16,
                ),
                # Right side: Version info
                ft.Row(
                    controls=[
                        ft.Text(
                            f"{GAME_NAME} v{get_version()}",
                            size=10,
                            color="#666666",
                            style=ft.TextStyle(italic=True),
                        ),
                    ],
                    spacing=4,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    
    def set_status(
        self,
        connection_status: Optional[ConnectionStatus] = None,
        message: Optional[str] = None,
    ):
        """
        Update the status bar.
        
        Args:
            connection_status: New connection status
            message: Status message to display
        """
        if connection_status is not None:
            self._connection_status = connection_status
        if message is not None:
            self._status_message = message
        
        self.content = self._build_content()
        self.update()
