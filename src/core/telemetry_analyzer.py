"""
Telemetry Analyzer Module

Analyzes captured telemetry data and generates HTML reports and AI coaching prompts.
Based on test_scripts/telemetry/2-analyze.py
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

# ── Constants used directly in TelemetryAnalyzer.analyze()
# ── Functions
from src.core.analyzer._util import (
    _PLAUSIBLE_FRAME_THRESHOLD,
    _confidence_label,
    _corner_measurement_window,
    _decide_analysis_mode,
    _find_frame_index,
    _fraction,
    _optional_float,
    _profile_corner_sanity_notes,
    _safe_4,
    _sanitize_slip,
    _select_track_profile_for_analysis,
    balance_hint,
    classify_corner_issue,
    extract_car_state,
    format_car_state,
    get_physics,
    variation_label,
)
from src.core.analyzer.ai_prompt import generate_ai_prompt
from src.core.analyzer.analysis_result import AnalysisResult
from src.core.analyzer.build_track import build_track
from src.core.analyzer.canonical import _build_canonical_lap, _canonical_bins_for_profile
from src.core.analyzer.corner_detection import (
    _detect_profiled_corners_canonical,
    corner_segment_time,
    detect_corners,
    detect_profiled_corners,
    match_corners,
    match_profiled_corners,
)
from src.core.analyzer.html_renderer import render_html
from src.core.analyzer.lap_detection import (
    _detect_laps_by_timing_state,
    detect_laps,
)
from src.core.analyzer.metrics import (
    analyze_corner_phases,
    analyze_grip_utilization,
    analyze_lap_tyre_state,
    analyze_tyre_grip_degradation,
)
from src.core.analyzer.session_summary import _load_previous_summary, _write_session_summary
from src.core.telemetry_capture import CaptureMetadata, FrameData
from src.models import SharedSessionManager
from src.utils.structured_logger import Component, log_debug, log_info, log_warning

# Re-exported for backward compatibility with existing import sites/tests.
__all__ = [
    "TelemetryAnalyzer",
    "AnalysisResult",
    "_corner_measurement_window",
    "_decide_analysis_mode",
    "_detect_profiled_corners_canonical",
    "_find_frame_index",
    "_read_static_track_config",
    "_safe_4",
    "_sanitize_slip",
    "_select_track_profile_for_analysis",
    "analyze_corner_phases",
    "analyze_grip_utilization",
    "analyze_lap_tyre_state",
    "analyze_tyre_grip_degradation",
    "balance_hint",
    "build_track",
    "classify_corner_issue",
    "corner_segment_time",
    "detect_corners",
    "detect_laps",
    "detect_profiled_corners",
    "extract_car_state",
    "format_car_state",
    "get_physics",
    "match_corners",
    "match_profiled_corners",
    "variation_label",
]

_LAP_TIME_ALIGNMENT_TOLERANCE_MS = 2.0
_LAP_SEGMENT_MIN_TRIM_MS = 2_000.0


def _nearest_lap_marker_by_time(
    markers: List,
    timing_lap_time: Any,
    *,
    expected_original_number: Optional[int] = None,
):
    """Return the nearest unused log marker within rounding tolerance."""
    if not isinstance(timing_lap_time, (int, float)) or isinstance(timing_lap_time, bool):
        return None

    candidates = []
    for order, marker in enumerate(markers):
        marker_lap_time = marker[1]
        if not isinstance(marker_lap_time, (int, float)) or isinstance(marker_lap_time, bool):
            continue
        delta_ms = abs(float(marker_lap_time) - float(timing_lap_time))
        if delta_ms <= _LAP_TIME_ALIGNMENT_TOLERANCE_MS:
            candidates.append((delta_ms, order, marker))

    if not candidates:
        return None
    if expected_original_number is not None:
        ordered = [
            candidate
            for candidate in candidates
            if (
                candidate[2][4] is not None
                or candidate[2][5]
                or candidate[2][6]
            )
            and candidate[2][4] == expected_original_number
        ]
        if ordered:
            return min(ordered, key=lambda candidate: (candidate[0], candidate[1]))[2]
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]


def _clean_completed_lap_track(
    lap_track: List[Dict[str, Any]],
    completed_lap_time_ms: Any,
    *,
    hz: float,
) -> tuple[List[Dict[str, Any]], int, int, bool]:
    """Select one timer epoch and report whether derived metrics are trusted.

    ACE's live current-lap timer can continue through ``BackToPit`` and a
    subsequent pit exit even though the next reported completed-lap time only
    covers the final on-track portion.  The finish-line boundary is still
    authoritative; when the monotonic timer span is materially longer than
    that completed time, retain the matching suffix.  Ordinary rounding and
    normal pit-lane starts remain below the deliberately generous tolerance.
    """
    if not lap_track:
        return [], 0, 0, False

    active_track = [point for point in lap_track if point.get("status_name") != "AC_PAUSE"]
    paused_removed = len(lap_track) - len(active_track)

    def finalize(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not paused_removed or not points:
            return points
        first_frame = points[0]["frame"]
        compressed = []
        for offset, point in enumerate(points):
            sample = dict(point)
            sample["source_frame"] = point["frame"]
            sample["frame"] = first_frame + offset
            compressed.append(sample)
        return compressed

    if len(active_track) < 20:
        return finalize(active_track), 0, paused_removed, False

    completed_time = _optional_float(completed_lap_time_ms)
    if completed_time is None or completed_time <= 0:
        return finalize(active_track), 0, paused_removed, False

    timed_points: List[tuple[int, float]] = []
    for index, point in enumerate(active_track):
        timer = _optional_float(point.get("lap_time_ms"))
        if timer is not None and timer >= 0:
            timed_points.append((index, timer))
    if len(timed_points) < 2:
        return finalize(active_track), 0, paused_removed, False

    sampling_interval_ms = 1_000.0 / max(hz, 1.0)
    duration_tolerance_ms = 2.0 * sampling_interval_ms + _LAP_TIME_ALIGNMENT_TOLERANCE_MS

    # Treat a positive timer followed by zero as a reset too. ACE uses this
    # exact shape for pit outlaps, where ``last_laptime_ms`` remains zero.
    reset_indices: List[int] = []
    previous_timer: Optional[float] = None
    for index, point in enumerate(active_track):
        timer = _optional_float(point.get("lap_time_ms"))
        if timer is None or timer < 0:
            continue
        if previous_timer is not None and (
            (previous_timer > 0 and timer <= 0)
            or (previous_timer > duration_tolerance_ms and timer + duration_tolerance_ms < previous_timer)
        ):
            reset_indices.append(index)
        previous_timer = timer

    def duration_agrees(points: List[Dict[str, Any]], timer_span: float) -> bool:
        if len(points) < 2 or hz <= 0:
            return False
        sampled_duration_ms = (len(points) - 1) * 1_000.0 / hz
        return (
            abs(timer_span - completed_time) <= duration_tolerance_ms
            and abs(sampled_duration_ms - completed_time) <= duration_tolerance_ms
        )

    if reset_indices:
        candidates = []
        epoch_starts = [0] + reset_indices
        epoch_ends = reset_indices + [len(active_track)]
        for epoch_start, epoch_end in zip(epoch_starts, epoch_ends, strict=True):
            candidate = active_track[epoch_start:epoch_end]
            suffix_timers = [
                timer for index, timer in timed_points if epoch_start <= index < epoch_end
            ]
            if len(suffix_timers) < 2:
                continue
            timer_span = suffix_timers[-1] - suffix_timers[0]
            if duration_agrees(candidate, timer_span):
                candidates.append((epoch_start, candidate))
        if len(candidates) != 1:
            return finalize(active_track), 0, paused_removed, False
        reset_index, candidate = candidates[0]
        return finalize(candidate), reset_index, paused_removed, True

    timer_start = timed_points[0][1]
    timer_end = timed_points[-1][1]
    timer_span = timer_end - timer_start
    trim_tolerance_ms = max(
        _LAP_SEGMENT_MIN_TRIM_MS,
        completed_time * 0.05,
        duration_tolerance_ms,
    )
    if timer_span <= completed_time + trim_tolerance_ms:
        return finalize(active_track), 0, paused_removed, duration_agrees(active_track, timer_span)

    # A long monotonic span is only eligible for evidenced pit-prefix
    # trimming. Without a pit or BackToPit signal, choosing a suffix would
    # manufacture a plausible lap from an arbitrary slice of telemetry.
    target_timer = timer_end - completed_time
    trim_index = next(
        (index for index, timer in timed_points if timer >= target_timer),
        0,
    )
    pit_prefix_evidenced = any(
        bool(point.get("is_in_pit"))
        or bool(point.get("is_in_pit_lane"))
        or (
            "pit" in str(point.get("status_name") or "").casefold()
            and (
                "back" in str(point.get("status_name") or "").casefold()
                or "lane" in str(point.get("status_name") or "").casefold()
            )
        )
        for point in active_track[: trim_index + 1]
    )
    if not pit_prefix_evidenced:
        return finalize(active_track), 0, paused_removed, False

    candidate = active_track[trim_index:]
    if trim_index <= 0 or len(candidate) < 20:
        return finalize(active_track), 0, paused_removed, False

    candidate_timer_start = _optional_float(candidate[0].get("lap_time_ms"))
    candidate_timer_end = _optional_float(candidate[-1].get("lap_time_ms"))
    candidate_timer_span = (
        candidate_timer_end - candidate_timer_start
        if candidate_timer_start is not None and candidate_timer_end is not None
        else None
    )
    if candidate_timer_span is None or not duration_agrees(candidate, candidate_timer_span):
        return finalize(active_track), 0, paused_removed, False

    return finalize(candidate), trim_index, paused_removed, True


def _read_static_track_config(frames: List[FrameData]) -> tuple[Optional[str], Optional[str]]:
    """Extract authoritative track/config names from the static SHM region.

    AC Evo publishes ``track`` / ``track_configuration`` in the static region;
    these are the reliable layout selectors (graphics lap-length reads garbage
    in 0.8.x). The static payload is constant across frames, so the first
    populated values win.
    """
    track = config = None
    for frame in frames:
        static = frame.static or {}
        track = track or static.get("track") or None
        config = config or static.get("track_configuration") or None
        if track and config:
            break
    return track, config


class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""

    def __init__(
        self,
        output_dir: str,
        track_catalog: Optional[dict] = None,
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
        game_lap_boundaries: Optional[
            List
        ] = None,  # Can be List[int] or List[Tuple[int, Optional[float], Optional[int]]]
        car_name: Optional[str] = None,
    ) -> AnalysisResult:
        """Run full analysis pipeline and generate outputs."""
        log_info(
            Component.ANALYZER, "Starting analysis", frames=len(frames), hz=hz, track=track_name, prefix=output_prefix
        )

        if len(frames) < 20 and not game_lap_boundaries:
            log_warning(
                Component.ANALYZER, "Analysis skipped: insufficient frames", frames=len(frames), prefix=output_prefix
            )
            return await self._generate_empty_result(output_prefix)

        static_track_name, static_config_name = _read_static_track_config(frames)
        track_key, track_profile = _select_track_profile_for_analysis(
            static_track_name or track_name, static_config_name
        )
        if track_profile:
            log_info(Component.ANALYZER, "Track profile selected", profile=track_profile["display_name"])
        else:
            log_debug(Component.ANALYZER, "Track profile: none - using auto corner detection")

        drive_start = 0
        first_graphics = frames[0].graphics if frames and isinstance(frames[0].graphics, dict) else {}
        first_static = frames[0].static if frames and isinstance(frames[0].static, dict) else {}
        standing_start = (
            str(first_static.get("session_name") or "").casefold() == "race"
            and first_graphics.get("completed_laps") == 0
            and isinstance(
                first_graphics.get("current_lap_time_ms", first_graphics.get("current_time_ms")),
                (int, float),
            )
            and first_graphics.get("current_lap_time_ms", first_graphics.get("current_time_ms", 0)) <= 1_000
        )
        if not standing_start:
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
        if not track and not game_lap_boundaries:
            log_warning(Component.ANALYZER, "No plausible telemetry frames after quality filtering")
            return await self._generate_empty_result(output_prefix)

        authoritative_progress_ratio = _fraction(
            track, lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None
        )
        plausible_frame_ratio = _fraction(
            track,
            lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
        )
        analysis_confidence_score = round(authoritative_progress_ratio * 0.7 + plausible_frame_ratio * 0.3, 3)
        analysis_confidence = _confidence_label(analysis_confidence_score)

        analysis_mode, has_authoritative, has_high_plausible = _decide_analysis_mode(
            authoritative_progress_ratio,
            plausible_frame_ratio,
        )
        analysis_notes: List[str] = []

        if track_profile and track_profile.get("confidence") == "estimated":
            analysis_notes.append(
                "Track profile corner windows are estimated from public track maps, "
                "not verified telemetry - treat per-corner segment deltas as directional only."
            )

        log_info(
            Component.ANALYZER,
            "Data quality assessed",
            progress_ratio=f"{authoritative_progress_ratio:.1%}",
            frame_ratio=f"{plausible_frame_ratio:.1%}",
            confidence=analysis_confidence,
            confidence_score=analysis_confidence_score,
        )
        log_info(
            Component.ANALYZER,
            "Analysis mode determined",
            mode=analysis_mode,
            auth_ok=has_authoritative,
            plausible_fallback_ok=has_high_plausible,
        )

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
                f"Physics frame plausibility coverage is only {plausible_frame_ratio:.0%}; derived metrics are degraded."  # noqa: E501
            )

        # Prioritize definitive lap detection sources over telemetry heuristics.
        # 1st: Game log boundaries (most authoritative)
        # 2nd: Shared memory timing state (last_laptime_ms updates)
        # 3rd: Telemetry-based detection (position crossing as fallback)
        lap_bounds = None
        lap_times_ms = None
        lap_numbers = None
        lap_source_numbers = None
        lap_session_ids = None
        lap_result_ids = None
        lap_types = None
        timing_bounds = _detect_laps_by_timing_state(track, hz=hz) or []

        # 1st priority: Game log boundaries (most definitive)
        if game_lap_boundaries and len(game_lap_boundaries) >= 1:
            # Extract frame indices and lap times from tuples
            if isinstance(game_lap_boundaries[0], (tuple, list)) or hasattr(game_lap_boundaries[0], "frame_index"):
                initial_completed_laps = 0
                try:
                    initial_completed_laps = int(track[0].get("completed_laps") or 0) if track else 0
                except (TypeError, ValueError):
                    initial_completed_laps = 0

                def marker_fields(boundary):
                    if hasattr(boundary, "frame_index"):
                        return (
                            int(boundary.frame_index),
                            boundary.lap_time_ms,
                            boundary.lap_number,
                            boundary.lap_type or "VALID",
                            boundary.original_lap_number,
                            boundary.session_id,
                            boundary.result_id,
                        )
                    return (
                        int(boundary[0]),
                        boundary[1] if len(boundary) > 1 else None,
                        int(boundary[2]) if len(boundary) > 2 and boundary[2] is not None else None,
                        str(boundary[3]) if len(boundary) > 3 and boundary[3] is not None else "VALID",
                        None,
                        None,
                        None,
                    )

                sorted_markers = sorted(
                    (marker_fields(boundary) for boundary in game_lap_boundaries),
                    key=lambda item: item[0],
                )
                start_frame = track[0]["frame"] if track else 0
                if timing_bounds:
                    # Callback frame indices can be late when ACE buffers its
                    # file log or the UI event loop stalls. SHM timer changes
                    # preserve the physical order and exact frame boundary;
                    # enrich those boundaries with matching log metadata by
                    # lap time instead of trusting callback arrival order.
                    track_by_frame = {point["frame"]: point for point in track}
                    unused_markers = list(sorted_markers)
                    callback_frame_deltas = []
                    timing_slots = []
                    for idx, frame in enumerate(timing_bounds):
                        point = track_by_frame.get(frame, {})
                        timing_lap_time = point.get("last_lap_time_ms")
                        match = _nearest_lap_marker_by_time(
                            unused_markers,
                            timing_lap_time,
                            expected_original_number=initial_completed_laps + idx + 1,
                        )
                        if match is not None and match[3] in {"OUTLAP", "INLAP", "ABORTED"}:
                            timed_markers = [
                                marker
                                for marker in unused_markers
                                if marker[3] not in {"OUTLAP", "INLAP", "ABORTED"}
                            ]
                            preferred = _nearest_lap_marker_by_time(
                                timed_markers,
                                timing_lap_time,
                                expected_original_number=initial_completed_laps + idx + 1,
                            )
                            if preferred is not None:
                                match = preferred
                        if match is not None:
                            unused_markers.remove(match)
                            callback_frame_deltas.append(abs(match[0] - frame))
                        timing_slots.append([frame, timing_lap_time, match, idx])

                    # A delayed or rounded log callback can miss a matching
                    # SHM last-lap value. Reuse an unclaimed physical timing
                    # slot by frame proximity before creating a new boundary;
                    # this retains the captured result without consulting a
                    # shared map keyed only by its displayed number.
                    for marker in list(unused_markers):
                        available = [slot for slot in timing_slots if slot[2] is None]
                        if not available:
                            timing_slots.append([marker[0], None, marker, len(timing_slots)])
                            unused_markers.remove(marker)
                            continue
                        slot = min(available, key=lambda candidate: abs(marker[0] - candidate[0]))
                        slot[2] = marker
                        callback_frame_deltas.append(abs(marker[0] - slot[0]))
                        unused_markers.remove(marker)

                    timing_slots.sort(key=lambda slot: slot[0])
                    reconciled_markers = []
                    for order, (frame, timing_lap_time, match, _slot_index) in enumerate(timing_slots):
                        marker_number = match[2] if match is not None else None
                        source_number = match[4] if match is not None else None
                        if source_number is None:
                            source_number = marker_number
                        display_number = (
                            marker_number
                            if marker_number is not None
                            else initial_completed_laps + order + 1
                        )
                        reconciled_markers.append(
                            (
                                frame,
                                match[1]
                                if match is not None and isinstance(match[1], (int, float)) and match[1] > 0
                                else (
                                    timing_lap_time
                                    if isinstance(timing_lap_time, (int, float)) and timing_lap_time > 0
                                    else None
                                ),
                                display_number,
                                match[3] if match else "VALID",
                                source_number,
                                match[5] if match else None,
                                match[6] if match else None,
                            )
                        )

                    lap_bounds = [start_frame] + [marker[0] for marker in reconciled_markers]
                    lap_times_ms = [marker[1] for marker in reconciled_markers]
                    lap_numbers = [marker[2] for marker in reconciled_markers]
                    lap_types = [marker[3] for marker in reconciled_markers]
                    lap_source_numbers = [marker[4] for marker in reconciled_markers]
                    lap_session_ids = [marker[5] for marker in reconciled_markers]
                    lap_result_ids = [marker[6] for marker in reconciled_markers]
                    materially_delayed = any(delta > max(2, int(round(hz * 2.0))) for delta in callback_frame_deltas)
                    if len(sorted_markers) != len(timing_bounds) or materially_delayed:
                        analysis_notes.append(
                            "Delayed or incomplete log callbacks were realigned to shared-memory timing boundaries."
                        )
                    log_info(
                        Component.ANALYZER,
                        "Lap detection successful",
                        method="shared-memory timing boundaries enriched by game logs",
                        laps=len(timing_bounds),
                    )
                else:
                    lap_bounds = [start_frame] + [marker[0] for marker in sorted_markers]
                    lap_times_ms = [marker[1] for marker in sorted_markers]
                    lap_numbers = [
                        marker[2] if marker[2] is not None else initial_completed_laps + idx + 1
                        for idx, marker in enumerate(sorted_markers)
                    ]
                    lap_types = [marker[3] for marker in sorted_markers]
                    lap_source_numbers = [
                        marker[4] if marker[4] is not None else marker[2]
                        for marker in sorted_markers
                    ]
                    lap_session_ids = [marker[5] for marker in sorted_markers]
                    lap_result_ids = [marker[6] for marker in sorted_markers]
                    log_info(
                        Component.ANALYZER,
                        "Lap detection successful",
                        method="authoritative game log boundaries",
                        laps=len(lap_bounds) - 1,
                    )
                if initial_completed_laps > 0 and (not lap_numbers or lap_numbers[0] > 1):
                    analysis_notes.append(
                        f"Capture started after {initial_completed_laps} completed game lap(s); earlier laps are omitted from telemetry."  # noqa: E501
                    )
            else:
                lap_bounds = game_lap_boundaries
                log_info(
                    Component.ANALYZER,
                    "Lap detection successful",
                    method="authoritative game log boundaries",
                    laps=len(lap_bounds) - 1,
                )
        # 2nd priority: Shared memory timing state (last_laptime_ms updates)
        else:
            if timing_bounds and len(timing_bounds) >= 1:
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + timing_bounds
                # With no log boundary there is no result identity to carry;
                # SHM's ordered completion stream is the only authoritative
                # fallback. It is intentionally limited to this legacy path.
                lap_times_ms = []
                lap_types = []
                for _index, frame in enumerate(timing_bounds, start=1):
                    point = next((item for item in track if item["frame"] == frame), {})
                    lap_times_ms.append(point.get("last_lap_time_ms") or None)
                    validity = point.get("is_valid_lap")
                    lap_types.append("VALID" if validity is not False else "INVALID_GAME")
                log_info(
                    Component.ANALYZER,
                    "Lap detection successful",
                    method="shared memory timing state",
                    laps=len(lap_bounds),
                )
            else:
                lap_bounds = []

        if not lap_bounds or len(lap_bounds) < 2:
            log_warning(Component.ANALYZER, "Lap detection failed", reason="no valid boundaries")
            analysis_mode = "diagnostic"
            analysis_notes.append("No reliable lap boundaries were found from any detection method.")
            lap_bounds = []

        laps = []
        trimmed_lap_segments = 0
        trimmed_prefix_frames = 0
        paused_frames_removed = 0
        for i in range(len(lap_bounds) - 1):
            s, e = lap_bounds[i], lap_bounds[i + 1]
            game_lap_num = lap_numbers[i] if lap_numbers and i < len(lap_numbers) else i + 1
            source_lap_num = (
                lap_source_numbers[i]
                if lap_source_numbers and i < len(lap_source_numbers)
                else None
            )
            session_id = lap_session_ids[i] if lap_session_ids and i < len(lap_session_ids) else None
            result_id = lap_result_ids[i] if lap_result_ids and i < len(lap_result_ids) else None
            lap_type = lap_types[i] if lap_types and i < len(lap_types) else "VALID"
            if lap_type in {"OUTLAP", "INLAP", "ABORTED"}:
                continue

            game_lap_time_ms = lap_times_ms[i] if lap_times_ms and i < len(lap_times_ms) else None
            boundary_track = [pt for pt in track if s <= pt["frame"] < e]
            lap_track, prefix_removed, pause_removed, derived_metrics_trustworthy = _clean_completed_lap_track(
                boundary_track,
                game_lap_time_ms,
                hz=hz,
            )
            if prefix_removed:
                trimmed_lap_segments += 1
                trimmed_prefix_frames += prefix_removed
            paused_frames_removed += pause_removed
            # Keep the official result when telemetry is absent or too short.
            # Derived metrics remain unavailable, but dropping the result here
            # would make a captured callback disappear from the report.
            effective_start_frame = lap_track[0]["frame"] if lap_track else s

            lap_progress_ratio = _fraction(
                lap_track,
                lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None,
            )
            lap_plausible_ratio = _fraction(
                lap_track,
                lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
            )
            lap_quality_score = round(lap_progress_ratio * 0.7 + lap_plausible_ratio * 0.3, 3)
            canonical_lap = (
                _build_canonical_lap(
                    lap_track,
                    lap_start_frame=s,
                    hz=hz,
                    bins=_canonical_bins_for_profile(track_profile),
                )
                if derived_metrics_trustworthy
                else None
            )
            uses_canonical_progress = canonical_lap is not None

            if not derived_metrics_trustworthy:
                corners = []
            elif track_profile and track_profile.get("corners") and canonical_lap is not None:
                corners = _detect_profiled_corners_canonical(
                    canonical_lap["samples"],
                    track_profile,
                    hz,
                    authoritative_progress=lap_progress_ratio >= 0.60,
                )
            elif track_profile and track_profile.get("corners"):
                # Use profile-based corner detection even without canonical progress
                corners = detect_profiled_corners(
                    lap_track,
                    effective_start_frame,
                    e,
                    track_profile,
                    hz=hz,
                )
            else:
                corners = detect_corners(
                    lap_track,
                    effective_start_frame,
                    e,
                    hz=hz,
                )

            # Use game-reported lap times when available.
            if game_lap_time_ms is not None:
                lap_time = game_lap_time_ms / 1000.0
            else:
                # Fall back to telemetry-derived duration so laps without
                # game-reported times (e.g. invalid/aborted laps) are still
                # included in the analysis rather than silently dropped.
                lap_time = (e - s) / hz

            # Calculate fuel consumption from samples that belong to this lap.
            # Do not use the first point of the next lap or mapping-teardown
            # zeroes as the end sample.
            fuel_used = None
            if derived_metrics_trustworthy:
                fuel_samples = [
                    float(point["fuel"])
                    for point in lap_track
                    if isinstance(point.get("fuel"), (int, float)) and point["fuel"] > 0
                ]
                if len(fuel_samples) >= 2 and fuel_samples[0] > fuel_samples[-1]:
                    fuel_used = round(fuel_samples[0] - fuel_samples[-1], 3)

            max_speed = max(pt["speed"] for pt in lap_track) if derived_metrics_trustworthy else None
            avg_speed = (
                sum(pt["speed"] for pt in lap_track) / len(lap_track)
                if derived_metrics_trustworthy
                else None
            )

            laps.append(
                {
                    "lap_num": game_lap_num,
                    "capture_lap_index": i + 1,
                    "start_frame": effective_start_frame,
                    "end_frame": e,
                    "lap_time_s": lap_time,
                    "lap_time_str": f"{int(lap_time // 60)}:{lap_time % 60:05.2f}",
                    "max_speed": max_speed,
                    "avg_speed": avg_speed,
                    "fuel_used": fuel_used,
                    "is_valid": lap_type == "VALID",
                    "source_lap_num": source_lap_num,
                    "session_id": session_id,
                    "result_id": result_id,
                    "track": lap_track,
                    "canonical_track": canonical_lap["samples"] if canonical_lap else None,
                    "corners": corners,
                    "quality_score": lap_quality_score if derived_metrics_trustworthy else 0.0,
                    "confidence_label": (
                        _confidence_label(lap_quality_score)
                        if derived_metrics_trustworthy
                        else "low"
                    ),
                    "progress_ratio": lap_progress_ratio,
                    "plausible_frame_ratio": lap_plausible_ratio,
                    "uses_canonical_progress": uses_canonical_progress,
                    "derived_metrics_trustworthy": derived_metrics_trustworthy,
                }
            )
            # The displayed lap number is presentation metadata and may be
            # reused after a restart or corrected by a delayed callback.  Give
            # every captured result a stable internal key for report joins.
            lap = laps[-1]
            if result_id is not None:
                lap["result_key"] = f"result:{session_id or ''}:{result_id}"
            else:
                lap["result_key"] = f"capture:{i + 1}"
            fuel_str = f"  fuel {fuel_used:.3f}L" if fuel_used is not None else ""
            log_debug(
                Component.ANALYZER,
                "Lap summary",
                lap_num=game_lap_num,
                lap_time=f"{lap_time:.0f}s",
                max_speed=(f"{max_speed:.0f} km/h" if max_speed is not None else "unavailable"),
                corners=len(corners),
                fuel=fuel_str,
                prefix_frames_removed=prefix_removed,
                paused_frames_removed=pause_removed,
            )

        if trimmed_lap_segments:
            analysis_notes.append(
                f"Trimmed {trimmed_prefix_frames} stale prefix frame(s) from "
                f"{trimmed_lap_segments} lap segment(s) using the authoritative lap duration."
            )
        if paused_frames_removed:
            analysis_notes.append(f"Excluded {paused_frames_removed} paused telemetry samples from completed laps.")

        if not laps:
            log_warning(Component.ANALYZER, "Analysis complete: no valid laps found")
            return await self._generate_empty_result(output_prefix)

        # Official result metadata was frozen with each captured boundary
        # before segmentation. Never overwrite it from live maps keyed by the
        # displayed lap number: a later session may reuse that number.
        if lap_numbers and lap_numbers[0] > 1:
            analysis_notes.append(
                f"Telemetry starts at game lap {lap_numbers[0]}; earlier logged laps are not included."
            )

        # Valid and invalid laps retain the upstream coaching policy, while a
        # segment whose timer epoch cannot be verified contributes no derived
        # metrics or comparison data.
        coached_laps = [lap for lap in laps if lap.get("derived_metrics_trustworthy", True)]
        if coached_laps:
            # Capture-level quality can be depressed by an abandoned or
            # partial segment. Recompute coaching mode from the segments that
            # passed the timer trust contract so one good lap remains usable.
            trusted_track = [point for lap in coached_laps for point in lap.get("track", [])]
            trusted_authoritative_ratio = _fraction(
                trusted_track,
                lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None,
            )
            trusted_plausible_ratio = _fraction(
                trusted_track,
                lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
            )
            # Recompute quality from trustworthy segments in both directions:
            # an incomplete high-quality prefix must not make a weaker
            # trustworthy lap eligible for full coaching.
            authoritative_progress_ratio = trusted_authoritative_ratio
            plausible_frame_ratio = trusted_plausible_ratio
            analysis_confidence_score = round(
                authoritative_progress_ratio * 0.7 + plausible_frame_ratio * 0.3,
                3,
            )
            analysis_confidence = _confidence_label(analysis_confidence_score)
            analysis_mode, _, _ = _decide_analysis_mode(
                authoritative_progress_ratio,
                plausible_frame_ratio,
            )
            _, trusted_has_authoritative, trusted_has_high_plausible = _decide_analysis_mode(
                authoritative_progress_ratio,
                plausible_frame_ratio,
            )
            analysis_notes = [
                note
                for note in analysis_notes
                if not note.startswith(
                    (
                        "Authoritative graphics progress coverage is",
                        "Authoritative graphics progress coverage too low",
                        "Physics frame plausibility coverage is only",
                    )
                )
            ]
            if not trusted_has_authoritative and trusted_has_high_plausible:
                analysis_notes.append(
                    f"Authoritative graphics progress coverage is {authoritative_progress_ratio:.0%}, "
                    f"but physics frame plausibility is {plausible_frame_ratio:.0%} — using "
                    "dead-reckoning progress for coaching. Lap 1 may be missing if capture "
                    "started mid-lap."
                )
            elif not trusted_has_authoritative and not trusted_has_high_plausible:
                analysis_notes.append(
                    f"Authoritative graphics progress coverage too low ({authoritative_progress_ratio:.0%}) "
                    f"and plausible physics coverage is only {plausible_frame_ratio:.0%}; detailed coaching disabled."
                )
            if plausible_frame_ratio < 0.75:
                analysis_notes.append(
                    f"Physics frame plausibility coverage is only {plausible_frame_ratio:.0%}; "
                    "derived metrics are degraded."
                )
        valid_laps = [lap for lap in laps if lap.get("is_valid", True)]
        profile_sanity_notes = _profile_corner_sanity_notes(
            coached_laps,
            profile_corners=track_profile.get("corners", []) if track_profile else None,
        )
        if profile_sanity_notes:
            analysis_mode = "diagnostic"
            analysis_notes.extend(profile_sanity_notes)

        # The official best is a result-level fact and stays visible even if
        # its telemetry coverage is unavailable.
        best_lap = min(laps, key=lambda lap: lap["lap_time_s"]) if laps else None
        laps_with_corners = [lap for lap in coached_laps if lap.get("corners")]
        trusted_candidates = [lap for lap in coached_laps if lap.get("derived_metrics_trustworthy", True)]
        ref_lap = (
            min(laps_with_corners, key=lambda lap: lap["lap_time_s"])
            if laps_with_corners
            else (min(trusted_candidates, key=lambda lap: lap["lap_time_s"]) if trusted_candidates else None)
        )
        coachable_laps = [lap for lap in laps_with_corners if lap.get("confidence_label") != "low"]
        comparison_pool = coachable_laps or laps_with_corners or coached_laps
        comparison_pool = sorted(
            (lap for lap in comparison_pool if ref_lap is None or lap["result_key"] != ref_lap["result_key"]),
            key=lambda lap: lap["lap_time_s"],
        )
        comparison_lap = comparison_pool[(len(comparison_pool) - 1) // 2] if comparison_pool else None
        ref_corners = ref_lap.get("corners", []) if ref_lap else []

        if comparison_lap is None:
            analysis_notes.append("Only one coachable lap was available; comparative coaching is unavailable.")

        untrusted_count = sum(
            1 for lap in laps if not lap.get("derived_metrics_trustworthy", True)
        )
        if untrusted_count:
            analysis_notes.append(
                f"Derived metrics were unavailable for {untrusted_count} lap segment(s) because "
                "the captured timer coverage was incomplete or ambiguous."
            )

        log_info(
            Component.ANALYZER,
            "Analysis complete",
            laps=len(laps),
            best_lap_time=(f"{best_lap['lap_time_s']:.1f}s" if best_lap else "none"),
            coachable_laps=len(coachable_laps),
        )

        if not ref_corners:
            analysis_mode = "diagnostic"
            analysis_notes.append("No trustworthy canonical corners were available for comparison.")

        corner_data: Dict[Any, Dict[Any, Dict[str, Any]]] = defaultdict(dict)
        corner_speeds: Dict[Any, Dict[Any, float]] = defaultdict(dict)
        for lap in laps_with_corners:
            if track_profile and track_profile.get("corners"):
                matched = match_profiled_corners(ref_corners, lap["corners"])
            else:
                matched = match_corners(ref_corners, lap["corners"])
            for cid, corner in matched.items():
                if corner and corner.get("confidence_label") != "low":
                    seg_time = corner_segment_time(corner, hz)
                    corner_data[cid][lap["result_key"]] = {
                        "apex": round(corner["apex_speed"], 1),
                        "entry": round(corner["entry_speed"], 1),
                        "exit": round(corner["exit_speed"], 1),
                        "seg_time": round(seg_time, 3),
                        "confidence": round(float(corner.get("confidence", 0.0)), 3),
                        "confidence_label": corner.get("confidence_label", "low"),
                    }
                    corner_speeds[cid][lap["result_key"]] = corner["apex_speed"]

        data = {
            "meta": metadata.to_dict() if metadata else {},
            "hz": hz,
            "track_key": track_key,
            "track_name": track_profile["track_name"] if track_profile else track_name,
            "config_key": track_profile["config_key"] if track_profile else None,
            "config_name": track_profile["config_name"] if track_profile else None,
            "track_label": track_profile["display_name"] if track_profile else track_name,
            "car": car_name or self._session_manager.get_car(),
            "laps": laps,
            "best_lap_num": best_lap["lap_num"] if best_lap else None,
            "best_lap_result_key": best_lap.get("result_key") if best_lap else None,
            "reference_lap_num": ref_lap["lap_num"] if ref_lap else None,
            "reference_lap_result_key": ref_lap.get("result_key") if ref_lap else None,
            "comparison_lap_num": comparison_lap["lap_num"] if comparison_lap else None,
            "comparison_lap_result_key": comparison_lap.get("result_key") if comparison_lap else None,
            "comparison_available": comparison_lap is not None,
            "valid_lap_nums": [lap["lap_num"] for lap in valid_laps],
            "coaching_lap_nums": [lap["lap_num"] for lap in coachable_laps],
            "ref_corners": ref_corners,
            "profile_corners": track_profile.get("corners", []) if track_profile else [],
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

        # ── Session-over-session comparison
        _track_label = data.get("track_label") or data.get("track_name") or ""
        _car = data.get("car") or ""
        _laps_with_fuel = [lap for lap in coached_laps if lap.get("fuel_used") is not None]
        _avg_fuel = sum(lap["fuel_used"] for lap in _laps_with_fuel) / len(_laps_with_fuel) if _laps_with_fuel else None
        _prev = _load_previous_summary(self._output_dir, _track_label, _car) if best_lap else None
        if _prev and best_lap:
            _delta = best_lap["lap_time_s"] - _prev["best_lap_time_s"]
            _delta_str = f"+{_delta:.2f}s" if _delta > 0 else f"{_delta:.2f}s"
            analysis_notes.append(
                f"Last session best: {_prev['best_lap_time_str']} (today {best_lap['lap_time_str']}, {_delta_str})."
            )
        if best_lap and coached_laps:
            _write_session_summary(
                self._output_dir,
                _track_label,
                _car,
                best_lap["lap_time_s"],
                max((lap.get("max_speed") or 0.0) for lap in coached_laps) if coached_laps else 0.0,
                len(coached_laps),
                _avg_fuel,
            )

        if coached_laps:
            telemetry_summary = {
                "max_speed": max((lap.get("max_speed") or 0.0) for lap in coached_laps),
                "stint_number": 1,
            }
            identity_bearing_results = any(
                lap.get("session_id") or lap.get("result_id")
                for lap in laps
            )
            if not identity_bearing_results:
                self._session_manager.update_from_telemetry(telemetry_summary)
            else:
                report_session_ids = [lap.get("session_id") for lap in laps]
                owned_session_ids = {session_id for session_id in report_session_ids if session_id}
                all_results_owned = (
                    len(owned_session_ids) == 1
                    and all(session_id == next(iter(owned_session_ids)) for session_id in report_session_ids)
                )
                if all_results_owned:
                    owned_session_id = next(iter(owned_session_ids))
                    self._session_manager.update_from_telemetry(
                        telemetry_summary,
                        expected_session_id=owned_session_id,
                    )
                else:
                    log_debug(
                        Component.ANALYZER,
                        "Skipped shared telemetry summary for closed session",
                        report_sessions=sorted(owned_session_ids),
                    )

        log_info(Component.ANALYZER, "Generating outputs", prefix=output_prefix)
        html_path = await self._generate_html(data, output_prefix)
        ai_prompt_path = await self._generate_ai_prompt(data, output_prefix)
        log_info(Component.ANALYZER, "Outputs generated", html=html_path, ai_prompt=ai_prompt_path)

        return AnalysisResult(
            html_path=html_path,
            ai_prompt_path=ai_prompt_path,
            laps_detected=len(laps),
            best_lap_time=best_lap["lap_time_s"] if best_lap else None,
            track_name=data.get("track_label") or data.get("track_name"),
        )

    async def _generate_empty_result(self, output_prefix: Optional[str] = None) -> AnalysisResult:
        """Generate result for empty/invalid data without creating files."""
        log_info(Component.ANALYZER, "Skipping output: insufficient or invalid telemetry data", prefix=output_prefix)
        return AnalysisResult(
            html_path=None,
            ai_prompt_path=None,
            laps_detected=0,
            best_lap_time=None,
            track_name=None,
        )

    async def _generate_html(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate HTML report with full telemetry visualization."""
        return await render_html(data, self._output_dir, output_prefix)

    async def _generate_ai_prompt(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate detailed AI coaching prompt with per-corner analysis and setup recommendations."""
        return await generate_ai_prompt(data, self._output_dir, output_prefix)
