"""Regression coverage for capture lifetime and finalization ownership."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.core.security import GameProcessStatus
from src.core.telemetry_capture import FrameData, TelemetryCapture
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService


def _stationary_frame(capture: TelemetryCapture, frame_number: int) -> FrameData:
    capture._last_sample_had_data = True
    if frame_number == 1:
        capture._running = False
    return FrameData(
        timestamp="2026-01-01T00:00:00Z",
        frame_number=frame_number,
        physics={"speed_kmh": 0.0},
    )


@pytest.mark.asyncio
async def test_stationary_recording_survives_former_idle_timeout():
    """A recording stays active while the driver is stationary for >120s."""
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    # Keep the independent data-loss watchdog out of this lifetime test.
    capture.HEARTBEAT_TIMEOUT_SECONDS = 1_000.0
    capture._recording_awaiting_boundary = False
    capture._running = True
    capture._readers = {"physics": MagicMock(size=4)}
    seen = []

    def sample(frame_number):
        seen.append(frame_number)
        return _stationary_frame(capture, frame_number)

    # The second loop iteration is 121 seconds after the first without
    # waiting in real time.  The former speed timeout stopped before frame 1.
    perf_counter_calls = 0

    def perf_counter():
        nonlocal perf_counter_calls
        perf_counter_calls += 1
        # asyncio and logging may consult the shared time module while the
        # loop yields, so keep returning the post-timeout value after the
        # transition rather than relying on a fixed call count.
        return 0.0 if perf_counter_calls <= 3 else 121.0

    with (
        patch.object(capture, "_reconnect_missing"),
        patch.object(capture, "_capture_frame", side_effect=sample),
        patch("src.core.telemetry_capture.time.perf_counter", side_effect=perf_counter),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
    ):
        await capture._capture_loop()

    assert seen == [0, 1]
    assert capture.get_stop_reason() is None
    assert capture.get_frame_count() == 2


@pytest.mark.asyncio
async def test_explicit_lifecycle_stop_analyzes_once_for_car_removed():
    """The public lifecycle stop owns analysis even for car_removed."""
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    service = TelemetryLifecycleService()
    frame_ready = asyncio.Event()

    def sample(frame_number):
        capture._last_sample_had_data = True
        frame_ready.set()
        return FrameData(
            timestamp="2026-01-01T00:00:00Z",
            frame_number=frame_number,
            physics={"speed_kmh": 80.0},
            graphics={
                "status_name": "AC_LIVE",
                "completed_laps": 0,
                "current_lap_time_ms": 0,
            },
            static={"session_name": "Race"},
        )

    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(
        return_value=SimpleNamespace(
            laps_detected=0,
            best_lap_time=None,
            html_path=None,
            ai_prompt_path=None,
        )
    )
    callback = Mock()

    # If the capture loop incorrectly treats this explicit reason as an
    # internal stop, this callback would schedule a second analysis.
    async def auto_stop(reason):
        await service.handle_auto_stop(
            reason=reason,
            telemetry_capture=capture,
            telemetry_analyzer=analyzer,
            home_page=MagicMock(),
            current_track_name=None,
        )

    callback.side_effect = auto_stop
    capture.set_on_stop_callback(callback)

    with (
        patch.object(capture, "_connect_regions", return_value={"physics": MagicMock(size=4)}),
        patch.object(capture, "_reconnect_missing"),
        patch.object(capture, "_capture_frame", side_effect=sample),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
    ):
        await capture.start_capture()
        await asyncio.wait_for(frame_ready.wait(), timeout=1.0)
        await service.stop_capture(
            reason="car_removed",
            discard=False,
            telemetry_capture=capture,
            telemetry_analyzer=analyzer,
            home_page=MagicMock(),
            current_track_name=None,
        )
        await asyncio.sleep(0)

    callback.assert_not_called()
    analyzer.analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_stop_flag_resets_before_next_internal_stop():
    """A later capture can notify after an earlier explicit stop."""
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    frame_ready = asyncio.Event()
    callback_event = asyncio.Event()
    callback_reasons = []
    run_number = 0
    second_run_checks = 0

    def callback(reason):
        callback_reasons.append(reason)
        callback_event.set()

    def sample(frame_number):
        capture._last_sample_had_data = True
        frame_ready.set()
        return FrameData(
            timestamp="2026-01-01T00:00:00Z",
            frame_number=frame_number,
            physics={"speed_kmh": 10.0},
            graphics={
                "status_name": "AC_LIVE",
                "completed_laps": 0,
                "current_lap_time_ms": 0,
            },
            static={"session_name": "Race"},
        )

    def process_status():
        nonlocal second_run_checks
        if run_number == 1:
            return GameProcessStatus.RUNNING
        second_run_checks += 1
        return GameProcessStatus.RUNNING if second_run_checks == 1 else GameProcessStatus.NOT_RUNNING

    capture.set_on_stop_callback(callback)
    with (
        patch.object(capture, "_connect_regions", return_value={"physics": MagicMock(size=4)}),
        patch.object(capture, "_reconnect_missing"),
        patch.object(capture, "_capture_frame", side_effect=sample),
        patch("src.core.telemetry_capture.is_game_running", side_effect=process_status),
    ):
        run_number = 1
        await capture.start_capture()
        await asyncio.wait_for(frame_ready.wait(), timeout=1.0)
        await capture.stop_capture("car_removed")
        assert callback_reasons == []

        frame_ready.clear()
        second_run_checks = 0
        run_number = 2
        await capture.start_capture()
        await asyncio.wait_for(callback_event.wait(), timeout=1.0)
        await capture._task

    assert callback_reasons == ["game_not_running"]


@pytest.mark.asyncio
async def test_internal_process_stop_notifies_once():
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    callback = Mock()
    capture.set_on_stop_callback(callback)
    frame_ready = asyncio.Event()

    def sample(frame_number):
        capture._last_sample_had_data = True
        frame_ready.set()
        return FrameData("2026-01-01T00:00:00Z", frame_number, {"speed_kmh": 10.0})

    with (
        patch.object(capture, "_connect_regions", return_value={"physics": MagicMock(size=4)}),
        patch.object(capture, "_reconnect_missing"),
        patch.object(capture, "_capture_frame", side_effect=sample),
        patch(
            "src.core.telemetry_capture.is_game_running",
            side_effect=(GameProcessStatus.RUNNING, GameProcessStatus.NOT_RUNNING),
        ),
    ):
        await capture.start_capture()
        await asyncio.wait_for(frame_ready.wait(), timeout=1.0)
        await capture._task

    callback.assert_called_once_with("game_not_running")


@pytest.mark.asyncio
async def test_internal_mapping_stop_notifies_once():
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    capture._running = True
    capture._readers = {"physics": MagicMock(size=4)}
    capture.DISCONNECT_TIMEOUT_SECONDS = 0.0
    callback = Mock()
    capture.set_on_stop_callback(callback)
    reconnect_calls = 0

    def reconnect(readers):
        nonlocal reconnect_calls
        if reconnect_calls:
            readers.clear()
        reconnect_calls += 1

    def sample(frame_number):
        capture._last_sample_had_data = True
        return FrameData("2026-01-01T00:00:00Z", frame_number, {"speed_kmh": 10.0})

    with (
        patch.object(capture, "_reconnect_missing", side_effect=reconnect),
        patch.object(capture, "_capture_frame", side_effect=sample),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
    ):
        await capture._capture_loop()

    callback.assert_called_once_with("disconnect_timeout (0.0s)")


@pytest.mark.asyncio
async def test_internal_heartbeat_stop_notifies_once():
    capture = TelemetryCapture(hz=1000.0, record_frames=True)
    capture._running = True
    capture._readers = {"physics": MagicMock(size=4)}
    capture._last_valid_frame_time = 0.0
    callback = Mock()
    capture.set_on_stop_callback(callback)

    with (
        patch.object(capture, "_reconnect_missing"),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
    ):
        await capture._capture_loop()

    assert capture.get_stop_reason().startswith("heartbeat_timeout")
    callback.assert_called_once_with(capture.get_stop_reason())
