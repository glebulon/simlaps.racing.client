"""Tests for shared session data infrastructure."""

import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from src.models import (
    LapData,
    LapState,
    SessionData,
    SharedSessionManager,
)


def test_update_from_logs_reuses_metadata_sync_and_processes_laps() -> None:
    manager = SharedSessionManager()
    metadata_sync = MagicMock(wraps=manager.update_session_metadata_from_logs)
    manager.update_session_metadata_from_logs = metadata_sync
    session = SessionData(
        session_id="session-logs",
        game_version="0.9.4",
        session_type="RACE",
        car="ks_ferrari_296_gt3",
        track="monza",
        weather="clear",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="player-car",
        laps=[
            LapData(
                lap_number=1,
                physics_lap_number=1,
                lap_time_ms=110000,
                lap_time_str="1:50.000",
                is_valid=True,
            )
        ],
    )

    manager.update_from_logs(session)

    metadata_sync.assert_called_once_with(session)
    metadata = manager.get_session_metadata_data()
    assert metadata.session_id == "session-logs"
    assert metadata.game_version == "0.9.4"
    assert metadata.session_type == "RACE"
    assert metadata.track == "monza"
    assert metadata.weather == "clear"

    identity = manager.get_player_identification()
    assert identity.steam_id == "76561198321627695"
    assert identity.player_name == "Driver"
    assert identity.car_uuid == "player-car"
    assert identity.car_model == "ks_ferrari_296_gt3"

    sources = manager.get_data_sources()
    assert sources["game_version"] == {"logs"}
    assert sources["session_type"] == {"logs"}
    assert sources["track"] == {"logs"}

    timing = manager.get_lap_timing_data(1)
    assert timing is not None
    assert timing.completed_lap_time == 110000.0


def test_update_from_graphics_sets_timing_and_fuel() -> None:
    manager = SharedSessionManager()

    manager.update_from_graphics_shm(
        {
            "session_current_lap": 3,
            "current_lap_time_ms": 61234,
            "last_laptime_ms": 120123,
            "best_laptime_ms": 119999,
            "ideal_laptime_ms": 119800,
            "delta_time_ms": -124,
            "timing_is_invalid": True,
            "fuel_liter_current_quantity": 21.5,
            "fuel_liter_per_km": 2.34,
            "km_per_fuel_liter": 0.42,
        }
    )

    # SHM validity flags are now wired into shared session.
    lap_validity = manager.get_lap_validity_data(3)
    assert lap_validity is not None
    assert lap_validity.is_valid is False
    assert lap_validity.lap_state == "INVALID_GAME"
    assert lap_validity.source == "shm_graphics"

    lap_timing = manager.get_lap_timing_data(3)
    assert lap_timing is not None
    assert lap_timing.current_lap_time_ms == 61234
    assert lap_timing.last_lap_time_ms == 120123

    fuel = manager.get_fuel_data()
    assert fuel.current_fuel == 21.5
    assert fuel.fuel_consumption_rate == 2.34
    assert fuel.fuel_economy == 0.42


def test_graphics_lap_counter_transition_snapshots_completed_lap() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 75000,
            "last_laptime_ms": 0,
            "is_valid_lap": False,
        }
    )
    assert manager.get_latest_lap_completion() is None

    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 50,
            "last_laptime_ms": 75684,
            "is_valid_lap": True,
        }
    )

    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.completed_laps == 1
    assert completion.lap_time_ms == 75684
    assert completion.is_valid is False
    assert manager.get_all_lap_times() == {1: 75684.0}
    assert manager.get_lap_time(2) is None


def test_graphics_terminal_transition_does_not_publish_shutdown_completion() -> None:
    """Ended timing/counter snapshots are teardown, not finish-line events."""
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 75000,
            "last_laptime_ms": 0,
            "is_valid_lap": False,
            "session_phase": "Session",
        }
    )

    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 50,
            "last_laptime_ms": 75684,
            "is_valid_lap": True,
            "session_phase": "Ended",
        }
    )

    assert manager.get_latest_lap_completion() is None
    assert manager.get_lap_completions_after(0.0) == []
    assert manager._session_data.active_lap_is_valid is None
    assert manager.get_lap_timing_data(2).last_lap_time_ms == 0


