"""
Telemetry Analyzer Module

Analyzes captured telemetry data and generates HTML reports and AI coaching prompts.
Based on test_scripts/telemetry/2-analyze.py
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.core.telemetry_capture import CaptureMetadata, FrameData
from src.core.track_catalog import select_track_profile
from src.models import SharedSessionManager
from src.utils.structured_logger import log_debug, log_info, log_warning, log_error, log_exception, Component


@dataclass
class AnalysisResult:
    """Result of telemetry analysis."""
    html_path: Optional[str]
    ai_prompt_path: Optional[str]
    laps_detected: int
    best_lap_time: float
    track_name: Optional[str]


def _safe_4(arr: list, default: float = 0.0) -> List[float]:
    """Safely extract 4-element array."""
    if not isinstance(arr, (list, tuple)):
        return [default, default, default, default]
    out = [default, default, default, default]
    for i in range(min(4, len(arr))):
        out[i] = arr[i]
    return out


def _sanitize_slip(v: Any) -> float:
    """Sanitize wheel slip value."""
    try:
        v = float(v)
    except Exception:
        return 0.0
    if not math.isfinite(v) or v < 0:
        return 0.0
    return min(v, 5.0)


def get_physics(frame: FrameData) -> Dict[str, Any]:
    """Get physics data from frame."""
    return frame.physics


def get_graphics(frame: FrameData) -> Dict[str, Any]:
    """Get graphics data from frame.

    Prefers the decoded ``frame.graphics`` payload (AC Evo
    ``SPageFileGraphicEvo`` via ``decode_graphics_evo``) when it carries
    authoritative track progress; falls back to physics-derived
    dead-reckoning values otherwise so older captures (taken before the
    graphics decoder landed) still analyze.
    """
    graphics = frame.graphics or {}
    if graphics.get("has_authoritative_progress") and graphics.get("normalized_car_position") is not None:
        return graphics

    # Fallback: physics dead-reckoning (legacy behaviour for old captures
    # without a decoded graphics region).
    physics = frame.physics or {}
    return {
        "normalized_car_position": physics.get("normalized_car_position"),
        "normalized_position_source": physics.get("normalized_position_source"),
        "has_authoritative_progress": physics.get("has_authoritative_progress", False),
        "completed_laps": graphics.get("completed_laps", 0),
        "current_time_ms": graphics.get("current_time_ms", 0),
        "last_time_ms": graphics.get("last_time_ms", 0),
        "best_time_ms": graphics.get("best_time_ms", 0),
        "is_valid_lap": graphics.get("is_valid_lap"),
        "is_in_pit_lane": graphics.get("is_in_pit_lane", False),
    }


def _optional_float(value: Any) -> Optional[float]:
    """Convert a value to a finite float or return None."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _fraction(points: List[Dict], predicate) -> float:
    """Return the fraction of points matching a predicate."""
    if not points:
        return 0.0
    return sum(1 for point in points if predicate(point)) / len(points)


# Quality-gate thresholds for the analyzer. Documented here so the single
# source of truth lives next to the decision helper below.
_AUTHORITATIVE_PROGRESS_THRESHOLD = 0.60
_PLAUSIBLE_FRAME_THRESHOLD = 0.66
_HIGH_PLAUSIBLE_FALLBACK = 0.95


def _decide_analysis_mode(
    authoritative_progress_ratio: float,
    plausible_frame_ratio: float,
) -> Tuple[str, bool, bool]:
    """Decide whether to run full coaching or the diagnostic stub.

    Ideally we have authoritative track progress from the graphics SHM
    region (>= 60% coverage). Until the AC Evo ``SPageFileGraphicEvo``
    decoder exists, live sessions fall back to physics-derived
    dead-reckoning ``normalized_car_position``. That signal is still
    accurate enough for lap-over-lap coaching when physics frames are
    consistently plausible across the whole capture (>= 95% coverage with
    ``frame_quality`` >= 0.66).

    Returns ``(mode, has_authoritative, has_high_plausible)`` — the two
    booleans are exposed so callers can choose tailored status messages
    without re-running the comparison.

    Once ``decode_graphics_evo`` lands and ``authoritative_progress_ratio``
    becomes the norm, the plausible-physics fallback degrades to a no-op.
    """
    has_authoritative = authoritative_progress_ratio >= _AUTHORITATIVE_PROGRESS_THRESHOLD
    has_high_plausible = plausible_frame_ratio >= _HIGH_PLAUSIBLE_FALLBACK
    mode = "full" if (has_authoritative or has_high_plausible) else "diagnostic"
    return mode, has_authoritative, has_high_plausible


def _confidence_label(score: float) -> str:
    """Convert a numeric confidence score into a label."""
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _median(values: List[float]) -> Optional[float]:
    clean = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return float(clean[mid])
    return float((clean[mid - 1] + clean[mid]) / 2.0)


def _profile_corner_sanity_notes(laps: List[Dict]) -> List[str]:
    """Catch obviously shifted track-profile windows before coaching."""
    by_name: Dict[str, List[float]] = defaultdict(list)
    for lap in laps:
        for corner in lap.get("corners", []):
            name = (corner.get("name") or "").lower()
            apex = corner.get("apex_speed")
            if isinstance(apex, (int, float)) and math.isfinite(apex):
                by_name[name].append(float(apex))

    notes: List[str] = []
    for name, speeds in by_name.items():
        median_apex = _median(speeds)
        if median_apex is None:
            continue
        if "rettifilo" in name and median_apex > 190:
            notes.append(
                f"Track profile sanity check failed: {name} median apex is {median_apex:.0f} km/h, which is too fast for the first chicane."
            )
        if "curva grande" in name and median_apex < 120:
            notes.append(
                f"Track profile sanity check failed: {name} median apex is {median_apex:.0f} km/h, which is too slow for Curva Grande."
            )
    return notes


def _interpolate_value(left: Dict, right: Dict, field: str, ratio: float) -> Optional[float]:
    """Linearly interpolate a scalar field between two samples."""
    left_value = _optional_float(left.get(field))
    right_value = _optional_float(right.get(field))

    if left_value is None and right_value is None:
        return None
    if left_value is None:
        return right_value
    if right_value is None:
        return left_value
    return left_value + (right_value - left_value) * ratio


