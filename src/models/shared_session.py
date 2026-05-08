"""Shared session data models and manager.

This module provides a single thread-safe session store used by log parsing,
shared-memory decoding, telemetry analysis, and API submission code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set
import threading
import uuid

from .lap import LapData, SessionData


@dataclass
class LapValidityData:
    """Lap validity information for lap posting."""

    lap_number: int
    is_valid: bool
    invalidation_reason: Optional[str] = None
    invalidation_timestamp: Optional[str] = None
    source: str = "shm_graphics"
    penalty_count: Optional[int] = None
    track_limit_violations: Optional[int] = None


@dataclass
class LapTimingData:
    """Lap timing information with source tracking."""

    lap_number: int
    current_lap_time_ms: Optional[int] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None
    source: str = "shm_graphics"
    lap_time_str: Optional[str] = None
    lap_completion_timestamp: Optional[str] = None


@dataclass
class FuelData:
    """Fuel information with source tracking."""

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None
    fuel_consumed_lap: Optional[float] = None
    source: str = "shm_graphics"


@dataclass
class PlayerIdentificationData:
    """Player identification from logs (SHM does not provide this)."""

    steam_id: Optional[str] = None
    player_name: Optional[str] = None
    car_uuid: Optional[str] = None
    car_model: Optional[str] = None
    source: str = "logs"


@dataclass
class SectorSplitData:
    """Sector split times from logs (SHM does not provide this)."""

    lap_number: int
    sector1_ms: Optional[int] = None
    sector2_ms: Optional[int] = None
    sector3_ms: Optional[int] = None
    source: str = "logs"


@dataclass
class SessionMetadataData:
    """Session metadata from Static SHM (or logs as fallback)."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    session_name: str = "Unknown"
    track: str = "Unknown"
    track_configuration: str = "Unknown"
    track_length_m: Optional[float] = None
    weather: str = "Unknown"
    is_online: bool = False
    is_timed_race: bool = False
    event_id: Optional[int] = None
    source: str = "shm_static"


