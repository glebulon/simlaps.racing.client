"""Session, lap, fuel, and electronics prompt sections."""

from typing import Dict, List

from src.core.analyzer._util import _trend_direction
from src.core.analyzer.corner_detection import corner_segment_time
from src.core.analyzer.metrics import analyze_electronics_per_lap
from src.core.car_tuning_catalog import format_tuning_block

from .context import PromptContext


def build_diagnostic_sections(ctx: PromptContext) -> List[str]:
    lines: List[str] = [
        "Telemetry coaching is running in DIAGNOSTIC mode.",
        "",
        f"Track: {ctx.track_label}",
        f"Car: {ctx.car_model}",
        f"Laps available: {len(ctx.all_laps)}",
        f"Analysis confidence: {ctx.analysis_confidence}",
        f"Authoritative progress coverage: {ctx.authoritative_progress_ratio:.0%}",
        f"Plausible physics coverage: {ctx.plausible_frame_ratio:.0%}",
        "",
    ]
    if ctx.no_valid_laps:
        lines.append(
            "Detailed corner coaching has been suppressed because no valid "
            "completed lap is available."
        )
    else:
        lines.append(
            "Detailed corner coaching has been suppressed because the lap "
            "alignment is not trustworthy enough."
        )
    if ctx.analysis_notes:
        lines.extend(["", "Reasons:"])
        lines.extend(f"- {note}" for note in ctx.analysis_notes)
    lines.append("")
    if ctx.no_valid_laps:
        lines.append(
            "Use this session only for invalid-lap diagnostics; record at "
            "least one valid lap for coaching."
        )
    else:
        lines.append(
            "Use this session only for diagnostics; no coaching conclusions "
            "should be drawn from it."
        )
    return lines


def build_single_lap_sections(ctx: PromptContext) -> List[str]:
    best_lap = ctx.best_lap
    assert best_lap is not None
    lines: List[str] = [
        "COMPARATIVE COACHING UNAVAILABLE",
        "",
        f"Track: {ctx.track_label}",
        f"Car: {ctx.car_model}",
        f"Detected laps: {len(ctx.all_laps)}",
        f"Valid laps: {len(ctx.valid_laps)}",
        f"Analysis confidence: {ctx.analysis_confidence}",
        "",
        (
            "Only one coachable valid lap is available. Relative corner deltas, "
            "time-loss rankings, and theoretical-best estimates are suppressed."
        ),
        "",
        f"- Best lap:   #{best_lap['lap_num']}  {best_lap['lap_time_str']}",
        f"- Top speed:  {best_lap['max_speed']:.1f} km/h",
    ]
    if best_lap.get("fuel_used") is not None:
        lines.append(f"- Fuel used:  {best_lap['fuel_used']:.3f}L")
    if ctx.invalid_laps:
        lines.extend(["", "INVALID LAPS (diagnostic only; excluded from coaching):"])
        lines.extend(
            f"- Lap {lap['lap_num']}: {lap['lap_time_str']} [INVALID]"
            for lap in ctx.invalid_laps
        )
    if ctx.analysis_notes:
        lines.extend(["", "ANALYSIS NOTES:"])
        lines.extend(f"- {note}" for note in ctx.analysis_notes)
    return lines