def test_graphics_active_transition_still_publishes_completion() -> None:
    """A matching transition in an active session remains a live completion."""
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 75000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
            "session_phase": "Session",
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 50,
            "last_laptime_ms": 75684,
            "is_valid_lap": True,
            "session_phase": "Session",
        }
    )

    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.lap_time_ms == 75684


def test_graphics_disqualified_and_teardown_states_suppress_completion() -> None:
    for phase in ("Disqualified", "Teardown"):
        manager = SharedSessionManager()
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 75000,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
                "session_phase": "Session",
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 50,
                "last_laptime_ms": 75684,
                "is_valid_lap": True,
                "session_phase": phase,
            }
        )
        assert manager.get_latest_lap_completion() is None


def test_graphics_timer_reset_snapshots_invalid_lap_before_counter_advances() -> None:
    """ACE resets timing/validity before its completed-lap counter changes."""
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 87500,
            "last_laptime_ms": 0,
            "is_valid_lap": False,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 75,
            "last_laptime_ms": 87570,
            "is_valid_lap": True,
        }
    )

    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.lap_time_ms == 87570
    assert completion.is_valid is False

    # The delayed counter transition must not replace the same completion
    # with the new lap's valid=True state.
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 2,
            "current_lap_time_ms": 3000,
            "last_laptime_ms": 87570,
            "is_valid_lap": True,
        }
    )
    assert manager.get_latest_lap_completion() == completion


def test_delayed_counter_echo_keeps_next_lap_invalidation_latched() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 87_500,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 75,
            "last_laptime_ms": 87_570,
            "is_valid_lap": True,
        }
    )

    # The completed counter arrives seconds later, after the new lap has been
    # invalidated. It is an echo of the prior reset and must not reset the
    # active validity latch back to True.
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 2,
            "current_lap_time_ms": 3_000,
            "last_laptime_ms": 87_570,
            "is_valid_lap": False,
        }
    )
    validity = manager.get_lap_validity_data(3)
    assert validity is not None
    assert validity.is_valid is False


def test_counter_jump_publishes_later_completion_after_missed_echo() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 90_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 50,
            "last_laptime_ms": 90_000,
            "is_valid_lap": True,
        }
    )

    # The counter skipped the expected value because the intermediate sample
    # was absent. This is a new completion, despite the outstanding echo
    # marker from the first timer reset.
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 2,
            "current_lap_time_ms": 3_000,
            "last_laptime_ms": 90_000,
            "is_valid_lap": True,
        }
    )
    completions = manager.get_lap_completions_after(0.0)
    assert [item.lap_time_ms for item in completions] == [90_000, 90_000]


def test_zero_completed_first_lap_delayed_counter_does_not_duplicate_or_reset_validity() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 90_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 100,
            "last_laptime_ms": 90_000,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 2_500,
            "last_laptime_ms": 90_000,
            "is_valid_lap": False,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 4_000,
            "last_laptime_ms": 90_000,
            "is_valid_lap": True,
        }
    )

    assert len(manager.get_lap_completions_after(0.0)) == 1
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is False


def test_graphics_outlap_timer_reset_clears_validity_without_completed_time() -> None:
    """An invalid pit outlap must not contaminate the first timed lap."""
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 62000,
            "last_laptime_ms": 0,
            "is_valid_lap": False,
        }
    )

    # ACE resets the timer at the outlap boundary but does not publish the
    # outlap as last_laptime_ms or advance its completed-lap counter.
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 86,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    assert manager.get_latest_lap_completion() is None

    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 67000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 118,
            "last_laptime_ms": 67074,
            "is_valid_lap": True,
        }
    )

    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.lap_time_ms == 67074
    assert completion.is_valid is True


def test_graphics_completion_validity_survives_physical_counter_reuse_after_pit() -> None:
    """A new stint must not inherit a frozen verdict for the same SHM lap key."""
    manager = SharedSessionManager()
    prior_lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=70290,
        lap_time_str="01:10.290",
        is_valid=True,
        lap_state=LapState.VALID,
        lap_type="VALID",
        timestamp="2026-08-16T14:48:32.770",
    )
    manager.update_lap_from_logs(prior_lap)

    # ACE returns to the pits and later reuses physical lap 2. The old keyed
    # log verdict is frozen, but the independent live latch tracks this stint.
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 83000,
            "last_laptime_ms": 0,
            "is_valid_lap": False,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 80,
            "last_laptime_ms": 84057,
            "is_valid_lap": True,
        }
    )

    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.lap_time_ms == 84057
    assert completion.is_valid is False


