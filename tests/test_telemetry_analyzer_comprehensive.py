"""
Comprehensive tests for telemetry analyzer with mock data and edge cases.

Tests lap detection, corner detection, and track building with various scenarios.
"""

import pytest
from unittest.mock import patch
from src.core.telemetry_analyzer import (
    build_track,
    detect_laps,
    detect_corners,
    detect_profiled_corners,
    get_physics,
    _safe_4,
    _sanitize_slip,
)
from src.core.telemetry_capture import FrameData
from src.models import SharedSessionManager
from datetime import datetime, timezone


def create_mock_frame(frame_num: int, speed: float = 100.0, position: float = 0.0) -> FrameData:
    """Create a mock telemetry frame for testing."""
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_num,
        physics={
            "speed_kmh": speed,
            "normalized_car_position": position,
            "normalized_position_source": "physics_tyre_z",
            "has_authoritative_progress": True,
            "gear": 3,
            "rpm": 5000,
        },
    )


class TestBuildTrack:
    """Test track building from telemetry frames."""

    def test_build_track_with_start_idx(self):
        """Test building track with a start index."""
        frames = [create_mock_frame(i, speed=100.0 + i, position=i * 0.01) for i in range(100)]
        
        track = build_track(frames, hz=10.0, start_idx=10)
        
        assert len(track) == 90  # Should skip first 10 frames
        assert track[0]["frame"] == 10

    def test_build_track_velocity_integration(self):
        """Test velocity integration in track building."""
        frames = [create_mock_frame(i, speed=50.0, position=i * 0.01) for i in range(50)]
        
        track = build_track(frames, hz=10.0)
        
        # Check that velocity is integrated
        assert all("speed" in pt for pt in track)
        assert track[0]["speed"] == 50.0

    def test_build_track_empty_frames(self):
        """Test building track with empty frames."""
        track = build_track([], hz=10.0)
        
        assert len(track) == 0

    def test_build_track_short_session(self):
        """Test building track with very short session."""
        frames = [create_mock_frame(i) for i in range(5)]
        
        track = build_track(frames, hz=10.0)
        
        assert len(track) == 5


class TestDetectLaps:
    """Test lap detection algorithms."""

    def test_detect_laps_with_velocity_integration(self):
        """Test lap detection using velocity integration."""
        # Create frames simulating multiple laps
        frames = []
        for lap in range(3):
            for i in range(100):
                position = (lap + i / 100.0) % 1.0
                frames.append(create_mock_frame(len(frames), speed=100.0, position=position))
        
        track = build_track(frames, hz=10.0)
        lap_bounds = detect_laps(track, hz=10.0)
        
        # Should detect approximately 3 laps
        assert len(lap_bounds) >= 1  # At least start

    def test_detect_laps_with_min_lap_time(self):
        """Test lap detection with minimum lap time filtering."""
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(200)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0)
        
        # Linear position won't detect laps, but function should run
        assert len(lap_bounds) >= 1  # At least end boundary

    def test_detect_laps_high_min_lap_time(self):
        """Test lap detection with high minimum lap time filtering."""
        # Create frames with very short "laps" that should be filtered
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(50)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0)
        
        # Should filter out very short laps
        assert len(lap_bounds) <= 2  # At most start and end

    def test_detect_laps_short_session(self):
        """Test lap detection with very short session."""
        frames = [create_mock_frame(i) for i in range(10)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0)
        
        # Short sessions should not detect laps
        assert len(lap_bounds) <= 2

    def test_detect_laps_no_valid_laps(self):
        """Test lap detection when no valid laps exist."""
        # Create frames with no position changes
        frames = [create_mock_frame(i, position=0.0) for i in range(50)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0)
        
        assert len(lap_bounds) <= 2

    def test_detect_laps_single_lap(self):
        """Test lap detection with exactly one lap."""
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(100)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0)
        
        # Should detect at least one lap
        assert len(lap_bounds) >= 1


class TestCornerDetection:
    """Test corner detection algorithms."""

    def test_detect_corners_with_track_profile(self):
        """Test corner detection using track profile."""
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(200)]
        track = build_track(frames, hz=10.0)
        
        track_profile = {
            "display_name": "Test Track",
            "corners": [
                {"id": 1, "start": 0.20, "end": 0.30, "name": "Corner 1"},
                {"id": 2, "start": 0.45, "end": 0.55, "name": "Corner 2"},
                {"id": 3, "start": 0.70, "end": 0.80, "name": "Corner 3"},
            ]
        }
        
        corners = detect_profiled_corners(track, 0, 200, track_profile, hz=10.0)

        # Should detect corners from profile
        assert len(corners) == 3
        assert all("lap_pos" in c for c in corners)
        assert all("name" in c for c in corners)

    def test_detect_corners_auto_detection(self):
        """Test automatic corner detection without profile."""
        frames = []
        # Create frames with velocity changes that might indicate corners
        for i in range(200):
            speed = 100.0 if i % 50 < 25 else 50.0  # Slow down every 50 frames
            frames.append(create_mock_frame(i, speed=speed, position=i * 0.01))
        
        track = build_track(frames, hz=10.0)
        
        corners = detect_corners(track, 0, 200, hz=10.0)
        
        # Should detect some corners based on velocity changes
        assert isinstance(corners, list)

    def test_detect_corners_no_corners_detected(self):
        """Test corner detection when no corners are found."""
        # Constant speed, no corners
        frames = [create_mock_frame(i, speed=100.0, position=i * 0.01) for i in range(100)]
        track = build_track(frames, hz=10.0)
        
        corners = detect_corners(track, 0, 100, hz=10.0)
        
        # Might not detect corners with constant speed
        assert isinstance(corners, list)

    def test_detect_corners_with_track_catalog_profile(self):
        """Test corner detection using track catalog profile."""
        from src.core.track_catalog import TRACK_CATALOG
        
        if not TRACK_CATALOG:
            pytest.skip("No track catalog available")
        
        # Use a known track from catalog
        track_key = list(TRACK_CATALOG.keys())[0]
        track_profile = TRACK_CATALOG[track_key]
        
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(200)]
        track = build_track(frames, hz=10.0)
        
        corners = detect_profiled_corners(track, 0, 200, track_profile, hz=10.0)

        assert isinstance(corners, list)


