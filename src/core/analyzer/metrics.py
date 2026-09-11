"""Analysis metric functions — extracted from telemetry_analyzer.py."""

import math
from typing import Any, Dict, List, Optional

from src.core.analyzer._util import (
    _avg,
    _trend_direction,
)


def analyze_corner_phases(
    track: List[Dict],
    corner: Dict,
    lap_start_frame: int,
    hz: float,
    approach_seconds: float = 3.0,
    exit_seconds: float = 2.0,
) -> Optional[Dict]:
    """Analyze brake, turn-in, and throttle timing around a corner."""
    BRAKE_THRESH = 0.10
    STEER_THRESH = 0.03
    GAS_THRESH = 0.15

    corner_start = corner["start_frame"]
    apex_frame = corner["apex_frame"]
    corner_end = corner["end_frame"]

    approach_frames = int(approach_seconds * hz)
    exit_frames = int(exit_seconds * hz)

    approach_start = max(lap_start_frame, corner_start - approach_frames)
    approach = [pt for pt in track if approach_start <= pt["frame"] < corner_start]
    corner_zone = [pt for pt in track if corner_start <= pt["frame"] <= corner_end]
    exit_zone = [pt for pt in track if apex_frame <= pt["frame"] <= corner_end + exit_frames]

    if len(approach) < 3 or len(corner_zone) < 3:
        return None

    # Brake onset
    brake_onset_dt = None
    for pt in reversed(approach):
        if (pt.get("brake", 0) or 0) >= BRAKE_THRESH:
            brake_onset_dt = (corner_start - pt["frame"]) / hz
        else:
            if brake_onset_dt is not None:
                break
    if brake_onset_dt is None:
        if corner_zone and (corner_zone[0].get("brake", 0) or 0) >= BRAKE_THRESH:
            brake_onset_dt = 0.0

    # Turn-in
    turn_in_dt = None
    for pt in reversed(approach):
        if abs(pt.get("steer", 0) or 0) >= STEER_THRESH:
            turn_in_dt = (corner_start - pt["frame"]) / hz
        else:
            if turn_in_dt is not None:
                break
    if turn_in_dt is None:
        if corner_zone and abs(corner_zone[0].get("steer", 0) or 0) >= STEER_THRESH:
            turn_in_dt = 0.0

    # Gas-on
    gas_on_dt = None
    for pt in exit_zone:
        gas_val = pt.get("gas_percent", pt.get("gas", 0)) or 0
        if gas_val >= GAS_THRESH:
            gas_on_dt = (pt["frame"] - apex_frame) / hz
            break

    # Trail braking
    entry_to_apex = [pt for pt in corner_zone if pt["frame"] <= apex_frame]
    trail_brake_frames = sum(1 for pt in entry_to_apex if (pt.get("brake", 0) or 0) > 0.05)
    trail_brake_pct = trail_brake_frames / max(len(entry_to_apex), 1)

    # Coast frames
    coast_frames_half_window = int(0.5 * hz)
    apex_vicinity = [pt for pt in corner_zone if abs(pt["frame"] - apex_frame) <= coast_frames_half_window]
    coast_frames = sum(
        1
        for pt in apex_vicinity
        if ((pt.get("gas_percent", pt.get("gas", 0)) or 0) < 0.10 and (pt.get("brake", 0) or 0) < 0.10)
    )

    # Peak braking G
    peak_brake_g = 0.0
    for pt in approach + entry_to_apex:
        long_g = abs(pt.get("acc_g_z", 0) or 0)
        if long_g > peak_brake_g:
            peak_brake_g = long_g

    return {
        "brake_onset_dt": brake_onset_dt,
        "turn_in_dt": turn_in_dt,
        "gas_on_dt": gas_on_dt,
        "trail_brake_pct": trail_brake_pct,
        "coast_frames": coast_frames,
        "peak_brake_g": peak_brake_g,
        "entry_speed": corner.get("entry_speed", 0),
        "apex_speed": corner.get("apex_speed", 0),
        "exit_speed": corner.get("exit_speed", 0),
    }


