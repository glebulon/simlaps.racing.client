"""Coverage-focused tests for telemetry_capture untested methods."""

import asyncio
import json
import os
import tempfile
from unittest.mock import MagicMock, Mock, patch, AsyncMock

import pytest

from src.core.telemetry_capture import (
    FrameData,
    RegionReader,
    TelemetryCapture,
    REGIONS,
)
from src.models import SharedSessionManager


class TestShouldNotifyStopCallback:
    """Test _should_notify_stop_callback edge cases."""

    def test_notify_for_unexpected_stop(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "heartbeat_timeout"
        assert capture._should_notify_stop_callback() is True

    def test_no_notify_for_manual_stop(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "manual"
        assert capture._should_notify_stop_callback() is False

    def test_no_notify_for_session_end(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "session_end"
        assert capture._should_notify_stop_callback() is False

    def test_no_notify_for_disabled(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "disabled"
        assert capture._should_notify_stop_callback() is False

    def test_no_notify_for_app_close(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "app_close"
        assert capture._should_notify_stop_callback() is False

    def test_notify_for_none_reason_while_running(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = None
        assert capture._should_notify_stop_callback() is False

    def test_notify_for_game_not_running(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "game_not_running"
        assert capture._should_notify_stop_callback() is True


class TestSaveRawDump:
    """Test save_raw_dump method."""

    def test_save_raw_dump_success(self):
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [
            FrameData(
                timestamp="2024-01-01T00:00:00Z",
                frame_number=0,
                physics={"speed_kmh": 100.0},
                physics_raw="aabbccdd",
                graphics_raw="11223344",
            ),
            FrameData(
                timestamp="2024-01-01T00:00:01Z",
                frame_number=1,
                physics={"speed_kmh": 105.0},
                physics_raw="ddeeff00",
            ),
        ]

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = f.name

        try:
            result = capture.save_raw_dump(path)
            assert result is True

            with open(path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f]

            assert len(lines) == 2
            assert lines[0]["frame_number"] == 0
            assert lines[0]["physics_raw"] == "aabbccdd"
            assert lines[0]["graphics_raw"] == "11223344"
            assert lines[1]["frame_number"] == 1
            assert lines[1]["graphics_raw"] is None
        finally:
            os.unlink(path)

    def test_save_raw_dump_empty_frames(self):
        capture = TelemetryCapture(hz=10.0)
        capture._frames = []

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = f.name

        try:
            result = capture.save_raw_dump(path)
            assert result is True
            with open(path, "r", encoding="utf-8") as f:
                assert f.read() == ""
        finally:
            os.unlink(path)

    def test_save_raw_dump_failure(self):
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]

        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = capture.save_raw_dump("some_path.jsonl")
        assert result is False


class TestExportToJsonl:
    """Test export_to_jsonl method."""

    def test_export_success(self):
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test_prefix"
        capture._session_start_time = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        capture._readers = {}
        capture._region_paths = {}
        capture._frames = [
            FrameData(
                timestamp="2024-01-01T00:00:00Z",
                frame_number=0,
                physics={"speed_kmh": 100.0},
                physics_raw="aabbccdd",
            )
        ]

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = f.name

        try:
            result = capture.export_to_jsonl(path)
            assert result is True

            with open(path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f]

            assert lines[0]["_record_type"] == "meta"
            assert lines[1]["_record_type"] == "frame"
            assert lines[1]["_frame"] == 1
        finally:
            os.unlink(path)

    def test_export_with_metadata(self):
        from src.core.telemetry_capture import CaptureMetadata

        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        capture._session_start_time = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        capture._readers = {}
        capture._region_paths = {}
        capture._metadata = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024},
        )
        capture._frames = []

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = f.name

        try:
            result = capture.export_to_jsonl(path)
            assert result is True

            with open(path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f]

            assert lines[0]["capture_metadata"]["hz"] == 10.0
        finally:
            os.unlink(path)

    def test_export_failure(self):
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]

        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = capture.export_to_jsonl("some_path.jsonl")
        assert result is False


class TestClear:
    """Test clear method."""

    def test_clear_removes_frames(self):
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]
        capture._metadata = Mock()

        capture.clear()

        assert capture._frames == []
        assert capture._metadata is None


class TestConnectRegions:
    """Test _connect_regions and _reconnect_missing."""

    @patch("src.core.telemetry_capture.RegionReader")
    def test_connect_regions_success(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "Local\\acevo_pmf_physics"
        mock_reader_cls.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        readers = capture._connect_regions()

        assert "physics" in readers
        assert capture._region_paths["physics"] == "Local\\acevo_pmf_physics"

    @patch("src.core.telemetry_capture.RegionReader")
    def test_connect_regions_failure(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.open.return_value = False
        mock_reader_cls.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        readers = capture._connect_regions()

        assert "physics" not in readers

    @patch("src.core.telemetry_capture.RegionReader")
    def test_reconnect_missing_adds_new_reader(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "Local\\acevo_pmf_graphics"
        mock_reader_cls.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        existing = {"physics": MagicMock()}
        capture._reconnect_missing(existing)

        assert "graphics" in existing
        assert capture._region_paths.get("graphics") == "Local\\acevo_pmf_graphics"

    @patch("src.core.telemetry_capture.RegionReader")
    def test_reconnect_missing_no_change_when_already_present(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader_cls.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        existing_reader = MagicMock()
        existing = {"physics": existing_reader}
        capture._reconnect_missing(existing)

        assert existing["physics"] is existing_reader
        assert mock_reader_cls.call_count == 2  # Called for graphics and static


class TestStopCapture:
    """Test stop_capture method."""

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = False
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]

        frames = await capture.stop_capture("manual")

        assert frames == capture._frames
        assert capture._stop_reason is None  # Not set when not running

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        capture._stop_reason = None
        capture._frames = []

        # Create a mock task that takes a while
        async def slow_task():
            await asyncio.sleep(10)

        capture._task = asyncio.create_task(slow_task())

        frames = await capture.stop_capture("manual")

        assert capture._running is False
        assert capture._stop_reason == "manual"
        assert capture._task is None

    @pytest.mark.asyncio
    async def test_stop_closes_readers(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        mock_reader = MagicMock()
        capture._readers = {"physics": mock_reader}
        capture._frames = []

        frames = await capture.stop_capture("session_end")

        mock_reader.close.assert_called_once()
        assert capture._readers == {}

    @pytest.mark.asyncio
    async def test_stop_closes_diag_file(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        capture._frames = []
        mock_file = MagicMock()
        capture._diag_file = mock_file

        frames = await capture.stop_capture("manual")

        mock_file.close.assert_called_once()
        assert capture._diag_file is None


class TestStartCapture:
    """Test start_capture method."""

    @pytest.mark.asyncio
    async def test_start_when_already_running(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = True

        result = await capture.start_capture()

        assert result is True

    @pytest.mark.asyncio
    async def test_start_initializes_state(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = False

        with patch.object(capture, "_connect_regions", return_value={}):
            result = await capture.start_capture()

        assert result is True
        assert capture._running is True
        assert capture._frames == []
        assert capture._lap_boundaries == []
        assert capture._metadata is None
        assert capture._stop_reason is None
        assert capture._output_prefix is not None

    @pytest.mark.asyncio
    async def test_start_with_debug_logs_opens_diag_file(self):
        capture = TelemetryCapture(hz=10.0, debug_logs=True)
        capture._running = False
        capture._output_dir = tempfile.mkdtemp()

        try:
            with patch.object(capture, "_connect_regions", return_value={}):
                result = await capture.start_capture()

            assert result is True
            assert capture._diag_file is not None
            capture._diag_file.close()
        finally:
            import shutil
            shutil.rmtree(capture._output_dir, ignore_errors=True)


class TestCompatFrameRecord:
    """Test _build_compat_frame_record with graphics/static branches."""

    def test_frame_record_with_graphics_and_static(self):
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"

        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0},
            physics_raw="aabbccdd",
            graphics={"session_current_lap": 1},
            graphics_raw="11223344",
            static={"track": "spa"},
            static_raw="55667788",
        )

        record = capture._build_compat_frame_record(frame)

        assert record["regions"]["graphics"]["raw_hex"] == "11223344"
        assert record["regions"]["graphics"]["session_current_lap"] == 1
        assert record["regions"]["static"]["raw_hex"] == "55667788"
        assert record["regions"]["static"]["track"] == "spa"

    def test_frame_record_without_raw_hex(self):
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"

        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0},
        )

        record = capture._build_compat_frame_record(frame)

        assert "graphics" not in record["regions"]
        assert "static" not in record["regions"]

    def test_frame_record_non_dict_payload(self):
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"

        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics=[1, 2, 3],  # non-dict payload
            physics_raw="aabbccdd",
        )

        record = capture._build_compat_frame_record(frame)

        assert record["regions"]["physics"]["value"] == [1, 2, 3]


class TestCloseReaders:
    """Test _close_readers method."""

    def test_close_all_readers(self):
        capture = TelemetryCapture(hz=10.0)
        mock_reader1 = MagicMock()
        mock_reader2 = MagicMock()
        capture._readers = {"physics": mock_reader1, "graphics": mock_reader2}

        capture._close_readers()

        mock_reader1.close.assert_called_once()
        mock_reader2.close.assert_called_once()
        assert capture._readers == {}

    def test_close_empty_readers(self):
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {}

        capture._close_readers()

        assert capture._readers == {}
