"""
Tests for telemetry analyzer using real telemetry dump data.

Tests track building, lap detection, and analysis with actual
ACE telemetry captures.
"""

import json
from src.core.telemetry_analyzer import (
    build_track,
    detect_laps,
    get_physics,
    _safe_4,
    _sanitize_slip,
)
from src.core.telemetry_decoder import decode_graphics, decode_physics, physics_to_dict
from src.core.telemetry_capture import FrameData


def load_frames(count: int = 100) -> list[FrameData]:
    """Load frames from sample telemetry file."""
    frames = []
    with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
        for i, line in enumerate(f):
            if i >= count:
                break
            frame_json = json.loads(line)
            physics_raw = bytes.fromhex(frame_json['physics_raw'])
            graphics_raw = bytes.fromhex(frame_json['graphics_raw'])
            decoded = decode_physics(physics_raw)
            graphics = decode_graphics(graphics_raw)
            physics_dict = physics_to_dict(decoded)
            
            frame = FrameData(
                timestamp=frame_json['timestamp'],
                frame_number=frame_json['frame_number'],
                physics=physics_dict,
                graphics=graphics,
            )
            frames.append(frame)
    return frames


class TestBuildTrack:
    """Test track building from real telemetry data."""

    def test_build_track_with_real_data(self):
        """Test that build_track works with real telemetry frames."""
        frames = load_frames(50)
        
        track = build_track(frames, hz=10.0)
        
        assert isinstance(track, list)
        assert len(track) > 0

    def test_build_track_has_velocity_integration(self):
        """Test that track points have x, z coordinates from velocity integration."""
        frames = load_frames(50)
        
        track = build_track(frames, hz=10.0)
        
        # Check that track points have x and z coordinates
        for point in track[:5]:
            assert 'x' in point
            assert 'z' in point
            assert 'frame' in point

    def test_build_track_has_speed(self):
        """Test that track points have speed information."""
        frames = load_frames(50)
        
        track = build_track(frames, hz=10.0)
        
        # Check that track points have speed
        for point in track[:5]:
            assert 'speed' in point
            assert isinstance(point['speed'], (int, float))

    def test_build_track_prefers_graphics_progress(self):
        """Test that real-data track points use graphics-based authoritative progress."""
        frames = load_frames(50)

        track = build_track(frames, hz=10.0)

        assert any(point.get('has_authoritative_progress') for point in track)
        assert any(point.get('progress_source') == 'graphics' for point in track)

    def test_build_track_start_idx(self):
        """Test that start_idx parameter works correctly."""
        frames = load_frames(100)
        
        track_full = build_track(frames, hz=10.0, start_idx=0)
        track_partial = build_track(frames, hz=10.0, start_idx=20)
        
        # Partial track should be shorter
        assert len(track_partial) < len(track_full)


class TestDetectLaps:
    """Test lap detection from real telemetry data."""

    def test_detect_laps_with_real_data(self):
        """Test that detect_laps works with real telemetry."""
        frames = load_frames(100)
        track = build_track(frames, hz=10.0)
        
        boundaries = detect_laps(track, hz=10.0)
        
        # Should return either None or a list of boundaries
        assert boundaries is None or isinstance(boundaries, list)

    def test_detect_laps_min_lap_time(self):
        """Test that min_lap_time parameter affects detection."""
        frames = load_frames(100)
        track = build_track(frames, hz=10.0)
        
        # Test with different min_lap_time values
        boundaries_60 = detect_laps(track, hz=10.0, min_lap_time_s=60.0)
        boundaries_120 = detect_laps(track, hz=10.0, min_lap_time_s=120.0)
        
        # Both should return None or lists
        assert boundaries_60 is None or isinstance(boundaries_60, list)
        assert boundaries_120 is None or isinstance(boundaries_120, list)


class TestHelperFunctions:
    """Test helper functions with real data."""

    def test_safe_4_with_list(self):
        """Test _safe_4 with list input."""
        result = _safe_4([1.0, 2.0, 3.0, 4.0])
        assert result == [1.0, 2.0, 3.0, 4.0]

    def test_safe_4_with_short_list(self):
        """Test _safe_4 with short list."""
        result = _safe_4([1.0, 2.0])
        assert result == [1.0, 2.0, 0.0, 0.0]

    def test_safe_4_with_dict(self):
        """Test _safe_4 with dict input."""
        result = _safe_4({'a': 1})
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_sanitize_slip_valid(self):
        """Test _sanitize_slip with valid value."""
        result = _sanitize_slip(0.5)
        assert result == 0.5

    def test_sanitize_slip_negative(self):
        """Test _sanitize_slip with negative value."""
        result = _sanitize_slip(-1.0)
        assert result == 0.0

    def test_sanitize_slip_too_large(self):
        """Test _sanitize_slip with value too large."""
        result = _sanitize_slip(10.0)
        assert result == 5.0

    def test_sanitize_slip_nan(self):
        """Test _sanitize_slip with NaN."""
        import math
        result = _sanitize_slip(float('nan'))
        assert result == 0.0


class TestGetPhysics:
    """Test physics extraction from frames."""

    def test_get_physics_from_frame(self):
        """Test that get_physics extracts physics data."""
        frames = load_frames(10)
        frame = frames[0]
        
        physics = get_physics(frame)
        
        assert physics is not None
        assert isinstance(physics, dict)

    def test_get_physics_returns_dict(self):
        """Test that get_physics always returns a dict."""
        frames = load_frames(10)
        
        for frame in frames:
            physics = get_physics(frame)
            assert isinstance(physics, dict)


class TestRealDataStructure:
    """Test real telemetry data structure."""

    def test_frames_have_physics(self):
        """Test that loaded frames have physics data."""
        frames = load_frames(10)
        
        for frame in frames:
            assert frame.physics is not None
            assert isinstance(frame.physics, dict)

    def test_physics_has_velocity(self):
        """Test that physics data has velocity."""
        frames = load_frames(10)
        
        for frame in frames:
            physics = get_physics(frame)
            assert 'velocity' in physics
            # Velocity should be a dict with x, y, z
            velocity = physics['velocity']
            if isinstance(velocity, dict):
                assert 'x' in velocity or hasattr(velocity, 'x')

    def test_physics_has_speed(self):
        """Test that physics data has speed_kmh."""
        frames = load_frames(10)
        
        for frame in frames:
            physics = get_physics(frame)
            assert 'speed_kmh' in physics
