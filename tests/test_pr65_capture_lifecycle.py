"""PR65 capture warning and lifecycle regressions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.telemetry_capture import FrameData, GameProcessStatus, TelemetryCapture
from src.models import SharedSessionManager
from src.ui.app import SimLapsApp
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService


def _active_frame(frame_number: int) -> FrameData:
    return FrameData(
        timestamp="now",
        frame_number=frame_number,
        physics={},
        graphics={
            "status_name": "AC_LIVE",
            "session_phase": "PRACTICE",
        },
    )


def test_recreated_app_capture_installs_origin_callback():
    app = SimLapsApp.__new__(SimLapsApp)
    app._config = SimpleNamespace(
        telemetry_output_path=r"C:\isolated-telemetry",
        telemetry_debug_logs=False,
        telemetry_enabled=False,
    )
    app._session_manager = MagicMock()
    app._home_page = None
    app._session_lifecycle_service = MagicMock()
    capture = MagicMock()

    with patch("src.ui.app.TelemetryCapture", return_value=capture):
        app._init_telemetry_services()

    capture.set_on_stop_callback.assert_called_once_with(app._on_telemetry_auto_stop)
    capture.set_on_origin_status_callback.assert_called_once_with(app._on_telemetry_origin_status)
    app._session_lifecycle_service.set_telemetry_capture.assert_called_once_with(capture)


@pytest.mark.asyncio
async def test_origin_rejected_frame_is_never_retained() -> None:
    capture = TelemetryCapture(hz=10.0, record_frames=True)
    capture._running = True
    capture._readers = {"physics": SimpleNamespace(size=1, close=lambda: None)}
    capture._capture_origin_ready = lambda: False

    def rejected_frame(_frame_number: int) -> FrameData:
        capture._last_sample_had_data = True
        capture._last_frame_origin_stable = False
        capture._running = False
        return FrameData(timestamp="now", frame_number=0, physics={})

    capture._capture_frame = rejected_frame

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "src.core.telemetry_capture.is_game_running",
            lambda: GameProcessStatus.RUNNING,
        )
        await capture._capture_loop()

    assert capture.get_frames() == []


@pytest.mark.asyncio
async def test_ten_active_origin_rejections_emit_warning_then_one_recovery() -> None:
    manager = SharedSessionManager()
    manager.begin_session("active-session", car_model="BMW M4 GT3")
    capture = TelemetryCapture(
        hz=10.0,
        record_frames=False,
        session_manager=manager,
    )
    capture._capture_origin = manager.get_session_origin()
    capture._capture_id = "capture-under-test"
    capture._running = True
    capture._readers = {"physics": SimpleNamespace(size=1, close=lambda: None)}
    events = []
    capture.set_on_origin_status_callback(events.append)

    def sample(frame_number: int) -> FrameData:
        capture._last_sample_had_data = True
        capture._last_frame_origin_stable = frame_number >= 10
        if frame_number == 10:
            capture._running = False
        return _active_frame(frame_number)

    capture._capture_frame = sample

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "src.core.telemetry_capture.is_game_running",
            lambda: GameProcessStatus.RUNNING,
        )
        await capture._capture_loop()

    await asyncio.sleep(0)
    assert [event.status for event in events] == ["warning", "recovered"]
    assert events[0].capture_id == events[1].capture_id
    assert events[0].generation == events[1].generation


@pytest.mark.asyncio
async def test_origin_rejections_do_not_warn_without_active_session_owner() -> None:
    capture = TelemetryCapture(hz=10.0, record_frames=False)
    capture._running = True
    capture._readers = {"physics": SimpleNamespace(size=1, close=lambda: None)}
    capture._capture_id = "capture-ended"
    events = []
    capture.set_on_origin_status_callback(events.append)

    def sample(frame_number: int) -> FrameData:
        capture._last_sample_had_data = True
        capture._last_frame_origin_stable = False
        if frame_number == 11:
            capture._running = False
        return _active_frame(frame_number)

    capture._capture_frame = sample

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "src.core.telemetry_capture.is_game_running",
            lambda: GameProcessStatus.RUNNING,
        )
        await capture._capture_loop()

    await asyncio.sleep(0)
    assert events == []


@pytest.mark.asyncio
async def test_automatic_capture_loop_exit_invalidates_queued_origin_status() -> None:
    capture = TelemetryCapture(hz=10.0, record_frames=False)
    capture._capture_id = "ending-capture"
    capture._capture_generation = 4
    capture._running = True
    capture._readers = {"physics": SimpleNamespace(size=1, close=lambda: None)}
    events = []
    capture.set_on_origin_status_callback(events.append)
    capture._emit_origin_status("warning")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "src.core.telemetry_capture.is_game_running",
            lambda: GameProcessStatus.NOT_RUNNING,
        )
        await capture._capture_loop()

    assert len(events) == 1
    assert events[0].generation == 4
    assert capture.get_capture_generation() == 5
    home = MagicMock()
    await TelemetryLifecycleService().handle_origin_status(
        event=events[0],
        telemetry_capture=capture,
        home_page=home,
    )
    home.set_telemetry_status.assert_not_called()