def _median3(values: List[Optional[float]]) -> List[Optional[float]]:
    """Apply a 3-point median filter to a numeric series."""
    smoothed: List[Optional[float]] = []
    for idx in range(len(values)):
        window = [
            value
            for value in values[max(0, idx - 1):min(len(values), idx + 2)]
            if value is not None
        ]
        if not window:
            smoothed.append(None)
            continue
        window.sort()
        smoothed.append(window[len(window) // 2])
    return smoothed


def _local_average(points: List[Dict], center_idx: int, field: str, radius: int = 1) -> float:
    """Average a scalar field in a small local neighborhood."""
    values = [
        _optional_float(points[idx].get(field))
        for idx in range(max(0, center_idx - radius), min(len(points), center_idx + radius + 1))
    ]
    finite_values = [value for value in values if value is not None]
    if not finite_values:
        return 0.0
    return sum(finite_values) / len(finite_values)


def _build_canonical_lap(lap_track: List[Dict], lap_start_frame: int, hz: float, bins: int = 200) -> Optional[Dict[str, Any]]:
    """Resample a lap onto a common progress grid."""
    samples: List[Dict[str, Any]] = []
    last_progress = None

    for pt in lap_track:
        progress = _optional_float(pt.get("norm_pos"))
        if progress is None or progress < 0.0 or progress > 1.0:
            continue
        if last_progress is not None and progress + 0.02 < last_progress:
            continue
        sample = dict(pt)
        sample["lap_progress"] = progress
        sample["lap_pos"] = progress
        sample["time_s"] = (pt["frame"] - lap_start_frame) / hz
        samples.append(sample)
        last_progress = progress

    if len(samples) < 8:
        return None

    progress_start = samples[0]["lap_progress"]
    progress_end = samples[-1]["lap_progress"]
    if progress_start > 0.10 or progress_end < 0.90:
        return None

    grid = [idx / max(bins - 1, 1) for idx in range(bins)]
    scalar_fields = [
        "frame",
        "time_s",
        "x",
        "z",
        "speed",
        "heading",
        "steer",
        "brake",
        "gas",
        "yaw_rate",
        "acc_g_x",
        "acc_g_y",
        "acc_g_z",
    ]
    canonical: List[Dict[str, Any]] = []
    cursor = 0

    for gp in grid:
        while cursor + 1 < len(samples) - 1 and samples[cursor + 1]["lap_progress"] < gp:
            cursor += 1

        left = samples[cursor]
        right = samples[min(cursor + 1, len(samples) - 1)]
        left_progress = left["lap_progress"]
        right_progress = right["lap_progress"]

        if gp < left_progress or gp > right_progress:
            continue

        ratio = 0.0 if right_progress <= left_progress else (gp - left_progress) / (right_progress - left_progress)
        nearest = left if ratio <= 0.5 else right
        point = dict(nearest)
        point["lap_progress"] = gp
        point["lap_pos"] = gp

        for field in scalar_fields:
            value = _interpolate_value(left, right, field, ratio)
            if field == "frame":
                point[field] = int(round(value)) if value is not None else nearest.get("frame")
            elif value is not None:
                point[field] = value

        canonical.append(point)

    if len(canonical) < 20:
        return None

    return {
        "samples": canonical,
        "progress_start": progress_start,
        "progress_end": progress_end,
        "source_samples": len(samples),
        "grid_bins": bins,
    }


def _detect_profiled_corners_canonical(
    canonical_track: List[Dict],
    profile: Dict[str, Any],
    hz: float,
    authoritative_progress: bool,
) -> List[Dict]:
    """Detect profiled corners on a canonical progress grid."""
    result = []
    for spec in profile.get("corners", []):
        window = [
            pt for pt in canonical_track
            if spec["start"] <= pt.get("lap_progress", -1.0) < spec["end"]
        ]
        if len(window) < 4:
            continue

        speed_series = [_optional_float(pt.get("speed")) for pt in window]
        smoothed_speed = _median3(speed_series)
        apex_candidates = [
            (idx, value)
            for idx, value in enumerate(smoothed_speed)
            if value is not None
        ]
        if not apex_candidates:
            continue

        apex_idx, apex_speed = min(apex_candidates, key=lambda item: item[1])
        entry_idx = 0
        for idx, pt in enumerate(window[:apex_idx + 1]):
            brake = _optional_float(pt.get("brake")) or 0.0
            steer = abs(_optional_float(pt.get("steer")) or 0.0)
            if brake >= 0.08 or steer >= 0.03:
                entry_idx = idx
                break

        exit_idx = len(window) - 1
        for idx in range(apex_idx + 1, len(window)):
            gas = _optional_float(window[idx].get("gas_percent", window[idx].get("gas"))) or 0.0
            if gas >= 0.20:
                exit_idx = idx
                break

        if exit_idx <= apex_idx:
            exit_idx = min(len(window) - 1, apex_idx + 1)

        entry = window[entry_idx]
        apex = window[apex_idx]
        exit_pt = window[exit_idx]
        valid_speed_ratio = sum(1 for value in speed_series if value is not None) / len(window)
        confidence = round(
            min(1.0, len(window) / 8.0) * 0.2
            + valid_speed_ratio * 0.4
            + (0.4 if authoritative_progress else 0.1),
            3,
        )

        result.append({
            "id": spec["id"],
            "name": spec["name"],
            "start_frame": entry["frame"],
            "end_frame": exit_pt["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": min(value for value in smoothed_speed[max(0, apex_idx - 1):min(len(window), apex_idx + 2)] if value is not None),
            "min_speed": min(value for value in speed_series if value is not None),
            "entry_speed": _local_average(window, entry_idx, "speed"),
            "exit_speed": _local_average(window, exit_idx, "speed"),
            "apex_x": _optional_float(apex.get("x")) or 0.0,
            "apex_z": _optional_float(apex.get("z")) or 0.0,
            "lap_pos": apex.get("lap_progress", spec["start"]),
            "segment_time_s": max(0.0, (_optional_float(exit_pt.get("time_s")) or 0.0) - (_optional_float(entry.get("time_s")) or 0.0)),
            "confidence": confidence,
            "confidence_label": _confidence_label(confidence),
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    return result


def _select_track_profile_for_analysis(track_name: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a track profile from the reported session track name."""
    if not track_name:
        return None, None

    track_key, track_profile = select_track_profile(track_name=track_name)
    if track_profile:
        return track_key, track_profile

    # Fallback to path-style substring matching for names like "circuit_de_spa_francorchamps gp".
    return select_track_profile(path=track_name)


def build_track(frames: List[FrameData], hz: float = 1.0, start_idx: int = 0) -> List[Dict]:
    """Build track map from frames."""
    track = []
    x = z = 0.0
    dt = 1.0 / hz

    for i in range(start_idx, len(frames)):
        f = frames[i]
        ph = get_physics(f)
        gr = get_graphics(f)
        if not ph or ph.get("is_plausible") is False:
            continue
        
        # Debug: Check graphics data on first frame
        if i == start_idx:
            has_graphics = bool(gr)
            has_auth_progress = gr.get("has_authoritative_progress", False) if gr else False
            norm_pos = gr.get("normalized_car_position") if gr else None
            log_debug(Component.ANALYZER, "Frame graphics check", frame=i, has_graphics=has_graphics, has_auth_progress=has_auth_progress, norm_pos=norm_pos)

        speed = _optional_float(ph.get("speed_kmh"))
        if speed is None:
            continue

        wp = ph.get("world_position") or ph.get("worldPosition")
        if wp and isinstance(wp, dict):
            wp_x = _optional_float(wp.get("x"))
            wp_z = _optional_float(wp.get("z"))
            if wp_x is not None:
                x = wp_x
            if wp_z is not None:
                z = wp_z
        else:
            velocity = ph.get("velocity", {})
            vx = _optional_float(velocity.get("x")) if isinstance(velocity, dict) else _optional_float(getattr(velocity, "x", None))
            vz = _optional_float(velocity.get("z")) if isinstance(velocity, dict) else _optional_float(getattr(velocity, "z", None))
            if vx is not None:
                x += vx * dt
            if vz is not None:
                z += vz * dt

        graphics_norm_pos = None
        if gr.get("has_authoritative_progress"):
            graphics_norm_pos = _optional_float(gr.get("normalized_car_position"))
            # Debug: Log graphics position on first frame
            if i == start_idx and graphics_norm_pos is not None:
                log_debug(Component.ANALYZER, "First frame graphics normalized_position", norm_pos=graphics_norm_pos)

        physics_norm_pos = _optional_float(
            ph.get("normalized_spline_position")
            or ph.get("spNormalizedCarPosition")
            or ph.get("normalizedCarPosition")
            or ph.get("normalized_car_position")
        )

        norm_pos = graphics_norm_pos if graphics_norm_pos is not None else physics_norm_pos
        progress_source = "graphics" if graphics_norm_pos is not None else "physics" if physics_norm_pos is not None else None
        physics_quality = _optional_float(ph.get("quality_score")) or 0.0
        graphics_quality = _optional_float(gr.get("quality_score"))
        frame_quality = physics_quality if progress_source != "graphics" or graphics_quality is None else min(physics_quality, graphics_quality)

        tyre_core_temp = _safe_4(ph.get("tyre_core_temp", []), default=0.0)
        wheels_pressure = _safe_4(ph.get("wheels_pressure", []), default=0.0)
        wheel_slip_raw = _safe_4(ph.get("wheel_slip", []), default=0.0)
        wheel_slip = [_sanitize_slip(v) for v in wheel_slip_raw]
        wheel_load = _safe_4(ph.get("wheel_load", []), default=0.0)
        suspension_travel = _safe_4(ph.get("suspension_travel", []), default=0.0)
        camber_rad = _safe_4(ph.get("camber_rad", []), default=0.0)
        brake_temp = _safe_4(ph.get("brake_temp", []), default=0.0)
        # Tyre wear (0.0 fresh -> 1.0 worn) and dirt level for grip-degradation analysis.
        tyre_wear = _safe_4(ph.get("tyre_wear", []), default=0.0)
        tyre_dirty_level = _safe_4(ph.get("tyre_dirty_level", []), default=0.0)
        
        # AC Evo precision fields
        fx = _safe_4(ph.get("fx", []), default=0.0)
        fy = _safe_4(ph.get("fy", []), default=0.0)
        slip_ratio = _safe_4(ph.get("slip_ratio", []), default=0.0)
        slip_angle = _safe_4(ph.get("slip_angle", []), default=0.0)
        brake_torque = _safe_4(ph.get("brake_torque", []), default=0.0)

        acc_g = ph.get("acc_g", {}) or {}
        local_ang_vel = ph.get("local_angular_velocity", {}) or {}

        if isinstance(acc_g, dict):
            acc_g_x = acc_g.get("x", 0)
            acc_g_y = acc_g.get("y", 0)
            acc_g_z = acc_g.get("z", 0)
        else:
            acc_g_x = acc_g_y = acc_g_z = 0

        if isinstance(local_ang_vel, dict):
            yaw_rate = local_ang_vel.get("y", 0)
        else:
            yaw_rate = 0

        track.append({
            "frame": i,
            "x": x,
            "z": z,
            "speed": speed,
            "heading": _optional_float(ph.get("heading")) or 0.0,
            "steer": _optional_float(ph.get("steer_angle")) or 0.0,
            "brake": _optional_float(ph.get("brake")) or 0.0,
            "gas": _optional_float(ph.get("gas")) or 0.0,
            "gear": ph.get("gear", 0) or 0,
            "rpms": ph.get("rpms", 0) or 0,
            "norm_pos": float(norm_pos) if norm_pos is not None else None,
            "progress_source": progress_source,
            "has_authoritative_progress": progress_source == "graphics",
            "physics_quality": physics_quality,
            "graphics_quality": graphics_quality,
            "frame_quality": frame_quality,
            "abs": _optional_float(ph.get("abs")) or 0.0,
            "absin_action": ph.get("absin_action", False),
            "tc": _optional_float(ph.get("tc")) or 0.0,
            "drs": _optional_float(ph.get("drs")) or 0.0,
            "drs_available": ph.get("drs_available", False),
            "drs_enabled": ph.get("drs_enabled", False),
            "acc_g_x": acc_g_x,
            "acc_g_y": acc_g_y,
            "acc_g_z": acc_g_z,
            "yaw_rate": yaw_rate,
            "air_temp": _optional_float(ph.get("air_temp")) or 0.0,
            "road_temp": _optional_float(ph.get("road_temp")) or 0.0,
            "completed_laps": gr.get("completed_laps"),
            "current_sector_index": gr.get("current_sector_index"),
            "is_valid_lap": gr.get("is_valid_lap"),
            "is_in_pit": gr.get("is_in_pit"),
            "is_in_pit_lane": gr.get("is_in_pit_lane"),
            "distance_traveled": gr.get("distance_traveled"),
            "lap_time_ms": gr.get("current_time_ms"),
            "last_lap_time_ms": gr.get("last_time_ms"),
            "best_lap_time_ms": gr.get("best_time_ms"),
            "tyre_temp_fl": tyre_core_temp[0] if len(tyre_core_temp) > 0 else 0,
            "tyre_temp_fr": tyre_core_temp[1] if len(tyre_core_temp) > 1 else 0,
            "tyre_temp_rl": tyre_core_temp[2] if len(tyre_core_temp) > 2 else 0,
            "tyre_temp_rr": tyre_core_temp[3] if len(tyre_core_temp) > 3 else 0,
            "pressure_fl": wheels_pressure[0] if len(wheels_pressure) > 0 else 0,
            "pressure_fr": wheels_pressure[1] if len(wheels_pressure) > 1 else 0,
            "pressure_rl": wheels_pressure[2] if len(wheels_pressure) > 2 else 0,
            "pressure_rr": wheels_pressure[3] if len(wheels_pressure) > 3 else 0,
            "slip_fl": wheel_slip[0] if len(wheel_slip) > 0 else 0,
            "slip_fr": wheel_slip[1] if len(wheel_slip) > 1 else 0,
            "slip_rl": wheel_slip[2] if len(wheel_slip) > 2 else 0,
            "slip_rr": wheel_slip[3] if len(wheel_slip) > 3 else 0,
            "load_fl": wheel_load[0] if len(wheel_load) > 0 else 0,
            "load_fr": wheel_load[1] if len(wheel_load) > 1 else 0,
            "load_rl": wheel_load[2] if len(wheel_load) > 2 else 0,
            "load_rr": wheel_load[3] if len(wheel_load) > 3 else 0,
            "sus_fl": suspension_travel[0] if len(suspension_travel) > 0 else 0,
            "sus_fr": suspension_travel[1] if len(suspension_travel) > 1 else 0,
            "sus_rl": suspension_travel[2] if len(suspension_travel) > 2 else 0,
            "sus_rr": suspension_travel[3] if len(suspension_travel) > 3 else 0,
            "camber_fl": camber_rad[0] if len(camber_rad) > 0 else 0,
            "camber_fr": camber_rad[1] if len(camber_rad) > 1 else 0,
            "camber_rl": camber_rad[2] if len(camber_rad) > 2 else 0,
            "camber_rr": camber_rad[3] if len(camber_rad) > 3 else 0,
            "brake_temp_fl": brake_temp[0] if len(brake_temp) > 0 else 0,
            "brake_temp_fr": brake_temp[1] if len(brake_temp) > 1 else 0,
            "brake_temp_rl": brake_temp[2] if len(brake_temp) > 2 else 0,
            "brake_temp_rr": brake_temp[3] if len(brake_temp) > 3 else 0,
            # Tyre wear (0.0 fresh -> 1.0 worn) — used for stint grip-degradation analysis.
            "tyre_wear_fl": tyre_wear[0] if len(tyre_wear) > 0 else 0.0,
            "tyre_wear_fr": tyre_wear[1] if len(tyre_wear) > 1 else 0.0,
            "tyre_wear_rl": tyre_wear[2] if len(tyre_wear) > 2 else 0.0,
            "tyre_wear_rr": tyre_wear[3] if len(tyre_wear) > 3 else 0.0,
            "tyre_dirty_fl": tyre_dirty_level[0] if len(tyre_dirty_level) > 0 else 0.0,
            "tyre_dirty_fr": tyre_dirty_level[1] if len(tyre_dirty_level) > 1 else 0.0,
            "tyre_dirty_rl": tyre_dirty_level[2] if len(tyre_dirty_level) > 2 else 0.0,
            "tyre_dirty_rr": tyre_dirty_level[3] if len(tyre_dirty_level) > 3 else 0.0,
            # AC Evo precision fields
            "fx_fl": fx[0] if len(fx) > 0 else 0,
            "fx_fr": fx[1] if len(fx) > 1 else 0,
            "fx_rl": fx[2] if len(fx) > 2 else 0,
            "fx_rr": fx[3] if len(fx) > 3 else 0,
            "fy_fl": fy[0] if len(fy) > 0 else 0,
            "fy_fr": fy[1] if len(fy) > 1 else 0,
            "fy_rl": fy[2] if len(fy) > 2 else 0,
            "fy_rr": fy[3] if len(fy) > 3 else 0,
            "slip_ratio_fl": slip_ratio[0] if len(slip_ratio) > 0 else 0,
            "slip_ratio_fr": slip_ratio[1] if len(slip_ratio) > 1 else 0,
            "slip_ratio_rl": slip_ratio[2] if len(slip_ratio) > 2 else 0,
            "slip_ratio_rr": slip_ratio[3] if len(slip_ratio) > 3 else 0,
            "slip_angle_fl": slip_angle[0] if len(slip_angle) > 0 else 0,
            "slip_angle_fr": slip_angle[1] if len(slip_angle) > 1 else 0,
            "slip_angle_rl": slip_angle[2] if len(slip_angle) > 2 else 0,
            "slip_angle_rr": slip_angle[3] if len(slip_angle) > 3 else 0,
            "brake_torque_fl": brake_torque[0] if len(brake_torque) > 0 else 0,
            "brake_torque_fr": brake_torque[1] if len(brake_torque) > 1 else 0,
            "brake_torque_rl": brake_torque[2] if len(brake_torque) > 2 else 0,
            "brake_torque_rr": brake_torque[3] if len(brake_torque) > 3 else 0,
            "brake_bias": _optional_float(ph.get("brake_bias")),
            "engine_brake": ph.get("engine_brake", 0),
            "water_temp": _optional_float(ph.get("water_temp")),
            # Aerodynamics data
            "pitch": _optional_float(ph.get("pitch")),
            "roll": _optional_float(ph.get("roll")),
            "cg_height": _optional_float(ph.get("cg_height")),
            "ride_height_front": _safe_4(ph.get("ride_height", []), default=0.0)[0] if len(_safe_4(ph.get("ride_height", []), default=0.0)) > 0 else 0.0,
            "ride_height_rear": _safe_4(ph.get("ride_height", []), default=0.0)[1] if len(_safe_4(ph.get("ride_height", []), default=0.0)) > 1 else 0.0,
            "air_density": _optional_float(ph.get("air_density")),
            "air_temp": _optional_float(ph.get("air_temp")),
            "road_temp": _optional_float(ph.get("road_temp")),
            # Graphics-sourced performance fields
            "gear_rpm_window": _optional_float(gr.get("gear_rpm_window")),
            "predicted_lap_time_ms": gr.get("predicted_lap_time_ms"),
            "delta_time_ms": gr.get("delta_time_ms"),
            "current_bhp": gr.get("current_bhp"),
            "current_torque": _optional_float(gr.get("current_torque")),
            "rpm_percent": _optional_float(gr.get("rpm_percent")),
            "gas_percent": _optional_float(gr.get("gas_percent")),
            "brake_percent": _optional_float(gr.get("brake_percent")),
            "clutch_percent": _optional_float(gr.get("clutch_percent")),
            "steering_percent": _optional_float(gr.get("steering_percent")),
            "turbo_boost": _optional_float(gr.get("turbo_boost")),
            "turbo_boost_perc": _optional_float(gr.get("turbo_boost_perc")),
            # Electronics / aids from Graphics SHM SMEvoElectronics (None if buffer too small)
            "tc_level": gr.get("electronics_tc_level"),
            "abs_level": gr.get("electronics_abs_level"),
            "engine_map_level": gr.get("electronics_engine_map"),
            "diff_power_level": gr.get("electronics_diff_power"),
            "diff_coast_level": gr.get("electronics_diff_coast"),
            "electronics_perf_mode": gr.get("electronics_perf_mode"),
            "electronics_pitlimiter_on": gr.get("electronics_pitlimiter_on"),
        })
    return track


def _detect_laps_by_norm_pos(track: List[Dict], hz: float = 1.0) -> Optional[List[int]]:
    """Detect laps using normalized spline position."""
    # Use a conservative minimum based on sample rate - 10 frames at 10Hz = 1 second minimum
    # This prevents false positives from noise in the data
    min_lap_frames = max(10, int(round(1.0 * hz)))
    boundaries = []
    prev_norm = None

    for pt in track:
        norm = pt.get("norm_pos")
        if norm is None:
            return None
        if prev_norm is not None and prev_norm > 0.92 and norm < 0.08:
            frame = pt["frame"]
            if not boundaries or (frame - boundaries[-1]) >= min_lap_frames:
                boundaries.append(frame)
        prev_norm = norm

    return boundaries if len(boundaries) >= 2 else None


def _detect_laps_by_position(track: List[Dict], hz: float = 1.0, warmup_time_s: float = 40.0) -> List[int]:
    """Fallback lap detection using position."""
    # Use a conservative minimum based on sample rate - 10 frames at 10Hz = 1 second minimum
    # This prevents false positives from noise in the data
    min_lap_frames = max(10, int(round(1.0 * hz)))
    warmup_frames = max(0, int(round(warmup_time_s * hz)))

    ref_pt = None
    for pt in track[warmup_frames:]:
        if pt["speed"] > 80 and abs(pt["steer"]) < 0.05:
            ref_pt = pt
            break
    if ref_pt is None:
        ref_pt = track[min(warmup_frames, len(track) - 1)]

    ref_x, ref_z = ref_pt["x"], ref_pt["z"]
    boundaries = [ref_pt["frame"]]

    for pt in track:
        if pt["frame"] <= ref_pt["frame"] + min_lap_frames:
            continue
        dx = pt["x"] - ref_x
        dz = pt["z"] - ref_z
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 20 and pt["speed"] > 20:
            if (pt["frame"] - boundaries[-1]) >= min_lap_frames:
                boundaries.append(pt["frame"])

    return boundaries


def _detect_laps_by_timing_state(track: List[Dict], hz: float = 1.0) -> Optional[List[int]]:
    """Detect laps using shared memory timing state (last_laptime_ms updates)."""
    min_lap_frames = max(10, int(round(1.0 * hz)))
    boundaries = []
    prev_last_laptime = None
    
    for pt in track:
        last_laptime = pt.get("last_laptime_ms")
        if last_laptime is None:
            continue
            
        # Detect when last_laptime changes (lap completion event)
        if prev_last_laptime is not None and last_laptime != prev_last_laptime:
            frame = pt["frame"]
            if not boundaries or (frame - boundaries[-1]) >= min_lap_frames:
                boundaries.append(frame)
        prev_last_laptime = last_laptime
    
    return boundaries if len(boundaries) >= 1 else None


def detect_laps(track: List[Dict], hz: float = 1.0, allow_position_fallback: bool = True) -> List[int]:
    """Detect lap boundaries."""
    norm_result = _detect_laps_by_norm_pos(track, hz=hz)
    if norm_result:
        log_debug(Component.ANALYZER, "Lap detection: using normalized car position")
        return norm_result

    if not allow_position_fallback:
        log_debug(Component.ANALYZER, "Lap detection: normalized progress unavailable")
        return []

    log_debug(Component.ANALYZER, "Lap detection: using dead-reckoning position")
    return _detect_laps_by_position(track, hz=hz)


def extract_car_state(pt: Dict) -> Optional[Dict]:
    """Extract car state data from a track point."""
    if not pt:
        return None
    return {
        "abs": pt.get("abs", 0),
        "tc": pt.get("tc", 0),
        "steer": pt.get("steer", 0),
        "speed": pt.get("speed", 0),
        "gas": pt.get("gas", 0),
        "brake": pt.get("brake", 0),
        "acc_g_x": pt.get("acc_g_x", 0),
        "acc_g_y": pt.get("acc_g_y", 0),
        "acc_g_z": pt.get("acc_g_z", 0),
        "yaw_rate": pt.get("yaw_rate", 0),
        "air_temp": pt.get("air_temp", 0),
        "road_temp": pt.get("road_temp", 0),
        "tyre_temp_fl": pt.get("tyre_temp_fl", 0),
        "tyre_temp_fr": pt.get("tyre_temp_fr", 0),
        "tyre_temp_rl": pt.get("tyre_temp_rl", 0),
        "tyre_temp_rr": pt.get("tyre_temp_rr", 0),
        "pressure_fl": pt.get("pressure_fl", 0),
        "pressure_fr": pt.get("pressure_fr", 0),
        "pressure_rl": pt.get("pressure_rl", 0),
        "pressure_rr": pt.get("pressure_rr", 0),
        "slip_fl": pt.get("slip_fl", 0),
        "slip_fr": pt.get("slip_fr", 0),
        "slip_rl": pt.get("slip_rl", 0),
        "slip_rr": pt.get("slip_rr", 0),
        "load_fl": pt.get("load_fl", 0),
        "load_fr": pt.get("load_fr", 0),
        "load_rl": pt.get("load_rl", 0),
        "load_rr": pt.get("load_rr", 0),
        "sus_fl": pt.get("sus_fl", 0),
        "sus_fr": pt.get("sus_fr", 0),
        "sus_rl": pt.get("sus_rl", 0),
        "sus_rr": pt.get("sus_rr", 0),
        "camber_fl": pt.get("camber_fl", 0),
        "camber_fr": pt.get("camber_fr", 0),
        "camber_rl": pt.get("camber_rl", 0),
        "camber_rr": pt.get("camber_rr", 0),
        "brake_temp_fl": pt.get("brake_temp_fl", 0),
        "brake_temp_fr": pt.get("brake_temp_fr", 0),
        "brake_temp_rl": pt.get("brake_temp_rl", 0),
        "brake_temp_rr": pt.get("brake_temp_rr", 0),
        # AC Evo precision fields
        "fx_fl": pt.get("fx_fl", 0),
        "fx_fr": pt.get("fx_fr", 0),
        "fx_rl": pt.get("fx_rl", 0),
        "fx_rr": pt.get("fx_rr", 0),
        "fy_fl": pt.get("fy_fl", 0),
        "fy_fr": pt.get("fy_fr", 0),
        "fy_rl": pt.get("fy_rl", 0),
        "fy_rr": pt.get("fy_rr", 0),
        "slip_ratio_fl": pt.get("slip_ratio_fl", 0),
        "slip_ratio_fr": pt.get("slip_ratio_fr", 0),
        "slip_ratio_rl": pt.get("slip_ratio_rl", 0),
        "slip_ratio_rr": pt.get("slip_ratio_rr", 0),
        "slip_angle_fl": pt.get("slip_angle_fl", 0),
        "slip_angle_fr": pt.get("slip_angle_fr", 0),
        "slip_angle_rl": pt.get("slip_angle_rl", 0),
        "slip_angle_rr": pt.get("slip_angle_rr", 0),
        "brake_torque_fl": pt.get("brake_torque_fl", 0),
        "brake_torque_fr": pt.get("brake_torque_fr", 0),
        "brake_torque_rl": pt.get("brake_torque_rl", 0),
        "brake_torque_rr": pt.get("brake_torque_rr", 0),
        "brake_bias": pt.get("brake_bias"),
        "water_temp": pt.get("water_temp"),
        "gear_rpm_window": pt.get("gear_rpm_window"),
        "rpm_percent": pt.get("rpm_percent"),
        "current_bhp": pt.get("current_bhp"),
        "current_torque": pt.get("current_torque"),
    }


def detect_corners(track: List[Dict], lap_start_frame: int, lap_end_frame: int, hz: float = 1.0) -> List[Dict]:
    """Identify corners within a lap segment."""
    dheading_rate_thresh = 0.60
    merge_gap_s = 0.6
    min_dur_s = 0.8

    merge_gap = max(1, int(round(merge_gap_s * hz)))
    min_dur = max(1, int(round(min_dur_s * hz)))

    seg = [dict(pt) for pt in track if lap_start_frame <= pt["frame"] < lap_end_frame]
    if len(seg) < 4:
        return []

    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = idx / n

    corner_flags = [False]
    for i in range(1, len(seg)):
        dh = seg[i]["heading"] - seg[i - 1]["heading"]
        dh = (dh + math.pi) % (2 * math.pi) - math.pi
        corner_flags.append(abs(dh) * hz > dheading_rate_thresh)

    in_corner = False
    corners = []
    cur_start = None
    gap = 0
    for i, flag in enumerate(corner_flags):
        if flag:
            if not in_corner:
                in_corner = True
                cur_start = i
            gap = 0
        else:
            if in_corner:
                gap += 1
                if gap > merge_gap:
                    corners.append((cur_start, i - gap))
                    in_corner = False
                    gap = 0
    if in_corner:
        corners.append((cur_start, len(seg) - 1))

    result = []
    for cid, (ci_start, ci_end) in enumerate(corners):
        dur = ci_end - ci_start + 1
        if dur < min_dur:
            continue
        window = seg[ci_start:ci_end + 1]
        apex_idx = min(range(len(window)), key=lambda i: window[i]["speed"])
        apex = window[apex_idx]
        entry = window[0]
        exit_pt = window[-1]

        result.append({
            "id": cid,
            "start_frame": seg[ci_start]["frame"],
            "end_frame": seg[ci_end]["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": entry["speed"],
            "exit_speed": exit_pt["speed"],
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": seg[ci_start]["lap_pos"],
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    for i, c in enumerate(result):
        c["id"] = i + 1

    return result


def detect_profiled_corners(track: List[Dict], lap_start_frame: int, lap_end_frame: int, profile: Dict[str, Any]) -> List[Dict]:
    """Detect corners using predefined track profile windows."""
    seg = [dict(pt) for pt in track if lap_start_frame <= pt["frame"] < lap_end_frame]
    if not seg:
        return []

    has_norm_pos = seg[0].get("norm_pos") is not None
    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = pt["norm_pos"] if has_norm_pos else idx / n

    result = []
    for spec in profile.get("corners", []):
        window = [pt for pt in seg if spec["start"] <= pt["lap_pos"] < spec["end"]]
        if not window:
            continue

        apex = min(window, key=lambda pt: pt["speed"])
        entry = window[0]
        exit_pt = window[-1]
        result.append({
            "id": spec["id"],
            "name": spec["name"],
            "start_frame": entry["frame"],
            "end_frame": exit_pt["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": entry["speed"],
            "exit_speed": exit_pt["speed"],
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": apex["lap_pos"],
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    return result


def match_profiled_corners(ref_corners: List[Dict], lap_corners: List[Dict]) -> Dict[int, Optional[Dict]]:
    """Match profiled corners by stable corner id."""
    lap_by_id = {corner["id"]: corner for corner in lap_corners}
    return {ref_corner["id"]: lap_by_id.get(ref_corner["id"]) for ref_corner in ref_corners}


def match_corners(ref_corners: List[Dict], lap_corners: List[Dict], tol: float = 0.15) -> Dict:
    """Sequential nearest-neighbor corner matching."""
    matched = {}
    last_idx = 0
    for ref_corner in ref_corners:
        best = None
        best_dist = tol
        for i in range(last_idx, len(lap_corners)):
            dist = abs(lap_corners[i]["lap_pos"] - ref_corner["lap_pos"])
            if dist < best_dist:
                best_dist = dist
                best = lap_corners[i]
                last_idx = i
        matched[ref_corner["id"]] = best
    return matched


def corner_segment_time(corner: Dict, hz: float) -> float:
    """Seconds elapsed from corner start_frame to end_frame."""
    if corner.get("segment_time_s") is not None:
        return float(corner["segment_time_s"])
    return (corner["end_frame"] - corner["start_frame"]) / hz


def variation_label(delta_kmh: float) -> str:
    if delta_kmh >= 25:
        return "HIGH"
    if delta_kmh >= 15:
        return "MEDIUM"
    return "LOW"


def classify_corner_issue(entry_delta: float, apex_delta: float, exit_delta: float) -> str:
    """Heuristic: given speed deltas (best - worst) at entry/apex/exit, suggest root cause."""
    if entry_delta > apex_delta and entry_delta > exit_delta:
        return "Braking inconsistency — arriving at different speeds"
    if exit_delta > entry_delta and exit_delta > apex_delta:
        return "Throttle application point varies — losing drive on exit"
    if apex_delta > entry_delta and apex_delta > exit_delta:
        return "Line variation — mid-corner speed differs despite similar entry"
    return "Mixed — entry and exit both vary"


def format_car_state(state: Optional[Dict]) -> str:
    """Format car state (ABS, TC, temps, slip) for AI prompt."""
    if not state:
        return "No data"

    abs_active = "YES" if state.get("abs", 0) > 0.5 else "no"
    tc_active = "YES" if state.get("tc", 0) > 0.5 else "no"

    steer_rad = float(state.get("steer", 0) or 0)
    steer_deg = steer_rad * (180.0 / math.pi)
    yaw_rate = float(state.get("yaw_rate", 0) or 0)

    lat_g = float(state.get("acc_g_x", 0) or 0)
    long_g = float(state.get("acc_g_z", 0) or 0)

    temps = [float(state.get(f"tyre_temp_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    avg_temp = sum(temps) / len(temps) if temps else 0
    temp_range = f"{min(temps):.0f}-{max(temps):.0f}" if temps else "N/A"

    pressures = [float(state.get(f"pressure_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    avg_pressure = sum(pressures) / len(pressures) if pressures else 0

    slips = [float(state.get(f"slip_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_slip = max(slips[0], slips[1]) if len(slips) >= 2 else 0
    rear_slip = max(slips[2], slips[3]) if len(slips) >= 4 else 0

    loads = [float(state.get(f"load_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_load = (loads[0] + loads[1]) / 2 if len(loads) >= 2 else 0
    rear_load = (loads[2] + loads[3]) / 2 if len(loads) >= 4 else 0

    sus = [float(state.get(f"sus_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_sus = (sus[0] + sus[1]) / 2 if len(sus) >= 2 else 0
    rear_sus = (sus[2] + sus[3]) / 2 if len(sus) >= 4 else 0

    bt = [float(state.get(f"brake_temp_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_bt = (bt[0] + bt[1]) / 2 if len(bt) >= 2 else 0
    rear_bt = (bt[2] + bt[3]) / 2 if len(bt) >= 4 else 0

    # AC Evo precision fields
    fx_vals = [float(state.get(f"fx_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    fy_vals = [float(state.get(f"fy_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    slip_ratio_vals = [float(state.get(f"slip_ratio_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    slip_angle_vals = [float(state.get(f"slip_angle_{x}", 0) or 0) * (180.0 / math.pi) for x in ["fl", "fr", "rl", "rr"]]  # Convert to degrees
    brake_torque_vals = [float(state.get(f"brake_torque_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    
    front_fx = (fx_vals[0] + fx_vals[1]) / 2 if len(fx_vals) >= 2 else 0
    rear_fx = (fx_vals[2] + fx_vals[3]) / 2 if len(fx_vals) >= 4 else 0
    front_fy = (fy_vals[0] + fy_vals[1]) / 2 if len(fy_vals) >= 2 else 0
    rear_fy = (fy_vals[2] + fy_vals[3]) / 2 if len(fy_vals) >= 4 else 0
    
    front_slip_ratio = (slip_ratio_vals[0] + slip_ratio_vals[1]) / 2 if len(slip_ratio_vals) >= 2 else 0
    rear_slip_ratio = (slip_ratio_vals[2] + slip_ratio_vals[3]) / 2 if len(slip_ratio_vals) >= 4 else 0
    front_slip_angle = (slip_angle_vals[0] + slip_angle_vals[1]) / 2 if len(slip_angle_vals) >= 2 else 0
    rear_slip_angle = (slip_angle_vals[2] + slip_angle_vals[3]) / 2 if len(slip_angle_vals) >= 4 else 0
    
    front_brake_torque = (brake_torque_vals[0] + brake_torque_vals[1]) / 2 if len(brake_torque_vals) >= 2 else 0
    rear_brake_torque = (brake_torque_vals[2] + brake_torque_vals[3]) / 2 if len(brake_torque_vals) >= 4 else 0
    
    brake_bias_val = state.get("brake_bias")
    brake_bias_str = f" BrakeBias:{brake_bias_val:.2f}" if brake_bias_val is not None and brake_bias_val > 0 else ""
    
    gear_rpm_window = state.get("gear_rpm_window")
    rpm_percent = state.get("rpm_percent")
    gear_str = ""
    if gear_rpm_window is not None and gear_rpm_window > 0:
        gear_str = f" GearOpt:{gear_rpm_window:.2f}"
    elif rpm_percent is not None and rpm_percent > 0:
        gear_str = f" RPM%:{rpm_percent:.1%}"
    
    # Build precision data string if any tire force data is present
    precision_str = ""
    if abs(front_fx) + abs(rear_fx) + abs(front_fy) + abs(rear_fy) > 100:  # Only show if meaningful values
        precision_str = (
            f" | Fx(F/R):{front_fx:.0f}/{rear_fx:.0f}N Fy(F/R):{front_fy:.0f}/{rear_fy:.0f}N"
            f" SlipRatio(F/R):{front_slip_ratio:.2f}/{rear_slip_ratio:.2f}"
            f" SlipAngle(F/R):{front_slip_angle:.1f}/{rear_slip_angle:.1f}deg"
        )
    
    brake_torque_str = ""
    if abs(front_brake_torque) + abs(rear_brake_torque) > 100:
        brake_torque_str = f" BrakeTq(F/R):{front_brake_torque:.0f}/{rear_brake_torque:.0f}Nm"

    # DRS status
    drs_state = state.get("drs", 0)
    drs_enabled = state.get("drs_enabled", False)
    drs_str = ""
    if drs_enabled or drs_state > 0.5:
        drs_str = f" DRS:OPEN"
    elif state.get("drs_available", False):
        drs_str = f" DRS:AVAIL"

    return (
        f"ABS:{abs_active} TC:{tc_active} "
        f"Steer:{steer_deg:+.1f}deg Yaw:{yaw_rate:+.3f} "
        f"G(lat/long):{lat_g:+.2f}/{long_g:+.2f} "
        f"Slip(F/R):{front_slip:.2f}/{rear_slip:.2f} "
        f"Load(F/R):{front_load:.0f}/{rear_load:.0f} "
        f"Sus(F/R):{front_sus:.3f}/{rear_sus:.3f} "
        f"BrakeT(F/R):{front_bt:.0f}/{rear_bt:.0f} "
        f"TyreT:{avg_temp:.0f}C({temp_range}) "
        f"P:{avg_pressure:.1f}"
        f"{brake_bias_str}{gear_str}{brake_torque_str}{drs_str}{precision_str}"
    )


def balance_hint(state: Optional[Dict]) -> str:
    """Rough balance hint (understeer/oversteer/neutral) from per-point telemetry."""
    if not state:
        return "unknown"
    slips = [float(state.get(f"slip_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_slip = max(slips[0], slips[1]) if len(slips) >= 2 else 0
    rear_slip = max(slips[2], slips[3]) if len(slips) >= 4 else 0
    steer = abs(float(state.get("steer", 0) or 0))
    yaw = abs(float(state.get("yaw_rate", 0) or 0))

    if front_slip > rear_slip * 1.15 and steer > 0.03 and yaw < 0.20:
        return "understeer"
    if rear_slip > front_slip * 1.15 and yaw > 0.25:
        return "oversteer"
    return "neutral"


def _find_frame_index(track: List[Dict], frame: int) -> int:
    """Find the index in track list closest to a given frame number."""
    for i, pt in enumerate(track):
        if pt["frame"] >= frame:
            return i
    return len(track) - 1


def analyze_corner_phases(
    track: List[Dict],
    corner: Dict,
    lap_start_frame: int,
    hz: float,
    approach_seconds: float = 3.0,
    exit_seconds: float = 2.0,
) -> Optional[Dict]:
    """Analyze brake, turn-in, and throttle timing around a corner.

    Scans the approach zone (before corner start) and exit zone (after apex)
    to find:
      - brake_onset_dt: seconds before corner entry that brake > threshold
      - turn_in_dt: seconds before corner entry that |steer| exceeds threshold
      - gas_on_dt: seconds after apex that throttle > threshold
      - trail_brake_frames: how many frames from entry to apex still have brake > 0.05
      - coast_frames: frames near apex with both gas < 0.1 and brake < 0.1

    Returns None if there isn't enough data.
    """
    BRAKE_THRESH = 0.10
    STEER_THRESH = 0.03  # ~1.7 degrees
    GAS_THRESH = 0.15

    corner_start = corner["start_frame"]
    apex_frame = corner["apex_frame"]
    corner_end = corner["end_frame"]

    approach_frames = int(approach_seconds * hz)
    exit_frames = int(exit_seconds * hz)

    # Get approach zone: frames leading up to corner entry
    approach_start = max(lap_start_frame, corner_start - approach_frames)
    approach = [pt for pt in track if approach_start <= pt["frame"] < corner_start]
    # Corner zone: entry to exit
    corner_zone = [pt for pt in track if corner_start <= pt["frame"] <= corner_end]
    # Exit zone: from apex onward
    exit_zone = [pt for pt in track if apex_frame <= pt["frame"] <= corner_end + exit_frames]

    if len(approach) < 3 or len(corner_zone) < 3:
        return None

    # ── Brake onset: scan approach backwards from corner entry to find first brake
    brake_onset_dt = None
    for pt in reversed(approach):
        if pt.get("brake", 0) >= BRAKE_THRESH:
            brake_onset_dt = (corner_start - pt["frame"]) / hz
        else:
            if brake_onset_dt is not None:
                break  # Found the start of the braking zone

    # If brake was already applied at approach start, mark it
    if brake_onset_dt is None:
        # Check if braking at corner entry
        if corner_zone and corner_zone[0].get("brake", 0) >= BRAKE_THRESH:
            brake_onset_dt = 0.0

    # ── Turn-in: scan approach backwards to find steer onset
    turn_in_dt = None
    for pt in reversed(approach):
        if abs(pt.get("steer", 0)) >= STEER_THRESH:
            turn_in_dt = (corner_start - pt["frame"]) / hz
        else:
            if turn_in_dt is not None:
                break

    if turn_in_dt is None:
        if corner_zone and abs(corner_zone[0].get("steer", 0)) >= STEER_THRESH:
            turn_in_dt = 0.0

    # ── Gas-on: scan from apex forward to find throttle application
    # Prefer graphics gas_percent over physics gas for better data quality
    gas_on_dt = None
    for pt in exit_zone:
        gas_val = pt.get("gas_percent", pt.get("gas", 0))
        if gas_val >= GAS_THRESH:
            gas_on_dt = (pt["frame"] - apex_frame) / hz
            break

    # ── Trail braking: frames from entry to apex with brake > threshold
    entry_to_apex = [pt for pt in corner_zone if pt["frame"] <= apex_frame]
    trail_brake_frames = sum(1 for pt in entry_to_apex if pt.get("brake", 0) > 0.05)
    trail_brake_pct = trail_brake_frames / max(len(entry_to_apex), 1)

    # ── Coast frames: near apex, both gas and brake below threshold
    coast_frames_half_window = int(0.5 * hz)
    apex_vicinity = [
        pt for pt in corner_zone
        if abs(pt["frame"] - apex_frame) <= coast_frames_half_window
    ]
    coast_frames = sum(
        1 for pt in apex_vicinity
        if (pt.get("gas_percent", pt.get("gas", 0)) < 0.10 
            and pt.get("brake", 0) < 0.10)
    )

    # ── Peak braking G (longitudinal deceleration)
    peak_brake_g = 0.0
    for pt in approach + entry_to_apex:
        long_g = abs(pt.get("acc_g_z", 0))
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
    """Analyze grip usage through friction circle metrics.

    For the corner window, compute:
      - peak_total_g: max sqrt(lat_g^2 + long_g^2) — the grip envelope
      - avg_total_g: average combined G through the corner
      - peak_lat_g: peak lateral G (cornering force)
      - peak_long_g: peak longitudinal G (braking/accel)
      - grip_fill_pct: average total_g / peak_total_g — how much of the grip
        envelope is being used on average (100% = always at the limit)
      - combined_braking_pct: % of braking-zone frames where lat_g > 0.3
        (trail braking into corner = using combined grip)
    """
    corner_pts = [
        pt for pt in track
        if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
    ]
    if len(corner_pts) < 3:
        return None

    total_gs = []
    lat_gs = []
    long_gs = []
    combined_brake_count = 0
    brake_count = 0

    for pt in corner_pts:
        lat_g = abs(pt.get("acc_g_x", 0))
        long_g = abs(pt.get("acc_g_z", 0))
        total_g = math.sqrt(lat_g ** 2 + long_g ** 2)
        total_gs.append(total_g)
        lat_gs.append(lat_g)
        long_gs.append(long_g)

        if pt.get("brake", 0) > 0.05:
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


# ── Tyre grip-degradation analysis (stint-level) ──────────────────────────────
#
# For longer races the user can lose grip from any combination of:
#   - tyre core temperature climbing past the optimal window (overheating)
#   - mechanical wear (tyre_wear field, 0.0 fresh -> 1.0 worn)
#   - dirt pickup off-line (tyre_dirty_level)
#   - increasing slip angles / sliding as the carcass softens
#
# The functions below compute compact per-lap summaries of those signals so
# the AI prompt can show a stint-progression table and call out monotonic
# degradation trends (e.g. "lat-G falling lap after lap with similar inputs
# = tyres losing grip; back off slightly for longer races").

def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _trend_direction(values: List[float], threshold: float) -> str:
    """Classify a per-lap series as RISING / FALLING / FLAT.

    Uses the per-step delta against ``threshold``. A series is only flagged
    monotonic if every step moves in the same direction beyond the threshold;
    otherwise it is FLAT. Needs at least 3 laps to be meaningful.
    """
    if len(values) < 3:
        return "FLAT"
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if all(d > threshold for d in diffs):
        return "RISING"
    if all(d < -threshold for d in diffs):
        return "FALLING"
    # Net trend if endpoints diverge enough even with noise in between.
    span = values[-1] - values[0]
    if span > threshold * (len(values) - 1):
        return "RISING"
    if span < -threshold * (len(values) - 1):
        return "FALLING"
    return "FLAT"


def analyze_lap_tyre_state(lap_track: List[Dict]) -> Optional[Dict]:
    """Per-lap tyre summary for stint-level grip degradation analysis.

    Cornering frames are isolated by lateral G threshold so that the slip,
    lat-G, and slip-angle metrics reflect grip-limited driving rather than
    straight-line cruising. Wear and core-temp metrics use the whole lap
    because they evolve continuously.
    """
    if not lap_track:
        return None

    # ── Cornering subset: |lat_g| > 0.5 (loose threshold; covers any non-trivial corner)
    corner_frames = [pt for pt in lap_track if abs(pt.get("acc_g_x", 0.0)) > 0.5]

    # ── Tyre core temps (per corner)
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

    # ── Wear delta across the lap (front + rear average)
    wear_keys = ("tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl", "tyre_wear_rr")
    start_wear = _avg([float(lap_track[0].get(k, 0.0) or 0.0) for k in wear_keys])
    end_wear = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in wear_keys])
    wear_delta = max(0.0, end_wear - start_wear)

    # ── Dirt pickup (end-of-lap snapshot, average across wheels)
    dirty_keys = ("tyre_dirty_fl", "tyre_dirty_fr", "tyre_dirty_rl", "tyre_dirty_rr")
    end_dirty = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in dirty_keys])

    # ── Cornering-only metrics (grip-limited frames)
    if corner_frames:
        peak_lat_g = max(abs(pt.get("acc_g_x", 0.0)) for pt in corner_frames)
        avg_lat_g = _avg([abs(pt.get("acc_g_x", 0.0)) for pt in corner_frames])
        # Slip angle peaks (radians) — use max wheel each frame, then session peak
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
    """Compute per-lap tyre states and detect stint-level grip-loss trends.

    Returns a dict with:
      - per_lap: list of (lap_num, state_dict) tuples
      - trends: dict of trend labels for each tracked metric
      - flags: human-readable warnings (e.g. "lat-G falling across stint")
    """
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

        trends["core_temp"] = _trend_direction(avg_temps, threshold=1.5)        # >1.5 °C/lap
        trends["peak_lat_g"] = _trend_direction(peak_lat_gs, threshold=0.03)    # >0.03G/lap
        trends["peak_slip_angle"] = _trend_direction(peak_slips, threshold=0.3)  # >0.3°/lap
        trends["wear"] = _trend_direction(end_wear, threshold=0.2)              # >0.2%/lap

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
                "Peak slip angles are growing each lap — the car is sliding more, "
                "another sign of grip falloff."
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
    """Per-lap snapshot of electronic aid settings (TC, ABS, engine map, diff).

    Uses the first track-point of each lap as the representative settings
    snapshot and detects whether any key setting was changed by the final
    track-point (mid-lap adjustment).
    """
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
            f = first.get(key)
            ll = last.get(key)
            return f is not None and ll is not None and f != ll

        result.append({
            "lap_num": lap["lap_num"],
            "tc_level": _val(first, "tc_level"),
            "abs_level": _val(first, "abs_level"),
            "engine_map": _val(first, "engine_map_level"),
            "diff_power": _val(first, "diff_power_level"),
            "diff_coast": _val(first, "diff_coast_level"),
            "perf_mode": _val(first, "electronics_perf_mode"),
            "tc_changed": _changed("tc_level"),
            "abs_changed": _changed("abs_level"),
            "engine_map_changed": _changed("engine_map_level"),
        })
    return result



class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""

    def __init__(
        self,
        output_dir: str,
        track_catalog: dict = None,
        session_manager: Optional[SharedSessionManager] = None,
    ):
        self._output_dir = output_dir
        self._track_catalog = track_catalog
        self._session_manager = session_manager or SharedSessionManager()

    async def analyze(
        self,
        frames: List[FrameData],
        hz: float,
        metadata: Optional[CaptureMetadata] = None,
        track_name: Optional[str] = None,
        output_prefix: Optional[str] = None,
        game_lap_boundaries: Optional[List] = None,  # Can be List[int] or List[Tuple[int, Optional[float], Optional[int]]]
    ) -> AnalysisResult:
        """Run full analysis pipeline and generate outputs."""
        log_info(Component.ANALYZER, "Starting analysis", frames=len(frames), hz=hz, track=track_name, prefix=output_prefix)

        if len(frames) < 20:
            log_warning(Component.ANALYZER, "Analysis skipped: insufficient frames", frames=len(frames), prefix=output_prefix)
            return await self._generate_empty_result(output_prefix)

        track_key, track_profile = _select_track_profile_for_analysis(track_name)
        if track_profile:
            log_info(Component.ANALYZER, "Track profile selected", profile=track_profile['display_name'])
        else:
            log_debug(Component.ANALYZER, "Track profile: none - using auto corner detection")

        drive_start = 0
        for i, f in enumerate(frames):
            ph = get_physics(f)
            if ph and ph.get("speed_kmh", 0) > 5:
                if all(
                    get_physics(frames[min(i + j, len(frames) - 1)]).get("speed_kmh", 0) > 2
                    for j in range(5)
                    if get_physics(frames[min(i + j, len(frames) - 1)])
                ):
                    drive_start = max(0, i - 5)
                    break

        track = build_track(frames, hz=hz, start_idx=drive_start)
        if not track:
            log_warning(Component.ANALYZER, "No plausible telemetry frames after quality filtering")
            return await self._generate_empty_result(output_prefix)

        authoritative_progress_ratio = _fraction(track, lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None)
        plausible_frame_ratio = _fraction(
            track,
            lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
        )
        analysis_confidence_score = round(authoritative_progress_ratio * 0.7 + plausible_frame_ratio * 0.3, 3)
        analysis_confidence = _confidence_label(analysis_confidence_score)

        analysis_mode, has_authoritative, has_high_plausible = _decide_analysis_mode(
            authoritative_progress_ratio, plausible_frame_ratio,
        )
        analysis_notes: List[str] = []

        log_info(Component.ANALYZER, "Data quality assessed",
                progress_ratio=f"{authoritative_progress_ratio:.1%}",
                frame_ratio=f"{plausible_frame_ratio:.1%}",
                confidence=analysis_confidence,
                confidence_score=analysis_confidence_score)
        log_info(Component.ANALYZER, "Analysis mode determined", mode=analysis_mode,
                 auth_ok=has_authoritative, plausible_fallback_ok=has_high_plausible)

        if not has_authoritative and has_high_plausible:
            # Full coaching is unlocked via the plausible-physics fallback;
            # flag this in the notes so the user knows authoritative progress
            # from graphics SHM would further improve analysis quality.
            analysis_notes.append(
                f"Authoritative graphics progress coverage is {authoritative_progress_ratio:.0%}, "
                f"but physics frame plausibility is {plausible_frame_ratio:.0%} — using "
                "dead-reckoning progress for coaching. Lap 1 may be missing if capture "
                "started mid-lap."
            )
        elif not has_authoritative and not has_high_plausible:
            analysis_notes.append(
                f"Authoritative graphics progress coverage too low ({authoritative_progress_ratio:.0%}) "
                f"and plausible physics coverage is only {plausible_frame_ratio:.0%}; detailed coaching disabled."
            )
        if plausible_frame_ratio < 0.75:
            analysis_notes.append(
                f"Physics frame plausibility coverage is only {plausible_frame_ratio:.0%}; derived metrics are degraded."
            )

        # Prioritize definitive lap detection sources over telemetry heuristics.
        # 1st: Game log boundaries (most authoritative)
        # 2nd: Shared memory timing state (last_laptime_ms updates) 
        # 3rd: Telemetry-based detection (position crossing as fallback)
        lap_bounds = None
        lap_times_ms = None
        lap_numbers = None
        prefer_game_lap_times = False

        # 1st priority: Game log boundaries (most definitive)
        if game_lap_boundaries and len(game_lap_boundaries) >= 1:
            # Extract frame indices and lap times from tuples
            if isinstance(game_lap_boundaries[0], (tuple, list)):
                initial_completed_laps = 0
                try:
                    initial_completed_laps = int(track[0].get("completed_laps") or 0)
                except (TypeError, ValueError):
                    initial_completed_laps = 0

                sorted_markers = sorted(
                    (
                        (
                            int(b[0]),
                            b[1] if len(b) > 1 else None,
                            int(b[2]) if len(b) > 2 and b[2] is not None else None,
                        )
                        for b in game_lap_boundaries
                    ),
                    key=lambda item: item[0],
                )
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + [marker[0] for marker in sorted_markers]
                lap_times_ms = [marker[1] for marker in sorted_markers]
                lap_numbers = [
                    marker[2] if marker[2] is not None else initial_completed_laps + idx + 1
                    for idx, marker in enumerate(sorted_markers)
                ]
                prefer_game_lap_times = True
                if initial_completed_laps > 0 and (not lap_numbers or lap_numbers[0] > 1):
                    analysis_notes.append(
                        f"Capture started after {initial_completed_laps} completed game lap(s); earlier laps are omitted from telemetry."
                    )
            else:
                lap_bounds = game_lap_boundaries
            log_info(Component.ANALYZER, "Lap detection successful", method="authoritative game log boundaries", laps=len(lap_bounds))
        # 2nd priority: Shared memory timing state (last_laptime_ms updates)
        else:
            timing_bounds = _detect_laps_by_timing_state(track, hz=hz)
            if timing_bounds and len(timing_bounds) >= 1:
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + timing_bounds
                log_info(Component.ANALYZER, "Lap detection successful", method="shared memory timing state", laps=len(lap_bounds))
            # 3rd priority: Telemetry-based detection (normalized position)
            else:
                lap_bounds = detect_laps(track, hz=hz, allow_position_fallback=False)
                if lap_bounds and len(lap_bounds) >= 2:
                    log_info(Component.ANALYZER, "Lap detection successful", method="telemetry-based (normalized position)", laps=len(lap_bounds))
                else:
                    # Try position-based lap detection as final fallback
                    lap_bounds = detect_laps(track, hz=hz, allow_position_fallback=True)
                    if lap_bounds and len(lap_bounds) >= 2:
                        log_info(Component.ANALYZER, "Lap detection successful", method="telemetry-based (position fallback)", laps=len(lap_bounds))

        if not lap_bounds or len(lap_bounds) < 2:
            log_warning(Component.ANALYZER, "Lap detection failed", reason="no valid boundaries")
            analysis_mode = "diagnostic"
            analysis_notes.append("No reliable lap boundaries were found from any detection method.")
            lap_bounds = []

        laps = []
        for i in range(len(lap_bounds) - 1):
            s, e = lap_bounds[i], lap_bounds[i + 1]
            game_lap_num = lap_numbers[i] if lap_numbers and i < len(lap_numbers) else i + 1
            lap_track = [pt for pt in track if s <= pt["frame"] < e]
            if len(lap_track) < 20:
                continue

            lap_progress_ratio = _fraction(
                lap_track,
                lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None,
            )
            lap_plausible_ratio = _fraction(
                lap_track,
                lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
            )
            lap_quality_score = round(lap_progress_ratio * 0.7 + lap_plausible_ratio * 0.3, 3)
            canonical_lap = _build_canonical_lap(lap_track, lap_start_frame=s, hz=hz, bins=200)
            uses_canonical_progress = canonical_lap is not None

            if track_profile and track_profile.get("corners") and uses_canonical_progress:
                corners = _detect_profiled_corners_canonical(
                    canonical_lap["samples"],
                    track_profile,
                    hz,
                    authoritative_progress=lap_progress_ratio >= 0.60,
                )
            elif track_profile and track_profile.get("corners"):
                # Use profile-based corner detection even without canonical progress
                corners = detect_profiled_corners(track, s, e, track_profile)
            else:
                corners = detect_corners(track, s, e, hz=hz)

            # Use game-reported lap times when available.
            if lap_times_ms and i < len(lap_times_ms) and lap_times_ms[i] is not None:
                lap_time = lap_times_ms[i] / 1000.0  # Convert ms to seconds
            elif prefer_game_lap_times:
                # If game times were provided for this analysis, do not silently
                # mix in telemetry-derived durations for missing entries.
                continue
            else:
                lap_time = (e - s) / hz
            
            # Calculate fuel consumption from telemetry (start fuel - end fuel)
            fuel_used = None
            if lap_track:
                # Get fuel level at lap start and end
                start_pt = next((pt for pt in track if pt["frame"] == s), None)
                end_pt = next((pt for pt in track if pt["frame"] == e), None)
                
                if start_pt and end_pt:
                    fuel_start = start_pt.get("fuel")
                    fuel_end = end_pt.get("fuel")
                    
                    if fuel_start is not None and fuel_end is not None and fuel_start > fuel_end:
                        fuel_used = round(fuel_start - fuel_end, 3)
            
            laps.append({
                "lap_num": game_lap_num,
                "capture_lap_index": i + 1,
                "start_frame": s,
                "end_frame": e,
                "lap_time_s": lap_time,
                "lap_time_str": f"{int(lap_time // 60)}:{lap_time % 60:05.2f}",
                "max_speed": max(pt["speed"] for pt in lap_track),
                "avg_speed": sum(pt["speed"] for pt in lap_track) / len(lap_track),
                "fuel_used": fuel_used,
                "track": lap_track,
                "canonical_track": canonical_lap["samples"] if canonical_lap else None,
                "corners": corners,
                "quality_score": lap_quality_score,
                "confidence_label": _confidence_label(lap_quality_score),
                "progress_ratio": lap_progress_ratio,
                "plausible_frame_ratio": lap_plausible_ratio,
                "uses_canonical_progress": uses_canonical_progress,
            })
            fuel_str = f"  fuel {fuel_used:.3f}L" if fuel_used is not None else ""
            log_debug(Component.ANALYZER, "Lap summary", lap_num=game_lap_num, lap_time=f"{lap_time:.0f}s", max_speed=f"{max(pt['speed'] for pt in lap_track):.0f} km/h", corners=len(corners), fuel=fuel_str)

        if not laps:
            log_warning(Component.ANALYZER, "Analysis complete: no valid laps found")
            return await self._generate_empty_result(output_prefix)

        # Prefer authoritative lap data already merged into the shared session
        # state (e.g. log parser + graphics SHM) when available.
        shared_lap_times = self._session_manager.get_all_lap_times()
        shared_lap_validity = self._session_manager.get_all_lap_validity()
        if shared_lap_times and laps:
            try:
                max_shared_lap = max(int(k) for k in shared_lap_times.keys())
                max_analyzed_lap = max(int(lap["lap_num"]) for lap in laps)
                min_analyzed_lap = min(int(lap["lap_num"]) for lap in laps)
                if min_analyzed_lap > 1:
                    analysis_notes.append(
                        f"Telemetry starts at game lap {min_analyzed_lap}; earlier logged laps are not included."
                    )
                if max_shared_lap > max_analyzed_lap:
                    analysis_mode = "diagnostic"
                    analysis_notes.append(
                        f"Log/shared session reaches lap {max_shared_lap}, but telemetry only reaches lap {max_analyzed_lap}; detailed coaching suppressed."
                    )
            except (TypeError, ValueError):
                pass
        for lap in laps:
            shared_time_ms = shared_lap_times.get(lap["lap_num"])
            if isinstance(shared_time_ms, (int, float)) and shared_time_ms > 0:
                shared_lap_time_s = float(shared_time_ms) / 1000.0
                lap["lap_time_s"] = shared_lap_time_s
                lap["lap_time_str"] = f"{int(shared_lap_time_s // 60)}:{shared_lap_time_s % 60:05.2f}"

            shared_validity = shared_lap_validity.get(lap["lap_num"])
            if isinstance(shared_validity, bool):
                lap["is_valid"] = shared_validity

        profile_sanity_notes = _profile_corner_sanity_notes(laps)
        if profile_sanity_notes:
            analysis_mode = "diagnostic"
            analysis_notes.extend(profile_sanity_notes)

        best_lap = min(laps, key=lambda lap: lap["lap_time_s"])
        laps_with_corners = [lap for lap in laps if lap.get("corners")]
        ref_lap = min(laps_with_corners, key=lambda lap: lap["lap_time_s"]) if laps_with_corners else best_lap
        coachable_laps = [lap for lap in laps_with_corners if lap.get("confidence_label") != "low"]
        comparison_pool = coachable_laps or laps_with_corners or [best_lap]
        comparison_pool = sorted(comparison_pool, key=lambda lap: lap["lap_time_s"])
        comparison_lap = comparison_pool[len(comparison_pool) // 2]
        ref_corners = ref_lap.get("corners", [])

        log_info(Component.ANALYZER, "Analysis complete", 
                laps=len(laps), 
                best_lap_time=f"{best_lap['lap_time_s']:.1f}s", 
                coachable_laps=len(coachable_laps))

        if not ref_corners:
            analysis_mode = "diagnostic"
            analysis_notes.append("No trustworthy canonical corners were available for comparison.")

        corner_data = defaultdict(dict)
        corner_speeds = defaultdict(dict)
        for lap in laps:
            if track_profile and track_profile.get("corners"):
                matched = match_profiled_corners(ref_corners, lap["corners"])
            else:
                matched = match_corners(ref_corners, lap["corners"])
            for cid, corner in matched.items():
                if corner and corner.get("confidence_label") != "low":
                    seg_time = corner_segment_time(corner, hz)
                    corner_data[cid][lap["lap_num"]] = {
                        "apex": round(corner["apex_speed"], 1),
                        "entry": round(corner["entry_speed"], 1),
                        "exit": round(corner["exit_speed"], 1),
                        "seg_time": round(seg_time, 3),
                        "confidence": round(float(corner.get("confidence", 0.0)), 3),
                        "confidence_label": corner.get("confidence_label", "low"),
                    }
                    corner_speeds[cid][lap["lap_num"]] = corner["apex_speed"]

        data = {
            "meta": metadata.to_dict() if metadata else {},
            "hz": hz,
            "track_key": track_key,
            "track_name": track_profile["track_name"] if track_profile else track_name,
            "config_key": track_profile["config_key"] if track_profile else None,
            "config_name": track_profile["config_name"] if track_profile else None,
            "track_label": track_profile["display_name"] if track_profile else track_name,
            "car": self._session_manager.get_car(),
            "laps": laps,
            "best_lap_num": best_lap["lap_num"],
            "reference_lap_num": ref_lap["lap_num"],
            "comparison_lap_num": comparison_lap["lap_num"],
            "ref_corners": ref_corners,
            "corner_data": corner_data,
            "corner_speeds": corner_speeds,
            "telem": track,
            "drive_start": drive_start,
            "lap_bounds": lap_bounds,
            "analysis_mode": analysis_mode,
            "analysis_confidence": analysis_confidence,
            "analysis_confidence_score": analysis_confidence_score,
            "analysis_notes": analysis_notes,
            "authoritative_progress_ratio": authoritative_progress_ratio,
            "plausible_frame_ratio": plausible_frame_ratio,
        }

        telemetry_summary = {
            "max_speed": max((lap.get("max_speed") or 0.0) for lap in laps),
            "stint_number": 1,
        }
        self._session_manager.update_from_telemetry(telemetry_summary)

        log_info(Component.ANALYZER, "Generating outputs", prefix=output_prefix)
        html_path = await self._generate_html(data, output_prefix)
        ai_prompt_path = await self._generate_ai_prompt(data, output_prefix)
        log_info(Component.ANALYZER, "Outputs generated", html=html_path, ai_prompt=ai_prompt_path)

        return AnalysisResult(
            html_path=html_path,
            ai_prompt_path=ai_prompt_path,
            laps_detected=len(laps),
            best_lap_time=best_lap["lap_time_s"],
            track_name=data.get("track_label") or data.get("track_name"),
        )

    async def _generate_empty_result(self, output_prefix: Optional[str] = None) -> AnalysisResult:
        """Generate result for empty/invalid data without creating files."""
        log_info(Component.ANALYZER, "Skipping output: insufficient or invalid telemetry data", prefix=output_prefix)
        return AnalysisResult(
            html_path=None,
            ai_prompt_path=None,
            laps_detected=0,
            best_lap_time=0.0,
            track_name=None,
        )

    async def _generate_html(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate HTML report with full telemetry visualization."""
        prefix = output_prefix or datetime.now().strftime("%m-%d-%H-%M-%S")
        html_path = os.path.join(self._output_dir, f"telemetry_{prefix}.html")

        os.makedirs(self._output_dir, exist_ok=True)

        laps_json = []
        for lap in data["laps"]:
            render_track = lap.get("canonical_track") or lap["track"]
            track_slim = [
                {
                    "frame": pt["frame"],
                    "x": round(pt["x"], 2),
                    "z": round(pt["z"], 2),
                    "speed": round(pt["speed"], 1),
                    "brake": round(pt["brake"], 3),
                    "gas": round(pt["gas"], 3),
                    "gear": pt["gear"],
                    "steer": round(pt.get("steer", 0), 6),
                    "yaw_rate": round(pt.get("yaw_rate", 0), 6),
                    "acc_g_x": round(pt.get("acc_g_x", 0), 6),
                    "acc_g_z": round(pt.get("acc_g_z", 0), 6),
                    "brake_temp_fl": round(pt.get("brake_temp_fl", 0), 2),
                    "brake_temp_fr": round(pt.get("brake_temp_fr", 0), 2),
                    "brake_temp_rl": round(pt.get("brake_temp_rl", 0), 2),
                    "brake_temp_rr": round(pt.get("brake_temp_rr", 0), 2),
                }
                for pt in render_track
            ]
            corners_json = [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "start_frame": c["start_frame"],
                    "end_frame": c["end_frame"],
                    "apex_frame": c["apex_frame"],
                    "apex_speed": round(c["apex_speed"], 1),
                    "entry_speed": round(c["entry_speed"], 1),
                    "exit_speed": round(c["exit_speed"], 1),
                    "apex_x": round(c["apex_x"], 1),
                    "apex_z": round(c["apex_z"], 1),
                    "lap_pos": round(c["lap_pos"], 4),
                }
                for c in lap["corners"]
            ]
            laps_json.append({
                "lap_num": lap["lap_num"],
                "start_frame": lap["start_frame"],
                "end_frame": lap["end_frame"],
                "lap_time_s": round(lap["lap_time_s"], 3),
                "lap_time_str": lap["lap_time_str"],
                "max_speed": round(lap["max_speed"], 1),
                "avg_speed": round(lap["avg_speed"], 1),
                "confidence_label": lap.get("confidence_label"),
                "track": track_slim,
                "corners": corners_json,
            })

        ref_corners_json = [
            {
                "id": c["id"],
                "name": c.get("name"),
                "lap_pos": round(c["lap_pos"], 4),
            }
            for c in data["ref_corners"]
        ]

        corner_data_json = {}
        for cid, speeds in data["corner_data"].items():
            corner_data_json[str(cid)] = {str(k): v for k, v in speeds.items()}

        corner_speeds_json = {}
        for cid, speeds in data["corner_speeds"].items():
            corner_speeds_json[str(cid)] = {str(k): v for k, v in speeds.items()}

        data_json = json.dumps({
            "meta": data["meta"],
            "hz": data["hz"],
            "track_key": data["track_key"],
            "track_name": data["track_name"],
            "config_key": data["config_key"],
            "config_name": data["config_name"],
            "track_label": data["track_label"],
            "laps": laps_json,
            "best_lap_num": data["best_lap_num"],
            "reference_lap_num": data.get("reference_lap_num"),
            "comparison_lap_num": data.get("comparison_lap_num"),
            "analysis_mode": data.get("analysis_mode"),
            "analysis_confidence": data.get("analysis_confidence"),
            "analysis_notes": data.get("analysis_notes", []),
            "ref_corners": ref_corners_json,
            "corner_data": corner_data_json,
            "corner_speeds": corner_speeds_json,
        })

        html_content = self._build_html_template(data_json)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        log_debug(Component.ANALYZER, "Generated HTML report", path=html_path)
        return html_path

    @staticmethod
    def _build_html_template(data_json: str) -> str:
        """Build the full HTML report template with all chart sections."""
        return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC Evo Lap Analysis</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
  :root { --bg: #0d0d0f; --panel: #16181d; --border: #2a2d36; --text: #e0e2ea; --muted: #6b7280; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --orange: #f97316; --yellow: #eab308; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: var(--panel); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 600; letter-spacing: 0.02em; }
  header .sub { color: var(--muted); font-size: 12px; }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
  .stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 130px; }
  .stat .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .stat .value { font-size: 22px; font-weight: 700; margin-top: 2px; }
  .notice { display: none; border: 1px solid rgba(249,115,22,0.55); background: rgba(249,115,22,0.10); border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; color: #fed7aa; line-height: 1.45; }
  .notice strong { color: #ffedd5; }
  .notice ul { margin: 8px 0 0 18px; }
  .lap-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
  .lap-btn { background: var(--border); border: 1px solid transparent; border-radius: 6px; padding: 5px 13px; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--muted); transition: all 0.15s; }
  .lap-btn.active { border-color: currentColor; color: var(--text); }
  .lap-btn:hover { background: #2a2d3a; }
  canvas { max-width: 100%; }
  .track-wrap { position: relative; }
  #track-canvas { border-radius: 6px; cursor: crosshair; }
  #map-tooltip { position: absolute; background: rgba(13,15,20,0.95); border: 1px solid #3a3d4a; color: #e0e2ea; padding: 4px 10px; border-radius: 5px; font-size: 12px; font-weight: 600; pointer-events: none; display: none; white-space: nowrap; z-index: 10; }
  .corner-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .corner-table th { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .corner-table td { padding: 6px 10px; border-bottom: 1px solid #1e2028; }
  .corner-table tr:hover td { background: #1e2028; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .pill { display: inline-block; padding: 2px 7px; border-radius: 99px; font-size: 11px; background: var(--border); }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .section-title { font-size: 15px; font-weight: 700; margin: 20px 0 10px; padding-left: 2px; }
  select { background: var(--border); border: 1px solid #3a3d4a; border-radius: 6px; padding: 5px 10px; color: var(--text); font-size: 12px; cursor: pointer; }
</style>
</head>
<body>
<header>
  <div>
    <h1>AC Evo &mdash; Lap Telemetry</h1>
    <div class="sub" id="session-info">Loading&hellip;</div>
  </div>
</header>
<div class="container">
  <!-- Summary stats -->
  <div class="stat-row" id="stats-row"></div>
  <div class="notice" id="analysis-notice"></div>

  <!-- Track map + speed chart -->
  <div class="grid-2">
    <div class="card">
      <h2>Track Map</h2>
      <div style="margin-bottom:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <span style="font-size:12px; color:var(--muted)">Color by:</span>
        <select id="map-color-mode" onchange="drawTrackMap()">
          <option value="speed">Speed</option>
          <option value="brake">Brake</option>
          <option value="gas">Throttle</option>
        </select>
        <select id="map-lap-select" onchange="drawTrackMap()"></select>
      </div>
      <div class="track-wrap">
        <canvas id="track-canvas"></canvas>
        <div id="map-tooltip"></div>
      </div>
      <div style="margin-top:8px; display:flex; gap:6px; align-items:center; font-size:11px; color:var(--muted)">
        <span>Low</span>
        <canvas id="colorbar" width="200" height="14" style="border-radius:3px"></canvas>
        <span>High</span>
        <span id="colorbar-label" style="margin-left:8px"></span>
      </div>
    </div>
    <div class="card">
      <h2>Speed Trace</h2>
      <div class="lap-filters" id="speed-lap-filters"></div>
      <canvas id="speed-chart" height="260"></canvas>
    </div>
  </div>

  <!-- Corner speed -->
  <div class="section-title">Corner Speed Comparison</div>
  <div class="grid-2">
    <div class="card">
      <h2>Apex Speed by Corner</h2>
      <canvas id="corner-chart" height="280"></canvas>
    </div>
    <div class="card" style="overflow-x:auto">
      <h2>Corner Speed Table (km/h)</h2>
      <table class="corner-table" id="corner-table"></table>
    </div>
  </div>

  <!-- Input channels -->
  <div class="section-title">Input Channels</div>
  <div class="card" style="margin-bottom:16px">
    <h2>Brake &bull; Throttle &bull; Gear</h2>
    <div class="lap-filters" id="inputs-lap-filters"></div>
    <canvas id="inputs-chart" height="200"></canvas>
  </div>

  <!-- Dynamics -->
  <div class="section-title">Dynamics</div>
  <div class="card" style="margin-bottom:16px">
    <h2>Steering &bull; G &bull; Yaw &bull; Brake Temp</h2>
    <div style="margin-bottom:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
      <span style="font-size:12px; color:var(--muted)">Metric:</span>
      <select id="dynamics-mode" onchange="buildDynamicsChart()">
        <option value="steer">Steer (deg)</option>
        <option value="yaw_rate">Yaw rate</option>
        <option value="lat_g">Lateral G</option>
        <option value="long_g">Longitudinal G</option>
        <option value="brake_temp_front">Brake temp front (avg)</option>
        <option value="brake_temp_rear">Brake temp rear (avg)</option>
      </select>
    </div>
    <div class="lap-filters" id="dynamics-lap-filters"></div>
    <canvas id="dynamics-chart" height="220"></canvas>
  </div>
</div>

<script>
const DATA = __DATA__;
const LAP_COLORS = ['#3b82f6','#22c55e','#f97316','#a855f7','#eab308','#ec4899','#06b6d4'];
function lapColor(n) { return LAP_COLORS[(n - 1) % LAP_COLORS.length]; }
function speedColor(frac) {
  const stops = [[0,[30,120,255]],[0.25,[0,210,220]],[0.5,[0,200,80]],[0.75,[240,200,0]],[1,[255,40,40]]];
  frac = Math.max(0, Math.min(1, frac));
  for (let i = 1; i < stops.length; i++) {
    if (frac <= stops[i][0]) {
      const t = (frac - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
      const c0 = stops[i-1][1], c1 = stops[i][1];
      return `rgb(${Math.round(c0[0]+t*(c1[0]-c0[0]))},${Math.round(c0[1]+t*(c1[1]-c0[1]))},${Math.round(c0[2]+t*(c1[2]-c0[2]))})`;
    }
  }
  return 'rgb(255,40,40)';
}
function brakeColor(v) { const r = Math.round(60 + v * 195); return `rgb(${r},${Math.round(30*(1-v))},${Math.round(30*(1-v))})`; }
function gasColor(v) { const g = Math.round(60 + v * 175); return `rgb(${Math.round(30*(1-v))},${g},${Math.round(30*(1-v))})`; }

const activeLaps = new Set(DATA.laps.map(l => l.lap_num));

function syncFilterButtons() {
  document.querySelectorAll('.lap-btn').forEach(btn => {
    const n = parseInt(btn.dataset.lap);
    btn.classList.toggle('active', activeLaps.has(n));
  });
}

function rebuildAll() { syncFilterButtons(); buildSpeedChart(); buildInputsChart(); buildDynamicsChart(); }

function makeLapFilters(containerId, onChange) {
  const el = document.getElementById(containerId);
  el.innerHTML = '<span style="font-size:12px;color:var(--muted);margin-right:4px">Laps:</span>';
  DATA.laps.forEach(lap => {
    const btn = document.createElement('button');
    btn.className = 'lap-btn active';
    btn.style.color = lapColor(lap.lap_num);
    btn.textContent = `L${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} - ${lap.lap_time_str}`;
    btn.dataset.lap = lap.lap_num;
    btn.addEventListener('click', () => {
      if (activeLaps.has(lap.lap_num)) activeLaps.delete(lap.lap_num);
      else activeLaps.add(lap.lap_num);
      onChange();
    });
    el.appendChild(btn);
  });
}

function renderStats() {
  const row = document.getElementById('stats-row');
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num) || DATA.laps.reduce((best, lap) => lap.lap_time_s < best.lap_time_s ? lap : best, DATA.laps[0]);
  const maxSpd = Math.max(...DATA.laps.map(l => l.max_speed));
  const stats = [
    { label: 'Laps', value: DATA.laps.length },
    { label: 'Best Lap', value: bestLap.lap_time_str },
    { label: 'Top Speed', value: maxSpd.toFixed(0) + ' km/h' },
    { label: 'Corners / Lap', value: DATA.ref_corners.length },
  ];
  row.innerHTML = stats.map(s => `<div class="stat"><div class="label">${s.label}</div><div class="value">${s.value}</div></div>`).join('');
  const notice = document.getElementById('analysis-notice');
  const notes = DATA.analysis_notes || [];
  if (notice && (DATA.analysis_mode !== 'full' || notes.length)) {
    const escaped = notes.map(note => String(note).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])));
    const title = DATA.analysis_mode !== 'full'
      ? '<strong>Diagnostic mode:</strong> Detailed coaching is suppressed because this capture is not fully trustworthy.'
      : '<strong>Analysis notes:</strong>';
    notice.innerHTML = title + (escaped.length ? `<ul>${escaped.map(note => `<li>${note}</li>`).join('')}</ul>` : '');
    notice.style.display = 'block';
  } else if (notice) {
    notice.style.display = 'none';
  }
  const prefix = DATA.track_label || DATA.track_name || '';
  document.getElementById('session-info').textContent = `${prefix ? prefix + '  |  ' : ''}${DATA.laps.length} laps detected  -  best ${bestLap.lap_time_str}`;
}

/* ── Track map ─────────────────────────────────────────────── */
function drawTrackMap() {
  const canvas = document.getElementById('track-canvas');
  const mode = document.getElementById('map-color-mode').value;
  const sel = document.getElementById('map-lap-select');
  const lapNum = parseInt(sel.value);
  const lap = DATA.laps.find(l => l.lap_num === lapNum);
  if (!lap) return;
  const pts = lap.track;
  const xs = pts.map(p => p.x), zs = pts.map(p => p.z);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const wrap = canvas.parentElement;
  const size = Math.min(wrap.clientWidth, 420);
  canvas.width = size; canvas.height = size;
  const pad = 24, sc = Math.min((size - 2*pad) / (maxX - minX || 1), (size - 2*pad) / (maxZ - minZ || 1));
  const offX = pad + ((size - 2*pad) - (maxX-minX)*sc) / 2, offZ = pad + ((size - 2*pad) - (maxZ-minZ)*sc) / 2;
  const cx = x => offX + (x - minX) * sc, cz = z => offZ + (z - minZ) * sc;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size); ctx.fillStyle = '#0d0f14'; ctx.fillRect(0, 0, size, size);
  let vals = mode === 'speed' ? pts.map(p => p.speed) : mode === 'brake' ? pts.map(p => p.brake) : pts.map(p => p.gas);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  ctx.beginPath(); ctx.strokeStyle = '#1e2028'; ctx.lineWidth = 12; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  pts.forEach((p, i) => { i === 0 ? ctx.moveTo(cx(p.x), cz(p.z)) : ctx.lineTo(cx(p.x), cz(p.z)); });
  ctx.stroke();
  for (let i = 1; i < pts.length; i++) {
    const p0 = pts[i - 1], p1 = pts[i];
    const frac = (vals[i] - minV) / (maxV - minV || 1);
    ctx.beginPath(); ctx.lineWidth = 6;
    ctx.strokeStyle = mode === 'speed' ? speedColor(frac) : mode === 'brake' ? brakeColor(vals[i]) : gasColor(vals[i]);
    ctx.moveTo(cx(p0.x), cz(p0.z)); ctx.lineTo(cx(p1.x), cz(p1.z)); ctx.stroke();
  }
  window._cornerHits = [];
  lap.corners.forEach((c, idx) => {
    const p = pts.find(pt => pt.frame === c.apex_frame) || pts[0];
    const px = cx(p.x), pz = cz(p.z);
    ctx.beginPath(); ctx.arc(px, pz, 6, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill();
    ctx.fillStyle = '#000'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(idx + 1, px, pz);
    window._cornerHits.push({ px, pz, num: idx + 1, name: c.name || null });
  });
  const start = pts[0];
  ctx.beginPath(); ctx.arc(cx(start.x), cz(start.z), 8, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill();
  ctx.fillStyle = '#111'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('S', cx(start.x), cz(start.z));
  drawColorbar(mode, minV, maxV);
}

function drawColorbar(mode, minV, maxV) {
  const cb = document.getElementById('colorbar');
  const ctx = cb.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 200, 0);
  if (mode === 'speed') { for (let i = 0; i <= 10; i++) { grad.addColorStop(i/10, speedColor(i/10)); } }
  else if (mode === 'brake') { grad.addColorStop(0, brakeColor(0)); grad.addColorStop(1, brakeColor(1)); }
  else { grad.addColorStop(0, gasColor(0)); grad.addColorStop(1, gasColor(1)); }
  ctx.fillStyle = grad; ctx.fillRect(0, 0, 200, 14);
  document.getElementById('colorbar-label').textContent = mode === 'speed' ? `${minV.toFixed(0)}\u2013${maxV.toFixed(0)} km/h` : '0\u2013100%';
}

/* ── Speed chart with corner shading ──────────────────────── */
let speedChart = null;
function buildSpeedChart() {
  const ctx = document.getElementById('speed-chart').getContext('2d');
  if (speedChart) speedChart.destroy();
  const datasets = DATA.laps.filter(l => activeLaps.has(l.lap_num)).map(lap => ({
    label: `Lap ${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} (${lap.lap_time_str})`,
    data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.speed })),
    borderColor: lapColor(lap.lap_num), backgroundColor: 'transparent', borderWidth: 1.8, pointRadius: 0, tension: 0.3,
  }));
  const annotations = {};
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num) || DATA.laps[0];
  if (bestLap) {
    bestLap.corners.forEach(c => {
      const s = (c.start_frame - bestLap.start_frame) / Math.max(bestLap.track.length - 1, 1) * 100;
      const e = (c.end_frame - bestLap.start_frame) / Math.max(bestLap.track.length - 1, 1) * 100;
      annotations['corner' + c.id] = {
        type: 'box', xMin: s, xMax: e,
        backgroundColor: 'rgba(255,255,255,0.04)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        label: { content: c.name || ('C' + c.id), display: true, color: '#9ca3af', font: { size: 9 } },
      };
    });
  }
  speedChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index', intersect: false },
    scales: {
      x: { type: 'linear', min: 0, max: 100, title: { display: true, text: 'Lap progress (%)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { title: { display: true, text: 'Speed (km/h)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } }, annotation: { annotations } },
  }});
}

/* ── Corner charts ────────────────────────────────────────── */
let cornerChart = null;
function buildCornerChart() {
  const ctx = document.getElementById('corner-chart').getContext('2d');
  if (cornerChart) cornerChart.destroy();
  const labels = DATA.ref_corners.map(c => c.name || ('C' + c.id));
  const datasets = DATA.laps.map(lap => ({
    label: `Lap ${lap.lap_num}`,
    data: DATA.ref_corners.map(c => { const s = DATA.corner_speeds[c.id]; return s ? (s[lap.lap_num] || null) : null; }),
    backgroundColor: lapColor(lap.lap_num) + 'cc', borderColor: lapColor(lap.lap_num), borderWidth: 1, borderRadius: 4,
  }));
  cornerChart = new Chart(ctx, { type: 'bar', data: { labels, datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index' },
    scales: {
      x: { grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
      y: { title: { display: true, text: 'Apex Speed (km/h)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } },
  }});
}

function buildCornerTable() {
  const table = document.getElementById('corner-table');
  const lapNums = DATA.laps.map(l => l.lap_num);
  let html = '<thead><tr><th>Corner</th>' + lapNums.map(n => `<th>Lap ${n}</th>`).join('') + '<th>\u0394 Best\u2013Worst</th></tr></thead><tbody>';
  DATA.ref_corners.forEach(c => {
    const speeds = DATA.corner_data?.[c.id] || {};
    const vals = lapNums.map(n => speeds[n]?.apex).filter(v => v !== undefined);
    const best = vals.length ? Math.max(...vals) : null;
    const worst = vals.length ? Math.min(...vals) : null;
    const delta = best !== null ? (best - worst).toFixed(1) : '\u2014';
    html += `<tr><td><span class="badge" style="background:var(--border)">${c.name || ('C' + c.id)}</span></td>`;
    lapNums.forEach(n => {
      const v = speeds[n]?.apex;
      if (v === undefined) { html += '<td style="color:var(--muted)">-</td>'; return; }
      const isB = v === best, isW = v === worst;
      const color = isB ? 'var(--green)' : isW ? 'var(--red)' : 'var(--text)';
      html += `<td style="color:${color};font-weight:${isB||isW?700:400}">${v.toFixed(1)}</td>`;
    });
    html += `<td style="color:var(--orange)">${delta}</td></tr>`;
  });
  html += '</tbody>'; table.innerHTML = html;
}

/* ── Inputs chart (Brake / Throttle / Gear) ───────────────── */
let inputsChart = null;
function buildInputsChart() {
  const ctx = document.getElementById('inputs-chart').getContext('2d');
  if (inputsChart) inputsChart.destroy();
  const active = DATA.laps.filter(l => activeLaps.has(l.lap_num));
  if (!active.length) return;
  const datasets = [];
  active.forEach(lap => {
    const color = lapColor(lap.lap_num);
    datasets.push({
      label: `L${lap.lap_num} Brake`,
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.brake * 100 })),
      borderColor: color, backgroundColor: 'transparent', borderWidth: 2, borderDash: [], pointRadius: 0, tension: 0.2,
    });
    datasets.push({
      label: `L${lap.lap_num} Throttle`,
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.gas * 100 })),
      borderColor: color, backgroundColor: 'transparent', borderWidth: 2, borderDash: [4, 3], pointRadius: 0, tension: 0.2,
    });
  });
  datasets.push({
    label: 'Gear \u00d7 10',
    data: active[0].track.map((pt, i) => ({ x: i / Math.max(active[0].track.length - 1, 1) * 100, y: pt.gear * 10 })),
    borderColor: '#eab308', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0, borderDash: [2, 4],
  });
  inputsChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false,
    scales: {
      x: { type: 'linear', min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12, font: { size: 10 } } } },
  }});
}

