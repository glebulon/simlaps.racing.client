"""
Comprehensive tests for telemetry capture with mock shared memory.

Tests shared memory region reading, capture loop, error handling, and metadata.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.core.telemetry_capture import (
    RegionReader,
    TelemetryCapture,
    REGIONS,
    FrameData,
    CaptureMetadata,
)
from datetime import datetime, timezone


class TestRegionReader:
    """Test shared memory region reader."""

    def test_region_reader_initialization(self):
        """Test region reader initialization."""
        reader = RegionReader("test_region", 1024)
        
        assert reader.name == "test_region"
        assert reader.size == 1024
        assert reader._handle is None
        assert reader._view is None
        assert reader._path_used is None

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_success(self, mock_kernel32):
        """Test successful region reader open."""
        # Mock successful open
        mock_handle = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = MagicMock()
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is True
        assert reader._handle == mock_handle
        assert reader._view is not None

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_failure(self, mock_kernel32):
        """Test region reader open failure."""
        # Mock failed open
        mock_kernel32.OpenFileMappingW.return_value = 0
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is False
        assert reader._handle is None

    @patch('src.core.telemetry_capture.kernel32')
    @patch('src.core.telemetry_capture.ctypes')
    def test_region_reader_read_raw(self, mock_ctypes, mock_kernel32):
        """Test reading raw bytes from region."""
        # Mock successful open and read
        mock_handle = MagicMock()
        mock_view = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        test_data = b'\x00\x01\x02\x03' * 256  # 1024 bytes
        mock_ctypes.string_at.return_value = test_data
        
        reader = RegionReader("test_region", 1024)
        reader.open()
        result = reader.read_raw()
        
        assert result == test_data
        assert len(result) == 1024

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_read_raw_not_open(self, mock_kernel32):
        """Test reading from unopened region raises error."""
        reader = RegionReader("test_region", 1024)
        
        with pytest.raises(RuntimeError):
            reader.read_raw()

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_close(self, mock_kernel32):
        """Test closing region reader."""
        mock_handle = MagicMock()
        mock_view = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        reader = RegionReader("test_region", 1024)
        reader.open()
        reader.close()
        
        assert reader._handle is None
        assert reader._view is None
        mock_kernel32.UnmapViewOfFile.assert_called_once()
        mock_kernel32.CloseHandle.assert_called_once()


class TestTelemetryCapture:
    """Test telemetry capture system."""

    def test_capture_initialization(self):
        """Test telemetry capture initialization."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture._hz == 10.0
        assert capture._frames == []
        assert capture._running is False
        assert capture._readers == {}
        assert capture._lap_boundaries == []  # Empty list is fine, tuples only added when recording

    def test_capture_frame_count(self):
        """Test getting frame count."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_frame_count() == 0

    def test_capture_is_capturing(self):
        """Test checking if capturing."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.is_capturing() is False

    def test_capture_get_stop_reason(self):
        """Test getting stop reason."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_stop_reason() is None

    def test_capture_get_output_prefix(self):
        """Test getting output prefix."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_output_prefix() is None

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_decoding(self, mock_kernel32):
        """Test single frame capture and decoding."""
        # Mock region reader
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert frame.frame_number == 0
        assert frame.physics is not None or frame.physics == {}

    def test_capture_lap_boundary_recording(self):
        """Test recording lap boundaries."""
        capture = TelemetryCapture(hz=10.0)
        
        # Add some frames
        capture._frames = [
            FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=i, physics={})
            for i in range(10)
        ]
        
        capture.record_lap_boundary()
        
        assert len(capture.get_lap_boundaries()) == 1
        assert capture.get_lap_boundaries()[0][0] == 9  # Last frame index

    def test_capture_get_lap_boundaries(self):
        """Test getting lap boundaries."""
        capture = TelemetryCapture(hz=10.0)
        capture._lap_boundaries = [(10, None), (20, None), (30, None)]
        
        boundaries = capture.get_lap_boundaries()
        
        assert boundaries == [(10, None), (20, None), (30, None)]

    def test_capture_metadata_creation(self):
        """Test capture metadata creation."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        capture._region_paths = {"physics": "Local\\acevo_pmf_physics"}
        capture._session_start_time = datetime.now(timezone.utc)
        
        meta = capture._build_compat_meta_record()
        
        assert meta["_record_type"] == "meta"
        assert meta["_hz"] == 10.0
        assert "physics" in meta["_regions_known"]

    def test_capture_frame_record_creation(self):
        """Test frame record creation for compatibility."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0},
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        assert record["_frame"] == 1
        assert "physics" in record["regions"]
        assert record["regions"]["physics"]["size"] == 1024

    def test_capture_get_frames(self):
        """Test getting captured frames."""
        capture = TelemetryCapture(hz=10.0)
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={}
        )
        capture._frames = [frame]
        
        frames = capture.get_frames()
        
        assert len(frames) == 1
        assert frames[0] == frame

    def test_capture_get_metadata(self):
        """Test getting capture metadata."""
        capture = TelemetryCapture(hz=10.0)
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        capture._metadata = meta
        
        result = capture.get_metadata()
        
        assert result == meta


