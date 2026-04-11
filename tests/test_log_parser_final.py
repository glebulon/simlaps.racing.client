"""
Final targeted tests to reach 80% coverage.

Targeting specific uncovered branches and edge cases.
"""

import pytest
import asyncio
import os
os.environ["APP_SECRET"] = "31cdbbaf05e962038c9221bdc22845b7639f4a1e914b4596db6b8608a5ea5e18"

from src.core.log_parser import LogParser
from src.models import SessionData, LapState, LapData


class TestLapTimeParsing:
    """Test lap time parsing edge cases."""

    def test_parse_lap_time_milliseconds(self):
        """Test parsing lap time with only milliseconds."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("0:00.123")
        assert result == 123

    def test_parse_lap_time_zero(self):
        """Test parsing zero lap time."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("0:00.000")
        assert result == 0

    def test_parse_lap_time_invalid(self):
        """Test parsing invalid lap time format."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("invalid")
        assert result is None

    def test_parse_lap_time_empty(self):
        """Test parsing empty lap time."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("")
        assert result is None


class TestTimestampExtraction:
    """Test timestamp extraction from log lines."""

    def test_extract_timestamp_valid(self):
        """Test extracting timestamp from valid line."""
        parser = LogParser()
        line = "[2024-01-01 12:34:56] Some log message"
        result = parser._extract_line_timestamp(line)
        assert result is not None

    def test_extract_timestamp_no_timestamp(self):
        """Test extracting from line without timestamp."""
        parser = LogParser()
        line = "Some log message without timestamp"
        result = parser._extract_line_timestamp(line)
        assert result is None

    def test_extract_timestamp_invalid_format(self):
        """Test extracting from line with invalid timestamp format."""
        parser = LogParser()
        line = "[invalid] Some message"
        result = parser._extract_line_timestamp(line)
        assert result is None


class TestCarAndPlayerIdChecks:
    """Test car ID and player ID validation."""

    def test_is_player_car_true(self):
        """Test is_player_car returns True for matching car."""
        parser = LogParser()
        parser.context.car_uuid = "player_car_123"
        result = parser._is_player_car("player_car_123")
        assert result is True

    def test_is_player_car_false(self):
        """Test is_player_car returns False for different car."""
        parser = LogParser()
        parser.context.car_uuid = "player_car_123"
        result = parser._is_player_car("other_car_456")
        assert result is False

    def test_is_player_car_no_car(self):
        """Test is_player_car when no car_uuid set."""
        parser = LogParser()
        # car_uuid is None by default
        result = parser._is_player_car("any_car")
        assert result is False

    def test_is_steam_id_valid(self):
        """Test is_steam_id with valid Steam ID."""
        parser = LogParser()
        result = parser._is_steam_id("76561198321627695")
        assert result is True

    def test_is_steam_id_invalid(self):
        """Test is_steam_id with invalid ID."""
        parser = LogParser()
        result = parser._is_steam_id("not_a_steam_id")
        assert result is False

    def test_is_steam_id_empty(self):
        """Test is_steam_id with empty string."""
        parser = LogParser()
        result = parser._is_steam_id("")
        assert result is False


class TestTrackNameCleaning:
    """Test track name cleaning."""

    def test_clean_track_name_with_suffix(self):
        """Test cleaning track name with _gp suffix."""
        parser = LogParser()
        result = parser._clean_track_name("spa_francorchamps_gp")
        assert result == "spa_francorchamps"

    def test_clean_track_name_no_suffix(self):
        """Test cleaning track name without suffix."""
        parser = LogParser()
        result = parser._clean_track_name("monza")
        assert result == "monza"

    def test_clean_track_name_none(self):
        """Test cleaning None track name."""
        parser = LogParser()
        result = parser._clean_track_name(None)
        assert result is None