def analyze_grip_utilization(
    track: List[Dict],
    corner: Dict,
    hz: float,
) -> Optional[Dict]:
    """Analyze grip usage through friction circle metrics."""
    corner_pts = [pt for pt in track if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]]
    if len(corner_pts) < 3:
        return None

    total_gs = []
    lat_gs = []
    long_gs = []
    combined_brake_count = 0
    brake_count = 0

    for pt in corner_pts:
        lat_g = abs(pt.get("acc_g_x", 0) or 0)
        long_g = abs(pt.get("acc_g_z", 0) or 0)
        total_g = math.sqrt(lat_g**2 + long_g**2)
        total_gs.append(total_g)
        lat_gs.append(lat_g)
        long_gs.append(long_g)

        if (pt.get("brake", 0) or 0) > 0.05:
            brake_count += 1
            if lat_g > 0.3:
                combined_brake_count += 1

    peak_total_g = max(total_gs) if total_gs else 0
    avg_total_g = sum(total_gs) / len(total_gs) if total_gs else 0
    peak_lat_g = max(lat_gs) if lat_gs else 0
    peak_long_g = max(long_gs) if long_gs else 0

    grip_fill_pct = (avg_total_g / peak_total_g * 100) if peak_total_g > 0.1 else 0
    combined_braking_pct = (combined_brake_count / brake_count * 100) if brake_count > 0 else 0

    return {
        "peak_total_g": peak_total_g,
        "avg_total_g": avg_total_g,
        "peak_lat_g": peak_lat_g,
        "peak_long_g": peak_long_g,
        "grip_fill_pct": grip_fill_pct,
        "combined_braking_pct": combined_braking_pct,
    }


def analyze_lap_tyre_state(lap_track: List[Dict]) -> Optional[Dict]:
    """Per-lap tyre summary for stint-level grip degradation analysis."""
    if not lap_track:
        return None

    corner_frames = [pt for pt in lap_track if abs(pt.get("acc_g_x", 0.0) or 0.0) > 0.5]

    temps_fl = [pt.get("tyre_temp_fl", 0.0) for pt in lap_track if pt.get("tyre_temp_fl") is not None]
    temps_fr = [pt.get("tyre_temp_fr", 0.0) for pt in lap_track if pt.get("tyre_temp_fr") is not None]
    temps_rl = [pt.get("tyre_temp_rl", 0.0) for pt in lap_track if pt.get("tyre_temp_rl") is not None]
    temps_rr = [pt.get("tyre_temp_rr", 0.0) for pt in lap_track if pt.get("tyre_temp_rr") is not None]
    avg_core_temp = _avg([_avg(temps_fl), _avg(temps_fr), _avg(temps_rl), _avg(temps_rr)])
    peak_core_temp = max(
        (max(temps_fl) if temps_fl else 0.0),
        (max(temps_fr) if temps_fr else 0.0),
        (max(temps_rl) if temps_rl else 0.0),
        (max(temps_rr) if temps_rr else 0.0),
    )

    wear_keys = ("tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl", "tyre_wear_rr")
    start_wear = _avg([float(lap_track[0].get(k, 0.0) or 0.0) for k in wear_keys])
    end_wear = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in wear_keys])
    wear_delta = max(0.0, end_wear - start_wear)

    dirty_keys = ("tyre_dirty_fl", "tyre_dirty_fr", "tyre_dirty_rl", "tyre_dirty_rr")
    end_dirty = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in dirty_keys])

    if corner_frames:
        peak_lat_g = max(abs(pt.get("acc_g_x", 0.0) or 0.0) for pt in corner_frames)
        avg_lat_g = _avg([abs(pt.get("acc_g_x", 0.0) or 0.0) for pt in corner_frames])
        slip_angle_keys = ("slip_angle_fl", "slip_angle_fr", "slip_angle_rl", "slip_angle_rr")
        peak_slip_angle = max(
            (max(abs(pt.get(k, 0.0) or 0.0) for k in slip_angle_keys) for pt in corner_frames),
            default=0.0,
        )
    else:
        peak_lat_g = 0.0
        avg_lat_g = 0.0
        peak_slip_angle = 0.0

    return {
        "avg_core_temp_c": round(avg_core_temp, 1),
        "peak_core_temp_c": round(peak_core_temp, 1),
        "wear_delta_pct": round(wear_delta * 100.0, 3),
        "end_wear_pct": round(end_wear * 100.0, 2),
        "end_dirty_pct": round(end_dirty * 100.0, 2),
        "peak_lat_g": round(peak_lat_g, 2),
        "avg_lat_g": round(avg_lat_g, 2),
        "peak_slip_angle_deg": round(math.degrees(peak_slip_angle), 1),
        "corner_frames": len(corner_frames),
    }