def test_graphics_retains_multiple_unconsumed_lap_completions() -> None:
    """Delayed log polling must not collapse a multi-lap SHM backlog."""
    manager = SharedSessionManager()

    for completed_laps, lap_time_ms, is_valid in (
        (1, 66393, True),
        (2, 65559, False),
    ):
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps - 1,
                "current_lap_time_ms": lap_time_ms - 50,
                "last_laptime_ms": 0,
                "is_valid_lap": is_valid,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps,
                "current_lap_time_ms": 50,
                "last_laptime_ms": lap_time_ms,
                "is_valid_lap": True,
            }
        )

    completions = manager.get_lap_completions_after(0.0)
    assert [completion.lap_time_ms for completion in completions] == [66393, 65559]
    assert [completion.is_valid for completion in completions] == [True, False]


def test_lap_completion_matching_tolerates_rounding_once_and_rejects_outside() -> None:
    """Cross-source timing drift is accepted once, but never reused."""
    manager = SharedSessionManager()
    manager.update_from_graphics_shm({
        "total_lap_count": 0,
        "current_lap_time_ms": 100000,
        "last_laptime_ms": 0,
        "is_valid_lap": False,
    })
    manager.update_from_graphics_shm({
        "total_lap_count": 1,
        "current_lap_time_ms": 10,
        "last_laptime_ms": 100000,
        "is_valid_lap": True,
    })

    completion = manager.get_lap_completion_by_time(100001, consume=True)
    assert completion is not None
    assert completion.lap_time_ms == 100000
    assert manager.get_lap_completion_by_time(100001) is None
    assert manager.get_lap_completion_by_time(100000 + 3) is None


def test_graphics_publishes_equal_and_near_equal_consecutive_completions() -> None:
    """Physical timer resets own boundaries even when lap times round alike."""
    manager = SharedSessionManager()
    for lap_time_ms, completed_laps in (
        (100_000, 1),
        (100_001, 2),
        (100_002, 3),
    ):
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps - 1,
                "current_lap_time_ms": 100_000,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps,
                "current_lap_time_ms": 10,
                "last_laptime_ms": lap_time_ms,
                "is_valid_lap": True,
            }
        )

    completions = manager.get_lap_completions_after(0.0)
    assert [item.lap_time_ms for item in completions] == [100_000, 100_001, 100_002]
    assert len({id(item) for item in completions}) == 3


def test_hybrid_capabilities_survive_same_car_reset_and_clear_on_car_change() -> None:
    manager = SharedSessionManager()
    manager.update_player_identification_from_logs({"car_uuid": "car-a"})
    manager.update_from_static_shm(
        {"car_uuid": "car-a", "has_ers": True, "has_kers": False}
    )
    assert manager.get_hybrid_flags() == (True, False)

    manager.reset()
    assert manager.get_hybrid_flags() == (True, False)

    manager.update_player_identification_from_logs({"car_uuid": "car-b"})
    assert manager.get_hybrid_flags() == (None, None)


def test_update_lap_from_logs_populates_player_and_sector_data() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-1",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="1:40.000",
        sector1_ms=32000,
        sector2_ms=33000,
        sector3_ms=35000,
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)

    ident = manager.get_player_identification()
    assert ident.steam_id == "76561198321627695"
    assert ident.car_uuid == "car-uuid"
    assert ident.player_name == "Driver"

    sectors = manager.get_sector_split_data(1)
    assert sectors is not None
    assert sectors.sector1_ms == 32000
    assert sectors.sector2_ms == 33000
    assert sectors.sector3_ms == 35000


