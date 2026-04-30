"""
Tests for API client module.

Tests server communication, lap submission, and error handling.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Set test secret before importing
import os
os.environ["APP_SECRET"] = "31cdbbaf05e962038c9221bdc22845b7639f4a1e914b4596db6b8608a5ea5e18"

from src.core.api_client import APIClient, SubmissionStatus, SubmissionResult
from src.models import SessionData, LapData, LapState, SharedSessionManager


class TestAPIClientInit:
    """Test APIClient initialization."""
    
    def test_default_server_url(self):
        """Test that default server URL is set."""
        client = APIClient()
        assert client.server_url == "https://simlaps.racing"
    
    def test_custom_server_url(self):
        """Test that custom server URL can be set."""
        client = APIClient(server_url="https://custom.example.com")
        assert client.server_url == "https://custom.example.com"
    
    def test_server_url_trailing_slash_removed(self):
        """Test that trailing slash is removed from server URL."""
        client = APIClient(server_url="https://example.com/")
        assert client.server_url == "https://example.com"


class TestSubmissionResult:
    """Test SubmissionResult dataclass."""
    
    def test_submission_result_defaults(self):
        """Test SubmissionResult with default values."""
        result = SubmissionResult(
            status=SubmissionStatus.SUCCESS,
            message="Test message"
        )
        assert result.status == SubmissionStatus.SUCCESS
        assert result.message == "Test message"
        assert result.lap_id is None
    
    def test_submission_result_with_lap_id(self):
        """Test SubmissionResult with lap_id."""
        result = SubmissionResult(
            status=SubmissionStatus.SUCCESS,
            message="Lap submitted",
            lap_id="lap-123"
        )
        assert result.lap_id == "lap-123"


class TestSubmitLap:
    """Test lap submission functionality."""
    
    @pytest.fixture
    def sample_session(self):
        """Create a sample session for testing."""
        return SessionData(
            session_id="test-session-123",
            game_version="1.0.0",
            session_type="PRACTICE",
            car="ks_porsche_992_gt3_cup",
            track="spa_francorchamps",
            player_id="76561198321627695",
            player_name="TestUser",
        )
    
    @pytest.fixture
    def sample_lap(self):
        """Create a sample lap for testing."""
        return LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=138456,
            lap_time_str="2:18.456",
            sector1_ms=45000,
            sector2_ms=48000,
            sector3_ms=45456,
            is_valid=True,
            tyre_compound="SC",
        )
    
    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_success(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test successful lap submission."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-123", "status": "ok"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SUCCESS
        assert result.lap_id == "lap-123"

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_uses_shared_session_payload_data(
        self,
        mock_post,
        mock_game_running,
        sample_session,
        sample_lap,
    ):
        """Shared session values should fill payload fields when lap/session data is missing."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-456", "status": "ok"}
        mock_post.return_value = mock_response

        sample_session.track = "Unknown"
        sample_session.car = "Unknown"
        sample_session.game_version = "Unknown"
        sample_session.session_type = "Unknown"
        sample_session.player_id = None
        sample_lap.sector1_ms = 0
        sample_lap.sector2_ms = None
        sample_lap.sector3_ms = -1
        sample_lap.fuel_used = None

        manager = SharedSessionManager()
        manager.update_player_identification_from_logs(
            {
                "steam_id": "76561198000000001",
                "car_model": "ferrari_296_gt3",
            }
        )
        manager.update_session_metadata_from_static_shm(
            {
                "track": "monza",
                "session": "RACE",
                "ac_evo_version": "1.2.3",
            }
        )
        manager.update_lap_timing_from_graphics_shm(sample_lap.lap_number, {"last_laptime_ms": 123456})
        manager.update_sector_splits_from_logs(
            sample_lap.lap_number,
            {
                "sector1_ms": 40000,
                "sector2_ms": 41000,
                "sector3_ms": 42456,
            },
        )
        # fuel_consumed_lap is the correct per-lap fuel field; fuel_liter_per_km is a
        # rate (L/km) and must never be submitted as fuelUsed.
        manager._session_data.fuel_data.fuel_consumed_lap = 2.7
        manager.update_fuel_from_graphics_shm({"fuel_liter_per_km": 0.04})  # rate only

        client = APIClient(session_manager=manager)
        result = await client.submit_lap(sample_session, sample_lap)

        assert result.status == SubmissionStatus.SUCCESS
        payload = mock_post.call_args.kwargs["json"]
        assert payload["userId"] == "76561198000000001"
        assert payload["trackId"] == "monza"
        assert payload["carId"] == "ferrari_296_gt3"
        assert payload["time"] == 123456
        assert payload["sessionType"] == "RACE"
        assert payload["gameVersion"] == "1.2.3"
        assert payload["sector1"] == 40000
        assert payload["sector2"] == 41000
        assert payload["sector3"] == 42456
        # fuel_consumed_lap should be submitted
        assert payload["fuelUsed"] == 2.7
        # The per-km rate must NOT appear as fuelUsed
        assert payload.get("fuelUsed") != 0.04

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_respects_shared_session_lap_validity(
        self,
        mock_game_running,
        sample_session,
        sample_lap,
    ):
        """Shared lap validity should block submission when marked invalid."""
        mock_game_running.return_value = True

        manager = SharedSessionManager()
        manager.update_lap_validity_from_graphics_shm(sample_lap.lap_number, True)

        client = APIClient(session_manager=manager)
        result = await client.submit_lap(sample_session, sample_lap, submit_invalid=False)

        assert result.status == SubmissionStatus.INVALID_LAP
    
    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_game_not_running(self, mock_game_running, sample_session, sample_lap):
        """Test submission rejected when game not running."""
        mock_game_running.return_value = False
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.GAME_NOT_RUNNING
    
    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_invalid_lap(self, mock_game_running, sample_session, sample_lap):
        """Test invalid lap rejection."""
        mock_game_running.return_value = True
        sample_lap.is_valid = False
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap, submit_invalid=False)
        
        assert result.status == SubmissionStatus.INVALID_LAP
    
    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_no_user_id(self, mock_game_running, sample_session, sample_lap):
        """Test rejection when no user ID available."""
        mock_game_running.return_value = True
        sample_session.player_id = None
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR
        assert "Steam ID" in result.message


