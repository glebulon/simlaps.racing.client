"""Focused parser ownership and boundary regressions for PR65."""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.log_parser import LogParser, _PendingOutgoingValidity
from src.models import LapCompletionData, LapData, LapState, SessionData, SharedSessionManager
from src.ui.services.session_lifecycle_service import SessionLifecycleService

PLAYER_ID = "76561197986609341"
OTHER_PLAYER_ID = "76561197986609342"
BMW_UUID = "11111111-1111-1111-1111-111111111111"
PORSCHE_UUID = "22222222-2222-2222-2222-222222222222"


class _LegacyManager:
    def get_latest_lap_completion(self):
        return None


class _BoundaryLegacyManager(_LegacyManager):
    def reset(self, *, preserve_completions=False):
        return None


class _OwnerIndexManager:
    def __init__(self, owner_id: str):
        self.active = None
        self.owner_id = owner_id
        self.completion = LapCompletionData(
            completed_laps=1,
            lap_time_ms=100000,
            is_valid=True,
            timestamp="2026-09-12T10:00:00+00:00",
            observed_at=1.0,
        )

    def get_latest_lap_completion(self):
        return None

    def get_active_session_id(self):
        return self.active

    def begin_session(self, session_id, *, car_model=None, car_uuid=None):
        self.active = session_id

    def bind_session_identity(self, *, session_id=None, car_model=None, car_uuid=None):
        return None

    def get_lap_completions_after(self, observed_at):
        return [self.completion]

    def get_lap_completion_owner(self, completion):
        return (1, self.owner_id, "bmw_m4_gt3", BMW_UUID)

    def update_session_metadata_from_logs(self, session):
        return None

    def update_from_logs(self, session):
        return None


def _session(car: str, uuid: str) -> SessionData:
    return SessionData(
        track="Nordschleife",
        car=car,
        car_uuid=uuid,
        player_id=PLAYER_ID,
        session_type="PRACTICE",
    )


def test_connect_boundary_uses_verified_car_aliases_only():
    parser = LogParser(session_manager=SharedSessionManager())
    parser.current_session = _session("ks_bmw_m4_gt3", BMW_UUID)

    assert parser._connect_is_same_car("bmw_m4_gt3", "33333333-3333-3333-3333-333333333333")
    assert not parser._connect_is_same_car("porsche_992_gt3_r", PORSCHE_UUID)


def test_modern_anonymous_completion_is_not_legacy_adopted_after_rollover():
    parser = LogParser(session_manager=SharedSessionManager())
    parser.current_session = _session("porsche_992_gt3_cup", PORSCHE_UUID)
    parser._session_completion_floor = 10.0

    completion = LapCompletionData(
        completed_laps=1,
        lap_time_ms=100000,
        is_valid=True,
        timestamp="2026-09-12T10:00:00+00:00",
        observed_at=11.0,
        origin_epoch=2,
        session_id=None,
        car_model="porsche_992_gt3_cup",
        car_uuid=None,
    )

    assert parser._session_for_completion(completion) is None


def test_ownerless_pre_boundary_completion_stays_unresolved_after_rollover():
    parser = LogParser(session_manager=SharedSessionManager())
    parser.current_session = _session("porsche_992_gt3_cup", PORSCHE_UUID)
    parser._session_completion_floor = 100.0

    completion = LapCompletionData(
        completed_laps=1,
        lap_time_ms=100000,
        is_valid=True,
        timestamp="2026-09-12T10:00:00+00:00",
        observed_at=99.0,
        origin_epoch=None,
        session_id=None,
        car_model="porsche_992_gt3_cup",
        car_uuid=PORSCHE_UUID,
    )

    assert parser._session_for_completion(completion) is None


def test_ownerless_completion_between_cursor_and_actual_boundary_stays_unresolved(monkeypatch):
    """The ownership floor is the rollover instant, rather than the cursor."""
    parser = LogParser(session_manager=_BoundaryLegacyManager())
    parser.current_session = _session("bmw_m4_gt3", BMW_UUID)
    parser._last_shm_completion_observed_at = 100.0
    monkeypatch.setattr("src.core.log_parser.time.monotonic", lambda: 200.0)

    parser._reset_session_boundary(
        "car rollover", finalize_current_session=False
    )
    parser.current_session = _session("bmw_m4_gt3", BMW_UUID)
    completion = LapCompletionData(
        completed_laps=1,
        lap_time_ms=100000,
        is_valid=True,
        timestamp="2026-09-12T10:00:00+00:00",
        observed_at=150.0,
        origin_epoch=None,
        session_id=None,
        car_model="bmw_m4_gt3",
        car_uuid=BMW_UUID,
    )

    assert parser._session_for_completion(completion) is None


