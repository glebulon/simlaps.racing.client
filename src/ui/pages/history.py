"""
History Page - View past lap submissions.
"""

import flet as ft
from typing import Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime

from ...utils.helpers import format_lap_time, format_car_name, format_track_name


@dataclass
class HistoryEntry:
    """A historical lap entry."""
    track: str
    car: str
    lap_time_ms: int
    timestamp: str
    was_submitted: bool
    was_valid: bool


class HistoryPage(ft.Container):
    """
    History page showing past lap submissions.
    """
    
    def __init__(
        self,
        on_back: Optional[Callable] = None,
    ):
        self.on_back = on_back
        self._entries: List[HistoryEntry] = []
        
        self._list_view = ft.ListView(
            expand=True,
            spacing=8,
            padding=0,
        )
        
        super().__init__(
            content=self._build_content(),
            expand=True,
        )
    
    def _build_content(self) -> ft.Control:
        """Build the history page content."""
        # Header with back button
        header = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color="#ffffff",
                    on_click=lambda _: self.on_back() if self.on_back else None,
                ),
                ft.Text(
                    "Lap Submission",
                    size=24,
                    weight=ft.FontWeight.W_700,
                    color="#ffffff",
                    expand=True,
                ),
            ],
            spacing=8,
        )
        
        # Stats summary
        stats = self._build_stats()
        
        # Update list view content
        self._update_list_view()
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Container(height=16),
                    stats,
                    ft.Container(height=16),
                    ft.Container(
                        content=self._list_view,
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            padding=20,
            bgcolor="#0f0f1a",
            expand=True,
        )
    
    def _build_stats(self) -> ft.Container:
        """Build statistics summary."""
        total = len(self._entries)
        submitted = sum(1 for e in self._entries if e.was_submitted)
        valid = sum(1 for e in self._entries if e.was_valid)
        
        # Debug logging
        from ...utils.structured_logger import log_debug, Component
        log_debug(Component.HISTORY, "History stats", total=total, submitted=submitted, valid=valid)
        for i, entry in enumerate(self._entries):
            log_debug(Component.HISTORY, f"Entry {i}", submitted=entry.was_submitted, valid=entry.was_valid)
        
        return ft.Container(
            content=ft.Row(
                controls=[
                    self._build_stat("Total Laps (found in logs)", str(total), ft.Icons.FLAG),
                    ft.Container(width=1, height=40, bgcolor="#3d3d5c"),
                    self._build_stat("Submitted (to server)", str(submitted), ft.Icons.CLOUD_UPLOAD),
                    ft.Container(width=1, height=40, bgcolor="#3d3d5c"),
                    self._build_stat("Valid (No Penalties)", str(valid), ft.Icons.CHECK_CIRCLE),
                ],
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
            ),
            padding=16,
            bgcolor="#1e1e2e",
            border_radius=12,
            border=ft.border.all(1, "#3d3d5c"),
        )
    
    def _build_stat(self, label: str, value: str, icon) -> ft.Column:
        """Build a stat display."""
        return ft.Column(
            controls=[
                ft.Icon(icon, color="#7c3aed", size=24),
                ft.Text(
                    value,
                    size=20,
                    weight=ft.FontWeight.W_700,
                    color="#ffffff",
                ),
                ft.Text(
                    label,
                    size=12,
                    color="#888888",
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        )
    
    def _build_entry_row(self, entry: HistoryEntry) -> ft.Container:
        """Build a history entry row."""
        # Parse timestamp for display
        try:
            dt = datetime.fromisoformat(entry.timestamp)
            time_str = dt.strftime("%H:%M")
            date_str = dt.strftime("%b %d")
        except (TypeError, ValueError):
            time_str = "--:--"
            date_str = "---"
        except Exception as ex:
            from ...utils.structured_logger import log_exception, Component

            log_exception(
                Component.HISTORY,
                "Unexpected error parsing history timestamp",
                ex,
                timestamp=entry.timestamp,
            )
            time_str = "--:--"
            date_str = "---"
        
        status_icon = ft.Icons.CHECK_CIRCLE if entry.was_submitted else (
            ft.Icons.CANCEL if not entry.was_valid else ft.Icons.SCHEDULE
        )
        status_color = "#51cf66" if entry.was_submitted else (
            "#888888" if not entry.was_valid else "#ffd43b"
        )
        
        return ft.Container(
            content=ft.Row(
                controls=[
                    # Date/time
                    ft.Column(
                        controls=[
                            ft.Text(time_str, size=14, color="#ffffff", weight=ft.FontWeight.W_500),
                            ft.Text(date_str, size=11, color="#666666"),
                        ],
                        spacing=2,
                        width=50,
                    ),
                    # Track/Car
                    ft.Column(
                        controls=[
                            ft.Text(
                                format_track_name(entry.track),
                                size=14,
                                color="#ffffff",
                                weight=ft.FontWeight.W_500,
                            ),
                            ft.Text(
                                format_car_name(entry.car),
                                size=12,
                                color="#888888",
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    # Lap time
                    ft.Text(
                        format_lap_time(entry.lap_time_ms),
                        size=16,
                        weight=ft.FontWeight.W_600,
                        color="#ffffff" if entry.was_valid else "#666666",
                        font_family="monospace",
                    ),
                    # Status
                    ft.Icon(status_icon, color=status_color, size=20),
                ],
                spacing=12,
            ),
            padding=12,
            bgcolor="#1e1e2e",
            border_radius=8,
            border=ft.border.all(1, "#2d2d4a"),
        )
    
    def _update_list_view(self):
        """Update the list view with current entries."""
        if not self._entries:
            self._list_view.controls = [
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.HISTORY, color="#444444", size=48),
                            ft.Text(
                                "No history yet",
                                size=14,
                                color="#666666",
                            ),
                            ft.Text(
                                "Completed laps will appear here",
                                size=12,
                                color="#444444",
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    padding=48,
                    alignment=ft.Alignment(0, 0),
                ),
            ]
        else:
            self._list_view.controls = [
                self._build_entry_row(entry)
                for entry in reversed(self._entries)  # Most recent first
            ]
    
    def add_entry(self, entry: HistoryEntry):
        """Add a new history entry."""
        self._entries.append(entry)
        self._update_list_view()
        self.content = self._build_content()
        if self.page:
            self.update()
    
    def set_entries(self, entries: List[HistoryEntry]):
        """Set all history entries."""
        self._entries = list(entries)
        self._update_list_view()
        self.content = self._build_content()
        # Don't call update() here - will be updated when added to page
    
    def clear_entries(self):
        """Clear all history entries."""
        self._entries.clear()
        self._update_list_view()
        self.content = self._build_content()
        if self.page:
            self.update()
