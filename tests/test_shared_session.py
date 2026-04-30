"""Tests for shared session data infrastructure."""

from concurrent.futures import ThreadPoolExecutor
import time
import tracemalloc

from src.models import (
    LapData,
    SessionData,
    SharedSessionManager,
)


def test_update_from_graphics_sets_lap_validity_and_fuel() -> None:
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

    lap_validity = manager.get_lap_validity_data(3)
    assert lap_validity is not None
    assert lap_validity.is_valid is False
    assert lap_validity.source == "shm_graphics"

    lap_timing = manager.get_lap_timing_data(3)
    assert lap_timing is not None
    assert lap_timing.current_lap_time_ms == 61234
    assert lap_timing.last_lap_time_ms == 120123

    fuel = manager.get_fuel_data()
    assert fuel.current_fuel == 21.5
    assert fuel.fuel_consumption_rate == 2.34
    assert fuel.fuel_economy == 0.42


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

    manager._session_data.calc_lap_times[5] = 150000.0
    manager._session_data.lap_times_logs[5] = 130000.0
    assert manager.get_lap_time(5) == 130000.0

    manager._session_data.lap_times_graphics[5] = 120000.0
    assert manager.get_lap_time(5) == 120000.0


def test_validate_data_consistency_reports_large_source_drift() -> None:
    manager = SharedSessionManager()

    manager._session_data.lap_times_graphics[1] = 100000.0
    manager._session_data.lap_times_logs[1] = 100050.0
    manager._session_data.lap_times_graphics[2] = 100000.0
    manager._session_data.lap_times_logs[2] = 100250.0

    result = manager.validate_data_consistency()

    assert "inconsistencies" in result
    assert len(result["inconsistencies"]) == 1
    assert "lap 2" in result["inconsistencies"][0]


def test_legacy_wrapper_converts_shared_state_to_session_data() -> None:
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
    wrapper = manager.get_legacy_wrapper()
    legacy_session = wrapper.to_session_data()

    assert legacy_session.session_id == "session-legacy"
    assert legacy_session.player_id == "76561198321627695"
    assert len(legacy_session.laps) == 1
    assert legacy_session.laps[0].lap_time_ms == 123456
    assert legacy_session.laps[0].sector2_ms == 41000


def test_to_legacy_session_data_uses_graphics_lap_time_priority() -> None:
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

    legacy_session = manager.to_legacy_session_data()
    lap_two = next(l for l in legacy_session.laps if l.lap_number == 2)
    assert lap_two.lap_time_ms == 120000


def test_observer_notified_and_observer_errors_are_isolated() -> None:
    manager = SharedSessionManager()
    notifications: list[int] = []

    def _ok_observer(snapshot) -> None:
        notifications.append(snapshot.current_lap or 0)

    def _failing_observer(_snapshot) -> None:
        raise RuntimeError("observer failure")

    manager.subscribe(_failing_observer)
    manager.subscribe(_ok_observer)

    manager.update_from_graphics_shm({"session_current_lap": 7, "last_laptime_ms": 111111})

    assert notifications
    assert notifications[-1] == 7


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

    lap_times = manager.get_all_lap_times()
    lap_validity = manager.get_all_lap_validity()
    assert len(lap_times) == 49
    assert len(lap_validity) == 49


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
    assert len(manager.get_all_lap_times()) == 1500


def test_memory_usage_optimization() -> None:
    manager = SharedSessionManager()

    tracemalloc.start()
    for idx in range(1, 3001):
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 100000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, idx % 11 == 0)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Peak should stay well-bounded for a few thousand updates.
    assert peak < 32 * 1024 * 1024
    assert current < peak


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

    legacy_session = manager.to_legacy_session_data()
    assert len(legacy_session.laps) == 2000
    assert manager.get_lap_time(2000) == 102000.0
