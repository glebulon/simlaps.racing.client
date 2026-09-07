"""Regression coverage for callbacks racing with Flet page navigation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.core.api_client import SubmissionStatus
from src.models import LapData, SessionData
from src.ui.app import SimLapsApp
from src.ui.components.lap_card import LapCardStatus
from src.ui.components.mount_safe import safe_update
from src.ui.pages.home import HomePage
from src.ui.services.lap_processing_service import LapProcessingService
from src.ui.services.lap_submission_service import LapSubmissionService
from src.ui.services.session_lifecycle_service import SessionLifecycleService
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService
from src.utils.config import AppConfig


def _lap() -> LapData:
    return LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=89556,
        lap_time_str="1:29.556",
        sector1_ms=30000,
        sector2_ms=30000,
        sector3_ms=29556,
        timestamp="2026-08-26T12:00:00",
        is_valid=True,
    )


def _unmounted_page() -> PropertyMock:
    return PropertyMock(side_effect=RuntimeError("Control must be added to the page first"))


def test_safe_update_does_not_hide_unrelated_runtime_errors():
    control = SimpleNamespace(page=object())
    control.update = MagicMock(side_effect=RuntimeError("unexpected update failure"))

    with pytest.raises(RuntimeError, match="unexpected update failure"):
        safe_update(control)


@pytest.mark.asyncio
async def test_game_status_callback_resets_and_starts_capture_when_home_is_unmounted():
    home = HomePage(AppConfig())
    manager = MagicMock()
    manager.get_best_lap_time.side_effect = [90000, None]
    manager.get_all_lap_times.side_effect = [[90000], []]
    capture = MagicMock()
    capture.is_capturing.return_value = False
    start_capture = AsyncMock()
    stop_capture = AsyncMock()
    service = SessionLifecycleService(
        home_page=home,
        session_manager=manager,
        telemetry_capture=capture,
        start_capture=start_capture,
        stop_capture=stop_capture,
    )

    with patch.object(HomePage, "page", new_callable=_unmounted_page):
        await service.handle_game_status_change(True)

    manager.reset.assert_called_once_with()
    start_capture.assert_awaited_once_with()
    assert home._game_running is True


@pytest.mark.asyncio
async def test_telemetry_start_updates_state_when_home_is_unmounted():
    home = HomePage(AppConfig())
    capture = MagicMock()
    capture.is_capturing.return_value = False
    capture.get_output_prefix.return_value = "capture"
    capture.start_capture = AsyncMock(return_value=True)

    with patch.object(HomePage, "page", new_callable=_unmounted_page):
        await TelemetryLifecycleService().start_capture(
            telemetry_capture=capture,
            home_page=home,
            telemetry_enabled=True,
        )

    capture.start_capture.assert_awaited_once_with()
    assert home._telemetry_status._status.value == "capturing"


@pytest.mark.asyncio
async def test_lap_complete_records_history_and_schedules_submission_when_home_is_unmounted():
    home = HomePage(AppConfig())
    schedule_submission = MagicMock()
    app = SimLapsApp.__new__(SimLapsApp)
    app._lap_processing_service = LapProcessingService()
    app._home_page = home
    app._telemetry_capture = None
    app._config = AppConfig(auto_submit=True)
    app._session_manager = MagicMock()
    app._pb_cache = MagicMock()
    app._pb_cache.check_and_update_pb.return_value = True
    app._history_entries = []
    app._current_track_name = None
    app._schedule_lap_submission = schedule_submission

    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = _lap()
    with patch.object(HomePage, "page", new_callable=_unmounted_page):
        await app._on_lap_complete(session, lap)

    assert len(app._history_entries) == 1
    schedule_submission.assert_called_once()
    assert home._lap_count == 1
    assert len(home._lap_cards) == 1


@pytest.mark.asyncio
async def test_lap_submission_updates_history_and_discord_when_card_is_unmounted():
    home = HomePage(AppConfig())
    session = SessionData(track="Laguna Seca", car="Ferrari 296 GT3", player_id="steam123")
    card = home.add_lap(session, _lap(), LapCardStatus.SUBMITTING)
    history_entry = SimpleNamespace(was_submitted=False)
    api_client = MagicMock()
    api_client.submit_lap = AsyncMock(
        return_value=SimpleNamespace(status=SubmissionStatus.SUCCESS, message="ok")
    )
    post_to_discord = AsyncMock()

    with patch.object(type(card), "page", new_callable=_unmounted_page):
        await LapSubmissionService().submit_lap(
            api_client=api_client,
            config=SimpleNamespace(submit_invalid_laps=False, server_url="https://simlaps.racing"),
            card=card,
            session=session,
            lap=_lap(),
            history_entry=history_entry,
            pb_was_new=True,
            post_to_discord=post_to_discord,
        )

    assert card.data.status is LapCardStatus.SUBMITTED
    assert history_entry.was_submitted is True
    post_to_discord.assert_awaited_once()