def analyze_tyre_grip_degradation(laps: List[Dict]) -> Dict:
    """Compute per-lap tyre states and detect stint-level grip-loss trends."""
    per_lap: List[tuple] = []
    for lap in laps:
        lap_track = lap.get("track") or []
        state = analyze_lap_tyre_state(lap_track)
        if state is not None:
            per_lap.append((lap["lap_num"], state))

    trends: Dict[str, str] = {}
    flags: List[str] = []
    if len(per_lap) >= 3:
        avg_temps = [s["avg_core_temp_c"] for _, s in per_lap]
        peak_lat_gs = [s["peak_lat_g"] for _, s in per_lap]
        peak_slips = [s["peak_slip_angle_deg"] for _, s in per_lap]
        end_wear = [s["end_wear_pct"] for _, s in per_lap]

        trends["core_temp"] = _trend_direction(avg_temps, threshold=1.5)
        trends["peak_lat_g"] = _trend_direction(peak_lat_gs, threshold=0.03)
        trends["peak_slip_angle"] = _trend_direction(peak_slips, threshold=0.3)
        trends["wear"] = _trend_direction(end_wear, threshold=0.2)

        if trends["peak_lat_g"] == "FALLING":
            flags.append(
                "Peak cornering lat-G is dropping lap-over-lap — driver is getting "
                "less grip out of the same inputs. Tyres are likely past their peak."
            )
        if trends["core_temp"] == "RISING":
            flags.append(
                "Tyre core temperatures are climbing across the stint — overheating "
                "tyres lose grip; consider cooler inputs (smoother throttle/steering)."
            )
        if trends["peak_slip_angle"] == "RISING":
            flags.append(
                "Peak slip angles are growing each lap — the car is sliding more, another sign of grip falloff."
            )
        if trends["wear"] == "RISING" and end_wear and end_wear[-1] - end_wear[0] > 1.0:
            flags.append(
                f"Mechanical tyre wear is accumulating ({end_wear[0]:.1f}% -> "
                f"{end_wear[-1]:.1f}%); for longer races, manage rears in particular."
            )

    return {
        "per_lap": per_lap,
        "trends": trends,
        "flags": flags,
    }


