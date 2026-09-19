"""Public parser/capture/report flow for outlap and late-result identity."""

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.core.log_parser import LogParser
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData, TelemetryCapture
from src.models import LapState, SessionData, SharedSessionManager
from src.ui.app import SimLapsApp
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _app(manager, telemetry):
    app = SimLapsApp.__new__(SimLapsApp)
    app._config = AppConfig(auto_submit=False, telemetry_enabled=True)
    app._session_manager = manager
    app._pb_cache = MagicMock()
    app._telemetry_capture = telemetry
    app._history_entries = []
    app._history_entry_by_lap_id = {}
    app._lap_processing_service = LapProcessingService()
    app._home_page = MagicMock()
    app._home_page._lap_count = 0
    app.page = MagicMock()
    app._current_track_name = None

    def add_lap(*_args):
        app._home_page._lap_count += 1
        return MagicMock()

    app._home_page.add_lap.side_effect = add_lap
    return app


def _frame(frame_number: int, lap_frame: int, lap_frames: int, fuel: float) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_number,
        physics={"speed_kmh": 100.0, "gear": 3, "fuel": fuel, "quality_score": 1.0},
        graphics={
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "normalized_car_position": lap_frame / max(lap_frames - 1, 1),
            "has_authoritative_progress": True,
            "quality_score": 1.0,
            "current_time_ms": lap_frame * 100,
            "last_time_ms": 0,
            "completed_laps": 0,
            "is_valid_lap": True,
            "is_in_pit_lane": False,
        },
    )


def _lap_frames(start: int, lap_time_ms: int, fuel_start: float) -> list[FrameData]:
    count = round(lap_time_ms / 1000 * 10) + 1
    return [
        _frame(
            start + index,
            index,
            count,
            fuel_start - index / max(count - 1, 1),
        )
        for index in range(count)
    ]


def _shm_completion(manager: SharedSessionManager, completed_laps: int, lap_time_ms: int, valid: bool) -> None:
    manager.update_from_graphics_shm(
        {
            "status_name": "AC_LIVE",
            "total_lap_count": completed_laps - 1,
            "current_lap_time_ms": lap_time_ms,
            "last_laptime_ms": 0,
            "is_valid_lap": valid,
        }
    )
    manager.update_from_graphics_shm(
        {
            "status_name": "AC_LIVE",
            "total_lap_count": completed_laps,
            "current_lap_time_ms": 50,
            "last_laptime_ms": lap_time_ms,
            "is_valid_lap": True,
        }
    )


def _log_lines(car_id: str) -> str:
    return "\n".join(
        (
            "[2026-09-18 12:00:00.000] [gameplay] [info] Outplap split",
            "[2026-09-18 12:00:01.000] [gameplay] [error] Couldn't create lap from opensplits",
            "[2026-09-18 12:01:00.000] [physics] [info] Lap test evOnLapCompleted 2 completed",
            f"[2026-09-18 12:01:00.100] [gameplay] [info] New lap carId {car_id}: 01:20.000",
            "[2026-09-18 12:01:00.200] [network] [info] Relevant onSplit for Combo 1@2: "
            "laptime 80000, valid false, flags 1, lap 1 (prev 0)",
            "[2026-09-18 12:03:00.000] [physics] [info] Lap test evOnLapCompleted 3 completed",
            f"[2026-09-18 12:03:00.100] [gameplay] [info] New lap carId {car_id}: 01:50.373",
            "[2026-09-18 12:03:00.200] [network] [info] Relevant onSplit for Combo 1@2: "
            "laptime 110373, valid false, flags 1, lap 2 (prev 1)",
            "[2026-09-18 12:05:00.000] [physics] [info] Lap test evOnLapCompleted 4 completed",
            f"[2026-09-18 12:05:00.100] [gameplay] [info] New lap carId {car_id}: 01:46.698",
            "[2026-09-18 12:05:00.200] [network] [info] Relevant onSplit for Combo 1@2: "
            "laptime 106698, valid true, flags 2, lap 3 (prev 2)",
        )
    ) + "\n"


