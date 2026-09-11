"""
Tests for security module.

Tests HMAC signing, game detection, and secret management.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.security import (
    GAME_PROCESS_NAMES,
    GameProcessStatus,
    create_signature,
    generate_nonce,
    get_app_secret,
    get_security_status,
    get_timestamp,
    is_game_running,
    is_secret_configured,
    sign_payload,
)


class TestSecretManagement:
    """Test secret loading and retrieval."""

    def test_get_app_secret_returns_bytes(self, configured_app_secret):
        """Test that get_app_secret returns bytes."""
        secret = get_app_secret()
        assert isinstance(secret, bytes)
        assert len(secret) == 64  # 64 hex chars = 32 bytes

    def test_app_secret_matches_configured_fixture(self, configured_app_secret):
        """Test that loaded secret matches environment variable."""
        secret = get_app_secret()
        expected = configured_app_secret.encode('utf-8')
        assert secret == expected

    def test_is_secret_configured_and_get_app_secret_without_secret(self):
        """Test offline behavior when APP_SECRET is not set."""
        from src.core import security

        assert security.APP_SECRET is None
        assert is_secret_configured() is False
        with pytest.raises(RuntimeError, match="APP_SECRET not provisioned"):
            get_app_secret()


@pytest.mark.usefixtures("configured_app_secret")
class TestHMACSigning:
    """Test HMAC-SHA256 signing functionality."""

    def test_create_signature_consistency(self):
        """Test that same inputs produce same signature."""
        sig1 = create_signature(
            timestamp=1706054400000,
            nonce="test-nonce-123",
            user_id="76561198321627695",
            track_id="spa_francorchamps",
            lap_time=138456,
        )
        sig2 = create_signature(
            timestamp=1706054400000,
            nonce="test-nonce-123",
            user_id="76561198321627695",
            track_id="spa_francorchamps",
            lap_time=138456,
        )
        assert sig1 == sig2
        assert len(sig1) == 64  # SHA-256 hex = 64 chars

    def test_create_signature_different_inputs(self):
        """Test that different inputs produce different signatures."""
        sig1 = create_signature(
            timestamp=1706054400000,
            nonce="test-nonce-123",
            user_id="76561198321627695",
            track_id="spa_francorchamps",
            lap_time=138456,
        )
        sig2 = create_signature(
            timestamp=1706054400000,
            nonce="test-nonce-456",  # Different nonce
            user_id="76561198321627695",
            track_id="spa_francorchamps",
            lap_time=138456,
        )
        assert sig1 != sig2

    def test_sign_payload_structure(self):
        """Test that sign_payload adds required fields."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa",
            "carId": "porsche",
            "time": 120000,
        }

        signed = sign_payload(payload)

        # Should add _timestamp, _nonce, _signature
        assert "_timestamp" in signed
        assert "_nonce" in signed
        assert "_signature" in signed

        # Original fields preserved
        assert signed["userId"] == "76561198321627695"
        assert signed["trackId"] == "spa"
        assert signed["carId"] == "porsche"
        assert signed["time"] == 120000

    def test_sign_payload_nonce_unique(self):
        """Test that each signed payload gets a unique nonce."""
        payload = {"userId": "test", "trackId": "spa", "carId": "porsche", "time": 120000}

        signed1 = sign_payload(payload.copy())
        signed2 = sign_payload(payload.copy())

        assert signed1["_nonce"] != signed2["_nonce"]


class TestTimestampAndNonce:
    """Test timestamp and nonce generation."""

    def test_get_timestamp_returns_int(self):
        """Test that get_timestamp returns an integer."""
        ts = get_timestamp()
        assert isinstance(ts, int)
        assert ts > 1700000000000  # Should be after 2023

    def test_generate_nonce_format(self):
        """Test that generate_nonce returns valid UUID format."""
        nonce = generate_nonce()
        assert isinstance(nonce, str)
        assert len(nonce) == 36  # UUID string length
        parts = nonce.split("-")
        assert len(parts) == 5  # UUID has 5 hyphen-separated parts

    def test_generate_nonce_unique(self):
        """Test that generate_nonce produces unique values."""
        nonces = [generate_nonce() for _ in range(100)]
        assert len(set(nonces)) == 100  # All should be unique


class TestGameDetection:
    """Test game process detection."""

    @patch('src.core.security.PSUTIL_AVAILABLE', False)
    def test_is_game_running_no_psutil(self):
        """Test that is_game_running returns UNKNOWN when psutil unavailable."""
        result = is_game_running()
        assert result == GameProcessStatus.UNKNOWN

    @patch('src.core.security.PSUTIL_AVAILABLE', True)
    @patch('psutil.process_iter')
    def test_is_game_running_found(self, mock_process_iter):
        """Test detection when game process exists."""
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'AssettoCorsaEVO.exe'}
        mock_process_iter.return_value = [mock_proc]

        result = is_game_running()
        assert result == GameProcessStatus.RUNNING

    @patch('src.core.security.PSUTIL_AVAILABLE', True)
    @patch('psutil.process_iter')
    def test_is_game_running_not_found(self, mock_process_iter):
        """Test detection when game process not running."""
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'some_other_process.exe'}
        mock_process_iter.return_value = [mock_proc]

        result = is_game_running()
        assert result == GameProcessStatus.NOT_RUNNING

    def test_game_process_names(self):
        """Test that known game process names are defined."""
        assert "AssettoCorsaEVO.exe" in GAME_PROCESS_NAMES
        assert "AC2-Win64-Shipping.exe" in GAME_PROCESS_NAMES


class TestSecurityStatus:
    """Test security status helper."""

    @patch("src.core.security.is_game_running", return_value=False)
    def test_get_security_status_includes_secret_configured(self, _mock_is_game_running):
        status = get_security_status()

        assert "secret_configured" in status
        assert isinstance(status["secret_configured"], bool)
