"""
Advanced tests for telemetry analyzer to improve coverage.

Tests track profile selection, AnalysisResult dataclass, frame edge cases,
and CaptureMetadata. Helper-function tests (_safe_4, _sanitize_slip)
are consolidated in ``test_telemetry_analyzer_comprehensive.py``.
"""

from src.core.telemetry_analyzer import (
    AnalysisResult,
    _read_static_track_config,
    _select_track_profile_for_analysis,
    get_physics,
)
from src.core.telemetry_capture import CaptureMetadata, FrameData


class TestSelectTrackProfile:
    """Test track profile selection for analysis."""

    def test_select_track_profile_with_name(self):
        """Selecting a known track name returns its profile."""
        result = _select_track_profile_for_analysis("spa_francorchamps")
        assert isinstance(result, tuple)
        assert len(result) == 2
        profile = result[1]
        assert profile is not None
        assert "corners" in profile
        assert len(profile["corners"]) > 0

    def test_select_track_profile_none(self):
        """Selecting with None returns (None, None)."""
        result = _select_track_profile_for_analysis(None)
        assert result == (None, None)

    def test_select_track_profile_empty_string(self):
        """Selecting with empty string returns (None, None)."""
        result = _select_track_profile_for_analysis("")
        assert result == (None, None)

    def test_select_track_profile_path_fallback(self):
        """A path-style name triggers fallback matching (finds Spa)."""
        result = _select_track_profile_for_analysis("circuit_de_spa_francorchamps gp")
        assert isinstance(result, tuple)
        assert len(result) == 2
        profile = result[1]
        # Path fallback should resolve to the spa profile
        assert profile is not None
        assert "corners" in profile

    def test_select_track_profile_by_static_config_nordschleife(self):
        """Static config "Nordschleife" selects the plain Nordschleife layout."""
        track_key, profile = _select_track_profile_for_analysis("Nurburgring", "Nordschleife")
        assert track_key == "nurburgring_nordschleife"
        assert profile["config_key"] == "nordschleife"

    def test_select_track_profile_by_static_config_gp(self):
        """Static config "GP" selects the GP layout, not the Nordschleife 24h."""
        track_key, profile = _select_track_profile_for_analysis("Nurburgring", "GP")
        assert track_key == "nurburgring_gp"
        assert profile["config_key"] == "gp"

    def test_explicit_conflicting_layout_stays_unresolved(self):
        assert _select_track_profile_for_analysis("Suzuka East", "Full") == (None, None)

    def test_read_static_track_config_extracts_names(self):
        """Static frames yield authoritative track/config names."""
        frames = [
            FrameData(
                timestamp="",
                frame_number=i,
                physics={},
                static={"track": "Nurburgring", "track_configuration": "Nordschleife"},
            )
            for i in range(3)
        ]
        assert _read_static_track_config(frames) == ("Nurburgring", "Nordschleife")


class TestAnalysisResult:
    """Test AnalysisResult dataclass."""

    def test_analysis_result_creation(self):
        """Test creating AnalysisResult."""
        result = AnalysisResult(
            html_path="/path/to/report.html",
            ai_prompt_path="/path/to/prompt.txt",
            laps_detected=5,
            best_lap_time=83.456,
            track_name="spa_francorchamps",
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
            track_name=None,
        )

        assert result.track_name is None


class TestFrameDataEdgeCases:
    """Test frame data edge cases for analyzer."""

    def test_frame_with_missing_physics(self):
        """get_physics returns None when frame has no physics data."""
        frame = FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics=None)

        physics = get_physics(frame)

        assert physics is None

    def test_frame_with_empty_physics(self):
        """get_physics returns an empty dict when frame has an empty physics dict."""
        frame = FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})

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
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024},
        )

        assert metadata.captured_at == "2024-01-01T00:00:00Z"
        assert metadata.hz == 10.0
        assert metadata.regions_found == ["physics"]