def analyze_electronics_per_lap(laps: List[Dict]) -> List[Dict]:
    """Per-lap snapshot of electronic aid settings."""
    result: List[Dict] = []
    for lap in laps:
        track = lap.get("track") or []
        if not track:
            continue
        first = track[0]
        last = track[-1]

        def _val(pt: Dict, key: str):
            v = pt.get(key)
            return int(v) if v is not None else None

        def _changed(key: str) -> bool:
            f = first.get(key)  # noqa: B023
            ll = last.get(key)  # noqa: B023
            return f is not None and ll is not None and f != ll

        result.append(
            {
                "lap_num": lap["lap_num"],
                "tc_level": _val(first, "tc_level"),
                "abs_level": _val(first, "abs_level"),
                "engine_map": _val(first, "engine_map_level"),
                "diff_power": _val(first, "diff_power_level"),
                "diff_coast": _val(first, "diff_coast_level"),
                "front_bump_damper": _val(first, "front_bump_damper"),
                "front_rebound_damper": _val(first, "front_rebound_damper"),
                "rear_bump_damper": _val(first, "rear_bump_damper"),
                "rear_rebound_damper": _val(first, "rear_rebound_damper"),
                "perf_mode": _val(first, "electronics_perf_mode"),
                "tc_changed": _changed("tc_level"),
                "abs_changed": _changed("abs_level"),
                "engine_map_changed": _changed("engine_map_level"),
                "tc_level_min": _val(first, "tc_level_min"),
                "abs_level_min": _val(first, "abs_level_min"),
                "brake_bias_min": first.get("brake_bias_min"),
                "engine_map_min": _val(first, "engine_map_min"),
                "diff_power_min": _val(first, "diff_power_min"),
                "diff_coast_min": _val(first, "diff_coast_min"),
                "front_bump_damper_min": _val(first, "front_bump_damper_min"),
                "front_rebound_damper_min": _val(first, "front_rebound_damper_min"),
                "rear_bump_damper_min": _val(first, "rear_bump_damper_min"),
                "rear_rebound_damper_min": _val(first, "rear_rebound_damper_min"),
                "perf_mode_min": _val(first, "perf_mode_min"),
                "tc_level_max": _val(first, "tc_level_max"),
                "abs_level_max": _val(first, "abs_level_max"),
                "brake_bias_max": first.get("brake_bias_max"),
                "engine_map_max": _val(first, "engine_map_max"),
                "diff_power_max": _val(first, "diff_power_max"),
                "diff_coast_max": _val(first, "diff_coast_max"),
                "front_bump_damper_max": _val(first, "front_bump_damper_max"),
                "front_rebound_damper_max": _val(first, "front_rebound_damper_max"),
                "rear_bump_damper_max": _val(first, "rear_bump_damper_max"),
                "rear_rebound_damper_max": _val(first, "rear_rebound_damper_max"),
                "perf_mode_max": _val(first, "perf_mode_max"),
                "tc_level_modifiable": first.get("tc_level_modifiable"),
                "abs_level_modifiable": first.get("abs_level_modifiable"),
                "brake_bias_modifiable": first.get("brake_bias_modifiable"),
                "engine_map_modifiable": first.get("engine_map_modifiable"),
                "diff_power_modifiable": first.get("diff_power_modifiable"),
                "diff_coast_modifiable": first.get("diff_coast_modifiable"),
                "front_bump_damper_modifiable": first.get("front_bump_damper_modifiable"),
                "front_rebound_damper_modifiable": first.get("front_rebound_damper_modifiable"),
                "rear_bump_damper_modifiable": first.get("rear_bump_damper_modifiable"),
                "rear_rebound_damper_modifiable": first.get("rear_rebound_damper_modifiable"),
                "pitlimiter_modifiable": first.get("pitlimiter_modifiable"),
                "perf_mode_modifiable": first.get("perf_mode_modifiable"),
            }
        )
    return result


def analyze_brake_thermals(laps: List[Dict]) -> Dict[str, Any]:
    """Analyze brake temperatures across laps for imbalance and fade."""

    def _avg_temp(points: List[Dict], keys: List[str]) -> Optional[float]:
        values = [
            pt.get(key, 0)
            for pt in points
            for key in keys
            if isinstance(pt.get(key, 0), (int, float)) and pt.get(key, 0) > 0
        ]
        return sum(values) / len(values) if values else None

    per_lap: List[Dict[str, Any]] = []
    for lap in laps:
        track_pts = lap.get("track", [])
        braking_pts = [pt for pt in track_pts if (pt.get("brake") or 0) > 0.4]
        front_peaks = [
            pt.get(key, 0)
            for pt in track_pts
            for key in ("brake_temp_fl", "brake_temp_fr")
            if isinstance(pt.get(key, 0), (int, float)) and pt.get(key, 0) > 0
        ]
        per_lap.append(
            {
                "lap_num": lap["lap_num"],
                "front_avg": _avg_temp(braking_pts, ["brake_temp_fl", "brake_temp_fr"]),
                "rear_avg": _avg_temp(braking_pts, ["brake_temp_rl", "brake_temp_rr"]),
                "peak_front": max(front_peaks) if front_peaks else None,
            }
        )

    imbalance_note: Optional[str] = None
    front_avgs = [e["front_avg"] for e in per_lap if e["front_avg"] is not None]
    rear_avgs = [e["rear_avg"] for e in per_lap if e["rear_avg"] is not None]
    if front_avgs and rear_avgs:
        front_mean = sum(front_avgs) / len(front_avgs)
        rear_mean = sum(rear_avgs) / len(rear_avgs)
        if rear_mean > 0 and front_mean > 1.5 * rear_mean:
            imbalance_note = (
                f"Front brakes run {front_mean / rear_mean:.1f}x hotter than rears under heavy braking "
                f"({front_mean:.0f}C vs {rear_mean:.0f}C) - brake bias may be too far forward, "
                "or the rears are underworked."
            )
        elif front_mean > 0 and rear_mean > front_mean:
            imbalance_note = (
                f"Rear brakes run hotter than fronts under heavy braking "
                f"({rear_mean:.0f}C vs {front_mean:.0f}C) - rear bias or rear-duct issue."
            )

    fade_note: Optional[str] = None
    peaks = [e["peak_front"] for e in per_lap if e["peak_front"] is not None]
    if len(peaks) >= 3:
        rising = all(later >= earlier for earlier, later in zip(peaks, peaks[1:], strict=False))
        total_rise = peaks[-1] - peaks[0]
        if rising and total_rise > 60:
            fade_note = (
                f"Peak front brake temps climbing every lap ({peaks[0]:.0f}C -> {peaks[-1]:.0f}C, "
                f"+{total_rise:.0f}C) - heat is not recovering between braking zones; "
                "consider more brake duct or earlier/shorter braking before fade sets in."
            )

    return {"per_lap": per_lap, "imbalance_note": imbalance_note, "fade_note": fade_note}


