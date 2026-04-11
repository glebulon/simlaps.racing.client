"""
Comprehensive tests for log parser with mock log data and edge cases.

Tests complete session parsing, state management, and error handling.
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from src.core.log_parser import LogParser
from src.models import SessionData, LapData


class TestLogParserInitialization:
    """Test log parser initialization."""

    def test_parser_initialization(self):
        """Test creating a log parser."""
        parser = LogParser()
        
        assert parser is not None
        assert parser.context is not None
        assert parser.sessions == []

    def test_parser_with_log_path(self, tmp_path):
        """Test creating parser with custom log path."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        
        parser = LogParser(log_path=str(log_file))
        
        assert parser.log_path == log_file


class TestLogParserFlow:
    """Test complete log parsing workflow."""

    @pytest.mark.asyncio
    async def test_parse_empty_log(self, tmp_path):
        """Test parsing an empty log file."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        
        parser = LogParser(log_path=str(log_file))
        result = await parser.parse_file()
        
        assert result is not None
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_parse_log_with_basic_lap(self, tmp_path):
        """Test parsing log with basic lap completion."""
        log_content = """[2024-01-01 12:00:00] [gameplay] [info] Game Started! GameModeType_PRACTICE | TestTrack | porsche_992_gt3_cup | GameModeSelectionWeatherType_Clear
[2024-01-01 12:00:01] [gameplay] [info] New lap carId abc123-456: 1:23.456
"""
        log_file = tmp_path / "lap.log"
        log_file.write_text(log_content)
        
        parser = LogParser(log_path=str(log_file))
        result = await parser.parse_file()
        
        assert result is not None
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_parse_log_with_multiple_laps(self, tmp_path):
        """Test parsing log with multiple laps."""
        log_content = """[2024-01-01 12:00:00] [gameplay] [info] Game Started! GameModeType_PRACTICE | TestTrack | porsche_992_gt3_cup | GameModeSelectionWeatherType_Clear
[2024-01-01 12:00:01] [gameplay] [info] New lap carId abc123-456: 1:23.456
[2024-01-01 12:00:02] [gameplay] [info] New lap carId abc123-456: 1:24.567
[2024-01-01 12:00:03] [gameplay] [info] New lap carId abc123-456: 1:22.345
"""
        log_file = tmp_path / "multilap.log"
        log_file.write_text(log_content)
        
        parser = LogParser(log_path=str(log_file))
        result = await parser.parse_file()
        
        assert result is not None
        assert isinstance(result, list)


class TestSessionState:
    """Test session state management."""

    def test_session_initialization(self):
        """Test session data initialization."""
        session = SessionData()
        
        assert session is not None
        assert session.laps == []

    def test_parser_context_initialization(self):
        """Test parser context initialization."""
        parser = LogParser()
        
        assert parser.context is not None
        assert parser.current_session is None


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_parse_nonexistent_log(self):
        """Test parsing non-existent log file."""
        parser = LogParser(log_path="/nonexistent/path/to/log.txt")
        result = await parser.parse_file()
        
        # Should handle missing file gracefully
        assert result is not None

    @pytest.mark.asyncio
    async def test_parse_malformed_log(self, tmp_path):
        """Test parsing malformed log entries."""
        log_content = """INVALID LINE FORMAT
