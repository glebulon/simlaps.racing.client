"""
Compare Log Parser Data vs Shared Memory Data

This script analyzes what data is available from game logs vs shared memory
to identify gaps and opportunities for unified session data.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from enum import Enum


class DataSource(Enum):
    LOGS = "Game Logs"
    SHARED_MEMORY = "Shared Memory"
    BOTH = "Both Sources"
    NEITHER = "Neither"


@dataclass
class DataField:
    """Represents a data field with its availability across sources"""
    name: str
    description: str
    log_available: bool = False
    shm_available: bool = False
    log_field_name: Optional[str] = None
    shm_field_name: Optional[str] = None
    notes: str = ""
    
    @property
    def source(self) -> DataSource:
        if self.log_available and self.shm_available:
            return DataSource.BOTH
        elif self.log_available:
            return DataSource.LOGS
        elif self.shm_available:
            return DataSource.SHARED_MEMORY
        else:
            return DataSource.NEITHER


@dataclass
class ComparisonResult:
    """Results of the comparison analysis"""
    fields: List[DataField] = field(default_factory=list)
    logs_only: List[DataField] = field(default_factory=list)
    shm_only: List[DataField] = field(default_factory=list)
    both: List[DataField] = field(default_factory=list)
    
    def __post_init__(self):
        """Categorize fields by source availability"""
        for field in self.fields:
            if field.source == DataSource.LOGS:
                self.logs_only.append(field)
            elif field.source == DataSource.SHARED_MEMORY:
                self.shm_only.append(field)
            elif field.source == DataSource.BOTH:
                self.both.append(field)


def analyze_data_sources() -> ComparisonResult:
    """Analyze data availability between logs and shared memory"""
    
    result = ComparisonResult()
    
    # === LAP TIMING DATA ===
    result.fields.extend([
        DataField(
            name="lap_time_ms",
            description="Lap completion time in milliseconds",
            log_available=True,
            log_field_name="lap_time_ms",
            shm_available=True,
            shm_field_name="last_laptime_ms",
            notes="Logs are authoritative (game's official timing)"
        ),
        DataField(
            name="lap_number",
            description="Lap number within session",
            log_available=True,
            log_field_name="lap_number",
            shm_available=True,
            shm_field_name="session_current_lap",
            notes="Both available, logs definitive for completed laps"
        ),
        DataField(
            name="physics_lap_number",
            description="Physics engine lap counter (ground truth)",
            log_available=True,
            log_field_name="physics_lap_number",
            shm_available=False,
            notes="Only in logs via evOnLapCompleted"
        ),
        DataField(
            name="sector1_ms",
            description="Sector 1 time in milliseconds",
            log_available=True,
            log_field_name="sector1_ms",
            shm_available=False,
            notes="Only in logs via split events"
        ),
        DataField(
            name="sector2_ms",
            description="Sector 2 time in milliseconds",
            log_available=True,
            log_field_name="sector2_ms",
            shm_available=False,
            notes="Only in logs via split events"
        ),
        DataField(
            name="sector3_ms",
            description="Sector 3 time in milliseconds",
            log_available=True,
            log_field_name="sector3_ms",
            shm_available=False,
            notes="Only in logs via split events"
        ),
        DataField(
            name="sectors_consistent",
            description="Whether S1+S2+S3 equals lap time",
            log_available=True,
            log_field_name="sectors_consistent",
            shm_available=False,
            notes="Calculated in logs, not available in SHM"
        ),
        DataField(
            name="current_lap_time_ms",
            description="Current lap time (running)",
            log_available=False,
            shm_available=True,
            shm_field_name="current_lap_time_ms",
            notes="Real-time from SHM, not in logs"
        ),
        DataField(
            name="predicted_lap_time_ms",
            description="Predicted lap time based on current pace",
            log_available=False,
            shm_available=True,
            shm_field_name="predicted_lap_time_ms",
            notes="Real-time prediction from SHM"
        ),
        DataField(
            name="best_laptime_ms",
            description="Best lap time in session",
            log_available=True,
            log_field_name="calculated from laps",
            shm_available=True,
            shm_field_name="best_laptime_ms",
            notes="Both available, SHM provides real-time"
        ),
        DataField(
            name="delta_time_ms",
            description="Time delta to reference lap",
            log_available=False,
            shm_available=True,
            shm_field_name="delta_time_ms",
            notes="Real-time delta from SHM"
        ),
    ])
    
    # === LAP VALIDITY DATA ===
    result.fields.extend([
        DataField(
            name="is_valid",
            description="Whether lap is valid/official",
            log_available=True,
            log_field_name="is_valid",
            shm_available=True,
            shm_field_name="is_valid_lap",
            notes="Logs track multiple invalidation reasons, SHM has simple flag"
        ),
        DataField(
            name="lap_state",
            description="Detailed lap state (PUSH, OUTLAP, INVALID_*, ABORTED)",
            log_available=True,
            log_field_name="lap_state",
            shm_available=False,
            notes="Logs track specific invalidation reasons (track limit, penalty, etc.)"
        ),
        DataField(
            name="has_track_limit_violation",
            description="Track limit violation detected",
            log_available=True,
            log_field_name="has_track_limit_violation",
            shm_available=False,
            notes="Only in logs via tyre out event"
        ),
        DataField(
            name="has_penalty",
            description="Penalty notification received",
            log_available=True,
            log_field_name="has_penalty",
            shm_available=False,
            notes="Only in logs via penalty events"
        ),
        DataField(
            name="timing_is_invalid",
            description="Game's authoritative validity flag",
            log_available=False,
            shm_available=True,
            shm_field_name="timing_is_invalid",
            notes="From SHM timing state, but less detailed than logs"
        ),
    ])
    
    # === FUEL DATA ===
    result.fields.extend([
        DataField(
            name="fuel_used",
            description="Fuel used per lap in liters",
            log_available=True,
            log_field_name="fuel_used",
            shm_available=True,
            shm_field_name="fuel_liter_used",
            notes="Logs track per-lap, SHM has session-level"
        ),
        DataField(
            name="fuel_per_lap",
            description="Target fuel consumption per lap in litres",
            log_available=True,
            log_field_name="calculated",
            shm_available=True,
            shm_field_name="fuel_per_lap (graphics SHM)",
            notes="Both available, SHM provides target value"
        ),
        DataField(
            name="fuel_estimated_laps",
            description="Estimated laps remaining with current fuel",
            log_available=False,
            shm_available=True,
            shm_field_name="fuel_estimated_laps (graphics SHM)",
            notes="Only in graphics SHM"
        ),
        DataField(
            name="max_fuel",
            description="Maximum fuel tank capacity of the car in litres",
            log_available=False,
            shm_available=True,
            shm_field_name="max_fuel (graphics SHM)",
            notes="Only in graphics SHM"
        ),
        DataField(
            name="fuel_reliable",
            description="Whether fuel data is reliable",
            log_available=True,
            log_field_name="fuel_reliable",
            shm_available=False,
            notes="Logs track reliability (hybrid cars, etc.)"
        ),
        DataField(
            name="fuel_liter_current_quantity",
            description="Current fuel quantity in liters",
            log_available=False,
            shm_available=True,
            shm_field_name="fuel_liter_current_quantity",
            notes="Real-time fuel level from SHM"
        ),
        DataField(
            name="fuel_liter_per_lap",
            description="Fuel consumption per lap",
            log_available=True,
            log_field_name="calculated",
            shm_available=True,
            shm_field_name="fuel_liter_per_lap",
            notes="Both available, SHM provides real-time estimate"
        ),
        DataField(
            name="initial_fuel",
            description="Initial fuel at session start",
            log_available=True,
            log_field_name="initial_fuel",
            shm_available=False,
            notes="Calculated in logs from first energy event"
        ),
        DataField(
            name="fuel_used_session",
            description="Total fuel used in session",
            log_available=True,
            log_field_name="fuel_used_session",
            shm_available=False,
            notes="Calculated in logs from energy events"
        ),
        DataField(
            name="laps_possible_with_fuel",
            description="Estimated laps remaining with current fuel",
            log_available=False,
            shm_available=True,
            shm_field_name="laps_possible_with_fuel",
            notes="Real-time calculation from SHM"
        ),
    ])
    
    # === TIRE DATA ===
    result.fields.extend([
        DataField(
            name="tyre_compound",
            description="Tire compound type",
            log_available=True,
            log_field_name="tyre_compound",
            shm_available=False,
            notes="Logs track compound via tyre events, SHM doesn't expose"
        ),
        DataField(
            name="tyre_wear",
            description="Tire wear percentage (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="tyre_wear",
            notes="Real-time wear from physics SHM"
        ),
        DataField(
            name="tyre_core_temp",
            description="Tire core temperature (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="tyre_core_temp",
            notes="Real-time temps from physics SHM"
        ),
        DataField(
            name="tyre_temp",
            description="Tire temperature (per wheel, AC Evo)",
            log_available=False,
            shm_available=True,
            shm_field_name="tyre_temp",
            notes="Real-time temps from physics SHM"
        ),
        DataField(
            name="tyre_temp_i/m/o",
            description="Tire inner/middle/outer temps (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="tyre_temp_i, tyre_temp_m, tyre_temp_o",
            notes="Real-time temps from physics SHM"
        ),
        DataField(
            name="tyre_dirty_level",
            description="Tire dirt/debris level (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="tyre_dirty_level",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="wheels_pressure",
            description="Tire pressure (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="wheels_pressure",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="camber_rad",
            description="Wheel camber angle in radians (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="camber_rad",
            notes="Real-time from physics SHM"
        ),
    ])
    
    # === SESSION METADATA ===
    result.fields.extend([
        DataField(
            name="session_id",
            description="Unique session identifier",
            log_available=True,
            log_field_name="session_id",
            shm_available=True,
            shm_field_name="session_id (static SHM)",
            notes="Logs generate UUID, SHM has event/session IDs"
        ),
        DataField(
            name="game_version",
            description="Game version string",
            log_available=True,
            log_field_name="game_version",
            shm_available=True,
            shm_field_name="ac_evo_version (static SHM)",
            notes="Both available, SHM has build version"
        ),
        DataField(
            name="session_type",
            description="Session type (practice, qualify, race, etc.)",
            log_available=True,
            log_field_name="session_type",
            shm_available=True,
            shm_field_name="session (static SHM) / session_phase_name (graphics SHM)",
            notes="Logs have enum, SHM has enum (static) and phase name (graphics)"
        ),
        DataField(
            name="session_phase_name",
            description="Current session phase name",
            log_available=False,
            shm_available=True,
            shm_field_name="session_phase_name",
            notes="Real-time phase from SHM"
        ),
        DataField(
            name="session_time_left_ms",
            description="Time remaining in session",
            log_available=False,
            shm_available=True,
            shm_field_name="session_time_left_ms",
            notes="Real-time from SHM"
        ),
        DataField(
            name="session_total_lap",
            description="Total laps in session",
            log_available=True,
            log_field_name="calculated from laps",
            shm_available=True,
            shm_field_name="session_total_lap",
            notes="Both available"
        ),
        DataField(
            name="session_lap_length_km",
            description="Lap length in kilometers",
            log_available=False,
            shm_available=True,
            shm_field_name="session_lap_length_km (graphics SHM) / track_length_m (static SHM)",
            notes="From both graphics and static SHM"
        ),
        DataField(
            name="track",
            description="Track name",
            log_available=True,
            log_field_name="track",
            shm_available=True,
            shm_field_name="track (static SHM)",
            notes="Both available"
        ),
        DataField(
            name="track_configuration",
            description="Track layout variant or configuration name",
            log_available=False,
            shm_available=True,
            shm_field_name="track_configuration (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="session_name",
            description="Human-readable session name (e.g. 'Race 1')",
            log_available=False,
            shm_available=True,
            shm_field_name="session_name (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="event_id",
            description="Unique identifier of the event within the championship",
            log_available=False,
            shm_available=True,
            shm_field_name="event_id (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="is_online",
            description="Session is an online multiplayer event",
            log_available=False,
            shm_available=True,
            shm_field_name="is_online (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="is_timed_race",
            description="Session ends by elapsed time rather than lap count",
            log_available=False,
            shm_available=True,
            shm_field_name="is_timed_race (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="is_static_weather",
            description="Weather is fixed and will not change during the session",
            log_available=False,
            shm_available=True,
            shm_field_name="is_static_weather (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="car",
            description="Car model name",
            log_available=True,
            log_field_name="car",
            shm_available=True,
            shm_field_name="car_model",
            notes="Both available"
        ),
        DataField(
            name="car_uuid",
            description="Unique car identifier",
            log_available=True,
            log_field_name="car_uuid",
            shm_available=True,
            shm_field_name="player_car_id",
            notes="Both available, different field names"
        ),
        DataField(
            name="weather",
            description="Weather conditions",
            log_available=True,
            log_field_name="weather",
            shm_available=True,
            shm_field_name="is_static_weather (static SHM) / starting_ambient_temperature_c (static SHM)",
            notes="Logs have weather string, SHM has static flag and starting temps"
        ),
        DataField(
            name="starting_ambient_temperature_c",
            description="Ambient air temperature at session start in °C",
            log_available=False,
            shm_available=True,
            shm_field_name="starting_ambient_temperature_c (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="starting_ground_temperature_c",
            description="Road surface temperature at session start in °C",
            log_available=False,
            shm_available=True,
            shm_field_name="starting_ground_temperature_c (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="starting_grip",
            description="Tyre grip condition at session start",
            log_available=False,
            shm_available=True,
            shm_field_name="starting_grip (static SHM)",
            notes="Only in static SHM"
        ),
        DataField(
            name="player_name",
            description="Player name",
            log_available=True,
            log_field_name="player_name",
            shm_available=True,
            shm_field_name="driver_name",
            notes="Both available"
        ),
        DataField(
            name="player_id",
            description="Player ID",
            log_available=True,
            log_field_name="player_id",
            shm_available=False,
            notes="Only in logs via connecting events"
        ),
        DataField(
            name="start_time",
            description="Session start timestamp",
            log_available=True,
            log_field_name="start_time",
            shm_available=False,
            notes="Generated in logs"
        ),
    ])
    
    # === CAR SETUP DATA ===
    result.fields.extend([
        DataField(
            name="setup_notes",
            description="Car setup notes/parameters",
            log_available=True,
            log_field_name="setup_notes",
            shm_available=False,
            notes="Logs track setup via setup events, SHM doesn't expose"
        ),
        DataField(
            name="brake_bias",
            description="Brake bias distribution",
            log_available=False,
            shm_available=True,
            shm_field_name="brake_bias",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="turbo_boost",
            description="Turbo boost level",
            log_available=False,
            shm_available=True,
            shm_field_name="turbo_boost",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="ballast",
            description="Ballast weight",
            log_available=False,
            shm_available=True,
            shm_field_name="ballast",
            notes="From physics SHM"
        ),
        DataField(
            name="drs_available",
            description="DRS availability",
            log_available=False,
            shm_available=True,
            shm_field_name="drs_available",
            notes="From physics SHM"
        ),
        DataField(
            name="drs_enabled",
            description="DRS activation status",
            log_available=False,
            shm_available=True,
            shm_field_name="drs_enabled",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="diff_coast_raw_value",
            description="Differential coast setting",
            log_available=False,
            shm_available=True,
            shm_field_name="diff_coast_raw_value",
            notes="From graphics SHM"
        ),
        DataField(
            name="diff_power_raw_value",
            description="Differential power setting",
            log_available=False,
            shm_available=True,
            shm_field_name="diff_power_raw_value",
            notes="From graphics SHM"
        ),
    ])
    
    # === TELEMETRY DATA (SHM ONLY) ===
    result.fields.extend([
        DataField(
            name="speed_kmh",
            description="Speed in km/h",
            log_available=False,
            shm_available=True,
            shm_field_name="speed_kmh",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="rpm",
            description="Engine RPM",
            log_available=False,
            shm_available=True,
            shm_field_name="rpms",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="gas",
            description="Throttle input (0-1)",
            log_available=False,
            shm_available=True,
            shm_field_name="gas",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="brake",
            description="Brake input (0-1)",
            log_available=False,
            shm_available=True,
            shm_field_name="brake",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="steer_angle",
            description="Steering angle",
            log_available=False,
            shm_available=True,
            shm_field_name="steer_angle",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="gear",
            description="Current gear",
            log_available=False,
            shm_available=True,
            shm_field_name="gear",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="acc_g",
            description="Acceleration G-forces (x, y, z)",
            log_available=False,
            shm_available=True,
            shm_field_name="acc_g",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="wheel_slip",
            description="Wheel slip ratio (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="wheel_slip",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="wheel_load",
            description="Wheel load (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="wheel_load",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="suspension_travel",
            description="Suspension travel (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="suspension_travel",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="ride_height",
            description="Ride height (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="ride_height",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="pitch",
            description="Car pitch angle",
            log_available=False,
            shm_available=True,
            shm_field_name="pitch",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="roll",
            description="Car roll angle",
            log_available=False,
            shm_available=True,
            shm_field_name="roll",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="heading",
            description="Car heading",
            log_available=False,
            shm_available=True,
            shm_field_name="heading",
            notes="Real-time from physics SHM"
        ),
        DataField(
            name="slip_ratio",
            description="Tire slip ratio (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="slip_ratio",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="slip_angle",
            description="Tire slip angle (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="slip_angle",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="fx",
            description="Longitudinal tire forces (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="fx",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="fy",
            description="Lateral tire forces (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="fy",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="brake_temp",
            description="Brake temperature (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="brake_temp",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="brake_torque",
            description="Brake torque (per wheel)",
            log_available=False,
            shm_available=True,
            shm_field_name="brake_torque",
            notes="Real-time from physics SHM (AC Evo)"
        ),
        DataField(
            name="air_density",
            description="Air density",
            log_available=False,
            shm_available=True,
            shm_field_name="air_density",
            notes="From physics SHM"
        ),
        DataField(
            name="air_temp",
            description="Air temperature",
            log_available=False,
            shm_available=True,
            shm_field_name="air_temp",
            notes="From physics SHM"
        ),
        DataField(
            name="road_temp",
            description="Road temperature",
            log_available=False,
            shm_available=True,
            shm_field_name="road_temp",
            notes="From physics SHM"
        ),
    ])
    
    # === POSITION/PROGRESS DATA ===
    result.fields.extend([
        DataField(
            name="npos",
            description="Normalized car position (0-1) on track",
            log_available=False,
            shm_available=True,
            shm_field_name="npos",
            notes="Authoritative progress from graphics SHM"
        ),
        DataField(
            name="distance_hundredm",
            description="Distance covered in hundredmeters",
            log_available=True,
            log_field_name="distance_hundredm",
            shm_available=False,
            notes="Calculated in logs from hundredmeter events"
        ),
        DataField(
            name="current_km",
            description="Total distance traveled in km",
            log_available=False,
            shm_available=True,
            shm_field_name="current_km",
            notes="From graphics SHM"
        ),
    ])
    
    # === STINT DATA ===
    result.fields.extend([
        DataField(
            name="stint_number",
            description="Stint number within session",
            log_available=True,
            log_field_name="stint_number",
            shm_available=False,
            notes="Calculated in logs based on compound changes"
        ),
        DataField(
            name="tyre_state",
            description="Tire state tracking across sessions",
            log_available=True,
            log_field_name="tyre_state",
            shm_available=False,
            notes="Persistent tracking in LogContext"
        ),
    ])
    
    # === RACE POSITION DATA ===
    result.fields.extend([
        DataField(
            name="current_pos",
            description="Current race position",
            log_available=False,
            shm_available=True,
            shm_field_name="current_pos",
            notes="From graphics SHM"
        ),
        DataField(
            name="total_drivers",
            description="Total number of drivers",
            log_available=False,
            shm_available=True,
            shm_field_name="total_drivers",
            notes="From graphics SHM"
        ),
        DataField(
            name="active_cars",
            description="Number of cars actively participating in the session",
            log_available=False,
            shm_available=True,
            shm_field_name="active_cars (graphics SHM)",
            notes="Only in graphics SHM"
        ),
        DataField(
            name="gap_behind",
            description="Time gap to the car immediately behind in seconds",
            log_available=False,
            shm_available=True,
            shm_field_name="gap_behind (graphics SHM)",
            notes="Only in graphics SHM"
        ),
    ])
    
    # === CAR SETUP DATA (Additional from Graphics SHM) ===
    result.fields.extend([
        DataField(
            name="max_turbo_boost",
            description="Maximum turbo boost pressure in bar",
            log_available=False,
            shm_available=True,
            shm_field_name="max_turbo_boost (graphics SHM)",
            notes="Only in graphics SHM"
        ),
        DataField(
            name="use_single_compound",
            description="Car is restricted to a single tyre compound for both axles",
            log_available=False,
            shm_available=True,
            shm_field_name="use_single_compound (graphics SHM)",
            notes="Only in graphics SHM"
        ),
        DataField(
            name="assists_state",
            description="All driver-assist levels currently active",
            log_available=False,
            shm_available=True,
            shm_field_name="assists_state (graphics SHM)",
            notes="Only in graphics SHM"
        ),
    ])
    
    # === ENGINE/POWERTRAIN DATA ===
    result.fields.extend([
        DataField(
            name="water_temp",
            description="Water temperature",
            log_available=False,
            shm_available=True,
            shm_field_name="water_temperature_c",
            notes="From graphics SHM"
        ),
        DataField(
            name="oil_temp",
            description="Oil temperature",
            log_available=False,
            shm_available=True,
            shm_field_name="oil_temperature_c",
            notes="From graphics SHM"
        ),
        DataField(
            name="oil_pressure",
            description="Oil pressure",
            log_available=False,
            shm_available=True,
            shm_field_name="oil_pressure_bar",
            notes="From graphics SHM"
        ),
        DataField(
            name="battery_voltage",
            description="Battery voltage",
            log_available=False,
            shm_available=True,
            shm_field_name="battery_voltage",
            notes="From graphics SHM"
        ),
        DataField(
            name="ers_recovery_level",
            description="ERS recovery level",
            log_available=False,
            shm_available=True,
            shm_field_name="ers_recovery_level",
            notes="From physics SHM (AC Evo)"
        ),
        DataField(
            name="ers_power_level",
            description="ERS power level",
            log_available=False,
            shm_available=True,
            shm_field_name="ers_power_level",
            notes="From physics SHM (AC Evo)"
        ),
        DataField(
            name="kers_charge",
            description="KERS charge level",
            log_available=False,
            shm_available=True,
            shm_field_name="kers_charge",
            notes="From physics SHM"
        ),
    ])
    
    # === PIT DATA ===
    result.fields.extend([
        DataField(
            name="is_in_pit_box",
            description="Whether car is in pit box",
            log_available=False,
            shm_available=True,
            shm_field_name="is_in_pit_box",
            notes="From graphics SHM"
        ),
        DataField(
            name="is_in_pit_lane",
            description="Whether car is in pit lane",
            log_available=False,
            shm_available=True,
            shm_field_name="is_in_pit_lane",
            notes="From graphics SHM"
        ),
    ])
    
    # === FLAG DATA ===
    result.fields.extend([
        DataField(
            name="flag",
            description="Current flag type",
            log_available=False,
            shm_available=True,
            shm_field_name="flag",
            notes="From graphics SHM"
        ),
        DataField(
            name="global_flag",
            description="Global flag type",
            log_available=False,
            shm_available=True,
            shm_field_name="global_flag",
            notes="From graphics SHM"
        ),
    ])
    
    return result


def print_comparison_report(result: ComparisonResult):
    """Print a detailed comparison report"""
    
    # Categorize fields
    result.logs_only = [f for f in result.fields if f.source == DataSource.LOGS]
    result.shm_only = [f for f in result.fields if f.source == DataSource.SHARED_MEMORY]
    result.both = [f for f in result.fields if f.source == DataSource.BOTH]
    
    print("=" * 80)
    print("LOG PARSER vs SHARED MEMORY DATA COMPARISON")
    print("=" * 80)
    print()
    
    # Summary statistics
    print(f"Total Fields Analyzed: {len(result.fields)}")
    print(f"Logs Only: {len(result.logs_only)} fields")
    print(f"Shared Memory Only: {len(result.shm_only)} fields")
    print(f"Both Sources: {len(result.both)} fields")
    print()
    
    # Logs Only Section
    print("=" * 80)
    print("DATA ONLY IN LOGS (CRITICAL FOR SESSION TRACKING)")
    print("=" * 80)
    print()
    
    if result.logs_only:
        for field in result.logs_only:
            print(f"📋 {field.name}")
            print(f"   Description: {field.description}")
            print(f"   Log Field: {field.log_field_name}")
            print(f"   Notes: {field.notes}")
            print()
    else:
        print("No fields found only in logs.")
        print()
    
    # Shared Memory Only Section
    print("=" * 80)
    print("DATA ONLY IN SHARED MEMORY (CRITICAL FOR TELEMETRY)")
    print("=" * 80)
    print()
    
    if result.shm_only:
        for field in result.shm_only:
            print(f"📊 {field.name}")
            print(f"   Description: {field.description}")
            print(f"   SHM Field: {field.shm_field_name}")
            print(f"   Notes: {field.notes}")
            print()
    else:
        print("No fields found only in shared memory.")
        print()
    
    # Both Sources Section
    print("=" * 80)
    print("DATA IN BOTH SOURCES (USE PRIORITY: LOGS > SHM)")
    print("=" * 80)
    print()
    
    if result.both:
        for field in result.both:
            print(f"🔄 {field.name}")
            print(f"   Description: {field.description}")
            print(f"   Log Field: {field.log_field_name}")
            print(f"   SHM Field: {field.shm_field_name}")
            print(f"   Notes: {field.notes}")
            print()
    else:
        print("No fields found in both sources.")
        print()
    
    # Critical Gaps Analysis
    print("=" * 80)
    print("CRITICAL GAPS ANALYSIS")
    print("=" * 80)
    print()
    
    print("🚨 DATA ONLY IN LOGS (Cannot be replaced by SHM):")
    print()
    critical_log_fields = [
        "physics_lap_number", "sector1_ms", "sector2_ms", "sector3_ms",
        "sectors_consistent", "lap_state", "has_track_limit_violation",
        "has_penalty", "fuel_reliable", "tyre_compound", "player_id",
        "setup_notes", "stint_number", "distance_hundredm"
    ]
    
    for field in result.logs_only:
        if field.name in critical_log_fields:
            print(f"  ❌ {field.name}: {field.description}")
    
    print()
    print("✅ DATA ONLY IN SHM (Cannot be replaced by logs):")
    print()
    critical_shm_fields = [
        "speed_kmh", "rpm", "gas", "brake", "steer_angle", "gear",
        "acc_g", "wheel_slip", "wheel_load", "suspension_travel",
        "ride_height", "pitch", "roll", "slip_ratio", "slip_angle",
        "fx", "fy", "brake_temp", "brake_torque", "air_density"
    ]
    
    for field in result.shm_only:
        if field.name in critical_shm_fields:
            print(f"  ✅ {field.name}: {field.description}")
    
    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()
    print("1. ✅ Keep logs for: Lap timing, validity, session metadata, fuel tracking")
    print("2. ✅ Keep SHM for: Telemetry, car setup, real-time data, aerodynamics")
    print("3. 🔄 Use priority system: Logs (definitive) > SHM (real-time) > Calculated")
    print("4. 📊 Shared session data should combine both sources")
    print("5. 🎯 Logs are authoritative for lap completion events")
    print("6. 🎯 SHM is authoritative for real-time telemetry and car setup")
    print()


def main():
    """Main function to run the comparison analysis"""
    result = analyze_data_sources()
    print_comparison_report(result)


if __name__ == "__main__":
    main()