/* ── Dynamics chart (Steer / Yaw / G / Brake Temp) ────────── */
let dynamicsChart = null;

function dynValue(pt, mode) {
  if (mode === 'steer') return (pt.steer || 0) * (180 / Math.PI);
  if (mode === 'yaw_rate') return pt.yaw_rate || 0;
  if (mode === 'lat_g') return pt.acc_g_x || 0;
  if (mode === 'long_g') return pt.acc_g_z || 0;
  if (mode === 'brake_temp_front') return ((pt.brake_temp_fl || 0) + (pt.brake_temp_fr || 0)) / 2;
  if (mode === 'brake_temp_rear') return ((pt.brake_temp_rl || 0) + (pt.brake_temp_rr || 0)) / 2;
  return 0;
}
function dynLabel(mode) {
  const m = { steer: 'Steer (deg)', yaw_rate: 'Yaw rate', lat_g: 'Lateral G', long_g: 'Longitudinal G', brake_temp_front: 'Brake temp front (avg)', brake_temp_rear: 'Brake temp rear (avg)' };
  return m[mode] || mode;
}

function buildDynamicsChart() {
  const ctx = document.getElementById('dynamics-chart').getContext('2d');
  if (dynamicsChart) dynamicsChart.destroy();
  const active = DATA.laps.filter(l => activeLaps.has(l.lap_num));
  if (!active.length) return;
  const mode = document.getElementById('dynamics-mode').value;
  const datasets = active.map(lap => ({
    label: `Lap ${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} (${lap.lap_time_str})`,
    data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: dynValue(pt, mode) })),
    borderColor: lapColor(lap.lap_num), backgroundColor: 'transparent', borderWidth: 1.8, pointRadius: 0, tension: 0.2,
  }));
  dynamicsChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index', intersect: false },
    scales: {
      x: { type: 'linear', min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { title: { display: true, text: dynLabel(mode), color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } },
  }});
}