def test_session_for_lap_falls_through_membership_when_owner_lookup_is_missing():
    parser = LogParser(session_manager=_LegacyManager())
    old = _session("ks_bmw_m4_gt3", BMW_UUID)
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        is_valid=True,
    )
    old.laps.append(lap)
    parser.sessions.append(old)
    parser.current_session = _session("porsche_992_gt3_cup", PORSCHE_UUID)
    parser._lap_completion_by_lap_id[id(lap)] = LapCompletionData(
        completed_laps=1,
        lap_time_ms=100000,
        is_valid=True,
        timestamp="2026-09-12T10:00:00+00:00",
        observed_at=1.0,
    )

    assert parser._session_for_lap(lap) is old


def test_delayed_outgoing_log_and_validity_find_finalized_session_after_index_eviction():
    """Late metadata still updates the retained session object after pruning."""
    parser = LogParser(session_manager=_LegacyManager())
    old = _session("bmw_m4_gt3", BMW_UUID)
    old_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        is_valid=True,
        validity_source="shm_graphics",
    )
    old.laps.append(old_lap)
    parser.sessions.append(old)
    parser.current_session = _session("porsche_992_gt3_cup", PORSCHE_UUID)
    parser.context.car_uuid = PORSCHE_UUID
    parser.context.player_car_uuids.update({BMW_UUID, PORSCHE_UUID})
    parser._shm_emitted_laps.append(old_lap)

    parser._handle_lap_complete(
        "[2026-09-12 10:00:00.000] [gameplay] [info] "
        f"New lap carId {BMW_UUID}: 01:40.000"
    )
    assert parser._pending_outgoing_validity is not None
    assert parser._pending_outgoing_validity.session is old

    parser._handle_lap_validity(
        "[2026-09-12 10:00:00.001] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 100000, "
        "valid false, flags 1, lap 1 (prev 0)"
    )
    assert old_lap.is_valid is False
    assert old_lap.lap_state == LapState.INVALID_GAME
    assert parser._reconciled_lap is old_lap


def test_offline_creating_car_is_shared_car_rollover_but_set_new_car_is_not():
    parser = LogParser(session_manager=SharedSessionManager())
    old = _session("bmw_m4_gt3", BMW_UUID)
    old.laps.append(
        LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=100000,
            lap_time_str="01:40.000",
            lap_state=LapState.VALID,
            is_valid=True,
        )
    )
    parser.current_session = old
    parser._sessions_by_id[old.session_id] = old
    parser.context.current_car = old.car
    parser.context.car_uuid = old.car_uuid
    parser.context.player_id = PLAYER_ID
    parser.context.player_car_uuids.add(BMW_UUID)

    parser._handle_player_car_binding(
        "onSetPlayerCurrentCarCommand: Set new car "
        f"{PORSCHE_UUID} content\\cars\\porsche_992_gt3_cup\\data"
    )
    assert parser.current_session is old

    parser._handle_player_car_binding(
        f"[ServerVehicleSystem][{PORSCHE_UUID}] Creating Car "
        f"(Driver  {PLAYER_ID})"
    )

    assert parser.current_session is not old
    assert parser.current_session.car == "porsche_992_gt3_cup"
    assert parser.current_session.car_uuid == PORSCHE_UUID
    assert old in parser.sessions


