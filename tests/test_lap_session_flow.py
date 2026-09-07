"""Public lap/session ownership regressions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.api_client import APIClient
from src.core.log_parser import LogParser
from src.models import LapData, SessionData, SharedSessionManager
from src.ui.services.lap_processing_service import LapProcessingService
from src.ui.services.session_lifecycle_service import SessionLifecycleService
from src.utils.config import AppConfig


def _game_started(track: str, car: str, stamp: str) -> str:
    return (
        f"[{stamp}] [gameplay] [info] Game Started! "
        f"GameModeType_PRACTICE | {track} | {car} | "
        "GameModeSelectionWeatherType_Clear\n"
    )


def _bmw_boundary(manager: SharedSessionManager) -> None:
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
            "is_valid_lap": True,
        }
    )


def _alfa_boundary(manager: SharedSessionManager) -> None:
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
            "is_valid_lap": True,
        }
    )


@pytest.mark.asyncio
async def test_follow_routes_reordered_old_completion_and_allows_new_equal_time(
    tmp_path,
):
    """A reordered Game Started cannot retag BMW's finish as Alfa's lap."""
    bmw_uuid = "4d27cc23-ee6ce0de-9c3810448288bcbb"
    alfa_uuid = "5e38dd34-ff7de1ef-0d492f9a9399ccee"
    log_file = tmp_path / "session-flow.log"
    log_file.write_text(
        _game_started("Monza", "ks_bmw_m2_coupe", "2026-09-07 10:00:00.000"),
        encoding="utf-8",
    )
    manager = SharedSessionManager()
    emitted: list[tuple[SessionData, LapData]] = []
    updates: list[tuple[SessionData, LapData]] = []
    live = asyncio.Event()
    telemetry = MagicMock()
    telemetry.is_capturing.return_value = True
    telemetry.owns_session.side_effect = lambda session_id: session_id == ""
    home = MagicMock()
    home._lap_count = 0
    history = []
    schedules = MagicMock()
    service = LapProcessingService()

    def add_lap(*_args):
        home._lap_count += 1
        return MagicMock()

    home.add_lap.side_effect = add_lap

    async def on_status(status: str) -> None:
        if "Monitoring for new laps" in status:
            live.set()

    async def on_lap(session: SessionData, lap: LapData) -> None:
        emitted.append((session, lap))
        telemetry.owns_session.side_effect = (
            lambda session_id: session_id == session.session_id
            if session.car == "ks_bmw_m2_coupe"
            else False
        )
        await service.handle_lap_complete(
            session=session,
            lap=lap,
            home_page=home,
            telemetry_capture=telemetry,
            config=AppConfig(auto_submit=True, telemetry_enabled=True),
            session_manager=manager,
            pb_cache=MagicMock(),
            history_entries=history,
            schedule_submission=schedules,
            create_history_entry=lambda **values: SimpleNamespace(**values),
        )

    async def on_lap_update(session: SessionData, lap: LapData) -> None:
        updates.append((session, lap))

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
        on_status_change=on_status,
        on_lap_complete=on_lap,
        on_lap_update=on_lap_update,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 60.0
    task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        await asyncio.wait_for(live.wait(), timeout=1.0)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                "[2026-09-07 10:00:01.000] [network] [info] "
                f"76561198321627695 connected on car ks_bmw_m2_coupe, "
                f"with new carId {bmw_uuid}\n"
            )
        await asyncio.sleep(0.03)

        _bmw_boundary(manager)
        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M2 Coupe",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "total_lap_count": 0,
                "current_lap_time_ms": 65_000,
                "last_laptime_ms": 108_315,
                "is_valid_lap": True,
            }
        )
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                _game_started(
                    "Monza",
                    "ks_alfa_romeo_giulia_gtam",
                    "2026-09-07 10:00:02.000",
                )
            )
        await asyncio.sleep(0.03)

        # This is the stale BMW sample that caused the phantom Alfa lap.
        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M2 Coupe",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "total_lap_count": 0,
                "current_lap_time_ms": 0,
                "last_laptime_ms": 108_315,
                "is_valid_lap": True,
            }
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 0.0
        deadline = asyncio.get_running_loop().time() + 1.0
        while len(emitted) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert [session.car for session, _ in emitted] == ["ks_bmw_m2_coupe"]
        assert len(history) == 1
        assert schedules.call_count == 1

        # The delayed outgoing log boundary is routed to the already-emitted
        # BMW object after the session switch, so it cannot create Alfa data.
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-09-07 10:00:03.000] [gameplay] [info] "
                f"New lap carId {bmw_uuid}: 01:48.315\n"
                "[2026-09-07 10:00:03.010] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 108315, "
                "valid false, flags 1, lap 1 (prev 0)\n"
            )
        await asyncio.sleep(0.05)
        assert updates and updates[-1][0].car == "ks_bmw_m2_coupe"
        assert updates[-1][1].is_valid is False
        assert emitted[0][1].is_valid is False

        _alfa_boundary(manager)
        deadline = asyncio.get_running_loop().time() + 1.0
        while len(emitted) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                "[2026-09-07 10:00:04.000] [network] [info] "
                f"76561198321627695 connected on car ks_alfa_romeo_giulia_gtam, "
                f"with new carId {alfa_uuid}\n"
                f"[2026-09-07 10:00:05.000] [gameplay] [info] "
                f"New lap carId {alfa_uuid}: 01:48.315\n"
            )
        await asyncio.sleep(0.05)
    finally:
        parser.stop()
        await asyncio.wait_for(task, timeout=1.0)

    assert [session.car for session, _ in emitted] == [
        "ks_bmw_m2_coupe",
        "ks_alfa_romeo_giulia_gtam",
    ]
    assert [lap.lap_time_ms for _, lap in emitted] == [108_315, 108_315]
    assert emitted[1][1].is_valid is True
    assert emitted[0][0].car_uuid == bmw_uuid
    assert len({session.session_id for session, _ in emitted}) == 2
    assert emitted[0][0] in parser.sessions
    assert len(history) == 2
    assert [entry.track for entry in history] == ["Monza", "Monza"]
    assert schedules.call_count == 2
    assert [call.args[1].car for call in schedules.call_args_list] == [
        "ks_bmw_m2_coupe",
        "ks_alfa_romeo_giulia_gtam",
    ]
    assert telemetry.record_lap_boundary.call_count == 1
    assert telemetry.record_lap_boundary.call_args.args[:2] == (108_315, 1)


def test_submission_does_not_overlay_active_alfa_on_delayed_bmw() -> None:
    """An outgoing lap uses frozen BMW fields while Alfa is active."""
    manager = SharedSessionManager()
    manager.begin_session(
        "alfa-session",
        car_model="ks_alfa_romeo_giulia_gtam",
        car_uuid="alfa-uuid",
    )
    manager.update_player_identification_from_logs(
        {"steam_id": "76561198000000001", "car_model": "Alfa Romeo Giulia GTAm"}
    )
    manager.update_session_metadata_from_static_shm(
        {"track": "alfa-track", "session": "RACE", "ac_evo_version": "alfa-build"}
    )
    manager.update_sector_splits_from_logs(
        1, {"sector1_ms": 10_000, "sector2_ms": 20_000, "sector3_ms": 30_000}
    )
    manager.update_fuel_from_graphics_shm({"fuel_liter_per_lap": 9.0})

    bmw = SessionData(
        session_id="bmw-session",
        track="bmw-track",
        car="ks_bmw_m2_coupe",
        session_type="PRACTICE",
        game_version="bmw-build",
        player_id="76561198321627695",
    )
    bmw_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=108_315,
        lap_time_str="01:48.315",
        sector1_ms=35_000,
        sector2_ms=36_000,
        sector3_ms=37_315,
        fuel_used=1.5,
        is_valid=True,
    )
    payload, error = APIClient(session_manager=manager)._build_submission_payload(
        session=bmw,
        lap=bmw_lap,
        final_user_id=bmw.player_id,
        shared_player=manager.get_player_identification(),
        effective_is_valid=True,
    )

    assert error is None
    assert payload is not None
    assert payload["trackId"] == "bmwtrack"
    assert payload["carId"] == "ks_bmw_m2_coupe"
    assert payload["sessionType"] == "PRACTICE"
    assert payload["gameVersion"] == "bmw-build"
    assert payload["sector1"] == 35_000
    assert payload["sector2"] == 36_000
    assert payload["sector3"] == 37_315
    assert payload["fuelUsed"] == 1.5


def test_parser_connect_cannot_bind_graphics_alfa_completion_to_bmw() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 108_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 108_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 108_000,
            "total_lap_count": 0,
            "last_laptime_ms": 0,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 0,
            "total_lap_count": 0,
            "last_laptime_ms": 108_315,
        }
    )
    completion = manager.get_latest_lap_completion()
    assert completion is not None and completion.session_id is None

    parser = LogParser(session_manager=manager)
    assert parser._handle_session_start(
        _game_started("Monza", "ks_bmw_m2_coupe", "2026-09-07 10:01:00.000")
    )
    parser._handle_connect(
        "[2026-09-07 10:01:01.000] [network] [info] "
        "76561198321627695 connected on car ks_bmw_m2_coupe, "
        "with new carId 4d27cc23-ee6ce0de-9c3810448288bcbb"
    )

    assert manager.get_lap_completion_owner(completion)[1] is None


@pytest.mark.asyncio
async def test_connect_only_car_switch_keeps_queued_completion_on_outgoing_session() -> None:
    """A connect line alone cannot retag a queued BMW completion as Alfa."""
    bmw_uuid = "4d27cc23-ee6ce0de-9c3810448288bcbb"
    alfa_uuid = "5e38dd34-ff7de1ef-0d492f9a9399ccee"
    manager = SharedSessionManager()
    emitted: list[tuple[SessionData, LapData]] = []
    parser = LogParser(session_manager=manager)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0.0

    async def on_lap(session: SessionData, lap: LapData) -> None:
        emitted.append((session, lap))

    parser.on_lap_complete = on_lap

    assert parser._handle_session_start(
        _game_started("Monza", "ks_bmw_m2_coupe", "2026-09-07 10:02:00.000")
    )
    parser._handle_connect(
        "[2026-09-07 10:02:01.000] [network] [info] "
        f"76561198321627695 connected on car ks_bmw_m2_coupe, "
        f"with new carId {bmw_uuid}"
    )
    outgoing = parser.current_session
    assert outgoing is not None

    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "total_lap_count": 0,
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
            "is_valid_lap": True,
        }
    )
    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert manager.get_lap_completion_owner(completion)[1] == outgoing.session_id

    # No Game Started marker arrives for the new car. The connect line must
    # open a replacement owner while leaving the outgoing object immutable.
    parser._handle_connect(
        "[2026-09-07 10:02:02.000] [network] [info] "
        f"76561198321627695 connected on car ks_alfa_romeo_giulia_gtam, "
        f"with new carId {alfa_uuid}"
    )
    replacement = parser.current_session
    assert replacement is not None and replacement is not outgoing
    assert replacement.car == "ks_alfa_romeo_giulia_gtam"
    assert replacement.car_uuid == alfa_uuid
    assert outgoing.car == "ks_bmw_m2_coupe"
    assert outgoing.car_uuid == bmw_uuid
    assert manager.get_active_session_id() == replacement.session_id
    assert parser._pending_connect_rollover is True

    lap = parser._take_ready_shm_lap()
    assert lap is not None
    assert parser._session_for_lap(lap) is outgoing
    await parser._emit_lap(outgoing, lap)
    assert [(session.car, session.car_uuid) for session, _ in emitted] == [
        ("ks_bmw_m2_coupe", bmw_uuid)
    ]


def test_omitted_outgoing_validity_cannot_capture_later_equal_time_alfa() -> None:
    """A later Alfa verdict must not finalize an unclosed BMW target."""
    bmw_uuid = "4d27cc23-ee6ce0de-9c3810448288bcbb"
    alfa_uuid = "5e38dd34-ff7de1ef-0d492f9a9399ccee"
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)

    assert parser._handle_session_start(
        _game_started("Monza", "ks_bmw_m2_coupe", "2026-09-07 10:03:00.000")
    )
    parser._handle_connect(
        "[2026-09-07 10:03:01.000] [network] [info] "
        f"76561198321627695 connected on car ks_bmw_m2_coupe, "
        f"with new carId {bmw_uuid}"
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    outgoing = parser.current_session
    assert outgoing is not None

    parser._handle_connect(
        "[2026-09-07 10:03:02.000] [network] [info] "
        f"76561198321627695 connected on car ks_alfa_romeo_giulia_gtam, "
        f"with new carId {alfa_uuid}"
    )
    current = parser.current_session
    assert current is not None and current is not outgoing
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0.0
    old_lap = parser._take_ready_shm_lap()
    assert old_lap is not None and parser._session_for_lap(old_lap) is outgoing

    parser._handle_lap_complete(
        f"[2026-09-07 10:03:03.000] [gameplay] [info] "
        f"New lap carId {bmw_uuid}: 01:48.315"
    )
    assert parser._pending_outgoing_validity is not None

    # ACE omits BMW's Relevant line. A real Alfa boundary at the same rounded
    # time must clear the old target before its own validity is applied.
    parser._handle_lap_complete(
        f"[2026-09-07 10:03:04.000] [gameplay] [info] "
        f"New lap carId {alfa_uuid}: 01:48.315"
    )
    assert parser._pending_outgoing_validity is None
    parser._handle_lap_validity(
        "[2026-09-07 10:03:04.010] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 108315, "
        "valid false, flags 1, lap 1 (prev 0)"
    )

    assert old_lap.is_valid is True
    assert current.laps and current.laps[-1].is_valid is False
    assert current.laps[-1].lap_time_ms == old_lap.lap_time_ms
    assert parser._session_for_lap(old_lap) is outgoing


@pytest.mark.asyncio
async def test_follow_connect_rollover_drains_outgoing_before_lifecycle_callback(tmp_path) -> None:
    """Live connect-only rollover publishes BMW before arming the Alfa epoch."""
    bmw_uuid = "4d27cc23-ee6ce0de-9c3810448288bcbb"
    alfa_uuid = "5e38dd34-ff7de1ef-0d492f9a9399ccee"
    log_file = tmp_path / "connect-rollover.log"
    log_file.write_text(
        _game_started("Monza", "ks_bmw_m2_coupe", "2026-09-07 10:04:00.000")
        + "[2026-09-07 10:04:01.000] [network] [info] "
        f"76561198321627695 connected on car ks_bmw_m2_coupe, "
        f"with new carId {bmw_uuid}\n",
        encoding="utf-8",
    )
    manager = SharedSessionManager()
    events: list[tuple[str, str]] = []
    capture_events: list[str] = []
    lap_capture_states: list[bool] = []
    live = asyncio.Event()
    rollover = asyncio.Event()

    class Capture:
        def __init__(self) -> None:
            self.capturing = False

        def is_capturing(self) -> bool:
            return self.capturing

    capture = Capture()

    async def start_capture() -> None:
        capture_events.append("start")
        capture.capturing = True

    async def stop_capture(reason: str, **_kwargs) -> None:
        capture_events.append(reason)
        capture.capturing = False

    lifecycle = SessionLifecycleService(
        home_page=MagicMock(),
        session_manager=manager,
        telemetry_capture=capture,
        start_capture=start_capture,
        stop_capture=stop_capture,
    )

    async def on_status(status: str) -> None:
        if "Monitoring for new laps" in status:
            live.set()

    async def on_game_status(is_running: bool) -> None:
        events.append(("status", str(is_running)))
        await lifecycle.handle_game_status_change(is_running)
        if len(events) >= 3:
            rollover.set()

    async def on_lap(session: SessionData, _lap: LapData) -> None:
        events.append(("lap", session.car))
        lap_capture_states.append(capture.capturing)

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
        on_status_change=on_status,
        on_game_status_change=on_game_status,
        on_lap_complete=on_lap,
    )
    task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        await asyncio.wait_for(live.wait(), timeout=1.0)
        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M2 Coupe",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "current_lap_time_ms": 108_000,
                "last_laptime_ms": 0,
                "total_lap_count": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "car_model": "BMW M2 Coupe",
                "status_name": "AC_LIVE",
                "session_phase": "Session",
                "current_lap_time_ms": 0,
                "last_laptime_ms": 108_315,
                "total_lap_count": 0,
                "is_valid_lap": True,
            }
        )
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                "[2026-09-07 10:04:02.000] [network] [info] "
                f"76561198321627695 connected on car ks_alfa_romeo_giulia_gtam, "
                f"with new carId {alfa_uuid}\n"
            )
        await asyncio.wait_for(rollover.wait(), timeout=1.0)
    finally:
        parser.stop()
        await asyncio.wait_for(task, timeout=1.0)

    assert events[0] == ("status", "True")
    assert events[-2:] == [
        ("lap", "ks_bmw_m2_coupe"),
        ("status", "True"),
    ]
    assert lap_capture_states == [True]
    assert capture_events == ["start", "session_rollover", "start"]
    assert parser.current_session is not None
    assert parser.current_session.car == "ks_alfa_romeo_giulia_gtam"
    assert manager.get_active_session_id() == parser.current_session.session_id


def test_submission_does_not_overlay_anonymous_graphics_car_on_bmw() -> None:
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="BMW")
    manager.update_from_graphics_shm(
        {"car_model": "BMW", "status_name": "AC_LIVE", "current_lap_time_ms": 12_000}
    )
    manager.update_from_graphics_shm(
        {"car_model": "Alfa", "status_name": "AC_LIVE", "current_lap_time_ms": 12_000}
    )
    manager.update_fuel_from_graphics_shm({"fuel_liter_per_lap": 9.0})
    bmw = SessionData(
        session_id="bmw-session", track="bmw-track", car="BMW", session_type="PRACTICE"
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=108_315,
        lap_time_str="01:48.315",
        sector1_ms=35_000,
        sector2_ms=36_000,
        sector3_ms=37_315,
        fuel_used=1.5,
        is_valid=True,
    )
    payload, error = APIClient(session_manager=manager)._build_submission_payload(
        session=bmw,
        lap=lap,
        final_user_id="76561198321627695",
        shared_player=manager.get_player_identification(),
        effective_is_valid=True,
    )

    assert error is None
    assert payload is not None
    assert payload["fuelUsed"] == 1.5


def test_graphics_only_car_epoch_clears_old_merged_data_but_keeps_completion_queue() -> None:
    """A graphics-led car change cannot expose BMW timing as anonymous Alfa."""
    manager = SharedSessionManager()
    manager.begin_session(
        "bmw-session", car_model="ks_bmw_m2_coupe", car_uuid="bmw-uuid"
    )
    manager.update_session_metadata_from_logs(
        SessionData(
            session_id="bmw-session",
            track="BMW Track",
            car="ks_bmw_m2_coupe",
            player_id="76561198321627695",
            car_uuid="bmw-uuid",
        )
    )
    bmw_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=108_315,
        lap_time_str="01:48.315",
        is_valid=False,
    )
    manager.update_lap_from_logs(
        bmw_lap,
        session_data=SessionData(
            session_id="bmw-session",
            track="BMW Track",
            car="ks_bmw_m2_coupe",
            car_uuid="bmw-uuid",
        ),
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 108_000,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M2 Coupe",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    completion = manager.get_latest_lap_completion()
    assert completion is not None
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa Romeo Giulia GTAm",
            "status_name": "AC_LIVE",
            "session_phase": "Session",
            "current_lap_time_ms": 12_000,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )

    assert manager.get_lap_completion_owner(completion)[1] == "bmw-session"
    assert manager.get_all_lap_times() == {}
    assert manager.get_lap_validity_data(1) is None
    assert manager.get_session_metadata()["track"] == "Unknown"
    assert manager.get_player_identification().steam_id == "76561198321627695"
    assert manager.get_player_identification().car_uuid is None


def test_delayed_outgoing_race_lines_do_not_fill_current_alfa_accumulator() -> None:
    manager = SharedSessionManager()
    bmw = SessionData(
        session_id="bmw-session",
        track="Monza",
        car="ks_bmw_m2_coupe",
        session_type="RACE",
        car_uuid="4d27cc23-ee6ce0de-9c3810448288bcbb",
    )
    alfa = SessionData(
        session_id="alfa-session",
        track="Monza",
        car="ks_alfa_romeo_giulia_gtam",
        session_type="RACE",
        car_uuid="5e38dd34-ff7de1ef-0d492f9a9399ccee",
    )
    manager.begin_session(alfa.session_id, car_model=alfa.car, car_uuid=alfa.car_uuid)
    parser = LogParser(session_manager=manager)
    parser.current_session = alfa
    parser._sessions_by_id[bmw.session_id] = bmw
    parser._sessions_by_id[alfa.session_id] = alfa
    parser.context.car_uuid = alfa.car_uuid
    parser.context.player_car_uuids.update({bmw.car_uuid, alfa.car_uuid})

    parser._handle_splits_race(
        f"[2026-09-07 10:02:00.000] [gameplay] [info] Split completed for car "
        f"{bmw.car_uuid}: (35000 ms, splitindex 0)"
    )
    parser._handle_fuel(
        f"[2026-09-07 10:02:01.000] [gameplay] [info] Energy source car "
        f"{bmw.car_uuid} for driver 76561198321627695 hundredmeters done: "
        "100 fuel consumed: 1.5 L"
    )

    assert parser._ip.splits == {}
    assert parser._ip.fuel_used is None


def test_unbound_completion_does_not_block_later_owned_completion() -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {"car_model": "BMW", "status_name": "AC_LIVE", "current_lap_time_ms": 108_000}
    )
    manager.update_from_graphics_shm(
        {"car_model": "Alfa", "status_name": "AC_LIVE", "current_lap_time_ms": 108_000}
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
        }
    )
    # Complete the anonymous Alfa episode with a post-baseline active sample.
    manager.update_from_graphics_shm(
        {"car_model": "Alfa", "status_name": "AC_LIVE", "current_lap_time_ms": 108_000}
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "Alfa",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
        }
    )
    anonymous = manager.get_latest_lap_completion()
    assert anonymous is not None and anonymous.session_id is None

    manager.begin_session("bmw-session", car_model="BMW")
    manager.update_from_graphics_shm(
        {"car_model": "BMW", "status_name": "AC_LIVE", "current_lap_time_ms": 0}
    )
    manager.update_from_graphics_shm(
        {"car_model": "BMW", "status_name": "AC_LIVE", "current_lap_time_ms": 108_000}
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 0,
            "last_laptime_ms": 108_315,
        }
    )

    parser = LogParser(session_manager=manager)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    parser._last_shm_completion_observed_at = 0
    parser.current_session = SessionData(
        session_id="bmw-session", track="Monza", car="BMW", session_type="PRACTICE"
    )
    parser._sessions_by_id[parser.current_session.session_id] = parser.current_session
    lap = parser._take_ready_shm_lap()

    assert lap is not None and lap.lap_time_ms == 108_315
    assert manager.get_lap_completions_after(0.0) == [anonymous]


@pytest.mark.asyncio
async def test_restart_keeps_parser_owner_and_queued_completion() -> None:
    """Parser restart creates one owner transition before lifecycle reset."""
    manager = SharedSessionManager()
    old_session = SessionData(
        session_id="old-session",
        track="Monza",
        car="ks_bmw_m2_coupe",
        session_type="PRACTICE",
        car_uuid="bmw-uuid",
    )
    manager.begin_session(
        old_session.session_id,
        car_model=old_session.car,
        car_uuid=old_session.car_uuid,
    )
    _bmw_boundary(manager)
    parser = LogParser(session_manager=manager)
    parser.current_session = old_session
    parser.context.current_car = old_session.car
    parser.context.current_track = old_session.track
    parser._establish_session_ownership(old_session)

    home = MagicMock()
    capture = MagicMock()
    capture.is_capturing.return_value = True
    start_capture = AsyncMock()
    stop_capture = AsyncMock()
    lifecycle = SessionLifecycleService(
        home_page=home,
        session_manager=manager,
        telemetry_capture=capture,
        start_capture=start_capture,
        stop_capture=stop_capture,
    )
    parser.on_session_restart = lifecycle.handle_session_restart
    old_epoch = manager.get_session_epoch()

    await parser._emit_session_restart()

    assert parser.current_session is not None
    assert parser.current_session.session_id != old_session.session_id
    assert manager.get_active_session_id() == parser.current_session.session_id
    assert manager.get_session_epoch() == old_epoch + 1
    assert manager.get_lap_completions_after(0.0)
    assert manager.get_session_metadata_data().session_id == parser.current_session.session_id
    stop_capture.assert_awaited_once_with("session_restart", discard=True)
    start_capture.assert_awaited_once_with()
