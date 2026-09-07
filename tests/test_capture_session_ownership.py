"""Regression tests for telemetry capture session ownership."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.security import GameProcessStatus
from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData, TelemetryCapture
from src.models import SharedSessionManager


def _live_graphics(car_model: str, *, current_lap_time_ms: int = 12_000) -> dict:
    return {
        "car_model": car_model,
        "status_name": "AC_LIVE",
        "session_phase": "RACE",
        "current_lap_time_ms": current_lap_time_ms,
        "last_laptime_ms": 0,
        "total_lap_count": 0,
        "is_valid_lap": True,
    }


def _capture_with_owner(*, manager: SharedSessionManager, record_frames: bool = True) -> TelemetryCapture:
    capture = TelemetryCapture(
        hz=1_000.0,
        session_manager=manager,
        record_frames=record_frames,
    )
    capture._capture_origin = manager.get_session_origin()
    capture._running = True
    capture._recording_awaiting_boundary = False
    capture._readers = {"physics": MagicMock(size=1)}
    return capture


async def _run_samples(capture: TelemetryCapture, sample) -> None:
    with (
        patch.object(capture, "_reconnect_missing"),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch.object(capture, "_capture_frame", side_effect=sample),
    ):
        await capture._capture_loop()


@pytest.mark.asyncio
async def test_origin_change_keeps_sampler_alive_but_rejects_new_car_frames():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    capture = _capture_with_owner(manager=manager)
    seen = []

    def sample(frame_number: int) -> FrameData:
        seen.append(frame_number)
        capture._last_sample_had_data = True
        if frame_number == 1:
            manager.begin_session("session-alfa", car_model="Alfa")
        if frame_number == 2:
            capture._running = False
        return FrameData(
            timestamp=f"2026-09-07T00:00:0{frame_number}Z",
            frame_number=frame_number,
            physics={"speed_kmh": 100.0},
        )

    await _run_samples(capture, sample)

    assert seen == [0, 1, 2]
    assert [frame.frame_number for frame in capture.get_frames()] == [0]
    assert capture.get_capture_origin().session_id == "session-bmw"


@pytest.mark.asyncio
async def test_start_capture_freezes_manager_origin():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    capture = TelemetryCapture(
        hz=1_000.0,
        session_manager=manager,
        record_frames=False,
    )

    with patch.object(capture, "_connect_regions", return_value={}):
        assert await capture.start_capture() is True
    try:
        assert capture.get_capture_origin() == manager.get_session_origin()
    finally:
        await capture.stop_capture("manual")


@pytest.mark.asyncio
async def test_anonymous_graphics_epoch_does_not_inherit_stale_track_metadata():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_static_shm({"track": "BMW Track"})
    manager.update_from_graphics_shm(_live_graphics("Alfa"))
    capture = TelemetryCapture(
        hz=1_000.0,
        session_manager=manager,
        record_frames=False,
    )

    with patch.object(capture, "_connect_regions", return_value={}):
        assert await capture.start_capture() is True
    try:
        assert capture.get_capture_origin().session_id is None
        assert capture.get_capture_track_name() is None
        capture._readers = {
            "static": MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        }
        with patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "Alfa Track"},
        ):
            capture._capture_frame(0)
        assert capture.get_capture_track_name() == "Alfa Track"
    finally:
        await capture.stop_capture("manual")


@pytest.mark.asyncio
async def test_validity_only_continues_sampling_across_origin_change():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    capture = _capture_with_owner(manager=manager, record_frames=False)
    seen = []

    def sample(frame_number: int) -> FrameData:
        seen.append(frame_number)
        capture._last_sample_had_data = True
        if frame_number == 1:
            manager.begin_session("session-alfa", car_model="Alfa")
        if frame_number == 2:
            capture._running = False
        return FrameData(
            timestamp=f"2026-09-07T00:00:0{frame_number}Z",
            frame_number=frame_number,
            physics={"speed_kmh": 100.0},
        )

    await _run_samples(capture, sample)

    assert seen == [0, 1, 2]
    assert capture.get_frames() == []
    assert capture.is_capturing() is False


@pytest.mark.asyncio
async def test_graphics_leading_car_change_is_not_retained_without_log_owner():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    capture = _capture_with_owner(manager=manager)

    def sample(frame_number: int) -> FrameData:
        capture._last_sample_had_data = True
        if frame_number == 1:
            manager.update_from_graphics_shm(_live_graphics("Alfa"))
        if frame_number == 2:
            capture._running = False
        return FrameData(
            timestamp=f"2026-09-07T00:00:0{frame_number}Z",
            frame_number=frame_number,
            physics={"speed_kmh": 100.0},
        )

    await _run_samples(capture, sample)

    assert [frame.frame_number for frame in capture.get_frames()] == [0]
    assert manager.get_active_session_id() is None
    assert manager.get_session_origin().epoch != capture.get_capture_origin().epoch


@pytest.mark.asyncio
async def test_parser_leading_session_waits_for_matching_graphics_car():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.begin_session("session-alfa", car_model="Alfa")
    assert manager.get_session_origin().graphics_ready is False

    capture = _capture_with_owner(manager=manager)

    def sample(frame_number: int) -> FrameData:
        capture._last_sample_had_data = True
        capture._last_frame_origin_stable = frame_number >= 2
        manager.update_from_graphics_shm(
            _live_graphics("BMW" if frame_number == 0 else "Alfa")
        )
        if frame_number == 2:
            capture._running = False
        return FrameData(
            timestamp=f"2026-09-07T00:00:0{frame_number}Z",
            frame_number=frame_number,
            physics={"speed_kmh": 100.0},
        )

    await _run_samples(capture, sample)

    assert [frame.frame_number for frame in capture.get_frames()] == [2]
    assert capture.get_capture_origin().session_id == "session-alfa"
    assert manager.get_session_origin().graphics_ready is True


def test_transition_frames_do_not_write_outgoing_static_or_physics_state():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.begin_session("session-alfa", car_model="Alfa")
    capture = _capture_with_owner(manager=manager)
    readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    capture._readers = readers

    with (
        patch(
            "src.core.telemetry_capture.peek_graphics_validity",
            return_value={},
        ),
        patch(
            "src.core.telemetry_capture.decode_graphics",
            side_effect=[_live_graphics("BMW"), _live_graphics("Alfa"), _live_graphics("Alfa")],
        ),
        patch(
            "src.core.telemetry_capture.decode_static",
            side_effect=[
                {"track": "stale-bmw"},
                {"track": "transition-alfa"},
                {"track": "stable-alfa"},
            ],
        ),
        patch(
            "src.core.telemetry_capture.decode_physics",
            return_value={"speed_kmh": 100.0},
        ),
    ):
        capture._capture_frame(0)
        assert manager.get_session_metadata_data().track == "Unknown"
        capture._capture_frame(1)
        assert manager.get_session_metadata_data().track == "Unknown"
        capture._capture_frame(2)

    assert manager.get_session_metadata_data().track == "stable-alfa"
    assert capture.get_capture_origin().session_id == "session-alfa"
    assert capture.get_capture_origin().graphics_car_model == "Alfa"
    assert capture.get_capture_track_name() == "stable-alfa"


def test_secondary_shm_commit_aborts_when_origin_rolls_after_graphics():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_static_shm({"track": "BMW Track"})
    capture = _capture_with_owner(manager=manager)
    capture._capture_track_name = "BMW Track"
    original_static = manager.update_from_static_shm
    readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    capture._readers = readers

    def delayed_static(static_data, *, expected_origin=None):
        manager.begin_session("session-alfa", car_model="Alfa", car_uuid="alfa")
        return original_static(static_data, expected_origin=expected_origin)

    with (
        patch.object(
            manager,
            "update_from_static_shm",
            side_effect=delayed_static,
        ),
        patch(
            "src.core.telemetry_capture.peek_graphics_validity",
            return_value={},
        ),
        patch(
            "src.core.telemetry_capture.decode_graphics",
            return_value=_live_graphics("BMW"),
        ),
        patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "BMW Track"},
        ),
        patch(
            "src.core.telemetry_capture.decode_physics",
            return_value={"speed_kmh": 222.0},
        ),
    ):
        frame = capture._capture_frame(1)

    assert manager.get_session_metadata_data().track != "BMW Track"
    assert capture.get_capture_track_name() == "BMW Track"
    assert manager._session_data.max_speed != 222.0
    assert capture._last_frame_origin_stable is False
    assert frame.car_model == "BMW"


def test_owns_session_is_frozen_until_capture_restarts():
    manager = SharedSessionManager()
    manager.begin_session("session-bmw", car_model="BMW")
    capture = _capture_with_owner(manager=manager)

    manager.begin_session("session-alfa", car_model="Alfa")

    assert capture.owns_session("session-bmw") is True
    assert capture.owns_session("session-alfa") is False


def test_anonymous_capture_accepts_same_epoch_graphics_confirmed_binding():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_graphics_shm(_live_graphics("Alfa"))
    capture = _capture_with_owner(manager=manager)

    manager.begin_session("session-alfa", car_model="Alfa")

    assert capture.get_capture_origin().session_id is None
    assert capture.owns_session("session-alfa") is True
    assert capture._capture_origin_ready() is True


def test_anonymous_capture_rejects_same_epoch_binding_until_graphics_confirms():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_graphics_shm(_live_graphics("Alfa"))
    capture = _capture_with_owner(manager=manager)

    manager.begin_session("session-bmw", car_model="BMW")

    assert capture.owns_session("session-bmw") is False
    assert capture._capture_origin_ready() is False

    # The matching BMW graphics transition clears manager pending state in
    # the same epoch, but it still must not reopen this frozen Alfa capture.
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    assert manager.get_session_origin().graphics_ready is True
    assert capture.owns_session("session-bmw") is False
    assert capture._capture_origin_ready() is False


def test_mismatched_parser_session_cannot_adopt_anonymous_completion():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("BMW"))
    manager.update_from_graphics_shm(_live_graphics("Alfa"))
    manager.update_from_graphics_shm(_live_graphics("Alfa", current_lap_time_ms=108_000))
    manager.update_from_graphics_shm(
        {
            **_live_graphics("Alfa", current_lap_time_ms=0),
            "last_laptime_ms": 108_315,
        }
    )
    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.session_id is None

    manager.begin_session("session-bmw", car_model="BMW")

    assert manager.get_lap_completion_owner(completion)[1] is None


def test_anonymous_capture_does_not_resume_after_mismatched_parser_epoch():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(_live_graphics("Alfa"))
    capture = _capture_with_owner(manager=manager)
    original_epoch = capture.get_capture_origin().epoch

    manager.begin_session("session-bmw", car_model="BMW")
    assert manager.get_session_epoch() != original_epoch
    manager.update_from_graphics_shm(_live_graphics("BMW"))

    assert capture.owns_session("session-bmw") is False
    assert capture._capture_origin_ready() is False


def _m3_graphics(frame_number: int) -> dict:
    if frame_number < 50:
        current_lap_time_ms = 12_000
    elif frame_number < 650:
        current_lap_time_ms = (frame_number - 50) * 100
    else:
        current_lap_time_ms = (frame_number - 650) * 100
    return {
        "car_model": "BMW M3 E30 Sport Evo (Evolution III)",
        "status_name": "AC_LIVE",
        "session_phase": "PRACTICE",
        "current_lap_time_ms": current_lap_time_ms,
        "last_laptime_ms": 0,
        "total_lap_count": 0,
        "is_valid_lap": True,
        "normalized_car_position": (frame_number % 10) / 10.0,
        "has_authoritative_progress": True,
    }


@pytest.mark.asyncio
async def test_m3_log_display_alias_retains_two_laps_and_excludes_outlap(tmp_path):
    """The public capture loop accepts the verified log/display M3 alias."""
    manager = SharedSessionManager()
    manager.begin_session(
        "m3-session",
        car_model="ks_bmw_m3_e30_evo_iii",
        car_uuid="m3-uuid",
    )
    capture = TelemetryCapture(
        output_dir=str(tmp_path),
        hz=10.0,
        session_manager=manager,
        record_frames=True,
    )
    readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    capture._interval = 0.00001
    decode_count = 0

    def decode_graphics(_raw):
        nonlocal decode_count
        if decode_count == 50:
            capture.record_lap_boundary(55_000, 1, "OUTLAP")
        if decode_count == 650:
            capture.record_lap_boundary(60_000, 2, "VALID")
        if decode_count == 1_260:
            capture.record_lap_boundary(61_000, 3, "VALID")
        if decode_count == 1_310:
            capture._running = False
        result = _m3_graphics(decode_count)
        decode_count += 1
        return result

    with (
        patch.object(capture, "_connect_regions", return_value=readers),
        patch.object(capture, "_reconnect_missing"),
        patch("src.core.telemetry_capture.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("src.core.telemetry_capture.peek_graphics_validity", return_value={}),
        patch("src.core.telemetry_capture.decode_graphics", side_effect=decode_graphics),
        patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "Laguna Seca", "session_name": "PRACTICE"},
        ),
        patch(
            "src.core.telemetry_capture.decode_physics",
            return_value={"speed_kmh": 100.0},
        ),
    ):
        assert await capture.start_capture() is True
        await capture._task
        frames = await capture.stop_capture("manual")

    assert len(frames) >= 1_260
    assert frames[0].frame_number == 50
    first_lap_frames = [frame for frame in frames if 50 <= frame.frame_number < 650]
    second_lap_frames = [frame for frame in frames if 650 <= frame.frame_number < 1_260]
    assert len(first_lap_frames) == 600
    assert len(second_lap_frames) == 610
    assert first_lap_frames[-1].graphics["current_lap_time_ms"] == 59_900
    assert second_lap_frames[-1].graphics["current_lap_time_ms"] == 60_900
    assert capture.get_capture_track_name() == "Laguna Seca"
    assert [(b.lap_time_ms, b.lap_number, b.lap_type) for b in capture.get_lap_boundaries()] == [
        (60_000, 2, "VALID"),
        (61_000, 3, "VALID"),
    ]

    timestamp_origin = datetime(2026, 9, 7, tzinfo=timezone.utc)
    for frame in frames:
        frame.timestamp = (
            timestamp_origin
            + timedelta(seconds=(frame.frame_number - 50) / 10.0)
        ).isoformat()
    assert frames[1].timestamp == (
        timestamp_origin + timedelta(seconds=0.1)
    ).isoformat()

    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    boundaries = [
        (boundary.frame_index, boundary.lap_time_ms, boundary.lap_number, boundary.lap_type)
        for boundary in capture.get_lap_boundaries()
    ]
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            frames,
            hz=10.0,
            track_name="Laguna Seca",
            game_lap_boundaries=boundaries,
            output_prefix="m3-alias",
            capture_origin=capture.get_capture_origin(),
            capture_track_name=capture.get_capture_track_name(),
        )

    report = html_spy.await_args.args[0]
    assert [lap["lap_num"] for lap in report["laps"]] == [2, 3]
    assert [lap["lap_time_s"] for lap in report["laps"]] == pytest.approx([60.0, 61.0])


def test_m3_alias_allows_source_priority_but_rejects_cross_car():
    manager = SharedSessionManager()
    manager.begin_session("m3-session", car_model="ks_bmw_m3_e30_evo_iii")
    manager.update_from_graphics_shm(_m3_graphics(2))

    assert manager.update_from_static_shm({"track": "Laguna Seca"}) is True
    assert manager.get_session_metadata_data().track == "Laguna Seca"

    old_origin = manager.get_session_origin()
    manager.begin_session(
        "alfa-session",
        car_model="ks_alfa_romeo_giulia_gtam",
    )
    assert manager.update_from_static_shm(
        {"track": "Alfa Track"}, expected_origin=old_origin
    ) is False
    assert manager.get_session_metadata_data().track == "Unknown"


def test_m3_alias_keeps_validity_only_sampling_without_retaining_frames():
    manager = SharedSessionManager()
    manager.begin_session("m3-session", car_model="ks_bmw_m3_e30_evo_iii")
    capture = _capture_with_owner(manager=manager, record_frames=False)
    readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    capture._readers = readers

    with (
        patch("src.core.telemetry_capture.peek_graphics_validity", return_value={}),
        patch("src.core.telemetry_capture.decode_graphics", return_value=_m3_graphics(2)),
        patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "Laguna Seca", "session_name": "PRACTICE"},
        ),
        patch(
            "src.core.telemetry_capture.decode_physics",
            return_value={"speed_kmh": 100.0},
        ),
    ):
        frame = capture._capture_frame(0)

    assert frame.car_model == "BMW M3 E30 Sport Evo (Evolution III)"
    assert capture._last_frame_origin_stable is True
    assert capture.get_frames() == []
    assert manager.get_session_metadata_data().track == "Laguna Seca"
