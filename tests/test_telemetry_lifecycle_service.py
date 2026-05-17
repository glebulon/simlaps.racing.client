from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ui.components.telemetry_status import TelemetryStatus
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService


@pytest.mark.asyncio
async def test_start_capture_sets_capturing_and_starts_capture():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = False
    telemetry_capture.start_capture = AsyncMock(return_value=True)

    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=telemetry_capture,
        home_page=home_page,
        telemetry_enabled=True,
    )

    telemetry_capture.start_capture.assert_awaited_once()
    home_page.set_telemetry_status.assert_called_once_with(TelemetryStatus.CAPTURING, 0)


@pytest.mark.asyncio
async def test_stop_capture_runs_analysis_and_sets_complete_status():
    service = TelemetryLifecycleService()

    frames = [{"speed": 100}, {"speed": 120}]
    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = True
    telemetry_capture.stop_capture = AsyncMock(return_value=frames)
    telemetry_capture.get_metadata.return_value = {"car": "Ferrari 296 GT3"}
    telemetry_capture.get_lap_boundaries.return_value = [0, 1]

    telemetry_analyzer = MagicMock()
    telemetry_analyzer.analyze = AsyncMock(
        return_value=SimpleNamespace(
            laps_detected=1,
            best_lap_time=89.55,
            html_path="analysis.html",
            ai_prompt_path="prompt.txt",
        )
    )

    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=False,
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    telemetry_capture.stop_capture.assert_awaited_once_with("session_end")
    telemetry_analyzer.analyze.assert_awaited_once()
    home_page.set_telemetry_status.assert_any_call(TelemetryStatus.ANALYZING, 2)
    home_page.set_telemetry_status.assert_any_call(
        TelemetryStatus.COMPLETE,
        2,
        "analysis.html",
    )


@pytest.mark.asyncio
async def test_handle_auto_stop_with_no_frames_sets_idle():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.get_frames.return_value = []

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.handle_auto_stop(
        reason="heartbeat_timeout",
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    home_page.set_telemetry_status.assert_called_once_with(TelemetryStatus.IDLE)
    telemetry_analyzer.analyze.assert_not_called()


@pytest.mark.asyncio
async def test_start_capture_skipped_when_disabled():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=telemetry_capture,
        home_page=home_page,
        telemetry_enabled=False,
    )

    telemetry_capture.start_capture.assert_not_called()
    home_page.set_telemetry_status.assert_not_called()


@pytest.mark.asyncio
async def test_start_capture_skipped_when_no_capture():
    service = TelemetryLifecycleService()

    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=None,
        home_page=home_page,
        telemetry_enabled=True,
    )

    home_page.set_telemetry_status.assert_not_called()


@pytest.mark.asyncio
async def test_start_capture_skipped_when_already_capturing():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = True

    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=telemetry_capture,
        home_page=home_page,
        telemetry_enabled=True,
    )

    telemetry_capture.start_capture.assert_not_called()
    home_page.set_telemetry_status.assert_not_called()


@pytest.mark.asyncio
async def test_start_capture_failure_sets_error():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = False
    telemetry_capture.start_capture = AsyncMock(return_value=False)

    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=telemetry_capture,
        home_page=home_page,
        telemetry_enabled=True,
    )

    home_page.set_telemetry_status.assert_any_call(TelemetryStatus.ERROR)


@pytest.mark.asyncio
async def test_start_capture_exception_sets_error():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = False
    telemetry_capture.start_capture = AsyncMock(side_effect=RuntimeError("boom"))

    home_page = MagicMock()

    await service.start_capture(
        telemetry_capture=telemetry_capture,
        home_page=home_page,
        telemetry_enabled=True,
    )

    home_page.set_telemetry_status.assert_any_call(TelemetryStatus.ERROR)


@pytest.mark.asyncio
async def test_stop_capture_skipped_when_already_stopped():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = False
    telemetry_capture.get_stop_reason.return_value = "heartbeat_timeout"

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=False,
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    telemetry_capture.stop_capture.assert_not_called()
    telemetry_analyzer.analyze.assert_not_called()
    home_page.set_telemetry_status.assert_not_called()


@pytest.mark.asyncio
async def test_stop_capture_discard_sets_idle():
    service = TelemetryLifecycleService()

    frames = [{"speed": 100}]
    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = True
    telemetry_capture.stop_capture = AsyncMock(return_value=frames)

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=True,
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    telemetry_analyzer.analyze.assert_not_called()
    home_page.set_telemetry_status.assert_called_once_with(TelemetryStatus.IDLE)


@pytest.mark.asyncio
async def test_stop_capture_no_frames_sets_idle():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = True
    telemetry_capture.stop_capture = AsyncMock(return_value=[])

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=False,
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    telemetry_analyzer.analyze.assert_not_called()
    home_page.set_telemetry_status.assert_called_once_with(TelemetryStatus.IDLE)


@pytest.mark.asyncio
async def test_stop_capture_exception_sets_error():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.is_capturing.return_value = True
    telemetry_capture.stop_capture = AsyncMock(side_effect=RuntimeError("boom"))

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=False,
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    home_page.set_telemetry_status.assert_called_once_with(TelemetryStatus.ERROR)


@pytest.mark.asyncio
async def test_stop_capture_missing_capture_or_analyzer():
    service = TelemetryLifecycleService()

    home_page = MagicMock()

    await service.stop_capture(
        reason="session_end",
        discard=False,
        telemetry_capture=None,
        telemetry_analyzer=MagicMock(),
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    home_page.set_telemetry_status.assert_not_called()


@pytest.mark.asyncio
async def test_handle_auto_stop_analysis_error_sets_error():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.get_frames.return_value = [{"speed": 100}]

    telemetry_analyzer = MagicMock()
    telemetry_analyzer.analyze = AsyncMock(side_effect=RuntimeError("analysis failed"))

    home_page = MagicMock()

    await service.handle_auto_stop(
        reason="heartbeat_timeout",
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    home_page.set_telemetry_status.assert_any_call(TelemetryStatus.ERROR)
    telemetry_analyzer.analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_auto_stop_sets_connection_status():
    service = TelemetryLifecycleService()

    telemetry_capture = MagicMock()
    telemetry_capture.get_output_prefix.return_value = "05-10-20-12-00"
    telemetry_capture.get_frames.return_value = []

    telemetry_analyzer = MagicMock()
    home_page = MagicMock()

    await service.handle_auto_stop(
        reason="game_not_running",
        telemetry_capture=telemetry_capture,
        telemetry_analyzer=telemetry_analyzer,
        home_page=home_page,
        current_track_name="Laguna Seca",
    )

    from src.ui.components.status_bar import ConnectionStatus
    home_page.set_connection_status.assert_called_once_with(
        ConnectionStatus.CONNECTED,
        "Session ended (game_not_running)",
    )
