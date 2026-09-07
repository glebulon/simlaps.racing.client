"""User bootstrap/identity service extracted from SimLapsApp.

Owns startup/log-driven user detection handling: UI identity projection,
Discord notifier initialization, and PB cache preload orchestration.
"""

from typing import Callable, Optional, TYPE_CHECKING

from src.utils.structured_logger import Component, log_debug, log_info, log_warning

if TYPE_CHECKING:
    from ..app import SimLapsApp
    from src.core.discord_notifier import DiscordNotifier


class UserBootstrapService:
    """Encapsulates user identity bootstrap flows for app startup and callbacks."""

    async def _bootstrap_known_user(
        self,
        *,
        app: "SimLapsApp",
        steam_id: str,
        player_name: Optional[str],
        create_discord_notifier: "Callable[[str], DiscordNotifier]",
        source: str,
    ) -> None:
        """Project a known user into runtime services from either source."""
        if app._home_page:
            app._home_page.set_detected_user(steam_id, player_name)

        if app._config.discord_webhook_url and app._config.discord_enabled:
            notifier = create_discord_notifier(app._config.discord_webhook_url)
            app._discord_notifier = notifier
            if source == "startup" and notifier is not None:
                log_info(Component.APP, "Discord notifier initialized", steam_id=steam_id)

        if app._pb_cache.is_loaded() and app._pb_cache.get_steam_id() == steam_id:
            return

        if source == "startup":
            log_info(Component.APP, "Triggering PB preload for Steam user", steam_id=steam_id)
        else:
            log_info(
                Component.APP,
                "Preloading personal bests",
                server_url=app._config.server_url,
                steam_id=steam_id,
            )

        success = await app._pb_cache.preload_from_api(steam_id)
        if success:
            stats = app._pb_cache.get_cache_stats()
            if source == "startup":
                log_info(Component.APP, "PB cache loaded on startup", combo_count=stats["combo_count"])
            else:
                log_info(
                    Component.APP,
                    "PB cache loaded successfully",
                    combo_count=stats["combo_count"],
                    stats=stats,
                )
        elif source == "startup":
            log_warning(Component.APP, "Failed to preload PB cache on startup")
        else:
            log_warning(Component.APP, "Failed to preload PB cache from server")
            log_warning(Component.APP, "Discord PB detection may be unreliable")

    async def handle_detected_user(
        self,
        *,
        app: "SimLapsApp",
        steam_id: str,
        player_name: Optional[str],
        create_discord_notifier: "Callable[[str], DiscordNotifier]",
    ) -> None:
        """Handle user detection from log parser callback path."""
        await self._bootstrap_known_user(
            app=app,
            steam_id=steam_id,
            player_name=player_name,
            create_discord_notifier=create_discord_notifier,
            source="detected",
        )

    async def handle_startup_user(
        self,
        *,
        app: "SimLapsApp",
        steam_id: Optional[str],
        steam_name: Optional[str],
        create_discord_notifier: "Callable[[str], DiscordNotifier]",
    ) -> None:
        """Handle startup-time Steam user bootstrap from registry detection."""
        if not steam_id:
            log_debug(Component.APP, "No Steam user detected - PB preload will wait for log detection")
            return

        log_info(Component.APP, "Steam user detected on startup", steam_id=steam_id, steam_name=steam_name)
        await self._bootstrap_known_user(
            app=app,
            steam_id=steam_id,
            player_name=steam_name,
            create_discord_notifier=create_discord_notifier,
            source="startup",
        )