def analyze_steering_smoothness(
    lap_track: List[Dict],
    corner: Dict,
    hz: float,
) -> Optional[Dict]:
    """Analyze steering smoothness through a corner.

    Counts steering direction reversals and computes peak/average steer rate.
    Uses the ``steer`` field (radians) — NOT ``steering_percent``.

    Returns ``{reversals, peak_steer_rate, avg_steer_rate, smoothness_score}``
    or ``None`` if the corner zone is too short.
    """
    corner_pts = [pt for pt in lap_track if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]]
    if len(corner_pts) < 4:
        return None

    steer_values: List[float] = []
    for pt in corner_pts:
        s = pt.get("steer")
        if isinstance(s, (int, float)):
            steer_values.append(float(s))

    if len(steer_values) < 4:
        return None

    # Count sign changes (direction reversals)
    reversals = 0
    prev_sign = 0
    for s in steer_values:
        sign = 1 if s > 0.02 else (-1 if s < -0.02 else 0)
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            reversals += 1
        if sign != 0:
            prev_sign = sign

    # Compute steer rate (delta between consecutive frames, rad/s)
    steer_deltas: List[float] = []
    for i in range(1, len(steer_values)):
        dt = (corner_pts[i]["frame"] - corner_pts[i - 1]["frame"]) / hz
        if dt > 0:
            steer_deltas.append(abs(steer_values[i] - steer_values[i - 1]) / dt)

    peak_steer_rate = max(steer_deltas) if steer_deltas else 0.0
    avg_steer_rate = sum(steer_deltas) / len(steer_deltas) if steer_deltas else 0.0

    # Smoothness score: lower reversals + lower peak rate = smoother (0-1, higher is smoother)
    _rev_penalty = min(1.0, reversals / 10.0)
    _rate_factor = 1.0 if peak_steer_rate < 0.5 else (0.5 / peak_steer_rate if peak_steer_rate > 0 else 0.0)
    smoothness_score = max(0.0, min(1.0, (1.0 - _rev_penalty) * 0.5 + _rate_factor * 0.5))

    return {
        "reversals": reversals,
        "peak_steer_rate": round(peak_steer_rate, 3),
        "avg_steer_rate": round(avg_steer_rate, 3),
        "smoothness_score": round(smoothness_score, 2),
    }


