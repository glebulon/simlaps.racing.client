"""AnalysisResult dataclass — extracted from telemetry_analyzer.py."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AnalysisResult:
    """Result of telemetry analysis."""

    html_path: Optional[str]
    ai_prompt_path: Optional[str]
    laps_detected: int
    best_lap_time: Optional[float]
    track_name: Optional[str]
