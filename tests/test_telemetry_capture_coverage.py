"""Coverage-focused tests for telemetry_capture untested methods."""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.core.security import GameProcessStatus
from src.core.telemetry_capture import (
    FrameData,
    TelemetryCapture,
)


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

    def test_no_notify_for_session_restart(self):
        capture = TelemetryCapture(hz=10.0)
        capture._stop_reason = "session_restart"
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
        capture._session_start_time = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
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
        capture._session_start_time = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
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

        await capture.stop_capture("manual")

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

        await capture.stop_capture("session_end")

        mock_reader.close.assert_called_once()
        assert capture._readers == {}

    @pytest.mark.asyncio
    async def test_stop_closes_diag_file(self):
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        capture._frames = []
        mock_file = MagicMock()
        capture._diag_file = mock_file

        await capture.stop_capture("manual")

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


class TestValidityOnlyCaptureLoop:
    def test_configure_reopens_debug_log_in_new_output_directory(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        capture = TelemetryCapture(output_dir=str(old_dir), debug_logs=True)
        capture._running = True
        old_dir.mkdir()
        old_log = old_dir / "active.log"
        capture._diag_file = old_log.open("w", encoding="utf-8")

        capture.configure(output_dir=str(new_dir), debug_logs=True)

        assert capture._diag_file is not None
        assert capture._diag_file.closed is False
        assert capture._diag_file.name.startswith(str(new_dir))
        capture._diag_file.close()

    @pytest.mark.asyncio
    async def test_validity_only_samples_advance_without_retaining_frames(self):
        capture = TelemetryCapture(hz=1000.0, record_frames=False)
        capture._running = True
        reader = MagicMock()
        reader.size = 4
        capture._readers = {"physics": reader}
        seen = []

        def sample(frame_number):
            seen.append(frame_number)
            capture._last_sample_had_data = True
            if frame_number == 2:
                capture._running = False
            return FrameData(
                timestamp="2026-01-01T00:00:00Z",
                frame_number=frame_number,
                physics={"speed_kmh": 10.0},
            )

        with (
            patch.object(capture, "_reconnect_missing"),
            patch.object(capture, "_capture_frame", side_effect=sample),
            patch(
                "src.core.telemetry_capture.is_game_running",
                return_value=GameProcessStatus.RUNNING,
            ),
        ):
            await capture._capture_loop()

        assert seen == [0, 1, 2]
        assert capture.get_frames() == []

    @pytest.mark.parametrize(
        ("record_frames", "awaiting_boundary"),
        ((False, False), (True, True)),
    )
    @pytest.mark.asyncio
    async def test_idle_timeout_waits_for_full_recording_to_start(
        self,
        record_frames,
        awaiting_boundary,
    ):
        capture = TelemetryCapture(hz=1000.0, record_frames=record_frames)
        capture.IDLE_TIMEOUT_SECONDS = 0.0
        capture._recording_awaiting_boundary = awaiting_boundary
        capture._running = True
        capture._readers = {"physics": MagicMock(size=4)}
        seen = []

        def sample(frame_number):
            seen.append(frame_number)
            capture._last_sample_had_data = True
            if frame_number == 1:
                capture._running = False
            return FrameData(
                timestamp="2026-01-01T00:00:00Z",
                frame_number=frame_number,
                physics={"speed_kmh": 0.0},
            )

        with (
            patch.object(capture, "_reconnect_missing"),
            patch.object(capture, "_capture_frame", side_effect=sample),
            patch(
                "src.core.telemetry_capture.is_game_running",
                return_value=GameProcessStatus.RUNNING,
            ),
        ):
            await capture._capture_loop()

        assert seen == [0, 1]
        assert capture.get_stop_reason() is None
        assert capture._idle_since is None

    def test_recording_arms_at_clean_boundary(self):
        capture = TelemetryCapture(record_frames=False)
        capture._running = True
        capture.set_record_frames(True)
        capture._frames = [FrameData("2026-01-01T00:00:00Z", 50, {"speed_kmh": 100.0})]

        capture.record_lap_boundary(120000, 1, "OUTLAP")

        assert capture.get_frames() == []
        assert capture.get_lap_boundaries() == []
        capture._frames = [
            FrameData("2026-01-01T00:00:01Z", 80, {"speed_kmh": 100.0}),
            FrameData("2026-01-01T00:00:02Z", 81, {"speed_kmh": 100.0}),
        ]
        capture.record_lap_boundary(90000, 2, "VALID")
        # The absolute sampler is at frame 81, but the retained analyzer
        # buffer starts at 80 after the armed outlap was discarded.
        assert capture.get_lap_boundaries()[0].frame_index == 1
        assert capture.get_lap_boundaries()[0].lap_type == "VALID"

    @pytest.mark.asyncio
    async def test_armed_recording_starts_on_authoritative_lap_timer_reset(self):
        """A pit outlap timer reset starts capture without an ACE log lap."""
        capture = TelemetryCapture(hz=1000.0, record_frames=True)
        capture._recording_awaiting_boundary = True
        capture._running = True
        capture._readers = {"physics": MagicMock(size=4)}
        lap_times = [78_000, 104, 210]

        def sample(frame_number):
            capture._last_sample_had_data = True
            if frame_number == len(lap_times) - 1:
                capture._running = False
            return FrameData(
                timestamp=f"2026-01-01T00:00:0{frame_number}Z",
                frame_number=frame_number,
                physics={"speed_kmh": 100.0},
                graphics={
                    "status_name": "AC_LIVE",
                    "current_lap_time_ms": lap_times[frame_number],
                },
            )

        with (
            patch.object(capture, "_reconnect_missing"),
            patch.object(capture, "_capture_frame", side_effect=sample),
            patch(
                "src.core.telemetry_capture.is_game_running",
                return_value=GameProcessStatus.RUNNING,
            ),
        ):
            await capture._capture_loop()

        assert capture._recording_awaiting_boundary is False
        assert [frame.frame_number for frame in capture.get_frames()] == [1, 2]

    def test_armed_recording_keeps_race_from_standing_start(self):
        capture = TelemetryCapture(record_frames=True)
        capture._recording_awaiting_boundary = True
        frame = FrameData(
            "2026-01-01T00:00:00Z",
            0,
            {"speed_kmh": 0.0},
            graphics={
                "status_name": "AC_LIVE",
                "completed_laps": 0,
                "current_lap_time_ms": 0,
            },
            static={"session_name": "Race"},
        )

        assert capture._start_recording_at_timing_boundary(frame) is True
        assert capture._recording_awaiting_boundary is False

    @pytest.mark.parametrize("session_name", ["Practice", "Qualifying"])
    def test_armed_recording_still_waits_through_non_race_outlap(self, session_name):
        capture = TelemetryCapture(record_frames=True)
        capture._recording_awaiting_boundary = True
        frame = FrameData(
            "2026-01-01T00:00:00Z",
            0,
            {"speed_kmh": 0.0},
            graphics={
                "status_name": "AC_LIVE",
                "completed_laps": 0,
                "current_lap_time_ms": 0,
            },
            static={"session_name": session_name},
        )

        assert capture._start_recording_at_timing_boundary(frame) is False
        assert capture._recording_awaiting_boundary is True

    def test_armed_recording_does_not_keep_partial_first_race_lap(self):
        capture = TelemetryCapture(record_frames=True)
        capture._recording_awaiting_boundary = True
        frame = FrameData(
            "2026-01-01T00:00:00Z",
            0,
            {"speed_kmh": 100.0},
            graphics={
                "status_name": "AC_LIVE",
                "completed_laps": 0,
                "current_lap_time_ms": 20_000,
            },
            static={"session_name": "Race"},
        )

        assert capture._start_recording_at_timing_boundary(frame) is False
        assert capture._recording_awaiting_boundary is True

    def test_armed_recording_ignores_spline_wrap_without_timer_reset(self):
        capture = TelemetryCapture(record_frames=True)
        capture._recording_awaiting_boundary = True
        first = FrameData(
            "2026-01-01T00:00:00Z",
            0,
            {"speed_kmh": 100.0},
            graphics={
                "status_name": "AC_LIVE",
                "current_lap_time_ms": 20_000,
                "normalized_car_position": 0.95,
            },
        )
        second = FrameData(
            "2026-01-01T00:00:01Z",
            1,
            {"speed_kmh": 100.0},
            graphics={
                "status_name": "AC_LIVE",
                "current_lap_time_ms": 20_100,
                "normalized_car_position": 0.02,
            },
        )

        assert capture._start_recording_at_timing_boundary(first) is False
        assert capture._start_recording_at_timing_boundary(second) is False
        assert capture._recording_awaiting_boundary is True

    @pytest.mark.asyncio
    async def test_wrapper_preserves_original_exception_reason(self):
        capture = TelemetryCapture(debug_logs=False)
        with patch.object(capture, "_capture_loop", AsyncMock(side_effect=RuntimeError("boom"))):
            await capture._capture_loop_wrapper()

        assert capture.get_stop_reason() == "unhandled_exception: boom"
        assert capture.is_capturing() is False