def test_log_heuristic_valid_overwrites_shm_invalid() -> None:
    """SHM is_valid_lap cannot distinguish contact from track cuts, and
    contact must never invalidate a lap.  The log verdict (even heuristic
    VALID) always wins for completed laps."""
    manager = SharedSessionManager()

    # SHM reports lap 2 as invalid (per-frame flag while lap was in progress)
    manager.update_from_graphics_shm({
        "session_current_lap": 2,
        "is_invalid": True,
    })
    assert manager.get_lap_validity(2) is False

    # Log parser emits lap 2 with heuristic VALID (no authoritative onSplit)
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=True,
        lap_state=LapState.VALID,
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Log verdict wins — SHM contact-based invalidity must not persist.
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.lap_state == "VALID"
    assert validity.source == "logs"


def test_log_heuristic_valid_wins_over_shm_invalid() -> None:
    """AC Evo 0.8.0.1: session_current_lap is 0, so current lap is derived from
    total_lap_count.  When total_lap_count=1 and is_valid_lap=False with an
    active current_lap_time_ms, the in-progress lap is lap 2.  SHM
    is_valid_lap cannot distinguish contact from track cuts, and contact must
    never invalidate a lap, so the log verdict (even heuristic VALID) always
    wins for completed laps.
    """
    manager = SharedSessionManager()

    # Lap 1 finishes; lap 2 starts and is immediately invalidated per SHM.
    manager.update_from_graphics_shm({
        "session_current_lap": 0,
        "total_lap_count": 1,
        "is_valid_lap": False,
        "current_lap_time_ms": 44573,
    })

    # SHM must derive lap 2 and mark it invalid while in progress.
    assert manager.get_lap_validity(2) is False
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.lap_state == "INVALID_GAME"
    assert validity.source == "shm_graphics"

    # Log parser emits lap 2 with heuristic VALID (no Relevant onSplit).
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=54453,
        lap_time_str="0:54.453",
        is_valid=True,
        lap_state=LapState.VALID,
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Log verdict wins — SHM contact-based invalidity must not persist.
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.lap_state == "VALID"
    assert validity.source == "logs"


def test_log_authoritative_invalid_not_overwritten_by_shm_valid() -> None:
    """Log parser authoritative INVALID_GAME must not be overwritten by SHM VALID."""
    manager = SharedSessionManager()

    # Log parser emits lap 1 with authoritative INVALID_GAME.
    # In production INVALID_GAME is always paired with
    # validity_source="authoritative" because it comes from the game's
    # ``Relevant onSplit`` broadcast.
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=False,
        lap_state=LapState.INVALID_GAME,
        lap_type="INVALID_GAME",
        validity_source="authoritative",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)
    assert manager.get_lap_validity(1) is False

    # SHM later reports lap 1 as valid
    manager.update_lap_validity_from_graphics_shm(1, is_invalid=False)

    # Log verdict must be preserved
    validity = manager.get_lap_validity_data(1)
    assert validity is not None
    assert validity.is_valid is False
    assert validity.lap_state == "INVALID_GAME"
    assert validity.source == "logs"


