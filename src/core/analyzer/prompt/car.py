"""Car-analysis prompt section builders."""

import math
from typing import Dict, List

from src.core.analyzer.metrics import analyze_brake_thermals, analyze_suspension
from src.core.car_tuning_catalog import format_tuning_block

from .context import PromptContext


def build_aero_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    laps = list(ctx.trusted_valid_laps)
    lines: List[str] = []
    # ── DRS/Aerodynamics analysis — gate on data presence
    _any_drs_available = any(any(pt.get("drs_available", False) for pt in lap.get("track", [])) for lap in laps)
    if _any_drs_available:
        lines.append("AERODYNAMICS & DRS ANALYSIS:")
        lines.append(
            "(drs_state = DRS flap position; drs_available = activation permitted; drs_enabled = currently active)"
        )
        lines.append("")

        # Analyze DRS usage patterns
        drs_usage_per_lap = {}
        for lap in laps:
            lap_num = lap["lap_num"]
            lap_track = lap.get("track", [])
            drs_active_frames = 0
            drs_available_frames = 0

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
                    "usage_pct": drs_usage_pct,
                }

                lines.append(
                    f"  Lap {lap_num}: DRS used {drs_usage_pct:.1f}% of available time "
                    f"({drs_active_frames}/{drs_available_frames} frames)"
                )

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

    # ── Aerodynamics setup analysis — gate on data presence
    _any_aero_data = any(
        (pt.get("ride_height_front", 0) or 0) > 1.0 or (pt.get("pitch", 0) or 0) != 0
        for lap in laps
        for pt in lap.get("track", [])
    )
    if _any_aero_data:
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

            # Collect ride height and pitch data (filter <1mm — likely uninitialized SHM in AC Evo EA)
            front_heights = [
                pt.get("ride_height_front", 0) for pt in lap_track if (pt.get("ride_height_front", 0) or 0) > 1.0
            ]
            rear_heights = [
                pt.get("ride_height_rear", 0) for pt in lap_track if (pt.get("ride_height_rear", 0) or 0) > 1.0
            ]
            pitch_values = [pt.get("pitch", 0) for pt in lap_track if (pt.get("pitch", 0) or 0) != 0]
            air_densities = [pt.get("air_density", 0) for pt in lap_track if (pt.get("air_density", 0) or 0) > 0]

            if front_heights and rear_heights:
                avg_front = sum(front_heights) / len(front_heights)
                avg_rear = sum(rear_heights) / len(rear_heights)
                ride_height_data.append((lap_num, avg_front, avg_rear, avg_rear - avg_front))

                lines.append(
                    f"  Lap {lap_num}: Ride Height F={avg_front:.1f}mm R={avg_rear:.1f}mm "
                    f"Rake={(avg_rear - avg_front):.1f}mm"
                )

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
                lines.append(
                    f"    >> LOW RAKE: {avg_rake:.1f}mm average - consider increasing rear ride height "
                    f"or lowering front for more rear downforce"
                )
            elif avg_rake > 50.0:  # More than 50mm rake
                lines.append(
                    f"    >> HIGH RAKE: {avg_rake:.1f}mm average - may be excessive drag, "
                    f"consider reducing rake for better top speed"
                )

            if rake_variance > 15.0:  # More than 15mm variation
                lines.append(
                    f"    >> INCONSISTENT RAKE: varies by {rake_variance:.1f}mm - "
                    f"suspension compliance issue or inconsistent ride heights"
                )

            # Check for pitch sensitivity
            if pitch_data:
                # pitch_data now stores (lap_num, avg_pitch, pitch_deg, pitch_range)
                pitch_ranges = [p[3] for p in pitch_data]  # Use stored pitch_range
                avg_pitch_range = sum(pitch_ranges) / len(pitch_ranges) if pitch_ranges else 0
                avg_pitch_range_deg = avg_pitch_range * (180.0 / math.pi)

                if avg_pitch_range_deg > 2.0:  # More than 2 degrees pitch variation
                    lines.append(
                        f"    >> HIGH PITCH SENSITIVITY: {avg_pitch_range_deg:.1f}° variation - "
                        f"consider stiffer springs or more aero balance"
                    )

        lines.append("")
        lines.append("")

    return lines


