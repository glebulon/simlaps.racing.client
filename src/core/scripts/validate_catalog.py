"""Validate the track catalog for data quality issues."""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent.parent / "data" / "track_catalog.json"

with open(CATALOG_PATH, "r", encoding="utf-8") as f:
    catalog = json.load(f)

issues = []
corner_count = 0

for track_key, track in catalog.items():
    for config_key, config in track.get("configs", {}).items():
        corners = config.get("corners", [])
        if not corners:
            issues.append(f"EMPTY: {track_key}/{config_key} has no corners")
            continue

        # Check ordering and structure
        prev_id = 0
        prev_end = -1
        for c in corners:
            corner_count += 1
            cid = c["id"]
            start = c["start"]
            end = c["end"]

            # ID ordering
            if cid != prev_id + 1:
                issues.append(f"ID GAP: {track_key}/{config_key} corner id {cid} (expected {prev_id + 1})")

            # Start < End
            if start >= end:
                issues.append(f"RANGE: {track_key}/{config_key} corner {cid} ({c['name']}): start={start} >= end={end}")

            # Overlap with previous
            if prev_end > 0 and start < prev_end:
                issues.append(
                    f"OVERLAP: {track_key}/{config_key} corner {cid} starts at {start} before prev corner ends at {prev_end}"  # noqa: E501
                )

            # Gap too large
            if prev_end > 0 and start - prev_end > 0.06:
                issues.append(
                    f"GAP: {track_key}/{config_key} gap of {start - prev_end:.3f} between corner {cid - 1} and {cid}"
                )

            # Bounds
            if start < 0 or end > 1:
                issues.append(f"BOUNDS: {track_key}/{config_key} corner {cid}: start={start}, end={end} out of [0,1]")

            prev_id = cid
            prev_end = end

        # Check if last corner doesn't wrap around to start (for circuits)
        if corners and corners[-1]["end"] < 1.0:
            diff = 1.0 - corners[-1]["end"]
            if diff > 0.02:
                issues.append(
                    f'WRAP: {track_key}/{config_key} last corner "{corners[-1]["name"]}" ends at {corners[-1]["end"]}, gap of {diff:.3f} to finish line'  # noqa: E501
                )

print(f"Validated {len(catalog)} tracks, {corner_count} corners total")
if issues:
    print(f"\nFound {len(issues)} potential issues:")
    for i in issues:
        print(f"  {i}")
else:
    print("No issues found.")