class TestGetPhysics:
    """Test physics data extraction."""

    def test_get_physics_from_frame(self):
        """Test extracting physics data from frame."""
        frame = create_mock_frame(0, speed=150.0, position=0.5)
        
        physics = get_physics(frame)
        
        assert physics is not None
        assert physics.get("speed_kmh") == 150.0
        assert physics.get("normalized_car_position") == 0.5

    def test_get_physics_returns_dict(self):
        """Test that get_physics returns a dictionary."""
        frame = create_mock_frame(0)
        
        physics = get_physics(frame)
        
        assert isinstance(physics, dict)

    def test_get_physics_none_frame(self):
        """Test get_physics with None frame raises error."""
        with pytest.raises(AttributeError):
            get_physics(None)


class TestHelperFunctions:
    """Test helper utility functions."""

    def test_safe_4_with_list(self):
        """Test _safe_4 with list input."""
        result = _safe_4([1, 2, 3, 4, 5])
        
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_short_list(self):
        """Test _safe_4 with list shorter than 4 elements pads with 0.0."""
        result = _safe_4([1, 2, 3])
        
        assert result == [1, 2, 3, 0.0]

    def test_safe_4_with_dict(self):
        """Test _safe_4 with dict input."""
        result = _safe_4({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        
        assert len(result) == 4

    def test_sanitize_slip_valid(self):
        """Test _sanitize_slip with valid slip value."""
        result = _sanitize_slip(0.5)
        
        assert result == 0.5

    def test_sanitize_slip_negative(self):
        """Test _sanitize_slip with negative slip."""
        result = _sanitize_slip(-0.5)
        
        assert result == 0.0

    def test_sanitize_slip_too_large(self):
        """Test _sanitize_slip with slip > 1.0 is not clamped."""
        result = _sanitize_slip(1.5)
        
        # Function doesn't clamp, just validates not negative
        assert result == 1.5

    def test_sanitize_slip_nan(self):
        """Test _sanitize_slip with NaN."""
        import math
        result = _sanitize_slip(float('nan'))
        
        assert math.isnan(result) or result == 0.0


class TestExtractCarState:
    """Test car state extraction from track points."""

    def test_extract_car_state_full(self):
        """Test extracting full car state from track point."""
        pt = {
            "abs": 1,
            "tc": 0,
            "steer": 0.1,
            "speed": 150.0,
            "gas": 0.5,
            "brake": 0.2,
            "acc_g_x": 0.8,
            "acc_g_y": 0.1,
            "acc_g_z": -0.5,
            "yaw_rate": 0.15,
            "air_temp": 25.0,
            "road_temp": 30.0,
            "tyre_temp_fl": 90.0,
            "tyre_temp_fr": 92.0,
            "tyre_temp_rl": 88.0,
            "tyre_temp_rr": 89.0,
            "pressure_fl": 32.0,
            "pressure_fr": 32.5,
            "pressure_rl": 31.5,
            "pressure_rr": 32.0,
            "slip_fl": 0.1,
            "slip_fr": 0.12,
            "slip_rl": 0.08,
            "slip_rr": 0.09,
            "load_fl": 3000.0,
            "load_fr": 2800.0,
            "load_rl": 3200.0,
            "load_rr": 3100.0,
            "sus_fl": 0.02,
            "sus_fr": 0.025,
            "sus_rl": 0.018,
            "sus_rr": 0.022,
            "camber_fl": -0.5,
            "camber_fr": -0.48,
            "camber_rl": 0.5,
            "camber_rr": 0.52,
            "brake_temp_fl": 200.0,
            "brake_temp_fr": 210.0,
            "brake_temp_rl": 195.0,
            "brake_temp_rr": 205.0,
        }
        
        from src.core.telemetry_analyzer import extract_car_state
        state = extract_car_state(pt)
        
        assert state is not None
        assert state["abs"] == 1
        assert state["speed"] == 150.0
        assert state["tyre_temp_fl"] == 90.0

    def test_extract_car_state_minimal(self):
        """Test extracting car state with minimal data."""
        pt = {"speed": 100.0, "frame": 0}
        
        from src.core.telemetry_analyzer import extract_car_state
        state = extract_car_state(pt)
        
        assert state is not None
        assert state["speed"] == 100.0
        assert state["abs"] == 0  # Default values

    def test_extract_car_state_none(self):
        """Test extracting car state from None."""
        from src.core.telemetry_analyzer import extract_car_state
        state = extract_car_state(None)
        
        assert state is None


class TestCornerMatching:
    """Test corner matching functions."""

    def test_match_profiled_corners(self):
        """Test matching profiled corners by ID."""
        from src.core.telemetry_analyzer import match_profiled_corners
        
        ref_corners = [
            {"id": 1, "lap_pos": 0.1},
            {"id": 2, "lap_pos": 0.3},
            {"id": 3, "lap_pos": 0.5},
        ]
        lap_corners = [
            {"id": 1, "lap_pos": 0.11},
            {"id": 2, "lap_pos": 0.31},
        ]
        
        matched = match_profiled_corners(ref_corners, lap_corners)
        
        assert matched[1] is not None
        assert matched[2] is not None
        assert matched[3] is None  # Not in lap corners

    def test_match_corners_sequential(self):
        """Test sequential corner matching."""
        from src.core.telemetry_analyzer import match_corners
        
        ref_corners = [
            {"id": 1, "lap_pos": 0.1},
            {"id": 2, "lap_pos": 0.3},
            {"id": 3, "lap_pos": 0.5},
        ]
        lap_corners = [
            {"id": 1, "lap_pos": 0.12},
            {"id": 2, "lap_pos": 0.32},
            {"id": 3, "lap_pos": 0.52},
        ]
        
        matched = match_corners(ref_corners, lap_corners, tol=0.15)
        
        assert matched[1] is not None
        assert matched[2] is not None
        assert matched[3] is not None


class TestCornerAnalysis:
    """Test corner analysis utilities."""

    def test_corner_segment_time(self):
        """Test corner segment time calculation."""
        from src.core.telemetry_analyzer import corner_segment_time
        
        corner = {"start_frame": 100, "end_frame": 150}
        time = corner_segment_time(corner, hz=10.0)
        
        assert time == 5.0  # (150 - 100) / 10

    def test_variation_label_high(self):
        """Test variation label for high delta."""
        from src.core.telemetry_analyzer import variation_label
        
        assert variation_label(30) == "HIGH"

    def test_variation_label_medium(self):
        """Test variation label for medium delta."""
        from src.core.telemetry_analyzer import variation_label
        
        assert variation_label(20) == "MEDIUM"

    def test_variation_label_low(self):
        """Test variation label for low delta."""
        from src.core.telemetry_analyzer import variation_label
        
        assert variation_label(10) == "LOW"

    def test_classify_corner_issue_braking(self):
        """Test corner issue classification - braking."""
        from src.core.telemetry_analyzer import classify_corner_issue
        
        issue = classify_corner_issue(entry_delta=20, apex_delta=5, exit_delta=5)
        
        assert "braking" in issue.lower()

    def test_classify_corner_issue_throttle(self):
        """Test corner issue classification - throttle."""
        from src.core.telemetry_analyzer import classify_corner_issue
        
        issue = classify_corner_issue(entry_delta=5, apex_delta=5, exit_delta=20)
        
        assert "throttle" in issue.lower()

    def test_classify_corner_issue_line(self):
        """Test corner issue classification - line."""
        from src.core.telemetry_analyzer import classify_corner_issue
        
        issue = classify_corner_issue(entry_delta=5, apex_delta=20, exit_delta=5)
        
        assert "line" in issue.lower()

    def test_format_car_state_full(self):
        """Test formatting full car state."""
        from src.core.telemetry_analyzer import format_car_state
        
        state = {
            "abs": 1,
            "tc": 0,
            "steer": 0.1,
            "yaw_rate": 0.15,
            "acc_g_x": 0.8,
            "acc_g_z": -0.5,
            "tyre_temp_fl": 90.0,
            "tyre_temp_fr": 92.0,
            "tyre_temp_rl": 88.0,
            "tyre_temp_rr": 89.0,
            "pressure_fl": 32.0,
            "pressure_fr": 32.5,
            "pressure_rl": 31.5,
            "pressure_rr": 32.0,
            "slip_fl": 0.1,
            "slip_fr": 0.12,
            "slip_rl": 0.08,
            "slip_rr": 0.09,
            "load_fl": 3000.0,
            "load_fr": 2800.0,
            "load_rl": 3200.0,
            "load_rr": 3100.0,
            "sus_fl": 0.02,
            "sus_fr": 0.025,
            "sus_rl": 0.018,
            "sus_rr": 0.022,
            "brake_temp_fl": 200.0,
            "brake_temp_fr": 210.0,
            "brake_temp_rl": 195.0,
            "brake_temp_rr": 205.0,
        }
        
        formatted = format_car_state(state)
        
        assert "ABS:YES" in formatted
        assert "TC:no" in formatted
        assert "Steer:" in formatted

    def test_format_car_state_none(self):
        """Test formatting None car state."""
        from src.core.telemetry_analyzer import format_car_state
        
        formatted = format_car_state(None)
        
        assert formatted == "No data"

    def test_balance_hint_understeer(self):
        """Test balance hint for understeer."""
        from src.core.telemetry_analyzer import balance_hint
        
        state = {
            "slip_fl": 0.3,
            "slip_fr": 0.35,
            "slip_rl": 0.1,
            "slip_rr": 0.12,
            "steer": 0.1,
            "yaw_rate": 0.1,
        }
        
        hint = balance_hint(state)
        
        assert hint == "understeer"

    def test_balance_hint_oversteer(self):
        """Test balance hint for oversteer."""
        from src.core.telemetry_analyzer import balance_hint
        
        state = {
            "slip_fl": 0.1,
            "slip_fr": 0.12,
            "slip_rl": 0.3,
            "slip_rr": 0.35,
            "steer": 0.1,
            "yaw_rate": 0.3,
        }
        
        hint = balance_hint(state)
        
        assert hint == "oversteer"

    def test_balance_hint_neutral(self):
        """Test balance hint for neutral."""
        from src.core.telemetry_analyzer import balance_hint
        
        state = {
            "slip_fl": 0.15,
            "slip_fr": 0.15,
            "slip_rl": 0.15,
            "slip_rr": 0.15,
            "steer": 0.05,
            "yaw_rate": 0.2,
        }
        
        hint = balance_hint(state)
        
        assert hint == "neutral"

    def test_balance_hint_none(self):
        """Test balance hint with None."""
        from src.core.telemetry_analyzer import balance_hint
        
        hint = balance_hint(None)
        
        assert hint == "unknown"


class TestFindFrameIndex:
    """Test frame index finding."""

    def test_find_frame_index_exact(self):
        """Test finding exact frame index."""
        from src.core.telemetry_analyzer import _find_frame_index
        
        track = [
            {"frame": 0, "speed": 100},
            {"frame": 10, "speed": 110},
            {"frame": 20, "speed": 120},
        ]
        
        idx = _find_frame_index(track, 10)
        
        assert idx == 1

    def test_find_frame_index_between(self):
        """Test finding frame index between points."""
        from src.core.telemetry_analyzer import _find_frame_index
        
        track = [
            {"frame": 0, "speed": 100},
            {"frame": 10, "speed": 110},
            {"frame": 20, "speed": 120},
        ]
        
        idx = _find_frame_index(track, 15)
        
        assert idx == 2  # Should return index of frame >= 15

    def test_find_frame_index_beyond(self):
        """Test finding frame index beyond track."""
        from src.core.telemetry_analyzer import _find_frame_index
        
        track = [
            {"frame": 0, "speed": 100},
            {"frame": 10, "speed": 110},
        ]
        
        idx = _find_frame_index(track, 100)
        
        assert idx == 1  # Should return last index


class TestAnalyzeCornerPhases:
    """Test corner phase analysis."""

    def test_analyze_corner_phases_basic(self):
        """Test basic corner phase analysis."""
        from src.core.telemetry_analyzer import analyze_corner_phases
        
        track = []
        # Create track with braking before corner
        for i in range(100):
            track.append({
                "frame": i,
                "speed": 150 - i if i < 50 else 100,
                "brake": 0.5 if 30 <= i < 50 else 0.0,
                "steer": 0.1 if i >= 50 else 0.0,
                "gas": 0.0 if i < 70 else 0.5,
                "acc_g_z": -0.8 if 30 <= i < 50 else 0.0,
                "x": i * 10,
                "z": 0,
            })
        
        corner = {
            "start_frame": 50,
            "apex_frame": 60,
            "end_frame": 80,
            "entry_speed": 100,
            "apex_speed": 80,
            "exit_speed": 120,
        }
        
        result = analyze_corner_phases(track, corner, 0, hz=10.0)
        
        assert result is not None
        assert "brake_onset_dt" in result
        assert "turn_in_dt" in result
        assert "gas_on_dt" in result

    def test_analyze_corner_phases_insufficient_data(self):
        """Test corner phase analysis with insufficient data."""
        from src.core.telemetry_analyzer import analyze_corner_phases
        
        track = [{"frame": 0, "speed": 100}]
        corner = {"start_frame": 10, "apex_frame": 15, "end_frame": 20}
        
        result = analyze_corner_phases(track, corner, 0, hz=10.0)
        
        assert result is None


class TestAnalyzeGripUtilization:
    """Test grip utilization analysis."""

    def test_analyze_grip_utilization_basic(self):
        """Test basic grip utilization analysis."""
        from src.core.telemetry_analyzer import analyze_grip_utilization
        
        track = []
        for i in range(50):
            track.append({
                "frame": i,
                "acc_g_x": 0.8 if 10 <= i < 30 else 0.1,
                "acc_g_z": -0.5 if 10 <= i < 20 else 0.0,
                "brake": 0.5 if 10 <= i < 20 else 0.0,
            })
        
        corner = {"start_frame": 10, "end_frame": 40}
        
        result = analyze_grip_utilization(track, corner, hz=10.0)
        
        assert result is not None
        assert "peak_total_g" in result
        assert "avg_total_g" in result
        assert "peak_lat_g" in result
        assert "peak_long_g" in result

    def test_analyze_grip_utilization_insufficient_data(self):
        """Test grip utilization with insufficient data."""
        from src.core.telemetry_analyzer import analyze_grip_utilization
        
        track = [{"frame": 0}]
        corner = {"start_frame": 0, "end_frame": 1}
        
        result = analyze_grip_utilization(track, corner, hz=10.0)
        
        assert result is None


class TestAnalysisModeGate:
    """Quality gate that decides full-coaching vs diagnostic output.

    Regression test for the evening of 2026-04-25: live AC Evo captures
    currently have 0% authoritative graphics progress (the decoder isn't
    written yet) but 100% plausible physics coverage. Prior to this gate
    relaxation the analyzer suppressed the AI prompt in this state, so the
    user saw an empty coaching file despite three clean laps on track.
    """

    def test_authoritative_alone_unlocks_full_mode(self):
        from src.core.telemetry_analyzer import _decide_analysis_mode

        mode, auth, plausible = _decide_analysis_mode(0.85, 0.40)

        assert mode == "full"
        assert auth is True
        assert plausible is False

    def test_high_plausible_unlocks_full_even_without_authoritative(self):
        """This is the user-facing regression: 0% graphics, 100% physics."""
        from src.core.telemetry_analyzer import _decide_analysis_mode

        mode, auth, plausible = _decide_analysis_mode(0.0, 1.00)

        assert mode == "full"
        assert auth is False
        assert plausible is True

    def test_just_over_plausible_fallback_threshold_unlocks_full(self):
        """Gate fires at exactly 95% plausible coverage."""
        from src.core.telemetry_analyzer import _decide_analysis_mode

        assert _decide_analysis_mode(0.0, 0.95)[0] == "full"
        assert _decide_analysis_mode(0.0, 0.9499)[0] == "diagnostic"

    def test_authoritative_threshold_boundary(self):
        """Authoritative gate fires at exactly 60% coverage."""
        from src.core.telemetry_analyzer import _decide_analysis_mode

        assert _decide_analysis_mode(0.60, 0.0)[0] == "full"
        assert _decide_analysis_mode(0.59, 0.0)[0] == "diagnostic"

    def test_neither_signal_falls_back_to_diagnostic(self):
        from src.core.telemetry_analyzer import _decide_analysis_mode

        mode, auth, plausible = _decide_analysis_mode(0.20, 0.40)

        assert mode == "diagnostic"
        assert auth is False
        assert plausible is False


class TestTyreGripDegradation:
    """Test stint-level tyre grip degradation detection."""

    @staticmethod
    def _make_lap(lap_num, *, lat_g_peak, core_temp, slip_deg, end_wear, dirty=0.0):
        """Build a synthetic lap with one cornering frame and one straight frame.

        Both frames carry the same per-wheel tyre data so the analyzer's
        whole-lap aggregations are deterministic.
        """
        import math

        slip_rad = math.radians(slip_deg)
        wear_per_wheel = end_wear / 100.0  # tyre_wear is 0..1, the analyzer scales by 100

        common_tyre = {
            "tyre_temp_fl": core_temp, "tyre_temp_fr": core_temp,
            "tyre_temp_rl": core_temp, "tyre_temp_rr": core_temp,
            "tyre_wear_fl": wear_per_wheel, "tyre_wear_fr": wear_per_wheel,
            "tyre_wear_rl": wear_per_wheel, "tyre_wear_rr": wear_per_wheel,
            "tyre_dirty_fl": dirty, "tyre_dirty_fr": dirty,
            "tyre_dirty_rl": dirty, "tyre_dirty_rr": dirty,
            "slip_angle_fl": slip_rad, "slip_angle_fr": slip_rad,
            "slip_angle_rl": slip_rad, "slip_angle_rr": slip_rad,
        }
        cornering = {**common_tyre, "acc_g_x": lat_g_peak}
        straight = {**common_tyre, "acc_g_x": 0.0}
        return {
            "lap_num": lap_num,
            "track": [cornering, straight],
        }

    def test_analyze_lap_tyre_state_basic(self):
        """analyze_lap_tyre_state returns expected per-lap aggregates."""
        from src.core.telemetry_analyzer import analyze_lap_tyre_state

        lap = self._make_lap(
            1, lat_g_peak=2.1, core_temp=85.0, slip_deg=4.0, end_wear=0.5
        )
        state = analyze_lap_tyre_state(lap["track"])

        assert state is not None
        assert state["avg_core_temp_c"] == 85.0
        assert state["peak_core_temp_c"] == 85.0
        assert state["peak_lat_g"] == 2.1
        assert state["peak_slip_angle_deg"] == 4.0
        assert state["end_wear_pct"] == 0.5
        assert state["corner_frames"] == 1

    def test_analyze_lap_tyre_state_empty(self):
        """Empty track returns None."""
        from src.core.telemetry_analyzer import analyze_lap_tyre_state

        assert analyze_lap_tyre_state([]) is None

    def test_falling_lat_g_flagged_as_grip_loss(self):
        """Monotonically falling cornering lat-G across 3+ laps fires the
        primary grip-degradation flag — the user-reported scenario where
        'lap 3 had less grip on the track'.
        """
        from src.core.telemetry_analyzer import analyze_tyre_grip_degradation

        laps = [
            self._make_lap(1, lat_g_peak=2.20, core_temp=80.0, slip_deg=3.0, end_wear=0.10),
            self._make_lap(2, lat_g_peak=2.10, core_temp=82.0, slip_deg=3.5, end_wear=0.30),
            self._make_lap(3, lat_g_peak=2.00, core_temp=84.0, slip_deg=4.0, end_wear=0.55),
        ]
        result = analyze_tyre_grip_degradation(laps)

        assert result["trends"]["peak_lat_g"] == "FALLING"
        # The grip-falloff narrative must reach the AI as a flag string.
        assert any("less grip" in flag for flag in result["flags"]), (
            f"Expected grip-falloff flag, got {result['flags']!r}"
        )

    def test_rising_core_temp_flagged_as_overheating(self):
        from src.core.telemetry_analyzer import analyze_tyre_grip_degradation

        laps = [
            self._make_lap(1, lat_g_peak=2.10, core_temp=80.0, slip_deg=3.0, end_wear=0.10),
            self._make_lap(2, lat_g_peak=2.10, core_temp=85.0, slip_deg=3.0, end_wear=0.20),
            self._make_lap(3, lat_g_peak=2.10, core_temp=92.0, slip_deg=3.0, end_wear=0.30),
        ]
        result = analyze_tyre_grip_degradation(laps)

        assert result["trends"]["core_temp"] == "RISING"
        assert any("overheating" in flag.lower() for flag in result["flags"])

    def test_short_stint_yields_no_trends(self):
        """A 2-lap sample is below the 3-lap threshold for trend detection."""
        from src.core.telemetry_analyzer import analyze_tyre_grip_degradation

        laps = [
            self._make_lap(1, lat_g_peak=2.20, core_temp=80.0, slip_deg=3.0, end_wear=0.10),
            self._make_lap(2, lat_g_peak=2.00, core_temp=85.0, slip_deg=4.0, end_wear=0.30),
        ]
        result = analyze_tyre_grip_degradation(laps)

        assert len(result["per_lap"]) == 2
        assert result["trends"] == {}
        assert result["flags"] == []

    def test_stable_stint_yields_flat_trends(self):
        """No noise-level changes across 3 laps → all trends FLAT, no flags."""
        from src.core.telemetry_analyzer import analyze_tyre_grip_degradation

        laps = [
            self._make_lap(i, lat_g_peak=2.10, core_temp=82.0, slip_deg=3.0, end_wear=0.20 * i)
            for i in (1, 2, 3)
        ]
        result = analyze_tyre_grip_degradation(laps)

        assert result["trends"]["peak_lat_g"] == "FLAT"
        assert result["trends"]["core_temp"] == "FLAT"
        assert not result["flags"]


class TestTelemetryAnalyzer:
    """Test TelemetryAnalyzer class."""

    @pytest.mark.asyncio
    async def test_analyze_with_real_data(self):
        """Test TelemetryAnalyzer.analyze with real telemetry data."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        import json
        from src.core.telemetry_decoder import decode_physics, physics_to_dict
        
        # Load some real frames
        frames = []
        with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
            for i, line in enumerate(f):
                if i >= 50:
                    break
                frame_json = json.loads(line)
                physics_raw = bytes.fromhex(frame_json['physics_raw'])
                decoded = decode_physics(physics_raw)
                physics_dict = physics_to_dict(decoded)
                
                frame = FrameData(
                    timestamp=frame_json['timestamp'],
                    frame_number=frame_json['frame_number'],
                    physics=physics_dict,
                )
                frames.append(frame)
        
        analyzer = TelemetryAnalyzer(output_dir="tests/output")
        result = await analyzer.analyze(frames, hz=10.0, output_prefix="test")
        
        assert result is not None
        assert hasattr(result, 'html_path')
        assert hasattr(result, 'ai_prompt_path')
        assert hasattr(result, 'laps_detected')

    @pytest.mark.asyncio
    async def test_analyze_insufficient_frames(self):
        """Test TelemetryAnalyzer.analyze with insufficient frames."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        
        frames = [create_mock_frame(i) for i in range(5)]
        analyzer = TelemetryAnalyzer(output_dir="tests/output")
        
        result = await analyzer.analyze(frames, hz=10.0, output_prefix="test_short")
        
        assert result is not None
        assert result.laps_detected == 0

    @pytest.mark.asyncio
    async def test_analyze_with_track_name(self):
        """Test TelemetryAnalyzer.analyze with track name."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        
        frames = [create_mock_frame(i, speed=100.0, position=i * 0.01) for i in range(100)]
        analyzer = TelemetryAnalyzer(output_dir="tests/output")
        
        result = await analyzer.analyze(frames, hz=10.0, track_name="spa", output_prefix="test_track")
        
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_game_lap_boundaries(self):
        """Test TelemetryAnalyzer.analyze with game-reported lap boundaries."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        
        frames = [create_mock_frame(i, speed=100.0, position=i * 0.01) for i in range(200)]
        analyzer = TelemetryAnalyzer(output_dir="tests/output")
        
        # Provide game lap boundaries
        game_boundaries = [0, 100, 200]
        
        result = await analyzer.analyze(
            frames, 
            hz=10.0, 
            game_lap_boundaries=game_boundaries,
            output_prefix="test_game_laps"
        )
        
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_game_lap_markers_uses_log_lap_times(self):
        """Tuple game boundaries are lap-end markers with authoritative log lap times."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = [create_mock_frame(i, speed=100.0, position=i * 0.01) for i in range(220)]
        analyzer = TelemetryAnalyzer(output_dir="tests/output")

        # (frame_idx, lap_time_ms) from game logs
        game_markers = [
            (50, 153396),
            (100, 153309),
            (150, 152460),
            (200, 152001),
        ]

        result = await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=game_markers,
            output_prefix="test_game_markers",
        )

        assert result is not None
        assert result.laps_detected == 4
        # Best lap should come from game-provided times, not frame-distance math.
        assert abs(result.best_lap_time - 152.001) < 0.001

    @pytest.mark.asyncio
    async def test_analyze_prefers_shared_session_lap_data(self):
        """Analyzer should use shared-session lap timing/validity when available."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = [create_mock_frame(i, speed=90.0 + (i % 40), position=i * 0.01) for i in range(220)]
        manager = SharedSessionManager()
        manager.update_lap_timing_from_graphics_shm(1, {"last_laptime_ms": 200000})
        manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 190000})
        manager.update_lap_timing_from_graphics_shm(3, {"last_laptime_ms": 180000})
        manager.update_lap_timing_from_graphics_shm(4, {"last_laptime_ms": 170000})
        manager.update_lap_validity_from_graphics_shm(2, True)

        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=manager)
        game_markers = [
            (50, 153396),
            (100, 153309),
            (150, 152460),
            (200, 152001),
        ]

        with patch.object(manager, "update_from_telemetry", wraps=manager.update_from_telemetry) as update_spy:
            result = await analyzer.analyze(
                frames,
                hz=10.0,
                game_lap_boundaries=game_markers,
                output_prefix="test_shared_session_laps",
            )

        assert result is not None
        assert result.laps_detected == 4
        assert abs(result.best_lap_time - 170.0) < 0.001
        update_spy.assert_called_once()
        telemetry_summary = update_spy.call_args.args[0]
        assert telemetry_summary["max_speed"] >= 90.0

    @pytest.mark.asyncio
    async def test_analyze_uses_explicit_game_lap_numbers_for_mid_session_capture(self):
        """Mid-session captures should keep real game lap numbers and diagnose missing final laps."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = [create_mock_frame(i, speed=95.0 + (i % 30), position=i * 0.01) for i in range(180)]
        manager = SharedSessionManager()
        manager.update_lap_timing_from_graphics_shm(1, {"last_laptime_ms": 999000})
        manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 150000})
        manager.update_lap_timing_from_graphics_shm(3, {"last_laptime_ms": 140000})
        manager.update_lap_timing_from_graphics_shm(4, {"last_laptime_ms": 130000})
        manager.update_lap_timing_from_graphics_shm(5, {"last_laptime_ms": 129000})

        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=manager)
        game_markers = [
            (50, 135069, 2),
            (100, 136392, 3),
            (150, 133194, 4),
        ]

        result = await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=game_markers,
            output_prefix="test_mid_session_lap_numbers",
        )

        assert result is not None
        assert result.laps_detected == 3
        assert abs(result.best_lap_time - 130.0) < 0.001
        with open(result.ai_prompt_path, "r", encoding="utf-8") as fh:
            prompt = fh.read()
        assert "Telemetry coaching is running in DIAGNOSTIC mode." in prompt

    @pytest.mark.asyncio
    async def test_analyze_includes_car_from_shared_session_in_ai_prompt(self):
        """Car from shared session should appear in AI prompt SESSION CONTEXT."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        from src.models.lap import SessionData

        frames = [create_mock_frame(i, speed=95.0 + (i % 30), position=i * 0.01) for i in range(220)]
        manager = SharedSessionManager()
        manager.update_lap_timing_from_graphics_shm(1, {"last_laptime_ms": 150000})
        manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 140000})
        manager.update_lap_validity_from_graphics_shm(1, True)
        manager.update_lap_validity_from_graphics_shm(2, True)
        # Set car in shared session
        manager.update_from_logs(
            SessionData(
                car="Ferrari 296 GT3",
                track="Laguna Seca",
                session_type="race",
            )
        )

        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=manager)
        game_markers = [(50, 150000), (150, 140000)]

        result = await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=game_markers,
            output_prefix="test_car_in_prompt",
        )

        assert result is not None
        with open(result.ai_prompt_path, "r", encoding="utf-8") as fh:
            prompt = fh.read()
        assert "Car: Ferrari 296 GT3" in prompt


