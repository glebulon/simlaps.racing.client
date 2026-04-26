"""
AC Evo Shared Memory Decoder with Fallback
========================================
Tries AC/ACC structure first, falls back to pattern detection for AC Evo.

Ported from test_scripts/telemetry/ac_evo_decoder.py
"""

import math
import struct
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Any, Dict, List, Optional


class AC_STATUS(Enum):
    AC_OFF = 0
    AC_REPLAY = 1
    AC_LIVE = 2
    AC_PAUSE = 3


class AC_SESSION_TYPE(Enum):
    AC_UNKNOWN = -1
    AC_PRACTICE = 0
    AC_QUALIFY = 1
    AC_RACE = 2
    AC_HOTLAP = 3
    AC_TIME_ATTACK = 4
    AC_DRIFT = 5
    AC_DRAG = 6


class AC_FLAG_TYPE(Enum):
    AC_NO_FLAG = 0
    AC_BLUE_FLAG = 1
    AC_YELLOW_FLAG = 2
    AC_BLACK_FLAG = 3
    AC_WHITE_FLAG = 4
    AC_CHECKERED_FLAG = 5
    AC_PENALTY_FLAG = 6


GRAPHICS_STRUCT_SIZE = 2048
STATIC_HEADER_SIZE = 404
STATIC_STRUCT_SIZE = 820
MAX_GRAPHICS_CARS = 60


@dataclass
class Coords:
    x: float
    y: float
    z: float


@dataclass
class Physics:
    packet_id: int
    gas: float
    brake: float
    fuel: float
    gear: int
    rpms: int
    steer_angle: float
    speed_kmh: float
    velocity: Coords
    acc_g: Coords
    wheel_slip: List[float]
    wheel_load: List[float]
    wheels_pressure: List[float]
    wheel_angular_speed: List[float]
    tyre_wear: List[float]
    tyre_dirty_level: List[float]
    tyre_core_temp: List[float]
    camber_rad: List[float]
    suspension_travel: List[float]
    drs: float
    tc: float
    heading: float
    pitch: float
    roll: float
    cg_height: float
    car_damage: List[float]
    number_of_tyres_out: int
    pit_limiter_on: bool
    abs: float
    kers_charge: float
    kers_input: float
    auto_shifter_on: bool
    ride_height: List[float]
    turbo_boost: float
    ballast: float
    air_density: float
    air_temp: float
    road_temp: float
    local_angular_velocity: Coords
    final_ff: float
    performance_meter: float
    engine_brake: int
    ers_recovery_level: int
    ers_power_level: int
    ers_heat_charging: int
    ers_is_charging: int
    kers_current_kj: float
    drs_available: bool
    drs_enabled: bool
    brake_temp: List[float]
    clutch: float
    tyre_temp_i: List[float]
    tyre_temp_m: List[float]
    tyre_temp_o: List[float]
    is_ai_controlled: bool
    tyre_contact_point: List[Coords]
    tyre_contact_normal: List[Coords]
    tyre_contact_heading: List[Coords]
    brake_bias: float
    local_velocity: Coords
    # AC Evo precision fields
    p2p_activations: int
    p2p_status: int
    current_max_rpm: int
    mz: List[float]
    fx: List[float]
    fy: List[float]
    slip_ratio: List[float]
    slip_angle: List[float]
    tcin_action: bool
    absin_action: bool
    suspension_damage: List[float]
    tyre_temp: List[float]
    water_temp: float
    brake_torque: List[float]
    front_brake_compound: int
    rear_brake_compound: int
    pad_life: List[float]
    disc_life: List[float]
    ignition_on: bool
    starter_engine_on: bool
    is_engine_running: bool
    kerb_vibration: float
    slip_vibrations: float
    groad_vibrations: float
    abs_vibrations: float


class R:
    """Binary reader for AC shared memory structures."""

    def __init__(self, data: bytes):
        self._b = BytesIO(data)
        self._pos = 0

    def i(self) -> int:
        val = struct.unpack("=i", self._b.read(4))[0]
        self._pos += 4
        return val

    def f(self) -> float:
        val = struct.unpack("=f", self._b.read(4))[0]
        self._pos += 4
        return val

    def fa(self, n: int) -> List[float]:
        vals = list(struct.unpack(f"={n}f", self._b.read(4 * n)))
        self._pos += 4 * n
        return vals

    def ia(self, n: int) -> List[int]:
        vals = list(struct.unpack(f"={n}i", self._b.read(4 * n)))
        self._pos += 4 * n
        return vals

    def coords(self) -> Coords:
        x, y, z = struct.unpack("=3f", self._b.read(12))
        self._pos += 12
        return Coords(x, y, z)

    def coords_list(self, n: int) -> List[Coords]:
        coords = []
        for _ in range(n):
            coords.append(self.coords())
        return coords

    def s(self, n: int, pad: int = 0) -> str:
        raw = self._b.read(2 * n + pad)
        self._pos += 2 * n + pad
        return raw[:2 * n].decode("utf-16-le", errors="ignore").rstrip("\x00")

    def skip(self, n: int):
        self._b.read(n)
        self._pos += n


def _enum_name(enum_cls, value: int) -> Optional[str]:
    try:
        return enum_cls(value).name
    except ValueError:
        return None


def _coords_to_dict(coords: Coords) -> Dict[str, float]:
    return {"x": coords.x, "y": coords.y, "z": coords.z}


