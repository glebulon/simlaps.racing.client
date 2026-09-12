"""Regression coverage for capture lifecycle and submission ownership."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.api_client import APIClient, SubmissionStatus
from src.core.security import GameProcessStatus
from src.core.telemetry_capture import FrameData, TelemetryCapture
from src.models import LapData, SessionData, SharedSessionData, SharedSessionManager
from src.ui.components.lap_card import LapCardStatus
from src.ui.services.lap_submission_service import LapSubmissionService
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService


def _session(session_id: str, *, player_id: str | None = None) -> SessionData:
    return SessionData(
        session_id=session_id,
        game_version="0.9.1",
        session_type="PRACTICE",
        car="ks_bmw_m4_gt3",
        track="monza",
        player_id=player_id,
        player_name="Driver",
    )


def _lap() -> LapData:
    return LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=120918,
        lap_time_str="2:00.918",
        is_valid=True,
    )


def _submit(client: APIClient, session: SessionData) -> object:
    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "lap-id"}
        post.return_value = response
        result = asyncio.run(client.submit_lap(session, _lap(), submit_invalid=True))
    return result


def test_bmw_m4_log_and_graphics_names_are_an_explicit_alias() -> None:
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="ks_bmw_m4_gt3")

    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M4 GT3 Evo",
            "status_name": "AC_LIVE",
            "session_phase": "PRACTICE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )

    assert manager.get_session_origin().graphics_car_model == "BMW M4 GT3 Evo"
    assert manager.get_active_session_id() == "bmw-session"


def test_bmw_alias_keeps_capture_callback_owned_and_applies_static_data(tmp_path) -> None:
    manager = SharedSessionManager()
    manager.begin_session("bmw-session", car_model="ks_bmw_m4_gt3")
    capture = TelemetryCapture(
        output_dir=str(tmp_path), session_manager=manager, record_frames=False
    )
    capture._readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    with (
        patch("src.core.telemetry_capture.peek_graphics_validity", return_value={}),
        patch(
            "src.core.telemetry_capture.decode_graphics",
            return_value={
                "car_model": "BMW M4 GT3 Evo",
                "status_name": "AC_LIVE",
                "session_phase": "PRACTICE",
                "current_lap_time_ms": 12_000,
                "total_lap_count": 0,
                "is_valid_lap": True,
            },
        ),
        patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "Monza", "session_name": "PRACTICE"},
        ),
        patch("src.core.telemetry_capture.decode_physics", return_value={"speed_kmh": 100.0}),
    ):
        frame = capture._capture_frame(0)

    assert frame.car_model == "BMW M4 GT3 Evo"
    assert capture._last_frame_origin_stable is True
    assert capture.get_frames() == []
    assert manager.get_session_metadata_data().track == "Monza"


@pytest.mark.parametrize("record_frames", [True, False])
def test_bmw_alias_survives_dallara_to_bmw_capture_transition(tmp_path, record_frames) -> None:
    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "car_model": "Dallara EXP",
            "status_name": "AC_LIVE",
            "session_phase": "PRACTICE",
            "current_lap_time_ms": 12_000,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    manager.begin_session(
        "bmw-session", car_model="ks_bmw_m4_gt3", car_uuid="bmw-uuid"
    )
    capture = TelemetryCapture(
        output_dir=str(tmp_path),
        session_manager=manager,
        record_frames=record_frames,
    )
    capture._capture_origin = manager.get_session_origin()
    capture._readers = {
        name: MagicMock(size=1, read_raw=MagicMock(return_value=b"x"))
        for name in ("graphics", "static", "physics")
    }
    sample_number = 0

    def graphics_sample(_raw):
        nonlocal sample_number
        sample_number += 1
        return {
            "car_model": "BMW M4 GT3 Evo",
            "status_name": "AC_LIVE",
            "session_phase": "PRACTICE",
            "current_lap_time_ms": 120_918 if sample_number < 3 else 0,
            "last_laptime_ms": 0 if sample_number < 3 else 120_918,
            "total_lap_count": 0,
            "is_valid_lap": True,
            "fuel_liter_current_quantity": 42.0,
            "fuel_liter_per_lap": 2.1,
        }

    with (
        patch("src.core.telemetry_capture.peek_graphics_validity", return_value={}),
        patch("src.core.telemetry_capture.decode_graphics", side_effect=graphics_sample),
        patch(
            "src.core.telemetry_capture.decode_static",
            return_value={"track": "Monza", "session_name": "PRACTICE"},
        ),
        patch("src.core.telemetry_capture.decode_physics", return_value={"speed_kmh": 200.0}),
    ):
        frames = [capture._capture_frame(i) for i in range(3)]

    origin = manager.get_session_origin()
    assert origin.session_id == "bmw-session"
    assert origin.graphics_ready is True
    assert capture._last_frame_origin_stable is True
    assert manager.get_current_lap_time() == 0
    assert manager.get_latest_lap_completion().lap_time_ms == 120_918
    assert manager.get_fuel_data().current_fuel == 42.0
    assert frames[-1].car_model == "BMW M4 GT3 Evo"
    assert capture.record_frames is record_frames


def test_lifecycle_stops_do_not_auto_notify_but_unexpected_stops_do() -> None:
    capture = TelemetryCapture(output_dir=".")

    for reason in ("session_rollover", "car_removed"):
        capture._stop_reason = reason
        assert capture._should_notify_stop_callback() is False

    capture._stop_reason = "game_not_running"
    assert capture._should_notify_stop_callback() is True


@pytest.mark.parametrize("reason", ["session_rollover", "car_removed"])
async def test_explicit_lifecycle_stop_runs_analysis_once(tmp_path, reason) -> None:
    manager = SharedSessionManager()
    manager.begin_session("session", car_model="BMW")
    capture = TelemetryCapture(output_dir=str(tmp_path), session_manager=manager)
    service = TelemetryLifecycleService()
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(
        return_value=SimpleNamespace(
            laps_detected=1,
            best_lap_time=60.0,
            html_path="report.html",
            ai_prompt_path="prompt.txt",
        )
    )

    async def automatic_callback(callback_reason: str) -> None:
        await service.handle_auto_stop(
            reason=callback_reason,
            telemetry_capture=capture,
            telemetry_analyzer=analyzer,
            home_page=None,
            current_track_name="Monza",
        )

    capture.set_on_stop_callback(automatic_callback)
    with (
        patch.object(capture, "_connect_regions", return_value={}),
        patch.object(capture, "_reconnect_missing"),
    ):
        assert await capture.start_capture() is True
        capture._frames = [
            FrameData(
                "2026-09-11T10:00:00Z",
                0,
                {"speed_kmh": 100.0},
            )
        ]
        await service.stop_capture(
            reason=reason,
            discard=False,
            telemetry_capture=capture,
            telemetry_analyzer=analyzer,
            home_page=None,
            current_track_name="Monza",
        )
        await asyncio.sleep(0)

    assert analyzer.analyze.await_count == 1


@pytest.mark.usefixtures("configured_app_secret")
def test_closed_same_session_uses_retained_snapshot_for_submission() -> None:
    manager = SharedSessionManager()
    manager.begin_session("closed-session", car_model="ks_bmw_m4_gt3")
    manager.update_player_identification_from_logs(
        {"steam_id": "old-driver", "car_model": "ks_bmw_m4_gt3"}
    )
    manager.end_session("closed-session")

    result = _submit(APIClient(session_manager=manager), _session("closed-session"))

    assert result.status == SubmissionStatus.SUCCESS


def test_closed_replacement_snapshot_cannot_supply_foreign_identity() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m4_gt3")
    manager.update_player_identification_from_logs({"steam_id": "old-driver"})
    manager.end_session("outgoing")
    manager.begin_session("replacement", car_model="ks_dallara_p217")
    manager.update_player_identification_from_logs({"steam_id": "new-driver"})
    manager.end_session("replacement")

    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("src.core.api_client.sign_payload") as signer,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        result = asyncio.run(
            APIClient(session_manager=manager).submit_lap(
                _session("outgoing"), _lap(), submit_invalid=True
            )
        )

    assert result.status == SubmissionStatus.ERROR
    signer.assert_not_called()
    post.assert_not_awaited()


def test_anonymous_replacement_cannot_reuse_closed_session_metadata() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m4_gt3")
    manager.update_player_identification_from_logs({"steam_id": "old-driver"})
    manager.update_from_graphics_shm(
        {
            "car_model": "BMW M4 GT3 Evo",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 12_000,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )
    manager.update_from_graphics_shm(
        {
            "car_model": "ks_dallara_p217",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 12_100,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": True,
        }
    )

    user_id, snapshot, error = APIClient(session_manager=manager)._validate_submission_preflight(
        session=_session("outgoing", player_id="old-driver"),
        effective_is_valid=True,
        user_id=None,
        submit_invalid=True,
    )

    assert user_id == "old-driver"
    assert snapshot is None
    assert error is None


@pytest.mark.usefixtures("configured_app_secret")
def test_queued_outgoing_submission_keeps_own_identity_after_replacement() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m4_gt3")
    manager.end_session("outgoing")
    manager.begin_session("replacement", car_model="ks_dallara_p217")

    session = _session("outgoing", player_id="old-driver")
    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "lap-id"}
        post.return_value = response
        result = asyncio.run(
            APIClient(session_manager=manager).submit_lap(
                session, _lap(), submit_invalid=True
            )
        )

    assert result.status == SubmissionStatus.SUCCESS
    payload = post.call_args.kwargs["json"]
    assert payload["userId"] == "old-driver"
    assert payload["carId"] == "ks_bmw_m4_gt3"


def test_unknown_session_car_falls_back_to_unknown_string() -> None:
    client = APIClient()
    payload, error = client._build_submission_payload(
        session=SessionData(
            session_id="session",
            game_version="0.9.1",
            session_type="PRACTICE",
            car="Unknown",
            track="monza",
        ),
        lap=_lap(),
        final_user_id="driver",
        shared_snapshot=SharedSessionData(),
        effective_is_valid=True,
    )

    assert error is None
    assert payload["carId"] == "Unknown"


def test_empty_network_error_has_exception_type_diagnostic() -> None:
    async def run() -> object:
        client = APIClient()
        with patch.object(client, "_get_client", new_callable=AsyncMock) as get_client:
            get_client.return_value.post = AsyncMock(side_effect=httpx.NetworkError(""))
            return await client._send_submission({})

    result = asyncio.run(run())
    assert result.status == SubmissionStatus.NETWORK_ERROR
    assert result.message == "Network error: NetworkError"


async def test_network_error_is_a_known_ui_failure() -> None:
    service = LapSubmissionService()
    card = MagicMock()
    api_client = MagicMock()
    api_client.submit_lap = AsyncMock(
        return_value=SimpleNamespace(
            status=SubmissionStatus.NETWORK_ERROR,
            message="Network error: NetworkError",
        )
    )
    with patch("src.ui.services.lap_submission_service.log_warning") as warning, patch(
        "src.ui.services.lap_submission_service.log_error"
    ) as error:
        await service.submit_lap(
            api_client=api_client,
            config=SimpleNamespace(
                submit_invalid_laps=False,
                server_url="https://simlaps.racing",
            ),
            card=card,
            session=SimpleNamespace(track="monza", player_id="driver", player_name="Driver"),
            lap=SimpleNamespace(lap_time_str="2:00.918", is_valid=True),
            history_entry=SimpleNamespace(was_submitted=False),
            pb_was_new=False,
            post_to_discord=AsyncMock(),
        )

    card.update_status.assert_any_call(
        LapCardStatus.FAILED, "Network error: NetworkError"
    )
    warning.assert_called_once()
    error.assert_not_called()