Another invalid line
[2024-01-01 12:00:00] [gameplay] [info] Game Started! GameModeType_PRACTICE | TestTrack | porsche_992_gt3_cup | GameModeSelectionWeatherType_Clear
"""
        log_file = tmp_path / "malformed.log"
        log_file.write_text(log_content)
        
        parser = LogParser(log_path=str(log_file))
        result = await parser.parse_file()
        
        # Should handle malformed lines gracefully
        assert result is not None


class TestLapData:
    """Test lap data structures."""

    def test_lap_data_creation(self):
        """Test creating lap data with correct field names."""
        lap = LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=83456,
            lap_time_str="1:23.456",
            sector1_ms=45000,
            sector2_ms=48000,
            sector3_ms=-1,
            is_valid=True,
            tyre_compound="Dry"
        )
        
        assert lap.lap_number == 1
        assert lap.lap_time_ms == 83456
        assert lap.is_valid is True
        assert lap.tyre_compound == "Dry"

    def test_lap_data_to_dict(self):
        """Test converting lap data to dict."""
        lap = LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=83456,
            lap_time_str="1:23.456"
        )
        
        result = lap.to_dict()
        
        assert isinstance(result, dict)
        assert result["lap_number"] == 1
        assert result["lap_time_ms"] == 83456


class TestLogContext:
    """Test log context state."""

    def test_context_initialization(self):
        """Test creating log context."""
        from src.models.context import LogContext
        context = LogContext()
        
        assert context is not None

    def test_parser_context_attribute(self):
        """Test parser has context attribute."""
        parser = LogParser()
        
        assert hasattr(parser, 'context')
        assert parser.context is not None


class TestParserCallbacks:
    """Test parser callback functionality."""

    def test_parser_with_lap_callback(self):
        """Test parser with lap complete callback."""
        callback_called = []
        
        def on_lap(lap):
            callback_called.append(lap)
        
        parser = LogParser(on_lap_complete=on_lap)
        
        assert parser.on_lap_complete is not None
        assert parser.on_lap_complete == on_lap

    def test_parser_with_status_callback(self):
        """Test parser with status change callback."""
        callback_called = []
        
        def on_status(status):
            callback_called.append(status)
        
        parser = LogParser(on_status_change=on_status)
        
        assert parser.on_status_change is not None
        assert parser.on_status_change == on_status


class TestParserConfiguration:
    """Test parser configuration options."""

    def test_default_log_path(self):
        """Test default log path."""
        parser = LogParser()
        
        assert parser.log_path is not None
        assert isinstance(parser.log_path, Path)

    def test_custom_log_path(self, tmp_path):
        """Test custom log path."""
        log_file = tmp_path / "custom.log"
        log_file.write_text("")
        
        parser = LogParser(log_path=str(log_file))
        
        assert parser.log_path == log_file

    def test_log_buffer_initialization(self):
        """Test log buffer initialization."""
        parser = LogParser()
        
        assert hasattr(parser, 'log_buffer')
        assert isinstance(parser.log_buffer, list)
        assert parser.max_log_lines == 100_000


class TestLogParserHelpers:
    """Test log parser helper methods."""

    def test_parse_lap_time_ms_with_minutes(self):
        """Test parsing lap time with minutes."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("1:23.456")
        
        assert result == 83456  # 1*60*1000 + 23*1000 + 456

    def test_parse_lap_time_ms_seconds_only(self):
        """Test parsing lap time with seconds only."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("23.456")
        
        assert result == 23456  # 23*1000 + 456

    def test_parse_lap_time_ms_invalid(self):
        """Test parsing invalid lap time."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("invalid")
        
        assert result == 0

    def test_extract_line_timestamp_valid(self):
        """Test extracting timestamp from valid line."""
        parser = LogParser()
        result = parser._extract_line_timestamp("[2024-01-01 12:00:00] Some log message")
        
        assert result == "2024-01-01 12:00:00"

    def test_extract_line_timestamp_no_bracket(self):
        """Test extracting timestamp from line without bracket."""
        parser = LogParser()
        result = parser._extract_line_timestamp("Some log message without timestamp")
        
        assert result is None

    def test_extract_line_timestamp_empty_bracket(self):
        """Test extracting timestamp from line with empty bracket."""
        parser = LogParser()
        result = parser._extract_line_timestamp("[] Empty bracket")
        
        assert result is None

    def test_is_player_car_match(self):
        """Test checking if car is player car."""
        parser = LogParser()
        parser.context.car_uuid = "abc123"
        
        assert parser._is_player_car("abc123") is True

    def test_is_player_car_no_match(self):
        """Test checking non-player car."""
        parser = LogParser()
        parser.context.car_uuid = "abc123"
        
        assert parser._is_player_car("xyz789") is False

    def test_is_steam_id_valid(self):
        """Test valid Steam ID detection."""
        parser = LogParser()
        
        assert parser._is_steam_id("76561198321627695") is True

    def test_is_steam_id_invalid(self):
        """Test invalid Steam ID detection."""
        parser = LogParser()
        
        assert parser._is_steam_id("12345") is False
        assert parser._is_steam_id("7656") is False

    def test_clean_track_name_with_session_suffix(self):
        """Test cleaning track name with session suffix."""
        parser = LogParser()
        result = parser._clean_track_name("Spa Francorchamps Race")
        
        assert result == "Spa Francorchamps"

    def test_clean_track_name_with_at_symbol(self):
        """Test cleaning track name with @ symbol."""
        parser = LogParser()
        result = parser._clean_track_name("Spa@12:00 PM")
        
        assert result == "Spa"

    def test_clean_track_name_no_suffix(self):
        """Test cleaning track name without suffix."""
        parser = LogParser()
        result = parser._clean_track_name("Brands Hatch")
        
        assert result == "Brands Hatch"

    def test_reset_in_progress(self):
        """Test resetting in-progress lap."""
        parser = LogParser()
        parser._ip.physics_lap_num = 5
        parser._ip.splits = {0: 12345}
        
        parser._reset_in_progress()
        
        assert parser._ip.physics_lap_num is None
        assert parser._ip.splits == {}


