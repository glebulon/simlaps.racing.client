"""
Telemetry Status Component

Displays telemetry capture status on the home page.
"""

import flet as ft
from enum import Enum


class TelemetryStatus(Enum):
    """Telemetry status states."""
    IDLE = "idle"
    CAPTURING = "capturing"
    ANALYZING = "analyzing"
    COMPLETE = "complete"
    ERROR = "error"


class TelemetryStatusIndicator(ft.Container):
    """Shows telemetry capture status."""

    def __init__(self):
        self._status = TelemetryStatus.IDLE
        self._frame_count = 0
        self._last_result_path = None

        self._status_icon = ft.Icons.RADIO_BUTTON_UNCHECKED
        self._status_color = "#666666"
        self._status_text = "Telemetry disabled"

        super().__init__(
            content=self._build_content(),
            padding=10,
            border_radius=8,
            bgcolor="#1e1e2e",
            visible=False,
        )

    def _build_content(self) -> ft.Control:
        self._icon = ft.Icon(self._status_icon, color=self._status_color, size=16)
        self._text = ft.Text(self._status_text, size=12, color=self._status_color)

        return ft.Row(
            controls=[
                self._icon,
                self._text,
            ],
            spacing=8,
        )

    def set_status(
        self,
        status: TelemetryStatus,
        frame_count: int = 0,
        result_path: str = None,
    ):
        """Update the telemetry status."""
        self._status = status
        self._frame_count = frame_count
        self._last_result_path = result_path

        if status == TelemetryStatus.IDLE:
            self._status_icon = ft.Icons.RADIO_BUTTON_UNCHECKED
            self._status_color = "#666666"
            self._status_text = "Telemetry idle"
            self.bgcolor = "#1e1e2e"
        elif status == TelemetryStatus.CAPTURING:
            self._status_icon = ft.Icons.FIBER_MANUAL_RECORD
            self._status_color = "#ef4444"
            self._status_text = f"Recording Telemetry ({frame_count} frames)"
            self.bgcolor = "#2d1f1f"
        elif status == TelemetryStatus.ANALYZING:
            self._status_icon = ft.Icons.AUTO_GRAPH
            self._status_color = "#f59e0b"
            self._status_text = "Analyzing telemetry..."
            self.bgcolor = "#2d2a1f"
        elif status == TelemetryStatus.COMPLETE:
            self._status_icon = ft.Icons.CHECK_CIRCLE
            self._status_color = "#22c55e"
            self._status_text = "Analysis complete"
            self.bgcolor = "#1f2d1f"
        elif status == TelemetryStatus.ERROR:
            self._status_icon = ft.Icons.ERROR
            self._status_color = "#ef4444"
            self._status_text = "Telemetry error"
            self.bgcolor = "#2d1f1f"

        self._icon.name = self._status_icon
        self._icon.color = self._status_color
        self._text.value = self._status_text
        self._text.color = self._status_color

        self.visible = status != TelemetryStatus.IDLE
        self.update()

    def show(self):
        """Show the indicator."""
        self.visible = True
        self.update()

    def hide(self):
        """Hide the indicator."""
        self.visible = False
        self.update()


class TelemetryButton(ft.Container):
    """Button to open telemetry output location."""

    def __init__(self, on_click=None, output_path: str = None):
        # Use _on_click_callback to avoid collision with parent Container.on_click
        self._on_click_callback = on_click
        self.output_path = output_path

        self._button = ft.ElevatedButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FOLDER_OPEN, size=16),
                    ft.Text("Open Telemetry Location", size=12),
                ],
                spacing=6,
            ),
            on_click=self._handle_click,
            style=ft.ButtonStyle(
                bgcolor="#1e1e2e",
                color="#888888",
                side=ft.BorderSide(1, "#3d3d5c"),
                padding=8,
            ),
        )

        super().__init__(
            content=self._button,
            padding=0,
        )

    def _handle_click(self, e):
        if self._on_click_callback:
            self._on_click_callback(e, self.output_path)

    def update_path(self, output_path: str):
        """Update the output path."""
        self.output_path = output_path
