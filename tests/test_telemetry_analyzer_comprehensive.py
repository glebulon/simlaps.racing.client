"""
Comprehensive tests for telemetry analyzer with mock data and edge cases.

Tests lap detection, corner detection, and track building with various scenarios.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.core.telemetry_analyzer import (
    _safe_4,
    _sanitize_slip,
    build_track,
    detect_corners,
    detect_laps,
    detect_profiled_corners,
    get_physics,
)
from src.core.telemetry_capture import FrameData
from src.models import SharedSessionManager


def create_mock_frame(
    frame_num: int, speed: float = 100.0, position: float = 0.0, last_lap_time_ms: int = None
) -> FrameData:
    """Create a mock telemetry frame for testing.

    Progress data is placed in ``graphics`` (the authoritative source).
    Pass ``last_lap_time_ms`` to simulate SHM timing-state lap boundaries.
    """
    graphics = {
        "completed_laps": 0,
        "current_time_ms": 0,
        "last_time_ms": 0,
        "best_time_ms": 0,
        "is_valid_lap": None,
    }
    if position is not None:
        graphics["normalized_car_position"] = position
        graphics["has_authoritative_progress"] = True
    if last_lap_time_ms is not None:
        graphics["last_time_ms"] = last_lap_time_ms
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=frame_num,
        physics={
            "speed_kmh": speed,
            "gear": 3,
            "rpm": 5000,
        },
        graphics=graphics,
    )


class TestBuildTrack:
    """Test track building from telemetry frames."""

    def test_build_track_with_start_idx(self):
        """Test building track with a start index."""
        frames = [create_mock_frame(i, speed=100.0 + i, position=i * 0.01) for i in range(100)]

        track = build_track(frames, hz=10.0, start_idx=10)

        assert len(track) == 90  # Should skip first 10 frames
        assert track[0]["frame"] == 10

    def test_build_track_with_graphics_progress(self):
        """Test that track building uses graphics-derived progress."""
        frames = [create_mock_frame(i, speed=50.0, position=i * 0.01) for i in range(50)]

        track = build_track(frames, hz=10.0)

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

    def test_build_track_dead_reckons_position_from_velocity(self):
        """Regression: ACE physics SHM has no world_position and 0.8.x does
        not populate graphics car_coordinates, so the track map rendered
        empty (all x/z = 0). Without a world position, x/z must be
        dead-reckoned from world-frame physics velocity."""
        frames = []
        for i in range(100):
            f = create_mock_frame(i, speed=36.0)  # 10 m/s
            f.physics["velocity"] = {"x": 10.0, "y": 0.0, "z": 0.0}
            frames.append(f)

        track = build_track(frames, hz=10.0)

        xs = [pt["x"] for pt in track]
        zs = [pt["z"] for pt in track]
        # 10 m/s over 10s = ~100 m along x, no z movement
        assert max(xs) - min(xs) == pytest.approx(99.0, abs=2.0)
        assert max(zs) - min(zs) == 0.0

    def test_build_track_world_position_takes_precedence_over_velocity(self):
        """A real world position must win over the velocity fallback."""
        frames = []
        for i in range(10):
            f = create_mock_frame(i, speed=36.0)
            f.physics["world_position"] = {"x": 500.0 + i, "z": -200.0}
            f.physics["velocity"] = {"x": 10.0, "y": 0.0, "z": 0.0}
            frames.append(f)

        track = build_track(frames, hz=10.0)

        assert track[0]["x"] == 500.0
        assert track[-1]["x"] == 509.0
        assert track[0]["z"] == -200.0


class TestDetectLaps:
    """Test lap detection algorithms."""

    def test_detect_laps_with_timing_state(self):
        """Test lap detection using SHM timing state (last_lap_time_ms changes)."""
        frames = []
        for lap in range(3):
            for _i in range(100):
                frames.append(create_mock_frame(len(frames), speed=100.0, position=0.5, last_lap_time_ms=lap * 90000))

        track = build_track(frames, hz=10.0)
        lap_bounds = detect_laps(track, hz=10.0)

        # Should detect 2 boundaries (last_lap_time changes at lap 1 and lap 2)
        assert len(lap_bounds) >= 1

    def test_detect_laps_ignores_synthetic_shutdown_lap_time(self):
        """An Ended-session timing update is not a finish-line crossing."""
        frames = []
        for i in range(30):
            if i < 10:
                last_lap_time_ms = 0
                session_phase = "Session"
            elif i < 20:
                last_lap_time_ms = 70_975
                session_phase = "Session"
            else:
                last_lap_time_ms = 241_661
                session_phase = "Ended"
            frame = create_mock_frame(
                i,
                speed=100.0,
                position=0.5,
                last_lap_time_ms=last_lap_time_ms,
            )
            frame.graphics["session_phase"] = session_phase
            frames.append(frame)

        track = build_track(frames, hz=10.0)

        assert detect_laps(track, hz=10.0) == [10]

    def test_detect_laps_no_timing_changes(self):
        """Test lap detection when last_lap_time_ms never changes."""
        frames = [create_mock_frame(i, position=i * 0.01, last_lap_time_ms=0) for i in range(200)]
        track = build_track(frames, hz=10.0)

        lap_bounds = detect_laps(track, hz=10.0)

        # No timing changes means no lap boundaries
        assert len(lap_bounds) == 0

    def test_detect_laps_filters_short_gaps(self):
        """Test lap detection filters boundaries too close together."""
        # Two timing changes only 5 frames apart (below min_lap_frames=10 at 10Hz)
        frames = []
        for i in range(50):
            llt = 90000 if i == 20 else (180000 if i == 25 else 0)
            frames.append(create_mock_frame(i, position=0.5, last_lap_time_ms=llt))
        track = build_track(frames, hz=10.0)

        lap_bounds = detect_laps(track, hz=10.0)

        # Second change at frame 25 is only 5 frames after first (filtered)
        assert len(lap_bounds) <= 1

    def test_detect_laps_short_session(self):
        """Test lap detection with very short session."""
        frames = [create_mock_frame(i) for i in range(10)]
        track = build_track(frames, hz=10.0)

        lap_bounds = detect_laps(track, hz=10.0)

        # Short sessions should not detect laps
        assert len(lap_bounds) == 0

    def test_detect_laps_no_valid_laps(self):
        """Test lap detection when no timing changes exist."""
        frames = [create_mock_frame(i, position=0.0, last_lap_time_ms=0) for i in range(50)]
        track = build_track(frames, hz=10.0)

        lap_bounds = detect_laps(track, hz=10.0)

        assert len(lap_bounds) == 0

    def test_detect_laps_single_lap(self):
        """Test lap detection with exactly one timing change."""
        frames = []
        for i in range(100):
            llt = 95000 if i >= 50 else 0
            frames.append(create_mock_frame(i, position=0.5, last_lap_time_ms=llt))
        track = build_track(frames, hz=10.0)

        lap_bounds = detect_laps(track, hz=10.0)

        # Should detect one boundary at the timing change
        assert len(lap_bounds) >= 1

    def test_detect_laps_ignores_shutdown_last_time_reset(self):
        """Cleared SHM mappings must not create a zero-second final lap."""
        frames = []
        for i in range(120):
            if i < 50:
                last_lap_time = 0
            elif i < 100:
                last_lap_time = 65000
            else:
                last_lap_time = 0
            frames.append(
                create_mock_frame(
                    i,
                    position=(i % 50) / 50,
                    last_lap_time_ms=last_lap_time,
                )
            )

        lap_bounds = detect_laps(build_track(frames, hz=10.0), hz=10.0)

        assert lap_bounds == [50]


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
            ],
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

    def test_get_graphics_progress_from_frame(self):
        """Test that graphics carries normalized_car_position."""
        frame = create_mock_frame(0, speed=150.0, position=0.5)

        graphics = frame.graphics

        assert graphics is not None
        assert graphics.get("normalized_car_position") == 0.5
        assert graphics.get("has_authoritative_progress") is True

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

    # --- _safe_4 ---

    def test_safe_4_with_list_longer_than_4(self):
        """_safe_4 truncates a list > 4 elements to the first 4."""
        result = _safe_4([1, 2, 3, 4, 5, 6])
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_list_exactly_4(self):
        """_safe_4 passes through exactly 4 elements unchanged."""
        result = _safe_4([1, 2, 3, 4])
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_short_list(self):
        """_safe_4 pads a list < 4 elements with the default value."""
        result = _safe_4([1, 2])
        assert result == [1, 2, 0.0, 0.0]

    def test_safe_4_with_empty_list(self):
        """_safe_4 returns all-defaults for an empty list."""
        result = _safe_4([])
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_tuple(self):
        """_safe_4 accepts a tuple and returns a list."""
        result = _safe_4((1, 2, 3, 4, 5))
        assert result == [1, 2, 3, 4]

    def test_safe_4_with_dict(self):
        """_safe_4 treats a dict as invalid and returns all-defaults."""
        result = _safe_4({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_string(self):
        """_safe_4 treats a string as invalid and returns all-defaults."""
        result = _safe_4("test")
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_none(self):
        """_safe_4 treats None as invalid and returns all-defaults."""
        result = _safe_4(None)
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_safe_4_with_custom_default(self):
        """_safe_4 uses the supplied default when padding."""
        result = _safe_4([1], default=5.0)
        assert result == [1, 5.0, 5.0, 5.0]

    # --- _sanitize_slip ---

    def test_sanitize_slip_valid_positive(self):
        """_sanitize_slip passes through a valid positive value."""
        result = _sanitize_slip(0.5)
        assert result == 0.5

    def test_sanitize_slip_zero(self):
        """_sanitize_slip accepts zero."""
        result = _sanitize_slip(0.0)
        assert result == 0.0

    def test_sanitize_slip_negative(self):
        """_sanitize_slip clamps negative values to 0.0."""
        result = _sanitize_slip(-0.5)
        assert result == 0.0

    def test_sanitize_slip_clamps_above_5(self):
        """_sanitize_slip clamps values above 5.0 to 5.0."""
        result = _sanitize_slip(10.0)
        assert result == 5.0

    def test_sanitize_slip_at_5(self):
        """_sanitize_slip accepts 5.0 (the upper bound) unchanged."""
        result = _sanitize_slip(5.0)
        assert result == 5.0

    def test_sanitize_slip_below_5(self):
        """_sanitize_slip passes through values between 0 and 5 unchanged."""
        result = _sanitize_slip(1.5)
        assert result == 1.5

    def test_sanitize_slip_with_string(self):
        """_sanitize_slip converts a numeric string."""
        result = _sanitize_slip("0.5")
        assert result == 0.5

    def test_sanitize_slip_with_invalid_string(self):
        """_sanitize_slip returns 0.0 for a non-numeric string."""
        result = _sanitize_slip("invalid")
        assert result == 0.0

    def test_sanitize_slip_with_none(self):
        """_sanitize_slip returns 0.0 for None."""
        result = _sanitize_slip(None)
        assert result == 0.0

    def test_sanitize_slip_infinity(self):
        """_sanitize_slip returns 0.0 for positive infinity."""
        result = _sanitize_slip(float("inf"))
        assert result == 0.0

    def test_sanitize_slip_negative_infinity(self):
        """_sanitize_slip returns 0.0 for negative infinity."""
        result = _sanitize_slip(float("-inf"))
        assert result == 0.0

    def test_sanitize_slip_nan(self):
        """_sanitize_slip returns 0.0 for NaN."""
        result = _sanitize_slip(float("nan"))
        assert result == 0.0


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
            track.append(
                {
                    "frame": i,
                    "speed": 150 - i if i < 50 else 100,
                    "brake": 0.5 if 30 <= i < 50 else 0.0,
                    "steer": 0.1 if i >= 50 else 0.0,
                    "gas": 0.0 if i < 70 else 0.5,
                    "acc_g_z": -0.8 if 30 <= i < 50 else 0.0,
                    "x": i * 10,
                    "z": 0,
                }
            )

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
            track.append(
                {
                    "frame": i,
                    "acc_g_x": 0.8 if 10 <= i < 30 else 0.1,
                    "acc_g_z": -0.5 if 10 <= i < 20 else 0.0,
                    "brake": 0.5 if 10 <= i < 20 else 0.0,
                }
            )

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

    Regression test for captures with 0% authoritative graphics progress
    (the decoder isn't
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
            "tyre_temp_fl": core_temp,
            "tyre_temp_fr": core_temp,
            "tyre_temp_rl": core_temp,
            "tyre_temp_rr": core_temp,
            "tyre_wear_fl": wear_per_wheel,
            "tyre_wear_fr": wear_per_wheel,
            "tyre_wear_rl": wear_per_wheel,
            "tyre_wear_rr": wear_per_wheel,
            "tyre_dirty_fl": dirty,
            "tyre_dirty_fr": dirty,
            "tyre_dirty_rl": dirty,
            "tyre_dirty_rr": dirty,
            "slip_angle_fl": slip_rad,
            "slip_angle_fr": slip_rad,
            "slip_angle_rl": slip_rad,
            "slip_angle_rr": slip_rad,
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

        lap = self._make_lap(1, lat_g_peak=2.1, core_temp=85.0, slip_deg=4.0, end_wear=0.5)
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

        laps = [self._make_lap(i, lat_g_peak=2.10, core_temp=82.0, slip_deg=3.0, end_wear=0.20 * i) for i in (1, 2, 3)]
        result = analyze_tyre_grip_degradation(laps)

        assert result["trends"]["peak_lat_g"] == "FLAT"
        assert result["trends"]["core_temp"] == "FLAT"
        assert not result["flags"]


class TestTelemetryAnalyzer:
    """Test TelemetryAnalyzer class."""

    @pytest.mark.asyncio
    async def test_analyze_with_captured_data(self, tmp_path):
        """Test TelemetryAnalyzer.analyze with captured telemetry data."""
        import json

        from src.core.telemetry_analyzer import TelemetryAnalyzer
        from src.core.telemetry_decoder import decode_physics, physics_to_dict

        # Load the captured startup frames
        frames = []
        with open("tests/fixtures/sample_telemetry.jsonl", "r") as f:
            for i, line in enumerate(f):
                if i >= 50:
                    break
                frame_json = json.loads(line)
                physics_raw = bytes.fromhex(frame_json["physics_raw"])
                decoded = decode_physics(physics_raw)
                physics_dict = physics_to_dict(decoded)

                frame = FrameData(
                    timestamp=frame_json["timestamp"],
                    frame_number=frame_json["frame_number"],
                    physics=physics_dict,
                )
                frames.append(frame)

        analyzer = TelemetryAnalyzer(output_dir=str(tmp_path))
        result = await analyzer.analyze(frames, hz=10.0, output_prefix="test")

        assert result is not None
        assert hasattr(result, "html_path")
        assert hasattr(result, "ai_prompt_path")
        assert hasattr(result, "laps_detected")

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
            frames, hz=10.0, game_lap_boundaries=game_boundaries, output_prefix="test_game_laps"
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
    async def test_analyze_realigns_delayed_callbacks_to_all_timing_boundaries(self, tmp_path):
        """Buffered callbacks retain invalidity across a 1 ms source mismatch."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        lap_times = [66393, 65559, 66174, 64428, 78888]
        frames = []
        last_lap_time = 0
        for frame_num in range(360):
            if frame_num in (60, 120, 180, 240, 300):
                last_lap_time = lap_times[(frame_num // 60) - 1]
            frames.append(
                create_mock_frame(
                    frame_num,
                    speed=100.0,
                    position=(frame_num % 60) / 60,
                    last_lap_time_ms=last_lap_time,
                )
            )

        manager = SharedSessionManager()
        manager.update_lap_validity_from_graphics_shm(5, True)
        analyzer = TelemetryAnalyzer(
            output_dir=str(tmp_path),
            session_manager=manager,
        )
        # Lap 3 arrived after lap 4 and differs from SHM by 1 ms. Its invalid
        # type must follow the nearest lap time rather than callback order.
        # The invalid fifth-lap callback is absent and comes from shared state.
        delayed_markers = [
            (60, 66393, 1, "VALID"),
            (120, 65559, 2, "VALID"),
            (270, 64428, 3, "VALID"),
            (320, 66175, 4, "INVALID_GAME"),
        ]

        with (
            patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
            patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
        ):
            result = await analyzer.analyze(
                frames,
                hz=10.0,
                game_lap_boundaries=delayed_markers,
                output_prefix="delayed_callbacks",
            )

        assert result.laps_detected == 5
        data = html_spy.await_args.args[0]
        assert [lap["lap_num"] for lap in data["laps"]] == [1, 2, 3, 4, 5]
        assert [lap["lap_time_s"] for lap in data["laps"]] == pytest.approx([66.393, 65.559, 66.174, 64.428, 78.888])
        assert [lap["is_valid"] for lap in data["laps"]] == [True, True, False, True, False]
        assert [lap["end_frame"] for lap in data["laps"]] == [60, 120, 180, 240, 300]
        assert any("realigned" in note for note in data["analysis_notes"])

    @pytest.mark.asyncio
    async def test_analyze_excludes_invalid_laps_from_best_and_coaching_selection(self, tmp_path):
        """A faster invalid lap must remain visible without becoming a PB or coach lap."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        lap_times = [70000, 60000, 65000, 75000]
        frames = []
        last_lap_time = 0
        for frame_num in range(300):
            if frame_num in (60, 120, 180, 240):
                last_lap_time = lap_times[(frame_num // 60) - 1]
            frames.append(
                create_mock_frame(
                    frame_num,
                    speed=(333.0 if 60 <= frame_num < 120 else 100.0),
                    position=(frame_num % 60) / 60,
                    last_lap_time_ms=last_lap_time,
                )
            )

        analyzer = TelemetryAnalyzer(output_dir=str(tmp_path))
        markers = [
            (60, 70000, 1, "VALID"),
            (120, 60000, 2, "INVALID_GAME"),
            (180, 65000, 3, "VALID"),
            (240, 75000, 4, "INVALID_GAME"),
        ]

        with (
            patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
            patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
        ):
            result = await analyzer.analyze(
                frames,
                hz=10.0,
                game_lap_boundaries=markers,
                output_prefix="valid_coaching_pool",
            )

        data = html_spy.await_args.args[0]
        assert [lap["lap_num"] for lap in data["laps"]] == [1, 2, 3, 4]
        valid_lap_numbers = {lap["lap_num"] for lap in data["laps"] if lap["is_valid"]}
        assert data["best_lap_num"] == 3
        assert data["reference_lap_num"] in valid_lap_numbers
        assert data["comparison_lap_num"] in valid_lap_numbers
        assert result.best_lap_time == pytest.approx(65.0)

        summary = json.loads((tmp_path / "session_history.jsonl").read_text().strip())
        assert summary["best_lap_time_s"] == pytest.approx(65.0)
        assert summary["top_speed"] == pytest.approx(100.0)
        assert summary["laps"] == 2

    @pytest.mark.asyncio
    async def test_analyze_all_invalid_session_has_no_best_or_persisted_pb(self, tmp_path):
        """An all-invalid session remains diagnostic and must not create PB history."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = []
        last_lap_time = 0
        for frame_num in range(180):
            if frame_num == 60:
                last_lap_time = 60000
            elif frame_num == 120:
                last_lap_time = 59000
            frames.append(
                create_mock_frame(
                    frame_num,
                    speed=100.0,
                    position=(frame_num % 60) / 60,
                    last_lap_time_ms=last_lap_time,
                )
            )

        analyzer = TelemetryAnalyzer(output_dir=str(tmp_path))
        with (
            patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")) as html_spy,
            patch.object(analyzer, "_generate_ai_prompt", new=AsyncMock(return_value="prompt.txt")),
        ):
            result = await analyzer.analyze(
                frames,
                hz=10.0,
                game_lap_boundaries=[
                    (60, 60000, 1, "INVALID_GAME"),
                    (120, 59000, 2, "INVALID_GAME"),
                ],
                output_prefix="all_invalid",
            )

        data = html_spy.await_args.args[0]
        assert data["best_lap_num"] is None
        assert data["reference_lap_num"] is None
        assert data["comparison_lap_num"] is None
        assert data["analysis_mode"] == "diagnostic"
        assert any("No valid completed laps" in note for note in data["analysis_notes"])
        assert result.best_lap_time is None
        assert not (tmp_path / "session_history.jsonl").exists()

    @pytest.mark.asyncio
    async def test_analyze_compares_with_summary_before_persisting_current_session(self, tmp_path):
        """Session notes must compare against the preceding run, not themselves."""
        from src.core.analyzer.session_summary import _write_session_summary
        from src.core.telemetry_analyzer import TelemetryAnalyzer
        from src.models.lap import SessionData

        manager = SharedSessionManager()
        manager.update_from_logs(SessionData(car="Test Car", track="Test Track"))
        _write_session_summary(
            str(tmp_path),
            "Test Track",
            "Test Car",
            best_lap_time_s=70.0,
            top_speed=150.0,
            lap_count=1,
            avg_fuel_per_lap=None,
        )
        frames = [create_mock_frame(i, speed=100.0, position=(i % 100) / 100) for i in range(220)]
        analyzer = TelemetryAnalyzer(output_dir=str(tmp_path), session_manager=manager)

        with (
            patch.object(analyzer, "_generate_html", new=AsyncMock(return_value="report.html")),
            patch.object(
                analyzer,
                "_generate_ai_prompt",
                new=AsyncMock(return_value="prompt.txt"),
            ) as prompt_spy,
        ):
            result = await analyzer.analyze(
                frames,
                hz=10.0,
                track_name="Test Track",
                game_lap_boundaries=[
                    (100, 70000, 1, "VALID"),
                    (200, 65000, 2, "VALID"),
                ],
                output_prefix="session_comparison",
            )

        assert result.best_lap_time == pytest.approx(65.0)
        analysis_data = prompt_spy.await_args.args[0]
        assert "Last session best: 1:10.00 (today 1:05.00, -5.00s)." in analysis_data["analysis_notes"]

    @pytest.mark.asyncio
    async def test_analyze_uses_outlap_boundary_but_excludes_outlap(self):
        """Structural boundaries delimit timed laps without becoming reports."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = [create_mock_frame(i, speed=100.0, position=(i % 50) / 50) for i in range(180)]
        analyzer = TelemetryAnalyzer(output_dir="tests/output")
        markers = [
            (50, 120000, 0, "OUTLAP"),
            (100, 150000, 1, "VALID"),
            (150, 140000, 2, "VALID"),
        ]

        result = await analyzer.analyze(
            frames,
            hz=10.0,
            game_lap_boundaries=markers,
            output_prefix="test_structural_outlap",
        )

        assert result.laps_detected == 2
        assert result.best_lap_time == pytest.approx(140.0)

    @pytest.mark.asyncio
    async def test_analyze_prefers_shared_session_lap_data(self):
        """Analyzer should use shared-session lap timing/validity when available."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        frames = [create_mock_frame(i, speed=90.0 + (i % 40), position=i * 0.01) for i in range(220)]
        manager = SharedSessionManager()
        # Lap times must come from logs (graphics SHM is not authoritative).
        from src.models.shared_session import LapTimingData

        for lap_num, time_ms in [(1, 200000.0), (2, 190000.0), (3, 180000.0), (4, 170000.0)]:
            manager._session_data.lap_timing[lap_num] = LapTimingData(
                lap_number=lap_num, completed_lap_time=time_ms, completed_lap_time_source="logs"
            )
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
        # Lap times must come from logs (graphics SHM is not authoritative).
        from src.models.shared_session import LapTimingData

        for lap_num, time_ms in [(1, 999000.0), (2, 150000.0), (3, 140000.0), (4, 130000.0), (5, 129000.0)]:
            manager._session_data.lap_timing[lap_num] = LapTimingData(
                lap_number=lap_num, completed_lap_time=time_ms, completed_lap_time_source="logs"
            )

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
            track.append(
                {
                    "frame": i,
                    "norm_pos": i / 200.0,
                    "speed": 80.0 if 0.20 <= (i / 200.0) < 0.30 else 100.0,
                    "x": float(i),
                    "z": 0.0,
                }
            )

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

    def test_detect_profiled_corners_skips_missing_progress_samples(self):
        """Shutdown frames may lose graphics progress inside a lap segment."""
        from src.core.telemetry_analyzer import detect_profiled_corners

        track = [
            {
                "frame": i,
                "norm_pos": None if i >= 80 else i / 100.0,
                "speed": 80.0 if 20 <= i < 30 else 100.0,
                "x": float(i),
                "z": 0.0,
            }
            for i in range(100)
        ]
        profile = {
            "corners": [
                {"id": 1, "start": 0.20, "end": 0.30, "name": "Corner 1"},
            ]
        }

        corners = detect_profiled_corners(track, 0, 100, profile, hz=10.0)

        assert len(corners) == 1
        assert corners[0]["name"] == "Corner 1"

    def test_detect_profiled_corners_fallback_without_norm_pos(self):
        """Without norm_pos confidence is LOW and segment_time_s is None."""
        from src.core.telemetry_analyzer import corner_segment_time, detect_profiled_corners

        track = []
        for i in range(200):
            track.append(
                {
                    "frame": i,
                    "speed": 80.0 if 40 <= i < 60 else 100.0,
                    "x": float(i),
                    "z": 0.0,
                }
            )

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
        from src.core.telemetry_analyzer import _detect_profiled_corners_canonical

        # Canonical track with uniform progress and time_s
        samples = []
        for i in range(100):
            samples.append(
                {
                    "frame": i,
                    "lap_progress": i / 100.0,
                    "time_s": i / 10.0,
                    "speed": 70.0 if 0.20 <= (i / 100.0) < 0.30 else 120.0,
                    "brake": 0.5 if 0.22 <= (i / 100.0) < 0.26 else 0.0,
                    "gas": 0.0,
                    "steer": 0.0,
                    "x": float(i),
                    "z": 0.0,
                }
            )

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

    @pytest.mark.asyncio
    async def test_ai_prompt_honors_valid_best_over_faster_invalid_lap(self, tmp_path):
        """Prompt rendering must not recompute session best from invalid laps."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        invalid_lap = {
            "lap_num": 1,
            "lap_time_s": 60.0,
            "lap_time_str": "1:00.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "is_valid": False,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": [],
        }
        valid_lap = {
            **invalid_lap,
            "lap_num": 2,
            "lap_time_s": 65.0,
            "lap_time_str": "1:05.00",
            "is_valid": True,
        }
        data = {
            "hz": 10.0,
            "laps": [invalid_lap, valid_lap],
            "best_lap_num": 2,
            "reference_lap_num": 2,
            "comparison_lap_num": 2,
            "ref_corners": [corner],
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

        analyzer = TelemetryAnalyzer(output_dir=str(tmp_path))
        path = await analyzer._generate_ai_prompt(data, output_prefix="valid_best")
        with open(path, encoding="utf-8") as fh:
            prompt = fh.read()

        assert "- Best lap:   #2  1:05.00" in prompt
        assert "Lap 1: 1:00.00" in prompt
        assert "Lap 1: 1:00.00  max 200.0 km/h  avg 150.0 km/h [INVALID] <- BEST" not in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_catalog_car_generates_proper_rules(self):
        """When tuning catalog exists for a car, prompt must reference it and enforce catalog-only rules.

        All cars now list brake bias, so the catalog-exists-with-brake-bias scenario
        validates that the prompt correctly defers to the catalog.
        """
        from src.core.car_tuning_catalog import get_tuning_params
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        car_model = "Ford Mustang GT3"
        params = get_tuning_params(car_model)
        assert params is not None, "Mustang GT3 should match catalog"
        param_labels = [p["label"].lower() for p in params]
        assert any("brake bias" in l for l in param_labels), "Mustang GT3 catalog should list brake bias"  # noqa: E741

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap = {
            "lap_num": 1,
            "lap_time_s": 90.0,
            "lap_time_str": "1:30.00",
            "max_speed": 200.0,
            "avg_speed": 140.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap, {**lap, "lap_num": 2}],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}],
            "corner_data": {},
            "corner_speeds": {},
            "analysis_mode": "full",
            "analysis_confidence": "high",
            "analysis_notes": [],
            "authoritative_progress_ratio": 1.0,
            "plausible_frame_ratio": 1.0,
            "track_label": "Test Track",
            "car": car_model,
        }
        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=SharedSessionManager())
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_catalog_car_rules")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "CAR SETUP PARAMETERS" in prompt, "Tuning block should be present"
        assert "ONLY recommend parameters listed in the CAR SETUP PARAMETERS" in prompt
        assert "If a parameter is NOT in that list, the car cannot adjust it" in prompt
        assert "Parameter and Signal MUST describe the same subsystem" in prompt
        assert "Tyre pressure rows MUST use tyre pressure evidence in psi only" in prompt
        assert "Brake temperature evidence may only support brake-related parameters" in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_unknown_car_forbids_brake_bias(self):
        """When car is unknown, prompt must not encourage brake bias recommendations."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap = {
            "lap_num": 1,
            "lap_time_s": 90.0,
            "lap_time_str": "1:30.00",
            "max_speed": 200.0,
            "avg_speed": 140.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap, {**lap, "lap_num": 2}],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}],
            "corner_data": {},
            "corner_speeds": {},
            "analysis_mode": "full",
            "analysis_confidence": "high",
            "analysis_notes": [],
            "authoritative_progress_ratio": 1.0,
            "plausible_frame_ratio": 1.0,
            "track_label": "Test Track",
            "car": "",
        }
        analyzer = TelemetryAnalyzer(output_dir="tests/output", session_manager=SharedSessionManager())
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_unknown_car")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "CAR SETUP" in prompt
        assert "SKIPPED" in prompt
        assert "Do NOT recommend brake bias" in prompt

    def test_analyze_suspension_with_detected_corners_no_keyerror(self):
        """Regression: analyze_suspension must not crash on detected lap corners
        that lack 'start'/'end' keys (they have 'start_frame'/'end_frame' instead).
        Profile corners (with start/end) should be used."""
        from src.core.analyzer.metrics import analyze_suspension

        # Detected lap corners — no 'start'/'end' progress keys
        detected_corners = [
            {
                "id": 1,
                "name": "T1",
                "start_frame": 10,
                "end_frame": 30,
                "apex_frame": 20,
                "apex_speed": 80,
                "lap_pos": 0.15,
            },
        ]
        # Should not raise KeyError — empty list means no analysis, no crash
        result = analyze_suspension([], detected_corners)
        assert result == {"bottoming_notes": [], "travel_delta_notes": [], "camber_notes": []}

        # Profile corners — have 'start'/'end' progress keys
        profile_corners = [
            {"id": 1, "name": "T1", "start": 0.10, "end": 0.20},
        ]
        laps = [
            {
                "lap_num": 1,
                "track": [{"lap_progress": 0.15, "sus_fl": 5.0, "sus_fr": 5.0, "sus_rl": 5.0, "sus_rr": 5.0}],
            }
        ]
        result = analyze_suspension(laps, profile_corners)
        assert isinstance(result, dict)

    def test_canonical_corner_segment_time_none_when_measurement_too_sparse(self):
        """Regression: canonical corner detection must set segment_time_s=None
        (not 0.0) when the measurement window has fewer than 2 points or
        produces zero elapsed time.  A 0.0 value was being selected as the
        'best segment' in theoretical best lap calculation."""
        from src.core.telemetry_analyzer import _detect_profiled_corners_canonical

        # Build a canonical lap where the corner window has very few samples
        samples = []
        for i in range(200):
            t = i / 199.0
            samples.append(
                {
                    "frame": i,
                    "time_s": i / 10.0,
                    "lap_progress": t,
                    "lap_pos": t,
                    "speed": 100.0,
                    "x": 0.0,
                    "z": 0.0,
                    "heading": 0.0,
                    "steer": 0.0,
                    "brake": 0.0,
                    "gas": 1.0,
                }
            )
        canonical_lap = {"samples": samples, "progress_start": 0.0, "progress_end": 1.0}
        profile = {
            "corners": [
                {"id": 1, "name": "T1", "start": 0.01, "end": 0.02},
            ]
        }
        corners = _detect_profiled_corners_canonical(
            canonical_lap["samples"],
            profile,
            hz=10.0,
            authoritative_progress=True,
        )
        # With a tiny window [0.01, 0.02], the measurement window may have
        # 0-1 points. segment_time_s must be None, not 0.0.
        if corners:
            assert corners[0]["segment_time_s"] is None or corners[0]["segment_time_s"] > 0.0

    def test_canonical_bins_adapt_to_dense_corner_profile(self):
        """Regression: the Nordschleife 24H profile (72 corners, windows as
        narrow as 0.005 of the lap) got zero canonical corners because the
        fixed 200-bin grid left <4 samples per corner window, suppressing
        all coaching. Bins must adapt to the narrowest corner window."""
        from src.core.analyzer.canonical import _build_canonical_lap, _canonical_bins_for_profile
        from src.core.analyzer.corner_detection import _detect_profiled_corners_canonical

        dense_profile = {
            "corners": [
                {"id": i + 1, "name": f"C{i + 1}", "start": 0.005 + i * 0.005, "end": 0.01 + i * 0.005}
                for i in range(72)
            ]
        }

        assert _canonical_bins_for_profile(None) == 200
        assert _canonical_bins_for_profile({"corners": [{"start": 0.1, "end": 0.2}]}) == 200
        assert _canonical_bins_for_profile(dense_profile) >= 1600

        lap_track = [
            {
                "frame": i,
                "norm_pos": i / 7199.0,
                "speed": 100.0 + (i % 37),
                "x": 0.0,
                "z": 0.0,
                "heading": 0.0,
                "steer": 0.05,
                "brake": 0.1,
                "gas": 0.5,
            }
            for i in range(7200)
        ]
        fine = _build_canonical_lap(
            lap_track,
            lap_start_frame=0,
            hz=10.0,
            bins=_canonical_bins_for_profile(dense_profile),
        )
        fine_corners = _detect_profiled_corners_canonical(
            fine["samples"],
            dense_profile,
            hz=10.0,
            authoritative_progress=True,
        )
        assert len(fine_corners) == 72

        coarse = _build_canonical_lap(lap_track, lap_start_frame=0, hz=10.0, bins=200)
        coarse_corners = _detect_profiled_corners_canonical(
            coarse["samples"],
            dense_profile,
            hz=10.0,
            authoritative_progress=True,
        )
        assert len(coarse_corners) == 0

    @pytest.mark.asyncio
    async def test_theoretical_best_uses_gap_sum_not_segment_sum(self):
        """Regression: theoretical best must be actual_best - sum(per_corner_gaps),
        not sum(corner_segments).  Old code summed only corner segments (e.g. 29s)
        and compared to full lap time (109s), producing nonsensical 80s 'potential gain'.
        """
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner1_lap1 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 3.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner1_lap2 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 85.0,
            "entry_speed": 100.0,
            "exit_speed": 95.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap1 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 70.0,
            "entry_speed": 90.0,
            "exit_speed": 80.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 4.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap2 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 75.0,
            "entry_speed": 90.0,
            "exit_speed": 85.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 3.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }

        lap1 = {
            "lap_num": 1,
            "lap_time_s": 100.0,
            "lap_time_str": "1:40.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 1000,
            "corners": [corner1_lap1, corner2_lap1],
            "track": [],
        }
        lap2 = {
            "lap_num": 2,
            "lap_time_s": 105.0,
            "lap_time_str": "1:45.00",
            "max_speed": 195.0,
            "avg_speed": 145.0,
            "start_frame": 0,
            "end_frame": 1050,
            "corners": [corner1_lap2, corner2_lap2],
            "track": [],
        }

        data = {
            "hz": 10.0,
            "laps": [lap1, lap2],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}, {"id": 2, "name": "T2"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_theoretical_best")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "THEORETICAL BEST LAP:" in prompt
        # Best lap is lap1 (100s). Gaps: T1=3.0-2.5=0.5s, T2=4.0-3.5=0.5s.
        # Total gap=1.0s. Theoretical best=100-1.0=99.0s.
        # Old buggy code would show "Assembled best segments: 6.0s" and "Potential gain: 94.0s"
        assert "Potential gain: 1.00s" in prompt
        assert "99.00s" in prompt
        # Must NOT contain the old "Assembled best segments" label
        assert "Assembled best segments" not in prompt

    @pytest.mark.asyncio
    async def test_theoretical_best_filters_impossibly_short_segments(self):
        """Regression: when a corner's best segment is less than 50% of the best
        lap's segment for that corner (e.g. 0.60s vs 4.40s), it should be excluded
        from the theoretical best calculation as a measurement artifact."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner1_lap1 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 4.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        # Lap 2 has an impossibly short segment (0.5s vs 4.0s) — artifact
        corner1_lap2 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 85.0,
            "entry_speed": 100.0,
            "exit_speed": 95.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 0.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap1 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 70.0,
            "entry_speed": 90.0,
            "exit_speed": 80.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 3.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap2 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 75.0,
            "entry_speed": 90.0,
            "exit_speed": 85.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 2.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }

        lap1 = {
            "lap_num": 1,
            "lap_time_s": 100.0,
            "lap_time_str": "1:40.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 1000,
            "corners": [corner1_lap1, corner2_lap1],
            "track": [],
        }
        lap2 = {
            "lap_num": 2,
            "lap_time_s": 105.0,
            "lap_time_str": "1:45.00",
            "max_speed": 195.0,
            "avg_speed": 145.0,
            "start_frame": 0,
            "end_frame": 1050,
            "corners": [corner1_lap2, corner2_lap2],
            "track": [],
        }

        data = {
            "hz": 10.0,
            "laps": [lap1, lap2],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}, {"id": 2, "name": "T2"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_theo_best_filter")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "THEORETICAL BEST LAP:" in prompt
        # T1 best segment (0.5s) is < 50% of best lap segment (4.0s) -> excluded.
        # Only T2 gap counts: 3.0-2.5=0.5s. Theoretical best=100-0.5=99.5s.
        assert "Potential gain: 0.50s" in prompt
        # T1 should not appear in per-corner breakdown (it was filtered)
        assert "C1 T1: best segment 0.50s" not in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_includes_lap_time_decomposition(self):
        """LAP TIME DECOMPOSITION section shows corner vs straight time per lap."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap = {
            "lap_num": 1,
            "lap_time_s": 90.0,
            "lap_time_str": "1:30.00",
            "max_speed": 200.0,
            "avg_speed": 140.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap, {**lap, "lap_num": 2}],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_decomp")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "LAP TIME DECOMPOSITION" in prompt
        assert "corners 2.0s" in prompt
        assert "straights 88.0s" in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_includes_straight_sector_analysis(self):
        """STRAIGHT/SECTOR ANALYSIS shows time between consecutive corners."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner1 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 70.0,
            "entry_speed": 90.0,
            "exit_speed": 80.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 3.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap1 = {
            "lap_num": 1,
            "lap_time_s": 100.0,
            "lap_time_str": "1:40.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 1000,
            "corners": [corner1, corner2],
            "track": [],
        }
        corner2_lap2 = {**corner2, "segment_time_s": 3.5}
        lap2 = {
            "lap_num": 2,
            "lap_time_s": 105.0,
            "lap_time_str": "1:45.00",
            "max_speed": 195.0,
            "avg_speed": 145.0,
            "start_frame": 0,
            "end_frame": 1050,
            "corners": [corner1, corner2_lap2],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap1, lap2],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}, {"id": 2, "name": "T2"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_straight")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "STRAIGHT/SECTOR ANALYSIS" in prompt
        assert "T1 → T2:" in prompt
        assert "spread" in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_includes_exit_to_entry_correlation(self):
        """EXIT-TO-ENTRY CORRELATION links corner exit speed to next corner entry speed."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner1_lap1 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap1 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 70.0,
            "entry_speed": 95.0,
            "exit_speed": 80.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 3.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner1_lap2 = {
            "id": 1,
            "name": "T1",
            "apex_speed": 75.0,
            "entry_speed": 100.0,
            "exit_speed": 80.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        corner2_lap2 = {
            "id": 2,
            "name": "T2",
            "apex_speed": 65.0,
            "entry_speed": 85.0,
            "exit_speed": 75.0,
            "start_frame": 50,
            "end_frame": 80,
            "apex_frame": 65,
            "segment_time_s": 3.5,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap1 = {
            "lap_num": 1,
            "lap_time_s": 100.0,
            "lap_time_str": "1:40.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 1000,
            "corners": [corner1_lap1, corner2_lap1],
            "track": [],
        }
        lap2 = {
            "lap_num": 2,
            "lap_time_s": 105.0,
            "lap_time_str": "1:45.00",
            "max_speed": 195.0,
            "avg_speed": 145.0,
            "start_frame": 0,
            "end_frame": 1050,
            "corners": [corner1_lap2, corner2_lap2],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap1, lap2],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}, {"id": 2, "name": "T2"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_corr")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "EXIT-TO-ENTRY CORRELATION" in prompt
        # Exit delta: 80-90 = -10 km/h, entry delta: 85-95 = -10 km/h
        assert "T1 → T2:" in prompt
        assert "exit D -10.0 km/h" in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_includes_coast_time_aggregation(self):
        """COAST TIME AGGREGATION sums total coasting per lap."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        # Build track with coasting near apex (frames 18-22: no gas, no brake)
        track = []
        for i in range(100):
            track.append(
                {
                    "frame": i,
                    "speed": 80.0,
                    "gas": 0.0 if 18 <= i <= 22 else 0.5,
                    "gas_percent": 0.0 if 18 <= i <= 22 else 0.5,
                    "brake": 0.0,
                    "steer": 0.0,
                    "acc_g_x": 0.0,
                    "acc_g_z": 0.0,
                }
            )
        lap = {
            "lap_num": 1,
            "lap_time_s": 100.0,
            "lap_time_str": "1:40.00",
            "max_speed": 200.0,
            "avg_speed": 150.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": track,
        }
        data = {
            "hz": 10.0,
            "laps": [lap, {**lap, "lap_num": 2}],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_coast")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "COAST TIME AGGREGATION" in prompt
        assert "coasting" in prompt

    @pytest.mark.asyncio
    async def test_ai_prompt_response_format_has_straights_section(self):
        """Response format includes section 5 for STRAIGHTS & SECTORS."""
        from src.core.telemetry_analyzer import TelemetryAnalyzer

        corner = {
            "id": 1,
            "name": "T1",
            "apex_speed": 80.0,
            "entry_speed": 100.0,
            "exit_speed": 90.0,
            "start_frame": 10,
            "end_frame": 30,
            "apex_frame": 20,
            "segment_time_s": 2.0,
            "confidence_label": "high",
            "entry_state": None,
            "apex_state": None,
            "exit_state": None,
        }
        lap = {
            "lap_num": 1,
            "lap_time_s": 90.0,
            "lap_time_str": "1:30.00",
            "max_speed": 200.0,
            "avg_speed": 140.0,
            "start_frame": 0,
            "end_frame": 100,
            "corners": [corner],
            "track": [],
        }
        data = {
            "hz": 10.0,
            "laps": [lap, {**lap, "lap_num": 2}],
            "best_lap_num": 1,
            "reference_lap_num": 1,
            "comparison_lap_num": 2,
            "ref_corners": [{"id": 1, "name": "T1"}],
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
        path = await analyzer._generate_ai_prompt(data, output_prefix="test_format")
        with open(path, "r", encoding="utf-8") as fh:
            prompt = fh.read()

        assert "## 5. STRAIGHTS & SECTORS" in prompt
        assert "## 6. TRACK NOTES" in prompt
        assert "## 7. SINGLE BIGGEST GAIN" in prompt
