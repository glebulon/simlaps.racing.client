"""Integration tests for LogParser + SharedSessionManager wiring."""

import pytest

from src.core.log_parser import LogParser
from src.models import LapData, SessionData, SharedSessionManager


@pytest.mark.asyncio
async def test_emit_lap_updates_shared_session_manager() -> None:
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    session = SessionData(
        session_id="session-42",
        game_version="0.9.3",
        session_type="PRACTICE",
        car="ks_porsche_992_gt3_cup",
        track="spa_francorchamps",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
    )
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=100111,
        lap_time_str="1:40.111",
        sector1_ms=33000,
        sector2_ms=33000,
        sector3_ms=34111,
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    await parser._emit_lap(session, lap)

    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.source == "logs"

    sectors = manager.get_sector_split_data(2)
    assert sectors is not None
    assert sectors.sector1_ms == 33000

    ident = manager.get_player_identification()
    assert ident.steam_id == "76561198321627695"


def test_finalise_current_session_syncs_session_to_shared_manager() -> None:
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    parser.current_session = SessionData(
        session_id="session-84",
        game_version="0.9.4",
        session_type="RACE",
        car="ks_ferrari_296_gt3",
        track="monza",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
    )
    parser.current_session.laps.append(
        LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=110000,
            lap_time_str="1:50.000",
            sector1_ms=36000,
            sector2_ms=36000,
            sector3_ms=38000,
            is_valid=False,
            timestamp="2026-01-01T00:00:00",
        )
    )

    parser._finalise_current_session()

    metadata = manager.get_session_metadata()
    assert metadata["session_id"] == "session-84"
    assert metadata["track"] == "monza"

    validity = manager.get_lap_validity_data(1)
    assert validity is not None
    assert validity.is_valid is False