def test_shm_valid_does_not_block_log_outlap() -> None:
    """SHM writes VALID for current lap → log parser emits OUTLAP → log must win
    because OUTLAP is a structural classification that SHM cannot provide."""
    manager = SharedSessionManager()

    # SHM reports lap 3 as valid (normal in-progress frame)
    manager.update_from_graphics_shm({
        "session_current_lap": 3,
        "is_invalid": False,
    })
    assert manager.get_lap_validity(3) is True

    # Log parser emits lap 3 as OUTLAP (heuristic structural classification)
    lap = LapData(
        lap_number=3,
        physics_lap_number=3,
        lap_time_ms=0,
        lap_time_str="0:00.000",
        is_valid=False,
        lap_state=LapState.OUTLAP,
        lap_type="OUTLAP",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Log OUTLAP replaces the SHM VALID — structural classifications win.
    validity = manager.get_lap_validity_data(3)
    assert validity is not None
    assert validity.lap_state == "OUTLAP"
    assert validity.source == "logs"


def test_authoritative_log_valid_overrides_shm_invalid() -> None:
    """SHM says invalid → log parser emits authoritative VALID (from onSplit) → log wins."""
    manager = SharedSessionManager()

    # SHM reports lap 5 as invalid
    manager.update_from_graphics_shm({
        "session_current_lap": 5,
        "is_invalid": True,
    })
    assert manager.get_lap_validity(5) is False

    # Log parser emits lap 5 with authoritative VALID (Relevant onSplit said valid)
    lap = LapData(
        lap_number=5,
        physics_lap_number=5,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=True,
        lap_state=LapState.VALID,
        lap_type="VALID",
        validity_source="authoritative",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Authoritative log VALID must override SHM INVALID
    validity = manager.get_lap_validity_data(5)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.lap_state == "VALID"
    assert validity.source == "logs"


def test_shm_invalid_does_not_block_log_invalid_split() -> None:
    """SHM says INVALID_GAME → log parser emits INVALID_SPLIT → log state must win
    because INVALID_SPLIT is a log-specific classification that SHM cannot provide."""
    manager = SharedSessionManager()

    manager.update_from_graphics_shm({
        "session_current_lap": 2,
        "is_invalid": True,
    })

    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=False,
        lap_state=LapState.INVALID_SPLIT,
        lap_type="INVALID_SPLIT",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Log-specific classification wins over SHM's generic INVALID_GAME.
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.lap_state == "INVALID_SPLIT"
    assert validity.source == "logs"


def test_shm_validity_repeated_frames_are_idempotent() -> None:
    """Repeated SHM frames with same (lap, is_invalid) should produce consistent validity."""
    manager = SharedSessionManager()

    # First frame: lap 1, invalid=False
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": False,
    })
    assert manager.get_lap_validity(1) is True

    # Second frame: same state — validity should remain unchanged
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": False,
    })
    assert manager.get_lap_validity(1) is True

    # Third frame: is_invalid transitions to True — should update
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": True,
    })
    assert manager.get_lap_validity(1) is False


def test_update_from_static_shm_sets_session_metadata() -> None:
    manager = SharedSessionManager()

    manager.update_from_static_shm(
        {
            "ac_evo_version": "0.9.3",
            "session": 1,
            "session_name": "Practice",
            "track": "spa_francorchamps",
            "track_configuration": "gp",
            "track_length_m": 7004.0,
            "is_online": True,
            "is_timed_race": False,
            "event_id": 4,
        }
    )

    metadata = manager.get_session_metadata_data()
    assert metadata.game_version == "0.9.3"
    assert metadata.session_type == "1"
    assert metadata.track == "spa_francorchamps"
    assert metadata.source == "shm_static"


def test_get_lap_time_uses_source_priority() -> None:
    manager = SharedSessionManager()

    # Set graphics-sourced time first (lower priority).
    manager.update_lap_timing_from_graphics_shm(5, {"last_laptime_ms": 120000})
    assert manager.get_lap_time(5) == 120000.0

    # Log-sourced time must override graphics.
    timing = manager._session_data.lap_timing[5]
    timing.completed_lap_time = 130000.0
    timing.completed_lap_time_source = "logs"
    assert manager.get_lap_time(5) == 130000.0


def test_get_lap_time_graphics_fallback_when_no_log_time() -> None:
    """Graphics SHM times must be visible when log-sourced times are absent."""
    manager = SharedSessionManager()

    manager.update_lap_timing_from_graphics_shm(3, {"last_laptime_ms": 95000})
    assert manager.get_lap_time(3) == 95000.0

    # get_all_lap_times must include graphics-only laps
    all_times = manager.get_all_lap_times()
    assert 3 in all_times
    assert all_times[3] == 95000.0

    # get_best_lap_time must consider graphics times
    assert manager.get_best_lap_time() == 95000.0


def test_validate_data_consistency_returns_empty_after_merge() -> None:
    """validate_data_consistency is a no-op after merging 4 dicts into 1."""
    manager = SharedSessionManager()

    manager.update_lap_timing_from_graphics_shm(1, {"last_laptime_ms": 100000})
    manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 100250})

    result = manager.validate_data_consistency()

    assert "inconsistencies" in result
    assert len(result["inconsistencies"]) == 0


