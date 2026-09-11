"""Captured ACE bytes exercise real decoder, analyzer and parser boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.core.log_parser import LogParser
from src.core.telemetry_analyzer import TelemetryAnalyzer, build_track, detect_laps
from src.core.telemetry_capture import FrameData, GameProcessStatus, TelemetryCapture
from src.core.telemetry_decoder import decode_graphics, decode_physics, decode_static
from src.models import SharedSessionManager

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def captured_rows():
    return [json.loads(line) for line in (FIXTURES / "sample_telemetry.jsonl").read_text().splitlines()]


def decode_frame(row):
    return FrameData(
        timestamp=row["timestamp"],
        frame_number=row["frame_number"],
        physics=decode_physics(bytes.fromhex(row["physics_raw"])),
        graphics=decode_graphics(bytes.fromhex(row["graphics_raw"])),
        static=decode_static(bytes.fromhex(row["static_raw"])),
    )


def test_captured_decoder_alignment_has_independent_expected_values(captured_rows):
    """Literal capture evidence must fail if decoder and synthetic offsets drift together."""
    frame = decode_frame(captured_rows[999])
    assert frame.graphics["session_time_left_ms"] == 216284
    assert frame.graphics["session_total_laps"] == 3
    assert frame.graphics["session_current_lap"] == 2
    assert frame.graphics["timing_last_laptime"] == "00:56.350"
    assert frame.graphics["timing_best_laptime"] == "00:56.350"
    assert frame.graphics["current_lap_time_ms"] == 18372
    assert frame.graphics["normalized_car_position"] == pytest.approx(0.3506346941)
    final = decode_frame(captured_rows[1930]).graphics
    assert (final["session_total_laps"], final["session_current_lap"]) == (3, 4)
    assert final["timing_is_invalid"] is True
    assert final["is_valid_lap"] is False
    assert final["session_phase"] == "Waiting_For_PitBox"


def test_capture_retains_contiguous_race_and_invalidation_signals(captured_rows):
    assert len(captured_rows) == 1931
    timestamps = [datetime.fromisoformat(row["timestamp"]) for row in captured_rows]
    assert timestamps == sorted(timestamps)
    assert timestamps[-1] > timestamps[0]
    expected = {
        251: (0, 0, 0, True),
        252: (33, 0, 0, True),
        815: (56330, 0, 0, True),
        816: (71, 56350, 1, True),
        1260: (44473, 56350, 1, True),
        1261: (44573, 56350, 1, False),
        1360: (54473, 56350, 1, False),
        1361: (120, 54453, 2, True),
    }
    for index, values in expected.items():
        graphics = decode_frame(captured_rows[index]).graphics
        assert tuple(graphics[key] for key in ("current_lap_time_ms", "last_laptime_ms", "completed_laps", "is_valid_lap")) == values


@pytest.mark.asyncio
@pytest.mark.parametrize("record_frames", [True, False], ids=["recording", "validity-only"])
async def test_captured_two_complete_laps_reach_analyzer(captured_rows, tmp_path, monkeypatch, record_frames):
    # Keep the full first two laps, including the exact standing-start and
    # completion samples. Capture output indices are relative to this slice.
    rows = captured_rows[251:1362]
    manager = SharedSessionManager()
    capture = TelemetryCapture(output_dir=str(tmp_path), hz=10.0, debug_logs=False, session_manager=manager, record_frames=record_frames)
    cursor = 0

    class CapturedRegionReader:
        """Inject only the read-only mapping boundary, leaving capture real."""

        def __init__(self, key, size):
            self.key, self.size = key, size

        def read_raw(self):
            nonlocal cursor
            data = bytes.fromhex(rows[cursor][self.key + "_raw"])
            if self.key == "static":
                cursor += 1
            return data

        def close(self):
            pass

    readers = {key: CapturedRegionReader(key, size) for key, size in (("physics", 1024), ("graphics", 4096), ("static", 2048))}
    monkeypatch.setattr(capture, "_connect_regions", lambda: readers)
    monkeypatch.setattr("src.core.telemetry_capture.is_game_running", lambda: GameProcessStatus.RUNNING if cursor < len(rows) else GameProcessStatus.NOT_RUNNING)
    # Sampling data already has the original cadence. Replay it without a
    # 111-second wall-clock wait; analysis still uses the captured 10 Hz rate.
    capture._interval = 0
    assert await capture.start_capture()
    await asyncio.wait_for(capture._task, timeout=5)
    frames = await capture.stop_capture()
    completions = manager.get_lap_completions_after(0)
    assert [(lap.lap_time_ms, lap.is_valid) for lap in completions] == [(56350, True), (54453, False)]
    if not record_frames:
        assert frames == []
        assert list(tmp_path.iterdir()) == []
        return
    assert len(frames) == 1111
    track = build_track(frames, hz=10.0)
    assert detect_laps(track, hz=10.0) == [565, 1110]
    assert any(point["progress_source"] == "graphics" for point in track)
    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)
    rendered_data = {}
    original_generate = analyzer._generate_ai_prompt

    async def observe_prompt(data, prefix):
        rendered_data.update(data)
        return await original_generate(data, prefix)

    monkeypatch.setattr(analyzer, "_generate_ai_prompt", observe_prompt)
    result = await analyzer.analyze(
        frames, hz=10.0, output_prefix="captured_race"
    )
    assert result.laps_detected == 2
    # The invalid second lap is faster; it must not become the reference/PB.
    assert result.best_lap_time == pytest.approx(56.350, abs=0.2)
    assert Path(result.html_path).is_file()
    assert Path(result.ai_prompt_path).read_text(encoding="utf-8")
    assert rendered_data["valid_lap_nums"] == [1]
    assert rendered_data["best_lap_num"] == 1
    assert rendered_data["reference_lap_num"] == 1
    assert rendered_data["comparison_lap_num"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True], ids=["live", "historical"])
async def test_real_log_follow_replays_ordered_player_laps(tmp_path, historical):
    data = (FIXTURES / "sample_log.txt").read_bytes()
    path = tmp_path / "captured.log"
    path.write_bytes(data if historical else b"")
    ready = asyncio.Event()
    finished = asyncio.Event()
    laps = []
    restarts = []

    async def status(message):
        if message.startswith(("Ready", "Monitoring for new laps")):
            ready.set()

    async def on_lap(session, lap):
        laps.append((lap.lap_time_ms, lap.lap_number, session.track, session.car))

    async def restart():
        restarts.append(len(laps))

    async def game_status(active):
        if not active and len(laps) == 11:
            finished.set()

    parser = LogParser(
        log_path=str(path), on_status_change=status, on_lap_complete=on_lap,
        on_session_restart=restart, on_game_status_change=game_status,
    )
    task = asyncio.create_task(parser.follow(poll_interval=0.001))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        if historical:
            assert laps == []
            assert restarts == []
        else:
            with path.open("ab") as handle:
                handle.write(data)
            await asyncio.wait_for(finished.wait(), timeout=5)
            assert [lap[0] for lap in laps] == [115264, 105177, 108180, 123071, 105879, 104859, 115214, 102186, 112717, 102957, 103215]
            assert restarts == [3, 6]
            assert all(lap[2] != "Unknown" and lap[3] == "ks_mazda_mx5_nd_cup" for lap in laps)
    finally:
        parser.stop()
        await asyncio.wait_for(task, timeout=5)
