"""Public capture-to-log callback regressions for terminal graphics frames."""

import asyncio
import struct
from unittest.mock import MagicMock

import pytest

from src.core.log_parser import LogParser
from src.core.telemetry_capture import TelemetryCapture
from src.models import SessionData, SharedSessionManager
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _graphics_frame(
    *,
    phase: str,
    current_lap_time_ms: int,
    total_lap_count: int,
    last_laptime_ms: int,
) -> bytes:
    data = bytearray(4096)
    struct.pack_into("<i", data, 4, 2)  # AC_LIVE
    struct.pack_into("<f", data, 1244, 0.5)  # authoritative npos
    struct.pack_into("<i", data, 188, current_lap_time_ms)
    struct.pack_into("<i", data, 2384, total_lap_count)
    struct.pack_into("<i", data, 2396, last_laptime_ms)
    data[2476 : 2476 + len(phase)] = phase.encode("ascii")
    data[3121] = 1
    return bytes(data)


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "expect_callback"), [("Ended", False), ("Session", True)])
async def test_capture_to_log_callback_ignores_terminal_shm_completion(
    tmp_path,
    phase: str,
    expect_callback: bool,
) -> None:
    """Raw graphics transitions reach the parser without phantom results."""
    manager = SharedSessionManager()
    emitted = []

    async def on_lap(session, lap):
        emitted.append(lap)

    log_file = tmp_path / "session.log"
    log_file.write_text("", encoding="utf-8")
    parser = LogParser(
        log_path=str(log_file),
        on_lap_complete=on_lap,
        session_manager=manager,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0.0
    parser.current_session = SessionData(
        track="monza",
        car="ks_ferrari_296_gt3",
        session_type="RACE",
    )

    reader = type("Reader", (), {})()
    reader.size = 4096
    reader.read_raw = lambda: frames.pop(0)
    reader.close = lambda: None
    frames = [
        _graphics_frame(
            phase="Session",
            current_lap_time_ms=75_000,
            total_lap_count=0,
            last_laptime_ms=0,
        ),
        _graphics_frame(
            phase="Session",
            current_lap_time_ms=76_000,
            total_lap_count=0,
            last_laptime_ms=0,
        ),
        _graphics_frame(
            phase=phase,
            current_lap_time_ms=50,
            total_lap_count=1,
            last_laptime_ms=75_684,
        ),
    ]
    capture = TelemetryCapture(hz=10.0, session_manager=manager)
    capture._readers = {"graphics": reader}

    follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        await asyncio.sleep(0.03)
        capture._capture_frame(0)
        capture._capture_frame(1)
        capture._capture_frame(2)
        await asyncio.sleep(0.05)
    finally:
        parser.stop()
        await asyncio.wait_for(follow_task, timeout=1.0)

    if expect_callback:
        assert [lap.lap_time_ms for lap in emitted] == [75_684]
    else:
        assert emitted == []
        assert manager.get_lap_completions_after(0.0) == []
        assert manager.get_latest_lap_completion() is None


@pytest.mark.asyncio
async def test_stale_shm_transition_keeps_parser_services_and_capture_armed(tmp_path) -> None:
    """Startup and same-car restart timing cannot release or process a lap."""
    manager = SharedSessionManager()
    emitted = []
    home = MagicMock()
    submissions = MagicMock()
    history = []
    service = LapProcessingService()
    telemetry = TelemetryCapture(hz=10.0, session_manager=manager, record_frames=True)
    telemetry._running = True
    telemetry._recording_awaiting_boundary = True
    frames = [
        _graphics_frame(phase="Session", current_lap_time_ms=90_000, total_lap_count=0, last_laptime_ms=0),
        _graphics_frame(phase="Session", current_lap_time_ms=50, total_lap_count=1, last_laptime_ms=90_000),
        _graphics_frame(phase="Session", current_lap_time_ms=90_000, total_lap_count=0, last_laptime_ms=0),
        _graphics_frame(phase="Session", current_lap_time_ms=50, total_lap_count=1, last_laptime_ms=90_000),
    ]

    class Reader:
        size = 4096

        def read_raw(self):
            return frames.pop(0)

        def close(self):
            pass

    telemetry._readers = {"graphics": Reader()}
    log_file = tmp_path / "session.log"
    log_file.write_text("", encoding="utf-8")

    async def on_lap(session, lap):
        emitted.append(lap)
        await service.handle_lap_complete(
            session=session,
            lap=lap,
            home_page=home,
            telemetry_capture=telemetry,
            config=AppConfig(auto_submit=True, telemetry_enabled=True),
            session_manager=manager,
            pb_cache=MagicMock(),
            history_entries=history,
            schedule_submission=submissions,
            create_history_entry=lambda **values: MagicMock(**values),
        )

    parser = LogParser(log_path=str(log_file), on_lap_complete=on_lap, session_manager=manager)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0.0
    parser.current_session = SessionData(track="monza", car="test-car", session_type="PRACTICE", car_uuid="same-car")
    follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        for frame_number in range(2):
            frame = telemetry._capture_frame(frame_number)
            telemetry._start_recording_at_timing_boundary(frame)
        await parser._emit_session_restart()
        telemetry._recording_awaiting_boundary = True
        for frame_number in range(2, 4):
            frame = telemetry._capture_frame(frame_number)
            telemetry._start_recording_at_timing_boundary(frame)
        await asyncio.sleep(0.1)
    finally:
        parser.stop()
        await asyncio.wait_for(follow_task, timeout=1.0)

    assert emitted == []
    home.add_lap.assert_not_called()
    submissions.assert_not_called()
    assert history == []
    assert telemetry._recording_awaiting_boundary is True