def _has_meaningful_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, list):
        return any(_has_meaningful_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    return value is not None


def _scan_utf16_strings(data: bytes, min_chars: int = 4, start_offset: int = 0) -> List[Dict[str, Any]]:
    strings: List[Dict[str, Any]] = []
    cursor_start = start_offset if start_offset % 2 == 0 else start_offset + 1
    last_end = -1

    for start in range(cursor_start, max(cursor_start, len(data) - min_chars * 2 + 1), 2):
        if start < last_end:
            continue

        chars: List[str] = []
        cursor = start
        while cursor + 1 < len(data):
            lo = data[cursor]
            hi = data[cursor + 1]
            if hi != 0 or lo == 0 or not (32 <= lo <= 126):
                break
            chars.append(chr(lo))
            cursor += 2

        if len(chars) >= min_chars:
            strings.append({"offset": start, "value": "".join(chars)})
            last_end = cursor

    return strings


def _word_candidate(data: bytes, offset: int, field: str) -> Dict[str, Any]:
    return {
        "field": field,
        "offset": offset,
        "raw_hex": data[offset:offset + 4].hex(),
        "int_value": struct.unpack_from("<i", data, offset)[0],
        "float_value": struct.unpack_from("<f", data, offset)[0],
    }


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sanitize_float_field(result: Dict[str, Any], field: str, invalid_reasons: List[str], low: float, high: float) -> None:
    value = result.get(field)
    if value is None:
        return
    if not _is_finite_number(value):
        result[field] = None
        invalid_reasons.append(f"{field}:non_finite")
        return
    value = float(value)
    if value < low or value > high:
        result[field] = None
        invalid_reasons.append(f"{field}:out_of_range")
        return
    result[field] = value


def _sanitize_int_field(result: Dict[str, Any], field: str, invalid_reasons: List[str], low: int, high: int) -> None:
    value = result.get(field)
    if value is None:
        return
    try:
        value = int(value)
    except (TypeError, ValueError):
        result[field] = None
        invalid_reasons.append(f"{field}:not_int")
        return
    if value < low or value > high:
        result[field] = None
        invalid_reasons.append(f"{field}:out_of_range")
        return
    result[field] = value


def _sanitize_coords(value: Any, invalid_reasons: List[str], field: str, abs_max: float) -> Optional[Dict[str, Optional[float]]]:
    if not isinstance(value, dict):
        return None

    cleaned: Dict[str, Optional[float]] = {}
    for axis in ("x", "y", "z"):
        axis_value = value.get(axis)
        if axis_value is None:
            cleaned[axis] = None
            continue
        if not _is_finite_number(axis_value):
            cleaned[axis] = None
            invalid_reasons.append(f"{field}.{axis}:non_finite")
            continue
        axis_value = float(axis_value)
        if abs(axis_value) > abs_max:
            cleaned[axis] = None
            invalid_reasons.append(f"{field}.{axis}:out_of_range")
            continue
        cleaned[axis] = axis_value

    return cleaned


def _sanitize_float_list(value: Any, invalid_reasons: List[str], field: str, low: float, high: float) -> Any:
    if not isinstance(value, list):
        return value

    cleaned = []
    for idx, item in enumerate(value):
        if item is None:
            cleaned.append(None)
            continue
        if not _is_finite_number(item):
            cleaned.append(None)
            invalid_reasons.append(f"{field}[{idx}]:non_finite")
            continue
        item = float(item)
        if item < low or item > high:
            cleaned.append(None)
            invalid_reasons.append(f"{field}[{idx}]:out_of_range")
            continue
        cleaned.append(item)
    return cleaned


def _sanitize_physics_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    invalid_reasons: List[str] = []

    result["decode_source"] = result.get("_decoder")
    _sanitize_float_field(result, "gas", invalid_reasons, 0.0, 1.05)
    _sanitize_float_field(result, "brake", invalid_reasons, 0.0, 1.05)
    _sanitize_float_field(result, "clutch", invalid_reasons, 0.0, 1.05)
    _sanitize_float_field(result, "speed_kmh", invalid_reasons, 0.0, 450.0)
    _sanitize_float_field(result, "steer_angle", invalid_reasons, -10.0, 10.0)
    _sanitize_float_field(result, "air_temp", invalid_reasons, -50.0, 100.0)
    _sanitize_float_field(result, "road_temp", invalid_reasons, -50.0, 120.0)
    _sanitize_int_field(result, "gear", invalid_reasons, -1, 12)
    _sanitize_int_field(result, "rpms", invalid_reasons, 0, 20000)

    result["velocity"] = _sanitize_coords(result.get("velocity"), invalid_reasons, "velocity", abs_max=200.0)
    result["acc_g"] = _sanitize_coords(result.get("acc_g"), invalid_reasons, "acc_g", abs_max=20.0)
    result["local_velocity"] = _sanitize_coords(result.get("local_velocity"), invalid_reasons, "local_velocity", abs_max=200.0)
    result["local_angular_velocity"] = _sanitize_coords(result.get("local_angular_velocity"), invalid_reasons, "local_angular_velocity", abs_max=50.0)

    result["wheel_slip"] = _sanitize_float_list(result.get("wheel_slip"), invalid_reasons, "wheel_slip", 0.0, 5.0)
    result["wheel_load"] = _sanitize_float_list(result.get("wheel_load"), invalid_reasons, "wheel_load", 0.0, 50000.0)
    result["wheels_pressure"] = _sanitize_float_list(result.get("wheels_pressure"), invalid_reasons, "wheels_pressure", 0.0, 80.0)
    result["tyre_core_temp"] = _sanitize_float_list(result.get("tyre_core_temp"), invalid_reasons, "tyre_core_temp", 0.0, 200.0)
    result["brake_temp"] = _sanitize_float_list(result.get("brake_temp"), invalid_reasons, "brake_temp", 0.0, 2000.0)
    result["tyre_temp_i"] = _sanitize_float_list(result.get("tyre_temp_i"), invalid_reasons, "tyre_temp_i", 0.0, 200.0)
    result["tyre_temp_m"] = _sanitize_float_list(result.get("tyre_temp_m"), invalid_reasons, "tyre_temp_m", 0.0, 200.0)
    result["tyre_temp_o"] = _sanitize_float_list(result.get("tyre_temp_o"), invalid_reasons, "tyre_temp_o", 0.0, 200.0)
    
    # AC Evo precision fields
    result["fx"] = _sanitize_float_list(result.get("fx"), invalid_reasons, "fx", -50000.0, 50000.0)
    result["fy"] = _sanitize_float_list(result.get("fy"), invalid_reasons, "fy", -50000.0, 50000.0)
    result["mz"] = _sanitize_float_list(result.get("mz"), invalid_reasons, "mz", -10000.0, 10000.0)
    result["slip_ratio"] = _sanitize_float_list(result.get("slip_ratio"), invalid_reasons, "slip_ratio", -5.0, 5.0)
    result["slip_angle"] = _sanitize_float_list(result.get("slip_angle"), invalid_reasons, "slip_angle", -1.6, 1.6)
    result["brake_torque"] = _sanitize_float_list(result.get("brake_torque"), invalid_reasons, "brake_torque", 0.0, 50000.0)
    result["suspension_damage"] = _sanitize_float_list(result.get("suspension_damage"), invalid_reasons, "suspension_damage", 0.0, 1.0)
    result["pad_life"] = _sanitize_float_list(result.get("pad_life"), invalid_reasons, "pad_life", 0.0, 1.0)
    result["disc_life"] = _sanitize_float_list(result.get("disc_life"), invalid_reasons, "disc_life", 0.0, 1.0)
    _sanitize_float_field(result, "water_temp", invalid_reasons, 0.0, 150.0)
    _sanitize_int_field(result, "current_max_rpm", invalid_reasons, 0, 25000)

    # Calculate normalized_car_position from tyre_contact_point Z coordinates
    # This is the primary position source since graphics region is disabled
    tyre_contact_points = result.get("tyre_contact_point")
    if isinstance(tyre_contact_points, list) and len(tyre_contact_points) > 0:
        # Use the first tyre's Z coordinate (all should be similar)
        first_tyre = tyre_contact_points[0]
        if isinstance(first_tyre, dict):
            z_coord = first_tyre.get("z", 0.0)
            if isinstance(z_coord, (int, float)) and z_coord != 0.0:
                # Normalize Z coordinate to 0-1 range
                # Most tracks have Z in range -2000 to 2000
                estimated_norm = (z_coord + 2000) / 4000
                estimated_norm = max(0.0, min(1.0, estimated_norm))
                result["normalized_car_position"] = estimated_norm
                result["normalized_position_source"] = "physics_tyre_z"

    # Physics-derived position is NOT authoritative (only graphics position is)
    result["has_authoritative_progress"] = False

    core_fields = ["speed_kmh", "gas", "brake", "gear", "rpms", "steer_angle"]
    valid_core_fields = sum(1 for field in core_fields if result.get(field) is not None)
    result["quality_score"] = round(valid_core_fields / len(core_fields), 3)
    result["invalid_reasons"] = sorted(dict.fromkeys(invalid_reasons))
    result["is_plausible"] = result["quality_score"] >= 0.67 and result.get("speed_kmh") is not None
    return result


def _sanitize_graphics_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    invalid_reasons: List[str] = []

    result["decode_source"] = result.get("_decoder")
    _sanitize_int_field(result, "completed_laps", invalid_reasons, 0, 10000)
    _sanitize_int_field(result, "position", invalid_reasons, 0, 200)
    _sanitize_int_field(result, "current_time_ms", invalid_reasons, 0, 10000000)
    _sanitize_int_field(result, "last_time_ms", invalid_reasons, 0, 10000000)
    _sanitize_int_field(result, "best_time_ms", invalid_reasons, 0, 10000000)
    _sanitize_int_field(result, "number_of_laps", invalid_reasons, 0, 10000)
    _sanitize_int_field(result, "active_cars", invalid_reasons, 0, MAX_GRAPHICS_CARS)
    _sanitize_float_field(result, "distance_traveled", invalid_reasons, 0.0, 1000000.0)

    # Calculate normalized_car_position from Z coordinates if not provided
    if result.get("normalized_car_position") == 0.0 and "car_coordinates" in result and result["car_coordinates"]:
        # Use player car's Z coordinate to estimate position
        player_car_id = result.get("player_car_id", 0)
        if player_car_id < len(result["car_coordinates"]):
            z_coord = result["car_coordinates"][player_car_id].get("z", 0.0)
            # Simple normalization: map Z range to 0-1
            # This is a rough estimate - track bounds would be better
            if z_coord != 0.0:
                # Scale Z coordinate to roughly 0-1 range
                # Most tracks have Z in range -1000 to 1000
                estimated_norm = (z_coord + 1000) / 2000
                estimated_norm = max(0.0, min(1.0, estimated_norm))
                result["normalized_car_position"] = estimated_norm
                result["normalized_position_source"] = "calculated_from_z"

    _sanitize_float_field(result, "normalized_car_position", invalid_reasons, 0.0, 1.0)

    active_cars = result.get("active_cars") or 0
    if isinstance(result.get("car_coordinates"), list):
        result["car_coordinates"] = result["car_coordinates"][:active_cars]
    if isinstance(result.get("car_ids"), list):
        result["car_ids"] = result["car_ids"][:active_cars]

    core_fields = [
        "normalized_car_position",
        "completed_laps",
        "current_time_ms",
        "distance_traveled",
        "current_sector_index",
        "is_valid_lap",
    ]
    valid_core_fields = sum(1 for field in core_fields if result.get(field) is not None)
    result["quality_score"] = round(valid_core_fields / len(core_fields), 3)
    result["invalid_reasons"] = sorted(dict.fromkeys(invalid_reasons))
    result["has_authoritative_progress"] = result.get("normalized_car_position") is not None
    return result


def decode_physics_ac(data: bytes) -> Optional[Physics]:
    """Try to decode physics using AC/ACC structure."""
    try:
        r = R(data)
        return Physics(
            packet_id=r.i(),
            gas=r.f(),
            brake=r.f(),
            fuel=r.f(),
            gear=r.i(),
            rpms=r.i(),
            steer_angle=r.f(),
            speed_kmh=r.f(),
            velocity=r.coords(),
            acc_g=r.coords(),
            wheel_slip=r.fa(4),
            wheel_load=r.fa(4),
            wheels_pressure=r.fa(4),
            wheel_angular_speed=r.fa(4),
            tyre_wear=r.fa(4),
            tyre_dirty_level=r.fa(4),
            tyre_core_temp=r.fa(4),
            camber_rad=r.fa(4),
            suspension_travel=r.fa(4),
            drs=r.f(),
            tc=r.f(),
            heading=r.f(),
            pitch=r.f(),
            roll=r.f(),
            cg_height=r.f(),
            car_damage=r.fa(5),
            number_of_tyres_out=r.i(),
            pit_limiter_on=bool(r.i()),
            abs=r.f(),
            kers_charge=r.f(),
            kers_input=r.f(),
            auto_shifter_on=bool(r.i()),
            ride_height=r.fa(2),
            turbo_boost=r.f(),
            ballast=r.f(),
            air_density=r.f(),
            air_temp=r.f(),
            road_temp=r.f(),
            local_angular_velocity=r.coords(),
            final_ff=r.f(),
            performance_meter=r.f(),
            engine_brake=r.i(),
            ers_recovery_level=r.i(),
            ers_power_level=r.i(),
            ers_heat_charging=r.i(),
            ers_is_charging=r.i(),
            kers_current_kj=r.f(),
            drs_available=bool(r.i()),
            drs_enabled=bool(r.i()),
            brake_temp=r.fa(4),
            clutch=r.f(),
            tyre_temp_i=r.fa(4),
            tyre_temp_m=r.fa(4),
            tyre_temp_o=r.fa(4),
            is_ai_controlled=bool(r.i()),
            tyre_contact_point=r.coords_list(4),
            tyre_contact_normal=r.coords_list(4),
            tyre_contact_heading=r.coords_list(4),
            brake_bias=r.f(),
            local_velocity=r.coords(),
            # AC Evo precision fields (may not exist in older formats)
            p2p_activations=r.i() if r._pos + 4 <= len(data) else 0,
            p2p_status=r.i() if r._pos + 4 <= len(data) else 0,
            current_max_rpm=r.i() if r._pos + 4 <= len(data) else 0,
            mz=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            fx=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            fy=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            slip_ratio=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            slip_angle=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            tcin_action=bool(r.i()) if r._pos + 4 <= len(data) else False,
            absin_action=bool(r.i()) if r._pos + 4 <= len(data) else False,
            suspension_damage=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            tyre_temp=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            water_temp=r.f() if r._pos + 4 <= len(data) else 0.0,
            brake_torque=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            front_brake_compound=r.i() if r._pos + 4 <= len(data) else 0,
            rear_brake_compound=r.i() if r._pos + 4 <= len(data) else 0,
            pad_life=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            disc_life=r.fa(4) if r._pos + 16 <= len(data) else [0.0] * 4,
            ignition_on=bool(r.i()) if r._pos + 4 <= len(data) else False,
            starter_engine_on=bool(r.i()) if r._pos + 4 <= len(data) else False,
            is_engine_running=bool(r.i()) if r._pos + 4 <= len(data) else False,
            kerb_vibration=r.f() if r._pos + 4 <= len(data) else 0.0,
            slip_vibrations=r.f() if r._pos + 4 <= len(data) else 0.0,
            groad_vibrations=r.f() if r._pos + 4 <= len(data) else 0.0,
            abs_vibrations=r.f() if r._pos + 4 <= len(data) else 0.0,
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# AC Evo SPageFileGraphicEvo decoder
# ─────────────────────────────────────────────────────────────────────────────
#
# Offsets below were validated empirically by ``tools/explore_graphics_offsets.py``
# against captured raw graphics bytes. Validation strategy:
#
# - ``gear`` was located via byte-pattern search across 393/400 active frames
#   at offset 68 (anchor).
# - All subsequent offsets were predicted from the anchor under MSVC-default
#   ``_pack_=4`` rules and then probed for plausible values across 5 sample
#   frames.
# - Cross-checks: ``rpm_percent`` ≈ ``physics.rpms / max_rpm`` ✓,
#   ``fuel_liter_current_quantity`` matches ``physics.fuel`` exactly ✓,
#   ``g_forces_x`` matches ``physics.acc_g.x`` ✓, ``car_model = 'Dallara EXP'``
#   ✓, ``driver_name = 'Glebulon'`` ✓, ``car_location = 4 (ACEVO_TRACK)`` ✓,
#   ``is_valid_lap = 1`` ✓, ``current_pos`` shows driver moving from P2 to P1 ✓.
# - ``npos`` at offset 1244 produces smooth monotonic progress (0.016 → 0.295
#   across 42 s of lap 1) where physics dead-reckoning periodically returned
#   0.0 — confirming the graphics value is the authoritative source.
#
# The 15 ``bool`` fields documented between ``rpm`` and ``display_speed_kmh``
# are skipped; the doc's bool-count appears to disagree with reality and we
# don't currently need them. Sub-structs (tyre states, damage, electronics,
# pit info, instrumentation, session/timing state, assists, car_coordinates)
# are also skipped — only their fixed sizes matter for offset arithmetic.
GRAPHICS_EVO_MIN_SIZE = 4096

# Top-level scalar offsets (0-indexed bytes from start of buffer).
_GE_PACKET_ID = 0
_GE_STATUS = 4
_GE_FOCUSED_CAR_ID_A = 8
_GE_FOCUSED_CAR_ID_B = 16
_GE_PLAYER_CAR_ID_A = 24
_GE_PLAYER_CAR_ID_B = 32
_GE_RPM = 40
_GE_DISPLAY_SPEED_KMH = 58
_GE_DISPLAY_SPEED_MPH = 60
_GE_DISPLAY_SPEED_MS = 62
_GE_PITSPEEDING_DELTA = 64
_GE_GEAR_INT = 68
_GE_RPM_PERCENT = 72
_GE_GAS_PERCENT = 76
_GE_BRAKE_PERCENT = 80
_GE_HANDBRAKE_PERCENT = 84
_GE_CLUTCH_PERCENT = 88
_GE_STEERING_PERCENT = 92
_GE_FFB_STRENGTH = 96
_GE_CAR_FFB_MULTIPLIER = 100
_GE_WATER_TEMPERATURE_PERCENT = 104
_GE_WATER_PRESSURE_BAR = 108
_GE_FUEL_PRESSURE_BAR = 112
_GE_WATER_TEMPERATURE_C = 116
_GE_AIR_TEMPERATURE_C = 117
_GE_OIL_TEMPERATURE_C = 120
_GE_OIL_PRESSURE_BAR = 124
_GE_EXHAUST_TEMPERATURE_C = 128
_GE_G_FORCES_X = 132
_GE_G_FORCES_Y = 136
_GE_G_FORCES_Z = 140
_GE_TURBO_BOOST = 144
_GE_TURBO_BOOST_LEVEL = 148
_GE_TURBO_BOOST_PERC = 152
_GE_STEER_DEGREES = 156
_GE_CURRENT_KM = 160
_GE_TOTAL_KM = 164
_GE_TOTAL_DRIVING_TIME_S = 168
_GE_TIME_OF_DAY_HOURS = 172
_GE_TIME_OF_DAY_MINUTES = 176
_GE_TIME_OF_DAY_SECONDS = 180
_GE_DELTA_TIME_MS = 184
_GE_CURRENT_LAP_TIME_MS = 188
_GE_PREDICTED_LAP_TIME_MS = 192
_GE_FUEL_LITER_CURRENT_QUANTITY = 196
_GE_FUEL_LITER_CURRENT_QUANTITY_PERCENT = 200
_GE_FUEL_LITER_PER_KM = 204
_GE_KM_PER_FUEL_LITER = 208
_GE_CURRENT_TORQUE = 212
_GE_CURRENT_BHP = 216
# 4 × SMEvoTyreState (256 B each) starts at 220 and ends at 1244.
_GE_NPOS = 1244
_GE_KERS_CHARGE_PERC = 1248
_GE_KERS_CURRENT_PERC = 1252
_GE_CONTROL_LOCK_TIME = 1256
# car_damage SMEvoDamageState (128 B) at 1260
_GE_CAR_LOCATION = 1388
# pit_info SMEvoPitInfo (64 B) at 1392
_GE_FUEL_LITER_USED = 1456
_GE_FUEL_LITER_PER_LAP = 1460
_GE_LAPS_POSSIBLE_WITH_FUEL = 1464
_GE_BATTERY_TEMPERATURE = 1468
_GE_BATTERY_VOLTAGE = 1472
_GE_INSTANTANEOUS_FUEL_LITER_PER_KM = 1476
_GE_INSTANTANEOUS_KM_PER_FUEL_LITER = 1480
_GE_GEAR_RPM_WINDOW = 1484
# instrumentation × 3 (128 B each) at 1488..1872
# electronics × 4 (128 B each) at 1872..2384
_GE_TOTAL_LAP_COUNT = 2384
_GE_CURRENT_POS = 2388
_GE_TOTAL_DRIVERS = 2392
_GE_LAST_LAPTIME_MS = 2396
_GE_BEST_LAPTIME_MS = 2400
_GE_FLAG = 2404
_GE_GLOBAL_FLAG = 2408
_GE_MAX_GEARS = 2412
_GE_ENGINE_TYPE = 2416
_GE_HAS_KERS = 2420
_GE_IS_LAST_LAP = 2421
_GE_PERFORMANCE_MODE_NAME = 2422  # char[33]
_GE_DIFF_COAST_RAW_VALUE = 2456
_GE_DIFF_POWER_RAW_VALUE = 2460
_GE_RACE_CUT_GAINED_TIME_MS = 2464
_GE_DISTANCE_TO_DEADLINE = 2468
_GE_RACE_CUT_CURRENT_DELTA = 2472
# session_state SMEvoSessionState (256 B) at 2476
_GE_SESSION_PHASE_NAME = 2476
_GE_SESSION_TIME_LEFT = 2511
_GE_SESSION_TOTAL_LAP = 2515
_GE_SESSION_CURRENT_LAP = 2519
_GE_SESSION_LAP_LENGTH_KM = 2527
# timing_state SMEvoTimingState (256 B) at 2732
_GE_TIMING_CURRENT_LAPTIME = 2732
_GE_TIMING_DELTA_CURRENT = 2747
_GE_TIMING_DELTA_LAST = 2762
_GE_TIMING_BEST_LAPTIME = 2777
_GE_TIMING_IDEAL_LAPTIME = 2792
_GE_TIMING_TOTAL_TIME = 2807
_GE_TIMING_IS_INVALID = 2822
_GE_PLAYER_PING = 2988
_GE_PLAYER_LATENCY = 2992
_GE_PLAYER_CPU_USAGE = 2996
_GE_PLAYER_CPU_USAGE_AVG = 3000
_GE_PLAYER_QOS = 3004
_GE_PLAYER_QOS_AVG = 3008
_GE_PLAYER_FPS = 3012
_GE_PLAYER_FPS_AVG = 3016
_GE_DRIVER_NAME = 3020  # char[33]
_GE_DRIVER_SURNAME = 3053  # char[33]
_GE_CAR_MODEL = 3086  # char[33]
_GE_IS_IN_PIT_BOX = 3119
_GE_IS_IN_PIT_LANE = 3120
_GE_IS_VALID_LAP = 3121


def _read_cstring(data: bytes, offset: int, max_length: int) -> str:
    """Read a null-terminated ASCII string of at most ``max_length`` bytes."""
    if offset + max_length > len(data):
        return ""
    raw = data[offset:offset + max_length]
    null_idx = raw.find(b"\x00")
    if null_idx >= 0:
        raw = raw[:null_idx]
    try:
        return raw.decode("ascii", errors="replace").rstrip()
    except Exception:
        return ""


def decode_graphics_evo(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode the AC Evo ``SPageFileGraphicEvo`` shared-memory region.

    Returns ``None`` if the buffer is too small or fails sanity checks.
    Otherwise returns a dict with both AC Evo-native field names and
    legacy aliases (``normalized_car_position``, ``completed_laps``,
    ``last_time_ms``, ``best_time_ms``, etc.) so existing analyzer code
    continues to work without changes.
    """
    if len(data) < GRAPHICS_EVO_MIN_SIZE:
        return None

    try:
        # Read everything in a single pass. ``unpack_from`` gracefully
        # handles unaligned offsets (we tolerated _pack_=4-style packing).
        npos = struct.unpack_from("<f", data, _GE_NPOS)[0]
        # Sanity check: npos must be a finite float in [-0.05, 1.05]. If we
        # got the offset wrong (unlikely after empirical validation) the
        # value would land far outside this range and we bail out so the
        # legacy / fallback decoders get a chance.
        if not (math.isfinite(npos) and -0.05 <= npos <= 1.05):
            return None

        packet_id = struct.unpack_from("<i", data, _GE_PACKET_ID)[0]
        status = struct.unpack_from("<i", data, _GE_STATUS)[0]
        rpm = struct.unpack_from("<H", data, _GE_RPM)[0]
        display_speed_kmh = struct.unpack_from("<h", data, _GE_DISPLAY_SPEED_KMH)[0]
        display_speed_mph = struct.unpack_from("<h", data, _GE_DISPLAY_SPEED_MPH)[0]
        gear_int = struct.unpack_from("<h", data, _GE_GEAR_INT)[0]
        rpm_percent = struct.unpack_from("<f", data, _GE_RPM_PERCENT)[0]
        gas_percent = struct.unpack_from("<f", data, _GE_GAS_PERCENT)[0]
        brake_percent = struct.unpack_from("<f", data, _GE_BRAKE_PERCENT)[0]
        clutch_percent = struct.unpack_from("<f", data, _GE_CLUTCH_PERCENT)[0]
        steering_percent = struct.unpack_from("<f", data, _GE_STEERING_PERCENT)[0]

        water_temperature_c = struct.unpack_from("<b", data, _GE_WATER_TEMPERATURE_C)[0]
        air_temperature_c = struct.unpack_from("<b", data, _GE_AIR_TEMPERATURE_C)[0]
        oil_temperature_c = struct.unpack_from("<f", data, _GE_OIL_TEMPERATURE_C)[0]
        oil_pressure_bar = struct.unpack_from("<f", data, _GE_OIL_PRESSURE_BAR)[0]

        g_x = struct.unpack_from("<f", data, _GE_G_FORCES_X)[0]
        g_y = struct.unpack_from("<f", data, _GE_G_FORCES_Y)[0]
        g_z = struct.unpack_from("<f", data, _GE_G_FORCES_Z)[0]

        turbo_boost = struct.unpack_from("<f", data, _GE_TURBO_BOOST)[0]
        turbo_boost_perc = struct.unpack_from("<f", data, _GE_TURBO_BOOST_PERC)[0]
        steer_degrees = struct.unpack_from("<i", data, _GE_STEER_DEGREES)[0]
        current_km = struct.unpack_from("<f", data, _GE_CURRENT_KM)[0]

        current_lap_time_ms = struct.unpack_from("<i", data, _GE_CURRENT_LAP_TIME_MS)[0]
        predicted_lap_time_ms = struct.unpack_from("<i", data, _GE_PREDICTED_LAP_TIME_MS)[0]
        delta_time_ms = struct.unpack_from("<i", data, _GE_DELTA_TIME_MS)[0]

        fuel_liter_current_quantity = struct.unpack_from("<f", data, _GE_FUEL_LITER_CURRENT_QUANTITY)[0]
        fuel_liter_current_quantity_percent = struct.unpack_from("<f", data, _GE_FUEL_LITER_CURRENT_QUANTITY_PERCENT)[0]
        current_torque = struct.unpack_from("<f", data, _GE_CURRENT_TORQUE)[0]
        current_bhp = struct.unpack_from("<i", data, _GE_CURRENT_BHP)[0]

        car_location = struct.unpack_from("<i", data, _GE_CAR_LOCATION)[0]
        fuel_liter_used = struct.unpack_from("<f", data, _GE_FUEL_LITER_USED)[0]
        fuel_liter_per_lap = struct.unpack_from("<f", data, _GE_FUEL_LITER_PER_LAP)[0]
        laps_possible_with_fuel = struct.unpack_from("<f", data, _GE_LAPS_POSSIBLE_WITH_FUEL)[0]
        battery_voltage = struct.unpack_from("<f", data, _GE_BATTERY_VOLTAGE)[0]

        total_lap_count = struct.unpack_from("<i", data, _GE_TOTAL_LAP_COUNT)[0]
        current_pos = struct.unpack_from("<I", data, _GE_CURRENT_POS)[0]
        total_drivers = struct.unpack_from("<I", data, _GE_TOTAL_DRIVERS)[0]
        last_laptime_ms = struct.unpack_from("<i", data, _GE_LAST_LAPTIME_MS)[0]
        best_laptime_ms = struct.unpack_from("<i", data, _GE_BEST_LAPTIME_MS)[0]
        
        # ── Session State (definitive lap counting)
        session_phase_name = struct.unpack_from("<33s", data, _GE_SESSION_PHASE_NAME)[0].decode('utf-8', 'ignore').rstrip('\x00')
        session_time_left_ms = struct.unpack_from("<i", data, _GE_SESSION_TIME_LEFT)[0]
        session_total_lap = struct.unpack_from("<i", data, _GE_SESSION_TOTAL_LAP)[0]
        session_current_lap = struct.unpack_from("<i", data, _GE_SESSION_CURRENT_LAP)[0]
        session_lap_length_km = struct.unpack_from("<f", data, _GE_SESSION_LAP_LENGTH_KM)[0]
        
        # ── Timing State (definitive timing data)
        timing_current_laptime = struct.unpack_from("<15s", data, _GE_TIMING_CURRENT_LAPTIME)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_delta_current = struct.unpack_from("<15s", data, _GE_TIMING_DELTA_CURRENT)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_delta_last = struct.unpack_from("<15s", data, _GE_TIMING_DELTA_LAST)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_best_laptime = struct.unpack_from("<15s", data, _GE_TIMING_BEST_LAPTIME)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_ideal_laptime = struct.unpack_from("<15s", data, _GE_TIMING_IDEAL_LAPTIME)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_total_time = struct.unpack_from("<15s", data, _GE_TIMING_TOTAL_TIME)[0].decode('utf-8', 'ignore').rstrip('\x00')
        timing_is_invalid = bool(struct.unpack_from("<?", data, _GE_TIMING_IS_INVALID)[0])
        flag = struct.unpack_from("<i", data, _GE_FLAG)[0]
        global_flag = struct.unpack_from("<i", data, _GE_GLOBAL_FLAG)[0]
        max_gears = struct.unpack_from("<I", data, _GE_MAX_GEARS)[0]
        engine_type = struct.unpack_from("<i", data, _GE_ENGINE_TYPE)[0]

        diff_coast_raw_value = struct.unpack_from("<f", data, _GE_DIFF_COAST_RAW_VALUE)[0]
        diff_power_raw_value = struct.unpack_from("<f", data, _GE_DIFF_POWER_RAW_VALUE)[0]

        player_fps = struct.unpack_from("<i", data, _GE_PLAYER_FPS)[0]
        driver_name = _read_cstring(data, _GE_DRIVER_NAME, 33)
        driver_surname = _read_cstring(data, _GE_DRIVER_SURNAME, 33)
        car_model = _read_cstring(data, _GE_CAR_MODEL, 33)

        is_in_pit_box = bool(data[_GE_IS_IN_PIT_BOX])
        is_in_pit_lane = bool(data[_GE_IS_IN_PIT_LANE])
        is_valid_lap = bool(data[_GE_IS_VALID_LAP])

        focused_car_id = struct.unpack_from("<Q", data, _GE_FOCUSED_CAR_ID_A)[0]
        player_car_id = struct.unpack_from("<Q", data, _GE_PLAYER_CAR_ID_A)[0]
    except (struct.error, IndexError):
        return None

    return {
        "_decoder": "ac_evo_graphics",
        "buffer_size": len(data),
        # ── Identity / status
        "packet_id": packet_id,
        "status": status,
        "status_name": _enum_name(AC_STATUS, status),
        "focused_car_id": focused_car_id,
        "player_car_id": player_car_id,
        "driver_name": driver_name,
        "driver_surname": driver_surname,
        "car_model": car_model,
        # ── Authoritative track progress (the headline result)
        "npos": npos,
        "normalized_car_position": npos,             # legacy-compat alias
        "normalized_position_source": "graphics_npos",
        "has_authoritative_progress": True,
        # ── Powertrain / inputs
        "rpm": rpm,
        "rpm_percent": rpm_percent,
        "gear_int": gear_int,
        "max_gears": max_gears,
        "engine_type": engine_type,
        "gas_percent": gas_percent,
        "brake_percent": brake_percent,
        "clutch_percent": clutch_percent,
        "steering_percent": steering_percent,
        "steer_degrees": steer_degrees,
        "current_torque": current_torque,
        "current_bhp": current_bhp,
        "turbo_boost": turbo_boost,
        "turbo_boost_perc": turbo_boost_perc,
        # ── Speed
        "display_speed_kmh": display_speed_kmh,
        "display_speed_mph": display_speed_mph,
        # ── Temps / pressures
        "water_temperature_c": water_temperature_c,
        "air_temperature_c": air_temperature_c,
        "oil_temperature_c": oil_temperature_c,
        "oil_pressure_bar": oil_pressure_bar,
        # ── G-forces
        "g_forces_x": g_x,
        "g_forces_y": g_y,
        "g_forces_z": g_z,
        # ── Lap timing
        "current_lap_time_ms": current_lap_time_ms,
        "current_time_ms": current_lap_time_ms,      # legacy-compat alias
        "predicted_lap_time_ms": predicted_lap_time_ms,
        "delta_time_ms": delta_time_ms,
        "last_laptime_ms": last_laptime_ms,
        "last_time_ms": last_laptime_ms,             # legacy-compat alias
        "best_laptime_ms": best_laptime_ms,
        "best_time_ms": best_laptime_ms,             # legacy-compat alias
        "total_lap_count": total_lap_count,
        "completed_laps": total_lap_count,           # legacy-compat alias
        # ── Session State (definitive)
        "session_phase": session_phase_name,
        "session_time_left_ms": session_time_left_ms,
        "session_total_laps": session_total_lap,
        "session_current_lap": session_current_lap,  # Definitive lap number
        "lap_length_km": session_lap_length_km,
        # ── Timing State (definitive)
        "timing_current_laptime": timing_current_laptime,
        "timing_delta_current": timing_delta_current,
        "timing_delta_last": timing_delta_last,
        "timing_best_laptime": timing_best_laptime,
        "timing_ideal_laptime": timing_ideal_laptime,
        "timing_total_time": timing_total_time,
        "timing_is_invalid": timing_is_invalid,
        # ── Race state
        "current_pos": current_pos,
        "position": current_pos,                     # legacy-compat alias
        "total_drivers": total_drivers,
        "flag": flag,
        "flag_name": _enum_name(AC_FLAG_TYPE, flag),
        "global_flag": global_flag,
        "car_location": car_location,
        "is_in_pit_box": is_in_pit_box,
        "is_in_pit": is_in_pit_box,                  # legacy-compat alias
        "is_in_pit_lane": is_in_pit_lane,
        "is_valid_lap": is_valid_lap,
        # ── Fuel / energy
        "fuel_liter_current_quantity": fuel_liter_current_quantity,
        "fuel_liter_current_quantity_percent": fuel_liter_current_quantity_percent,
        "fuel_liter_used": fuel_liter_used,
        "fuel_liter_per_lap": fuel_liter_per_lap,
        "laps_possible_with_fuel": laps_possible_with_fuel,
        "battery_voltage": battery_voltage,
        # ── Setup / performance hints
        "diff_coast_raw_value": diff_coast_raw_value,
        "diff_power_raw_value": diff_power_raw_value,
        "current_km": current_km,
        # ── Diagnostics
        "player_fps": player_fps,
        # ── Fields that don't exist in AC Evo SPageFileGraphicEvo but
        # legacy ACC consumers may probe. Set to None so analyzer can
        # detect unavailability without a KeyError.
        "current_sector_index": None,
        "last_sector_time_ms": None,
        "number_of_laps": None,
    }


def decode_graphics_ac(data: bytes) -> Optional[Dict[str, Any]]:
    try:
        if len(data) < GRAPHICS_STRUCT_SIZE:
            return None

        r = R(data[:GRAPHICS_STRUCT_SIZE])
        packet_id = r.i()
        status = r.i()
        session = r.i()
        current_time = r.s(15)
        last_time = r.s(15)
        best_time = r.s(15)
        split = r.s(15)
        completed_laps = r.i()
        position = r.i()
        current_time_ms = r.i()
        last_time_ms = r.i()
        best_time_ms = r.i()
        session_time_left = r.f()
        distance_traveled = r.f()
        is_in_pit = bool(r.i())
        current_sector_index = r.i()
        last_sector_time_ms = r.i()
        number_of_laps = r.i()
        tyre_compound = r.s(33, pad=2)
        replay_time_multiplier = r.f()
        normalized_car_position = r.f()
        active_cars = max(0, min(r.i(), MAX_GRAPHICS_CARS))
        car_coordinates = [_coords_to_dict(coords) for coords in r.coords_list(MAX_GRAPHICS_CARS)]
        car_ids = r.ia(MAX_GRAPHICS_CARS)
        player_car_id = r.i()
        penalty_time = r.f()
        flag = r.i()

        return {
            "_decoder": "acc_graphics_structure",
            "buffer_size": len(data),
            "parsed_size": GRAPHICS_STRUCT_SIZE,
            "extra_bytes": max(0, len(data) - GRAPHICS_STRUCT_SIZE),
            "packet_id": packet_id,
            "status": status,
            "status_name": _enum_name(AC_STATUS, status),
            "session": session,
            "session_name": _enum_name(AC_SESSION_TYPE, session),
            "current_time": current_time,
            "last_time": last_time,
            "best_time": best_time,
            "split": split,
            "completed_laps": completed_laps,
            "position": position,
            "current_time_ms": current_time_ms,
            "last_time_ms": last_time_ms,
            "best_time_ms": best_time_ms,
            "session_time_left": session_time_left,
            "distance_traveled": distance_traveled,
            "is_in_pit": is_in_pit,
            "current_sector_index": current_sector_index,
            "last_sector_time_ms": last_sector_time_ms,
            "number_of_laps": number_of_laps,
            "tyre_compound": tyre_compound,
            "replay_time_multiplier": replay_time_multiplier,
            "normalized_car_position": normalized_car_position,
            "active_cars": active_cars,
            "car_coordinates": car_coordinates[:active_cars],
            "car_ids": car_ids[:active_cars],
            "player_car_id": player_car_id,
            "penalty_time": penalty_time,
            "flag": flag,
            "flag_name": _enum_name(AC_FLAG_TYPE, flag),
            "penalty": r.i(),
            "ideal_line_on": bool(r.i()),
            "is_in_pit_lane": bool(r.i()),
            "surface_grip": r.f(),
            "mandatory_pit_done": bool(r.i()),
            "wind_speed": r.f(),
            "wind_direction": r.f(),
            "is_setup_menu_visible": bool(r.i()),
            "main_display_index": r.i(),
            "secondary_display_index": r.i(),
            "tc_level": r.i(),
            "tc_cut_level": r.i(),
            "engine_map": r.i(),
            "abs_level": r.i(),
            "fuel_per_lap": r.f(),
            "rain_light": bool(r.i()),
            "flashing_light": bool(r.i()),
            "light_stage": r.i(),
            "exhaust_temp": r.f(),
            "wiper_stage": r.i(),
            "driver_stint_total_time_left": r.i(),
            "driver_stint_time_left": r.i(),
            "rain_tyres": bool(r.i()),
            "session_index": r.i(),
            "used_fuel": r.f(),
            "delta_lap_time": r.s(15, pad=2),
            "delta_lap_time_ms": r.i(),
            "estimated_lap_time": r.s(15, pad=2),
            "estimated_lap_time_ms": r.i(),
            "is_delta_positive": bool(r.i()),
            "split_ms": r.i(),
            "is_valid_lap": bool(r.i()),
            "fuel_estimated_laps": r.f(),
            "track_status": r.s(33, pad=2),
            "missing_mandatory_pits": r.i(),
            "clock": r.f(),
            "direction_light_left": bool(r.i()),
            "direction_light_right": bool(r.i()),
            "global_yellow": bool(r.i()),
            "global_yellow_s1": bool(r.i()),
            "global_yellow_s2": bool(r.i()),
            "global_yellow_s3": bool(r.i()),
            "global_white": bool(r.i()),
            "global_green": bool(r.i()),
            "global_chequered": bool(r.i()),
            "global_red": bool(r.i()),
            "mfd_tyre_set": r.i(),
            "mfd_fuel_to_add": r.f(),
            "mfd_tyre_pressure": r.fa(4),
            "track_grip_status": r.i(),
            "rain_intensity": r.i(),
            "rain_intensity_in_10min": r.i(),
            "rain_intensity_in_30min": r.i(),
            "current_tyre_set": r.i(),
            "strategy_tyre_set": r.i(),
            "gap_ahead": r.i(),
            "gap_behind": r.i(),
            # AC Evo extended fields (may not exist in older ACC format)
            "gear_rpm_window": r.f() if r._pos + 4 <= len(data) else None,
            "predicted_lap_time_ms": r.i() if r._pos + 4 <= len(data) else None,
            "delta_time_ms": r.i() if r._pos + 4 <= len(data) else None,
            "current_bhp": r.i() if r._pos + 4 <= len(data) else None,
            "current_torque": r.f() if r._pos + 4 <= len(data) else None,
            "rpm_percent": r.f() if r._pos + 4 <= len(data) else None,
        }
    except Exception:
        return None


# ── SPageFileStaticEvo offsets ────────────────────────────────────────────────
# Empirically validated against captured static SHM (Brands Hatch Indy session).
# All offsets follow the layout in ``ACE_SharedFileOut_Documentation_v1.md``
# under ``_pack_=4`` packing.  Buffer is 2048 bytes; first ~208 bytes are the
# fixed struct, remainder is reserved.
STATIC_EVO_MIN_SIZE = 208

_SE_SM_VERSION         = 0    # char[15]
_SE_AC_EVO_VERSION     = 15   # char[15]
_SE_SESSION            = 32   # int (enum ACEVO_SESSION_TYPE)
_SE_SESSION_NAME       = 36   # char[33]
_SE_EVENT_ID           = 69   # uint8_t
_SE_SESSION_ID         = 70   # uint8_t
_SE_STARTING_GRIP      = 72   # int (enum ACEVO_STARTING_GRIP)
_SE_AMB_TEMP_C         = 76   # float
_SE_GROUND_TEMP_C      = 80   # float
_SE_IS_STATIC_WEATHER  = 84   # bool
_SE_IS_TIMED_RACE      = 85   # bool
_SE_IS_ONLINE          = 86   # bool
_SE_NUMBER_OF_SESSIONS = 88   # int
_SE_NATION             = 92   # char[33]
_SE_LONGITUDE          = 128  # float
_SE_LATITUDE           = 132  # float
_SE_TRACK              = 136  # char[33]
_SE_TRACK_CONFIG       = 169  # char[33]
_SE_TRACK_LENGTH_M     = 204  # float


# ACEVO_SESSION_TYPE enum names (best-effort; the doc enumerates these but
# does not pin the integer values).  Treat unknown values as raw ints.
ACEVO_SESSION_TYPE = {
    0: "PRACTICE",
    1: "QUALIFY",
    2: "RACE",
    3: "HOTLAP",
    4: "TIME_ATTACK",
    5: "DRIFT",
    6: "DRAG",
}

# ACEVO_STARTING_GRIP enum names (same caveat).
ACEVO_STARTING_GRIP = {
    0: "GREEN",
    1: "FAST",
    2: "OPTIMUM",
    3: "GREASY",
    4: "DAMP",
    5: "WET",
    6: "FLOODED",
}


def decode_static_evo(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode the AC Evo ``SPageFileStaticEvo`` shared-memory region.

    Returns ``None`` if the buffer is too small or fails sanity checks.
    Otherwise returns a dict with both AC Evo-native field names and a
    handful of legacy aliases (``track``, ``track_configuration``,
    ``sm_version``, ``ac_version``, ``number_of_sessions``,
    ``track_spline_length``, ``is_timed_race``, ``is_online``) so existing
    analyzer / consumer code keeps working without changes.
    """
    if len(data) < STATIC_EVO_MIN_SIZE:
        return None

    try:
        sm_version     = _read_cstring(data, _SE_SM_VERSION, 15)
        ac_evo_version = _read_cstring(data, _SE_AC_EVO_VERSION, 15)
        session        = struct.unpack_from("<i", data, _SE_SESSION)[0]
        session_name   = _read_cstring(data, _SE_SESSION_NAME, 33)
        event_id       = data[_SE_EVENT_ID]
        session_id     = data[_SE_SESSION_ID]
        starting_grip  = struct.unpack_from("<i", data, _SE_STARTING_GRIP)[0]
        amb_temp_c     = struct.unpack_from("<f", data, _SE_AMB_TEMP_C)[0]
        ground_temp_c  = struct.unpack_from("<f", data, _SE_GROUND_TEMP_C)[0]
        is_static_wx   = bool(data[_SE_IS_STATIC_WEATHER])
        is_timed_race  = bool(data[_SE_IS_TIMED_RACE])
        is_online      = bool(data[_SE_IS_ONLINE])
        num_sessions   = struct.unpack_from("<i", data, _SE_NUMBER_OF_SESSIONS)[0]
        nation         = _read_cstring(data, _SE_NATION, 33)
        longitude      = struct.unpack_from("<f", data, _SE_LONGITUDE)[0]
        latitude       = struct.unpack_from("<f", data, _SE_LATITUDE)[0]
        track          = _read_cstring(data, _SE_TRACK, 33)
        track_config   = _read_cstring(data, _SE_TRACK_CONFIG, 33)
        track_length_m = struct.unpack_from("<f", data, _SE_TRACK_LENGTH_M)[0]
    except (struct.error, IndexError):
        return None

    # Sanity gate: the static region is unwritten until the game session
    # actually loads.  If both version strings AND the track name are empty,
    # treat the buffer as unpopulated and bail so the fallback decoder
    # surfaces the raw bytes.
    if not (sm_version or ac_evo_version) and not track:
        return None

    # Track length must be a finite, plausible value (1 metre - 30 km
    # covers every real-world circuit).  If it lands outside this range the
    # offset is wrong somewhere; refuse the decode rather than emit junk.
    if not (math.isfinite(track_length_m) and 1.0 <= track_length_m <= 30000.0):
        # Allow zero (region not yet populated) without failing the whole
        # decode — empties are normal during early-load frames.
        if track_length_m != 0.0:
            return None

    return {
        "_decoder": "ac_evo_static",
        "buffer_size": len(data),
        # ── Versions
        "sm_version": sm_version,
        "ac_evo_version": ac_evo_version,
        "ac_version": ac_evo_version,                 # legacy-compat alias
        # ── Session identity
        "session": session,
        "session_name_enum": ACEVO_SESSION_TYPE.get(session, str(session)),
        "session_name": session_name,
        "event_id": event_id,
        "session_id": session_id,
        "number_of_sessions": num_sessions,
        # ── Conditions at session start
        "starting_grip": starting_grip,
        "starting_grip_name": ACEVO_STARTING_GRIP.get(starting_grip, str(starting_grip)),
        "starting_ambient_temperature_c": amb_temp_c,
        "starting_ground_temperature_c": ground_temp_c,
        "is_static_weather": is_static_wx,
        "is_timed_race": is_timed_race,
        "is_online": is_online,
        # ── Geography
        "nation": nation,
        "longitude": longitude,
        "latitude": latitude,
        # ── Track
        "track": track,
        "track_configuration": track_config,
        "track_length_m": track_length_m,
        "track_length_km": track_length_m / 1000.0 if track_length_m > 0 else 0.0,
        "track_spline_length": track_length_m,        # legacy-compat alias (ACC name)
    }


def decode_static_ac(data: bytes) -> Optional[Dict[str, Any]]:
    try:
        if len(data) < STATIC_HEADER_SIZE:
            return None

        r = R(data[:min(len(data), STATIC_STRUCT_SIZE)])
        result = {
            "_decoder": "acc_static_structure",
            "buffer_size": len(data),
            "parsed_size": min(len(data), STATIC_STRUCT_SIZE),
            "extra_bytes": max(0, len(data) - STATIC_STRUCT_SIZE),
            "sm_version": r.s(15),
            "ac_version": r.s(15),
            "number_of_sessions": r.i(),
            "num_cars": r.i(),
            "car_model": r.s(33),
            "track": r.s(33),
            "player_name": r.s(33),
            "player_surname": r.s(33),
            "player_nick": r.s(33, pad=2),
            "sector_count": r.i(),
        }

        if len(data) < STATIC_STRUCT_SIZE:
            result["layout_confidence"] = "header_only"
            result["populated_fields"] = sorted(
                key for key, value in result.items()
                if key not in {"_decoder", "buffer_size", "parsed_size", "extra_bytes", "layout_confidence"}
                and _has_meaningful_value(value)
            )
            return result

        result.update({
            "max_torque": r.f(),
            "max_power": r.f(),
            "max_rpm": r.i(),
            "max_fuel": r.f(),
            "suspension_max_travel": r.fa(4),
            "tyre_radius": r.fa(4),
            "max_turbo_boost": r.f(),
            "deprecated_1": r.f(),
            "deprecated_2": r.f(),
            "penalties_enabled": bool(r.i()),
            "aid_fuel_rate": r.f(),
            "aid_tire_rate": r.f(),
            "aid_mechanical_damage": r.f(),
            "allow_tyre_blankets": r.f(),
            "aid_stability": r.f(),
            "aid_auto_clutch": bool(r.i()),
            "aid_auto_blip": bool(r.i()),
            "has_drs": bool(r.i()),
            "has_ers": bool(r.i()),
            "has_kers": bool(r.i()),
            "kers_max_j": r.f(),
            "engine_brake_settings_count": r.i(),
            "ers_power_controller_count": r.i(),
            "track_spline_length": r.f(),
            "track_configuration": r.s(33, pad=2),
            "ers_max_j": r.f(),
            "is_timed_race": bool(r.i()),
            "has_extra_lap": bool(r.i()),
            "car_skin": r.s(33, pad=2),
            "reversed_grid_positions": r.i(),
            "pit_window_start": r.i(),
            "pit_window_end": r.i(),
            "is_online": bool(r.i()),
            "dry_tyres_name": r.s(33),
            "wet_tyres_name": r.s(33),
        })

        nonzero_numeric_fields = []
        if result["max_torque"]:
            nonzero_numeric_fields.append(_word_candidate(data, 404, "max_torque"))
        if result["max_power"]:
            nonzero_numeric_fields.append(_word_candidate(data, 408, "max_power"))
        if result["max_rpm"]:
            nonzero_numeric_fields.append(_word_candidate(data, 412, "max_rpm"))
        if result["max_fuel"]:
            nonzero_numeric_fields.append(_word_candidate(data, 416, "max_fuel"))
        if result["max_turbo_boost"]:
            nonzero_numeric_fields.append(_word_candidate(data, 452, "max_turbo_boost"))
        if result["track_spline_length"]:
            nonzero_numeric_fields.append(_word_candidate(data, 520, "track_spline_length"))
        if result["ers_max_j"]:
            nonzero_numeric_fields.append(_word_candidate(data, 592, "ers_max_j"))

        result["nonzero_numeric_fields"] = nonzero_numeric_fields
        result["observed_utf16_strings"] = _scan_utf16_strings(data)
        result["tail_utf16_strings"] = _scan_utf16_strings(data, start_offset=STATIC_STRUCT_SIZE)
        result["layout_confidence"] = "partial"
        result["populated_fields"] = sorted(
            key for key, value in result.items()
            if key not in {
                "_decoder",
                "buffer_size",
                "parsed_size",
                "extra_bytes",
                "layout_confidence",
                "populated_fields",
                "observed_utf16_strings",
                "tail_utf16_strings",
                "nonzero_numeric_fields",
            }
            and _has_meaningful_value(value)
        )
        return result
    except Exception:
        return None


def decode_physics_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for unknown structures."""
    result = {
        "_decoder": "fallback",
        "size": len(data),
        "decode_source": "fallback",
        "quality_score": 0.0,
        "invalid_reasons": ["fallback_decoder"],
        "is_plausible": False,
    }

    floats = []
    ints = []

    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                f = struct.unpack_from('<f', data, i)[0]
                if not (f != f or abs(f) > 1e6):
                    floats.append(round(f, 6))
            except Exception:
                break

    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                val = struct.unpack_from('<i', data, i)[0]
                if abs(val) < 100000:
                    ints.append(val)
            except Exception:
                break

    result["floats"] = floats[:20]
    result["ints"] = ints[:20]
    result["raw_hex_start"] = data[:100].hex()

    return result


def decode_graphics_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for graphics data."""
    result = {
        "_decoder": "fallback",
        "size": len(data),
        "decode_source": "fallback",
        "quality_score": 0.0,
        "invalid_reasons": ["fallback_decoder"],
        "has_authoritative_progress": False,
    }

    try:
        ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data[:200])
        result["ascii_start"] = ascii_part
    except Exception:
        pass

    floats = []
    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                f = struct.unpack_from('<f', data, i)[0]
                if not (f != f or abs(f) > 1e6):
                    floats.append(round(f, 6))
            except Exception:
                break

    result["floats"] = floats[:20]
    result["raw_hex_start"] = data[:100].hex()

    return result


def decode_static_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for static data."""
    result = {"_decoder": "fallback", "size": len(data)}

    result["bytes"] = list(data[:100])
    result["ascii"] = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data[:100])

    return result


def decode_physics(data: bytes) -> Dict[str, Any]:
    """Decode physics with AC/ACC structure fallback."""
    physics = decode_physics_ac(data)
    if physics:
        from dataclasses import asdict
        return _sanitize_physics_payload({"_decoder": "ac_structure", **asdict(physics)})
    return decode_physics_fallback(data)


def decode_graphics(data: bytes) -> Dict[str, Any]:
    """Decode graphics with fallback.

    Tries the AC Evo ``SPageFileGraphicEvo`` decoder first (current target
    game), falls back to the legacy ACC layout decoder, then to the
    pattern-detection fallback.
    """
    evo = decode_graphics_evo(data)
    if evo:
        return _sanitize_graphics_payload(evo)
    legacy = decode_graphics_ac(data)
    if legacy:
        return _sanitize_graphics_payload(legacy)
    return decode_graphics_fallback(data)


def decode_static(data: bytes) -> Dict[str, Any]:
    """Decode static with fallback.

    Tries the AC Evo ``SPageFileStaticEvo`` decoder first (current target
    game), falls back to the legacy ACC layout decoder, then to the
    pattern-detection fallback.
    """
    evo = decode_static_evo(data)
    if evo:
        return evo
    legacy = decode_static_ac(data)
    if legacy:
        return legacy
    return decode_static_fallback(data)


def physics_to_dict(physics_data: Any) -> Dict[str, Any]:
    """Convert physics data (dataclass or dict) to a flat dictionary."""
    if isinstance(physics_data, dict):
        return physics_data

    if hasattr(physics_data, "__dataclass_fields__"):
        result = {}
        for field, value in vars(physics_data).items():
            if isinstance(value, Coords):
                result[field] = {"x": value.x, "y": value.y, "z": value.z}
            elif isinstance(value, list):
                result[field] = value
            elif isinstance(value, (int, float, bool, str)):
                result[field] = value
            elif value is None:
                result[field] = None
            else:
                result[field] = str(value)
        return result

    return {"error": "Unknown physics data type"}


def graphics_to_dict(graphics_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert graphics data to a flat dictionary."""
    if isinstance(graphics_data, dict):
        return graphics_data
    return {"error": "Unknown graphics data type"}


def static_to_dict(static_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert static data to a flat dictionary."""
    if isinstance(static_data, dict):
        return static_data
    return {"error": "Unknown static data type"}
