"""Report finalization must keep the capture's session origin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData
from src.models import SharedSessionManager
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService


def _frame(frame_number: int) -> FrameData:
    return FrameData(
        timestamp=f"2026-09-07T00:00:{frame_number:02d}Z",
        frame_number=frame_number,
        physics={"speed_kmh": 100.0},
        graphics={
            "normalized_car_position": (frame_number % 60) / 60.0,
            "has_authoritative_progress": True,
        },
        static={"track": "Captured Track"},
    )


@pytest.mark.asyncio
async def test_outgoing_capture_ignores_new_manager_identity_and_overlays(tmp_path):
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="BMW")
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW",
            "status_name": "AC_LIVE",
            "session_phase": "RACE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    frozen_origin = manager.get_session_origin()

    manager.begin_session("alfa-session", car_model="Alfa")
    manager.update_lap_timing_from_graphics_shm(
        1,
        {"last_laptime_ms": 99_999},
        completed_lap_num=1,
    )
    manager.update_lap_timing_from_graphics_shm(
        2,
        {"last_laptime_ms": 88_888},
        completed_lap_num=2,
    )
    manager.update_lap_validity_from_graphics_shm(1, False)
    manager.update_lap_validity_from_graphics_shm(2, False)

    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)
    frames = [_frame(i) for i in range(121)]
    boundaries = [
        (60, 60_000, 1, "VALID"),
        (120, 65_000, 2, "INVALID_GAME"),
    ]

    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        result = await analyzer.analyze(
            frames,
            hz=10.0,
            track_name="Test Track",
            game_lap_boundaries=boundaries,
            output_prefix="bmw-outgoing",
            capture_origin=frozen_origin,
        )

    data = html_spy.await_args.args[0]
    assert data["car"] == "BMW"
    assert data["track_name"] == "Captured Track"
    assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx([60.0, 65.0])
    assert [lap["is_valid"] for lap in data["laps"]] == [True, False]
    assert result.best_lap_time == pytest.approx(60.0)
    assert any("earlier session origin" in note for note in data["analysis_notes"])


@pytest.mark.asyncio
async def test_same_epoch_anonymous_origin_uses_late_session_binding(tmp_path):
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW",
            "status_name": "AC_LIVE",
            "session_phase": "RACE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa",
            "status_name": "AC_LIVE",
            "session_phase": "RACE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    anonymous_origin = manager.get_session_origin()
    manager.begin_session("alfa-session", car_model="Alfa")
    manager.update_lap_timing_from_graphics_shm(
        1,
        {"last_laptime_ms": 70_000},
        completed_lap_num=1,
    )

    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            [_frame(i) for i in range(61)],
            hz=10.0,
            track_name="Current Track",
            game_lap_boundaries=[(60, 60_000, 1, "VALID")],
            output_prefix="late-binding",
            capture_origin=anonymous_origin,
        )

    data = html_spy.await_args.args[0]
    assert data["car"] == "Alfa"
    assert data["laps"][0]["lap_time_s"] == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_normal_lifecycle_end_keeps_closed_capture_timing_validity_and_track(tmp_path):
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="BMW")
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW",
            "status_name": "AC_LIVE",
            "session_phase": "RACE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_lap_timing_from_graphics_shm(
        1,
        {"last_laptime_ms": 50_000},
        completed_lap_num=1,
    )
    manager.update_lap_validity_from_graphics_shm(1, True)
    frozen_origin = manager.get_session_origin()
    manager.end_session("bmw-session")

    capture = MagicMock()
    capture.get_output_prefix.return_value = "closed-bmw"
    capture.is_capturing.return_value = True
    capture.stop_capture = AsyncMock(return_value=[
        FrameData(
            timestamp=f"2026-09-07T00:00:{i:02d}Z",
            frame_number=i,
            physics={"speed_kmh": 100.0},
            graphics={
                "normalized_car_position": (i % 60) / 60.0,
                "has_authoritative_progress": True,
            },
            static={},
        )
        for i in range(61)
    ])
    capture.get_metadata.return_value = None
    capture.get_lap_boundaries.return_value = [(60, 60_000, 1, "VALID")]
    capture.get_capture_origin.return_value = frozen_origin
    capture.get_capture_track_name.return_value = "Captured Track"

    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await TelemetryLifecycleService().stop_capture(
            reason="session_end",
            discard=False,
            telemetry_capture=capture,
            telemetry_analyzer=analyzer,
            home_page=MagicMock(),
            current_track_name="New Track",
        )

    data = html_spy.await_args.args[0]
    assert data["car"] == "BMW"
    assert data["track_name"] == "Captured Track"
    assert data["laps"][0]["lap_time_s"] == pytest.approx(50.0)
    assert data["laps"][0]["is_valid"] is False
    assert data["best_lap_num"] is None


@pytest.mark.asyncio
async def test_origin_snapshot_prevents_analyzer_read_race_with_new_session(tmp_path):
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="BMW")
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW",
            "status_name": "AC_LIVE",
            "session_phase": "RACE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_lap_timing_from_graphics_shm(
        1,
        {"last_laptime_ms": 60_000},
        completed_lap_num=1,
    )
    frozen_origin = manager.get_session_origin()
    original_snapshot = manager.get_analysis_snapshot_for_origin

    def race_after_snapshot(origin):
        snapshot = original_snapshot(origin)
        manager.begin_session("alfa-session", car_model="Alfa")
        manager.update_lap_timing_from_graphics_shm(
            1,
            {"last_laptime_ms": 99_999},
            completed_lap_num=1,
        )
        return snapshot

    analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)
    with (
        patch.object(
            manager,
            "get_analysis_snapshot_for_origin",
            side_effect=race_after_snapshot,
        ),
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            [_frame(i) for i in range(61)],
            hz=10.0,
            track_name="Current Track",
            game_lap_boundaries=[(60, 60_000, 1, "VALID")],
            output_prefix="snapshot-race",
            capture_origin=frozen_origin,
        )

    data = html_spy.await_args.args[0]
    assert data["car"] == "BMW"
    assert data["laps"][0]["lap_time_s"] == pytest.approx(60.0)
    assert manager.get_active_session_id() == "alfa-session"
    assert manager.get_session_origin().session_id == "alfa-session"
    assert manager.get_data_for_origin(manager.get_session_origin()).max_speed is None
