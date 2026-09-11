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
    notifier = MagicMock()
    create_discord_notifier = MagicMock(return_value=notifier)

    service = UserBootstrapService()
    await service.handle_detected_user(
        app=app,
        steam_id="76561198321627695",
        player_name="Driver",
        create_discord_notifier=create_discord_notifier,
    )

    create_discord_notifier.assert_called_once_with("https://discord.com/api/webhooks/123/abc")
    assert app._discord_notifier is notifier
    app._pb_cache.preload_from_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_startup_user_no_steam_id_is_noop():
    app = _make_app()
    create_discord_notifier = MagicMock()

    service = UserBootstrapService()
    await service.handle_startup_user(
        app=app,
        steam_id=None,
        steam_name=None,
        create_discord_notifier=create_discord_notifier,
    )

    app._home_page.set_detected_user.assert_not_called()
    app._pb_cache.preload_from_api.assert_not_awaited()
    create_discord_notifier.assert_not_called()


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


@pytest.mark.asyncio
async def test_handle_startup_user_skips_preload_when_cache_already_for_same_user():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = True
    app._pb_cache.get_steam_id.return_value = "76561198321627695"
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
    assert app._discord_notifier is notifier
    app._pb_cache.preload_from_api.assert_not_awaited()


@pytest.mark.parametrize(
    ("discord_enabled", "webhook_url"),
    [
        (False, "https://discord.com/api/webhooks/123/abc"),
        (True, ""),
    ],
)
@pytest.mark.parametrize("source", ["detected", "startup"])
@pytest.mark.asyncio
async def test_known_user_does_not_replace_discord_notifier_when_not_configured(source, discord_enabled, webhook_url):
    app = _make_app()
    app._config.discord_enabled = discord_enabled
    app._config.discord_webhook_url = webhook_url
    app._pb_cache.is_loaded.return_value = True
    app._pb_cache.get_steam_id.return_value = "76561198321627695"
    existing_notifier = MagicMock()
    app._discord_notifier = existing_notifier
    create_discord_notifier = MagicMock()

    service = UserBootstrapService()
    if source == "detected":
        await service.handle_detected_user(
            app=app,
            steam_id="76561198321627695",
            player_name="Driver",
            create_discord_notifier=create_discord_notifier,
        )
    else:
        await service.handle_startup_user(
            app=app,
            steam_id="76561198321627695",
            steam_name="Driver",
            create_discord_notifier=create_discord_notifier,
        )

    create_discord_notifier.assert_not_called()
    assert app._discord_notifier is existing_notifier
    app._pb_cache.preload_from_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_detected_user_logs_warning_on_preload_failure():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = False
    app._pb_cache.get_steam_id.return_value = None
    app._pb_cache.preload_from_api = AsyncMock(return_value=False)

    service = UserBootstrapService()
    await service.handle_detected_user(
        app=app,
        steam_id="76561198321627695",
        player_name="Driver",
        create_discord_notifier=MagicMock(return_value=MagicMock()),
    )

    app._pb_cache.preload_from_api.assert_awaited_once_with("76561198321627695")


@pytest.mark.asyncio
async def test_handle_startup_user_logs_warning_on_preload_failure():
    app = _make_app()
    app._pb_cache.is_loaded.return_value = False
    app._pb_cache.get_steam_id.return_value = None
    app._pb_cache.preload_from_api = AsyncMock(return_value=False)

    service = UserBootstrapService()
    await service.handle_startup_user(
        app=app,
        steam_id="76561198321627695",
        steam_name="Driver",
        create_discord_notifier=MagicMock(return_value=MagicMock()),
    )

    app._pb_cache.preload_from_api.assert_awaited_once_with("76561198321627695")
