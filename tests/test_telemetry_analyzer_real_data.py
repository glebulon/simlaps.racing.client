"""
Integration tests for telemetry analyzer using real telemetry dump data.

Tests track building, lap detection, and analysis with actual
ACE telemetry captures from ``tests/fixtures/sample_telemetry.jsonl``.

Helper-function tests (``_safe_4``, ``_sanitize_slip``) and basic
``get_physics`` coverage are consolidated in
``test_telemetry_analyzer_comprehensive.py``.
"""

import json

import pytest

from src.core.telemetry_analyzer import (
    build_track,
    detect_laps,
    get_physics,
)
from src.core.telemetry_capture import FrameData
from src.core.telemetry_decoder import decode_physics, physics_to_dict

pytestmark = pytest.mark.integration


def load_frames(count: int = 100) -> list[FrameData]:
    """Load frames from sample telemetry file."""
    frames = []
    with open("tests/fixtures/sample_telemetry.jsonl", "r") as f:
        for i, line in enumerate(f):
            if i >= count:
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
    return frames


class TestBuildTrack:
    """Test track building from real telemetry data."""

    def test_build_track_with_real_data(self):
        """Test that build_track works with real telemetry frames."""
        frames = load_frames(50)

        track = build_track(frames, hz=10.0)

        assert isinstance(track, list)
        assert len(track) > 0

    def test_build_track_has_coordinates(self):
        """Test that track points have x, z coordinates."""
        frames = load_frames(50)

        track = build_track(frames, hz=10.0)

        # Check that track points have x and z coordinates
        for point in track[:5]:
            assert "x" in point
            assert "z" in point
            assert "frame" in point

    def test_build_track_has_speed(self):
        """Test that track points have speed information."""
        frames = load_frames(50)

        track = build_track(frames, hz=10.0)

        # Check that track points have speed
        for point in track[:5]:
            assert "speed" in point
            assert isinstance(point["speed"], (int, float))

    def test_build_track_uses_graphics_progress(self):
        """Test that build_track uses graphics-based progress."""
        # Create mock frames with graphics progress data
        from tests.test_telemetry_analyzer_comprehensive import create_mock_frame

        frames = [create_mock_frame(i, position=i * 0.01, speed=50.0) for i in range(50)]

        track = build_track(frames, hz=10.0)

        # Check that track points have norm_pos from graphics
        for point in track[:5]:
            assert "norm_pos" in point
            assert point["norm_pos"] is not None

        assert any(point.get("has_authoritative_progress") for point in track)
        assert any(point.get("progress_source") == "graphics" for point in track)

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
        """detect_laps returns a boundary list (empty for physics-only frames)."""
        frames = load_frames(100)
        track = build_track(frames, hz=10.0)

        boundaries = detect_laps(track, hz=10.0)

        # Real data frames are physics-only (no graphics SHM timing state),
        # so the simplified detector returns no boundaries.
        assert isinstance(boundaries, list)
        assert all(isinstance(b, int) for b in boundaries)

    def test_detect_laps_min_lap_time(self):
        """detect_laps called twice with identical params yields the same result."""
        frames = load_frames(100)
        track = build_track(frames, hz=10.0)

        result_1 = detect_laps(track, hz=10.0)
        result_2 = detect_laps(track, hz=10.0)

        # Deterministic — same input produces same output
        assert result_1 == result_2


class TestRealDataStructure:
    """Test real telemetry data structure."""

    def test_frames_have_physics(self):
        """Loaded frames carry a non-None physics dict."""
        frames = load_frames(10)

        for frame in frames:
            assert frame.physics is not None
            assert isinstance(frame.physics, dict)
            assert len(frame.physics) > 0

    def test_physics_has_velocity(self):
        """Physics data includes a velocity field with x/y/z components."""
        frames = load_frames(10)

        for frame in frames:
            physics = get_physics(frame)
            assert "velocity" in physics
            velocity = physics["velocity"]
            assert isinstance(velocity, dict), f"Expected dict, got {type(velocity)}"
            for axis in ("x", "y", "z"):
                assert axis in velocity, f"velocity missing '{axis}'"

    def test_physics_has_speed(self):
        """Physics data includes speed_kmh as a finite number."""
        frames = load_frames(10)

        for frame in frames:
            physics = get_physics(frame)
            assert "speed_kmh" in physics
            speed = physics["speed_kmh"]
            assert isinstance(speed, (int, float)), f"Expected numeric, got {type(speed)}"
            assert speed >= 0, f"Speed should be non-negative, got {speed}"
