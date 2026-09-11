"""
ACE Log Parser Context

Persistent parsing context that survives across session boundaries.
"""

from typing import Optional

from .tyre_state import TyreState


class LogContext:
    """State that persists across session boundaries (player identity, etc.)."""

    def __init__(self) -> None:
        self.game_version: str = "Unknown"
        self.current_track: str = "Unknown"
        self.current_car: str = "Unknown"
        self.player_name: Optional[str] = None
        self.player_id: Optional[str] = None
        self.car_uuid: Optional[str] = None
        self.weather: str = "Unknown"
        self.car_is_hybrid: bool = False

        # Per-tire compound tracking (persists through session)
        self.tyre: TyreState = TyreState()

        # Fuel accounting
        # At race start the first Energy-source event is negative (tank fill);
        # store its abs value here to subtract from the first real consumption.
        self.fuel_init_correction: float = 0.0

        # Cumulative hundredmeters counter; delta gives per-lap distance.
        self.prev_hundredmeters: int = 0

        # car_uuid → {player_name, player_id} from 'connecting gamecar' lines
        self.car_meta: dict[str, dict] = {}

        # All car UUIDs that belong to this player (handles reconnections).
        self.player_car_uuids: set[str] = set()
        # Session setup map: setting name -> latest value.
        self.setup_values: dict[str, str] = {}

    def reset_for_new_session(self) -> None:
        self.tyre.reset()
        self.fuel_init_correction = 0.0
        self.prev_hundredmeters = 0
        self.player_car_uuids.clear()
        self.setup_values.clear()
        if self.car_uuid:
            self.player_car_uuids.add(self.car_uuid)