def build_session_context_sections(ctx: PromptContext) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    track_label = ctx.track_label
    analysis_mode = ctx.analysis_mode
    analysis_confidence = ctx.analysis_confidence
    analysis_notes = list(ctx.analysis_notes)
    authoritative_progress_ratio = ctx.authoritative_progress_ratio
    plausible_frame_ratio = ctx.plausible_frame_ratio
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    # ── Car name from shared session data
    car_model: str = data.get("car") or "Unknown Car"
    car_known = car_model != "Unknown Car"

    # ── Preamble / persona
    lines.append(
        f"You are an expert Assetto Corsa Evo race engineer. "
        f"Analyse telemetry for the {car_model} at {track_label}."
    )
    lines.append(
        "Your entire response must be CONCISE. "
        "Use bullet points. No padding, no repetition. "
        "Every claim must reference a specific number from the telemetry data below."
    )
    tuning_block = format_tuning_block(car_model) if car_known else ""
    if car_known and tuning_block:
        lines.append(
            f"The available setup parameters for the {car_model} in AC Evo are listed "
            f"in the CAR SETUP PARAMETERS section below. "
            f"Only recommend changes from that list."
        )
    elif car_known:
        lines.append(
            f"The {car_model} setup parameters are not in the catalog. "
            f"Only recommend setup changes for parameters adjustable in the car's setup screen. "
            f"Do NOT assume brake bias is adjustable -- many road cars have fixed brake bias. "
            f"Limit advice to tyre pressures, alignment, and parameters you are certain this car exposes."
        )
    else:
        lines.append(
            "Car identity was not captured from shared memory. "
            "Do NOT guess the car or fabricate setup parameters. "
            "Do NOT recommend brake bias changes -- many cars have fixed brake bias. "
            "Limit setup advice to tyre pressures only. Focus on driving technique."
        )
    lines.append("")
    lines.append(f"NOTE: Telemetry sampled at {hz}Hz. Timing values resolve to {1/hz:.2f}s — differences below this are noise.")
    lines.append("")

    # ── Session context
    lines.append("SESSION CONTEXT:")
    lines.append(f"- Track:          {track_label}")
    if car_known:
        lines.append(f"- Car:            {car_model}")
    else:
        lines.append("- Car:            Unknown (not captured from SHM)")
    lines.append(f"- Analysis mode:  {analysis_mode}")
    lines.append(f"- Confidence:     {analysis_confidence}")
    lines.append(f"- Reference lap:  #{reference_lap_num}")
    lines.append(f"- Compare lap:    #{comparison_lap_num}")
    # Compute average track and air temps across all laps
    _all_road_temps: List[float] = []
    _all_air_temps: List[float] = []
    for lap in laps:
        for pt in lap.get("track", []):
            rt = pt.get("road_temp")
            at = pt.get("air_temp")
            if isinstance(rt, (int, float)) and rt > 0:
                _all_road_temps.append(float(rt))
            if isinstance(at, (int, float)) and at > 0:
                _all_air_temps.append(float(at))
    if _all_road_temps:
        lines.append(f"- Track temp:     {sum(_all_road_temps)/len(_all_road_temps):.0f}°C")
    if _all_air_temps:
        lines.append(f"- Air temp:       {sum(_all_air_temps)/len(_all_air_temps):.0f}°C")
    if analysis_notes:
        lines.append("")
        lines.append("ANALYSIS NOTES:")
        for note in analysis_notes:
            lines.append(f"- {note}")
    lines.append("")

    # ── Car setup parameters
    if tuning_block:
        lines.append(tuning_block)
        lines.append("")

    return lines