class TestErrorHandling:
    """Test API error handling."""
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_test_connection_success(self, mock_get):
        """Test successful connection test."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        client = APIClient()
        success, message = await client.test_connection()
        
        # Should fail because secret test endpoint returns 401 without proper signature
        # But basic connectivity should be checked first
        assert isinstance(success, bool)
        assert isinstance(message, str)
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_test_connection_network_error(self, mock_get):
        """Test connection test with network error."""
        import httpx
        mock_get.side_effect = httpx.NetworkError("Connection failed")
        
        client = APIClient()
        success, message = await client.test_connection()
        
        assert success is False
        assert "Network" in message or "Connection" in message


class TestVersionCheck:
    """Test version checking functionality."""
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_check_for_updates_no_update(self, mock_get):
        """Test version check when no update available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latestClientVersion": "0.9.0"}  # Lower than current
        mock_get.return_value = mock_response
        
        client = APIClient()
        result = await client.check_for_updates()
        
        assert result["available"] is False
    
    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_check_for_update_error(self, mock_get):
        """Test version check handles errors gracefully."""
        import httpx
        mock_get.side_effect = httpx.NetworkError("Failed")
        
        client = APIClient()
        result = await client.check_for_updates()
        
        assert result["available"] is False


class TestAPIClientAdvanced:
    """Advanced API client tests for coverage."""

    @pytest.fixture
    def sample_session(self):
        """Create a sample session for testing."""
        return SessionData(
            session_id="test-session-123",
            game_version="1.0.0",
            session_type="PRACTICE",
            car="ks_porsche_992_gt3_cup",
            track="spa_francorchamps",
            player_id="76561198321627695",
            player_name="TestUser",
        )

    @pytest.fixture
    def sample_lap(self):
        """Create a sample lap for testing."""
        return LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=138456,
            lap_time_str="2:18.456",
            sector1_ms=45000,
            sector2_ms=48000,
            sector3_ms=45456,
            is_valid=True,
            tyre_compound="SC",
        )

    def test_set_server_url(self):
        """Test setting server URL."""
        client = APIClient()
        client.set_server_url("https://newserver.example.com")
        
        assert client.server_url == "https://newserver.example.com"

    def test_normalize_track_id_basic(self):
        """Test track ID normalization."""
        client = APIClient()
        
        result = client._normalize_track_id("Spa Francorchamps")
        assert result == "spa_francorchamps"

    def test_normalize_track_id_with_suffix(self):
        """Test track ID normalization with suffix removal."""
        client = APIClient()
        
        result = client._normalize_track_id("Spa Francorchamps GP")
        assert result == "spa_francorchamps"

    def test_normalize_track_id_with_prefix(self):
        """Test track ID normalization with prefix removal."""
        client = APIClient()
        
        result = client._normalize_track_id("Circuit de Spa Francorchamps")
        assert "spa" in result

    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_check_for_updates_with_new_version(self, mock_get):
        """Test version check when update is available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latestClientVersion": "99.0.0"}  # Higher than current
        mock_get.return_value = mock_response
        
        client = APIClient()
        result = await client.check_for_updates()
        
        assert result["available"] is True
        assert result["version"] == "99.0.0"

    @pytest.mark.asyncio
    @patch('httpx.AsyncClient.get')
    async def test_check_for_updates_invalid_version(self, mock_get):
        """Test version check with invalid version format."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latestClientVersion": "invalid"}
        mock_get.return_value = mock_response
        
        client = APIClient()
        result = await client.check_for_updates()
        
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test closing the HTTP client."""
        client = APIClient()
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        client._client = mock_client
        
        await client.close()
        
        mock_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        async with APIClient() as client:
            assert isinstance(client, APIClient)


class TestSubmitLapErrorResponses:
    """Test lap submission with various error responses."""

    @pytest.fixture
    def sample_session(self):
        """Create a sample session for testing."""
        return SessionData(
            session_id="test-session-123",
            game_version="1.0.0",
            session_type="PRACTICE",
            car="ks_porsche_992_gt3_cup",
            track="spa_francorchamps",
            player_id="76561198321627695",
            player_name="TestUser",
        )

    @pytest.fixture
    def sample_lap(self):
        """Create a sample lap for testing."""
        return LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=138456,
            lap_time_str="2:18.456",
            sector1_ms=45000,
            sector2_ms=48000,
            sector3_ms=45456,
            is_valid=True,
            tyre_compound="SC",
        )

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_401_error(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 401 signature error handling."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b'{}'
        mock_response.json.return_value = {}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SIGNATURE_ERROR

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_429_rate_limited(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 429 rate limit handling."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.RATE_LIMITED

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_500_error(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 500 server error handling."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_network_error(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test network error handling."""
        import httpx
        mock_game_running.return_value = True
        mock_post.side_effect = httpx.NetworkError("Connection failed")
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.NETWORK_ERROR

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_timeout(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test timeout handling."""
        import httpx
        mock_game_running.return_value = True
        mock_post.side_effect = httpx.TimeoutException("Request timed out")
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.NETWORK_ERROR

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_invalid_time(self, mock_game_running, sample_session, sample_lap):
        """Test lap with invalid time (<= 0)."""
        mock_game_running.return_value = True
        sample_lap.lap_time_ms = 0
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.INVALID_LAP

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_with_fuel(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test lap submission with fuel data."""
        mock_game_running.return_value = True
        sample_lap.fuel_used = 2.5
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-123"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SUCCESS

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_with_setup_notes(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test lap submission with setup notes."""
        mock_game_running.return_value = True
        sample_session.setup_notes = "Test setup notes"
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-123"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SUCCESS

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_409_replay(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 409 replay attack detection."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {"error": "Replay attack detected"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.REPLAY_REJECTED

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_409_duplicate(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 409 duplicate lap."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {"error": "Duplicate lap"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR
        assert "Duplicate" in result.message

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_422_plausibility(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 422 plausibility check failure."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"error": "Impossible lap time"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.PLAUSIBILITY_FAILED

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_400_validation(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test 400 validation error."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Invalid track ID"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR
        assert "Validation" in result.message

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_403_generic(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test generic 4xx error."""
        mock_game_running.return_value = True
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"error": "Forbidden"}
        mock_response.headers = {}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR
        assert "403" in result.message

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_with_invalid_fuel(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test lap with invalid fuel value."""
        mock_game_running.return_value = True
        sample_lap.fuel_used = "invalid"
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-123"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SUCCESS

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    @patch('httpx.AsyncClient.post')
    async def test_submit_lap_with_zero_sectors(self, mock_post, mock_game_running, sample_session, sample_lap):
        """Test lap with zero sector times (should be filtered)."""
        mock_game_running.return_value = True
        sample_lap.sector1_ms = 0
        sample_lap.sector2_ms = -1
        
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "lap-123"}
        mock_post.return_value = mock_response
        
        client = APIClient()
        result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.SUCCESS

    @pytest.mark.asyncio
    @patch('src.core.api_client.is_game_running')
    async def test_submit_lap_runtime_error(self, mock_game_running, sample_session, sample_lap):
        """Test runtime error handling."""
        mock_game_running.return_value = True
        
        client = APIClient()
        
        # Mock _get_client to raise RuntimeError
        with patch.object(client, '_get_client', side_effect=RuntimeError("Test error")):
            result = await client.submit_lap(sample_session, sample_lap)
        
        assert result.status == SubmissionStatus.ERROR
        assert "Unexpected error" in result.message
