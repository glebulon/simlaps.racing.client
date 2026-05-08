"""
Personal Best Cache Service

Manages in-memory cache of personal best lap times for Discord integration.
Preloads from API and provides fast PB detection for new laps.
"""

import httpx
from dataclasses import dataclass
from typing import Dict, Tuple, Optional
from datetime import datetime


@dataclass
class PersonalBest:
    """Personal best entry for a track+car combination."""
    best_time_ms: int
    last_lap_id: Optional[str] = None
    updated_at: Optional[datetime] = None


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
            
            print(f"[PB_CACHE] Fetching from: {url}")
            print(f"[PB_CACHE] Params: {params}")

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params)

                print(f"[PB_CACHE] Response status: {response.status_code}")

                if response.status_code != 200:
                    print(f"[PB_CACHE] Failed to preload PBs: HTTP {response.status_code}")
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
                            updated_at = datetime.fromisoformat(set_at.replace("Z", "+00:00"))
                        except ValueError:
                            pass

                    self._cache[key] = PersonalBest(
                        best_time_ms=best_time,
                        updated_at=updated_at
                    )

                self._steam_id = steam_id
                self._loaded = True

                print(f"Preloaded {len(self._cache)} personal bests for Steam ID {steam_id}")
                return True
                
        except httpx.TimeoutException:
            print("PB preload request timed out")
            return False
        except httpx.RequestError as e:
            print(f"PB preload request failed: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error during PB preload: {e}")
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
        print(f"[PB_CACHE] check_and_update_pb: track={track_id}, car={car_id}, time={lap_time_ms}ms")
        print(f"[PB_CACHE] Cache loaded: {self._loaded}, size: {len(self._cache)}")
        
        key = self._normalize_key(track_id, car_id)
        current = self._cache.get(key)
        
        print(f"[PB_CACHE] Key: {key}, current PB: {current}")
        
        # If no existing PB or new time is faster, update and return True
        if current is None or lap_time_ms < current.best_time_ms:
            new_pb = PersonalBest(best_time_ms=lap_time_ms, updated_at=datetime.now())
            self._cache[key] = new_pb
            print(f"[PB_CACHE] NEW PB! Updated cache: {key} -> {lap_time_ms}ms")
            return True
        
        print(f"[PB_CACHE] Not a PB (current: {current.best_time_ms}ms, new: {lap_time_ms}ms)")
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
    
    def get_cache_stats(self) -> Dict[str, any]:
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
    
    def get_all_pbs(self) -> Dict[str, PersonalBest]:
        """
        Get all personal bests from cache.
        
        Returns:
            Dictionary with (track, car) keys and PersonalBest values
        """
        return {key: value for key, value in self._cache.items()}


# Global PB cache instance
_pb_cache: Optional[PBCache] = None


def get_pb_cache(server_url: str) -> PBCache:
    """
    Get or create global PB cache instance.
    
    Args:
        server_url: Base URL for API server
        
    Returns:
        PBCache instance
    """
    global _pb_cache
    if _pb_cache is None or _pb_cache.server_url != server_url:
        _pb_cache = PBCache(server_url)
    return _pb_cache