@pytest.mark.asyncio
async def test_follow_offline_creating_car_rollover_drains_shm_before_capture_restart(tmp_path):
    """Offline car binding crosses the parser, SHM, and lifecycle callbacks."""
    log_file = tmp_path / "offline-rollover.log"
    log_file.write_text(
        "[2026-09-12 10:00:00.000] [gameplay] [info] "
        "onSetPlayerCurrentCarCommand: Set new car "
        f"{BMW_UUID} content\\cars\\bmw_m4_gt3\\data\n"
        "[2026-09-12 10:00:00.001] [server] [info] "
        f"[ServerVehicleSystem][{BMW_UUID}] Creating Car (Driver  {PLAYER_ID})\n"
        "[2026-09-12 10:00:01.000] [gameplay] [info] "
        "On Split start false end false id 0 splittime 0\n",
        encoding="utf-8",
    )
    manager = SharedSessionManager()
    events = []
    capture_events = []
    live = asyncio.Event()
    rollover = asyncio.Event()

    class Capture:
        def __init__(self):
            self.capturing = True

        def is_capturing(self):
            return self.capturing

    capture = Capture()

    async def start_capture():
        capture_events.append("start")
        capture.capturing = True

    async def stop_capture(reason, **_kwargs):
        capture_events.append(reason)
        capture.capturing = False

    lifecycle = SessionLifecycleService(
        home_page=MagicMock(),
        session_manager=manager,
        telemetry_capture=capture,
        start_capture=start_capture,
        stop_capture=stop_capture,
    )

    async def on_status(status):
        if "Monitoring for new laps" in status:
            live.set()

    async def on_game_status(is_running):
        events.append(("status", is_running, parser.current_session))
        await lifecycle.handle_game_status_change(is_running)
        if is_running and parser.current_session and parser.current_session.car_uuid == PORSCHE_UUID:
            rollover.set()

    async def on_lap(session, lap):
        events.append(("lap", session, lap))

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
        on_status_change=on_status,
        on_game_status_change=on_game_status,
        on_lap_complete=on_lap,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 5.0
    task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        await asyncio.wait_for(live.wait(), timeout=1.0)
        old_session = parser.current_session
        assert old_session is not None and old_session.car_uuid == BMW_UUID

        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M4 GT3",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "current_lap_time_ms": 100_000,
                "last_laptime_ms": 0,
                "total_lap_count": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M4 GT3",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "current_lap_time_ms": 0,
                "last_laptime_ms": 100_000,
                "total_lap_count": 0,
                "is_valid_lap": True,
            }
        )

        with log_file.open("a", encoding="utf-8") as handle:
            # This metadata line alone must not create the replacement epoch.
            handle.write(
                "[2026-09-12 10:00:02.000] [gameplay] [info] "
                "onSetPlayerCurrentCarCommand: Set new car "
                f"{PORSCHE_UUID} content\\cars\\porsche_992_gt3_cup\\data\n"
            )
            # A valid Steam ID for another driver must not steal the binding.
            handle.write(
                "[2026-09-12 10:00:02.001] [server] [info] "
                f"[ServerVehicleSystem][{PORSCHE_UUID}] Creating Car (Rival  {OTHER_PLAYER_ID})\n"
            )
        await asyncio.sleep(0.03)
        assert parser.current_session is old_session

        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                "[2026-09-12 10:00:03.000] [server] [info] "
                f"[ServerVehicleSystem][{PORSCHE_UUID}] Creating Car (Driver  {PLAYER_ID})\n"
            )
        await asyncio.wait_for(rollover.wait(), timeout=1.0)
    finally:
        parser.stop()
        await asyncio.wait_for(task, timeout=1.0)

    assert parser.current_session is not old_session
    assert parser.current_session.car_uuid == PORSCHE_UUID
    assert events[0][0] == "lap"
    assert events[0][1] is old_session
    assert events[0][2].lap_time_ms == 100_000
    assert events[1][0] == "status"
    assert events[1][2] is parser.current_session
    assert capture_events == ["session_rollover", "start"]


def test_pending_outgoing_generation_is_cleared_by_new_game_boundary():
    parser = LogParser(session_manager=SharedSessionManager())
    old = _session("bmw_m4_gt3", BMW_UUID)
    parser.current_session = old
    outgoing = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        is_valid=True,
    )
    parser._pending_outgoing_validity = _PendingOutgoingValidity(
        outgoing, old, parser._boundary_generation
    )

    parser._handle_session_start(
        "[2026-09-12 10:00:00.000] [gameplay] [info] Game Started! "
        "GameModeType_PRACTICE | Nordschleife | porsche_992_gt3_cup | "
        "GameModeSelectionWeatherType_Clear"
    )

    assert parser._pending_outgoing_validity is None


def test_owner_index_cap_preserves_unconsumed_completion_owner():
    first = _session("bmw_m4_gt3", BMW_UUID)
    manager = _OwnerIndexManager(first.session_id)
    parser = LogParser(session_manager=manager)
    parser.current_session = first
    parser._establish_session_ownership(first)

    for index in range(513):
        current = _session(f"car_{index}", f"{index + 3:032x}"[:8] + "-0000-0000-0000-000000000000")
        parser.current_session = current
        parser._establish_session_ownership(current)

    assert len(parser._sessions_by_id) <= 512
    assert first.session_id in parser._sessions_by_id