@pytest.mark.asyncio
async def test_public_outlap_invalid_valid_report_keeps_boundaries_and_identity(tmp_path):
    """Drive SHM, parser, app, capture, and analyzer through one recording."""
    manager = SharedSessionManager()
    for current_time in (10_000, 11_000):
        manager.update_from_graphics_shm(
            {
                "status_name": "AC_LIVE",
                "total_lap_count": 0,
                "current_lap_time_ms": current_time,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
            }
        )
    for number, (lap_time, valid) in enumerate(((80_000, False), (110_373, False), (106_698, True)), start=1):
        _shm_completion(manager, number, lap_time, valid)

    telemetry = TelemetryCapture(hz=10.0, record_frames=True)
    telemetry._running = True
    telemetry._recording_awaiting_boundary = False
    app = _app(manager, telemetry)
    session = SessionData(
        track="test-track",
        car="test-car",
        car_uuid="a5e-ca2",
        session_type="PRACTICE",
    )
    car_id = "a5e-ca2"
    chunks = [
        _lap_frames(0, 80_000, 10.0),
        _lap_frames(801, 110_373, 9.0),
        _lap_frames(1905, 106_698, 8.0),
    ]
    callback_index = 0

    async def on_lap_complete(callback_session, lap):
        nonlocal callback_index
        telemetry._frames.extend(chunks[callback_index])
        callback_index += 1
        await app._on_lap_complete(callback_session, lap)

    log_file = tmp_path / "outlap-invalid-valid.log"
    log_file.write_text(_log_lines(car_id), encoding="utf-8")
    parser = LogParser(
        log_path=str(log_file),
        on_lap_complete=on_lap_complete,
        session_manager=manager,
    )
    parser.current_session = session
    parser.context.car_uuid = car_id

    sessions = await parser.parse_file()
    assert len(sessions) == 1
    assert [lap.lap_state for lap in session.laps] == [LapState.OUTLAP, LapState.INVALID_GAME, LapState.VALID]
    assert [lap.lap_time_ms for lap in session.laps] == [80_000, 110_373, 106_698]
    assert callback_index == 3
    assert [entry.lap_time_ms for entry in app._history_entries] == [80_000, 110_373, 106_698]

    boundaries = telemetry.get_lap_boundaries()
    assert [boundary.lap_time_ms for boundary in boundaries] == [80_000, 110_373, 106_698]
    assert len({boundary.result_id for boundary in boundaries}) == 3
    invalid = session.laps[1]
    original_boundary = next(boundary for boundary in boundaries if boundary.result_id == invalid.result_id)

    late = replace(invalid, lap_number=9, timestamp="2026-09-18T12:06:00+00:00")
    await app._on_lap_update(session, late)
    updated_boundary = next(
        boundary for boundary in telemetry.get_lap_boundaries() if boundary.result_id == invalid.result_id
    )
    assert updated_boundary.frame_index == original_boundary.frame_index
    assert updated_boundary.lap_number == 9
    assert app._history_entries[1].lap_time_ms == 110_373

    manager.update_lap_timing_from_graphics_shm(
        9,
        {"last_laptime_ms": 111_147},
    )
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    result = await analyzer.analyze(
        telemetry.get_frames(),
        hz=10.0,
        game_lap_boundaries=telemetry.get_lap_boundaries(),
        output_prefix="outlap_identity",
    )
    assert result.laps_detected == 2
    assert result.best_lap_time == pytest.approx(106.698, abs=0.001)
    html = (tmp_path / "telemetry_outlap_identity.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (.*);\nconst LAP_COLORS", html).group(1))
    assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx([110.373, 106.698], abs=0.001)
    assert [lap["result_id"] for lap in data["laps"]] == [invalid.result_id, session.laps[2].result_id]
    assert [lap["is_valid"] for lap in data["laps"]] == [False, True]
    assert all(lap["fuel_used"] is not None for lap in data["laps"])
    assert all(0.9 <= lap["fuel_used"] <= 1.1 for lap in data["laps"])
    assert [call.args[1].lap_time_ms for call in app._home_page.add_lap.call_args_list] == [80_000, 110_373, 106_698]
