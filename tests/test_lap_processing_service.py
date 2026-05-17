from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import SessionData, LapData as SessionLapData
from src.ui.components.lap_card import LapCardStatus
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _make_app(*, auto_submit: bool = False, submit_invalid_laps: bool = False, telemetry_enabled: bool = False) -> SimpleNamespace:
    app = SimpleNamespace()
    app._config = AppConfig(
        auto_submit=auto_submit,
        submit_invalid_laps=submit_invalid_laps,
        telemetry_enabled=telemetry_enabled,
    )
    app._session_manager = MagicMock()
    app._pb_cache = MagicMock()
    app._telemetry_capture = None
    app._history_entries = []
    app._submit_lap = AsyncMock()
    app._home_page = MagicMock()
    app._home_page._lap_count = 1
    app._current_track_name = None
    return app


@pytest.mark.asyncio
async def test_handle_lap_complete_auto_submits_when_shared_validity_overrides_parser_invalid():
    app = _make_app(auto_submit=True, submit_invalid_laps=False)
    app._session_manager.get_lap_validity_data.return_value = MagicMock(is_valid=True)
    app._pb_cache.check_and_update_pb.return_value = True

    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=7,
        physics_lap_number=7,
        lap_time_ms=89556,
        lap_time_str="1:29.556",
        is_valid=False,
        timestamp="2026-04-29T00:21:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._home_page.add_lap.assert_called_once_with(session, lap, LapCardStatus.SUBMITTING)
    app._submit_lap.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_lap_complete_uses_invalid_status_when_not_submitting_invalid_laps():
    app = _make_app(auto_submit=False, submit_invalid_laps=False)
    app._session_manager.get_lap_validity_data.return_value = None

    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=5,
        physics_lap_number=5,
        lap_time_ms=90234,
        lap_time_str="1:30.234",
        is_valid=False,
        timestamp="2026-04-29T00:23:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._home_page.add_lap.assert_called_once_with(session, lap, LapCardStatus.INVALID)
    app._submit_lap.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_lap_complete_rolls_back_history_if_card_creation_fails():
    app = _make_app(auto_submit=False)
    app._session_manager.get_lap_validity_data.return_value = None
    app._home_page.add_lap.side_effect = RuntimeError("ui add failed")

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=8,
        physics_lap_number=8,
        lap_time_ms=88001,
        lap_time_str="1:28.001",
        is_valid=True,
        timestamp="2026-04-29T00:24:00",
    )

    service = LapProcessingService()
    with pytest.raises(RuntimeError, match="ui add failed"):
        await service.handle_lap_complete(
            app=app,
            session=session,
            lap=lap,
            create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        )

    assert app._history_entries == []


@pytest.mark.asyncio
async def test_handle_lap_complete_updates_detected_user_when_player_id_present():
    app = _make_app(auto_submit=False)
    app._session_manager.get_lap_validity_data.return_value = None
    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3", player_id="123", player_name="Driver")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._home_page.set_detected_user.assert_called_once_with("123", "Driver")


@pytest.mark.asyncio
async def test_handle_lap_complete_logs_telemetry_missed_boundary():
    app = _make_app(telemetry_enabled=True)
    app._telemetry_capture = MagicMock()
    app._telemetry_capture.is_capturing.return_value = False
    app._session_manager.get_lap_validity_data.return_value = None
    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._telemetry_capture.record_lap_boundary.assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_records_telemetry_boundary_when_capturing():
    app = _make_app(telemetry_enabled=True)
    app._telemetry_capture = MagicMock()
    app._telemetry_capture.is_capturing.return_value = True
    app._session_manager.get_lap_validity_data.return_value = None
    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._telemetry_capture.record_lap_boundary.assert_called_once_with(90000, 1)


@pytest.mark.asyncio
async def test_handle_lap_complete_skips_pb_cache_when_unknown_track_or_car():
    app = _make_app(auto_submit=False)
    app._session_manager.get_lap_validity_data.return_value = None
    card = MagicMock()
    app._home_page.add_lap.return_value = card

    session = SessionData(track="Unknown", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    app._pb_cache.check_and_update_pb.assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_logs_sync_mismatch():
    app = _make_app(auto_submit=False)
    app._session_manager.get_lap_validity_data.return_value = None
    card = MagicMock()
    app._home_page.add_lap.return_value = card
    app._home_page._lap_count = 999  # Force mismatch

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        app=app,
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    assert len(app._history_entries) == 1
