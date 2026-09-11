"""Normalized immutable context for AI prompt rendering."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class PromptContext:
    """Normalized lap selection, identity, confidence, and mode state."""

    data: Mapping[str, Any]
    all_laps: Tuple[dict, ...]
    valid_laps: Tuple[dict, ...]
    invalid_laps: Tuple[dict, ...]
    best_lap: Optional[dict]
    worst_lap: Optional[dict]
    no_valid_laps: bool
    time_diff: float
    hz: float
    track_label: str
    car_model: str
    ref_corners: Tuple[dict, ...]
    corner_speeds: Mapping[Any, Any]
    analysis_mode: str
    analysis_confidence: str
    analysis_notes: Tuple[str, ...]
    authoritative_progress_ratio: float
    plausible_frame_ratio: float
    reference_lap_num: Optional[int]
    comparison_lap_num: Optional[int]
    comparison_available: bool

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "PromptContext":
        all_laps = tuple(data.get("laps", []))
        hz = data.get("hz", 10.0)
        valid_laps = tuple(lap for lap in all_laps if lap.get("is_valid", True))
        invalid_laps = tuple(lap for lap in all_laps if not lap.get("is_valid", True))
        requested_best_lap_num = data.get("best_lap_num")
        best_lap = next(
            (lap for lap in valid_laps if lap.get("lap_num") == requested_best_lap_num),
            None,
        )
        if best_lap is None and valid_laps:
            best_lap = min(valid_laps, key=lambda lap: lap["lap_time_s"])
        no_valid_laps = best_lap is None
        if best_lap is None and all_laps:
            best_lap = min(all_laps, key=lambda lap: lap["lap_time_s"])
        worst_lap = max(valid_laps or all_laps, key=lambda lap: lap["lap_time_s"]) if all_laps else None
        time_diff = (
            worst_lap["lap_time_s"] - best_lap["lap_time_s"] if best_lap is not None and worst_lap is not None else 0.0
        )
        analysis_mode = data.get("analysis_mode", "diagnostic")
        ref_corners = tuple(data.get("ref_corners", []))
        analysis_notes = list(data.get("analysis_notes", []))
        if no_valid_laps and all_laps:
            analysis_mode = "diagnostic"
            ref_corners = ()
            note = "No valid completed laps were available; invalid laps are shown for diagnostics only."
            if note not in analysis_notes:
                analysis_notes.append(note)
        reference_lap_num = data.get("reference_lap_num")
        comparison_lap_num = data.get("comparison_lap_num")
        comparison_available = bool(
            data.get("comparison_available", comparison_lap_num is not None)
            and comparison_lap_num is not None
            and comparison_lap_num != reference_lap_num
        )
        return cls(
            data=data,
            all_laps=all_laps,
            valid_laps=valid_laps,
            invalid_laps=invalid_laps,
            best_lap=best_lap,
            worst_lap=worst_lap,
            no_valid_laps=no_valid_laps,
            time_diff=time_diff,
            hz=hz,
            track_label=data.get("track_label") or data.get("track_name") or "Unknown Track",
            car_model=data.get("car") or "Unknown Car",
            ref_corners=ref_corners,
            corner_speeds=data.get("corner_speeds", {}),
            analysis_mode=analysis_mode,
            analysis_confidence=data.get("analysis_confidence", "low"),
            analysis_notes=tuple(analysis_notes),
            authoritative_progress_ratio=float(data.get("authoritative_progress_ratio", 0.0) or 0.0),
            plausible_frame_ratio=float(data.get("plausible_frame_ratio", 0.0) or 0.0),
            reference_lap_num=reference_lap_num,
            comparison_lap_num=comparison_lap_num,
            comparison_available=comparison_available,
        )
