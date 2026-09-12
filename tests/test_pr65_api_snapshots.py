"""Focused API migration regressions for immutable submission snapshots."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.api_client import APIClient, SubmissionStatus
from src.models import (
    LapData,
    LapTimingData,
    OriginRelation,
    OriginSnapshotResult,
    SectorSplitData,
    SessionData,
    SharedSessionManager,
)


def _session(session_id: str = "outgoing", *, player_id: str | None = None) -> SessionData:
    return SessionData(
        session_id=session_id,
        game_version="0.9.1",
        session_type="PRACTICE",
        car="ks_bmw_m4_gt3",
        track="monza",
        player_id=player_id,
    )


def _lap() -> LapData:
    return LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90_000,
        lap_time_str="1:30.000",
        is_valid=True,
    )


def test_modern_mismatch_does_not_fall_back_to_primed_legacy_getters():
    manager = SharedSessionManager()
    manager.begin_session("outgoing", car_model="ks_bmw_m4_gt3")
    manager.update_player_identification_from_logs({"steam_id": "old-driver"})
    manager.begin_session("replacement", car_model="ks_dallara_p217")
    current = manager.get_session_origin()

    def mismatch(*_args, **_kwargs):
        return OriginSnapshotResult(OriginRelation.MISMATCH, current, None)

    manager.get_submission_snapshot_for_origin = mismatch
    manager.get_data_for_origin = MagicMock(side_effect=AssertionError("legacy data getter used"))
    manager.get_player_identification = MagicMock(side_effect=AssertionError("legacy identity getter used"))

    user_id, snapshot, error = APIClient(session_manager=manager)._validate_submission_preflight(
        session=_session("outgoing"),
        effective_is_valid=True,
        user_id=None,
        submit_invalid=True,
        lap_number=1,
    )

    assert user_id is None
    assert snapshot is None
    assert error is not None
    assert error.status is SubmissionStatus.ERROR
    manager.get_data_for_origin.assert_not_called()
    manager.get_player_identification.assert_not_called()


def test_manager_without_provenance_api_keeps_granular_getter_precedence():
    class LegacyManager:
        def get_player_identification(self):
            return SimpleNamespace(steam_id="shared-driver", car_model="legacy-bmw")

        def get_lap_timing_data(self, _lap_number):
            return LapTimingData(lap_number=1, last_lap_time_ms=91_000)

        def get_sector_split_data(self, _lap_number):
            return SectorSplitData(lap_number=1, sector1_ms=30_000, sector2_ms=30_000, sector3_ms=31_000)

        def get_fuel_data(self):
            return SimpleNamespace(fuel_consumed_lap=2.5)

        def get_session_metadata_data(self):
            return SimpleNamespace(
                session_id="legacy-session",
                track="legacy-track",
                session_type="PRACTICE",
                game_version="legacy-version",
            )

    client = APIClient(session_manager=LegacyManager())
    session = _session(player_id=None)
    user_id, snapshot, error = client._validate_submission_preflight(
        session=session,
        effective_is_valid=True,
        user_id=None,
        submit_invalid=True,
        lap_number=1,
    )

    assert user_id == "shared-driver"
    assert snapshot is None
    assert error is None
    payload, payload_error = client._build_submission_payload(
        session=SessionData(
            session_id="",
            game_version="Unknown",
            session_type="Unknown",
            car="Unknown",
            track="Unknown",
        ),
        lap=_lap(),
        final_user_id=user_id,
        shared_snapshot=None,
        effective_is_valid=True,
    )

    assert payload_error is None
    assert payload["trackId"] == "legacytrack"
    assert payload["carId"] == "legacy-bmw"
    assert payload["time"] == 90_000
    assert payload["sector1"] == 30_000
    assert payload["fuelUsed"] == 2.5


def test_no_secret_returns_before_manager_game_signing_or_http():
    manager = MagicMock()
    manager.get_session_origin.side_effect = AssertionError("manager used")
    client = APIClient(session_manager=manager)

    with (
        patch("src.core.api_client.is_secret_configured", return_value=False),
        patch("src.core.api_client.is_game_running", side_effect=AssertionError("game check used")),
        patch("src.core.api_client.sign_payload", side_effect=AssertionError("signer used")) as signer,
        patch.object(client, "_send_submission", new=AsyncMock(side_effect=AssertionError("HTTP used"))) as send,
    ):
        result = asyncio.run(client.submit_lap(_session(), _lap(), submit_invalid=True))

    assert result.status is SubmissionStatus.NO_SECRET
    manager.get_session_origin.assert_not_called()
    signer.assert_not_called()
    send.assert_not_awaited()
