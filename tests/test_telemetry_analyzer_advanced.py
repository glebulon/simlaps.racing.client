"""
Advanced tests for telemetry analyzer to improve coverage.

Tests the analyze function, track profile selection, and internal utilities.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.core.telemetry_analyzer import (
    _select_track_profile_for_analysis,
    _safe_4,
    _sanitize_slip,
    AnalysisResult,
)
from src.core.telemetry_capture import FrameData, CaptureMetadata
from datetime import datetime, timezone


class TestSelectTrackProfile:
    """Test track profile selection for analysis."""

    def test_select_track_profile_with_name(self):
        """Test selecting track profile by name."""
        result = _select_track_profile_for_analysis("spa_francorchamps")
        
        # Should return a tuple
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_select_track_profile_none(self):
        """Test selecting track profile with None."""
        result = _select_track_profile_for_analysis(None)
        
        assert result == (None, None)

    def test_select_track_profile_empty_string(self):
        """Test selecting track profile with empty string."""
        result = _select_track_profile_for_analysis("")
        
        assert result == (None, None)

    def test_select_track_profile_path_fallback(self):
        """Test selecting track profile with path-style name."""
        result = _select_track_profile_for_analysis("circuit_de_spa_francorchamps gp")
        
        # Should attempt path matching
        assert isinstance(result, tuple)


class TestSafe4:
    """Test _safe_4 utility function."""

    def test_safe_4_with_list_longer_than_4(self):
        """Test _safe_4 with list longer than 4 elements."""
        result = _safe_4([1, 2, 3, 4, 5, 6])
        
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_list_exactly_4(self):
        """Test _safe_4 with exactly 4 elements."""
        result = _safe_4([1, 2, 3, 4])
        
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_list_shorter_than_4(self):
        """Test _safe_4 with list shorter than 4 elements."""
        result = _safe_4([1, 2])
        
        assert result == [1, 2, 0.0, 0.0]

    def test_safe_4_with_empty_list(self):
        """Test _safe_4 with empty list."""
        result = _safe_4([])
        
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_tuple(self):
        """Test _safe_4 with tuple."""
        result = _safe_4((1, 2, 3, 4, 5))
        
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_dict(self):
        """Test _safe_4 with dict (should treat as invalid)."""
        result = _safe_4({"a": 1, "b": 2})
        
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_string(self):
        """Test _safe_4 with string (should treat as invalid)."""
        result = _safe_4("test")
        
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_none(self):
        """Test _safe_4 with None."""
        result = _safe_4(None)
        
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_custom_default(self):
        """Test _safe_4 with custom default value."""
        result = _safe_4([1, 2], default=5.0)
        
        assert result == [1, 2, 5.0, 5.0]


class TestSanitizeSlip:
    """Test _sanitize_slip utility function."""

    def test_sanitize_slip_valid_positive(self):
        """Test sanitizing valid positive slip."""
        result = _sanitize_slip(0.5)
        
        assert result == 0.5

    def test_sanitize_slip_zero(self):
        """Test sanitizing zero slip."""
        result = _sanitize_slip(0.0)
        
        assert result == 0.0

    def test_sanitize_slip_negative(self):
        """Test sanitizing negative slip."""
        result = _sanitize_slip(-0.5)
        
        assert result == 0.0

    def test_sanitize_slip_large_positive(self):
        """Test sanitizing large positive slip (> 5.0)."""
        result = _sanitize_slip(10.0)
        
        assert result == 5.0

    def test_sanitize_slip_exactly_5(self):
        """Test sanitizing slip exactly at 5.0."""
        result = _sanitize_slip(5.0)
        
        assert result == 5.0

    def test_sanitize_slip_with_string(self):
        """Test sanitizing slip from string."""
        result = _sanitize_slip("0.5")
        
        assert result == 0.5

    def test_sanitize_slip_with_invalid_string(self):
        """Test sanitizing slip from invalid string."""
        result = _sanitize_slip("invalid")
        
        assert result == 0.0

    def test_sanitize_slip_with_none(self):
        """Test sanitizing slip from None."""
        result = _sanitize_slip(None)
        
        assert result == 0.0

    def test_sanitize_slip_infinity(self):
        """Test sanitizing infinite slip."""
        import math
        result = _sanitize_slip(float('inf'))
        
        assert result == 0.0

    def test_sanitize_slip_negative_infinity(self):
        """Test sanitizing negative infinite slip."""
        import math
        result = _sanitize_slip(float('-inf'))
        
        assert result == 0.0

    def test_sanitize_slip_nan(self):
        """Test sanitizing NaN slip."""
        import math
        result = _sanitize_slip(float('nan'))
        
        assert result == 0.0


class TestAnalysisResult:
    """Test AnalysisResult dataclass."""

    def test_analysis_result_creation(self):
        """Test creating AnalysisResult."""
        result = AnalysisResult(
            html_path="/path/to/report.html",
            ai_prompt_path="/path/to/prompt.txt",
            laps_detected=5,
            best_lap_time=83.456,
            track_name="spa_francorchamps"
        )
        
        assert result.html_path == "/path/to/report.html"
        assert result.ai_prompt_path == "/path/to/prompt.txt"
        assert result.laps_detected == 5
        assert result.best_lap_time == 83.456
        assert result.track_name == "spa_francorchamps"

    def test_analysis_result_with_none_track(self):
        """Test AnalysisResult with None track name."""
        result = AnalysisResult(
            html_path="/path/to/report.html",
            ai_prompt_path="/path/to/prompt.txt",
            laps_detected=3,
            best_lap_time=90.123,
            track_name=None
        )
        
        assert result.track_name is None


class TestFrameDataEdgeCases:
    """Test frame data edge cases for analyzer."""

    def test_frame_with_missing_physics(self):
        """Test frame with missing physics data."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics=None
        )
        
        from src.core.telemetry_analyzer import get_physics
        physics = get_physics(frame)
        
        assert physics is None

    def test_frame_with_empty_physics(self):
        """Test frame with empty physics dict."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={}
        )
        
        from src.core.telemetry_analyzer import get_physics
        physics = get_physics(frame)
        
        assert physics == {}


class TestCaptureMetadata:
    """Test CaptureMetadata usage in analyzer."""

    def test_capture_metadata_fields(self):
        """Test CaptureMetadata has expected fields."""
        metadata = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acpmf_physics"},
            region_sizes={"physics": 1024}
        )
        
        assert metadata.captured_at == "2024-01-01T00:00:00Z"
        assert metadata.hz == 10.0
        assert metadata.regions_found == ["physics"]
