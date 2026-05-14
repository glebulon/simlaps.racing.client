from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.api_client import SubmissionStatus
from src.ui.components.lap_card import LapCardStatus
from src.ui.services.lap_submission_service import LapSubmissionService


@pytest.mark.asyncio
async def test_submit_lap_success_updates_card_and_history_and_posts_discord():
    service = LapSubmissionService()

    api_client = MagicMock()
    api_client.submit_lap = AsyncMock(
        return_value=SimpleNamespace(status=SubmissionStatus.SUCCESS, message="ok")
    )

    config = SimpleNamespace(submit_invalid_laps=False, server_url="https://simlaps.racing")
    card = MagicMock()
    history_entry = SimpleNamespace(was_submitted=False)
    post_to_discord = AsyncMock()

    session = SimpleNamespace(track="Laguna Seca", player_id="steam123", player_name="Driver")
    lap = SimpleNamespace(lap_time_str="1:29.556", is_valid=True)

    await service.submit_lap(
        api_client=api_client,
        config=config,
        card=card,
        session=session,
        lap=lap,
        history_entry=history_entry,
        pb_was_new=True,
        post_to_discord=post_to_discord,
    )

    card.update_status.assert_any_call(LapCardStatus.SUBMITTING)
    card.update_status.assert_any_call(LapCardStatus.SUBMITTED)
    assert history_entry.was_submitted is True
    post_to_discord.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_lap_invalid_maps_to_invalid_status():
    service = LapSubmissionService()

    api_client = MagicMock()
    api_client.submit_lap = AsyncMock(
        return_value=SimpleNamespace(status=SubmissionStatus.INVALID_LAP, message="invalid")
    )

    config = SimpleNamespace(submit_invalid_laps=False, server_url="https://simlaps.racing")
    card = MagicMock()

    await service.submit_lap(
        api_client=api_client,
        config=config,
        card=card,
        session=SimpleNamespace(track="Laguna Seca", player_id="steam123", player_name="Driver"),
        lap=SimpleNamespace(lap_time_str="1:29.556", is_valid=False),
        history_entry=SimpleNamespace(was_submitted=False),
        pb_was_new=False,
        post_to_discord=AsyncMock(),
    )

    card.update_status.assert_any_call(LapCardStatus.INVALID, "invalid")


@pytest.mark.asyncio
async def test_post_to_discord_pb_only_skips_when_not_pb():
    service = LapSubmissionService()

    config = SimpleNamespace(
        discord_enabled=True,
        discord_pb_only=True,
        discord_webhook_url="https://discord.com/api/webhooks/123/abc",
    )
    discord_notifier = MagicMock()
    discord_notifier.post_lap = AsyncMock(return_value=True)

    session = SimpleNamespace(track="Laguna Seca", car="Ferrari 296 GT3")
    lap = SimpleNamespace(
        lap_time_ms=89556,
        lap_time_str="1:29.556",
        is_valid=True,
        timestamp="2026-04-29T00:21:00",
        sector1_ms=None,
        sector2_ms=None,
        sector3_ms=None,
        fuel_used=2.3,
        tyre_compound="Unknown",
    )

    await service.post_to_discord(
        config=config,
        discord_notifier=discord_notifier,
        session=session,
        lap=lap,
        steam_id="steam123",
        steam_name="Driver",
        pb_was_new=False,
    )

    discord_notifier.post_lap.assert_not_called()