def test_update_lap_from_logs_preserves_session_metadata() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-legacy",
        game_version="1.0.0",
        session_type="PRACTICE",
        car="ks_porsche_992_gt3_cup",
        track="spa_francorchamps",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=123456,
        lap_time_str="2:03.456",
        sector1_ms=40000,
        sector2_ms=41000,
        sector3_ms=42456,
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)
    metadata = manager.get_session_metadata()

    assert metadata["session_id"] == "session-legacy"
    identity = manager.get_player_identification()
    assert identity.steam_id == "76561198321627695"
    assert manager.get_lap_time(1) == 123456.0
    sectors = manager.get_sector_times(1)
    assert sectors.get(2) == 41000


def test_lap_time_priority_logs_over_graphics() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-priority",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=130000,
        lap_time_str="2:10.000",
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)
    manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 120000})

    # Log-sourced time (130000) must take priority over graphics SHM (120000).
    assert manager.get_lap_time(2) == 130000.0


def test_update_lap_preserves_log_derived_outlap_state() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-outlap",
        session_type="PRACTICE",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
        track="spa_francorchamps",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=120000,
        lap_time_str="2:00.000",
        lap_state=LapState.OUTLAP,
        lap_type=LapState.OUTLAP.value,
        is_valid=False,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)

    validity = manager.get_lap_validity_data(1)
    assert validity is not None
    assert validity.lap_state == "OUTLAP"


def test_update_from_physics_shm_updates_car_setup_and_max_speed() -> None:
    manager = SharedSessionManager()

    manager.update_from_physics_shm(
        {
            "speed_kmh": 250.5,
            "car_setup": {"brake_bias": 0.59, "ride_height_front": 58},
            "assists_state": {"abs": True},
            "air_density": 1.18,
        }
    )

    setup = manager.get_car_setup()
    assert setup["brake_bias"] == 0.59
    assert setup["ride_height_front"] == 58
    assert manager._session_data.max_speed == 250.5
    assert manager._session_data.assists_state["abs"] is True
    assert manager._session_data.air_density == 1.18


def test_concurrent_updates_are_thread_safe() -> None:
    manager = SharedSessionManager()

    def _write(idx: int) -> None:
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 100000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, idx % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write, range(1, 50)))

    # Graphics times are stored in LapTimingData.completed_lap_time and serve
    # as fallback in get_all_lap_times when no log-sourced times exist.
    with manager._lock:
        timing_count = sum(
            1 for t in manager._session_data.lap_timing.values()
            if t.completed_lap_time is not None
        )
    lap_validity = manager.get_all_lap_validity()
    all_lap_times = manager.get_all_lap_times()
    assert timing_count == 49
    assert len(lap_validity) == 49
    assert len(all_lap_times) == 49


def test_concurrent_access_performance() -> None:
    manager = SharedSessionManager()

    def _write(idx: int) -> None:
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 90000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, False)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(_write, range(1, 1501)))
    elapsed = time.perf_counter() - start

    # Guard against major regressions while avoiding flaky micro-bench assertions.
    assert elapsed < 8.0
    with manager._lock:
        timing_count = sum(
            1 for t in manager._session_data.lap_timing.values()
            if t.completed_lap_time is not None
        )
    assert timing_count == 1500


def test_memory_usage_optimization() -> None:
    manager = SharedSessionManager()

    tracemalloc.start()
    for idx in range(1, 3001):
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 100000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, idx % 11 == 0)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Peak should stay well-bounded for a few thousand updates.
    assert current < 32 * 1024 * 1024
    assert peak < 32 * 1024 * 1024


def test_large_session_handling() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="large-session",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )

    for lap_num in range(1, 2001):
        lap = LapData(
            lap_number=lap_num,
            physics_lap_number=lap_num,
            lap_time_ms=100000 + lap_num,
            lap_time_str="1:40.000",
            sector1_ms=33000,
            sector2_ms=33000,
            sector3_ms=34000,
            is_valid=True,
            timestamp="2026-01-01T00:00:00",
        )
        manager.update_lap_from_logs(lap, session_data=session)

    assert manager.get_lap_time(2000) == 102000.0
    assert manager.get_all_lap_times().get(2000) == 102000.0


def test_shm_is_valid_lap_false_with_active_timing_marks_invalid() -> None:
    """is_valid_lap=False with current_lap_time_ms > 0 must mark lap invalid."""
    manager = SharedSessionManager()

    manager.update_from_graphics_shm({
        "total_lap_count": 1,
        "is_valid_lap": False,
        "current_lap_time_ms": 40374,
        "session_current_lap": 0,
        "is_invalid": None,
        "timing_is_invalid": None,
    })

    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is False
    assert validity.source == "shm_graphics"


