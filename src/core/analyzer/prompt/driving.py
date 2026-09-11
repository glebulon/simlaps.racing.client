"""Driving-analysis prompt section builders."""

from typing import Dict, List

from src.core.analyzer._util import (
    balance_hint,
    classify_corner_issue,
    format_car_state,
    variation_label,
)
from src.core.analyzer.corner_detection import corner_segment_time
from src.core.analyzer.metrics import (
    analyze_corner_phases,
    analyze_grip_utilization,
    analyze_steering_smoothness,
    analyze_throttle_exit,
    analyze_tyre_grip_degradation,
)

from .context import PromptContext


def build_corner_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    assert best_lap is not None
    ref_corners = list(ctx.ref_corners)
    corner_speeds = ctx.corner_speeds
    analysis_confidence = ctx.analysis_confidence
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    lines.append("CORNER-BY-CORNER ANALYSIS:")
    lines.append("(entry/apex/exit speeds in km/h; comparison model = reference lap vs comparison lap)")
    # ── Confidence-contradiction note: when session says high but all corners are low,
    # explain to the LLM that corner-level confidence is limited by sampling density.
    _all_corner_labels = []
    for spec in ref_corners:
        cid = spec["id"]
        rc = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cc = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if rc:
            _all_corner_labels.append(rc.get("confidence_label", "low"))
        if cc:
            _all_corner_labels.append(cc.get("confidence_label", "low"))
    if analysis_confidence == "high" and _all_corner_labels and all(lbl == "low" for lbl in _all_corner_labels):
        lines.append("NOTE: Session-level confidence is high (good progress/physics coverage) but")
        lines.append("      per-corner confidence is low because the track-profile windows are small.")
        lines.append("      Treat corner speed deltas as directional only, not exact comparisons.")
    lines.append("")

    # Rank corners by time lost (compare - ref segment delta) so the prompt
    # only carries full breakdowns for the biggest opportunities. SUSPECT
    # deltas (low confidence and >3s) are excluded from the ranking.
    _corner_seg_deltas: Dict[int, float] = {}
    for spec in ref_corners:
        cid = spec["id"]
        rc = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cc = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if not rc or not cc:
            continue
        seg_delta = corner_segment_time(cc, hz) - corner_segment_time(rc, hz)
        is_low_conf = (
            rc.get("confidence_label") == "low" or
            cc.get("confidence_label") == "low"
        )
        if is_low_conf and abs(seg_delta) > 3.0:
            continue
        _corner_seg_deltas[cid] = seg_delta
    _TOP_CORNER_COUNT = 5
    top_corner_ids = {
        cid for cid, _ in sorted(
            _corner_seg_deltas.items(), key=lambda item: item[1], reverse=True
        )[:_TOP_CORNER_COUNT]
    }
    # Only truncate when ranking produced data and there are more corners
    # than the cutoff; otherwise emit full breakdowns for everything.
    truncate_corners = bool(top_corner_ids) and len(ref_corners) > _TOP_CORNER_COUNT
    if truncate_corners:
        lines.append(f"NOTE: Full breakdowns below cover only the {_TOP_CORNER_COUNT} corners with the")
        lines.append("      largest time loss (compare - ref). Remaining corners are summarized in one line each.")
        lines.append("")

    compact_corner_lines: List[str] = []

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

        if truncate_corners and cid not in top_corner_ids:
            _seg = (
                corner_segment_time(comparison_corner, hz) -
                corner_segment_time(reference_corner, hz)
            )
            _apex_d = comparison_corner["apex_speed"] - reference_corner["apex_speed"]
            compact_corner_lines.append(
                f"  C{cid} {name}: apex {reference_corner['apex_speed']:.1f} vs "
                f"{comparison_corner['apex_speed']:.1f} (D {_apex_d:+.1f} km/h), "
                f"seg D {_seg:+.2f}s"
            )
            continue

        best_apex_lap_num, _ = max(corners_for_lap, key=lambda item: item[1]["apex_speed"])
        worst_apex_lap_num, _ = min(corners_for_lap, key=lambda item: item[1]["apex_speed"])

        lines.append(f"--- {name} (Corner {cid}) ---")
        lines.append(f"  Apex speed range: {variation:.1f} km/h (spread: {variation:.1f} km/h)  {variation_label(variation)}")
        if variation > 5.0:
            lines.append(f"  >> INCONSISTENT APEX SPEED: {variation:.1f} km/h spread across laps")

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
        is_low_conf = (
            reference_corner.get("confidence_label") == "low" or
            comparison_corner.get("confidence_label") == "low"
        )
        if is_low_conf and abs(seg_delta) > 3.0:
            lines.append(f"  Segment delta (compare - ref): {seg_delta:+.2f}s  >> SUSPECT")
            lines.append("    (LOW-confidence corner with >3.0s delta — do not treat as actionable time loss)")
        else:
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

        # ── Steering smoothness per corner (1.4)
        _steer_data: Dict[int, List[Dict[str, Any]]] = {}
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if corner:
                _ss = analyze_steering_smoothness(lap["track"], corner, hz)
                if _ss:
                    _steer_data.setdefault(lap["lap_num"], []).append(_ss)
        if _steer_data:
            lines.append("  Steering smoothness:")
            for ln, ss_list in sorted(_steer_data.items()):
                for _ss in ss_list:
                    _jerk_flag = "  >> JERKY STEERING" if _ss["reversals"] > 3 else ""
                    lines.append(
                        f"    Lap {ln}: reversals={_ss['reversals']}  "
                        f"peak_rate={_ss['peak_steer_rate']:.2f} rad/s  "
                        f"avg_rate={_ss['avg_steer_rate']:.2f} rad/s  "
                        f"smoothness={_ss['smoothness_score']:.2f}{_jerk_flag}"
                    )

        # ── Throttle exit profile per corner (1.5)
        _throttle_data: Dict[int, List[Dict[str, Any]]] = {}
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if corner:
                _te = analyze_throttle_exit(lap["track"], corner, hz)
                if _te:
                    _throttle_data.setdefault(lap["lap_num"], []).append(_te)
        if _throttle_data:
            lines.append("  Throttle exit profile:")
            for ln, te_list in sorted(_throttle_data.items()):
                for _te in te_list:
                    _tfull = f"{_te['time_to_full_throttle']:.2f}s" if _te["time_to_full_throttle"] is not None else "never"
                    _mod_flag = ""
                    if _te["modulation_count"] > 3:
                        _mod_flag = "  >> MODULATED THROTTLE"
                    if _te["time_to_full_throttle"] is not None and _te["time_to_full_throttle"] > 1.5:
                        _mod_flag = "  >> SLOW TO FULL THROTTLE"
                    lines.append(
                        f"    Lap {ln}: full_throttle={_tfull}  "
                        f"variance={_te['throttle_variance']:.4f}  "
                        f"modulation={_te['modulation_count']}  "
                        f"profile={_te['exit_profile']}{_mod_flag}"
                    )

        lines.append("")

    if compact_corner_lines:
        lines.append("OTHER CORNERS (compact, ref vs compare):")
        lines.extend(compact_corner_lines)
        lines.append("")
    return lines