def build_gearing_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    laps = list(ctx.trusted_valid_laps)
    ref_corners = list(ctx.ref_corners)
    lines: List[str] = []
    # ── Gear optimization analysis (if data available)
    gear_rpm_available = any(
        lap.get("track", [{}])[0].get("gear_rpm_window") is not None for lap in laps if lap.get("track")
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
                    pt for pt in lap["track"] if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                ]

                if corner_track:
                    # Sample gear/RPM at entry (25%), apex (50%), and exit (75%)
                    _n = len(corner_track)
                    _idx25 = max(0, _n // 4)
                    _idx50 = _n // 2
                    _idx75 = min(_n - 1, (3 * _n) // 4)
                    for _label, _idx in [("entry", _idx25), ("apex", _idx50), ("exit", _idx75)]:
                        _pt = corner_track[_idx]
                        gear_window = _pt.get("gear_rpm_window")
                        gear = _pt.get("gear", 0)
                        rpm_pct = _pt.get("rpm_percent")
                        if gear_window is not None:
                            gear_data.append((lap["lap_num"], gear, gear_window, rpm_pct, _label))

            if gear_data:
                lines.append(f"  {name}:")
                for item in gear_data:
                    ln, gear, gw, rpm_pct, label = item
                    rpm_str = f" RPM:{rpm_pct:.0%}" if rpm_pct else ""
                    gear_hint = ""
                    if gw < 0.80:
                        gear_hint = " <- GEAR TOO HIGH, shift down"
                    elif gw < 0.90:
                        gear_hint = " <- suboptimal, consider lower gear"
                    lines.append(f"    Lap {ln} ({label}): Gear {gear}  GearOpt={gw:.2f}{rpm_str}{gear_hint}")
                # Flag gear changes mid-corner
                _by_lap: Dict[int, List[int]] = {}
                for ln, gear, gw, rpm_pct, label in gear_data:  # noqa: B007
                    _by_lap.setdefault(ln, []).append(gear)
                for ln, gears in _by_lap.items():
                    if len(set(gears)) > 1:
                        lines.append(
                            f"    >> Lap {ln}: Gear changes mid-corner ({' → '.join(str(g) for g in gears)}) — consider earlier downshift"  # noqa: E501
                        )
                lines.append("")

    return lines


def build_brake_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    laps = list(ctx.trusted_valid_laps)
    ref_corners = list(ctx.ref_corners)
    lines: List[str] = []
    # ── Brake bias analysis (if data available)
    brake_bias_available = any(
        pt.get("brake_bias") is not None and pt.get("brake_bias") > 0 for lap in laps for pt in lap.get("track", [])
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
                    pt for pt in lap["track"] if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                ]

                # Sample brake bias during braking phase
                braking_pts = [pt for pt in corner_track if (pt.get("brake", 0) or 0) > 0.3]
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

    # ── Brake thermal analysis (front/rear imbalance, fade, extremes)
    _brake_thermals = analyze_brake_thermals(laps)
    _bt_rows = [e for e in _brake_thermals["per_lap"] if e["front_avg"] is not None or e["rear_avg"] is not None]
    if _bt_rows:
        lines.append("BRAKE THERMAL ANALYSIS:")
        lines.append("(averages over heavy-braking frames, brake > 40%)")
        for e in _bt_rows:
            _f = f"{e['front_avg']:.0f}C" if e["front_avg"] is not None else "n/a"
            _r = f"{e['rear_avg']:.0f}C" if e["rear_avg"] is not None else "n/a"
            _p = f"  peak front {e['peak_front']:.0f}C" if e["peak_front"] is not None else ""
            lines.append(f"  Lap {e['lap_num']}: front avg {_f}  rear avg {_r}{_p}")
        if _brake_thermals["imbalance_note"]:
            lines.append(f"  >> IMBALANCE: {_brake_thermals['imbalance_note']}")
        if _brake_thermals["fade_note"]:
            lines.append(f"  >> FADE RISK: {_brake_thermals['fade_note']}")
        # ── Cross-reference brake bias with brake thermals
        if brake_bias_available and _brake_thermals["imbalance_note"]:
            _session_bias: Optional[float] = None  # noqa: F821
            for spec in ref_corners:
                cid = spec["id"]
                for lap in laps:
                    corner = lap_corner_map[lap["lap_num"]].get(cid)
                    if not corner:
                        continue
                    corner_track = [
                        pt for pt in lap["track"] if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                    ]
                    braking_pts = [pt for pt in corner_track if (pt.get("brake", 0) or 0) > 0.3]
                    if braking_pts:
                        _biases = [
                            pt.get("brake_bias", 0) or 0 for pt in braking_pts if (pt.get("brake_bias", 0) or 0) > 0
                        ]
                        if _biases:
                            _session_bias = sum(_biases) / len(_biases)
                            break
                if _session_bias is not None:
                    break
            if _session_bias is not None and _session_bias > 0.60:
                _front_avgs = [e["front_avg"] for e in _bt_rows if e["front_avg"] is not None]
                _rear_avgs = [e["rear_avg"] for e in _bt_rows if e["rear_avg"] is not None]
                if _front_avgs and _rear_avgs:
                    _fmean = sum(_front_avgs) / len(_front_avgs)
                    _rmean = sum(_rear_avgs) / len(_rear_avgs)
                    if _fmean > _rmean * 1.3:
                        lines.append(
                            f"  >> COMBINED: Front-heavy bias ({_session_bias:.2f}) with elevated front brake temps ({_fmean:.0f}C vs {_rmean:.0f}C rear) — consider reducing front bias."  # noqa: E501
                        )
        lines.append("")

    # ── Brake temperature extreme flag
    _max_brake_temps: List[float] = []
    for lap in laps:
        for pt in lap.get("track", []):
            for corner in ["fl", "fr", "rl", "rr"]:
                bt = pt.get(f"brake_temp_{corner}", 0)
                if isinstance(bt, (int, float)) and bt > 0:
                    _max_brake_temps.append(bt)
    if _max_brake_temps:
        _peak_bt = max(_max_brake_temps)
        if _peak_bt > 500:
            lines.append("")
            lines.append(f"BRAKE THERMAL WARNING: Peak brake temperature {_peak_bt:.0f}C detected.")
            lines.append("  Consider shorter braking zones or adjusting brake bias to avoid fade.")

    return lines


def build_suspension_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.trusted_valid_laps)
    lines: List[str] = []
    # ── Suspension / alignment analysis
    profile_corners = data.get("profile_corners", [])
    _suspension = analyze_suspension(laps, profile_corners, lap_corner_map=lap_corner_map)
    _has_sus = any(_suspension[k] for k in ("bottoming_notes", "travel_delta_notes", "camber_notes"))
    if _has_sus:
        lines.append("SUSPENSION & ALIGNMENT ANALYSIS:")
        for note in _suspension["bottoming_notes"]:
            lines.append(f"  >> BOTTOMING: {note}")
        for note in _suspension["travel_delta_notes"]:
            lines.append(f"  >> TRAVEL: {note}")
        for note in _suspension["camber_notes"]:
            lines.append(f"  >> CAMBER: {note}")
        lines.append("")

    lines.append("=" * 60)
    return lines


def build_car_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    lines: List[str] = []
    tuning_block = format_tuning_block(ctx.car_model)
    if tuning_block:
        lines.append(tuning_block)
        lines.append("")
    lines.extend(build_aero_sections(ctx, lap_corner_map))
    lines.extend(build_gearing_sections(ctx, lap_corner_map))
    lines.extend(build_brake_sections(ctx, lap_corner_map))
    lines.extend(build_suspension_sections(ctx, lap_corner_map))
    return lines
