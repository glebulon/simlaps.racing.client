from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models import LapData as SessionLapData
from src.models import SessionData
from src.ui.components.lap_card import LapCardStatus
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _make_deps(
    *, auto_submit: bool = False, submit_invalid_laps: bool = False, telemetry_enabled: bool = False
) -> dict:
    config = AppConfig(
        auto_submit=auto_submit,
        submit_invalid_laps=submit_invalid_laps,
        telemetry_enabled=telemetry_enabled,
    )
    session_manager = MagicMock()
    pb_cache = MagicMock()
    telemetry_capture = None
    history_entries: list = []
    schedule_submission = MagicMock()
    home_page = MagicMock()
    home_page._lap_count = 1
    return dict(
        config=config,
        session_manager=session_manager,
        pb_cache=pb_cache,
        telemetry_capture=telemetry_capture,
        history_entries=history_entries,
        schedule_submission=schedule_submission,
        home_page=home_page,
    )


@pytest.mark.asyncio
async def test_handle_lap_complete_shm_valid_cannot_resurrect_parser_invalid():
    """The parser's verdict is authoritative for completed laps in both
    directions: an SHM "valid" flag must not resurrect a lap the parser
    classified invalid."""
    deps = _make_deps(auto_submit=True, submit_invalid_laps=False)
    deps["session_manager"].get_lap_validity_data.return_value = MagicMock(is_valid=True)
    deps["pb_cache"].check_and_update_pb.return_value = True

    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["home_page"].add_lap.assert_called_once_with(session, lap, LapCardStatus.INVALID)
    deps["schedule_submission"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_ignores_shm_invalid_verdict_in_race_session():
    """Race laps count regardless of contact; SHM is_valid_lap uses hotlap
    semantics (False on contact/damage) and must not suppress a log-valid
    race lap."""
    deps = _make_deps(auto_submit=True, submit_invalid_laps=False)
    deps["session_manager"].get_lap_validity_data.return_value = MagicMock(is_valid=False)
    deps["pb_cache"].check_and_update_pb.return_value = False

    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

    session = SessionData(session_type="RACE", track="Nordschleife", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=441811,
        lap_time_str="7:21.811",
        is_valid=True,
        timestamp="2026-08-08T00:07:34",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["home_page"].add_lap.assert_called_once_with(session, lap, LapCardStatus.SUBMITTING)
    deps["schedule_submission"].assert_called_once()


@pytest.mark.asyncio
async def test_handle_lap_complete_ignores_shm_invalid_verdict_in_practice_session():
    """SHM is_valid_lap cannot distinguish contact from track cuts, and
    contact must never invalidate a lap — the parser verdict wins in
    practice-like sessions too (cut detection comes from the game's
    ``Relevant onSplit`` broadcast in the log)."""
    deps = _make_deps(auto_submit=True, submit_invalid_laps=False)
    deps["session_manager"].get_lap_validity_data.return_value = MagicMock(is_valid=False)
    deps["pb_cache"].check_and_update_pb.return_value = False

    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

    session = SessionData(session_type="PRACTICE", track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=3,
        physics_lap_number=3,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
        timestamp="2026-08-08T00:07:34",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["home_page"].add_lap.assert_called_once_with(session, lap, LapCardStatus.SUBMITTING)
    deps["schedule_submission"].assert_called_once()


@pytest.mark.asyncio
async def test_handle_lap_complete_uses_invalid_status_when_not_submitting_invalid_laps():
    deps = _make_deps(auto_submit=False, submit_invalid_laps=False)
    deps["session_manager"].get_lap_validity_data.return_value = None

    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["home_page"].add_lap.assert_called_once_with(session, lap, LapCardStatus.INVALID)
    deps["schedule_submission"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_rolls_back_history_if_card_creation_fails():
    deps = _make_deps(auto_submit=False)
    deps["session_manager"].get_lap_validity_data.return_value = None
    deps["home_page"].add_lap.side_effect = RuntimeError("ui add failed")

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
            session=session,
            lap=lap,
            create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
            **deps,
        )

    assert deps["history_entries"] == []


@pytest.mark.asyncio
async def test_handle_lap_complete_updates_detected_user_when_player_id_present():
    deps = _make_deps(auto_submit=False)
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["home_page"].set_detected_user.assert_called_once_with("123", "Driver")


@pytest.mark.asyncio
async def test_handle_lap_complete_logs_telemetry_missed_boundary():
    deps = _make_deps(telemetry_enabled=True)
    deps["telemetry_capture"] = MagicMock()
    deps["telemetry_capture"].is_capturing.return_value = False
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["telemetry_capture"].record_lap_boundary.assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_records_telemetry_boundary_when_capturing():
    deps = _make_deps(telemetry_enabled=True)
    deps["telemetry_capture"] = MagicMock()
    deps["telemetry_capture"].is_capturing.return_value = True
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["telemetry_capture"].record_lap_boundary.assert_called_once_with(90000, 1, "VALID")


@pytest.mark.asyncio
async def test_handle_lap_complete_records_structural_outlap_boundary():
    deps = _make_deps(telemetry_enabled=True)
    deps["telemetry_capture"] = MagicMock()
    deps["telemetry_capture"].is_capturing.return_value = True
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SessionLapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=120000,
        lap_time_str="2:00.000",
        lap_type="OUTLAP",
        is_valid=False,
        timestamp="2026-04-29T00:00:00",
    )

    service = LapProcessingService()
    await service.handle_lap_complete(
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["telemetry_capture"].record_lap_boundary.assert_called_once_with(120000, 1, "OUTLAP")
    # The outlap is presented as an invalid lap so it is never silently
    # dropped, but it must not reach PB or submission.
    deps["home_page"].add_lap.assert_called_once()
    assert deps["home_page"].add_lap.call_args.args[2] == LapCardStatus.INVALID
    deps["pb_cache"].check_and_update_pb.assert_not_called()
    deps["schedule_submission"].assert_not_called()
    assert len(deps["history_entries"]) == 1
    assert deps["history_entries"][0].was_valid is False


@pytest.mark.asyncio
async def test_handle_lap_complete_skips_pb_cache_when_unknown_track_or_car():
    deps = _make_deps(auto_submit=False)
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    deps["pb_cache"].check_and_update_pb.assert_not_called()


@pytest.mark.asyncio
async def test_handle_lap_complete_logs_sync_mismatch():
    deps = _make_deps(auto_submit=False)
    deps["session_manager"].get_lap_validity_data.return_value = None
    card = MagicMock()
    deps["home_page"].add_lap.return_value = card
    deps["home_page"]._lap_count = 999  # Force mismatch

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
        session=session,
        lap=lap,
        create_history_entry=lambda **kwargs: SimpleNamespace(**kwargs),
        **deps,
    )

    assert len(deps["history_entries"]) == 1