def analyze_throttle_exit(
    lap_track: List[Dict],
    corner: Dict,
    hz: float,
) -> Optional[Dict]:
    """Analyze throttle application smoothness from apex to corner exit.

    Splits the corner track into apex-to-exit half and computes:
    time to full throttle (>90%), throttle smoothness, modulation count.

    Returns ``{time_to_full_throttle, throttle_variance, modulation_count, exit_profile}``
    or ``None`` if the exit zone is too short.
    """
    corner_pts = [pt for pt in lap_track if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]]
    if len(corner_pts) < 4:
        return None

    apex_frame = corner["apex_frame"]
    apex_idx = None
    for i, pt in enumerate(corner_pts):
        if pt["frame"] >= apex_frame:
            apex_idx = i
            break
    if apex_idx is None:
        apex_idx = len(corner_pts) // 2

    exit_pts = corner_pts[apex_idx:]
    if len(exit_pts) < 2:
        return None

    # Time from apex to full throttle (>90% gas)
    GAS_FULL = 0.90
    time_to_full = None
    for pt in exit_pts:
        gas = pt.get("gas_percent", pt.get("gas", 0)) or 0
        if gas >= GAS_FULL:
            time_to_full = (pt["frame"] - apex_frame) / hz
            break

    # Throttle deltas (variance of step changes)
    gas_values = [pt.get("gas_percent", pt.get("gas", 0)) or 0 for pt in exit_pts]
    gas_deltas: List[float] = []
    for i in range(1, len(gas_values)):
        gas_deltas.append(gas_values[i] - gas_values[i - 1])

    avg_delta = sum(gas_deltas) / len(gas_deltas) if gas_deltas else 0.0
    throttle_variance = sum((d - avg_delta) ** 2 for d in gas_deltas) / len(gas_deltas) if gas_deltas else 0.0

    # Modulation count (direction reversals in throttle)
    modulation_count = 0
    prev_direction = 0
    for d in gas_deltas:
        direction = 1 if d > 0.02 else (-1 if d < -0.02 else 0)
        if direction != 0 and prev_direction != 0 and direction != prev_direction:
            modulation_count += 1
        if direction != 0:
            prev_direction = direction

    # Exit profile classification
    if time_to_full is None:
        exit_profile = "never reaches full throttle"
    elif time_to_full < 0.3:
        exit_profile = "immediate full throttle"
    elif time_to_full < 1.0:
        exit_profile = "progressive throttle"
    else:
        exit_profile = "slow throttle application"

    return {
        "time_to_full_throttle": round(time_to_full, 2) if time_to_full is not None else None,
        "throttle_variance": round(throttle_variance, 4),
        "modulation_count": modulation_count,
        "exit_profile": exit_profile,
    }


