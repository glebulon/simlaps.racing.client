"""
Discord Notifier Module

Handles posting lap times to Discord webhooks with proper formatting
and error handling.
"""

import httpx
import asyncio
import re
from urllib.parse import urlsplit
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Dict, Any

from ..utils.structured_logger import log_debug, log_error, log_warning, Component


# Discord supports both hostnames for webhook URLs.  Keep this allowlist
# deliberately exact: the notifier must never become a generic HTTP client.
_DISCORD_WEBHOOK_HOSTS = frozenset({"discord.com", "discordapp.com"})
_WEBHOOK_ID_RE = re.compile(r"^[0-9]+$")
_WEBHOOK_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_WEBHOOK_PATH_PREFIX = "/api/webhooks/"
_INVALID_WEBHOOK_URL = "Invalid Discord webhook URL"


@dataclass
class DiscordLapPayload:
    """Data structure for lap information."""
    track_name: str
    car_name: str
    lap_time_ms: int
    valid: bool
    steam_id: str
    steam_name: Optional[str] = None
    is_personal_best: bool = False
    created_at: Optional[datetime | str] = None
    sector_times_ms: Optional[list[int]] = None  # [sector1, sector2, sector3] in ms
    fuel_used_liters: Optional[float] = None
    tire_compound: Optional[str] = None  # SC, SS, etc.


