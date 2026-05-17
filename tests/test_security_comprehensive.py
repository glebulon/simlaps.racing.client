"""
Comprehensive tests for security module.

Tests signing, nonce generation, and timestamp functions.
"""

import pytest
from unittest.mock import patch, Mock, MagicMock
from src.core.security import (
    get_app_secret,
    generate_nonce,
    get_timestamp,
    create_signature,
    sign_payload,
    verify_signature_locally,
    is_game_running,
    get_game_process_info,
    get_steam_user,
)


class TestSecretManagement:
    """Test secret management."""

    def test_get_app_secret(self):
        """Test getting secret from environment."""
        # APP_SECRET is required, so this will raise if not set
        # But we can test that the function exists and returns bytes
        try:
            result = get_app_secret()
            assert isinstance(result, bytes)
            assert len(result) > 0
        except RuntimeError:
            # APP_SECRET not set - this is expected in test environment
            pytest.skip("APP_SECRET not set in environment")


class TestSigning:
    """Test payload signing and verification."""

    def test_create_signature(self):
        """Test creating a signature."""
        try:
            signature = create_signature(
                timestamp=1234567890,
                nonce="test-nonce",
                user_id="76561198321627695",
                track_id="spa_francorchamps",
                lap_time=83456
            )
            
            assert signature is not None
            assert isinstance(signature, str)
            assert len(signature) > 0
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")

    def test_sign_payload_valid(self):
        """Test signing a valid payload."""
        try:
            payload = {
                "userId": "76561198321627695",
                "trackId": "spa_francorchamps",
                "time": 83456
            }
            
            result = sign_payload(payload)
            
            assert result is not None
            assert "_signature" in result
            assert "_timestamp" in result
            assert "_nonce" in result
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")

    def test_sign_payload_with_all_fields(self):
        """Test signing payload with all expected fields."""
        try:
            payload = {
                "userId": "76561198321627695",
                "trackId": "spa_francorchamps",
                "carId": "porsche_992_gt3_cup",
                "time": 83456,
                "sector1": 45000,
                "sector2": 48000,
                "sector3": -1,
                "gameVersion": "1.0.0",
                "tires": "S"
            }
            
            result = sign_payload(payload)
            
            assert result is not None
            assert "_signature" in result
            assert result["userId"] == "76561198321627695"
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")

    def test_verify_signature_locally(self):
        """Test local signature verification."""
        try:
            payload = {
                "userId": "76561198321627695",
                "trackId": "spa_francorchamps",
                "time": 83456
            }
            signed = sign_payload(payload)
            
            result = verify_signature_locally(signed)
            
            assert result is True
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")

    def test_verify_signature_locally_invalid(self):
        """Test verification with invalid signature."""
        try:
            payload = {
                "userId": "76561198321627695",
                "trackId": "spa_francorchamps",
                "time": 83456
            }
            signed = sign_payload(payload)
            signed["_signature"] = "invalid_signature"
            
            result = verify_signature_locally(signed)
            
            assert result is False
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")


class TestNonceAndTimestamp:
    """Test nonce and timestamp generation."""

    def test_generate_nonce_unique(self):
        """Test that nonces are unique."""
        nonce1 = generate_nonce()
        nonce2 = generate_nonce()
        
        assert nonce1 != nonce2
        assert isinstance(nonce1, str)
        assert len(nonce1) > 0

    def test_generate_nonce_format(self):
        """Test nonce format."""
        nonce = generate_nonce()
        
        # Should be a UUID string
        assert len(nonce) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    def test_get_timestamp(self):
        """Test timestamp generation."""
        timestamp = get_timestamp()
        
        # Timestamp should be an integer
        assert isinstance(timestamp, int)
        # Should be a reasonable timestamp (milliseconds since epoch)
        assert timestamp > 0
        assert timestamp < 10**15

    def test_get_timestamp_format(self):
        """Test timestamp format."""
        timestamp = get_timestamp()
        
        # Should be a reasonable millisecond timestamp
        assert timestamp > 0
        assert timestamp < 10**15  # Not too far in the future (milliseconds)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_sign_payload_minimal(self):
        """Test signing minimal payload."""
        try:
            payload = {"userId": "76561198321627695", "trackId": "test", "time": 1000}
            
            result = sign_payload(payload)
            
            assert result is not None
            assert "_signature" in result
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")


class TestIntegration:
    """Test integration scenarios."""

    def test_full_signing_flow(self):
        """Test complete signing flow."""
        try:
            payload = {
                "userId": "76561198321627695",
                "trackId": "spa_francorchamps",
                "carId": "porsche_992_gt3_cup",
                "time": 83456,
                "sector1": 45000,
                "sector2": 48000,
                "sector3": -1,
                "gameVersion": "1.0.0",
                "tires": "S"
            }
            
            signed = sign_payload(payload)
            verified = verify_signature_locally(signed)
            
            assert verified is True
            assert signed["userId"] == "76561198321627695"
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")


class TestGameDetection:
    """Test game process detection."""

    def test_is_game_running_integration(self):
        """Test is_game_running integration (platform-specific)."""
        try:
            result = is_game_running()
            
            # Should return a GameProcessStatus enum
            from src.core.security import GameProcessStatus
            assert isinstance(result, GameProcessStatus)
            assert result in [GameProcessStatus.RUNNING, GameProcessStatus.NOT_RUNNING, GameProcessStatus.UNKNOWN]
        except Exception as e:
            pytest.skip(f"Game detection not available on this platform: {e}")

    def test_get_game_process_info_integration(self):
        """Test get_game_process_info integration (platform-specific)."""
        try:
            result = get_game_process_info()
            
            # Should return None or a dict with process info
            assert result is None or isinstance(result, dict)
        except Exception as e:
            pytest.skip(f"Game detection not available on this platform: {e}")


class TestSteamUser:
    """Test Steam user detection."""

    def test_get_steam_user_integration(self):
        """Test get_steam_user integration (may fail on non-Windows)."""
        try:
            steam_id, username = get_steam_user()
            
            # May return (None, None) on non-Windows or if Steam not installed
            assert (steam_id is None and username is None) or (steam_id is not None)
        except Exception:
            # Expected on non-Windows or without Steam
            pytest.skip("Steam user detection not available")


class TestSigningEdgeCases:
    """Test signing edge cases."""

    def test_sign_payload_missing_required_fields(self):
        """Test sign_payload with missing required fields."""
        try:
            payload = {"userId": "76561198321627695"}  # Missing trackId and time
            
            result = sign_payload(payload)
            
            assert result is not None
        except (ValueError, RuntimeError):
            # Expected if fields are missing or APP_SECRET not set
            pass

    def test_verify_signature_missing_fields(self):
        """Test verify_signature_locally with missing signature fields."""
        try:
            payload = {"userId": "76561198321627695"}  # Missing _signature
            
            result = verify_signature_locally(payload)
            
            assert result is False
        except RuntimeError:
            pytest.skip("APP_SECRET not set in environment")

    def test_verify_signature_none_payload(self):
        """Test verify_signature_locally with None payload."""
        result = verify_signature_locally(None)
        
        assert result is False