def build_fuel_sections(ctx: PromptContext) -> List[str]:
    all_laps = list(ctx.all_laps)
    invalid_laps = list(ctx.invalid_laps)
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    worst_lap = ctx.worst_lap
    assert best_lap is not None and worst_lap is not None
    time_diff = ctx.time_diff
    authoritative_progress_ratio = ctx.authoritative_progress_ratio
    plausible_frame_ratio = ctx.plausible_frame_ratio
    lines: List[str] = []
    # ── Session overview
    lines.append("SESSION OVERVIEW:")
    lines.append(f"- Valid laps analysed: {len(laps)} of {len(all_laps)} detected")
    lines.append(f"- Best lap:   #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
    lines.append(f"- Worst lap:  #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
    lines.append(f"- Delta best/worst: {time_diff:.2f}s")
    lines.append(f"- Top speed: {max(l['max_speed'] for l in laps):.1f} km/h")
    lines.append(f"- Authoritative progress coverage: {authoritative_progress_ratio:.0%}")
    lines.append(f"- Plausible physics coverage:      {plausible_frame_ratio:.0%}")

    # ── Lap time progression trend
    _lap_times = [lap["lap_time_s"] for lap in laps if lap.get("lap_time_s")]
    if len(_lap_times) >= 3:
        _trend_raw = _trend_direction(_lap_times, threshold=0.15)
        # Only strictly monotonic changes are called improving/degrading.
        _trend_label = {
            "FALLING": "improving",
            "RISING": "degrading",
            "FLAT": "no monotonic trend",
        }.get(_trend_raw, _trend_raw.lower())
        _time_strs = " → ".join(f"{t:.1f}" if isinstance(t, (int, float)) else str(t) for t in _lap_times[:8])
        if len(_lap_times) > 8:
            _time_strs += " …"
        lines.append(f"- Lap times: {_time_strs}  (trend: {_trend_label})")

    # ── Fuel consumption summary (from telemetry)
    laps_with_fuel = [lap for lap in laps if lap.get('fuel_used') is not None]
    if laps_with_fuel:
        fuel_values = [lap['fuel_used'] for lap in laps_with_fuel]
        avg_fuel = sum(fuel_values) / len(fuel_values)
        total_fuel = sum(fuel_values)
        lines.append(f"- Fuel per lap (avg): {avg_fuel:.3f}L")
        lines.append(f"- Total fuel used: {total_fuel:.3f}L ({len(laps_with_fuel)} laps)")
        # ── Estimate laps remaining from current fuel level
        _last_lap = laps[-1]
        _last_track = _last_lap.get("track", [])
        if _last_track:
            _last_fuel = _last_track[-1].get("fuel")
            if isinstance(_last_fuel, (int, float)) and _last_fuel > 0 and avg_fuel > 0:
                _laps_remaining = int(_last_fuel / avg_fuel)
                lines.append(f"- Est. laps remaining: ~{_laps_remaining} (current fuel {_last_fuel:.1f}L)")

    lines.append("")

    if invalid_laps:
        lines.append("INVALID LAPS (diagnostic only; excluded from coaching):")
        for lap in invalid_laps:
            lines.append(f"  Lap {lap['lap_num']}: {lap['lap_time_str']} [INVALID]")
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

    return lines


def build_lap_sections(ctx: PromptContext) -> List[str]:
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    worst_lap = ctx.worst_lap
    assert best_lap is not None and worst_lap is not None
    hz = ctx.hz
    lines: List[str] = []
    # ── Lap-by-lap summary
    lines.append("LAP-BY-LAP SUMMARY:")
    for lap in laps:
        marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else \
                 " <- WORST" if lap["lap_num"] == worst_lap["lap_num"] else ""
        valid_str = "" if lap.get("is_valid", True) else " [INVALID]"
        fuel_str = f"  fuel {lap['fuel_used']:.3f}L" if lap.get('fuel_used') is not None else ""
        lines.append(
            f"  Lap {lap['lap_num']}: {lap['lap_time_str']}  "
            f"max {lap['max_speed']:.1f} km/h  "
            f"avg {lap['avg_speed']:.1f} km/h{fuel_str}{valid_str}{marker}"
        )
    lines.append("")

    # ── Lap time decomposition: corner time vs straight time
    _decomp_lines: List[str] = []
    for lap in laps:
        _corner_total = 0.0
        for corner in lap.get("corners", []):
            _st = corner_segment_time(corner, hz)
            if _st is not None and _st > 0.0:
                _corner_total += _st
        _straight_total = lap["lap_time_s"] - _corner_total
        _corner_pct = (_corner_total / lap["lap_time_s"] * 100) if lap["lap_time_s"] > 0 else 0
        _straight_pct = 100 - _corner_pct
        _marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else ""
        _decomp_lines.append(
            f"  Lap {lap['lap_num']}: corners {_corner_total:.1f}s ({_corner_pct:.0f}%)  "
            f"straights {_straight_total:.1f}s ({_straight_pct:.0f}%){_marker}"
        )
    if _decomp_lines:
        lines.append("LAP TIME DECOMPOSITION (corner vs straight):")
        lines.extend(_decomp_lines)
        lines.append("")

    return lines


def build_electronics_sections(ctx: PromptContext) -> List[str]:
    laps = list(ctx.valid_laps)
    lines: List[str] = []
    # ── Electronics / aids summary
    elec_per_lap = analyze_electronics_per_lap(laps)
    has_elec_data = any(
        e["tc_level"] is not None or e["abs_level"] is not None
        for e in elec_per_lap
    )
    if has_elec_data:
        lines.append("CAR ELECTRONICS / AIDS (start-of-lap SHM snapshot):")
        lines.append("(TC/ABS: 0=off, higher=more aggressive; EngMap=engine power mode;")
        lines.append(" DiffP=differential lock % under power; DiffC=differential lock % on coast)")
        lines.append("")

        # ── Show modifiable parameters and their limits (from first lap)
        first_elec = elec_per_lap[0] if elec_per_lap else {}
        has_limit_data = any(
            first_elec.get(f"{param}_min") is not None or first_elec.get(f"{param}_max") is not None
            for param in ["tc_level", "abs_level", "brake_bias", "engine_map", "diff_power", "diff_coast", "perf_mode"]
        )
        has_modifiable_data = any(
            first_elec.get(f"{param}_modifiable") is not None
            for param in ["tc_level", "abs_level", "brake_bias", "engine_map", "diff_power", "diff_coast",
                         "front_bump_damper", "front_rebound_damper", "rear_bump_damper", "rear_rebound_damper",
                         "pitlimiter", "perf_mode"]
        )

        if has_modifiable_data or has_limit_data:
            lines.append("ADJUSTABLE PARAMETERS (from shared memory limits/flags):")
            if has_modifiable_data:
                modifiable_params = []
                if first_elec.get("tc_level_modifiable"):
                    modifiable_params.append("TC")
                if first_elec.get("abs_level_modifiable"):
                    modifiable_params.append("ABS")
                if first_elec.get("brake_bias_modifiable"):
                    modifiable_params.append("BrakeBias")
                if first_elec.get("engine_map_modifiable"):
                    modifiable_params.append("EngMap")
                if first_elec.get("diff_power_modifiable"):
                    modifiable_params.append("DiffP")
                if first_elec.get("diff_coast_modifiable"):
                    modifiable_params.append("DiffC")
                if first_elec.get("front_bump_damper_modifiable"):
                    modifiable_params.append("FrontBump")
                if first_elec.get("front_rebound_damper_modifiable"):
                    modifiable_params.append("FrontRebound")
                if first_elec.get("rear_bump_damper_modifiable"):
                    modifiable_params.append("RearBump")
                if first_elec.get("rear_rebound_damper_modifiable"):
                    modifiable_params.append("RearRebound")
                if first_elec.get("perf_mode_modifiable"):
                    modifiable_params.append("PerfMode")
                if modifiable_params:
                    lines.append(f"  Modifiable in-session: {', '.join(modifiable_params)}")
                else:
                    lines.append("  Modifiable in-session: None (all parameters locked)")
            else:
                lines.append("  Modifiable in-session: Unknown (limits not available)")

            if has_limit_data:
                # Show limits for key parameters
                limit_lines = []
                if first_elec.get("tc_level_min") is not None and first_elec.get("tc_level_max") is not None:
                    limit_lines.append(f"TC: {first_elec['tc_level_min']}-{first_elec['tc_level_max']}")
                if first_elec.get("abs_level_min") is not None and first_elec.get("abs_level_max") is not None:
                    limit_lines.append(f"ABS: {first_elec['abs_level_min']}-{first_elec['abs_level_max']}")
                if first_elec.get("brake_bias_min") is not None and first_elec.get("brake_bias_max") is not None:
                    limit_lines.append(f"BrakeBias: {first_elec['brake_bias_min']:.2f}-{first_elec['brake_bias_max']:.2f}")
                if first_elec.get("engine_map_min") is not None and first_elec.get("engine_map_max") is not None:
                    limit_lines.append(f"EngMap: {first_elec['engine_map_min']}-{first_elec['engine_map_max']}")
                if first_elec.get("diff_power_min") is not None and first_elec.get("diff_power_max") is not None:
                    limit_lines.append(f"DiffP: {first_elec['diff_power_min']}-{first_elec['diff_power_max']}")
                if first_elec.get("diff_coast_min") is not None and first_elec.get("diff_coast_max") is not None:
                    limit_lines.append(f"DiffC: {first_elec['diff_coast_min']}-{first_elec['diff_coast_max']}")
                if first_elec.get("front_bump_damper_min") is not None and first_elec.get("front_bump_damper_max") is not None:
                    limit_lines.append(f"FrontBump: {first_elec['front_bump_damper_min']}-{first_elec['front_bump_damper_max']}")
                if first_elec.get("front_rebound_damper_min") is not None and first_elec.get("front_rebound_damper_max") is not None:
                    limit_lines.append(f"FrontRebound: {first_elec['front_rebound_damper_min']}-{first_elec['front_rebound_damper_max']}")
                if first_elec.get("rear_bump_damper_min") is not None and first_elec.get("rear_bump_damper_max") is not None:
                    limit_lines.append(f"RearBump: {first_elec['rear_bump_damper_min']}-{first_elec['rear_bump_damper_max']}")
                if first_elec.get("rear_rebound_damper_min") is not None and first_elec.get("rear_rebound_damper_max") is not None:
                    limit_lines.append(f"RearRebound: {first_elec['rear_rebound_damper_min']}-{first_elec['rear_rebound_damper_max']}")
                if limit_lines:
                    lines.append(f"  Valid ranges: {' | '.join(limit_lines)}")
            lines.append("")

        adjustments: List[str] = []
        diff_looks_invalid = False
        for e in elec_per_lap:
            tc_str = str(e["tc_level"]) if e["tc_level"] is not None else "?"
            abs_str = str(e["abs_level"]) if e["abs_level"] is not None else "?"
            map_str = str(e["engine_map"]) if e["engine_map"] is not None else "?"
            # Validate diff values: negative lock % is physically nonsensical
            dp_raw = e["diff_power"]
            dc_raw = e["diff_coast"]
            if dp_raw is not None and dp_raw < 0:
                dp_str = "N/A"
                diff_looks_invalid = True
            else:
                dp_str = str(dp_raw) if dp_raw is not None else "?"
            if dc_raw is not None and dc_raw < 0:
                dc_str = "N/A"
                diff_looks_invalid = True
            else:
                dc_str = str(dc_raw) if dc_raw is not None else "?"
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
        if diff_looks_invalid:
            lines.append("  >> Note: DiffP/DiffC values appear uninitialized (negative). Ignore diff setup advice.")
        if adjustments:
            lines.append("")
            lines.append("  Mid-lap adjustments detected:")
            for adj in adjustments:
                lines.append(f"  -> {adj}")
        lines.append("")

    return lines


def build_session_sections(
    ctx: PromptContext,
) -> tuple[List[str], Dict[int, Dict[int, Dict]]]:
    lines: List[str] = []
    lines.extend(build_session_context_sections(ctx))
    lines.extend(build_fuel_sections(ctx))
    lines.extend(build_lap_sections(ctx))
    lines.extend(build_electronics_sections(ctx))
    lap_corner_map: Dict[int, Dict[int, Dict]] = {
        lap["lap_num"]: {corner["id"]: corner for corner in lap["corners"]}
        for lap in ctx.valid_laps
    }
    return lines, lap_corner_map
