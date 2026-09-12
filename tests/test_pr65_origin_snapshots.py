"""Regression coverage for atomic, narrow session origin snapshots."""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData
from src.models import (
    AnalysisSnapshot,
    LapCompletionData,
    LapTimingData,
    OriginRelation,
    OriginSnapshotResult,
    SharedSessionManager,
)


class _NonCopyable:
    def __deepcopy__(self, memo):  # pragma: no cover - failure is the assertion
        raise AssertionError("the complete session object was copied")


def test_submission_snapshot_is_narrow_frozen_and_does_not_copy_setup():
    manager = SharedSessionManager()
    manager.begin_session("session-a", car_model="BMW", car_uuid="uuid-a")
    manager.update_player_identification_from_logs(
        {"steam_id": "steam-a", "car_model": "BMW", "car_uuid": "uuid-a"}
    )
    manager.update_session_metadata_from_static_shm(
        {"track": "monza", "session": "RACE", "ac_evo_version": "0.9"}
    )
    manager.update_lap_timing_from_graphics_shm(
        1, {"last_laptime_ms": 91_234}
    )
    manager._session_data.car_setup = {"unrelated": _NonCopyable()}

    origin = manager.get_session_origin()
    result = manager.get_submission_snapshot_for_origin(origin, lap_number=1)

    assert result.relation is OriginRelation.ACTIVE
    assert result.current_origin == origin
    assert result.snapshot is not None
    assert result.snapshot.lap_time_ms == 91_234
    assert result.snapshot.origin == origin
    with pytest.raises(FrozenInstanceError):
        result.snapshot.car_model = "Alfa"

    manager._session_data.lap_timing[2] = LapTimingData(
        lap_number=2, completed_lap_time=102_345.0
    )
    second = manager.get_submission_snapshot_for_origin(origin, lap_number=2)
    assert second.snapshot is not None
    assert second.snapshot.lap_time_ms == 102_345

    manager._session_data.lap_timing[3] = LapTimingData(
        lap_number=3, last_lap_time_ms=0, completed_lap_time=111_222.0
    )
    third = manager.get_submission_snapshot_for_origin(origin, lap_number=3)
    assert third.snapshot is not None
    assert third.snapshot.lap_time_ms == 111_222


def test_analysis_snapshot_is_narrow_frozen_and_does_not_copy_setup():
    manager = SharedSessionManager()
    manager.begin_session("session-a", car_model="BMW")
    manager._session_data.car_setup = {"unrelated": _NonCopyable()}
    manager._session_data.lap_timing[1] = LapTimingData(
        lap_number=1, completed_lap_time=91_234.0
    )
    manager._session_data.lap_validity[1] = type(
        "Validity", (), {"is_valid": True}
    )()

    result = manager.get_analysis_snapshot_for_origin(manager.get_session_origin())

    assert isinstance(result.snapshot, AnalysisSnapshot)
    assert result.snapshot.timing_records == ((1, 91_234.0),)
    assert result.snapshot.validity_records == ((1, True),)
    with pytest.raises(FrozenInstanceError):
        result.snapshot.car_model = "Alfa"


def _analysis_frame(frame_number: int) -> FrameData:
    return FrameData(
        timestamp=f"2026-09-12T00:00:{frame_number:02d}Z",
        frame_number=frame_number,
        physics={"speed_kmh": 100.0},
        graphics={
            "normalized_car_position": (frame_number % 60) / 60.0,
            "has_authoritative_progress": True,
        },
        static={"track": "Captured Track"},
    )


@pytest.mark.asyncio
async def test_analyzer_no_origin_uses_one_atomic_snapshot_before_rollover(tmp_path):
    manager = SharedSessionManager()
    manager.begin_session("old-session", car_model="BMW")
    manager.update_player_identification_from_logs({"car_model": "BMW"})
    original_snapshot = manager.get_analysis_snapshot_for_origin

    def snapshot_then_rollover(origin):
        result = original_snapshot(origin)
        manager.begin_session("new-session", car_model="Alfa")
        return result

    manager.get_analysis_snapshot_for_origin = snapshot_then_rollover
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            [_analysis_frame(index) for index in range(61)],
            hz=10.0,
            track_name="Current Track",
            game_lap_boundaries=[(60, 60_000, 1, "VALID")],
        )

    assert manager.get_active_session_id() == "new-session"
    assert html_spy.await_args.args[0]["car"] == "BMW"


@pytest.mark.asyncio
async def test_analyzer_rejected_modern_snapshot_does_not_read_current_car(tmp_path):
    manager = SharedSessionManager()
    current = manager.get_session_origin()
    manager.get_analysis_snapshot_for_origin = MagicMock(
        return_value=OriginSnapshotResult(OriginRelation.MISMATCH, current, None)
    )
    manager.get_car = MagicMock(side_effect=AssertionError("stale car read"))
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            [_analysis_frame(index) for index in range(61)],
            hz=10.0,
            track_name="Current Track",
            game_lap_boundaries=[(60, 60_000, 1, "VALID")],
        )

    assert html_spy.await_args.args[0]["car"] is None
    manager.get_car.assert_not_called()


