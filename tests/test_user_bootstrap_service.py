from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ui.services.user_bootstrap_service import UserBootstrapService
from src.utils.config import AppConfig


def _make_app() -> SimpleNamespace:
    app = SimpleNamespace()
    app._config = AppConfig(
        server_url="https://simlaps.racing",
        discord_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/123/abc",
    )
    app._home_page = MagicMock()
    app._pb_cache = MagicMock()
    app._pb_cache.preload_from_api = AsyncMock(return_value=True)
    app._pb_cache.get_cache_stats.return_value = {"combo_count": 55}
    app._discord_notifier = None
    return app


@pytest.mark.asyncio
async def test_handle_detected_user_updates_ui_initializes_discord_and_preloads_when_needed():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = False
    app._pb_cache.get_steam_id.return_value = None

    notifier = MagicMock()
    create_discord_notifier = MagicMock(return_value=notifier)

    service = UserBootstrapService()
    await service.handle_detected_user(
        app=app,
        steam_id="76561198321627695",
        player_name="Driver",
        create_discord_notifier=create_discord_notifier,
    )

    app._home_page.set_detected_user.assert_called_once_with("76561198321627695", "Driver")
    create_discord_notifier.assert_called_once_with("https://discord.com/api/webhooks/123/abc")
    app._pb_cache.preload_from_api.assert_awaited_once_with("76561198321627695")
    assert app._discord_notifier is notifier


@pytest.mark.asyncio
async def test_handle_detected_user_skips_preload_when_cache_already_for_same_user():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = True
    app._pb_cache.get_steam_id.return_value = "76561198321627695"

    service = UserBootstrapService()
    await service.handle_detected_user(
        app=app,
        steam_id="76561198321627695",
        player_name="Driver",
        create_discord_notifier=MagicMock(return_value=MagicMock()),
    )

    app._pb_cache.preload_from_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_startup_user_no_steam_id_is_noop():
    app = _make_app()

    service = UserBootstrapService()
    await service.handle_startup_user(
        app=app,
        steam_id=None,
        steam_name=None,
        create_discord_notifier=MagicMock(),
    )

    app._home_page.set_detected_user.assert_not_called()
    app._pb_cache.preload_from_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_startup_user_detected_initializes_and_preloads():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = False
    app._pb_cache.get_steam_id.return_value = None

    notifier = MagicMock()
    create_discord_notifier = MagicMock(return_value=notifier)

    service = UserBootstrapService()
    await service.handle_startup_user(
        app=app,
        steam_id="76561198321627695",
        steam_name="Driver",
        create_discord_notifier=create_discord_notifier,
    )

    app._home_page.set_detected_user.assert_called_once_with("76561198321627695", "Driver")
    create_discord_notifier.assert_called_once_with("https://discord.com/api/webhooks/123/abc")
    app._pb_cache.preload_from_api.assert_awaited_once_with("76561198321627695")
    assert app._discord_notifier is notifier
