"""Lap detection functions — extracted from telemetry_analyzer.py."""
from typing import Dict, List, Optional

from src.utils.structured_logger import Component, log_debug


def _detect_laps_by_timing_state(track: List[Dict], hz: float = 1.0) -> Optional[List[int]]:
    """Detect laps using shared memory timing state (last_laptime_ms updates)."""
    min_lap_frames = max(10, int(round(1.0 * hz)))
    boundaries = []
    prev_last_laptime = None
    saw_empty_last_laptime = False

    for pt in track:
        last_laptime = pt.get("last_lap_time_ms")
        # Zero/None means there is no completed lap (session start, pit
        # outlap, or mappings being cleared during shutdown). It is not a
        # finish-line transition and must not create a zero-second lap.
        if (
            not isinstance(last_laptime, (int, float))
            or isinstance(last_laptime, bool)
            or last_laptime <= 0
        ):
            if prev_last_laptime is None:
                saw_empty_last_laptime = True
            continue
        # Detect when last_laptime changes (lap completion event)
        completed_transition = (
            (prev_last_laptime is None and saw_empty_last_laptime)
            or (
                prev_last_laptime is not None
                and last_laptime != prev_last_laptime
            )
        )
        # ACE can synthesize a final lap time while tearing down a race (for
        # example after a disqualification) even though the player never
        # crossed the timing line. The graphics page already labels that
        # sample as Ended, so it is a shutdown snapshot rather than a physical
        # completed-lap boundary.
        if completed_transition and pt.get("session_phase") == "Ended":
            prev_last_laptime = last_laptime
            continue
        if completed_transition:
            frame = pt["frame"]
            if not boundaries or (frame - boundaries[-1]) >= min_lap_frames:
                boundaries.append(frame)
        prev_last_laptime = last_laptime

    return boundaries if len(boundaries) >= 1 else None


def detect_laps(track: List[Dict], hz: float = 1.0) -> List[int]:
    """Detect lap boundaries using SHM timing state.

    Tiers 1 (game log boundaries) and 2 (SHM timing state) are handled
    in TelemetryAnalyzer.analyze. This function wraps tier 2 for
    standalone/test use.
    """
    result = _detect_laps_by_timing_state(track, hz=hz)
    if result:
        log_debug(Component.ANALYZER, "Lap detection: using SHM timing state")
        return result
    return []
