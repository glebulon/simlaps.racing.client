"""Regression coverage for post-start lap identity and coaching trust."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.core.analyzer.prompt.context import PromptContext
from src.core.analyzer.prompt.driving import build_driving_sections
from src.core.analyzer.prompt.session import build_session_sections
from src.core.telemetry_analyzer import TelemetryAnalyzer, _nearest_shared_lap_by_time
from src.core.telemetry_capture import FrameData
from src.models import LapTimingData, SharedSessionManager


def _frame(
    frame: int,
    timer: int,
    last: int = 0,
    *,
    completed_laps: int = 3,
    fuel: float = 5.0,
) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame,
        physics={"speed_kmh": 100.0, "gear": 3, "fuel": fuel},
        graphics={
            "normalized_car_position": (frame % 1200) / 1200.0,
            "has_authoritative_progress": True,
            "current_time_ms": timer,
            "last_time_ms": last,
            "completed_laps": completed_laps,
            "is_valid_lap": True,
            "status_name": "AC_LIVE",
            "session_phase": "Session",
        },
    )


async def _analyze(analyzer, frames, **kwargs):
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        result = await analyzer.analyze(frames, hz=10.0, **kwargs)
    return result, html.await_args.args[0]


@pytest.mark.asyncio
async def test_shared_lap_overlay_keeps_callback_source_number_separate_from_display_number(
    tmp_path,
):
    manager = SharedSessionManager()
    for lap_num, lap_time in {
        1: 120_792.0,
        2: 120_477.0,
        3: 117_417.0,
        4: 119_958.0,
    }.items():
        manager._session_data.lap_timing[lap_num] = LapTimingData(
            lap_number=lap_num,
            completed_lap_time=lap_time,
            completed_lap_time_source="logs",
        )

    frames = []
    for lap_start, lap_end, last in (
        (0, 1200, 0),
        (1200, 2400, 120_477),
        (2400, 3600, 117_417),
        (3600, 3601, 119_958),
    ):
        for frame in range(lap_start, lap_end):
            frames.append(_frame(frame, (frame - lap_start) * 100, last))

    _, data = await _analyze(
        TelemetryAnalyzer(str(tmp_path), session_manager=manager),
        frames,
        game_lap_boundaries=[
            (1200, 120_477, 2, "VALID"),
            (2400, 117_417, 3, "VALID"),
            (3600, 119_958, 4, "VALID"),
        ],
        output_prefix="source_lap_numbers",
    )

    assert [lap["lap_num"] for lap in data["laps"]] == [4, 5, 6]
    assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx(
        [120.477, 117.417, 119.958]
    )


@pytest.mark.asyncio
async def test_unmatched_timing_boundaries_use_each_shared_time_once_with_rounding_tolerance(
    tmp_path,
):
    manager = SharedSessionManager()
    for lap_num, lap_time in ((1, 10_000.0), (2, 11_000.0)):
        manager._session_data.lap_timing[lap_num] = LapTimingData(
            lap_number=lap_num,
            completed_lap_time=lap_time,
            completed_lap_time_source="logs",
        )
    frames = []
    for frame in range(100):
        frames.append(_frame(frame, frame * 100, 0))
    frames.append(_frame(100, 0, 10_001))
    for frame in range(101, 200):
        frames.append(_frame(frame, (frame - 100) * 100, 10_001))
    frames.append(_frame(200, 0, 11_002))

    _, data = await _analyze(
        TelemetryAnalyzer(str(tmp_path), session_manager=manager),
        frames,
        game_lap_boundaries=[
            (100, 99_000, None, "VALID"),
            (200, 98_000, None, "VALID"),
        ],
        output_prefix="unmatched_timing",
    )

    assert [lap["lap_num"] for lap in data["laps"]] == [4, 5]
    assert [lap["source_lap_num"] for lap in data["laps"]] == [1, 2]
    assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx([10.0, 11.0])


@pytest.mark.asyncio
async def test_cold_start_does_not_apply_unrelated_shared_validity_key(tmp_path):
    manager = SharedSessionManager()
    manager.update_lap_validity_from_graphics_shm(5, True)
    frames = []
    for frame in range(100):
        frames.append(_frame(frame, frame * 100, 0, completed_laps=0))
    frames.append(_frame(100, 0, 10_000, completed_laps=0))
    for frame in range(101, 200):
        frames.append(_frame(frame, (frame - 100) * 100, 10_000, completed_laps=0))
    frames.append(_frame(200, 0, 11_000, completed_laps=0))

    _, data = await _analyze(
        TelemetryAnalyzer(str(tmp_path), session_manager=manager),
        frames,
        game_lap_boundaries=[
            (100, 99_000, None, "VALID"),
            (200, 98_000, None, "VALID"),
        ],
        output_prefix="unrelated_validity",
    )

    assert [lap["source_lap_num"] for lap in data["laps"]] == [None, None]
    assert [lap["is_valid"] for lap in data["laps"]] == [True, True]


@pytest.mark.asyncio
async def test_unnumbered_boundary_cannot_consume_later_explicit_shared_key(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.lap_timing[1] = LapTimingData(
        lap_number=1,
        completed_lap_time=200_000.0,
        completed_lap_time_source="logs",
    )
    frames = []
    for frame in range(100):
        frames.append(_frame(frame, frame * 100, 0))
    frames.append(_frame(100, 0, 200_000))
    for frame in range(101, 200):
        frames.append(_frame(frame, (frame - 100) * 100, 200_000))
    frames.append(_frame(200, 0, 153_000))

    _, data = await _analyze(
        TelemetryAnalyzer(str(tmp_path), session_manager=manager),
        frames,
        game_lap_boundaries=[
            (100, 200_000, None, "VALID"),
            (200, 153_000, 1, "VALID"),
        ],
        output_prefix="reserved_explicit_source",
    )

    assert [lap["source_lap_num"] for lap in data["laps"]] == [None, 1]
    assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx([200.0, 200.0])


def test_shared_time_match_is_single_use_and_rejects_three_ms_rounding_error():
    used = set()
    shared = {1: 10_000.0}

    assert _nearest_shared_lap_by_time(shared, 10_001.0, used) == 1
    used.add(1)
    assert _nearest_shared_lap_by_time(shared, 10_002.0, used) is None
    assert _nearest_shared_lap_by_time({2: 10_000.0}, 10_003.0, set()) is None


@pytest.mark.asyncio
async def test_final_shared_duration_is_used_before_cleaning_metrics(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.lap_timing[1] = LapTimingData(
        lap_number=1,
        completed_lap_time=200_000.0,
        completed_lap_time_source="logs",
    )
    frames = [_frame(frame, frame * 100) for frame in range(1601)]

    _, data = await _analyze(
        TelemetryAnalyzer(str(tmp_path), session_manager=manager),
        frames,
        game_lap_boundaries=[(1600, 153_000, 1, "VALID")],
        output_prefix="final_duration",
    )

    lap = data["laps"][0]
    assert lap["lap_time_s"] == pytest.approx(200.0)
    assert lap["derived_metrics_trustworthy"] is False
    assert lap["max_speed"] is None
    assert lap["avg_speed"] is None


@pytest.mark.asyncio
async def test_real_prompt_keeps_good_laps_and_omits_partial_derived_sections(tmp_path):
    manager = SharedSessionManager()
    for lap_num in (1, 2, 3):
        manager._session_data.lap_timing[lap_num] = LapTimingData(
            lap_number=lap_num,
            completed_lap_time=49_800.0,
            completed_lap_time_source="logs",
        )
    frames = []
    for frame in range(2000):
        timer = (
            (frame % 500) * 100
            if frame < 1000
            else (
                (frame - 1000) * 100
                if frame < 1500
                else (
                    (frame - 1500) * 100
                    if frame < 1800
                    else (frame - 1800) * 100
                )
            )
        )
        frames.append(_frame(frame, timer, fuel=5.0 - frame * 0.001))

    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        track_name="monza",
        game_lap_boundaries=[
            (500, 49_800, 1, "VALID"),
            (1000, 49_800, 2, "VALID"),
            (2000, 49_800, 3, "VALID"),
        ],
        output_prefix="real_mixed_prompt",
    )
    prompt = (tmp_path / "telemetry_real_mixed_prompt_ai_prompt.txt").read_text(
        encoding="utf-8"
    )

    assert result.laps_detected == 3
    assert "CORNER-BY-CORNER ANALYSIS" in prompt
    assert "Lap 3: 0:49.80" in prompt
    assert "Lap 3: corners" not in prompt
    assert "Lap 3: avg_temp=" not in prompt
    assert "Lap 1: corners" in prompt
    assert "Lap 2: corners" in prompt
    assert "Fuel per lap (avg):" in prompt
    assert "Total fuel used:" in prompt
    assert "Est. laps remaining" not in prompt


def _lap(lap_num: int, time: float, *, trusted: bool, value: float) -> dict:
    corner = {
        "id": 1,
        "name": "T1",
        "apex_speed": value,
        "entry_speed": value + 10,
        "exit_speed": value + 5,
        "start_frame": lap_num * 100,
        "end_frame": lap_num * 100 + 20,
        "apex_frame": lap_num * 100 + 10,
        "segment_time_s": 2.0,
        "confidence": 1.0,
        "confidence_label": "high",
        "lap_pos": 0.2,
    }
    return {
        "lap_num": lap_num,
        "lap_time_s": time,
        "lap_time_str": f"1:{time - 60:05.2f}",
        "is_valid": True,
        "derived_metrics_trustworthy": trusted,
        "max_speed": value,
        "avg_speed": value - 20,
        "fuel_used": 1.0,
        "start_frame": lap_num * 100,
        "end_frame": lap_num * 100 + 100,
        "corners": [corner],
        "track": [],
    }


def test_prompt_preserves_fastest_official_lap_but_excludes_partial_from_coaching():
    partial = _lap(1, 90.0, trusted=False, value=999.0)
    trusted = _lap(2, 100.0, trusted=True, value=200.0)
    comparison = _lap(3, 105.0, trusted=True, value=190.0)
    data = {
        "hz": 10.0,
        "laps": [partial, trusted, comparison],
        "best_lap_num": 1,
        "reference_lap_num": 1,
        "comparison_lap_num": 3,
        "comparison_available": True,
        "ref_corners": [trusted["corners"][0]],
        "corner_speeds": {1: {1: 999.0, 2: 200.0, 3: 190.0}},
        "profile_corners": [],
        "analysis_mode": "full",
        "analysis_confidence": "high",
        "track_label": "Test Track",
        "car": "Test Car",
    }
    ctx = PromptContext.from_data(data)

    assert ctx.best_lap["lap_num"] == 1
    assert [lap["lap_num"] for lap in ctx.trusted_valid_laps] == [2, 3]
    assert ctx.coaching_reference_lap["lap_num"] == 2

    session_lines, corner_map = build_session_sections(ctx)
    driving_lines, _ = build_driving_sections(ctx, corner_map)
    prompt = "\n".join(session_lines + driving_lines)
    assert "Lap 1: 1:30.00" in prompt
    assert "999.0" not in prompt
    assert "Lap 2" in prompt