class TestFollowMoreScenarios:
    """More follow() method tests."""

    @pytest.mark.asyncio
    async def test_follow_handles_many_lines(self, tmp_path):
        """Test follow handles many lines efficiently."""
        log_file = tmp_path / "test.log"
        
        # Create file with many lines
        lines = ["TRACK NAME: spa\n", "CAR NAME: porsche\n"]
        for i in range(100):
            lines.append(f"Log line {i}\n")
        log_file.write_text("".join(lines))
        
        parser = LogParser(log_path=str(log_file))
        parser._running = True
        
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.3)
        except asyncio.TimeoutError:
            pass
        
        parser.stop()
        
        assert parser.context.track_name == "spa"

    @pytest.mark.asyncio
    async def test_follow_empty_file(self, tmp_path):
        """Test follow with empty file."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")  # Empty
        
        parser = LogParser(log_path=str(log_file))
        parser._running = True
        
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass
        
        parser.stop()
        assert True  # Should not crash

    @pytest.mark.asyncio
    async def test_follow_binary_in_log(self, tmp_path):
        """Test follow handles binary data in log (edge case)."""
        log_file = tmp_path / "test.log"
        # Write some text then binary-like content
        log_file.write_bytes(b"Game Started!\n\x00\xff\xfeSome binary\n")
        
        parser = LogParser(log_path=str(log_file))
        parser._running = True
        
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass
        
        parser.stop()
        assert True  # Should handle gracefully


class TestOutlapHandling:
    """Test outlap detection and handling."""

    def test_outlap_detected_first_lap(self):
        """Test first lap is marked as outlap."""
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser.context.car_uuid = "abc123"
        
        # First lap number seen
        line = "[2024-01-01 12:00:00] Lap carId=abc123 lap=1"
        parser._handle_lap_number(line)
        
        # First lap should be outlap
        assert parser._ip.is_outlap is True

    def test_not_outlap_after_first_valid_lap(self):
        """Test subsequent laps are not outlaps."""
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        parser.context.car_uuid = "abc123"
        
        # Simulate having completed a lap
        parser.current_session.laps.append(LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=100000,
            lap_time_str="1:40.000",
            lap_state=LapState.PUSH
        ))
        
        # Next lap number
        line = "[2024-01-01 12:00:00] Lap carId=abc123 lap=2"
        parser._handle_lap_number(line)
        
        # Second lap should not be outlap
        assert parser._ip.is_outlap is False


class TestDetermineLapStateFull:
    """Comprehensive tests for _determine_lap_state."""

    def setup_method(self):
        """Setup for lap state tests."""
        self.parser = LogParser()

    def test_determine_lap_state_clean(self):
        """Test clean valid lap."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        assert state == LapState.PUSH

    def test_determine_lap_state_invalid(self):
        """Test invalid lap with violations."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = True  # Has violation
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        assert state == LapState.INVALID

    def test_determine_lap_state_incomplete_sectors(self):
        """Test lap with incomplete sector data."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        # Only 2 sectors
        splits = {0: 30000, 1: 30000}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Should be invalid due to incomplete sectors
        assert state == LapState.INVALID

    def test_determine_lap_state_sectors_inconsistent(self):
        """Test lap with sector sum inconsistent with lap time."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        # Sectors don't add up (way off)
        splits = {0: 10000, 1: 10000, 2: 10000}  # Sum = 30s, lap = 98s
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Should be invalid due to sector inconsistency
        assert state == LapState.INVALID

    def test_determine_lap_state_short_distance(self):
        """Test lap with insufficient distance."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 5  # Very short
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Should be invalid due to short distance
        assert state == LapState.INVALID

    def test_determine_lap_state_split_not_confirmed(self):
        """Test lap with split end not confirmed."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = False  # Not confirmed
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Should be invalid due to unconfirmed split
        assert state == LapState.INVALID

    def test_determine_lap_state_outlap(self):
        """Test outlap is always invalid."""
        ip = self.parser._ip
        ip.is_outlap = True  # Is outlap
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Outlap is always invalid
        assert state == LapState.INVALID

    def test_determine_lap_state_unexpected_split(self):
        """Test lap with unexpected split is invalid."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = False
        ip.has_unexpected_split = True  # Unexpected split
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Unexpected split makes lap invalid
        assert state == LapState.INVALID

    def test_determine_lap_state_penalty(self):
        """Test lap with penalty is invalid."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.has_track_limit_violation = False
        ip.has_penalty = True  # Has penalty
        ip.has_unexpected_split = False
        ip.split_end_confirmed = True
        ip.distance_hundredm = 50
        
        splits = {0: 30000, 1: 30000, 2: 38456}
        
        state = self.parser._determine_lap_state(
            ip, splits, splits, 98456, "PRACTICE"
        )
        
        # Penalty makes lap invalid
        assert state == LapState.INVALID


class TestLogBufferOperations:
    """Test log buffer operations."""

    def test_add_to_log_buffer(self):
        """Test adding lines to log buffer."""
        parser = LogParser()
        
        parser._add_to_log_buffer("Line 1")
        parser._add_to_log_buffer("Line 2")
        
        buffer = parser.get_log_buffer()
        assert len(buffer) == 2
        assert buffer[0] == "Line 1"
        assert buffer[1] == "Line 2"

    def test_log_buffer_max_size(self):
        """Test log buffer respects max size."""
        parser = LogParser()
        parser._log_buffer_max = 5
        
        for i in range(10):
            parser._add_to_log_buffer(f"Line {i}")
        
        buffer = parser.get_log_buffer()
        assert len(buffer) == 5
        # Should keep most recent
        assert buffer[0] == "Line 5"
        assert buffer[-1] == "Line 9"

    def test_clear_log_buffer(self):
        """Test clearing log buffer."""
        parser = LogParser()
        
        parser._add_to_log_buffer("Line 1")
        parser._add_to_log_buffer("Line 2")
        
        parser.clear_log_buffer()
        
        buffer = parser.get_log_buffer()
        assert len(buffer) == 0

    def test_export_logs_to_file(self, tmp_path):
        """Test exporting logs to file."""
        parser = LogParser()
        
        parser._add_to_log_buffer("Line 1")
        parser._add_to_log_buffer("Line 2")
        
        export_file = tmp_path / "export.txt"
        result = parser.export_logs_to_file(str(export_file))
        
        assert result is True
        content = export_file.read_text()
        assert "Line 1" in content
        assert "Line 2" in content

    def test_export_logs_failure(self, tmp_path):
        """Test export logs failure handling."""
        parser = LogParser()
        
        parser._add_to_log_buffer("Line 1")
        
        # Try to write to invalid path
        result = parser.export_logs_to_file("/nonexistent/path/file.txt")
        
        assert result is False
