"""
PB Cache Viewer - Shows personal best times loaded from server.

Displays all track/car combinations with their best lap times.
"""

import flet as ft

from ...core.pb_cache import PBCache
from ...utils.structured_logger import Component, log_debug


def show_pb_cache_dialog(page: ft.Page, pb_cache: PBCache):
    """Show a dialog with personal best cache contents."""
    log_debug(Component.UI, "show_pb_cache_dialog called")

    def _format_time(time_ms: int) -> str:
        """Format time in minutes:seconds.milliseconds."""
        total_seconds = time_ms / 1000
        minutes = int(total_seconds // 60)
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:06.3f}"

    def _close_dialog(e=None):
        """Close the dialog."""
        dialog.open = False
        page.update()

    # Get cache data
    cache_data = pb_cache.get_all_pbs()

    if not cache_data:
        content = ft.Container(
            content=ft.Column(
                [
                    ft.Text("No personal best data loaded", size=16, color="#888888"),
                    ft.Text("Try recording some laps or check server connection", size=12, color="#666666"),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.all(20),
            width=400,
            height=200,
        )
    else:
        # Build list of PB entries
        items = []
        for (track, car), pb_time in cache_data.items():
            items.append(
                ft.ListTile(
                    title=ft.Text(f"{track.replace('_', ' ').title()}", size=14),
                    subtitle=ft.Text(f"{car.replace('_', ' ').title()}", size=12, color="#888888"),
                    trailing=ft.Text(f"{_format_time(pb_time.best_time_ms)}", size=14, weight=ft.FontWeight.BOLD),
                )
            )

        content = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Personal Best Cache", size=18, weight=ft.FontWeight.BOLD),
                    ft.Divider(height=1),
                    ft.Container(
                        content=ft.ListView(
                            items,
                            height=300,
                            spacing=1,
                        ),
                        border=ft.Border.all(1, "#3d3d5c"),
                        border_radius=8,
                    ),
                ]
            ),
            padding=ft.Padding.all(20),
            width=500,
            height=400,
        )

    # Close button
    close_button = ft.Button(
        "Close",
        on_click=_close_dialog,
        style=ft.ButtonStyle(bgcolor="#7c3aed"),
    )

    # Show dialog
    log_debug(Component.UI, "Creating PB cache dialog")
    dialog = ft.AlertDialog(
        title=ft.Text("🏆 Personal Best Cache", size=20, weight=ft.FontWeight.BOLD),
        content=content,
        actions=[close_button],
        shape=ft.RoundedRectangleBorder(radius=12),
    )

    page.show_dialog(dialog)
