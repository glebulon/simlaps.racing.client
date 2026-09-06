"""
Personal Best Cache Service

Manages in-memory cache of personal best lap times for Discord integration.
Preloads from API and provides fast PB detection for new laps.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from src.utils.structured_logger import (
    Component,
    log_debug,
    log_info,
    log_warning,
    log_error,
)


@dataclass
class PersonalBest:
    """Personal best entry for a track+car combination."""
    best_time_ms: int
    last_lap_id: Optional[str] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Keep timestamps comparable by storing them as UTC-aware values."""
        if self.updated_at is None:
            return

        if self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=timezone.utc)
        else:
            self.updated_at = self.updated_at.astimezone(timezone.utc)


class PBCache:
    """
    In-memory cache for personal best lap times.
    
    Key: (track_id, car_id) tuple
    Value: PersonalBest with fastest lap time
    """
    
    def __init__(self, server_url: str, timeout: float = 10.0):
        """
        Initialize PB cache.
        
        Args:
            server_url: Base URL for API server
            timeout: Request timeout in seconds
        """
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self._cache: Dict[Tuple[str, str], PersonalBest] = {}
        self._steam_id: Optional[str] = None
        self._loaded = False
    
    def _normalize_key(self, track_id: str, car_id: str) -> Tuple[str, str]:
        """
        Normalize track and car IDs for consistent key generation.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            
        Returns:
            Normalized key tuple
        """
        # Convert to lowercase and strip whitespace for consistency
        return (track_id.lower().strip(), car_id.lower().strip())
    
    async def preload_from_api(self, steam_id: str) -> bool:
        """
        Preload personal bests from API endpoint.
        
        Args:
            steam_id: Steam ID64 of the user
            
        Returns:
            True if preload was successful, False otherwise
        """
        try:
            url = f"{self.server_url}/api/laptimes/pb/by-steam"
            params = {
                "steamId": steam_id,
                "includeAll": "false"  # Only valid laps for PB comparison
            }
            
            log_debug(Component.PB_CACHE, "Fetching PBs from API", url=url, params=params)

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params)

                log_debug(Component.PB_CACHE, "PB preload response", status_code=response.status_code)

                if response.status_code != 200:
                    log_warning(Component.PB_CACHE, "Failed to preload PBs", status_code=response.status_code)
                    return False

                data = response.json()
                personal_bests = data.get("personalBests", [])

                # Clear existing cache and populate with new data
                self._cache.clear()

                for pb in personal_bests:
                    track_id = pb.get("trackId", "")
                    car_id = pb.get("carId", "")
                    best_time = pb.get("bestTime", 0)
                    set_at = pb.get("setAt")

                    if not track_id or not car_id or best_time <= 0:
                        continue

                    key = self._normalize_key(track_id, car_id)

                    # Parse timestamp if available
                    updated_at = None
                    if set_at:
                        try:
                            if set_at.endswith("Z"):
                                set_at = f"{set_at[:-1]}+00:00"
                            updated_at = datetime.fromisoformat(set_at)
                        except (TypeError, ValueError):
                            pass

                    self._cache[key] = PersonalBest(
                        best_time_ms=best_time,
                        updated_at=updated_at
                    )

                self._steam_id = steam_id
                self._loaded = True

                log_info(Component.PB_CACHE, "Preloaded personal bests", count=len(self._cache), steam_id=steam_id)
                return True
                
        except httpx.TimeoutException:
            log_warning(Component.PB_CACHE, "PB preload request timed out")
            return False
        except httpx.RequestError as e:
            log_warning(Component.PB_CACHE, "PB preload request failed", error=str(e))
            return False
        except Exception as e:
            log_error(Component.PB_CACHE, "Unexpected error during PB preload", error=str(e))
            return False
    
    def check_and_update_pb(self, track_id: str, car_id: str, lap_time_ms: int) -> bool:
        """
        Check if a lap time is a new personal best and update cache if so.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            lap_time_ms: Lap time in milliseconds
            
        Returns:
            True if this is a new personal best, False otherwise
        """
        log_debug(Component.PB_CACHE, "Checking PB", track=track_id, car=car_id, time_ms=lap_time_ms)
        
        key = self._normalize_key(track_id, car_id)
        current = self._cache.get(key)
        
        # If no existing PB or new time is faster, update and return True
        if current is None or lap_time_ms < current.best_time_ms:
            new_pb = PersonalBest(
                best_time_ms=lap_time_ms,
                updated_at=datetime.now(timezone.utc),
            )
            self._cache[key] = new_pb
            log_info(Component.PB_CACHE, "New personal best!", track=track_id, car=car_id, time_ms=lap_time_ms)
            return True
        
        log_debug(Component.PB_CACHE, "Not a PB", current_ms=current.best_time_ms, new_ms=lap_time_ms)
        return False
    
    def get_personal_best(self, track_id: str, car_id: str) -> Optional[PersonalBest]:
        """
        Get current personal best for a track+car combination.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            
        Returns:
            PersonalBest entry or None if not found
        """
        if not self._loaded:
            return None
        
        key = self._normalize_key(track_id, car_id)
        return self._cache.get(key)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        return {
            "loaded": self._loaded,
            "steam_id": self._steam_id,
            "combo_count": len(self._cache),
            "oldest_entry": min(
                (pb.updated_at for pb in self._cache.values() if pb.updated_at),
                default=None
            ),
            "newest_entry": max(
                (pb.updated_at for pb in self._cache.values() if pb.updated_at),
                default=None
            )
        }
    
    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._steam_id = None
        self._loaded = False
    
    def is_loaded(self) -> bool:
        """Check if cache has been loaded."""
        return self._loaded

    def get_steam_id(self) -> Optional[str]:
        """Get the Steam ID associated with this cache."""
        return self._steam_id
    
    def get_all_pbs(self) -> Dict[Tuple[str, str], PersonalBest]:
        """
        Get all personal bests from cache.
        
        Returns:
            Dictionary with (track, car) keys and PersonalBest values
        """
        return {key: value for key, value in self._cache.items()}