class TestLogBufferOperations:
    """Test log buffer operations."""

    def test_add_to_log_buffer(self):
        """Test adding to log buffer."""
        parser = LogParser()
        parser._add_to_log_buffer("Test log line")
        
        assert "Test log line" in parser.log_buffer

    def test_add_to_log_buffer_overflow(self):
        """Test log buffer overflow handling."""
        parser = LogParser()
        parser.max_log_lines = 10
        
        for i in range(15):
            parser._add_to_log_buffer(f"Line {i}")
        
        assert len(parser.log_buffer) <= 10

    def test_get_log_buffer(self):
        """Test getting log buffer copy."""
        parser = LogParser()
        parser._add_to_log_buffer("Line 1")
        
        buffer = parser.get_log_buffer()
        
        assert buffer == ["Line 1"]
        assert buffer is not parser.log_buffer  # Should be a copy

    def test_clear_log_buffer(self):
        """Test clearing log buffer."""
        parser = LogParser()
        parser._add_to_log_buffer("Line 1")
        
        parser.clear_log_buffer()
        
        assert len(parser.log_buffer) == 0

    def test_export_logs_to_file_success(self, tmp_path):
        """Test exporting logs to file."""
        parser = LogParser()
        parser._add_to_log_buffer("Line 1")
        parser._add_to_log_buffer("Line 2")
        
        export_path = tmp_path / "export.log"
        result = parser.export_logs_to_file(str(export_path))
        
        assert result is True
        assert export_path.exists()

    def test_export_logs_to_file_failure(self, tmp_path):
        """Test exporting logs with invalid path."""
        parser = LogParser()
        
        result = parser.export_logs_to_file("/invalid/path/that/does/not/exist/export.log")
        
        assert result is False


class TestLogParserEmitters:
    """Test async emitter methods."""

    @pytest.mark.asyncio
    async def test_emit_status(self):
        """Test status emission."""
        callback_called = []
        
        async def on_status(status):
            callback_called.append(status)
        
        parser = LogParser(on_status_change=on_status)
        await parser._emit_status("Test status")
        
        assert len(callback_called) == 1
        assert callback_called[0] == "Test status"

    @pytest.mark.asyncio
    async def test_emit_lap(self):
        """Test lap emission."""
        callback_called = []
        
        async def on_lap(session, lap):
            callback_called.append((session, lap))
        
        parser = LogParser(on_lap_complete=on_lap)
        
        from src.models import SessionData, LapData, LapState
        session = SessionData(track="spa", car="porsche")
        lap = LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=100000,
            lap_time_str="1:40.000",
            lap_state=LapState.PUSH
        )
        
        await parser._emit_lap(session, lap)
        
        assert len(callback_called) == 1

    @pytest.mark.asyncio
    async def test_emit_game_status(self):
        """Test game status emission."""
        callback_called = []
        
        async def on_game_status(is_running):
            callback_called.append(is_running)
        
        parser = LogParser(on_game_status_change=on_game_status)
        await parser._emit_game_status(True)
        
        assert len(callback_called) == 1
        assert callback_called[0] is True

    @pytest.mark.asyncio
    async def test_emit_user_detected(self):
        """Test user detection emission."""
        callback_called = []
        
        async def on_user(user_id, name):
            callback_called.append((user_id, name))
        
        parser = LogParser(on_user_detected=on_user)
        await parser._emit_user_detected("76561198321627695", "TestUser")
        
        assert len(callback_called) == 1
        assert callback_called[0] == ("76561198321627695", "TestUser")

    @pytest.mark.asyncio
    async def test_emit_user_detected_no_callback(self):
        """Test user detection with no callback."""
        parser = LogParser()  # No callback set
        # Should not raise
        await parser._emit_user_detected("76561198321627695", "TestUser")