def analyze_suspension(
    laps: List[Dict],
    ref_corners: List[Dict],
    lap_corner_map: Optional[Dict[int, Dict[int, Dict]]] = None,
) -> Dict[str, Any]:
    """Analyze suspension travel and camber for setup hints.

    When ``lap_corner_map`` is provided, detected corner ``start_frame`` /
    ``end_frame`` are used for filtering (frame-range approach consistent
    with corner detection).  Otherwise falls back to progress-range filtering
    against the profile spec.
    """
    notes: Dict[str, List[str]] = {
        "bottoming_notes": [],
        "travel_delta_notes": [],
        "camber_notes": [],
    }

    _any_sus = any(
        pt.get(f"sus_{w}", 0) != 0 for lap in laps for pt in lap.get("track", []) for w in ("fl", "fr", "rl", "rr")
    )
    if not _any_sus:
        return notes

    _max_per_wheel = {w: 0.0 for w in ("fl", "fr", "rl", "rr")}
    for lap in laps:
        for pt in lap.get("track", []):
            for w in ("fl", "fr", "rl", "rr"):
                v = pt.get(f"sus_{w}", 0)
                if isinstance(v, (int, float)) and v > _max_per_wheel[w]:
                    _max_per_wheel[w] = v

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        for w in ("fl", "fr", "rl", "rr"):
            for lap in laps:
                ln = lap["lap_num"]
                if lap_corner_map:
                    _dc = lap_corner_map.get(ln, {}).get(cid)
                    if not _dc:
                        continue
                    _sf = _dc.get("start_frame")
                    _ef = _dc.get("end_frame")
                    if _sf is None or _ef is None:
                        continue
                    corner_pts = [pt for pt in lap.get("track", []) if _sf <= pt["frame"] <= _ef]
                else:
                    corner_pts = [
                        pt
                        for pt in lap.get("track", [])
                        if spec["start"]
                        <= (pt.get("lap_progress") if pt.get("lap_progress") is not None else -1)
                        < spec["end"]
                    ]
                if len(corner_pts) < 3:
                    continue
                streak = 0
                max_streak = 0
                threshold = _max_per_wheel[w] * 0.98
                for pt in corner_pts:
                    v = pt.get(f"sus_{w}", 0)
                    if isinstance(v, (int, float)) and v >= threshold:
                        streak += 1
                        max_streak = max(max_streak, streak)
                    else:
                        streak = 0
                if max_streak > 2:
                    notes["bottoming_notes"].append(
                        f"{w.upper()} bottoming at {name} ({max_streak} frames near max travel)"
                    )

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        apex_sus: Dict[int, Dict[str, float]] = {}
        for lap in laps:
            ln = lap["lap_num"]
            if lap_corner_map:
                _dc = lap_corner_map.get(ln, {}).get(cid)
                if not _dc:
                    continue
                _sf = _dc.get("start_frame")
                _ef = _dc.get("end_frame")
                if _sf is None or _ef is None:
                    continue
                for pt in lap.get("track", []):
                    if _sf <= pt["frame"] <= _ef:
                        for w in ("fl", "fr", "rl", "rr"):
                            v = pt.get(f"sus_{w}", 0)
                            if isinstance(v, (int, float)) and v > 0:
                                apex_sus.setdefault(ln, {})[w] = v
                        break
            else:
                for pt in lap.get("track", []):
                    progress = pt.get("lap_progress")
                    if progress is None:
                        progress = -1
                    if spec["start"] <= progress < spec["end"]:
                        for w in ("fl", "fr", "rl", "rr"):
                            v = pt.get(f"sus_{w}", 0)
                            if isinstance(v, (int, float)) and v > 0:
                                apex_sus.setdefault(ln, {})[w] = v
                        break
        if len(apex_sus) >= 2:
            for w in ("fl", "fr", "rl", "rr"):
                vals = [apex_sus[ln].get(w, 0) for ln in apex_sus if (apex_sus[ln].get(w, 0) or 0) > 0]
                if vals and max(vals) - min(vals) > 0.005:
                    notes["travel_delta_notes"].append(
                        f"{name} {w.upper()} apex travel varies "
                        f"{min(vals) * 1000:.0f}-{max(vals) * 1000:.0f}mm "
                        f"({(max(vals) - min(vals)) * 1000:.1f}mm spread) - line/curb usage tip"
                    )

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        max_magnitude_delta = 0.0
        for lap in laps:
            ln = lap["lap_num"]
            sample = None
            if lap_corner_map:
                detected_corner = lap_corner_map.get(ln, {}).get(cid)
                if not detected_corner:
                    continue
                start_frame = detected_corner.get("start_frame")
                end_frame = detected_corner.get("end_frame")
                if start_frame is None or end_frame is None:
                    continue
                sample = next(
                    (point for point in lap.get("track", []) if start_frame <= point["frame"] <= end_frame),
                    None,
                )
            else:
                sample = next(
                    (
                        point
                        for point in lap.get("track", [])
                        if spec["start"]
                        <= (point.get("lap_progress") if point.get("lap_progress") is not None else -1)
                        < spec["end"]
                    ),
                    None,
                )

            if sample is None:
                continue
            camber_left = sample.get("camber_fl", 0)
            camber_right = sample.get("camber_fr", 0)
            if isinstance(camber_left, (int, float)) and isinstance(camber_right, (int, float)):
                max_magnitude_delta = max(
                    max_magnitude_delta,
                    abs(abs(camber_left) - abs(camber_right)),
                )

        if max_magnitude_delta > 0.0087:
            mismatch_degrees = max_magnitude_delta * (180 / 3.14159)
            notes["camber_notes"].append(
                f"{name}: front camber magnitude mismatch "
                f"{mismatch_degrees:.1f}deg at apex - "
                "excessive body roll for camber setting"
            )

    return notes