def test_shm_is_valid_lap_false_with_zero_lap_time_skipped() -> None:
    """is_valid_lap=False with current_lap_time_ms == 0 means timing inactive, not invalid."""
    manager = SharedSessionManager()

    manager.update_from_graphics_shm({
        "total_lap_count": 0,
        "is_valid_lap": False,
        "current_lap_time_ms": 0,
        "session_current_lap": 0,
        "is_invalid": None,
        "timing_is_invalid": None,
    })

    assert manager.get_lap_validity_data(1) is None


def test_log_heuristic_valid_wins_over_shm_invalid_peek_path() -> None:
    """SHM is_valid_lap cannot distinguish contact from track cuts, and
    contact must never invalidate a lap.  The log verdict (even heuristic
    VALID) always wins for completed laps — including the peek path where
    is_valid_lap arrives via total_lap_count derivation."""
    manager = SharedSessionManager()

    manager.update_from_graphics_shm({
        "total_lap_count": 1,
        "is_valid_lap": False,
        "current_lap_time_ms": 40374,
        "session_current_lap": 0,
        "is_invalid": None,
        "timing_is_invalid": None,
    })
    assert manager.get_lap_validity(2) is False

    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=52371,
        lap_time_str="00:52.371",
        is_valid=True,
        lap_state=LapState.VALID,
        lap_type="VALID",
        timestamp="2026-07-20T23:24:20.446",
    )
    manager.update_lap_from_logs(lap)

    # Log verdict wins — SHM contact-based invalidity must not persist.
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.lap_state == "VALID"
    assert validity.source == "logs"


# ── Regression: SHM stale last_laptime_ms scrubbing ────────────────────────

def test_shm_stale_last_laptime_scrubbed_when_no_laps_completed() -> None:
    """Regression: stale ``last_laptime_ms`` from a previous game session
    must NOT be stored as a completed lap time for lap 1 of a new session.

    When ``total_lap_count == 0`` and ``current_lap <= 1``, no laps have
    been finished in the current session — any non-zero ``last_laptime_ms``
    is a carryover from the previous game's Windows file mapping.
    """
    manager = SharedSessionManager()

    # Simulate SHM data at session start: lap 1 in progress, no laps completed,
    # but last_laptime_ms carries a stale value from the previous session.
    manager.update_from_graphics_shm({
        "session_current_lap": 0,       # fallback path
        "total_lap_count": 0,           # no laps completed yet
        "last_laptime_ms": 83456,       # stale! (1:23.456 from old session)
        "current_lap_time_ms": 5000,
        "best_laptime_ms": 0,
    })

    timing = manager.get_lap_timing_data(1)
    assert timing is not None
    # The stale last_laptime_ms must NOT become a completed_lap_time.
    assert timing.completed_lap_time is None, (
        f"Stale last_laptime_ms should have been scrubbed, "
        f"but completed_lap_time={timing.completed_lap_time}"
    )
    # last_lap_time_ms should also be zeroed.
    assert timing.last_lap_time_ms == 0, (
        f"Stale last_laptime_ms should have been scrubbed, "
        f"but last_lap_time_ms={timing.last_lap_time_ms}"
    )


def test_shm_stale_last_laptime_not_scrubbed_when_laps_exist() -> None:
    """When laps have already been completed, ``last_laptime_ms`` is legitimate
    and must NOT be scrubbed.
    """
    manager = SharedSessionManager()

    # Simulate SHM data mid-session: lap 3 in progress, 2 laps completed.
    manager.update_from_graphics_shm({
        "session_current_lap": 3,
        "total_lap_count": 2,
        "last_laptime_ms": 120123,      # legitimate last lap time
        "current_lap_time_ms": 61234,
        "best_laptime_ms": 119999,
    })

    timing = manager.get_lap_timing_data(3)
    assert timing is not None
    # Legitimate last_laptime_ms must be preserved.
    assert timing.last_lap_time_ms == 120123, (
        f"Legitimate last_laptime_ms should be preserved, "
        f"but got {timing.last_lap_time_ms}"
    )
