"""Corner detection functions — extracted from telemetry_analyzer.py."""

import math
from typing import Any, Dict, List, Optional

from src.core.analyzer._util import (
    _confidence_label,
    _corner_measurement_window,
    _local_average,
    _median3,
    _optional_float,
    extract_car_state,
)


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
            pt
            for pt in canonical_track
            if spec["start"] <= (pt.get("lap_progress") if pt.get("lap_progress") is not None else -1.0) < spec["end"]
        ]
        if len(window) < 4:
            continue

        speed_series = [_optional_float(pt.get("speed")) for pt in window]
        smoothed_speed = _median3(speed_series)
        apex_candidates = [(idx, value) for idx, value in enumerate(smoothed_speed) if value is not None]
        if not apex_candidates:
            continue

        apex_idx, apex_speed = min(apex_candidates, key=lambda item: item[1])
        entry_idx = 0
        for idx, pt in enumerate(window[: apex_idx + 1]):
            brake = _optional_float(pt.get("brake")) or 0.0
            steer = abs(_optional_float(pt.get("steer")) or 0.0)
            if brake >= 0.08 or steer >= 0.03:
                entry_idx = idx
                break

        if entry_idx >= apex_idx and apex_idx > 0:
            entry_idx = 0

        exit_idx = len(window) - 1
        for idx in range(apex_idx + 1, len(window)):
            gas = _optional_float(window[idx].get("gas_percent", window[idx].get("gas"))) or 0.0
            if gas >= 0.20:
                exit_idx = idx
                break

        if exit_idx <= apex_idx:
            exit_idx = min(len(window) - 1, apex_idx + 1)
        if exit_idx == apex_idx and exit_idx < len(window) - 1:
            exit_idx = min(len(window) - 1, apex_idx + 1)

        entry = window[entry_idx]
        apex = window[apex_idx]
        exit_pt = window[exit_idx]
        valid_speed_ratio = sum(1 for value in speed_series if value is not None) / len(window)
        confidence = round(
            min(1.0, len(window) / 8.0) * 0.2 + valid_speed_ratio * 0.4 + (0.4 if authoritative_progress else 0.1),
            3,
        )

        m_start, m_end = _corner_measurement_window(spec)
        measurement = [
            pt
            for pt in canonical_track
            if m_start <= (pt.get("lap_progress") if pt.get("lap_progress") is not None else -1.0) < m_end
        ]
        if len(measurement) >= 2:
            segment_time_s = max(
                0.0,
                (_optional_float(measurement[-1].get("time_s")) or 0.0)
                - (_optional_float(measurement[0].get("time_s")) or 0.0),
            )
            if segment_time_s <= 0.0:
                segment_time_s = None
        else:
            segment_time_s = None

        result.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "start_frame": entry["frame"],
                "end_frame": exit_pt["frame"],
                "apex_frame": apex["frame"],
                "apex_speed": min(
                    value
                    for value in smoothed_speed[max(0, apex_idx - 1) : min(len(window), apex_idx + 2)]
                    if value is not None
                ),
                "min_speed": min(value for value in speed_series if value is not None),
                "entry_speed": _local_average(window, entry_idx, "speed"),
                "exit_speed": _local_average(window, exit_idx, "speed"),
                "apex_x": _optional_float(apex.get("x")) or 0.0,
                "apex_z": _optional_float(apex.get("z")) or 0.0,
                "lap_pos": apex.get("lap_progress", spec["start"]),
                "segment_time_s": segment_time_s,
                "confidence": confidence,
                "confidence_label": _confidence_label(confidence),
                "entry_state": extract_car_state(entry),
                "apex_state": extract_car_state(apex),
                "exit_state": extract_car_state(exit_pt),
            }
        )

    return result


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
        window = seg[ci_start : ci_end + 1]
        apex_idx = min(range(len(window)), key=lambda i: window[i]["speed"])
        apex = window[apex_idx]
        entry = window[0]
        exit_pt = window[-1]

        # Average entry/exit speeds over a few frames to reduce
        # single-point jitter on braking zones and acceleration zones.
        _N_AVG = min(3, max(1, len(window) // 3))
        entry_speed = sum(pt["speed"] for pt in window[:_N_AVG]) / _N_AVG
        exit_speed = sum(pt["speed"] for pt in window[-_N_AVG:]) / _N_AVG

        result.append(
            {
                "id": cid,
                "start_frame": seg[ci_start]["frame"],
                "end_frame": seg[ci_end]["frame"],
                "apex_frame": apex["frame"],
                "apex_speed": apex["speed"],
                "min_speed": min(pt["speed"] for pt in window),
                "entry_speed": entry_speed,
                "exit_speed": exit_speed,
                "apex_x": apex["x"],
                "apex_z": apex["z"],
                "lap_pos": seg[ci_start]["lap_pos"],
                "entry_state": extract_car_state(entry),
                "apex_state": extract_car_state(apex),
                "exit_state": extract_car_state(exit_pt),
            }
        )

    for i, c in enumerate(result):
        c["id"] = i + 1

    return result


def detect_profiled_corners(
    track: List[Dict],
    lap_start_frame: int,
    lap_end_frame: int,
    profile: Dict[str, Any],
    hz: float = 10.0,
) -> List[Dict]:
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
        window = [pt for pt in seg if pt["lap_pos"] is not None and spec["start"] <= pt["lap_pos"] < spec["end"]]
        if not window:
            continue

        apex = min(window, key=lambda pt: pt["speed"])
        entry = window[0]
        exit_pt = window[-1]

        # Average entry/exit speeds over a few frames to reduce
        # single-point jitter on braking zones and acceleration zones.
        _N_AVG = min(3, max(1, len(window) // 3))
        entry_speed = sum(pt["speed"] for pt in window[:_N_AVG]) / _N_AVG
        exit_speed = sum(pt["speed"] for pt in window[-_N_AVG:]) / _N_AVG

        # Measure segment time over a fixed lap_progress window so every lap
        # is evaluated on the identical track section.
        m_start, m_end = _corner_measurement_window(spec)
        if has_norm_pos:
            measurement = [pt for pt in seg if pt["lap_pos"] is not None and m_start <= pt["lap_pos"] < m_end]
            if len(measurement) >= 2:
                segment_time_s = (measurement[-1]["frame"] - measurement[0]["frame"]) / hz
                confidence = 0.5
            else:
                segment_time_s = None
                confidence = 0.3
            confidence_label = _confidence_label(confidence)
        else:
            segment_time_s = None
            confidence = 0.0
            confidence_label = "low"

        result.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "start_frame": entry["frame"],
                "end_frame": exit_pt["frame"],
                "apex_frame": apex["frame"],
                "apex_speed": apex["speed"],
                "min_speed": min(pt["speed"] for pt in window),
                "entry_speed": entry_speed,
                "exit_speed": exit_speed,
                "apex_x": apex["x"],
                "apex_z": apex["z"],
                "lap_pos": apex["lap_pos"],
                "segment_time_s": segment_time_s,
                "confidence": confidence,
                "confidence_label": confidence_label,
                "entry_state": extract_car_state(entry),
                "apex_state": extract_car_state(apex),
                "exit_state": extract_car_state(exit_pt),
            }
        )

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
