"""
Lap Card Component for displaying individual lap times.
"""

import flet as ft
from typing import Callable, Optional
from dataclasses import dataclass
from enum import Enum

from ...models import LapData, SessionData
from ...core.api_client import SubmissionStatus
from ...utils.helpers import format_lap_time, format_sector_time, format_car_name, format_track_name


class LapCardStatus(Enum):
    """Status of the lap card."""
    PENDING = "pending"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    INVALID = "invalid"


@dataclass
class LapCardData:
    """Data for a lap card display."""
    session: SessionData
    lap: LapData
    lap_number: int
    status: LapCardStatus = LapCardStatus.PENDING
    error_message: Optional[str] = None


class LapCard(ft.Container):
    """
    A card component displaying a single lap time.
    
    Shows track, car, lap time, sectors, and submission status.
    """
    
    def __init__(
        self,
        data: LapCardData,
        on_retry: Optional[Callable[["LapCard"], None]] = None,
    ):
        self.data = data
        self.on_retry = on_retry
        
        super().__init__(
            content=self._build_content(),
            padding=16,
            margin=ft.margin.only(bottom=8),
            border_radius=12,
            bgcolor=self._get_bgcolor(),
            border=ft.border.all(1, self._get_border_color()),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
    
    def _get_bgcolor(self) -> str:
        """Get background color based on status."""
        if self.data.status == LapCardStatus.INVALID:
            return "#1a1a1a"
        elif self.data.status == LapCardStatus.FAILED:
            return "#2d1f1f"
        elif self.data.status == LapCardStatus.SUBMITTED:
            return "#1f2d1f"
        else:
            return "#1e1e2e"
    
    def _get_border_color(self) -> str:
        """Get border color based on status."""
        if self.data.status == LapCardStatus.INVALID:
            return "#444444"
        elif self.data.status == LapCardStatus.FAILED:
            return "#ff6b6b"
        elif self.data.status == LapCardStatus.SUBMITTED:
            return "#51cf66"
        elif self.data.status == LapCardStatus.SUBMITTING:
            return "#ffd43b"
        else:
            return "#3d3d5c"
    
    def _get_status_icon(self) -> ft.Control:
        """Get status icon."""
        if self.data.status == LapCardStatus.INVALID:
            return ft.Icon(ft.Icons.CANCEL, color="#888888", size=20)
        elif self.data.status == LapCardStatus.FAILED:
            return ft.Icon(ft.Icons.ERROR, color="#ff6b6b", size=20)
        elif self.data.status == LapCardStatus.SUBMITTED:
            return ft.Icon(ft.Icons.CHECK_CIRCLE, color="#51cf66", size=20)
        elif self.data.status == LapCardStatus.SUBMITTING:
            return ft.Icon(ft.Icons.SCHEDULE, color="#ffd43b", size=20)
        else:
            return ft.Icon(ft.Icons.SCHEDULE, color="#888888", size=20)
    
    def _build_content(self) -> ft.Control:
        """Build the card content."""
        lap = self.data.lap
        session = self.data.session
        
        # Header row with track, car, and status
        header = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(
                            format_track_name(session.track),
                            size=14,
                            weight=ft.FontWeight.W_600,
                            color="#ffffff",
                        ),
                        ft.Text(
                            format_car_name(session.car),
                            size=12,
                            color="#888888",
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                self._get_status_icon(),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        
        # Lap time (large, prominent)
        lap_time_display = ft.Container(
            content=ft.Text(
                format_lap_time(lap.lap_time_ms),
                size=32,
                weight=ft.FontWeight.W_700,
                color="#ffffff" if lap.is_valid else "#666666",
                font_family="monospace",
            ),
            margin=ft.margin.symmetric(vertical=12),
        )
        
        # Sector times
        sectors = ft.Row(
            controls=[
                self._build_sector("S1", lap.sector1_ms),
                ft.Container(width=1, height=20, bgcolor="#333333"),
                self._build_sector("S2", lap.sector2_ms),
                ft.Container(width=1, height=20, bgcolor="#333333"),
                self._build_sector("S3", lap.sector3_ms),
            ],
            alignment=ft.MainAxisAlignment.SPACE_AROUND,
        )
        
        # Footer with metadata
        footer_items = [
            ft.Text(f"Lap #{self.data.lap_number}", size=11, color="#666666"),
            ft.Text(f"Tires: {lap.tyre_compound}", size=11, color="#666666"),
        ]
        
        if not lap.is_valid:
            footer_items.append(
                ft.Text("INVALID", size=11, color="#ff6b6b", weight=ft.FontWeight.W_600)
            )
        
        footer = ft.Row(
            controls=footer_items,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        
        # Error message if failed
        content_controls = [header, lap_time_display, sectors, footer]
        
        if self.data.status == LapCardStatus.FAILED and self.data.error_message:
            error_row = ft.Row(
                controls=[
                    ft.Text(
                        self.data.error_message,
                        size=11,
                        color="#ff6b6b",
                        expand=True,
                    ),
                    ft.TextButton(
                        "Retry",
                        on_click=lambda _: self.on_retry(self) if self.on_retry else None,
                        style=ft.ButtonStyle(color="#ff6b6b"),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
            content_controls.append(error_row)
        
        return ft.Column(
            controls=content_controls,
            spacing=8,
        )
    
    def _build_sector(self, label: str, time_ms: Optional[int]) -> ft.Control:
        """Build a sector time display."""
        return ft.Column(
            controls=[
                ft.Text(label, size=10, color="#666666"),
                ft.Text(
                    format_sector_time(time_ms),
                    size=14,
                    weight=ft.FontWeight.W_500,
                    color="#cccccc" if time_ms else "#444444",
                    font_family="monospace",
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        )
    
    def update_status(self, status: LapCardStatus, error_message: Optional[str] = None):
        """Update the card status and refresh display."""
        if self.data is None:
            # Safety check - data somehow became None
            print(f"[ERROR] LapCard.data is None, cannot update status to {status}")
            return
        self.data.status = status
        self.data.error_message = error_message
        self.content = self._build_content()
        self.bgcolor = self._get_bgcolor()
        self.border = ft.border.all(1, self._get_border_color())
        if self.page:
            self.update()
