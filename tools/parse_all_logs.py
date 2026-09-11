"""Parse all ACE logs in a folder and print results."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.log_parser import LogParser

LOG_DIR = Path("C:/Users/Gleb/Saved Games/ACE/Logs")
log_files = sorted(LOG_DIR.glob("*.txt"))


async def main():
    for log_file in log_files:
        print(f"\n{'=' * 80}")
        print(f"FILE: {log_file.name}")
        print(f"{'=' * 80}")

        parser = LogParser()
        parser.log_path = log_file
        try:
            sessions = await parser.parse_file()
        except Exception as e:
            print(f"ERROR parsing {log_file.name}: {e}")
            continue

        if not sessions:
            print("No sessions found.")
            continue

        for session in sessions:
            print(f"\nTrack: {session.track}")
            print(f"Car:   {session.car}")
            print(f"Type:  {session.session_type}")
            print(f"Laps:  {len(session.laps)}")
            print()
            print(
                f"{'Lap':>3}  {'Time':>10}  {'State':>12}  {'Valid':>5}  {'S1':>7}  {'S2':>7}  {'S3':>7}  {'Compound':>8}"  # noqa: E501
            )
            print("-" * 80)

            for lap in session.laps:
                time_str = lap.lap_time_str if lap.lap_time_str else "--:--.---"
                s1 = f"{lap.sector1_ms}" if lap.sector1_ms is not None else "-"
                s2 = f"{lap.sector2_ms}" if lap.sector2_ms is not None else "-"
                s3 = f"{lap.sector3_ms}" if lap.sector3_ms is not None else "-"
                compound = lap.tyre_compound or "-"
                print(
                    f"{lap.lap_number:>3}  {time_str:>10}  {lap.lap_state.value:>12}  "
                    f"{'Y' if lap.is_valid else 'N':>5}  {s1:>7}  {s2:>7}  {s3:>7}  {compound:>8}"
                )


asyncio.run(main())