@dataclass
class SharedSessionData:
    """Unified session data accessible by telemetry and log parser."""

    # Shared objects
    lap_validity: Dict[int, LapValidityData] = field(default_factory=dict)
    lap_timing: Dict[int, LapTimingData] = field(default_factory=dict)
    fuel_data: FuelData = field(default_factory=FuelData)
    player_identification: PlayerIdentificationData = field(
        default_factory=PlayerIdentificationData
    )
    sector_splits: Dict[int, SectorSplitData] = field(default_factory=dict)
    session_metadata: SessionMetadataData = field(default_factory=SessionMetadataData)

    # Legacy flat fields for migration compatibility
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    session_name: str = "Unknown"
    track: str = "Unknown"
    track_configuration: str = "Unknown"
    track_length_m: Optional[float] = None
    car: str = "Unknown"
    player_name: Optional[str] = None
    player_id: Optional[str] = None
    car_uuid: Optional[str] = None
    weather: str = "Unknown"
    is_online: bool = False
    is_timed_race: bool = False
    event_id: Optional[int] = None

    lap_times: Dict[int, float] = field(default_factory=dict)
    lap_times_graphics: Dict[int, float] = field(default_factory=dict)
    lap_times_logs: Dict[int, float] = field(default_factory=dict)
    calc_lap_times: Dict[int, float] = field(default_factory=dict)
    current_lap_time_ms: Optional[int] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None

    sector_times: Dict[int, Dict[int, int]] = field(default_factory=dict)

    lap_validity_flat: Dict[int, bool] = field(default_factory=dict)
    is_current_lap_invalid: Optional[bool] = None

    lap_boundaries: Dict[int, int] = field(default_factory=dict)
    lap_completion_timestamps: Dict[int, str] = field(default_factory=dict)

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None
    fuel_consumption: Dict[int, float] = field(default_factory=dict)

    total_laps: Optional[int] = None
    current_lap: Optional[int] = None
    session_phase: Optional[str] = None
    session_time_left_ms: Optional[int] = None
    current_pos: Optional[int] = None
    total_drivers: Optional[int] = None

    car_setup: Dict[str, Any] = field(default_factory=dict)
    assists_state: Dict[str, Any] = field(default_factory=dict)

    max_speed: Optional[float] = None
    tyre_compound: str = "Unknown"
    stint_number: int = 1

    starting_ambient_temp_c: Optional[float] = None
    starting_ground_temp_c: Optional[float] = None
    starting_grip: Optional[str] = None
    air_density: Optional[float] = None

    data_sources: Dict[str, Set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_metadata.session_id = self.session_id


class SharedSessionManager:
    """Thread-safe manager for shared session data."""

    def __init__(self) -> None:
        self._session_data = SharedSessionData()
        self._lock = threading.RLock()
        self._observers: list[Callable[[SharedSessionData], None]] = []

    def _mark_source(self, field_name: str, source: str) -> None:
        if field_name not in self._session_data.data_sources:
            self._session_data.data_sources[field_name] = set()
        self._session_data.data_sources[field_name].add(source)

    # New shared object access
    def get_lap_validity_data(self, lap_num: int) -> Optional[LapValidityData]:
        with self._lock:
            return self._session_data.lap_validity.get(lap_num)

    def get_lap_timing_data(self, lap_num: int) -> Optional[LapTimingData]:
        with self._lock:
            return self._session_data.lap_timing.get(lap_num)

    def get_fuel_data(self) -> FuelData:
        with self._lock:
            return self._session_data.fuel_data

    def get_player_identification(self) -> PlayerIdentificationData:
        with self._lock:
            return self._session_data.player_identification

    def get_sector_split_data(self, lap_num: int) -> Optional[SectorSplitData]:
        with self._lock:
            return self._session_data.sector_splits.get(lap_num)

    def get_session_metadata_data(self) -> SessionMetadataData:
        with self._lock:
            return self._session_data.session_metadata

    # Legacy accessors
    def get_lap_time(self, lap_num: int) -> Optional[float]:
        with self._lock:
            # 1st priority: Graphics SHM timing state.
            graphics_time = self._session_data.lap_times_graphics.get(lap_num)
            if graphics_time is not None:
                return graphics_time

            # 2nd priority: Log parser completed-lap timing.
            logs_time = self._session_data.lap_times_logs.get(lap_num)
            if logs_time is not None:
                return logs_time

            # 3rd priority: Derived telemetry timings.
            return self._session_data.calc_lap_times.get(lap_num)

    def get_current_lap_time(self) -> Optional[int]:
        with self._lock:
            return self._session_data.current_lap_time_ms

    def get_sector_times(self, lap_num: int) -> Optional[Dict[int, int]]:
        with self._lock:
            return self._session_data.sector_times.get(lap_num)

    def get_lap_validity(self, lap_num: int) -> bool:
        with self._lock:
            return self._session_data.lap_validity_flat.get(lap_num, True)

    def get_lap_state(self, lap_num: int) -> Optional[str]:
        with self._lock:
            lap_validity = self._session_data.lap_validity.get(lap_num)
            if lap_validity is None:
                return None
            return "PUSH" if lap_validity.is_valid else "INVALID_GAME"

    def get_car_setup(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._session_data.car_setup)

    def get_session_metadata(self) -> Dict[str, Any]:
        with self._lock:
            md = self._session_data.session_metadata
            sources = self._session_data.data_sources

            # Priority per plan: Static SHM > Logs > Graphics/session summary.
            game_version = (
                md.game_version
                if "shm_static" in sources.get("game_version", set())
                else self._session_data.game_version
            )
            session_type = (
                md.session_type
                if "shm_static" in sources.get("session_type", set())
                else self._session_data.session_type
            )
            track = (
                md.track
                if "shm_static" in sources.get("track", set())
                else self._session_data.track
            )
            is_online = (
                md.is_online
                if "shm_static" in sources.get("is_online", set())
                else self._session_data.is_online
            )
            is_timed_race = (
                md.is_timed_race
                if "shm_static" in sources.get("is_timed_race", set())
                else self._session_data.is_timed_race
            )
            event_id = md.event_id if "shm_static" in sources.get("event_id", set()) else self._session_data.event_id

            return {
                "session_id": self._session_data.session_id,
                "game_version": game_version,
                "session_type": session_type,
                "track": track,
                "track_configuration": self._session_data.track_configuration,
                "track_length_m": self._session_data.track_length_m,
                "is_online": is_online,
                "is_timed_race": is_timed_race,
                "event_id": event_id,
                # Logs-only identity fields.
                "player_id": self._session_data.player_id,
                "car_uuid": self._session_data.car_uuid,
            }

    def get_best_lap_time(self) -> Optional[float]:
        with self._lock:
            return min(self._session_data.lap_times.values()) if self._session_data.lap_times else None

    def get_all_lap_times(self) -> Dict[int, float]:
        with self._lock:
            lap_nums = (
                set(self._session_data.lap_times_graphics)
                | set(self._session_data.lap_times_logs)
                | set(self._session_data.calc_lap_times)
            )
            merged: Dict[int, float] = {}
            for lap_num in lap_nums:
                value = self._session_data.lap_times_graphics.get(lap_num)
                if value is None:
                    value = self._session_data.lap_times_logs.get(lap_num)
                if value is None:
                    value = self._session_data.calc_lap_times.get(lap_num)
                if value is not None:
                    merged[lap_num] = value
            return merged

    def validate_data_consistency(self) -> Dict[str, list[str]]:
        issues: list[str] = []
        with self._lock:
            lap_nums = set(self._session_data.lap_times_graphics) | set(self._session_data.lap_times_logs)
            for lap_num in sorted(lap_nums):
                graphics_time = self._session_data.lap_times_graphics.get(lap_num)
                logs_time = self._session_data.lap_times_logs.get(lap_num)
                if graphics_time is None or logs_time is None:
                    continue
                if abs(graphics_time - logs_time) > 100.0:
                    issues.append(
                        (
                            f"lap {lap_num}: graphics={int(graphics_time)}ms "
                            f"logs={int(logs_time)}ms"
                        )
                    )

        return {"inconsistencies": issues}

    def get_all_lap_validity(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._session_data.lap_validity_flat)

    # New shared object updates
    def update_lap_validity_from_graphics_shm(self, lap_num: int, is_invalid: bool) -> None:
        with self._lock:
            current = self._session_data.lap_validity.get(lap_num)
            if current is None:
                current = LapValidityData(lap_number=lap_num, is_valid=not is_invalid)
                self._session_data.lap_validity[lap_num] = current
            else:
                current.is_valid = not is_invalid
            current.source = "shm_graphics"

            self._session_data.lap_validity_flat[lap_num] = not is_invalid
            self._session_data.is_current_lap_invalid = is_invalid
            self._mark_source("lap_validity", "shm_graphics")

        self.notify_observers()

    def update_lap_timing_from_graphics_shm(self, lap_num: int, timing_data: Dict[str, Any]) -> None:
        with self._lock:
            current = self._session_data.lap_timing.get(lap_num)
            if current is None:
                current = LapTimingData(lap_number=lap_num)
                self._session_data.lap_timing[lap_num] = current

            current.current_lap_time_ms = timing_data.get(
                "current_lap_time_ms",
                timing_data.get("current_laptime_ms"),
            )
            current.last_lap_time_ms = timing_data.get("last_laptime_ms")
            current.best_lap_time_ms = timing_data.get("best_laptime_ms")
            current.ideal_lap_time_ms = timing_data.get("ideal_laptime_ms")
            current.delta_time_ms = timing_data.get("delta_time_ms")
            current.source = "shm_graphics"

            self._session_data.current_lap_time_ms = current.current_lap_time_ms
            self._session_data.last_lap_time_ms = current.last_lap_time_ms
            self._session_data.best_lap_time_ms = current.best_lap_time_ms
            self._session_data.ideal_lap_time_ms = current.ideal_lap_time_ms
            self._session_data.delta_time_ms = current.delta_time_ms

            if current.last_lap_time_ms and current.last_lap_time_ms > 0:
                lap_time = float(current.last_lap_time_ms)
                self._session_data.lap_times[lap_num] = lap_time
                self._session_data.lap_times_graphics[lap_num] = lap_time
                self._mark_source("lap_times", "shm_graphics")

            for field_name in (
                "current_lap_time_ms",
                "last_lap_time_ms",
                "best_lap_time_ms",
                "ideal_lap_time_ms",
                "delta_time_ms",
            ):
                self._mark_source(field_name, "shm_graphics")

        self.notify_observers()

    def update_fuel_from_graphics_shm(self, fuel_data: Dict[str, Any]) -> None:
        with self._lock:
            current_fuel = fuel_data.get("fuel_liter_current_quantity")
            fuel_rate = fuel_data.get("fuel_liter_per_km")
            fuel_economy = fuel_data.get("km_per_fuel_liter")
            fuel_per_lap = fuel_data.get("fuel_liter_per_lap")

            self._session_data.current_fuel = current_fuel
            self._session_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_economy = fuel_economy

            self._session_data.fuel_data.current_fuel = current_fuel
            self._session_data.fuel_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_data.fuel_economy = fuel_economy
            self._session_data.fuel_data.fuel_consumed_lap = fuel_per_lap
            self._session_data.fuel_data.source = "shm_graphics"

            self._mark_source("current_fuel", "shm_graphics")
            self._mark_source("fuel_consumption_rate", "shm_graphics")
            self._mark_source("fuel_economy", "shm_graphics")

        self.notify_observers()

    def update_player_identification_from_logs(self, player_data: Dict[str, Any]) -> None:
        with self._lock:
            ident = self._session_data.player_identification
            ident.steam_id = player_data.get("steam_id") or ident.steam_id
            ident.player_name = player_data.get("player_name") or ident.player_name
            ident.car_uuid = player_data.get("car_uuid") or ident.car_uuid
            ident.car_model = player_data.get("car_model") or ident.car_model
            ident.source = "logs"

            self._session_data.player_id = ident.steam_id
            self._session_data.player_name = ident.player_name
            self._session_data.car_uuid = ident.car_uuid
            if ident.car_model:
                self._session_data.car = ident.car_model

            self._mark_source("player_id", "logs")
            self._mark_source("car_uuid", "logs")

        self.notify_observers()

    def update_sector_splits_from_logs(self, lap_num: int, sector_data: Dict[str, Any]) -> None:
        with self._lock:
            splits = SectorSplitData(
                lap_number=lap_num,
                sector1_ms=sector_data.get("sector1_ms"),
                sector2_ms=sector_data.get("sector2_ms"),
                sector3_ms=sector_data.get("sector3_ms"),
                source="logs",
            )
            self._session_data.sector_splits[lap_num] = splits

            legacy: Dict[int, int] = {}
            if splits.sector1_ms is not None:
                legacy[1] = splits.sector1_ms
            if splits.sector2_ms is not None:
                legacy[2] = splits.sector2_ms
            if splits.sector3_ms is not None:
                legacy[3] = splits.sector3_ms
            if legacy:
                self._session_data.sector_times[lap_num] = legacy

            self._mark_source("sector_times", "logs")

        self.notify_observers()

    def update_session_metadata_from_static_shm(self, metadata: Dict[str, Any]) -> None:
        with self._lock:
            md = self._session_data.session_metadata
            md.session_id = self._session_data.session_id
            md.game_version = metadata.get("ac_evo_version", md.game_version)
            md.session_type = str(metadata.get("session", md.session_type))
            md.session_name = metadata.get("session_name", md.session_name)
            md.track = metadata.get("track", md.track)
            md.track_configuration = metadata.get("track_configuration", md.track_configuration)
            md.track_length_m = metadata.get("track_length_m", md.track_length_m)
            md.is_online = bool(metadata.get("is_online", md.is_online))
            md.is_timed_race = bool(metadata.get("is_timed_race", md.is_timed_race))
            md.event_id = metadata.get("event_id", md.event_id)
            md.source = "shm_static"

            self._session_data.game_version = md.game_version
            self._session_data.session_type = md.session_type
            self._session_data.session_name = md.session_name
            self._session_data.track = md.track
            self._session_data.track_configuration = md.track_configuration
            self._session_data.track_length_m = md.track_length_m
            self._session_data.is_online = md.is_online
            self._session_data.is_timed_race = md.is_timed_race
            self._session_data.event_id = md.event_id
            self._session_data.starting_ambient_temp_c = metadata.get(
                "starting_ambient_temperature_c", self._session_data.starting_ambient_temp_c
            )
            self._session_data.starting_ground_temp_c = metadata.get(
                "starting_ground_temperature_c", self._session_data.starting_ground_temp_c
            )
            starting_grip = metadata.get("starting_grip_name") or metadata.get("starting_grip")
            self._session_data.starting_grip = starting_grip

            for field_name in ("game_version", "session_type", "track", "is_online", "is_timed_race"):
                self._mark_source(field_name, "shm_static")

        self.notify_observers()

    # Legacy update entry points
    def update_lap_from_logs(self, lap_data: LapData, session_data: Optional[SessionData] = None) -> None:
        player_payload: Dict[str, Any] = {}
        if session_data is not None:
            with self._lock:
                self._session_data.session_id = session_data.session_id
                self._session_data.session_metadata.session_id = session_data.session_id
                self._session_data.game_version = session_data.game_version
                self._session_data.session_type = session_data.session_type
                self._session_data.track = session_data.track
                self._session_data.weather = session_data.weather
                self._session_data.car = session_data.car

                self._mark_source("game_version", "logs")
                self._mark_source("session_type", "logs")
                self._mark_source("track", "logs")

            player_payload = {
                "steam_id": session_data.player_id,
                "player_name": session_data.player_name,
                "car_uuid": session_data.car_uuid,
                "car_model": session_data.car,
            }
            self.update_player_identification_from_logs(player_payload)

        self.update_sector_splits_from_logs(
            lap_data.lap_number,
            {
                "sector1_ms": lap_data.sector1_ms,
                "sector2_ms": lap_data.sector2_ms,
                "sector3_ms": lap_data.sector3_ms,
            },
        )

        with self._lock:
            self._session_data.lap_boundaries[lap_data.lap_number] = lap_data.lap_number
            self._session_data.lap_completion_timestamps[lap_data.lap_number] = lap_data.timestamp
            self._session_data.fuel_consumption[lap_data.lap_number] = lap_data.fuel_used or 0.0

            if lap_data.lap_time_ms > 0:
                lap_time = float(lap_data.lap_time_ms)
                self._session_data.lap_times[lap_data.lap_number] = lap_time
                self._session_data.lap_times_logs[lap_data.lap_number] = lap_time
                self._mark_source("lap_times", "logs")

            self._session_data.lap_validity[lap_data.lap_number] = LapValidityData(
                lap_number=lap_data.lap_number,
                is_valid=lap_data.is_valid,
                source="logs",
            )
            self._session_data.lap_validity_flat[lap_data.lap_number] = lap_data.is_valid

            self._mark_source("lap_boundaries", "logs")
            self._mark_source("lap_completion_timestamps", "logs")
            self._mark_source("fuel_consumption", "logs")

        self.notify_observers()

    def update_from_logs(self, log_session_data: SessionData) -> None:
        with self._lock:
            self._session_data.session_id = log_session_data.session_id
            self._session_data.session_metadata.session_id = log_session_data.session_id
            self._session_data.game_version = log_session_data.game_version
            self._session_data.session_type = log_session_data.session_type
            self._session_data.track = log_session_data.track
            self._session_data.weather = log_session_data.weather
            self._session_data.car = log_session_data.car

        self.update_player_identification_from_logs(
            {
                "steam_id": log_session_data.player_id,
                "player_name": log_session_data.player_name,
                "car_uuid": log_session_data.car_uuid,
                "car_model": log_session_data.car,
            }
        )

        for lap in log_session_data.laps:
            self.update_lap_from_logs(lap, session_data=log_session_data)

    def update_from_static_shm(self, static_data: Dict[str, Any]) -> None:
        self.update_session_metadata_from_static_shm(static_data)

    def update_from_graphics_shm(self, graphics_data: Dict[str, Any]) -> None:
        current_lap = int(graphics_data.get("session_current_lap") or 0)
        if current_lap > 0:
            self.update_lap_timing_from_graphics_shm(current_lap, graphics_data)

        self.update_fuel_from_graphics_shm(graphics_data)

        with self._lock:
            self._session_data.total_laps = graphics_data.get("total_lap_count")
            self._session_data.current_lap = graphics_data.get("session_current_lap")
            self._session_data.session_phase = graphics_data.get("session_phase")
            self._session_data.session_time_left_ms = graphics_data.get("session_time_left_ms")
            self._session_data.current_pos = graphics_data.get("current_pos")
            self._session_data.total_drivers = graphics_data.get("total_drivers")

            self._mark_source("session_summary", "shm_graphics")

        self.notify_observers()

    def update_from_physics_shm(self, physics_data: Dict[str, Any]) -> None:
        with self._lock:
            speed_kmh = physics_data.get("speed_kmh")
            if isinstance(speed_kmh, (int, float)):
                if self._session_data.max_speed is None:
                    self._session_data.max_speed = float(speed_kmh)
                else:
                    self._session_data.max_speed = max(self._session_data.max_speed, float(speed_kmh))

            car_setup = physics_data.get("car_setup")
            if isinstance(car_setup, dict):
                self._session_data.car_setup.update(car_setup)
            assists_state = physics_data.get("assists_state")
            if isinstance(assists_state, dict):
                self._session_data.assists_state.update(assists_state)

            self._session_data.air_density = physics_data.get("air_density", self._session_data.air_density)

            self._mark_source("max_speed", "shm_physics")
            self._mark_source("car_setup", "shm_physics")

        self.notify_observers()

    def update_from_telemetry(self, telemetry_data: Dict[str, Any]) -> None:
        with self._lock:
            max_speed = telemetry_data.get("max_speed")
            if isinstance(max_speed, (int, float)):
                self._session_data.max_speed = float(max_speed)

            stint_number = telemetry_data.get("stint_number")
            if isinstance(stint_number, int) and stint_number > 0:
                self._session_data.stint_number = stint_number

            tyre_compound = telemetry_data.get("tyre_compound")
            if isinstance(tyre_compound, str) and tyre_compound.strip():
                self._session_data.tyre_compound = tyre_compound

            self._mark_source("telemetry_summary", "calculated")

        self.notify_observers()

    def get_data_sources(self) -> Dict[str, Set[str]]:
        """Return a snapshot of data source tracking (thread-safe)."""
        with self._lock:
            return {k: set(v) for k, v in self._session_data.data_sources.items()}

    def reset(self) -> None:
        """Replace session data with a fresh instance, preserving observers and lock.

        Call this when a new game session starts so stale lap validity, timing,
        and fuel data from the previous session cannot bleed into the new one.
        Player identification is intentionally preserved across resets because the
        same driver is still logged in.
        """
        with self._lock:
            old_ident = self._session_data.player_identification
            self._session_data = SharedSessionData()
            # Re-attach player identification — Steam ID / car UUID don't change
            # between sessions and must not be wiped.
            self._session_data.player_identification = old_ident
            self._session_data.player_id = old_ident.steam_id
            self._session_data.player_name = old_ident.player_name
            self._session_data.car_uuid = old_ident.car_uuid

    # Observer pattern
    def subscribe(self, callback: Callable[[SharedSessionData], None]) -> None:
        with self._lock:
            if callback not in self._observers:
                self._observers.append(callback)

    def notify_observers(self) -> None:
        with self._lock:
            observers = list(self._observers)
            snapshot = self._session_data

        for callback in observers:
            try:
                callback(snapshot)
            except Exception:
                # Observer failures must not break data flow.
                continue

    def to_legacy_session_data(self) -> SessionData:
        return LegacySessionDataWrapper(self).to_session_data()

    def get_legacy_wrapper(self) -> "LegacySessionDataWrapper":
        return LegacySessionDataWrapper(self)


class LegacySessionDataWrapper:
    """Compatibility adapter exposing shared state as legacy session models."""

    def __init__(self, shared_manager: SharedSessionManager):
        self._shared = shared_manager

    @staticmethod
    def _lap_time_str_from_ms(lap_time_ms: int) -> str:
        if lap_time_ms <= 0:
            return "0:00.000"
        minutes = lap_time_ms // 60000
        seconds = (lap_time_ms % 60000) / 1000.0
        return f"{minutes}:{seconds:06.3f}"

    @property
    def laps(self) -> list[LapData]:
        return self._convert_to_legacy_laps()

    def to_session_data(self) -> SessionData:
        metadata = self._shared.get_session_metadata()
        identity = self._shared.get_player_identification()

        return SessionData(
            session_id=metadata.get("session_id") or "",
            game_version=metadata.get("game_version") or "Unknown",
            session_type=metadata.get("session_type") or "Unknown",
            car=identity.car_model or "Unknown",
            track=metadata.get("track") or "Unknown",
            player_name=identity.player_name,
            player_id=identity.steam_id,
            car_uuid=identity.car_uuid,
            laps=self.laps,
        )

    def _convert_to_legacy_laps(self) -> list[LapData]:
        lap_times = self._shared.get_all_lap_times()
        lap_validity = self._shared.get_all_lap_validity()

        lap_numbers = sorted(set(lap_times) | set(lap_validity))
        laps: list[LapData] = []
        for lap_num in lap_numbers:
            lap_time_value = lap_times.get(lap_num)
            lap_time_ms = int(lap_time_value) if isinstance(lap_time_value, (int, float)) else 0
            sector_times = self._shared.get_sector_times(lap_num) or {}
            laps.append(
                LapData(
                    lap_number=lap_num,
                    physics_lap_number=lap_num,
                    lap_time_ms=lap_time_ms,
                    lap_time_str=self._lap_time_str_from_ms(lap_time_ms),
                    sector1_ms=sector_times.get(1),
                    sector2_ms=sector_times.get(2),
                    sector3_ms=sector_times.get(3),
                    is_valid=lap_validity.get(lap_num, True),
                )
            )

        return laps