@pytest.mark.asyncio
async def test_analyzer_rejected_old_provenance_snapshot_does_not_read_current_car(tmp_path):
    manager = SharedSessionManager()
    manager.get_analysis_snapshot_for_origin = None
    manager.get_data_for_origin = MagicMock(return_value=None)
    manager.get_car = MagicMock(side_effect=AssertionError("stale car read"))
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    with (
        patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
        patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
    ):
        await analyzer.analyze(
            [_analysis_frame(index) for index in range(61)],
            hz=10.0,
            track_name="Current Track",
            game_lap_boundaries=[(60, 60_000, 1, "VALID")],
        )

    assert html_spy.await_args.args[0]["car"] is None
    manager.get_car.assert_not_called()


def test_origin_relation_covers_late_binding_closed_and_epoch_mismatch():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm({"car_model": "BMW"})
    manager.update_from_graphics_shm({"car_model": "Alfa"})
    anonymous = manager.get_session_origin()

    manager.begin_session("alfa-session", car_model="Alfa")
    relation, current = manager.get_origin_relation(anonymous)
    assert relation is OriginRelation.LATE_BOUND
    assert current.session_id == "alfa-session"

    identified = manager.get_session_origin()
    manager.end_session("alfa-session")
    relation, current = manager.get_origin_relation(identified)
    assert relation is OriginRelation.CLOSED
    assert current.session_id is None

    manager.begin_session("new-session", car_model="Alfa")
    relation, _ = manager.get_origin_relation(anonymous)
    assert relation is OriginRelation.MISMATCH


def test_identity_contradiction_is_rejected_before_mutation():
    manager = SharedSessionManager()
    manager.begin_session("session-a", car_model="BMW", car_uuid="uuid-a")
    before = manager.get_player_identification()

    manager.update_player_identification_from_logs(
        {"steam_id": "steam-b", "car_model": "Alfa", "car_uuid": "uuid-b"}
    )

    assert manager.get_player_identification() == before
    assert manager.get_active_session_id() == "session-a"


def test_closed_identity_rejects_conflicting_log_and_bind_updates():
    manager = SharedSessionManager()
    manager.begin_session("session-a", car_model="BMW", car_uuid="uuid-a")
    manager.update_player_identification_from_logs(
        {"steam_id": "steam-a", "car_model": "BMW", "car_uuid": "uuid-a"}
    )
    manager.end_session("session-a")
    before = manager.get_player_identification()

    manager.update_player_identification_from_logs(
        {"steam_id": "steam-b", "car_model": "BMW", "car_uuid": "uuid-b"}
    )
    assert manager.get_player_identification() == before

    manager.bind_session_identity(
        session_id="session-a", car_model="BMW", car_uuid="uuid-b"
    )
    assert manager.get_active_session_id() is None
    assert manager.get_player_identification() == before

    # Matching delayed metadata remains valid for a retained closed owner.
    manager.update_player_identification_from_logs(
        {"steam_id": "steam-a-new", "car_model": "BMW", "car_uuid": "uuid-a"}
    )
    assert manager.get_player_identification().steam_id == "steam-a-new"
    manager.bind_session_identity(
        session_id="session-a", car_model="BMW", car_uuid="uuid-a"
    )
    assert manager.get_active_session_id() is None


def _anonymous_completion(manager, *, car_model=None, car_uuid=None):
    completion = LapCompletionData(
        completed_laps=1,
        lap_time_ms=90_000,
        is_valid=True,
        timestamp="now",
        observed_at=1.0,
        origin_epoch=manager.get_session_origin().epoch,
        car_model=car_model,
        car_uuid=car_uuid,
    )
    manager._session_data.lap_completions.append(completion)
    return completion


def test_bind_requires_positive_matching_evidence_and_normalizes_uuid():
    manager = SharedSessionManager()
    manager.update_from_graphics_shm({"car_model": "BMW"})
    completion = _anonymous_completion(manager, car_model="BMW", car_uuid="AB-CD")

    manager.bind_session_identity(
        session_id="session-a", car_model="BMW", car_uuid="abcd"
    )

    assert manager.get_lap_completion_owner(completion)[1:] == (
        "session-a",
        "BMW",
        "abcd",
    )

    manager = SharedSessionManager()
    manager.update_from_graphics_shm({"car_model": "BMW"})
    completion = _anonymous_completion(manager)
    manager.bind_session_identity(session_id="session-a", car_model="BMW")
    assert manager.get_lap_completion_owner(completion)[1] is None

    manager = SharedSessionManager()
    manager.update_from_graphics_shm({"car_model": "BMW"})
    completion = _anonymous_completion(manager, car_model="BMW")
    manager.bind_session_identity(session_id="session-a", car_model="Alfa")
    assert manager.get_active_session_id() is None
    assert manager.get_lap_completion_owner(completion)[1] is None


def test_completion_history_keeps_unconsumed_records_when_compacting():
    manager = SharedSessionManager()
    records = [
        LapCompletionData(
            completed_laps=index + 1,
            lap_time_ms=90_000 + index,
            is_valid=True,
            timestamp=str(index),
            observed_at=float(index),
        )
        for index in range(600)
    ]
    manager._session_data.lap_completions = records
    for record in records[:580]:
        manager.consume_lap_completion(record)

    retained = manager._session_data.lap_completions
    assert len(retained) == 532
    assert all(record in retained for record in records[580:])
    assert len(manager._session_data.consumed_lap_completion_times) == 512