class TestCaptureEdgeCases:
    """Test capture edge cases and error handling."""

    def test_capture_with_no_regions(self):
        """Test capture when no regions are available."""
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert frame.frame_number == 0
        assert frame.physics == {}

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_with_read_error(self, mock_kernel32):
        """Test capture when read fails."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.side_effect = Exception("Read error")
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed on error
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_with_incomplete_read(self, mock_kernel32):
        """Test capture when read returns incomplete data."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 500  # Incomplete
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed on incomplete read
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_decoder.decode_physics', side_effect=Exception("Decode error"))
    def test_capture_with_decode_error(self, mock_decode):
        """Test capture when decoding fails."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert "error" in frame.physics
        # Reader should NOT be removed on decode error (temporary corruption)
        assert "physics" in capture._readers


class TestCaptureIntegration:
    """Test capture integration scenarios."""

    def test_make_output_prefix(self):
        """Test output prefix generation."""
        capture = TelemetryCapture(hz=10.0)
        
        prefix = capture._make_output_prefix()
        
        assert prefix is not None
        assert len(prefix) > 0
        # Format should be MM-DD-HH-MM-SS
        assert len(prefix.split("-")) == 5

    def test_set_on_stop_callback(self):
        """Test setting stop callback."""
        capture = TelemetryCapture(hz=10.0)
        
        callback = Mock()
        capture.set_on_stop_callback(callback)
        
        assert capture._on_stop_callback == callback

    def test_regions_config(self):
        """All three AC Evo SHM regions are wired up for capture.

        Physics has a typed decoder; graphics and static are captured as raw
        bytes for offline reverse-engineering. Names follow the AC Evo
        ``acevo_pmf_*`` convention from SharedFileOut.h.
        """
        assert REGIONS["physics"] == ("acevo_pmf_physics", 1024)
        assert REGIONS["graphics"][0] == "acevo_pmf_graphics"
        assert REGIONS["graphics"][1] >= 2048, "graphics buffer must fit SPageFileGraphicEvo"
        assert REGIONS["static"][0] == "acevo_pmf_static"
        assert REGIONS["static"][1] >= 1024


class TestFrameData:
    """Test FrameData dataclass."""

    def test_frame_data_creation(self):
        """Test creating FrameData."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0}
        )
        
        assert frame.timestamp == "2024-01-01T00:00:00Z"
        assert frame.frame_number == 0
        assert frame.physics == {"speed_kmh": 100.0}
        assert frame.physics_raw is None

    def test_frame_data_to_dict(self):
        """Test converting FrameData to dict."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0}
        )
        
        result = frame.to_dict()
        
        assert isinstance(result, dict)
        assert result["timestamp"] == "2024-01-01T00:00:00Z"
        assert result["frame_number"] == 0


class TestCaptureMetadata:
    """Test CaptureMetadata dataclass."""

    def test_metadata_creation(self):
        """Test creating CaptureMetadata."""
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        
        assert meta.captured_at == "2024-01-01T00:00:00Z"
        assert meta.hz == 10.0
        assert meta.regions_found == ["physics"]

    def test_metadata_to_dict(self):
        """Test converting CaptureMetadata to dict."""
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        
        result = meta.to_dict()
        
        assert isinstance(result, dict)
        assert result["captured_at"] == "2024-01-01T00:00:00Z"
        assert result["hz"] == 10.0


class TestRegionReaderEdgeCases:
    """Test RegionReader edge cases."""

    @patch('src.core.telemetry_capture.sys')
    def test_region_reader_open_non_windows(self, mock_sys):
        """Test RegionReader.open on non-Windows platform."""
        mock_sys.platform = "linux"
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is False

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_close_exception_handling(self, mock_kernel32):
        """Test RegionReader.close with exception handling."""
        mock_kernel32.UnmapViewOfFile.side_effect = Exception("Unmap failed")
        mock_kernel32.CloseHandle.side_effect = Exception("Close failed")
        
        reader = RegionReader("test_region", 1024)
        reader._handle = MagicMock()
        reader._view = MagicMock()
        
        # Should not raise exception
        reader.close()
        
        assert reader._view is None
        assert reader._handle is None


class TestTelemetryCaptureEdgeCases:
    """Test TelemetryCapture edge cases."""

    @patch('src.core.telemetry_capture.os')
    def test_save_raw_dump(self, mock_os):
        """Test saving raw dump to file."""
        mock_os.makedirs.return_value = None
        
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [
            FrameData(
                timestamp="2024-01-01T00:00:00Z",
                frame_number=0,
                physics={"speed_kmh": 100.0},
                physics_raw="aabbccdd"
            )
        ]
        
        # Mock file write
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__ = Mock()
            mock_open.return_value.__exit__ = Mock()
            mock_open.return_value.write = Mock()
            
            result = capture.save_raw_dump("test_dump.jsonl")
            
            assert result is True

    @patch('src.core.telemetry_capture.os')
    def test_save_raw_dump_error(self, mock_os):
        """Test save_raw_dump with error."""
        mock_os.makedirs.side_effect = Exception("Dir error")
        
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]
        
        result = capture.save_raw_dump("test_dump.jsonl")
        
        assert result is False

    def test_build_compat_frame_record_non_dict_payload(self):
        """Test _build_compat_frame_record with non-dict payload."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics="string_value",  # Non-dict payload
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        assert record["regions"]["physics"]["value"] == "string_value"

    def test_build_compat_frame_record_none_payload(self):
        """Test _build_compat_frame_record with None payload."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics=None,
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        # Should handle None gracefully
        assert "physics" in record["regions"]

    def test_build_compat_meta_record_no_metadata(self):
        """Test _build_compat_meta_record without metadata."""
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {}
        capture._metadata = None
        capture._region_paths = {}
        
        record = capture._build_compat_meta_record()
        
        assert record["_record_type"] == "meta"
        assert record["_regions_found"] == []

    @pytest.mark.asyncio
    async def test_start_capture_already_running(self):
        """Test start_capture when already running."""
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        
        result = await capture.start_capture()
        
        assert result is True

    @pytest.mark.asyncio
    async def test_start_capture_initialization(self):
        """Test start_capture initialization."""
        capture = TelemetryCapture(hz=10.0)
        
        result = await capture.start_capture()
        
        assert result is True
        assert capture._running is True
        assert capture._frames == []
        assert capture._session_start_time is not None
        assert capture._output_prefix is not None
        assert capture._task is not None

    def test_close_readers(self):
        """Test _close_readers method."""
        mock_reader = MagicMock()
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        capture._close_readers()
        
        mock_reader.close.assert_called_once()
        assert capture._readers == {}

    def test_should_notify_stop_callback(self):
        """Test _should_notify_stop_callback logic."""
        capture = TelemetryCapture(hz=10.0)
        
        # Should notify for unexpected stops
        capture._stop_reason = "task_exception"
        assert capture._should_notify_stop_callback() is True
        
        # Should not notify for expected stops
        capture._stop_reason = "manual"
        assert capture._should_notify_stop_callback() is False
        capture._stop_reason = None
        assert capture._should_notify_stop_callback() is False
        capture._stop_reason = "session_end"
        assert capture._should_notify_stop_callback() is False

    @patch('src.core.telemetry_capture.RegionReader')
    def test_connect_regions(self, mock_reader_class):
        """Test _connect_regions method."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "test_path"
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        
        readers = capture._connect_regions()
        
        assert "physics" in readers
        assert capture._region_paths["physics"] == "test_path"

    @patch('src.core.telemetry_capture.RegionReader')
    def test_connect_regions_failure(self, mock_reader_class):
        """Test _connect_regions when region open fails."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = False
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        
        readers = capture._connect_regions()
        
        assert readers == {}

    @patch('src.core.telemetry_capture.RegionReader')
    def test_reconnect_missing(self, mock_reader_class):
        """Test _reconnect_missing method."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "test_path"
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        existing_readers = {}
        
        capture._reconnect_missing(existing_readers)
        
        assert "physics" in existing_readers
        assert capture._region_paths["physics"] == "test_path"

    @patch('src.core.telemetry_capture.RegionReader')
    def test_reconnect_missing_skips_existing(self, mock_reader_class):
        """Test _reconnect_missing skips regions already connected.

        All three SHM regions (physics, graphics, static) must be already
        present in ``existing_readers`` for this assertion to hold — the
        method's job is to fill in only the missing ones.
        """
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader_class.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        existing_readers = {
            "physics": MagicMock(),
            "graphics": MagicMock(),
            "static": MagicMock(),
        }

        capture._reconnect_missing(existing_readers)

        # All regions already present → no new RegionReader instances
        mock_reader_class.assert_not_called()

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_disconnected_reader(self, mock_kernel32):
        """Test _capture_frame with disconnected reader."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.side_effect = Exception("Disconnected")
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed after disconnection
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_duplicate_path(self, mock_kernel32):
        """Test RegionReader.open with duplicate path in candidates."""
        mock_handle = MagicMock()
        mock_view = MagicMock()
        
        # First call succeeds, second call would fail but shouldn't be attempted
        call_count = [0]
        def open_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_handle
            return 0
        
        mock_kernel32.OpenFileMappingW.side_effect = open_side_effect
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is True
        # Should only attempt open once for the successful path
        assert call_count[0] == 1