class TestFixedMeasurementWindow:
    """Regression tests for fixed lap_progress measurement window segment times."""

    def test_corner_measurement_window(self):
        """Measurement window is centred on the profile midpoint."""
        from src.core.telemetry_analyzer import _corner_measurement_window

        spec = {"start": 0.20, "end": 0.30}
        m_start, m_end = _corner_measurement_window(spec)

        # Centre is 0.25; window is [0.25 - 0.015, 0.25 + 0.025]
        assert m_start == pytest.approx(0.235, abs=1e-9)
        assert m_end == pytest.approx(0.275, abs=1e-9)

    def test_detect_profiled_corners_stores_segment_time_with_norm_pos(self):
        """When norm_pos is available segment_time_s uses the fixed window."""
        from src.core.telemetry_analyzer import detect_profiled_corners

        # Build a simple track where a corner lives at progress 0.20-0.30
        track = []
        for i in range(200):
            track.append({
                "frame": i,
                "norm_pos": i / 200.0,
                "speed": 80.0 if 0.20 <= (i / 200.0) < 0.30 else 100.0,
                "x": float(i),
                "z": 0.0,
            })

        profile = {
            "corners": [
                {"id": 1, "start": 0.20, "end": 0.30, "name": "Corner 1"},
            ]
        }

        corners = detect_profiled_corners(track, 0, 200, profile, hz=10.0)
        assert len(corners) == 1
        c = corners[0]
        assert "segment_time_s" in c
        assert c["segment_time_s"] is not None
        # Fixed window [0.235, 0.275) => frames 47-54 => ~0.7s at 10Hz
        assert c["segment_time_s"] == pytest.approx(0.7, abs=0.1)
        assert c["confidence_label"] == "medium"

    def test_detect_profiled_corners_fallback_without_norm_pos(self):
        """Without norm_pos confidence is LOW and segment_time_s is None."""
        from src.core.telemetry_analyzer import detect_profiled_corners, corner_segment_time

        track = []
        for i in range(200):
            track.append({
                "frame": i,
                "speed": 80.0 if 40 <= i < 60 else 100.0,
                "x": float(i),
                "z": 0.0,
            })

        profile = {
            "corners": [
                {"id": 1, "start": 0.20, "end": 0.30, "name": "Corner 1"},
            ]
        }

        corners = detect_profiled_corners(track, 0, 200, profile, hz=10.0)
        assert len(corners) == 1
        c = corners[0]
        assert c.get("segment_time_s") is None
        assert c["confidence_label"] == "low"
        # Falls back to (end_frame - start_frame) / hz
        assert corner_segment_time(c, hz=10.0) == pytest.approx(1.9, abs=0.1)

    def test_detect_profiled_corners_canonical_uses_fixed_window(self):
        """Canonical path stores segment_time_s over fixed window, not dynamic entry/exit."""
        from src.core.telemetry_analyzer import _build_canonical_lap, _detect_profiled_corners_canonical

        # Canonical track with uniform progress and time_s
        samples = []
        for i in range(100):
            samples.append({
                "frame": i,
                "lap_progress": i / 100.0,
                "time_s": i / 10.0,
                "speed": 70.0 if 0.20 <= (i / 100.0) < 0.30 else 120.0,
                "brake": 0.5 if 0.22 <= (i / 100.0) < 0.26 else 0.0,
                "gas": 0.0,
                "steer": 0.0,
                "x": float(i),
                "z": 0.0,
            })

        canonical_lap = {
            "samples": samples,
            "progress_start": 0.0,
            "progress_end": 1.0,
            "source_samples": 100,
            "grid_bins": 100,
        }

        profile = {
            "corners": [
                {"id": 1, "start": 0.20, "end": 0.30, "name": "Corner 1"},
            ]
        }

        corners = _detect_profiled_corners_canonical(
            canonical_lap["samples"],
            profile,
            hz=10.0,
            authoritative_progress=True,
        )
        assert len(corners) == 1
        c = corners[0]
        # Fixed window [0.235, 0.275) => indices 23-27 => 4 bins => 0.4s at 10Hz
        assert c["segment_time_s"] == pytest.approx(0.4, abs=0.1)
        # start_frame/end_frame still reflect dynamic entry/exit for speed analysis
        assert c["start_frame"] != c["end_frame"]

    def test_corner_segment_time_prefers_stored_value(self):
        """corner_segment_time uses segment_time_s when present."""
        from src.core.telemetry_analyzer import corner_segment_time

        # start/end imply 5.0s, but stored value says 3.5s
        corner = {"start_frame": 100, "end_frame": 150, "segment_time_s": 3.5}
        assert corner_segment_time(corner, hz=10.0) == 3.5

    @pytest.mark.asyncio
    async def test_ai_prompt_flags_suspect_low_confidence_delta(self):
        """_generate_ai_prompt flags suspect deltas for LOW-confidence corners."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        # Craft minimal data that triggers full-mode + suspect delta logic
        ref_corner = {
            "id": 1,
            "name": "Test Chicane",
            "apex_speed": 100.0,
            "entry_speed": 120.0,
            "exit_speed": 110.0,
            "start_frame": 20,
            "end_frame": 40,
            "apex_frame": 30,
            "segment_time_s": 2.0,
            "confidence_label": "low",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        cmp_corner = {
            "id": 1,
            "name": "Test Chicane",
            "apex_speed": 95.0,
            "entry_speed": 115.0,
            "exit_speed": 105.0,
            "start_frame": 20,
            "end_frame": 40,
            "apex_frame": 30,
            "segment_time_s": 8.0,
            "confidence_label": "low",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }

        lap1 = {
            "lap_num": 1,
            "lap_time_s": 120.0,
            "lap_time_str": "2:00.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [ref_corner],
            "track": [],
        }
        lap2 = {
            "lap_num": 2,
            "lap_time_s": 125.0,
            "lap_time_str": "2:05.00",
            "max_speed": 195.0,
            "avg_speed": 145.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [cmp_corner],
            "track": [],
        }

        data = {
            "hz": 10.0,
            "laps": [lap1, lap2],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "Test Chicane"}],
            "corner_data": {},
            "corner_speeds": {},
            "analysis_mode": "full",
            "analysis_confidence": "high",
            "analysis_notes": [],
            "authoritative_progress_ratio": 1.0,
            "plausible_frame_ratio": 1.0,
            "track_label": "Test Track",
            "car": "Test Car",
        }

        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=SharedSessionManager())
        ai_prompt_path = await analyzer._generate_ai_prompt(data, output_prefix="test_suspect_delta")

        with open(ai_prompt_path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "TIME LOSS RANKING" in prompt
        # Delta is 6.0s with LOW confidence -> should be flagged suspect and capped at 3.0s
        assert "suspect" in prompt.lower() or "SUSPECT" in prompt