/* ── Init ──────────────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', () => {
  renderStats();
  const sel = document.getElementById('map-lap-select');
  DATA.laps.forEach(l => {
    const opt = document.createElement('option');
    opt.value = l.lap_num;
    opt.textContent = `Lap ${l.lap_num}${l.lap_num===DATA.best_lap_num?'*':''} (${l.lap_time_str})`;
    sel.appendChild(opt);
  });
  makeLapFilters('speed-lap-filters', rebuildAll);
  makeLapFilters('inputs-lap-filters', rebuildAll);
  makeLapFilters('dynamics-lap-filters', rebuildAll);
  drawTrackMap(); buildSpeedChart(); buildCornerChart(); buildCornerTable(); buildInputsChart(); buildDynamicsChart();
  const mapCanvas = document.getElementById('track-canvas');
  const mapTip = document.getElementById('map-tooltip');
  mapCanvas.addEventListener('mousemove', e => {
    const rect = mapCanvas.getBoundingClientRect();
    const scaleX = mapCanvas.width / rect.width, scaleY = mapCanvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX, my = (e.clientY - rect.top) * scaleY;
    const hits = window._cornerHits || [];
    let found = null;
    for (const h of hits) { if (Math.hypot(mx - h.px, my - h.pz) < 10) { found = h; break; } }
    if (found) {
      mapTip.textContent = found.name ? `C${found.num}: ${found.name}` : `Corner ${found.num}`;
      const wrapRect = mapCanvas.parentElement.getBoundingClientRect();
      mapTip.style.left = (e.clientX - wrapRect.left + 14) + 'px';
      mapTip.style.top  = (e.clientY - wrapRect.top  - 10) + 'px';
      mapTip.style.display = 'block';
    } else { mapTip.style.display = 'none'; }
  });
  mapCanvas.addEventListener('mouseleave', () => { mapTip.style.display = 'none'; });
});
</script>
</body>
</html>""".replace("__DATA__", data_json)

    async def _generate_ai_prompt(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate detailed AI coaching prompt with per-corner analysis and setup recommendations."""
        prefix = output_prefix or datetime.now().strftime("%m-%d-%H-%M-%S")
        ai_prompt_path = os.path.join(self._output_dir, f"telemetry_{prefix}_ai_prompt.txt")

        os.makedirs(self._output_dir, exist_ok=True)

        laps = data.get("laps", [])
        if not laps:
            with open(ai_prompt_path, "w", encoding="utf-8") as f:
                f.write("No telemetry data available for coaching.\n")
            return ai_prompt_path

        hz = data.get("hz", 10.0)
        best_lap = min(laps, key=lambda l: l["lap_time_s"])
        worst_lap = max(laps, key=lambda l: l["lap_time_s"])
        time_diff = worst_lap["lap_time_s"] - best_lap["lap_time_s"]
        track_label = data.get("track_label") or data.get("track_name") or "Unknown Track"
        ref_corners = data.get("ref_corners", [])
        corner_speeds = data.get("corner_speeds", {})
        corner_data_map = data.get("corner_data", {})
        analysis_mode = data.get("analysis_mode", "diagnostic")
        analysis_confidence = data.get("analysis_confidence", "low")
        analysis_notes = data.get("analysis_notes", [])
        authoritative_progress_ratio = float(data.get("authoritative_progress_ratio", 0.0) or 0.0)
        plausible_frame_ratio = float(data.get("plausible_frame_ratio", 0.0) or 0.0)
        reference_lap_num = data.get("reference_lap_num", best_lap["lap_num"])
        comparison_lap_num = data.get("comparison_lap_num", best_lap["lap_num"])
        reference_lap = next((lap for lap in laps if lap["lap_num"] == reference_lap_num), best_lap)
        comparison_lap = next((lap for lap in laps if lap["lap_num"] == comparison_lap_num), best_lap)

        # ── Car name from shared session data
        car_model: str = data.get("car") or "Unknown Car"

        lines: List[str] = []

        if analysis_mode != "full" or not ref_corners:
            lines.append("Telemetry coaching is running in DIAGNOSTIC mode.")
            lines.append("")
            lines.append(f"Track: {track_label}")
            lines.append(f"Car: {car_model}")
            lines.append(f"Laps available: {len(laps)}")
            lines.append(f"Analysis confidence: {analysis_confidence}")
            lines.append(f"Authoritative progress coverage: {authoritative_progress_ratio:.0%}")
            lines.append(f"Plausible physics coverage: {plausible_frame_ratio:.0%}")
            lines.append("")
            lines.append("Detailed corner coaching has been suppressed because the lap alignment is not trustworthy enough.")
            if analysis_notes:
                lines.append("")
                lines.append("Reasons:")
                for note in analysis_notes:
                    lines.append(f"- {note}")
            lines.append("")
            lines.append("Use the session only for diagnostics until graphics-based progress coverage is reliable.")

            with open(ai_prompt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return ai_prompt_path

        # ── Car name from shared session data
        car_model: str = data.get("car") or "Unknown Car"

        # ── Preamble / persona
        lines.append(
            f"You are an expert Assetto Corsa Evo race engineer. "
            f"Analyse telemetry for the {car_model} at {track_label}."
        )
        lines.append("Before giving any setup advice you MUST do both of the following searches:")
        lines.append(
            f"  1. Search: \"{car_model} Assetto Corsa Evo setup\" — identify every setup "
            f"parameter that is actually exposed for this car in the game (suspension, aero, "
            f"differential, tyre pressures, brake bias, etc.). "
            f"Only recommend changes to parameters that exist for this specific car."
        )
        lines.append(
            f"  2. Search: \"{track_label} setup guide Assetto Corsa Evo\" and "
            f"\"{track_label} racing line key corners\" — incorporate track-specific knowledge: "
            f"typical brake points, high-speed sections, kerb usage, traction-limited exits."
        )
        lines.append(
            "Your entire response must be CONCISE. "
            "Use bullet points. No padding, no repetition. "
            "Every claim must reference a specific number from the telemetry data below."
        )
        lines.append("")

        # ── Session context
        lines.append("SESSION CONTEXT:")
        lines.append(f"- Track:          {track_label}")
        lines.append(f"- Car:            {car_model}")
        lines.append(f"- Analysis mode:  {analysis_mode}")
        lines.append(f"- Confidence:     {analysis_confidence}")
        lines.append(f"- Reference lap:  #{reference_lap_num}")
        lines.append(f"- Compare lap:    #{comparison_lap_num}")
        if analysis_notes:
            lines.append("")
            lines.append("ANALYSIS NOTES:")
            for note in analysis_notes:
                lines.append(f"- {note}")
        lines.append("")

        # ── Search context hints for the LLM
        lines.append("SEARCH CONTEXT (perform before responding):")
        lines.append(f"  Car:   {car_model}")
        lines.append(f"  Track: {track_label}")
        lines.append(
            f"  Required searches:"
            f" (a) \"{car_model} Assetto Corsa Evo setup parameters\""
            f" (b) \"{track_label} AC Evo setup guide\""
            f" (c) \"{track_label} key corners racing line\""
        )
        lines.append(
            "  Cross-reference search results with the telemetry numbers below. "
            "Do not recommend setup parameters that do not exist for this car."
        )
        lines.append("")

        # ── Session overview
        lines.append("SESSION OVERVIEW:")
        lines.append(f"- Total laps analysed: {len(laps)}")
        lines.append(f"- Best lap:   #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
        lines.append(f"- Worst lap:  #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
        lines.append(f"- Delta best/worst: {time_diff:.2f}s")
        lines.append(f"- Top speed: {max(l['max_speed'] for l in laps):.1f} km/h")
        lines.append(f"- Authoritative progress coverage: {authoritative_progress_ratio:.0%}")
        lines.append(f"- Plausible physics coverage:      {plausible_frame_ratio:.0%}")
        
        # ── Fuel consumption summary (from telemetry)
        laps_with_fuel = [lap for lap in laps if lap.get('fuel_used') is not None]
        if laps_with_fuel:
            fuel_values = [lap['fuel_used'] for lap in laps_with_fuel]
            avg_fuel = sum(fuel_values) / len(fuel_values)
            total_fuel = sum(fuel_values)
            lines.append(f"- Fuel per lap (avg): {avg_fuel:.3f}L")
            lines.append(f"- Total fuel used: {total_fuel:.3f}L ({len(laps_with_fuel)} laps)")
        
        lines.append("")

        # ── Outlier detection
        outliers: List[tuple] = []
        if len(laps) >= 2 and laps[0]["lap_num"] == 1:
            lap1_time = laps[0]["lap_time_s"]
            lap2_time = laps[1]["lap_time_s"]
            if lap1_time > lap2_time * 1.03:
                outliers.append((1, "First lap - likely cold tires or traffic"))

        for lap in laps:
            if lap["lap_num"] == best_lap["lap_num"]:
                continue
            delta_pct = (lap["lap_time_s"] - best_lap["lap_time_s"]) / best_lap["lap_time_s"]
            if delta_pct > 0.05:
                outliers.append((lap["lap_num"], f"{delta_pct * 100:.1f}% slower than best lap"))

        if outliers:
            lines.append("OUTLIER LAPS (may not represent true performance):")
            for lap_num, reason in outliers:
                lines.append(f"  Lap {lap_num}: {reason}")
            lines.append("  -> When analyzing, focus on the representative laps, not outliers")
            lines.append("")

        # ── Lap-by-lap summary
        lines.append("LAP-BY-LAP SUMMARY:")
        for lap in laps:
            marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else \
                     " <- WORST" if lap["lap_num"] == worst_lap["lap_num"] else ""
            fuel_str = f"  fuel {lap['fuel_used']:.3f}L" if lap.get('fuel_used') is not None else ""
            lines.append(
                f"  Lap {lap['lap_num']}: {lap['lap_time_str']}  "
                f"max {lap['max_speed']:.1f} km/h  "
                f"avg {lap['avg_speed']:.1f} km/h{fuel_str}{marker}"
            )
        lines.append("")

        # ── Electronics / aids summary
        elec_per_lap = analyze_electronics_per_lap(laps)
        has_elec_data = any(
            e["tc_level"] is not None or e["abs_level"] is not None
            for e in elec_per_lap
        )
        if has_elec_data:
            lines.append("CAR ELECTRONICS / AIDS (start-of-lap SHM snapshot):")
            lines.append("(TC/ABS: 0=off, higher=more aggressive; EngMap=engine power mode;")
            lines.append(" DiffP=differential lock under power; DiffC=differential lock on coast)")
            lines.append("")
            adjustments: List[str] = []
            for e in elec_per_lap:
                tc_str = str(e["tc_level"]) if e["tc_level"] is not None else "?"
                abs_str = str(e["abs_level"]) if e["abs_level"] is not None else "?"
                map_str = str(e["engine_map"]) if e["engine_map"] is not None else "?"
                dp_str = str(e["diff_power"]) if e["diff_power"] is not None else "?"
                dc_str = str(e["diff_coast"]) if e["diff_coast"] is not None else "?"
                lines.append(
                    f"  Lap {e['lap_num']}: TC={tc_str}  ABS={abs_str}  "
                    f"EngMap={map_str}  DiffP={dp_str}  DiffC={dc_str}"
                )
                changes: List[str] = []
                if e["tc_changed"]:
                    changes.append("TC")
                if e["abs_changed"]:
                    changes.append("ABS")
                if e["engine_map_changed"]:
                    changes.append("EngMap")
                if changes:
                    adjustments.append(f"Lap {e['lap_num']}: {', '.join(changes)} adjusted mid-lap")
            if adjustments:
                lines.append("")
                lines.append("  Mid-lap adjustments detected:")
                for adj in adjustments:
                    lines.append(f"  -> {adj}")
            lines.append("")

        # ── Corner-by-corner analysis
        lap_corner_map: Dict[int, Dict[int, Dict]] = {
            lap["lap_num"]: {c["id"]: c for c in lap["corners"]}
            for lap in laps
        }

        lines.append("CORNER-BY-CORNER ANALYSIS:")
        lines.append("(entry/apex/exit speeds in km/h; comparison model = reference lap vs comparison lap)")
        lines.append("")

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"
            speeds = corner_speeds.get(cid, {})
            if not speeds:
                continue

            apex_vals = list(speeds.values())
            best_apex = max(apex_vals)
            worst_apex = min(apex_vals)
            variation = best_apex - worst_apex

            corners_for_lap = []
            for lap in laps:
                corner = lap_corner_map[lap["lap_num"]].get(cid)
                if corner:
                    corners_for_lap.append((lap["lap_num"], corner))

            if not corners_for_lap:
                continue

            reference_corner = lap_corner_map.get(reference_lap_num, {}).get(cid)
            comparison_corner = lap_corner_map.get(comparison_lap_num, {}).get(cid)
            if not reference_corner or not comparison_corner:
                continue

            best_apex_lap_num, _ = max(corners_for_lap, key=lambda item: item[1]["apex_speed"])
            worst_apex_lap_num, _ = min(corners_for_lap, key=lambda item: item[1]["apex_speed"])

            lines.append(f"--- {name} (Corner {cid}) ---")
            lines.append(f"  Apex speed range: {variation:.1f} km/h  {variation_label(variation)}")

            lap_speed_strs = [f"Lap {ln}: {spd:.1f}" for ln, spd in sorted(speeds.items())]
            lines.append(f"  Apex speeds:  {',  '.join(lap_speed_strs)}")
            lines.append(f"  Highest apex: {best_apex:.1f} km/h (Lap {best_apex_lap_num})")
            lines.append(f"  Lowest apex:  {worst_apex:.1f} km/h (Lap {worst_apex_lap_num})")

            entry_delta = comparison_corner["entry_speed"] - reference_corner["entry_speed"]
            apex_delta = comparison_corner["apex_speed"] - reference_corner["apex_speed"]
            exit_delta = comparison_corner["exit_speed"] - reference_corner["exit_speed"]

            lines.append(
                f"  Reference lap: Lap {reference_lap_num}  "
                f"{corner_segment_time(reference_corner, hz):.2f}s"
            )
            lines.append(
                f"  Compare lap:   Lap {comparison_lap_num}  "
                f"{corner_segment_time(comparison_corner, hz):.2f}s"
            )
            lines.append(
                f"  Entry  -- Lap {reference_lap_num}: {reference_corner['entry_speed']:.1f}  |  "
                f"Lap {comparison_lap_num}: {comparison_corner['entry_speed']:.1f}  |  "
                f"D {entry_delta:+.1f} km/h"
            )
            lines.append(
                f"  Apex   -- Lap {reference_lap_num}: {reference_corner['apex_speed']:.1f}  |  "
                f"Lap {comparison_lap_num}: {comparison_corner['apex_speed']:.1f}  |  "
                f"D {apex_delta:+.1f} km/h"
            )
            lines.append(
                f"  Exit   -- Lap {reference_lap_num}: {reference_corner['exit_speed']:.1f}  |  "
                f"Lap {comparison_lap_num}: {comparison_corner['exit_speed']:.1f}  |  "
                f"D {exit_delta:+.1f} km/h"
            )

            seg_delta = (
                corner_segment_time(comparison_corner, hz) -
                corner_segment_time(reference_corner, hz)
            )
            lines.append(f"  Segment delta (compare - ref): {seg_delta:+.2f}s")
            lines.append(
                f"  Confidence: ref={reference_corner.get('confidence_label', 'low')}  "
                f"compare={comparison_corner.get('confidence_label', 'low')}"
            )

            issue = classify_corner_issue(entry_delta, apex_delta, exit_delta)
            lines.append(f"  Likely issue: {issue}")

            # Car state at entry/apex/exit for fastest vs slowest lap
            lines.append("  Car state (Entry | Apex | Exit):")

            fastest_entry = reference_corner.get("entry_state")
            fastest_apex_st = reference_corner.get("apex_state")
            fastest_exit = reference_corner.get("exit_state")
            if fastest_entry and fastest_apex_st and fastest_exit:
                lines.append(
                    f"    Lap {reference_lap_num} (reference): "
                    f"{format_car_state(fastest_entry)} | "
                    f"{format_car_state(fastest_apex_st)} | "
                    f"{format_car_state(fastest_exit)}"
                )
                lines.append(
                    f"    Balance hint @apex (Lap {reference_lap_num}): "
                    f"{balance_hint(fastest_apex_st)}"
                )

            slowest_entry = comparison_corner.get("entry_state")
            slowest_apex_st = comparison_corner.get("apex_state")
            slowest_exit = comparison_corner.get("exit_state")
            if slowest_entry and slowest_apex_st and slowest_exit:
                lines.append(
                    f"    Lap {comparison_lap_num} (compare): "
                    f"{format_car_state(slowest_entry)} | "
                    f"{format_car_state(slowest_apex_st)} | "
                    f"{format_car_state(slowest_exit)}"
                )
                lines.append(
                    f"    Balance hint @apex (Lap {comparison_lap_num}): "
                    f"{balance_hint(slowest_apex_st)}"
                )

            lines.append("")

        # ── Braking, turn-in, and throttle timing analysis
        lines.append("BRAKING & TIMING ANALYSIS:")
        lines.append("(brake_onset = seconds before corner entry; turn_in = seconds before entry;")
        lines.append(" gas_on = seconds after apex; trail_brake% = % of entry-to-apex with brake applied;")
        lines.append(" coast = frames near apex with neither gas nor brake; peak_brake_g = peak decel G)")
        lines.append("")

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"

            phase_data_per_lap = []
            for lap in laps:
                corner = lap_corner_map[lap["lap_num"]].get(cid)
                if not corner:
                    continue
                phases = analyze_corner_phases(
                    lap["track"], corner, lap["start_frame"], hz
                )
                if phases:
                    phase_data_per_lap.append((lap["lap_num"], phases))

            if not phase_data_per_lap:
                continue

            lines.append(f"  {name}:")
            for ln, ph in phase_data_per_lap:
                brake_str = f"{ph['brake_onset_dt']:.2f}s" if ph["brake_onset_dt"] is not None else "N/A"
                turnin_str = f"{ph['turn_in_dt']:.2f}s" if ph["turn_in_dt"] is not None else "N/A"
                gas_str = f"{ph['gas_on_dt']:.2f}s" if ph["gas_on_dt"] is not None else "N/A"
                lines.append(
                    f"    Lap {ln}: brake_onset={brake_str}  turn_in={turnin_str}  "
                    f"gas_on={gas_str}  trail_brake={ph['trail_brake_pct']:.0%}  "
                    f"coast={ph['coast_frames']}fr  peak_brake_g={ph['peak_brake_g']:.2f}"
                )

            # Compute deltas between fastest and slowest segment laps
            if len(phase_data_per_lap) >= 2:
                phase_map = dict(phase_data_per_lap)
                fast_ph = phase_map.get(reference_lap_num)
                slow_ph = phase_map.get(comparison_lap_num)

                if fast_ph and slow_ph:
                    hints = []
                    # Braking timing comparison
                    if fast_ph["brake_onset_dt"] is not None and slow_ph["brake_onset_dt"] is not None:
                        diff = slow_ph["brake_onset_dt"] - fast_ph["brake_onset_dt"]
                        if abs(diff) > 0.05:
                            if diff > 0:
                                hints.append(f"compare lap brakes {diff:.2f}s EARLIER")
                            else:
                                hints.append(f"compare lap brakes {abs(diff):.2f}s LATER")
                    # Turn-in comparison
                    if fast_ph["turn_in_dt"] is not None and slow_ph["turn_in_dt"] is not None:
                        diff = slow_ph["turn_in_dt"] - fast_ph["turn_in_dt"]
                        if abs(diff) > 0.05:
                            if diff > 0:
                                hints.append(f"compare lap turns in {diff:.2f}s EARLIER")
                            else:
                                hints.append(f"compare lap turns in {abs(diff):.2f}s LATER")
                    # Gas-on comparison
                    if fast_ph["gas_on_dt"] is not None and slow_ph["gas_on_dt"] is not None:
                        diff = slow_ph["gas_on_dt"] - fast_ph["gas_on_dt"]
                        if abs(diff) > 0.05:
                            hints.append(f"compare lap gets on gas {abs(diff):.2f}s {'LATER' if diff > 0 else 'EARLIER'}")
                    # Trail braking comparison
                    tb_diff = fast_ph["trail_brake_pct"] - slow_ph["trail_brake_pct"]
                    if abs(tb_diff) > 0.10:
                        if tb_diff > 0:
                            hints.append(f"reference lap trail brakes {tb_diff:.0%} MORE into corner")
                        else:
                            hints.append(f"compare lap trail brakes {abs(tb_diff):.0%} MORE into corner")
                    # Coasting comparison
                    if slow_ph["coast_frames"] > fast_ph["coast_frames"] + 2:
                        hints.append(f"compare lap coasts {slow_ph['coast_frames'] - fast_ph['coast_frames']} more frames near apex")
                    # Peak brake G comparison
                    g_diff = fast_ph["peak_brake_g"] - slow_ph["peak_brake_g"]
                    if abs(g_diff) > 0.15:
                        if g_diff > 0:
                            hints.append(f"reference lap brakes {g_diff:.2f}G harder")
                        else:
                            hints.append(f"compare lap brakes {abs(g_diff):.2f}G harder")

                    if hints:
                        lines.append(f"    >> Lap {reference_lap_num} vs Lap {comparison_lap_num}: {'; '.join(hints)}")

            lines.append("")

        # ── Tyre grip-degradation across the stint
        # Surfaces lap-over-lap trends in core temp, peak cornering G, slip
        # angles, and mechanical wear so the AI coach can call out tyres
        # falling off — useful for longer races where pace drops with age.
        tyre_deg = analyze_tyre_grip_degradation(laps)
        deg_per_lap = tyre_deg.get("per_lap") or []
        if deg_per_lap:
            lines.append("TYRE GRIP DEGRADATION OVER STINT:")
            lines.append("(per-lap tyre summary — watch for monotonic trends as the stint progresses;")
            lines.append(" avg_temp = avg core temp across all 4 corners; peak_lat_g & peak_slip are")
            lines.append(" computed only on cornering frames so they reflect grip-limited driving;")
            lines.append(" wear_delta = % wear consumed on this lap; end_dirty = dirt pickup at lap end)")
            lines.append("")
            for lap_num, s in deg_per_lap:
                lines.append(
                    f"  Lap {lap_num}: avg_temp={s['avg_core_temp_c']:.1f}C  "
                    f"peak_temp={s['peak_core_temp_c']:.1f}C  "
                    f"peak_lat_g={s['peak_lat_g']:.2f}  "
                    f"peak_slip={s['peak_slip_angle_deg']:.1f}deg  "
                    f"wear_delta={s['wear_delta_pct']:.2f}%  "
                    f"end_wear={s['end_wear_pct']:.2f}%  "
                    f"end_dirty={s['end_dirty_pct']:.1f}%"
                )

            trends = tyre_deg.get("trends") or {}
            if trends:
                lines.append("")
                lines.append(
                    f"  Trends across stint: core_temp={trends.get('core_temp', 'FLAT')}  "
                    f"peak_lat_g={trends.get('peak_lat_g', 'FLAT')}  "
                    f"peak_slip_angle={trends.get('peak_slip_angle', 'FLAT')}  "
                    f"wear={trends.get('wear', 'FLAT')}"
                )

            for flag in tyre_deg.get("flags") or []:
                lines.append(f"  >> {flag}")

            if len(deg_per_lap) < 3:
                lines.append(
                    "  (Need at least 3 laps to detect a stint trend; current sample is short.)"
                )

            lines.append("")

        # ── Grip utilization analysis
        lines.append("GRIP UTILIZATION ANALYSIS (friction circle):")
        lines.append("(peak_total_g = grip envelope; grip_fill% = avg/peak = how much grip is used on average;")
        lines.append(" combined_brake% = % of braking frames with lat_g > 0.3 = trail braking effectiveness)")
        lines.append("")

        # Compute session-wide peak G as the reference grip envelope
        session_peak_g = 0.0
        all_grip_data: Dict[int, List[tuple]] = {}  # cid -> [(lap_num, grip_dict)]

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"
            grip_per_lap = []

            for lap in laps:
                corner = lap_corner_map[lap["lap_num"]].get(cid)
                if not corner:
                    continue
                grip = analyze_grip_utilization(lap["track"], corner, hz)
                if grip:
                    grip_per_lap.append((lap["lap_num"], grip))
                    if grip["peak_total_g"] > session_peak_g:
                        session_peak_g = grip["peak_total_g"]

            all_grip_data[cid] = grip_per_lap

        lines.append(f"  Session peak combined G: {session_peak_g:.2f}G")
        lines.append("")

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"
            grip_per_lap = all_grip_data.get(cid, [])
            if not grip_per_lap:
                continue

            lines.append(f"  {name}:")
            for ln, g in grip_per_lap:
                # Compare to session peak to flag underutilized grip
                headroom = session_peak_g - g["peak_total_g"] if session_peak_g > 0.1 else 0
                headroom_str = f"  headroom={headroom:.2f}G" if headroom > 0.15 else ""
                lines.append(
                    f"    Lap {ln}: peak_g={g['peak_total_g']:.2f}  "
                    f"avg_g={g['avg_total_g']:.2f}  "
                    f"grip_fill={g['grip_fill_pct']:.0f}%  "
                    f"lat={g['peak_lat_g']:.2f}  long={g['peak_long_g']:.2f}  "
                    f"combined_brake={g['combined_braking_pct']:.0f}%{headroom_str}"
                )

            # Flag corners where grip is consistently low vs session peak
            avg_peak = sum(g["peak_total_g"] for _, g in grip_per_lap) / len(grip_per_lap)
            avg_fill = sum(g["grip_fill_pct"] for _, g in grip_per_lap) / len(grip_per_lap)
            avg_combined = sum(g["combined_braking_pct"] for _, g in grip_per_lap) / len(grip_per_lap)

            flags = []
            if session_peak_g > 0.5 and avg_peak < session_peak_g * 0.75:
                flags.append(f"UNDERUTILIZED: peak G only {avg_peak:.2f} vs session max {session_peak_g:.2f} -- driver has more grip available")
            if avg_fill < 55:
                flags.append(f"LOW GRIP FILL ({avg_fill:.0f}%) -- coasting or not loading tires through the corner")
            if avg_combined < 30 and any(g["peak_long_g"] > 0.5 for _, g in grip_per_lap):
                flags.append(f"LOW COMBINED BRAKING ({avg_combined:.0f}%) -- not trail braking effectively into this corner")

            for flag in flags:
                lines.append(f"    >> {flag}")
            lines.append("")

        # ── Time-loss ranking
        lines.append("TIME LOSS RANKING (worst -> best, by segment time delta):")
        ranked = []
        for spec in ref_corners:
            cid = spec["id"]
            ref_corner = lap_corner_map.get(reference_lap_num, {}).get(cid)
            cmp_corner = lap_corner_map.get(comparison_lap_num, {}).get(cid)
            if ref_corner and cmp_corner:
                delta = corner_segment_time(cmp_corner, hz) - corner_segment_time(ref_corner, hz)
                ranked.append((delta, spec.get("name") or f"Corner {cid}", cid))
        ranked.sort(reverse=True)
        for delta, name, cid in ranked:
            lines.append(f"  {name:<30} {delta:+.2f}s")
        lines.append("")

        # ── DRS/Aerodynamics analysis
        lines.append("AERODYNAMICS & DRS ANALYSIS:")
        lines.append("(drs_state = DRS flap position; drs_available = activation permitted; drs_enabled = currently active)")
        lines.append("")
        
        # Analyze DRS usage patterns
        drs_usage_per_lap = {}
        for lap in laps:
            lap_num = lap["lap_num"]
            lap_track = lap.get("track", [])
            drs_active_frames = 0
            drs_available_frames = 0
            total_frames = len(lap_track)

            for pt in lap_track:
                if pt.get("drs_enabled", False):
                    drs_active_frames += 1
                if pt.get("drs_available", False):
                    drs_available_frames += 1

            if drs_available_frames > 0:
                drs_usage_pct = (drs_active_frames / drs_available_frames) * 100
                drs_usage_per_lap[lap_num] = {
                    "active_frames": drs_active_frames,
                    "available_frames": drs_available_frames,
                    "usage_pct": drs_usage_pct
                }

                lines.append(f"  Lap {lap_num}: DRS used {drs_usage_pct:.1f}% of available time "
                           f"({drs_active_frames}/{drs_available_frames} frames)")
        
        if not drs_usage_per_lap:
            lines.append("  No DRS usage detected or DRS not available in this session")
        else:
            # Check for consistent DRS usage
            usage_values = [data["usage_pct"] for data in drs_usage_per_lap.values()]
            if len(usage_values) > 1:
                avg_usage = sum(usage_values) / len(usage_values)
                usage_variance = max(usage_values) - min(usage_values)
                if usage_variance > 20:  # Significant variation
                    lines.append(f"  >> INCONSISTENT DRS USAGE: varies by {usage_variance:.1f}% between laps")
                elif avg_usage < 50:
                    lines.append(f"  >> LOW DRS USAGE: only {avg_usage:.1f}% of available DRS zones utilized")
        
        lines.append("")

        # ── Aerodynamics setup analysis
        lines.append("AERODYNAMICS SETUP ANALYSIS:")
        lines.append("(pitch = chassis angle; ride_height = ground clearance; air_density affects downforce)")
        lines.append("")
        
        # Analyze ride height and pitch dynamics
        ride_height_data = []
        pitch_data = []
        air_density_data = []

        for lap in laps:
            lap_num = lap["lap_num"]
            lap_track = lap.get("track", [])

            # Collect ride height and pitch data
            front_heights = [pt.get("ride_height_front", 0) for pt in lap_track if pt.get("ride_height_front", 0) > 0]
            rear_heights = [pt.get("ride_height_rear", 0) for pt in lap_track if pt.get("ride_height_rear", 0) > 0]
            pitch_values = [pt.get("pitch", 0) for pt in lap_track if pt.get("pitch", 0) != 0]
            air_densities = [pt.get("air_density", 0) for pt in lap_track if pt.get("air_density", 0) > 0]

            if front_heights and rear_heights:
                avg_front = sum(front_heights) / len(front_heights)
                avg_rear = sum(rear_heights) / len(rear_heights)
                ride_height_data.append((lap_num, avg_front, avg_rear, avg_rear - avg_front))

                lines.append(f"  Lap {lap_num}: Ride Height F={avg_front:.1f}mm R={avg_rear:.1f}mm "
                           f"Rake={(avg_rear - avg_front):.1f}mm")

            if pitch_values:
                avg_pitch = sum(pitch_values) / len(pitch_values)
                pitch_deg = avg_pitch * (180.0 / math.pi)
                # Store pitch range along with avg for later sensitivity check
                min_pitch = min(pitch_values)
                max_pitch = max(pitch_values)
                pitch_range = max_pitch - min_pitch
                pitch_data.append((lap_num, avg_pitch, pitch_deg, pitch_range))

                # Show pitch range (important for aero balance)
                min_pitch_deg = min_pitch * (180.0 / math.pi)
                max_pitch_deg = max_pitch * (180.0 / math.pi)
                lines.append(f"    Pitch: avg={pitch_deg:+.2f}° range={min_pitch_deg:+.2f}° to {max_pitch_deg:+.2f}°")

            if air_densities:
                avg_density = sum(air_densities) / len(air_densities)
                air_density_data.append((lap_num, avg_density))
                lines.append(f"    Air Density: {avg_density:.3f} kg/m³")
        
        # Setup recommendations based on aero data
        if ride_height_data:
            lines.append("")
            lines.append("  AERO SETUP INSIGHTS:")
            
            # Analyze rake angle
            rakes = [data[3] for data in ride_height_data]
            avg_rake = sum(rakes) / len(rakes)
            rake_variance = max(rakes) - min(rakes)
            
            if avg_rake < 10.0:  # Less than 10mm rake
                lines.append(f"    >> LOW RAKE: {avg_rake:.1f}mm average - consider increasing rear ride height "
                           f"or lowering front for more rear downforce")
            elif avg_rake > 50.0:  # More than 50mm rake
                lines.append(f"    >> HIGH RAKE: {avg_rake:.1f}mm average - may be excessive drag, "
                           f"consider reducing rake for better top speed")
            
            if rake_variance > 15.0:  # More than 15mm variation
                lines.append(f"    >> INCONSISTENT RAKE: varies by {rake_variance:.1f}mm - "
                           f"suspension compliance issue or inconsistent ride heights")
            
            # Check for pitch sensitivity
            if pitch_data:
                # pitch_data now stores (lap_num, avg_pitch, pitch_deg, pitch_range)
                pitch_ranges = [p[3] for p in pitch_data]  # Use stored pitch_range
                avg_pitch_range = sum(pitch_ranges) / len(pitch_ranges) if pitch_ranges else 0
                avg_pitch_range_deg = avg_pitch_range * (180.0 / math.pi)

                if avg_pitch_range_deg > 2.0:  # More than 2 degrees pitch variation
                    lines.append(f"    >> HIGH PITCH SENSITIVITY: {avg_pitch_range_deg:.1f}° variation - "
                           f"consider stiffer springs or more aero balance")
        
        lines.append("")
        lines.append("")

        # ── Overall time analysis
        lines.append("OVERALL TIME ANALYSIS:")
        lines.append(f"  Best lap:  #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
        lines.append(f"  Worst lap: #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
        lines.append(f"  Delta: {time_diff:.2f}s")
        lines.append("")

        # ── Gear optimization analysis (if data available)
        gear_rpm_available = any(
            lap.get("track", [{}])[0].get("gear_rpm_window") is not None
            for lap in laps if lap.get("track")
        )
        
        if gear_rpm_available:
            lines.append("GEAR OPTIMIZATION ANALYSIS:")
            lines.append("(gear_rpm_window: 1.0 = perfect gear, <0.8 = too high gear, >1.0 = too low gear)")
            lines.append("")
            
            for spec in ref_corners:
                cid = spec["id"]
                name = spec.get("name") or f"Corner {cid}"
                
                gear_data = []
                for lap in laps:
                    corner = lap_corner_map[lap["lap_num"]].get(cid)
                    if not corner:
                        continue
                    
                    corner_track = [
                        pt for pt in lap["track"]
                        if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                    ]
                    
                    if corner_track:
                        apex_idx = len(corner_track) // 2
                        apex_pt = corner_track[apex_idx]
                        gear_window = apex_pt.get("gear_rpm_window")
                        gear = apex_pt.get("gear", 0)
                        rpm_pct = apex_pt.get("rpm_percent")
                        
                        if gear_window is not None:
                            gear_data.append((lap["lap_num"], gear, gear_window, rpm_pct))
                
                if gear_data:
                    lines.append(f"  {name}:")
                    for ln, gear, gw, rpm_pct in gear_data:
                        rpm_str = f" RPM:{rpm_pct:.0%}" if rpm_pct else ""
                        gear_hint = ""
                        if gw < 0.80:
                            gear_hint = " <- GEAR TOO HIGH, shift down"
                        elif gw < 0.90:
                            gear_hint = " <- suboptimal, consider lower gear"
                        lines.append(f"    Lap {ln}: Gear {gear}  GearOpt={gw:.2f}{rpm_str}{gear_hint}")
                    lines.append("")
        
        # ── Brake bias analysis (if data available)
        brake_bias_available = any(
            pt.get("brake_bias") is not None and pt.get("brake_bias") > 0
            for lap in laps
            for pt in lap.get("track", [])
        )
        
        if brake_bias_available:
            lines.append("BRAKE BIAS ANALYSIS:")
            lines.append("(brake_bias: ratio of front brake pressure, e.g. 0.56 = 56% front)")
            lines.append("")
            
            for spec in ref_corners:
                cid = spec["id"]
                name = spec.get("name") or f"Corner {cid}"
                
                bias_data = []
                for lap in laps:
                    corner = lap_corner_map[lap["lap_num"]].get(cid)
                    if not corner:
                        continue
                    
                    corner_track = [
                        pt for pt in lap["track"]
                        if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                    ]
                    
                    # Sample brake bias during braking phase
                    braking_pts = [pt for pt in corner_track if pt.get("brake", 0) > 0.3]
                    if braking_pts:
                        avg_bias = sum(pt.get("brake_bias", 0) or 0 for pt in braking_pts) / len(braking_pts)
                        if avg_bias > 0:
                            bias_data.append((lap["lap_num"], avg_bias))
                
                if bias_data:
                    lines.append(f"  {name}:")
                    for ln, bias in bias_data:
                        bias_hint = ""
                        if bias > 0.65:
                            bias_hint = " <- front-heavy, risk of front lock"
                        elif bias < 0.45:
                            bias_hint = " <- rear-heavy, risk of rear lock"
                        lines.append(f"    Lap {ln}: {bias:.2f} ({bias*100:.0f}% front){bias_hint}")
                    lines.append("")

        # ── Coaching request (concise)
        lines.append("=" * 60)
        lines.append("RESPOND WITH EXACTLY THESE SIX SECTIONS — BULLET POINTS ONLY:")
        lines.append("")
        lines.append(
            "1. TOP 3 TIME-LOSS CORNERS\n"
            "   For each: corner name | segment delta | one-sentence root cause."
        )
        lines.append("")
        lines.append(
            "2. DRIVING TECHNIQUE  (5 bullets max)\n"
            "   Cover: brake onset/G/trail, turn-in timing, gas pickup, coasting.\n"
            "   Cite specific numbers (e.g. 'brake onset 0.18s later at Raidillon')."
        )
        lines.append("")
        lines.append(
            "3. CONSISTENCY  (3 bullets max)\n"
            "   Highest-variation corners and whether the cause is reference-point "
            "or commitment."
        )
        lines.append("")
        lines.append(
            f"4. CAR SETUP — {car_model}\n"
            f"   Use your search results to confirm which parameters exist for this car.\n"
            f"   Then list only changes supported by the telemetry, in this format:\n"
            f"   | Parameter | Current indication from data | Suggested change | Reason |\n"
            f"   Cover: tyre pressures, alignment, ARBs, dampers, brake bias, aero.\n"
            f"   Skip any category where the data gives no clear signal."
        )
        lines.append("")
        lines.append(
            f"5. TRACK NOTES — {track_label}  (3 bullets max)\n"
            f"   Use your search results to add track-specific context the data confirms\n"
            f"   (e.g. 'Bus Stop — late apex: telemetry shows +0.4s lost here on exit')."
        )
        lines.append("")
        lines.append(
            "6. SINGLE BIGGEST GAIN\n"
            "   One sentence. Specific corner + specific action + expected time delta."
        )
        lines.append("")
        lines.append("Every bullet must be grounded in a number from the telemetry above.")
        lines.append("=" * 60)

        prompt = "\n".join(lines)

        with open(ai_prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        log_debug(Component.ANALYZER, "Generated AI prompt", path=ai_prompt_path, chars=len(prompt))
        return ai_prompt_path