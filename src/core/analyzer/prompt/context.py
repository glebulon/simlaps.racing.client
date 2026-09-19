"""Normalized immutable context for AI prompt rendering."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


def lap_result_key(lap: Mapping[str, Any]) -> str | int:
    """Return the stable report identity, with legacy lap-number fallback."""
    return lap.get("result_key") or lap.get("lap_num")


def _unique_lap_by_number(laps: Tuple[dict, ...], lap_num: Any) -> Optional[dict]:
    matches = tuple(lap for lap in laps if lap.get("lap_num") == lap_num)
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class PromptContext:
    """Normalized lap selection, identity, confidence, and mode state."""

    data: Mapping[str, Any]
    all_laps: Tuple[dict, ...]
    coached_laps: Tuple[dict, ...]
    valid_laps: Tuple[dict, ...]
    invalid_laps: Tuple[dict, ...]
    best_lap: Optional[dict]
    coaching_reference_lap: Optional[dict]
    worst_lap: Optional[dict]
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
    reference_lap_key: str | int | None
    comparison_lap_key: str | int | None
    comparison_available: bool

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "PromptContext":
        all_laps = tuple(data.get("laps", []))
        hz = data.get("hz", 10.0)
        # Coaching keeps the established invalid-lap policy, but excludes a
        # capture segment whose timer coverage was not trustworthy.
        coached_laps = tuple(
            lap for lap in all_laps if lap.get("derived_metrics_trustworthy", True)
        )
        valid_laps = tuple(lap for lap in all_laps if lap.get("is_valid", True))
        invalid_laps = tuple(lap for lap in all_laps if not lap.get("is_valid", True))
        requested_best_lap_key = data.get("best_lap_result_key")
        requested_best_lap_num = data.get("best_lap_num")
        best_lap = next(
            (lap for lap in all_laps if requested_best_lap_key and lap_result_key(lap) == requested_best_lap_key),
            None,
        )
        if best_lap is None and not requested_best_lap_key:
            best_lap = _unique_lap_by_number(all_laps, requested_best_lap_num)
        if best_lap is None and coached_laps:
            best_lap = min(coached_laps, key=lambda lap: lap["lap_time_s"])
        requested_reference_lap_num = data.get("reference_lap_num")
        requested_reference_lap_key = data.get("reference_lap_result_key")
        coaching_reference_lap = next(
            (
                lap
                for lap in coached_laps
                if requested_reference_lap_key and lap_result_key(lap) == requested_reference_lap_key
            ),
            None,
        )
        if coaching_reference_lap is None and not requested_reference_lap_key:
            coaching_reference_lap = _unique_lap_by_number(coached_laps, requested_reference_lap_num)
        if coaching_reference_lap is None and coached_laps:
            coaching_reference_lap = min(coached_laps, key=lambda lap: lap["lap_time_s"])
        worst_lap = max(coached_laps, key=lambda lap: lap["lap_time_s"]) if coached_laps else None
        time_diff = (
            worst_lap["lap_time_s"] - coaching_reference_lap["lap_time_s"]
            if coaching_reference_lap is not None and worst_lap is not None
            else 0.0
        )
        analysis_mode = data.get("analysis_mode", "diagnostic")
        ref_corners = tuple(data.get("ref_corners", []))
        analysis_notes = list(data.get("analysis_notes", []))
        reference_lap_num = data.get("reference_lap_num")
        comparison_lap_num = data.get("comparison_lap_num")
        reference_lap_key = (
            lap_result_key(coaching_reference_lap)
            if coaching_reference_lap is not None
            else requested_reference_lap_key
        )
        requested_comparison_lap_key = data.get("comparison_lap_result_key")
        comparison_lap = next(
            (
                lap
                for lap in coached_laps
                if requested_comparison_lap_key and lap_result_key(lap) == requested_comparison_lap_key
            ),
            None,
        )
        if comparison_lap is None and not requested_comparison_lap_key:
            comparison_lap = _unique_lap_by_number(coached_laps, comparison_lap_num)
        comparison_lap_key = (
            lap_result_key(comparison_lap) if comparison_lap is not None else requested_comparison_lap_key
        )
        comparison_available = bool(
            data.get("comparison_available", comparison_lap_num is not None)
            and comparison_lap_num is not None
            and (
                comparison_lap_key != reference_lap_key
                if comparison_lap_key is not None and reference_lap_key is not None
                else comparison_lap_num != reference_lap_num
            )
        )
        return cls(
            data=data,
            all_laps=all_laps,
            coached_laps=coached_laps,
            valid_laps=valid_laps,
            invalid_laps=invalid_laps,
            best_lap=best_lap,
            coaching_reference_lap=coaching_reference_lap,
            worst_lap=worst_lap,
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
            reference_lap_key=reference_lap_key,
            comparison_lap_key=comparison_lap_key,
            comparison_available=comparison_available,
        )