def build_straight_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    assert best_lap is not None
    ref_corners = list(ctx.ref_corners)
    corner_speeds = ctx.corner_speeds
    analysis_confidence = ctx.analysis_confidence
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    # ── Straight/sector analysis: time between consecutive corners
    _straight_lines: List[str] = []
    for i in range(len(ref_corners) - 1):
        spec_a = ref_corners[i]
        spec_b = ref_corners[i + 1]
        cid_a = spec_a["id"]
        cid_b = spec_b["id"]
        name_a = spec_a.get("name") or f"Corner {cid_a}"
        name_b = spec_b.get("name") or f"Corner {cid_b}"
        _straight_times: Dict[int, float] = {}
        for lap in laps:
            corner_a = lap_corner_map.get(lap["lap_num"], {}).get(cid_a)
            corner_b = lap_corner_map.get(lap["lap_num"], {}).get(cid_b)
            if corner_a and corner_b:
                _t_a = corner_segment_time(corner_a, hz)
                _t_b = corner_segment_time(corner_b, hz)
                if _t_a is not None and _t_b is not None and _t_a > 0 and _t_b > 0:
                    _straight_time = _t_b - _t_a
                    if _straight_time > 0:
                        _straight_times[lap["lap_num"]] = _straight_time
        if len(_straight_times) >= 2:
            _best_straight = min(_straight_times.values())
            _worst_straight = max(_straight_times.values())
            _best_lap_num = min(_straight_times, key=lambda k: _straight_times[k])
            _straight_lines.append(
                f"  {name_a} → {name_b}: "
                f"best {_best_straight:.2f}s (Lap {_best_lap_num})  "
                f"worst {_worst_straight:.2f}s  "
                f"spread {_worst_straight - _best_straight:.2f}s"
            )
    if _straight_lines:
        lines.append("STRAIGHT/SECTOR ANALYSIS (time between consecutive corner segments):")
        lines.extend(_straight_lines)
        lines.append("")

    # ── Exit-to-entry correlation: link corner exit speed to next corner entry speed
    _correlation_lines: List[str] = []
    for i in range(len(ref_corners) - 1):
        spec_a = ref_corners[i]
        spec_b = ref_corners[i + 1]
        cid_a = spec_a["id"]
        cid_b = spec_b["id"]
        name_a = spec_a.get("name") or f"Corner {cid_a}"
        name_b = spec_b.get("name") or f"Corner {cid_b}"
        _ref_exit = None
        _ref_entry_next = None
        _cmp_exit = None
        _cmp_entry_next = None
        ref_corner_a = lap_corner_map.get(reference_lap_num, {}).get(cid_a)
        ref_corner_b = lap_corner_map.get(reference_lap_num, {}).get(cid_b)
        cmp_corner_a = lap_corner_map.get(comparison_lap_num, {}).get(cid_a)
        cmp_corner_b = lap_corner_map.get(comparison_lap_num, {}).get(cid_b)
        if ref_corner_a and ref_corner_b:
            _ref_exit = ref_corner_a.get("exit_speed")
            _ref_entry_next = ref_corner_b.get("entry_speed")
        if cmp_corner_a and cmp_corner_b:
            _cmp_exit = cmp_corner_a.get("exit_speed")
            _cmp_entry_next = cmp_corner_b.get("entry_speed")
        if _ref_exit is not None and _ref_entry_next is not None:
            _exit_d = (_cmp_exit - _ref_exit) if _cmp_exit is not None else 0.0
            _entry_d = (_cmp_entry_next - _ref_entry_next) if _cmp_entry_next is not None else 0.0
            if abs(_exit_d) > 2.0 or abs(_entry_d) > 2.0:
                _correlation_lines.append(
                    f"  {name_a} → {name_b}: "
                    f"exit D {_exit_d:+.1f} km/h → entry D {_entry_d:+.1f} km/h"
                )
    if _correlation_lines:
        lines.append("EXIT-TO-ENTRY CORRELATION (corner exit impact on next corner):")
        lines.append("(D = compare lap minus reference lap; large exit delta cascades to next entry)")
        lines.extend(_correlation_lines)
        lines.append("")

    # ── Coast time aggregation: total coasting per lap
    _coast_lines: List[str] = []
    for lap in laps:
        _total_coast_frames = 0
        for corner in lap.get("corners", []):
            _phases = analyze_corner_phases(
                lap["track"], corner, lap["start_frame"], hz
            )
            if _phases:
                _total_coast_frames += _phases["coast_frames"]
        _total_coast_s = _total_coast_frames / hz if hz > 0 else 0.0
        if _total_coast_s > 0.1:
            _marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else ""
            _coast_lines.append(
                f"  Lap {lap['lap_num']}: {_total_coast_s:.1f}s coasting "
                f"({_total_coast_frames} frames across all corners){_marker}"
            )
    if _coast_lines:
        lines.append("COAST TIME AGGREGATION (total time coasting per lap, all corners):")
        lines.append("(coasting = neither brake nor gas near apex; pure time loss)")
        lines.extend(_coast_lines)
        _best_coast = min(
            (float(l.split(":")[1].split("s")[0].strip()) for l in _coast_lines),
            default=0.0,
        )
        _worst_coast = max(
            (float(l.split(":")[1].split("s")[0].strip()) for l in _coast_lines),
            default=0.0,
        )
        if _worst_coast - _best_coast > 0.3:
            lines.append(
                f"  >> COASTING SPREAD: {_worst_coast - _best_coast:.1f}s "
                f"between best and worst lap — reducing coast time is free lap time."
            )
        lines.append("")

    return lines