class TestLogParserStintHandling:
    """Test stint tracking functionality."""

    def test_ensure_stint_creates_new(self):
        """Test creating a new stint."""
        from src.models import SessionData
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser.context.tyre.set_all("SC")
        
        stint = parser._ensure_stint("SC")
        
        assert stint is not None
        assert stint.stint_number == 1
        assert stint.tyre_compound == "SC"

    def test_ensure_stint_reuses_existing(self):
        """Test reusing existing stint for same compound."""
        from src.models import SessionData
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser.context.tyre.set_all("SC")
        
        stint1 = parser._ensure_stint("SC")
        stint2 = parser._ensure_stint("SC")
        
        assert stint1 is stint2

    def test_ensure_stint_new_compound(self):
        """Test creating new stint for different compound."""
        from src.models import SessionData
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser.context.tyre.set_all("SC")
        
        stint1 = parser._ensure_stint("SC")
        parser.context.tyre.set_all("MC")
        stint2 = parser._ensure_stint("MC")
        
        assert stint1 is not stint2
        assert stint2.stint_number == 2

    def test_finalise_stints(self):
        """Test finalizing stints."""
        from src.models import SessionData
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser._ensure_stint("SC")
        parser._current_stint = None
        
        parser._finalise_stints()
        
        # Should not raise
        assert True


class TestLogParserLapState:
    """Test lap state determination logic."""

    def test_determine_lap_state_outlap(self):
        """Test outlap detection."""
        from src.models import InProgressLap, LapState
        parser = LogParser()
        ip = InProgressLap()
        ip.is_outlap = True
        ip.splits = {0: 30000, 1: 60000}
        
        state = parser._determine_lap_state(ip, [0, 1], [30000, 60000], 90000, "PRACTICE")
        
        assert state == LapState.OUTLAP

    def test_determine_lap_state_track_limit(self):
        """Test track limit invalidation."""
        from src.models import InProgressLap, LapState
        parser = LogParser()
        ip = InProgressLap()
        ip.has_track_limit_violation = True
        ip.splits = {0: 30000, 1: 60000}
        
        state = parser._determine_lap_state(ip, [0, 1], [30000, 60000], 90000, "PRACTICE")
        
        assert state == LapState.INVALID_TRACK_LIMIT

    def test_determine_lap_state_penalty(self):
        """Test penalty invalidation."""
        from src.models import InProgressLap, LapState
        parser = LogParser()
        ip = InProgressLap()
        ip.has_penalty = True
        ip.splits = {0: 30000, 1: 60000}
        
        state = parser._determine_lap_state(ip, [0, 1], [30000, 60000], 90000, "PRACTICE")
        
        assert state == LapState.INVALID_PENALTY

    def test_determine_lap_state_split_end_missing(self):
        """Test split end missing in non-practice."""
        from src.models import InProgressLap, LapState
        parser = LogParser()
        ip = InProgressLap()
        ip.split_end_confirmed = False
        ip.splits = {0: 30000, 1: 60000}
        
        state = parser._determine_lap_state(ip, [0, 1], [30000, 60000], 90000, "RACE")
        
        assert state == LapState.INVALID_SPLIT

    def test_determine_lap_state_valid(self):
        """Test valid lap (PUSH state)."""
        from src.models import InProgressLap, LapState
        parser = LogParser()
        ip = InProgressLap()
        ip.split_end_confirmed = True
        ip.splits = {0: 30000, 1: 60000}
        
        state = parser._determine_lap_state(ip, [0, 1], [30000, 60000], 90000, "PRACTICE")
        
        assert state == LapState.PUSH


class TestLogParserParserState:
    """Test parser state management."""

    def test_get_current_session_none(self):
        """Test get_current_session returns None initially."""
        parser = LogParser()
        
        assert parser.get_current_session() is None

    def test_get_player_id_none(self):
        """Test get_player_id returns None initially."""
        parser = LogParser()
        
        assert parser.get_player_id() is None

    def test_is_running_initially(self):
        """Test is_running property initially."""
        parser = LogParser()
        
        assert parser.is_running is False

    def test_stop_parser(self):
        """Test stopping the parser."""
        parser = LogParser()
        parser._running = True
        
        parser.stop()
        
        assert parser._running is False
