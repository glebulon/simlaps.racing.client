"""Re-decode graphics across an existing capture and report authoritative
progress coverage.

Compares against the analyzer's prior output (0% authoritative, fall-back
to dead-reckoning) to confirm the new ``decode_graphics_evo`` raises
authoritative coverage to ~100%.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.core.telemetry_decoder import decode_graphics


def main() -> int:
    capture = Path(sys.argv[1] if len(sys.argv) > 1 else "telemetry/capture_04-25-00-33-10.jsonl")
    if not capture.exists():
        print(f"capture not found: {capture}")
        return 1

    total = 0
    authoritative = 0
    sample_rows = []
    with capture.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if doc.get("_record_type") != "frame":
                continue
            g = (doc.get("regions") or {}).get("graphics") or {}
            raw = g.get("raw_hex")
            if not raw:
                continue
            total += 1
            decoded = decode_graphics(bytes.fromhex(raw))
            if decoded.get("has_authoritative_progress") and decoded.get("normalized_car_position") is not None:
                authoritative += 1
                if total % 500 == 0:
                    sample_rows.append((
                        doc.get("_frame", "?"),
                        decoded["npos"],
                        decoded["total_lap_count"],
                        decoded["current_lap_time_ms"],
                        decoded["last_laptime_ms"],
                        decoded["best_laptime_ms"],
                    ))

    if total == 0:
        print("no frames found in capture")
        return 1

    print(f"Total frames with graphics raw_hex: {total}")
    print(f"Authoritative progress (decoded npos valid): {authoritative}/{total} ({100 * authoritative / total:.1f}%)")
    print()
    print("Samples (every 500th frame):")
    print(f"  {'frame':>6}  {'npos':>7}  {'laps':>4}  {'lap_ms':>7}  {'last_ms':>8}  {'best_ms':>8}")
    for frame, npos, laps, lap_ms, last_ms, best_ms in sample_rows:
        print(f"  {frame:>6}  {npos:>7.4f}  {laps:>4}  {lap_ms:>7}  {last_ms:>8}  {best_ms:>8}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
