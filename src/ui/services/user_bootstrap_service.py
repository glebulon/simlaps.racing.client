"""User bootstrap/identity service extracted from SimLapsApp.

Owns startup/log-driven user detection handling: UI identity projection,
Discord notifier initialization, and PB cache preload orchestration.
"""

from typing import Any, Callable, Optional

from src.utils.structured_logger import Component, log_debug, log_info, log_warning


class UserBootstrapService:
    """Encapsulates user identity bootstrap flows for app startup and callbacks."""

    async def handle_detected_user(
        self,
        *,
        app: Any,
        steam_id: str,
        player_name: Optional[str],
        create_discord_notifier: Callable[[str], Any],
    ) -> None:
        """Handle user detection from log parser callback path."""
        if app._home_page:
            app._home_page.set_detected_user(steam_id, player_name)

        if app._config.discord_webhook_url and app._config.discord_enabled:
            app._discord_notifier = create_discord_notifier(app._config.discord_webhook_url)

        if not app._pb_cache.is_loaded() or app._pb_cache.get_steam_id() != steam_id:
            server_url = app._config.server_url
            log_info(Component.APP, "Preloading personal bests", server_url=server_url, steam_id=steam_id)
            success = await app._pb_cache.preload_from_api(steam_id)
            if success:
                stats = app._pb_cache.get_cache_stats()
                log_info(
                    Component.APP,
                    "PB cache loaded successfully",
                    combo_count=stats["combo_count"],
                    stats=stats,
                )
            else:
                log_warning(Component.APP, "Failed to preload PB cache from server")
                log_warning(Component.APP, "Discord PB detection may be unreliable")

    async def handle_startup_user(
        self,
        *,
        app: Any,
        steam_id: Optional[str],
        steam_name: Optional[str],
        create_discord_notifier: Callable[[str], Any],
    ) -> None:
        """Handle startup-time Steam user bootstrap from registry detection."""
        if not steam_id:
            log_debug(Component.APP, "No Steam user detected - PB preload will wait for log detection")
            return

        log_info(Component.APP, "Steam user detected on startup", steam_id=steam_id, steam_name=steam_name)

        if app._home_page:
            app._home_page.set_detected_user(steam_id, steam_name)

        if app._config.discord_webhook_url and app._config.discord_enabled:
            app._discord_notifier = create_discord_notifier(app._config.discord_webhook_url)
            log_info(Component.APP, "Discord notifier initialized", steam_id=steam_id)

        if not app._pb_cache.is_loaded() or app._pb_cache.get_steam_id() != steam_id:
            log_info(Component.APP, "Triggering PB preload for Steam user", steam_id=steam_id)
            success = await app._pb_cache.preload_from_api(steam_id)
            if success:
                stats = app._pb_cache.get_cache_stats()
                log_info(Component.APP, "PB cache loaded on startup", combo_count=stats["combo_count"])
            else:
                log_warning(Component.APP, "Failed to preload PB cache on startup")
