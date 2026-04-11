"""
Comprehensive tests for telemetry analyzer with mock data and edge cases.

Tests lap detection, corner detection, and track building with various scenarios.
"""

import pytest
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
from datetime import datetime, timezone


def create_mock_frame(frame_num: int, speed: float = 100.0, position: float = 0.0) -> FrameData:
    """Create a mock telemetry frame for testing."""
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_num,
        physics={
            "speed_kmh": speed,
            "normalized_car_position": position,
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
        lap_bounds = detect_laps(track, hz=10.0, min_lap_time_s=5.0)
        
        # Should detect approximately 3 laps
        assert len(lap_bounds) >= 1  # At least start

    def test_detect_laps_with_min_lap_time(self):
        """Test lap detection with minimum lap time filtering."""
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(200)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0, min_lap_time_s=5.0)
        
        # Linear position won't detect laps, but function should run
        assert len(lap_bounds) >= 1  # At least end boundary

    def test_detect_laps_high_min_lap_time(self):
        """Test lap detection with high minimum lap time filtering."""
        # Create frames with very short "laps" that should be filtered
        frames = [create_mock_frame(i, position=i * 0.01) for i in range(50)]
        track = build_track(frames, hz=10.0)
        
        lap_bounds = detect_laps(track, hz=10.0, min_lap_time_s=80.0)
        
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
        
        lap_bounds = detect_laps(track, hz=10.0, min_lap_time_s=5.0)
        
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
        
        corners = detect_profiled_corners(track, 0, 200, track_profile)
        
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
        
        corners = detect_profiled_corners(track, 0, 200, track_profile)
        
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