def build_braking_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    assert best_lap is not None
    ref_corners = list(ctx.ref_corners)
    corner_speeds = ctx.corner_speeds
    analysis_confidence = ctx.analysis_confidence
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    # ── Braking, turn-in, and throttle timing analysis
    lines.append("BRAKING & TIMING ANALYSIS:")
    lines.append("(brake_onset = seconds before corner entry; turn_in = seconds before entry;")
    lines.append(" gas_on = seconds after apex; trail_brake% = % of entry-to-apex with brake applied;")
    lines.append(" coast = frames near apex with neither gas nor brake; peak_brake_g = peak decel G)")
    lines.append("")
    # Compute coverage stats for brake_onset and gas_on so we can warn the LLM
    _total_phase_rows = 0
    _brake_onset_na_count = 0
    _gas_on_zero_count = 0
    for spec in ref_corners:
        cid = spec["id"]
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if not corner:
                continue
            phases = analyze_corner_phases(
                lap["track"], corner, lap["start_frame"], hz
            )
            if phases:
                _total_phase_rows += 1
                if phases["brake_onset_dt"] is None:
                    _brake_onset_na_count += 1
                if phases["gas_on_dt"] == 0.0:
                    _gas_on_zero_count += 1
    if _total_phase_rows > 0:
        _brake_na_pct = (_brake_onset_na_count / _total_phase_rows) * 100
        _gas_zero_pct = (_gas_on_zero_count / _total_phase_rows) * 100
        if _brake_na_pct > 50 or _gas_zero_pct > 50:
            lines.append("DATA QUALITY NOTE:")
            if _brake_na_pct > 50:
                lines.append(f"  brake_onset is N/A for {_brake_na_pct:.0f}% of corners — the approach zone may")
                lines.append("  not capture enough frames before entry, or braking threshold was not crossed.")
            if _gas_zero_pct > 50:
                lines.append(f"  gas_on reports 0.00s for {_gas_zero_pct:.0f}% of corners — this usually means")
                lines.append("  the driver was already on throttle at the apex frame, not that pickup is instant.")
            lines.append("  Treat these metrics as directional only, not absolute timing.")
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

    return lines