class DiscordNotifier:
    """
    Handles Discord webhook notifications for lap times.
    
    Provides methods to post lap data and test webhook connectivity.
    """
    
    def __init__(self, webhook_url: str, timeout: float = 10.0):
        """
        Initialize Discord notifier.
        
        Args:
            webhook_url: Discord webhook URL
            timeout: Request timeout in seconds
        """
        if not self.validate_webhook_url(webhook_url):
            # Do not include the URL in this error. Webhook tokens are
            # credentials and may otherwise be exposed by UI/log handling.
            raise ValueError(_INVALID_WEBHOOK_URL)

        self.webhook_url = webhook_url.strip()
        self.timeout = timeout
    
    def create_lap_embed(self, lap_data: DiscordLapPayload) -> Dict[str, Any]:
        """
        Create Discord embed for lap data.
        
        Args:
            lap_data: Lap information
            
        Returns:
            Discord embed dictionary
        """
        # Determine color based on lap validity and PB status
        if lap_data.is_personal_best:
            color = 5814783  # Gold for PB
        elif lap_data.valid:
            color = 3447003  # Blue for valid laps
        else:
            color = 15158332  # Red for invalid laps
        
        # Title with PB indicator (now included in field)
        title = "Lap Time Recorded"
        
        from src.utils.helpers import format_lap_time
        
        # Format sectors as code block
        sectors_code = ""
        if lap_data.sector_times_ms and len(lap_data.sector_times_ms) >= 3:
            s1, s2, s3 = lap_data.sector_times_ms[:3]
            sectors_code = f"```\nS1: {format_lap_time(s1)}\nS2: {format_lap_time(s2)}\nS3: {format_lap_time(s3)}\n```"
        
        # Format session details
        session_details = []
        if lap_data.fuel_used_liters is not None:
            session_details.append(f"⛽ Fuel: {lap_data.fuel_used_liters:.1f}L")
        if lap_data.tire_compound:
            session_details.append(f"🛞 Tires: {lap_data.tire_compound}")
        session_text = " • ".join(session_details) if session_details else ""
        
        # Build fields in requested order
        fields = []
        
        # Driver field (full width)
        field_name = "🫙 New PB" if lap_data.is_personal_best else "Lap Recorded"
        fields.append({
            "name": field_name,
            "value": f"**Driver:** {lap_data.steam_name or 'Unknown'}\n🏎️ **Car:** {lap_data.car_name.replace('_', ' ').replace('ks_', '').title()} • 🏁 **Track:** {lap_data.track_name.replace('_', ' ').title()} • ⏱️ **Lap Time:** {format_lap_time(lap_data.lap_time_ms)}",
            "inline": False
        })
        
        # Sector Times (code block)
        if sectors_code:
            fields.append({
                "name": "📊 Sector Times",
                "value": sectors_code,
                "inline": False
            })
        
        # Session Details
        if session_text:
            fields.append({
                "name": "📋 Session Details",
                "value": session_text,
                "inline": False
            })
        
        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "SimLaps Client"
            },
        }
        
        return embed
    
    async def post_lap(self, lap_data: DiscordLapPayload) -> bool:
        """
        Post lap data to Discord webhook.
        
        Args:
            lap_data: Lap information to post
            
        Returns:
            True if successful, False otherwise
        """
        try:
            log_debug(Component.DISCORD, f"post_lap called: {lap_data.lap_time_ms}ms on {lap_data.track_name}")
            
            embed = self.create_lap_embed(lap_data)
            payload = {
                "embeds": [embed]
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                log_debug(Component.DISCORD, f"Response status: {response.status_code}")
                
                # Discord returns 204 for successful webhook posts
                success = response.status_code in (200, 204)
                log_debug(Component.DISCORD, f"Post successful: {success}")
                return success
                
        except httpx.TimeoutException:
            log_warning(Component.DISCORD, "Discord webhook request timed out")
            return False
        except httpx.RequestError:
            # httpx exception text can include the full request URL, including
            # the webhook token. Keep diagnostics useful without leaking it.
            log_error(Component.DISCORD, "Discord webhook request failed")
            return False
        except (RuntimeError, ValueError, TypeError):
            log_error(Component.DISCORD, "Unexpected error posting to Discord")
            return False
    
    async def send_test_message(self) -> bool:
        """
        Send a test message to verify webhook connectivity.
        
        Returns:
            True if successful, False otherwise
        """
        test_lap = DiscordLapPayload(
            track_name="laguna_seca",
            car_name="ks_porsche_992_gt3_cup",
            lap_time_ms=92295,
            valid=True,
            steam_id="76561198321627695",
            steam_name="TestUser",
            is_personal_best=True,
            created_at=datetime.now(),
            sector_times_ms=[28456, 32123, 31716],
            fuel_used_liters=3.2,
            tire_compound="SC",
        )
        return await self.post_lap(test_lap)
    
    @staticmethod
    def validate_webhook_url(url: str) -> bool:
        """
        Validate Discord webhook URL format.
        
        Args:
            url: Webhook URL to validate
            
        Returns:
            True if URL format is valid
        """
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        if not url or any(character.isspace() for character in url):
            return False

        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            # A webhook URL is an HTTPS URL to one of Discord's exact hosts.
            # Reject credentials, ports, query/fragment suffixes, and all
            # other hosts so this notifier cannot be used for SSRF.
            if (
                parsed.scheme.lower() != "https"
                or hostname is None
                or hostname.lower() not in _DISCORD_WEBHOOK_HOSTS
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.query
                or parsed.fragment
            ):
                return False
        except ValueError:
            return False

        if not parsed.path.startswith(_WEBHOOK_PATH_PREFIX):
            return False

        components = parsed.path[len(_WEBHOOK_PATH_PREFIX):].split("/")
        if len(components) != 2:
            return False

        webhook_id, token = components
        return bool(
            _WEBHOOK_ID_RE.fullmatch(webhook_id)
            and _WEBHOOK_TOKEN_RE.fullmatch(token)
        )


def create_discord_notifier(webhook_url: str) -> Optional[DiscordNotifier]:
    """
    Create Discord notifier instance with validation.
    
    Args:
        webhook_url: Discord webhook URL
        
    Returns:
        DiscordNotifier instance or None if invalid URL
    """
    # Keep the factory's invalid-input contract (None) even though the class
    # constructor independently enforces the same security boundary.
    try:
        return DiscordNotifier(webhook_url)
    except ValueError:
        return None
