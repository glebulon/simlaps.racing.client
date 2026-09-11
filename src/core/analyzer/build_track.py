"""Track building from telemetry frames — extracted from telemetry_analyzer.py."""

from typing import Any, Dict, List

from src.core.analyzer._util import (
    _optional_float,
    _safe_4,
    _sanitize_slip,
    get_graphics,
    get_physics,
)
from src.core.telemetry_capture import FrameData
from src.utils.structured_logger import Component, log_debug


def _contact_point_centroid(value: Any) -> tuple[float, float] | None:
    """Return the car-centre X/Z proxy from decoded tyre contact points."""
    if not isinstance(value, (list, tuple)):
        return None

    coordinates: list[tuple[float, float]] = []
    for point in value[:4]:
        if not isinstance(point, dict):
            continue
        point_x = _optional_float(point.get("x"))
        point_z = _optional_float(point.get("z"))
        if point_x is not None and point_z is not None:
            coordinates.append((point_x, point_z))

    if not coordinates:
        return None
    return (
        sum(point[0] for point in coordinates) / len(coordinates),
        sum(point[1] for point in coordinates) / len(coordinates),
    )


def build_track(frames: List[FrameData], hz: float = 1.0, start_idx: int = 0) -> List[Dict]:
    """Build track map from frames."""
    track = []
    x = z = 0.0

    for i in range(start_idx, len(frames)):
        f = frames[i]
        ph = get_physics(f)
        gr = get_graphics(f)
        if not ph or ph.get("is_plausible") is False:
            continue

        if i == start_idx:
            has_graphics = bool(gr)
            has_auth_progress = gr.get("has_authoritative_progress", False) if gr else False
            norm_pos = gr.get("normalized_car_position") if gr else None
            log_debug(
                Component.ANALYZER,
                "Frame graphics check",
                frame=i,
                has_graphics=has_graphics,
                has_auth_progress=has_auth_progress,
                norm_pos=norm_pos,
            )

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
            contact_centroid = _contact_point_centroid(ph.get("tyre_contact_point"))
            if contact_centroid is not None:
                x, z = contact_centroid
            else:
                # Some ACE versions omit both world and contact positions.
                # Preserve a useful relative map by integrating velocity.
                vel = ph.get("velocity")
                if isinstance(vel, dict) and hz > 0:
                    vel_x = _optional_float(vel.get("x"))
                    vel_z = _optional_float(vel.get("z"))
                    if vel_x is not None and vel_z is not None:
                        x += vel_x / hz
                        z += vel_z / hz

        graphics_norm_pos = None
        if gr.get("has_authoritative_progress"):
            graphics_norm_pos = _optional_float(gr.get("normalized_car_position"))
            if i == start_idx and graphics_norm_pos is not None:
                log_debug(Component.ANALYZER, "First frame graphics normalized_position", norm_pos=graphics_norm_pos)

        norm_pos = graphics_norm_pos
        progress_source = "graphics" if graphics_norm_pos is not None else None
        physics_quality = _optional_float(ph.get("quality_score")) or 0.0
        graphics_quality = _optional_float(gr.get("quality_score"))
        frame_quality = (
            physics_quality
            if progress_source != "graphics" or graphics_quality is None
            else min(physics_quality, graphics_quality)
        )

        tyre_core_temp = _safe_4(ph.get("tyre_core_temp", []), default=0.0)
        wheels_pressure = _safe_4(ph.get("wheels_pressure", []), default=0.0)
        wheel_slip_raw = _safe_4(ph.get("wheel_slip", []), default=0.0)
        wheel_slip = [_sanitize_slip(v) for v in wheel_slip_raw]
        wheel_load = _safe_4(ph.get("wheel_load", []), default=0.0)
        suspension_travel = _safe_4(ph.get("suspension_travel", []), default=0.0)
        camber_rad = _safe_4(ph.get("camber_rad", []), default=0.0)
        brake_temp = _safe_4(ph.get("brake_temp", []), default=0.0)
        tyre_wear = _safe_4(ph.get("tyre_wear", []), default=0.0)
        tyre_dirty_level = _safe_4(ph.get("tyre_dirty_level", []), default=0.0)

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

        track.append(
            {
                "frame": i,
                "x": x,
                "z": z,
                "speed": speed,
                "fuel": _optional_float(ph.get("fuel")),
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
                "completed_laps": gr.get("completed_laps"),
                "current_sector_index": gr.get("current_sector_index"),
                "is_valid_lap": gr.get("is_valid_lap"),
                "is_in_pit": gr.get("is_in_pit"),
                "is_in_pit_lane": gr.get("is_in_pit_lane"),
                "distance_traveled": gr.get("distance_traveled"),
                "lap_time_ms": gr.get("current_time_ms"),
                "last_lap_time_ms": gr.get("last_time_ms"),
                "best_lap_time_ms": gr.get("best_time_ms"),
                "status_name": gr.get("status_name"),
                "session_phase": gr.get("session_phase"),
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
                "tyre_wear_fl": tyre_wear[0] if len(tyre_wear) > 0 else 0.0,
                "tyre_wear_fr": tyre_wear[1] if len(tyre_wear) > 1 else 0.0,
                "tyre_wear_rl": tyre_wear[2] if len(tyre_wear) > 2 else 0.0,
                "tyre_wear_rr": tyre_wear[3] if len(tyre_wear) > 3 else 0.0,
                "tyre_dirty_fl": tyre_dirty_level[0] if len(tyre_dirty_level) > 0 else 0.0,
                "tyre_dirty_fr": tyre_dirty_level[1] if len(tyre_dirty_level) > 1 else 0.0,
                "tyre_dirty_rl": tyre_dirty_level[2] if len(tyre_dirty_level) > 2 else 0.0,
                "tyre_dirty_rr": tyre_dirty_level[3] if len(tyre_dirty_level) > 3 else 0.0,
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
                "air_density": _optional_float(ph.get("air_density")),
                "air_temp": _optional_float(ph.get("air_temp")),
                "road_temp": _optional_float(ph.get("road_temp")),
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
                "tc_level": gr.get("electronics_tc_level"),
                "abs_level": gr.get("electronics_abs_level"),
                "engine_map_level": gr.get("electronics_engine_map"),
                "diff_power_level": gr.get("electronics_diff_power"),
                "diff_coast_level": gr.get("electronics_diff_coast"),
                "front_bump_damper": gr.get("electronics_front_bump_damper"),
                "front_rebound_damper": gr.get("electronics_front_rebound_damper"),
                "rear_bump_damper": gr.get("electronics_rear_bump_damper"),
                "rear_rebound_damper": gr.get("electronics_rear_rebound_damper"),
                "electronics_perf_mode": gr.get("electronics_perf_mode"),
                "electronics_pitlimiter_on": gr.get("electronics_pitlimiter_on"),
                "tc_level_min": gr.get("electronics_tc_level_min"),
                "abs_level_min": gr.get("electronics_abs_level_min"),
                "brake_bias_min": gr.get("electronics_brake_bias_min"),
                "engine_map_min": gr.get("electronics_engine_map_min"),
                "diff_power_min": gr.get("electronics_diff_power_min"),
                "diff_coast_min": gr.get("electronics_diff_coast_min"),
                "front_bump_damper_min": gr.get("electronics_front_bump_damper_min"),
                "front_rebound_damper_min": gr.get("electronics_front_rebound_damper_min"),
                "rear_bump_damper_min": gr.get("electronics_rear_bump_damper_min"),
                "rear_rebound_damper_min": gr.get("electronics_rear_rebound_damper_min"),
                "perf_mode_min": gr.get("electronics_perf_mode_min"),
                "tc_level_max": gr.get("electronics_tc_level_max"),
                "abs_level_max": gr.get("electronics_abs_level_max"),
                "brake_bias_max": gr.get("electronics_brake_bias_max"),
                "engine_map_max": gr.get("electronics_engine_map_max"),
                "diff_power_max": gr.get("electronics_diff_power_max"),
                "diff_coast_max": gr.get("electronics_diff_coast_max"),
                "front_bump_damper_max": gr.get("electronics_front_bump_damper_max"),
                "front_rebound_damper_max": gr.get("electronics_front_rebound_damper_max"),
                "rear_bump_damper_max": gr.get("electronics_rear_bump_damper_max"),
                "rear_rebound_damper_max": gr.get("electronics_rear_rebound_damper_max"),
                "perf_mode_max": gr.get("electronics_perf_mode_max"),
                "tc_level_modifiable": gr.get("electronics_tc_level_modifiable"),
                "abs_level_modifiable": gr.get("electronics_abs_level_modifiable"),
                "brake_bias_modifiable": gr.get("electronics_brake_bias_modifiable"),
                "engine_map_modifiable": gr.get("electronics_engine_map_modifiable"),
                "diff_power_modifiable": gr.get("electronics_diff_power_modifiable"),
                "diff_coast_modifiable": gr.get("electronics_diff_coast_modifiable"),
                "pitlimiter_modifiable": gr.get("electronics_pitlimiter_modifiable"),
                "perf_mode_modifiable": gr.get("electronics_perf_mode_modifiable"),
            }
        )
    return track
