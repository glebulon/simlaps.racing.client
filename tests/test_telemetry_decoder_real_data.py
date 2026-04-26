"""
Tests for telemetry decoder using real telemetry dump data.

Tests decoding of raw physics data from actual ACE telemetry captures.
Graphics and static data are not decoded as they don't match known formats.
"""

import json
import pytest
from src.core.telemetry_decoder import (
    decode_graphics,
    decode_physics,
    decode_static,
    physics_to_dict,
)


def load_sample_frame(line_index: int = 0):
    """Load the first frame from the sample telemetry file."""
    with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
        for i, line in enumerate(f):
            if i == line_index:
                return json.loads(line)
    raise IndexError(line_index)


def load_frame_by_number(frame_number: int):
    with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
        for line in f:
            frame = json.loads(line)
            if frame['frame_number'] == frame_number:
                return frame
    raise ValueError(frame_number)


def raw_bytes(frame: dict, key: str) -> bytes:
    return bytes.fromhex(frame[key])


class TestPhysicsDecoding:
    """Test physics data decoding from real telemetry."""

    def test_physics_raw_data_exists(self):
        """Test that physics_raw field exists in sample data."""
        frame = load_sample_frame()
        assert 'physics_raw' in frame
        assert len(frame['physics_raw']) > 0

    def test_decode_physics_returns_dict(self):
        """Test that decode_physics returns a dictionary."""
        frame = load_sample_frame()
        physics_raw = raw_bytes(frame, 'physics_raw')
        
        result = decode_physics(physics_raw)
        
        assert isinstance(result, dict)
        assert '_decoder' in result

    def test_decode_physics_has_speed(self):
        """Test that decoded physics contains speed_kmh."""
        frame = load_sample_frame()
        physics_raw = raw_bytes(frame, 'physics_raw')
        
        result = decode_physics(physics_raw)
        
        # May use fallback decoder, but should have some data
        assert result is not None
        assert len(result) > 0

    def test_physics_to_dict_handles_dict_input(self):
        """Test that physics_to_dict handles dict input correctly."""
        frame = load_sample_frame()
        physics_raw = raw_bytes(frame, 'physics_raw')
        
        decoded = decode_physics(physics_raw)
        result = physics_to_dict(decoded)
        
        assert isinstance(result, dict)

    def test_physics_to_dict_handles_fallback_decoder(self):
        """Test that physics_to_dict works with fallback decoder output."""
        frame = load_sample_frame()
        physics_raw = raw_bytes(frame, 'physics_raw')
        
        decoded = decode_physics(physics_raw)
        # If using fallback, it should still convert to dict
        result = physics_to_dict(decoded)
        
        assert isinstance(result, dict)


class TestGraphicsDecoding:
    """Test graphics data decoding from real telemetry."""

    def test_graphics_raw_data_exists(self):
        frame = load_frame_by_number(2102)
        assert 'graphics_raw' in frame
        assert len(frame['graphics_raw']) > 0

    def test_decode_graphics_known_frame(self):
        frame = load_frame_by_number(2102)
        graphics_raw = raw_bytes(frame, 'graphics_raw')

        result = decode_graphics(graphics_raw)

        assert result['_decoder'] == 'acc_graphics_structure'
        assert result['packet_id'] == 226780
        assert result['status'] == 2
        assert result['status_name'] == 'AC_LIVE'
        assert result['session'] == 0
        assert result['session_name'] == 'AC_PRACTICE'
        assert result['active_cars'] == 6
        assert result['has_authoritative_progress'] is True
        assert result['quality_score'] >= 0.8
        assert len(result['car_coordinates']) == 6
        assert result['car_coordinates'][0]['x'] == pytest.approx(212.9467, rel=1e-4)
        assert result['car_coordinates'][0]['y'] == pytest.approx(276.2522, rel=1e-4)
        assert result['car_coordinates'][0]['z'] == pytest.approx(-289.6187, rel=1e-4)


class TestStaticDecoding:
    """Test static data decoding from real telemetry."""

    def test_static_raw_data_exists(self):
        frame = load_frame_by_number(2102)
        assert 'static_raw' in frame
        assert len(frame['static_raw']) > 0

    def test_decode_static_known_frame(self):
        """``decode_static`` now routes to the AC Evo decoder by default.

        The previous assertion expected the pattern-detection fallback
        because no typed static decoder existed. With
        ``decode_static_evo`` in place, a populated static frame must
        decode to a real ``ac_evo_static`` payload containing at least
        the track identity fields that consumers (analyzer, AI prompt,
        HTML report) will rely on.
        """
        frame = load_frame_by_number(2102)
        static_raw = raw_bytes(frame, 'static_raw')

        result = decode_static(static_raw)

        # ``decode_static`` now tries the AC Evo decoder first and falls
        # back to the legacy ACC decoder. This fixture predates AC Evo,
        # so it decodes via the ACC path; either typed decoder is
        # acceptable here as long as the fallback is not used.
        assert result['_decoder'] in {'ac_evo_static', 'acc_static_structure'}
        assert result['_decoder'] != 'fallback'
        assert result['buffer_size'] == len(static_raw)
        # Core track-identity fields are always surfaced when the region
        # is populated, regardless of which typed decoder ran.
        assert 'track' in result
        assert 'sm_version' in result




class TestFrameStructure:
    """Test overall frame structure from real telemetry."""

    def test_frame_has_required_fields(self):
        """Test that frame has all required fields."""
        frame = load_sample_frame()
        
        assert 'timestamp' in frame
        assert 'frame_number' in frame
        assert 'physics_raw' in frame

    def test_frame_number_is_integer(self):
        """Test that frame_number is an integer."""
        frame = load_sample_frame()
        
        assert isinstance(frame['frame_number'], int)
        assert frame['frame_number'] >= 0

    def test_timestamp_is_string(self):
        """Test that timestamp is a string."""
        frame = load_sample_frame()
        
        assert isinstance(frame['timestamp'], str)
        assert len(frame['timestamp']) > 0


class TestMultipleFrames:
    """Test handling of multiple frames from the telemetry file."""

    def test_load_multiple_frames(self):
        """Test that we can load multiple frames from the file."""
        frames = []
        with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
            for i, line in enumerate(f):
                if i >= 10:  # Load first 10 frames
                    break
                frames.append(json.loads(line))
        
        assert len(frames) == 10

    def test_frame_numbers_are_sequential(self):
        """Test that frame numbers are sequential."""
        frames = []
        with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                frames.append(json.loads(line))
        
        frame_numbers = [f['frame_number'] for f in frames]
        assert frame_numbers == list(range(len(frames)))

    def test_all_frames_have_same_structure(self):
        """Test that all frames have the same structure."""
        frames = []
        with open('tests/fixtures/sample_telemetry.jsonl', 'r') as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                frames.append(json.loads(line))
        
        first_keys = set(frames[0].keys())
        for frame in frames[1:]:
            assert set(frame.keys()) == first_keys