def build_grip_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    assert best_lap is not None
    ref_corners = list(ctx.ref_corners)
    corner_speeds = ctx.corner_speeds
    analysis_confidence = ctx.analysis_confidence
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    # ── Tyre grip-degradation across the stint
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

    return lines


def build_time_loss_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> List[str]:
    data = ctx.data
    laps = list(ctx.valid_laps)
    best_lap = ctx.best_lap
    assert best_lap is not None
    ref_corners = list(ctx.ref_corners)
    corner_speeds = ctx.corner_speeds
    analysis_confidence = ctx.analysis_confidence
    reference_lap_num = ctx.reference_lap_num
    comparison_lap_num = ctx.comparison_lap_num
    hz = ctx.hz
    lines: List[str] = []
    # ── Theoretical best lap — assemble best segment time per corner across all laps
    # Compute per-corner gap (best_lap_segment - best_segment_across_laps), then
    # theoretical_best = actual_best_lap_time - sum(gaps).  This avoids the
    # nonsensical "sum of corner segments vs full lap" comparison that ignored
    # straights and produced inflated potential-gain figures.
    _best_segments: Dict[int, float] = {}
    _best_lap_segments: Dict[int, float] = {}
    _corner_gaps: Dict[int, float] = {}
    for spec in ref_corners:
        cid = spec["id"]
        _best_seg = None
        for lap in laps:
            corner = lap_corner_map.get(lap["lap_num"], {}).get(cid)
            if corner:
                seg = corner_segment_time(corner, hz)
                if seg is not None and seg > 0.0 and (_best_seg is None or seg < _best_seg):
                    _best_seg = seg
        if _best_seg is not None:
            _bl_corner = lap_corner_map.get(best_lap["lap_num"], {}).get(cid)
            if _bl_corner:
                _bl_seg = corner_segment_time(_bl_corner, hz)
                if _bl_seg is not None and _bl_seg > 0.0:
                    if _best_seg < _bl_seg * 0.5:
                        continue
                    _best_segments[cid] = _best_seg
                    _best_lap_segments[cid] = _bl_seg
                    _corner_gaps[cid] = _bl_seg - _best_seg
    if _corner_gaps:
        _actual_best_time = best_lap["lap_time_s"]
        _total_gap = sum(_corner_gaps.values())
        _theoretical_best = _actual_best_time - _total_gap
        lines.append("THEORETICAL BEST LAP:")
        lines.append(f"  Theoretical best: {_theoretical_best:.2f}s (actual best: {_actual_best_time:.2f}s)")
        lines.append(f"  Potential gain: {_total_gap:.2f}s across {len(_corner_gaps)} corners")
        lines.append("  Per-corner best segments vs best lap segments:")
        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"
            _best_seg = _best_segments.get(cid)
            _bl_seg = _best_lap_segments.get(cid)
            if _best_seg is None or _bl_seg is None:
                continue
            _gap = _corner_gaps.get(cid, 0.0)
            lines.append(f"    C{cid} {name}: best segment {_best_seg:.2f}s  |  best lap segment {_bl_seg:.2f}s  |  gap {_gap:+.2f}s")
        lines.append("")

    # ── Time-loss ranking (cap implausible deltas that are capture artifacts)
    _MAX_PLAUSIBLE_SEGMENT_DELTA = 10.0  # seconds; anything larger is almost certainly corrupt
    _SUSPECT_SEGMENT_DELTA = 3.0  # seconds; LOW-confidence corners above this are suspect
    lines.append("TIME LOSS RANKING (worst -> best, by segment time delta):")
    lines.append(
        f"  (Deltas capped at ±{_MAX_PLAUSIBLE_SEGMENT_DELTA:.0f}s; "
        f"LOW-confidence deltas > ±{_SUSPECT_SEGMENT_DELTA:.0f}s flagged as suspect.)"
    )
    ranked = []
    corrupt_corners: List[str] = []
    suspect_corners: List[str] = []
    for spec in ref_corners:
        cid = spec["id"]
        ref_corner = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cmp_corner = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if ref_corner and cmp_corner:
            delta = corner_segment_time(cmp_corner, hz) - corner_segment_time(ref_corner, hz)
            is_low_conf = (
                ref_corner.get("confidence_label") == "low" or
                cmp_corner.get("confidence_label") == "low"
            )
            if is_low_conf and abs(delta) > _SUSPECT_SEGMENT_DELTA:
                suspect_corners.append(spec.get("name") or f"Corner {cid}")
                delta = max(-_SUSPECT_SEGMENT_DELTA, min(_SUSPECT_SEGMENT_DELTA, delta))
            elif abs(delta) > _MAX_PLAUSIBLE_SEGMENT_DELTA:
                corrupt_corners.append(spec.get("name") or f"Corner {cid}")
                delta = max(-_MAX_PLAUSIBLE_SEGMENT_DELTA, min(_MAX_PLAUSIBLE_SEGMENT_DELTA, delta))
            ranked.append((delta, spec.get("name") or f"Corner {cid}", cid))
    ranked.sort(reverse=True)
    for delta, name, cid in ranked:
        lines.append(f"  {name:<30} {delta:+.2f}s")
    if suspect_corners:
        lines.append(
            f"  >> NOTE: {', '.join(suspect_corners)} had suspect deltas "
            f"(>±{_SUSPECT_SEGMENT_DELTA:.0f}s with LOW confidence)."
        )
        lines.append("     Do not recommend specific time gains for these corners — the data is unreliable.")
    if corrupt_corners:
        lines.append(f"  >> NOTE: {', '.join(corrupt_corners)} had implausible deltas and were capped.")
        lines.append("     Do not recommend specific time gains for these corners — the data is unreliable.")
    lines.append("")

    return lines


def build_driving_sections(
    ctx: PromptContext,
    lap_corner_map: Dict[int, Dict[int, Dict]],
) -> tuple[List[str], Dict[int, Dict[int, Dict]]]:
    lines: List[str] = []
    lines.extend(build_corner_sections(ctx, lap_corner_map))
    lines.extend(build_straight_sections(ctx, lap_corner_map))
    lines.extend(build_braking_sections(ctx, lap_corner_map))
    lines.extend(build_grip_sections(ctx, lap_corner_map))
    lines.extend(build_time_loss_sections(ctx, lap_corner_map))
    return lines, lap_corner_map
