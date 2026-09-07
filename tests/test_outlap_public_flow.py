"""Regression coverage for practice pit-exit/outlap coordination."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.log_parser import LogParser
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData
from src.models import LapCompletionData, LapState, SessionData, SharedSessionManager
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _publish_shm_completion(
    manager: SharedSessionManager,
    *,
    completed_laps: int,
    lap_time_ms: int,
    is_valid: bool,
) -> None:
    """Publish one timed-lap boundary through the real SHM manager API."""
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


def _format_lap_time(lap_time_ms: int) -> str:
    minutes, remainder = divmod(lap_time_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _analysis_frame(
    frame_number: int,
    *,
    lap_start_frame: int,
    lap_length_frames: int,
    status_name: str = "AC_LIVE",
) -> FrameData:
    """Build one analyzer-quality frame without touching saved telemetry."""
    lap_frame = frame_number - lap_start_frame
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_number,
        physics={"speed_kmh": 100.0, "gear": 3, "fuel": 5.0},
        graphics={
            "normalized_car_position": lap_frame / lap_length_frames,
            "has_authoritative_progress": True,
            "current_time_ms": frame_number * 100,
            "last_time_ms": 0,
            "completed_laps": 0,
            "is_valid_lap": False,
            "status_name": status_name,
            "session_phase": "Session",
            "is_in_pit_lane": False,
        },
    )


@pytest.mark.asyncio
async def test_valid_timed_lap_after_rejected_pit_prefix_is_not_suppressed(tmp_path):
    """Replay the audited Red Bull Ring shape through the public parser flow."""
    car_id = "45dee0b268b7dc7c-9bb207d2d0ce68ad"
    lap_times = [64419, 64344, 64509, 70218, 63804, 62916, 62808, 77901]
    lap_validity = [True, True, False, False, False, False, True, False]
    sectors = [
        (28761, 18165, 17493),
        (27633, 18636, 18075),
        (27975, 19353, 17181),
        (28305, 24066, 17847),
        (27135, 18315, 18354),
        (27561, 18387, 16968),
        (27699, 18543, 16566),
        (27111, 18177, 32613),
    ]

    session = SessionData(
        track="Red Bull Ring GP",
        car="ks_mazda_mx5_nd_cup",
        player_id="76561197986609341",
        session_type="PRACTICE",
        car_uuid=car_id,
    )
    manager = SharedSessionManager()
    manager.begin_session(
        session.session_id, car_model=session.car, car_uuid=session.car_uuid
    )
    for lap_number, (lap_time_ms, is_valid) in enumerate(
        zip(lap_times, lap_validity),
        start=1,
    ):
        _publish_shm_completion(
            manager,
            completed_laps=lap_number,
            lap_time_ms=lap_time_ms,
            is_valid=is_valid,
        )

    lines = [
        "[2026-08-18 15:00:00.000] [gameplay] [info] Outplap split",
        "[2026-08-18 15:00:01.000] [gameplay] [error] "
        "Couldn't create lap from opensplits (carId player): Splitcollection 1/3",
    ]
    for lap_number, (lap_time_ms, lap_sectors) in enumerate(
        zip(lap_times, sectors),
        start=1,
    ):
        lines.extend(
            [
                f"[2026-08-18 15:{lap_number:02d}:00.000] [physics] [info] "
                f"Lap test evOnLapCompleted {lap_number} completed",
                f"[2026-08-18 15:{lap_number:02d}:00.010] [gameplay] [info] "
                f"On Split start false end false id 0 splittime {lap_sectors[0]}",
                f"[2026-08-18 15:{lap_number:02d}:00.020] [gameplay] [info] "
                f"On Split start false end false id 1 splittime {lap_sectors[1]}",
                f"[2026-08-18 15:{lap_number:02d}:00.030] [gameplay] [info] "
                f"On Split start true end true id 2 splittime {lap_sectors[2]}",
                f"[2026-08-18 15:{lap_number:02d}:00.040] [gameplay] [info] "
                f"New lap carId {car_id}: {_format_lap_time(lap_time_ms)}",
            ]
        )

    log_file = tmp_path / "red-bull-ring-rejected-prefix.log"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    telemetry = MagicMock()
    telemetry.is_capturing.return_value = True
    home = MagicMock()
    home._lap_count = 0
    history = []
    submissions = MagicMock()
    service = LapProcessingService()

    def add_lap(*_args):
        home._lap_count += 1
        return MagicMock()

    home.add_lap.side_effect = add_lap

    async def present_lap(session, lap):
        await service.handle_lap_complete(
            session=session,
            lap=lap,
            home_page=home,
            telemetry_capture=telemetry,
            config=AppConfig(auto_submit=False, telemetry_enabled=True),
            session_manager=manager,
            pb_cache=MagicMock(),
            history_entries=history,
            schedule_submission=submissions,
            create_history_entry=lambda **values: SimpleNamespace(**values),
        )

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
    )
    parser.current_session = session
    parser._sessions_by_id[session.session_id] = session
    parser.context.player_id = "76561197986609341"
    parser.context.car_uuid = car_id
    parser.context.tyre.set_all("S")

    sessions = await parser.parse_file()

    assert len(sessions) == 1
    session = sessions[0]
    assert [lap.lap_number for lap in session.laps] == list(range(1, 9))
    assert [lap.lap_time_ms for lap in session.laps] == lap_times
    assert [lap.lap_state for lap in session.laps] == [
        LapState.VALID,
        LapState.VALID,
        LapState.INVALID_GAME,
        LapState.INVALID_GAME,
        LapState.INVALID_GAME,
        LapState.INVALID_GAME,
        LapState.VALID,
        LapState.INVALID_GAME,
    ]
    assert len(session.valid_laps) == 3
    assert session.best_lap is not None
    assert session.best_lap.lap_number == 7
    assert session.best_lap.lap_time_ms == 62808
    assert (
        session.laps[0].sector1_ms,
        session.laps[0].sector2_ms,
        session.laps[0].sector3_ms,
    ) == sectors[0]
    assert session.laps[0].sectors_consistent is True
    assert session.laps[0].validity_source == "shm_graphics"
    assert session.stints[0].lap_numbers == list(range(1, 9))

    # Feed the recovered parser result through the same presentation and
    # telemetry-boundary service used by the live client.
    for lap in session.laps:
        await present_lap(session, lap)

    assert [call.args[1].lap_time_ms for call in home.add_lap.call_args_list] == lap_times
    assert len(history) == 8
    assert [call.args for call in telemetry.record_lap_boundary.call_args_list] == [
        (lap_time_ms, lap_number, lap_state.value)
        for lap_number, (lap_time_ms, lap_state) in enumerate(
            zip(lap_times, [lap.lap_state for lap in session.laps]),
            start=1,
        )
    ]
    submissions.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_timed_lap_after_rejected_pit_prefix_reaches_diagnostics(
    tmp_path,
):
    """Replay the Red Bull Ring invalid-lap sequence through public flows."""
    car_id = "45dee0b268b7dc7c-9bb207d2d0ce68ad"
    lap_times = [61_854, 62_400]
    sectors = [(25_000, 18_000, 18_854), (25_200, 18_100, 19_100)]
    session = SessionData(
        track="Red Bull Ring GP",
        car="ks_mazda_mx5_nd_cup",
        player_id="76561197986609341",
        session_type="PRACTICE",
        car_uuid=car_id,
    )
    manager = SharedSessionManager()
    manager.begin_session(
        session.session_id, car_model=session.car, car_uuid=session.car_uuid
    )
    for lap_number, lap_time_ms in enumerate(lap_times, start=1):
        _publish_shm_completion(
            manager,
            completed_laps=lap_number,
            lap_time_ms=lap_time_ms,
            is_valid=False,
        )

    lines = [
        "[2026-08-19 23:25:55.000] [gameplay] [info] Outplap split",
        "[2026-08-19 23:25:56.000] [gameplay] [error] "
        "Couldn't create lap from opensplits (carId player): Splitcollection 1/3",
    ]
    for lap_number, (lap_time_ms, lap_sectors) in enumerate(
        zip(lap_times, sectors),
        start=1,
    ):
        lines.extend(
            [
                f"[2026-08-19 23:{25 + lap_number:02d}:00.000] [physics] [info] "
                f"Lap test evOnLapCompleted {lap_number} completed",
                f"[2026-08-19 23:{25 + lap_number:02d}:00.010] [gameplay] [info] "
                f"On Split start false end false id 0 splittime {lap_sectors[0]}",
                f"[2026-08-19 23:{25 + lap_number:02d}:00.020] [gameplay] [info] "
                f"On Split start false end false id 1 splittime {lap_sectors[1]}",
                f"[2026-08-19 23:{25 + lap_number:02d}:00.030] [gameplay] [info] "
                f"On Split start true end true id 2 splittime {lap_sectors[2]}",
                f"[2026-08-19 23:{25 + lap_number:02d}:00.040] [gameplay] [info] "
                f"New lap carId {car_id}: {_format_lap_time(lap_time_ms)}",
            ]
        )

    log_file = tmp_path / "red-bull-ring-invalid-rejected-prefix.log"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    telemetry = MagicMock()
    telemetry.is_capturing.return_value = True
    home = MagicMock()
    home._lap_count = 0
    history = []
    submissions = MagicMock()
    service = LapProcessingService()

    def add_lap(*_args):
        home._lap_count += 1
        return MagicMock()

    home.add_lap.side_effect = add_lap

    async def present_lap(session, lap):
        await service.handle_lap_complete(
            session=session,
            lap=lap,
            home_page=home,
            telemetry_capture=telemetry,
            config=AppConfig(auto_submit=True, telemetry_enabled=True),
            session_manager=manager,
            pb_cache=MagicMock(),
            history_entries=history,
            schedule_submission=submissions,
            create_history_entry=lambda **values: SimpleNamespace(**values),
        )

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
    )
    parser.current_session = session
    parser._sessions_by_id[session.session_id] = session
    parser.context.player_id = "76561197986609341"
    parser.context.car_uuid = car_id
    parser.context.tyre.set_all("S")

    sessions = await parser.parse_file()

    assert len(sessions) == 1
    session = sessions[0]
    assert [lap.lap_number for lap in session.laps] == [1, 2]
    assert [lap.lap_time_ms for lap in session.laps] == lap_times
    assert [lap.lap_state for lap in session.laps] == [
        LapState.INVALID_GAME,
        LapState.INVALID_GAME,
    ]
    recovered = session.laps[0]
    assert recovered.lap_time_str == "01:01.854"
    assert recovered.validity_source == "shm_graphics"
    assert recovered.stint_number == 1
    assert session.stints[0].lap_numbers == [1, 2]

    for lap in session.laps:
        await present_lap(session, lap)

    assert len(history) == 2
    assert history[0].lap_time_ms == 61_854
    assert history[0].was_valid is False
    assert [call.args for call in telemetry.record_lap_boundary.call_args_list] == [
        (61_854, 1, "INVALID_GAME"),
        (62_400, 2, "INVALID_GAME"),
    ]
    submissions.assert_not_called()

    frames = []
    for frame_number in range(180):
        second_lap = frame_number >= 80
        paused = 120 <= frame_number < 140
        frames.append(
            _analysis_frame(
                frame_number,
                lap_start_frame=80 if second_lap else 0,
                lap_length_frames=100 if second_lap else 80,
                status_name="AC_PAUSE" if paused else "AC_LIVE",
            )
        )
    boundary_frames = [80, 180]
    analyzer_boundaries = [
        (frame, *call.args)
        for frame, call in zip(
            boundary_frames,
            telemetry.record_lap_boundary.call_args_list,
        )
    ]
    analyzer = TelemetryAnalyzer(str(tmp_path))
    with (
        patch.object(
            analyzer,
            "_generate_html",
            new=AsyncMock(return_value="report.html"),
        ) as html_spy,
        patch.object(
            analyzer,
            "_generate_ai_prompt",
            new=AsyncMock(return_value="prompt.txt"),
        ),
    ):
        await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=analyzer_boundaries,
            output_prefix="red_bull_invalid",
        )

    analysis = html_spy.await_args.args[0]
    assert analysis["analysis_mode"] == "diagnostic"
    assert [lap["lap_num"] for lap in analysis["laps"]] == [1, 2]
    assert [lap["is_valid"] for lap in analysis["laps"]] == [False, False]
    second_lap = analysis["laps"][1]
    assert len(second_lap["track"]) == 80
    assert all(
        point["status_name"] != "AC_PAUSE" for point in second_lap["track"]
    )
    assert any(
        "invalid laps are shown for diagnostics only" in note
        for note in analysis["analysis_notes"]
    )
    assert any(
        "paused telemetry samples" in note for note in analysis["analysis_notes"]
    )


@pytest.mark.asyncio
async def test_laguna_live_log_flow_records_outlap_boundary_without_card(tmp_path):
    """Replay the signal order observed in the 2026-08-18 live session.

    ACE crosses the timing line while the car is still in pit lane, rejects
    that short prefix, and then reports the following full circuit as a normal
    ``New lap``. It is nevertheless the structural outlap: telemetry needs its
    end boundary, while the UI/history/submission paths must not treat it as a
    result.
    """
    car_id = "45dee0b268b7dc7c-9bb207d2d0ce68ad"
    log_file = tmp_path / "laguna-outlap.log"
    log_file.write_text(
        "\n".join(
            (
                "[2026-08-18 11:31:25.052] [gameplay] [info] Outplap split",
                "[2026-08-18 11:31:41.838] [gameplay] [error] "
                "Couldn't create lap from opensplits (carId player): Splitcollection 1/3",
                "[2026-08-18 11:32:26.244] [gameplay] [info] "
                "On Split start false end false id 0 splittime 44403",
                "[2026-08-18 11:32:53.895] [gameplay] [info] "
                "On Split start false end false id 1 splittime 27651",
                "[2026-08-18 11:33:36.971] [physics] [info] "
                "Lap test evOnLapCompleted 2 completed",
                "[2026-08-18 11:33:37.333] [gameplay] [info] "
                "On Split start true end true id 2 splittime 43440",
                f"[2026-08-18 11:33:37.333] [gameplay] [info] "
                f"New lap carId {car_id}: 01:55.494",
                "[2026-08-18 11:33:37.420] [network] [info] "
                "Relevant onSplit for Combo 6@2: laptime 115494, valid false, "
                "flags 1, lap 1 (prev 0)",
                "[2026-08-18 11:34:54.441] [gameplay] [info] "
                "On Split start false end false id 0 splittime 77109",
                "[2026-08-18 11:35:20.690] [gameplay] [info] "
                "On Split start false end false id 1 splittime 26247",
                "[2026-08-18 11:36:10.472] [physics] [info] "
                "Lap test evOnLapCompleted 3 completed",
                "[2026-08-18 11:36:10.840] [gameplay] [info] "
                "On Split start true end true id 2 splittime 50151",
                f"[2026-08-18 11:36:10.840] [gameplay] [info] "
                f"New lap carId {car_id}: 02:33.507",
                "[2026-08-18 11:36:10.865] [network] [info] "
                "Relevant onSplit for Combo 6@2: laptime 153507, valid false, "
                "flags 1, lap 2 (prev 1)",
                "[2026-08-18 11:36:52.962] [gameplay] [info] "
                "On Split start false end false id 0 splittime 42120",
                "[2026-08-18 11:37:19.918] [gameplay] [info] "
                "On Split start false end false id 1 splittime 26958",
                "[2026-08-18 11:38:07.538] [physics] [info] "
                "Lap test evOnLapCompleted 4 completed",
                "[2026-08-18 11:38:07.901] [gameplay] [info] "
                "On Split start true end true id 2 splittime 47982",
                f"[2026-08-18 11:38:07.901] [gameplay] [info] "
                f"New lap carId {car_id}: 01:57.060",
                "[2026-08-18 11:38:07.927] [network] [info] "
                "Relevant onSplit for Combo 6@2: laptime 117060, valid true, "
                "flags 2, lap 3 (prev 2)",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    manager = SharedSessionManager()
    telemetry = MagicMock()
    telemetry.is_capturing.return_value = True
    home = MagicMock()
    home._lap_count = 0
    history = []
    submissions = MagicMock()
    service = LapProcessingService()

    def add_lap(*_args):
        home._lap_count += 1
        return MagicMock()

    home.add_lap.side_effect = add_lap

    async def on_lap(session, lap):
        await service.handle_lap_complete(
            session=session,
            lap=lap,
            home_page=home,
            telemetry_capture=telemetry,
            config=AppConfig(auto_submit=True, telemetry_enabled=True),
            session_manager=manager,
            pb_cache=MagicMock(),
            history_entries=history,
            schedule_submission=submissions,
            create_history_entry=lambda **values: SimpleNamespace(**values),
        )

    parser = LogParser(
        log_path=str(log_file),
        on_lap_complete=on_lap,
        session_manager=manager,
    )
    parser.current_session = SessionData(
        track="laguna_seca gp",
        car="ks_mazda_mx5_nd_cup",
        player_id="76561197986609341",
        session_type="PRACTICE",
        car_uuid=car_id,
    )
    parser.context.player_id = "76561197986609341"
    parser.context.car_uuid = car_id
    parser.context.tyre.set_all("S")

    sessions = await parser.parse_file()

    assert len(sessions) == 1
    assert [lap.lap_state for lap in sessions[0].laps] == [
        LapState.OUTLAP,
        LapState.INVALID_GAME,
        LapState.VALID,
    ]
    assert [call.args[1].lap_time_ms for call in home.add_lap.call_args_list] == [
        153507,
        117060,
    ]
    assert len(history) == 2
    submissions.assert_called_once()
    assert [call.args for call in telemetry.record_lap_boundary.call_args_list] == [
        (115494, 1, "OUTLAP"),
        (153507, 2, "INVALID_GAME"),
        (117060, 3, "VALID"),
    ]


def test_shm_completion_waits_for_armed_outlap_log_classification():
    """A delayed log must not allow SHM to publish the outlap as invalid."""
    completion = LapCompletionData(
        completed_laps=2,
        lap_time_ms=115494,
        is_valid=False,
        timestamp="2026-08-18T11:33:37+00:00",
        observed_at=1.0,
    )
    manager = MagicMock()
    manager.get_latest_lap_completion.return_value = None
    manager.get_lap_completions_after.return_value = [completion]
    parser = LogParser(session_manager=manager)
    parser.current_session = SessionData(
        track="laguna_seca gp",
        car="ks_mazda_mx5_nd_cup",
        session_type="PRACTICE",
    )
    parser._ip.is_outlap = True

    assert parser._take_ready_shm_lap() is None
    assert parser.current_session.laps == []
    assert parser._last_shm_completion_observed_at == completion.observed_at


@pytest.mark.parametrize(
    (
        "completion_validity",
        "completion_counters",
        "physics_lap_number",
        "expected_state",
    ),
    [
        (True, [1], 1, LapState.VALID),
        (False, [1], 1, LapState.INVALID_GAME),
        (False, [2], 2, LapState.OUTLAP),
        (None, [], 1, LapState.OUTLAP),
        (False, [1, 1], 1, LapState.OUTLAP),
    ],
    ids=(
        "valid-exact",
        "invalid-counters-aligned",
        "invalid-parser-counter-mismatch",
        "missing-completion",
        "ambiguous-completion",
    ),
)
def test_provisional_outlap_reconciliation_requires_unambiguous_completion(
    completion_validity,
    completion_counters,
    physics_lap_number,
    expected_state,
):
    manager = MagicMock()
    manager.get_lap_completions_after.return_value = [
        LapCompletionData(
            completed_laps=completed_laps,
            lap_time_ms=115494,
            is_valid=completion_validity,
            timestamp="2026-08-18T11:33:37+00:00",
            observed_at=float(index),
        )
        for index, completed_laps in enumerate(completion_counters, start=1)
    ]
    manager.get_lap_validity_data.return_value = SimpleNamespace(
        is_valid=True,
        source="shm_graphics",
    )
    parser = LogParser(session_manager=manager)
    parser.current_session = SessionData(
        track="laguna_seca gp",
        car="ks_mazda_mx5_nd_cup",
        session_type="PRACTICE",
    )
    pending = SimpleNamespace(
        lap_number=1,
        physics_lap_number=physics_lap_number,
        lap_time_ms=115494,
        lap_state=LapState.OUTLAP,
        lap_type=LapState.OUTLAP.value,
        is_valid=False,
        validity_source="heuristic",
        tyre_compound="S",
        fuel_used=None,
        fuel_reliable=True,
        stint_number=1,
    )

    parser._apply_shm_fallback_validity(pending)

    assert pending.lap_state == expected_state
    if expected_state == LapState.VALID:
        assert pending.is_valid is True
        assert pending.validity_source == "shm_graphics"
        assert parser.current_session.stints[0].lap_numbers == [1]
    elif expected_state == LapState.INVALID_GAME:
        assert pending.is_valid is False
        assert pending.validity_source == "shm_graphics"
        assert parser.current_session.stints[0].lap_numbers == [1]
    else:
        assert pending.is_valid is False
        assert pending.validity_source == "heuristic"
        assert parser.current_session.stints == []
