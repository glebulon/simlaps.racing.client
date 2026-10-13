import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from ...utils.structured_logger import Component, log_warning
from ..components.mount_safe import safe_update

DEFAULT_RESULTS_DIR = Path.home() / "Saved Games" / "ACE" / "Results"


@dataclass
class MultiplayerSession:
    date: str
    server: str
    track: str
    layout: str
    kind: str
    drivers: list[dict]


def load_results(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[MultiplayerSession]:
    sessions = []
    for path in sorted(results_dir.glob("*_MP_*.sessionresults.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("drivers"), list):
                raise ValueError("Missing drivers list")
            date = datetime.strptime(path.name[:11], "%y%m%d-%H%M").strftime("%b %d, %Y %H:%M")
            sessions.append(
                MultiplayerSession(
                    date=date,
                    server=data.get("season", ""),
                    track=data.get("track", ""),
                    layout=data.get("layout", ""),
                    kind=data.get("session", ""),
                    drivers=data["drivers"],
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            log_warning(Component.UI, "Skipping invalid multiplayer result", file=str(path), error=str(exc))
    return sessions


class MultiplayerResultsPage(ft.Container):
    def __init__(self, on_back: Optional[Callable] = None, results_dir: Path = DEFAULT_RESULTS_DIR):
        self.on_back = on_back
        self.results_dir = results_dir
        self._sessions: list[MultiplayerSession] = []
        self._loaded = False
        self._selected: Optional[int] = None
        self._list_view = ft.ListView(expand=True, spacing=8)
        super().__init__(content=self._build_content(), expand=True)

    def load(self):
        if not self._loaded:
            self.refresh()

    def refresh(self, e=None):
        self._sessions = load_results(self.results_dir)
        self._loaded = True
        self._selected = None
        self._render()

    def _select_session(self, index: int):
        self._selected = index
        self._render()

    def _back(self, e=None):
        if self._selected is not None:
            self._selected = None
            self._render()
        elif self.on_back:
            self.on_back()

    def _render(self):
        self.content = self._build_content()
        safe_update(self)

    def _build_content(self):
        session = self._sessions[self._selected] if self._selected is not None else None
        title = "Multiplayer Results" if session is None else f"{session.track} · {session.kind}"
        self._list_view.controls = (
            [self._session_row(index, item) for index, item in enumerate(self._sessions)]
            if session is None
            else [self._driver_row(driver) for driver in session.drivers if isinstance(driver, dict)]
        )
        if session is None and not self._sessions:
            self._list_view.controls = [ft.Text("No multiplayer results found", color="#888888")]
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="#ffffff", on_click=self._back),
                            ft.Text(
                                title, size=18, weight=ft.FontWeight.W_700, color="#ffffff", expand=True, max_lines=2
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                icon_color="#ffffff",
                                tooltip="Refresh results",
                                on_click=self.refresh,
                            ),
                        ],
                    ),
                    ft.Text(
                        "Select a session to compare best valid laps"
                        if session is None
                        else f"{session.date} · {session.server} · {session.layout}",
                        size=12,
                        color="#888888",
                        max_lines=2,
                    ),
                    ft.Container(content=self._list_view, expand=True),
                ],
                expand=True,
                spacing=12,
            ),
            padding=20,
            bgcolor="#0f0f1a",
            expand=True,
        )

    def _session_row(self, index: int, session: MultiplayerSession):
        valid = sum(bool(driver.get("best_lap")) for driver in session.drivers if isinstance(driver, dict))
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(f"{session.date} · {session.kind}", size=14, color="#ffffff", weight=ft.FontWeight.W_600),
                    ft.Text(session.server, size=12, color="#aaaaaa", max_lines=2),
                    ft.Text(f"{session.track} · {session.layout}", size=12, color="#aaaaaa", max_lines=2),
                    ft.Text(f"{valid} valid times · {len(session.drivers)} drivers", size=11, color="#7c3aed"),
                ],
                spacing=4,
            ),
            padding=12,
            bgcolor="#1e1e2e",
            border_radius=8,
            border=ft.Border.all(1, "#2d2d4a"),
            on_click=lambda e: self._select_session(index),
        )

    def _driver_row(self, driver: dict):
        name = " ".join(
            part.strip() for part in (driver.get("driver_name", ""), driver.get("driver_surname", "")) if part.strip()
        )
        best_lap = driver.get("best_lap") or "No valid lap"
        gap = driver.get("diff_first", "") if driver.get("best_lap") else ""
        position = driver.get("position")
        place = str(position + 1) if isinstance(position, int) and position >= 0 else "—"
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(place, width=24, size=13, color="#7c3aed"),
                    ft.Column(
                        controls=[
                            ft.Text(name or "Unknown driver", size=13, color="#ffffff", weight=ft.FontWeight.W_600),
                            ft.Text(
                                f"{driver.get('car_name', '')} · {driver.get('laps', 0)} laps",
                                size=11,
                                color="#888888",
                                max_lines=2,
                            ),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(best_lap, size=12, color="#ffffff" if driver.get("best_lap") else "#888888"),
                            ft.Text(f"+{gap}" if gap else "", size=11, color="#888888"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                        spacing=2,
                    ),
                ],
                spacing=8,
            ),
            padding=10,
            bgcolor="#1e1e2e",
            border_radius=8,
        )
