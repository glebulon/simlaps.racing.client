"""Session summary persistence — extracted from telemetry_analyzer.py."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _session_summary_path(output_dir: str) -> str:
    return os.path.join(output_dir, "session_history.jsonl")


def _write_session_summary(
    output_dir: str,
    track: str,
    car: str,
    best_lap_time_s: float,
    top_speed: float,
    lap_count: int,
    avg_fuel_per_lap: Optional[float],
) -> None:
    path = _session_summary_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "track": track,
        "car": car,
        "best_lap_time_s": best_lap_time_s,
        "best_lap_time_str": f"{int(best_lap_time_s // 60)}:{best_lap_time_s % 60:05.2f}",
        "top_speed": top_speed,
        "laps": lap_count,
        "avg_fuel_per_lap": avg_fuel_per_lap,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load_previous_summary(output_dir: str, track: str, car: str) -> Optional[Dict[str, Any]]:
    path = _session_summary_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("track") == track and entry.get("car") == car:
            return entry
    return None
