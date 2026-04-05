"""
Tests for Discord integration functionality.

Tests Discord notifier, PB cache, and integration points.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.core.discord_notifier import DiscordNotifier, LapData, create_discord_notifier
from src.core.pb_cache import PBCache, PersonalBest


class TestDiscordNotifier:
    """Test Discord notifier functionality."""
    
    def test_format_lap_time(self):
        """Test lap time formatting."""
        from src.utils.helpers import format_lap_time
        
        # Test various lap times
        assert format_lap_time(65432) == "1:05.432"
        assert format_lap_time(120000) == "2:00.000"
        assert format_lap_time(95847) == "1:35.847"
        assert format_lap_time(60000) == "1:00.000"
    
    def test_create_lap_embed(self):
        """Test Discord embed creation."""
        notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
        
        lap_data = LapData(
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
            tire_compound="SC"
        )
        
        embed = notifier.create_lap_embed(lap_data)
        
        # Check embed structure
        assert embed["title"] == "Lap Time Recorded"
        assert embed["color"] == 5814783  # Gold for PB
        
        # Check that main field exists with PB indicator
        field_names = [field["name"] for field in embed["fields"]]
        assert "🫙 New PB" in field_names  # PB indicator field name
        
        # Check that driver info is in the main field value
        main_field = next(f for f in embed["fields"] if f["name"] == "🫙 New PB")
        assert "TestUser" in main_field["value"]
        assert "Porsche" in main_field["value"]
        assert "Laguna Seca" in main_field["value"]
    
    def test_create_lap_embed_invalid(self):
        """Test Discord embed for invalid lap."""
        notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
        
        lap_data = LapData(
            track_name="brands_hatch",
            car_name="ks_toyota_gr86",
            lap_time_ms=78664,
            valid=False,
            steam_id="76561198321627695",
            is_personal_best=False
        )
        
        embed = notifier.create_lap_embed(lap_data)
        
        # Check color for invalid lap
        assert embed["color"] == 15158332  # Red for invalid
        
        # Check that main field exists (non-PB)
        field_names = [field["name"] for field in embed["fields"]]
        assert "Lap Recorded" in field_names
    
    @pytest.mark.asyncio
    async def test_post_lap_success(self):
        """Test successful lap posting."""
        with patch('httpx.AsyncClient') as mock_client:
            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_client.return_value.__aenter__.return_value.post.return_value = mock_response
            
            notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
            
            lap_data = LapData(
                track_name="test_track",
                car_name="test_car", 
                lap_time_ms=60000,
                valid=True,
                steam_id="76561198321627695"
            )
            
            result = await notifier.post_lap(lap_data)
            assert result is True
    
    @pytest.mark.asyncio
    async def test_post_lap_failure(self):
        """Test lap posting failure."""
        with patch('httpx.AsyncClient') as mock_client:
            # Mock failed response with RuntimeError (which is now caught)
            mock_client.return_value.__aenter__.return_value.post.side_effect = RuntimeError("Network error")
            
            notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
            
            lap_data = LapData(
                track_name="test_track",
                car_name="test_car",
                lap_time_ms=60000,
                valid=True,
                steam_id="76561198321627695"
            )
            
            result = await notifier.post_lap(lap_data)
            assert result is False
    
    @pytest.mark.asyncio
    async def test_send_test_message(self):
        """Test sending test message."""
        with patch('httpx.AsyncClient') as mock_client:
            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_client.return_value.__aenter__.return_value.post.return_value = mock_response
            
            notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
            result = await notifier.send_test_message()
            assert result is True
    
    def test_validate_webhook_url(self):
        """Test webhook URL validation."""
        # Valid URLs
        assert DiscordNotifier.validate_webhook_url("https://discord.com/api/webhooks/1234567890/abcdef-123456")
        assert DiscordNotifier.validate_webhook_url("https://discord.com/api/webhooks/123/abc")
        
        # Invalid URLs
        assert not DiscordNotifier.validate_webhook_url("")
        assert not DiscordNotifier.validate_webhook_url(None)
        assert not DiscordNotifier.validate_webhook_url("https://example.com/webhook")
        assert not DiscordNotifier.validate_webhook_url("not-a-url")
        assert not DiscordNotifier.validate_webhook_url("https://discord.com/api/webhooks/")  # No id/token
    
    def test_create_discord_notifier(self):
        """Test Discord notifier factory function."""
        # Valid URL
        notifier = create_discord_notifier("https://discord.com/api/webhooks/123/abc")
        assert notifier is not None
        assert isinstance(notifier, DiscordNotifier)
        
        # Invalid URL
        notifier = create_discord_notifier("invalid-url")
        assert notifier is None


class TestPBCache:
    """Test personal best cache functionality."""
    
    def test_normalize_key(self):
        """Test key normalization."""
        cache = PBCache("http://localhost:3000")
        
        key1 = cache._normalize_key("Laguna_Seca", "KS_Porsche_992_GT3_Cup")
        key2 = cache._normalize_key("laguna_seca", "ks_porsche_992_gt3_cup")
        key3 = cache._normalize_key("  LAGUNA_SECA  ", "  ks_porsche_992_gt3_cup  ")
        
        assert key1 == key2 == key3 == ("laguna_seca", "ks_porsche_992_gt3_cup")
    
    @pytest.mark.asyncio
    async def test_preload_from_api_success(self):
        """Test successful API preload."""
        mock_response_data = {
            "steamId": "76561198321627695",
            "personalBests": [
                {
                    "trackId": "laguna_seca",
                    "carId": "ks_porsche_992_gt3_cup",
                    "bestTime": 92295,
                    "setAt": "2026-02-14T10:00:00Z"
                },
                {
                    "trackId": "brands_hatch",
                    "carId": "ks_toyota_gr86",
                    "bestTime": 78664,
                    "setAt": "2026-02-14T11:00:00Z"
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_response_data
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response
            
            cache = PBCache("http://localhost:3000")
            result = await cache.preload_from_api("76561198321627695")
            
            assert result is True
            assert cache.is_loaded()
            assert cache.get_steam_id() == "76561198321627695"
            assert len(cache._cache) == 2
            
            # Check specific entries
            pb = cache.get_personal_best("laguna_seca", "ks_porsche_992_gt3_cup")
            assert pb is not None
            assert pb.best_time_ms == 92295
    
    @pytest.mark.asyncio
    async def test_preload_from_api_failure(self):
        """Test API preload failure."""
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Network error")
            
            cache = PBCache("http://localhost:3000")
            result = await cache.preload_from_api("76561198321627695")
            
            assert result is False
            assert not cache.is_loaded()
    
    def test_check_and_update_pb_new_pb(self):
        """Test PB detection for new personal best."""
        cache = PBCache("http://localhost:3000")
        cache._loaded = True  # Mark cache as loaded for get_personal_best to work
        
        # No existing PB - should be treated as PB
        result = cache.check_and_update_pb("test_track", "test_car", 60000)
        assert result is True
        
        # Check the cache entry
        pb = cache.get_personal_best("test_track", "test_car")
        assert pb is not None
        assert pb.best_time_ms == 60000
    
    def test_check_and_update_pb_faster_lap(self):
        """Test PB detection for faster lap."""
        cache = PBCache("http://localhost:3000")
        cache._loaded = True  # Mark cache as loaded
        
        # Set initial PB
        cache._cache[("test_track", "test_car")] = PersonalBest(best_time_ms=65000)
        
        # Faster lap - should be new PB
        result = cache.check_and_update_pb("test_track", "test_car", 60000)
        assert result is True
        
        # Check updated cache
        pb = cache.get_personal_best("test_track", "test_car")
        assert pb.best_time_ms == 60000
    
    def test_check_and_update_pb_slower_lap(self):
        """Test PB detection for slower lap."""
        cache = PBCache("http://localhost:3000")
        cache._loaded = True  # Mark cache as loaded
        
        # Set initial PB
        cache._cache[("test_track", "test_car")] = PersonalBest(best_time_ms=60000)
        
        # Slower lap - should not be PB
        result = cache.check_and_update_pb("test_track", "test_car", 65000)
        assert result is False
        
        # Check cache unchanged
        pb = cache.get_personal_best("test_track", "test_car")
        assert pb.best_time_ms == 60000
    
    def test_check_and_update_pb_not_loaded(self):
        """Test PB detection when cache not loaded."""
        cache = PBCache("http://localhost:3000")
        
        # Cache not loaded - should return True to avoid missing notifications
        result = cache.check_and_update_pb("test_track", "test_car", 60000)
        assert result is True
    
    def test_get_cache_stats(self):
        """Test cache statistics."""
        cache = PBCache("http://localhost:3000")
        
        # Empty cache stats
        stats = cache.get_cache_stats()
        assert stats["loaded"] is False
        assert stats["steam_id"] is None
        assert stats["combo_count"] == 0
        
        # Add some data
        cache._steam_id = "76561198321627695"
        cache._loaded = True
        cache._cache[("test_track", "test_car")] = PersonalBest(
            best_time_ms=60000,
            updated_at=datetime.now()
        )
        
        stats = cache.get_cache_stats()
        assert stats["loaded"] is True
        assert stats["steam_id"] == "76561198321627695"
        assert stats["combo_count"] == 1
        assert stats["oldest_entry"] is not None
        assert stats["newest_entry"] is not None
    
    def test_clear_cache(self):
        """Test cache clearing."""
        cache = PBCache("http://localhost:3000")
        
        # Add some data
        cache._steam_id = "76561198321627695"
        cache._loaded = True
        cache._cache[("test_track", "test_car")] = PersonalBest(best_time_ms=60000)
        
        # Clear cache
        cache.clear()
        
        assert not cache.is_loaded()
        assert cache.get_steam_id() is None
        assert len(cache._cache) == 0


class TestIntegration:
    """Test integration between Discord and PB cache."""
    
    @pytest.mark.asyncio
    async def test_discord_pb_workflow(self):
        """Test complete Discord PB workflow."""
        # Mock successful Discord post
        with patch('httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_client.return_value.__aenter__.return_value.post.return_value = mock_response
            
            # Create cache and notifier
            cache = PBCache("http://localhost:3000")
            notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
            
            # Simulate initial PB
            is_pb1 = cache.check_and_update_pb("test_track", "test_car", 65000)
            assert is_pb1 is True
            
            lap_data1 = LapData(
                track_name="test_track",
                car_name="test_car",
                lap_time_ms=65000,
                valid=True,
                steam_id="76561198321627695",
                is_personal_best=True
            )
            
            result1 = await notifier.post_lap(lap_data1)
            assert result1 is True
            
            # Simulate faster lap (new PB)
            is_pb2 = cache.check_and_update_pb("test_track", "test_car", 60000)
            assert is_pb2 is True
            
            lap_data2 = LapData(
                track_name="test_track",
                car_name="test_car",
                lap_time_ms=60000,
                valid=True,
                steam_id="76561198321627695",
                is_personal_best=True
            )
            
            result2 = await notifier.post_lap(lap_data2)
            assert result2 is True
            
            # Simulate slower lap (not PB)
            is_pb3 = cache.check_and_update_pb("test_track", "test_car", 70000)
            assert is_pb3 is False
            
            # Should not post for non-PB in PB-only mode


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
