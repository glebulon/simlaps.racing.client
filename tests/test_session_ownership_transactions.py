"""Regression tests for atomic log updates and submission identity ownership."""

from __future__ import annotations

import asyncio
from threading import Event, Thread
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.api_client import APIClient, SubmissionStatus
from src.core.security import GameProcessStatus
from src.models import LapData, SessionData, SharedSessionManager


def _lap(number: int = 1) -> LapData:
    return LapData(
        lap_number=number,
        physics_lap_number=number,
        lap_time_ms=77_777,
        lap_time_str="1:17.777",
        sector1_ms=25_000,
        sector2_ms=25_000,
        sector3_ms=27_777,
        is_valid=False,
        lap_type="INVALID_GAME",
    )


def _session(session_id: str, *, player_id: str | None = None) -> SessionData:
    return SessionData(
        session_id=session_id,
        game_version="0.9.1",
        session_type="PRACTICE",
        car="ks_bmw_m3_e46_csl",
        track="road_atlanta_gp",
        player_id=player_id,
        player_name="Driver",
        car_uuid=f"{session_id}-car",
    )


class _BlockingLogUpdateManager(SharedSessionManager):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def update_sector_splits_from_logs(self, lap_num: int, sector_data: dict) -> None:
        self.entered.set()
        assert self.release.wait(timeout=2)
        super().update_sector_splits_from_logs(lap_num, sector_data)


class _BlockingMetadataManager(SharedSessionManager):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def update_session_metadata_from_logs(self, session_data: SessionData) -> None:
        self.entered.set()
        assert self.release.wait(timeout=2)
        super().update_session_metadata_from_logs(session_data)


class _BlockingIdentityManager(SharedSessionManager):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def update_player_identification_from_logs(self, player_data: dict) -> None:
        self.entered.set()
        assert self.release.wait(timeout=2)
        super().update_player_identification_from_logs(player_data)


def _rollover_after(
    manager: SharedSessionManager, entered: Event, errors: list[BaseException]
) -> Thread:
    def rollover() -> None:
        try:
            assert entered.wait(timeout=2)
            manager.begin_session("replacement", car_model="ks_alfa_romeo_giulia_gtam")
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=rollover)
    thread.start()
    return thread


def test_update_lap_from_logs_keeps_ownership_check_and_writes_atomic() -> None:
    manager = _BlockingLogUpdateManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    outgoing = _session("outgoing", player_id="old-player")
    errors: list[BaseException] = []

    def update_lap() -> None:
        try:
            manager.update_lap_from_logs(_lap(), outgoing)
        except BaseException as error:
            errors.append(error)

    update = Thread(target=update_lap)
    update.start()
    rollover = _rollover_after(manager, manager.entered, errors)
    manager.release.set()
    update.join(timeout=2)
    rollover.join(timeout=2)

    assert errors == []
    assert not update.is_alive()
    assert not rollover.is_alive()
    assert manager.get_active_session_id() == "replacement"
    assert manager.get_lap_timing_data(1) is None
    assert manager.get_lap_validity_data(1) is None
    assert manager.get_player_identification().car_model == "ks_alfa_romeo_giulia_gtam"


def test_update_from_logs_keeps_metadata_and_laps_in_one_transaction() -> None:
    manager = _BlockingMetadataManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    outgoing = _session("outgoing", player_id="old-player")
    outgoing.laps = [_lap()]
    errors: list[BaseException] = []

    def update_logs() -> None:
        try:
            manager.update_from_logs(outgoing)
        except BaseException as error:
            errors.append(error)

    update = Thread(target=update_logs)
    update.start()
    rollover = _rollover_after(manager, manager.entered, errors)
    manager.release.set()
    update.join(timeout=2)
    rollover.join(timeout=2)

    assert errors == []
    assert not update.is_alive()
    assert not rollover.is_alive()
    assert manager.get_active_session_id() == "replacement"
    assert manager.get_session_metadata_data().session_id == "replacement"
    assert manager.get_lap_timing_data(1) is None
    assert manager.get_player_identification().car_model == "ks_alfa_romeo_giulia_gtam"


def test_update_session_metadata_keeps_identity_in_one_transaction() -> None:
    manager = _BlockingIdentityManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    outgoing = _session("outgoing", player_id="old-player")
    errors: list[BaseException] = []

    def update_metadata() -> None:
        try:
            manager.update_session_metadata_from_logs(outgoing)
        except BaseException as error:
            errors.append(error)

    update = Thread(target=update_metadata)
    update.start()
    rollover = _rollover_after(manager, manager.entered, errors)
    manager.release.set()
    update.join(timeout=2)
    rollover.join(timeout=2)

    assert errors == []
    assert not update.is_alive()
    assert not rollover.is_alive()
    assert manager.get_active_session_id() == "replacement"
    assert manager.get_session_metadata_data().session_id == "replacement"
    assert manager.get_player_identification().car_model == "ks_alfa_romeo_giulia_gtam"


def test_submit_lap_does_not_use_replacement_identity_for_outgoing_session() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    manager.begin_session("replacement", car_model="ks_alfa_romeo_giulia_gtam")
    manager.update_player_identification_from_logs({"steam_id": "replacement-player"})
    session = _session("outgoing")
    session.player_id = None

    lap = _lap()
    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("src.core.api_client.sign_payload") as signer,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        result = asyncio.run(
            APIClient(session_manager=manager).submit_lap(
                session, lap, submit_invalid=True
            )
        )

    assert result.status == SubmissionStatus.ERROR
    assert "Steam ID" in result.message
    signer.assert_not_called()
    post.assert_not_awaited()


def test_submit_lap_rejects_origin_change_before_identity_snapshot() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    manager.update_player_identification_from_logs({"steam_id": "old-player"})
    original_origin = manager.get_session_origin

    def origin_then_rollover():
        origin = original_origin()
        manager.begin_session("replacement", car_model="ks_alfa_romeo_giulia_gtam")
        manager.update_player_identification_from_logs({"steam_id": "replacement-player"})
        return origin

    manager.get_session_origin = origin_then_rollover
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


@pytest.mark.usefixtures("configured_app_secret")
@pytest.mark.usefixtures("configured_app_secret")
def test_submit_lap_uses_frozen_identity_after_snapshot_rollover() -> None:
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m3_e46_csl")
    manager.update_player_identification_from_logs({"steam_id": "old-player"})
    original_snapshot = manager.get_submission_snapshot_for_origin
    rollover_happened = False

    def snapshot_then_rollover(origin, *, lap_number=None, session_id=None):
        nonlocal rollover_happened
        snapshot = original_snapshot(
            origin, lap_number=lap_number, session_id=session_id
        )
        manager.begin_session("replacement", car_model="ks_alfa_romeo_giulia_gtam")
        manager.update_player_identification_from_logs({"steam_id": "replacement-player"})
        rollover_happened = True
        return snapshot

    manager.get_submission_snapshot_for_origin = snapshot_then_rollover
    with (
        patch("src.core.api_client.is_secret_configured", return_value=True),
        patch("src.core.api_client.is_game_running", return_value=GameProcessStatus.RUNNING),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "old-lap"}
        post.return_value = response
        result = asyncio.run(
            APIClient(session_manager=manager).submit_lap(
                _session("outgoing", player_id=None), _lap(), submit_invalid=True
            )
        )

    assert result.status == SubmissionStatus.SUCCESS
    assert rollover_happened is True
    assert post.call_args.kwargs["json"]["userId"] == "old-player"
