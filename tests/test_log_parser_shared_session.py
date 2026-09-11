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
    # Log verdict is always authoritative for completed laps — SHM
    # is_valid_lap cannot distinguish contact from track cuts.
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


# ── Regression: _handle_session_start resets shared session ────────────────


def test_handle_session_start_resets_shared_session_after_finalising() -> None:
    """Regression: ``_handle_session_start`` must call ``reset()`` on the
    shared session manager AFTER finalising the old session and BEFORE syncing
    the new one.  This prevents stale lap timing/validity data from the
    previous session contaminating the new session.
    """
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    # Set up a "previous" session with lap data already in the shared manager.
    old_session = SessionData(
        session_id="old-session",
        car="old_car",
        track="old_track",
    )
    old_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90000,
        lap_time_str="1:30.000",
        is_valid=True,
    )
    parser.current_session = old_session
    parser.current_session.laps.append(old_lap)
    # Push old session data into shared manager (simulates what
    # _finalise_current_session does).
    manager.update_from_logs(old_session)
    # Verify old data is in shared manager.
    assert manager.get_lap_timing_data(1) is not None
    assert manager.get_best_lap_time() == 90000.0

    # Simulate a "Game Started!" line for a new session.
    # Use a realistic format that will match the regex.
    line = (
        "[2026-07-20 12:00:00] [gameplay] [info] Game Started! "
        "GameModeType_PRACTICE | Monza | ks_ferrari_296_gt3 | "
        "GameModeSelectionWeatherType_Clear"
    )
    result = parser._handle_session_start(line)

    # Session must have been created.
    assert result is True
    assert parser.current_session is not None
    assert parser.current_session.car == "ks_ferrari_296_gt3"

    # Stale lap timing from old session must be cleared.
    assert manager.get_best_lap_time() is None, "Stale best lap from old session should have been cleared by reset()"
    assert manager.get_lap_timing_data(1) is None, (
        "Stale lap timing from old session should have been cleared by reset()"
    )


def test_start_new_session_resets_shared_session() -> None:
    """Regression: ``_start_new_session`` (fallback session creator) must
    call ``reset()`` on the shared session manager before syncing.
    """
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    # Pre-populate shared manager with stale data.
    old_session = SessionData(
        session_id="stale-session",
        car="stale_car",
        track="stale_track",
    )
    old_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=85000,
        lap_time_str="1:25.000",
        is_valid=True,
    )
    parser.current_session = old_session
    parser.current_session.laps.append(old_lap)
    manager.update_from_logs(old_session)
    assert manager.get_best_lap_time() == 85000.0

    # Call the fallback session creator.
    parser._start_new_session("PRACTICE", "")

    # Stale data must be gone.
    assert manager.get_best_lap_time() is None, (
        "Stale best lap should have been cleared by reset() in _start_new_session"
    )


def test_handle_session_start_regex_mismatch_still_resets() -> None:
    """Regression: when the 'Game Started!' regex does NOT match (e.g. game
    version changes the line format), ``_handle_session_start`` must still
    call ``reset()`` to clear stale data, even though it cannot create a
    new parser session.
    """
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    # Pre-populate shared manager with stale data.
    old_session = SessionData(
        session_id="stale-session",
        car="stale_car",
        track="stale_track",
    )
    old_lap = LapData(
        lap_number=1,
        physics_lap_number=None,
        lap_time_ms=80000,
        lap_time_str="1:20.000",
        is_valid=True,
    )
    parser.current_session = old_session
    parser.current_session.laps.append(old_lap)
    manager.update_from_logs(old_session)
    assert manager.get_best_lap_time() == 80000.0

    # A "Game Started!" line that does NOT match the regex (unrecognized format).
    bad_line = (
        "[2026-07-20 12:00:00] [gameplay] [info] Game Started! "
        "NewFormatType_UNKNOWN | Some Track | some_car | WeatherType_Sunny"
    )
    result = parser._handle_session_start(bad_line)

    # Session must NOT have been created (regex mismatch).
    assert result is False
    # But stale shared session data MUST still be cleared.
    assert manager.get_best_lap_time() is None, (
        "Stale best lap should have been cleared by reset() even when regex does not match"
    )
