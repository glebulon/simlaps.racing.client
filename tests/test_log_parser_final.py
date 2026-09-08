"""
Final targeted tests to reach 80% coverage.

Targeting specific uncovered branches and edge cases.
"""

import pytest
import asyncio
from unittest.mock import patch

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
        """Test parsing invalid lap time format.

        ``_parse_lap_time_ms`` returns ``0`` (not ``None``) for unparseable
        input — the upstream parser only ever feeds it strings extracted by
        a regex match, so the malformed-input branch is intentionally a
        silent fall-through to a sentinel value.
        """
        parser = LogParser()
        result = parser._parse_lap_time_ms("invalid")
        assert result == 0

    def test_parse_lap_time_empty(self):
        """Test parsing empty lap time falls through to sentinel ``0``."""
        parser = LogParser()
        result = parser._parse_lap_time_ms("")
        assert result == 0


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
        """Anything between ``[...]`` is returned verbatim.

        ``_extract_line_timestamp`` deliberately doesn't validate the
        timestamp shape — the AC Evo log format is strict enough that
        anything inside ``[...]`` at line-start is taken as a timestamp
        string. This test pins that contract so future tightening is a
        deliberate decision.
        """
        parser = LogParser()
        line = "[invalid] Some message"
        result = parser._extract_line_timestamp(line)
        assert result == "invalid"


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

    def test_clean_track_name_strips_session_suffix(self):
        """Session-type suffixes injected by AC Evo are stripped."""
        parser = LogParser()
        assert parser._clean_track_name("Spa Francorchamps Race") == "Spa Francorchamps"
        assert parser._clean_track_name("Monza Time Attack Practice") == "Monza"
        assert parser._clean_track_name("Imola Qualifying") == "Imola"

    def test_clean_track_name_strips_at_date(self):
        """Track descriptions like ``'Monza @ 2024-01-01'`` keep only the name."""
        parser = LogParser()
        result = parser._clean_track_name("Monza @ 2024-01-01")
        assert result == "Monza"

    def test_clean_track_name_no_suffix(self):
        """Test cleaning track name without suffix."""
        parser = LogParser()
        result = parser._clean_track_name("monza")
        assert result == "monza"

    def test_clean_track_name_strips_session_length_descriptor(self):
        """Newer ACE builds append 'N laps'/'N seconds' before the @date."""
        parser = LogParser()
        assert (
            parser._clean_track_name(
                "Laguna Seca GP Race Race  5 laps @2014/8/15 15:0:0"
            )
            == "Laguna Seca GP"
        )
        assert (
            parser._clean_track_name(
                "Laguna Seca GP Time Attack Practice  5400 seconds @2014/8/15 15:0:0"
            )
            == "Laguna Seca GP"
        )


class TestFollowMoreScenarios:
    """More follow() method tests."""

    @pytest.mark.asyncio
    async def test_follow_handles_many_lines(self, tmp_path):
        """Test follow handles many lines efficiently.

        Uses the real ``TRACK NAME <name>`` log shape that
        ``_handle_track_name`` parses (regex ``r"TRACK NAME (.+)"``), and
        reads from ``context.current_track`` (the actual attribute on
        ``LogContext``).
        """
        log_file = tmp_path / "test.log"

        lines = ["TRACK NAME spa\n"]
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

        assert parser.context.current_track == "spa"

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


class TestDetermineLapStateFull:
    """Comprehensive tests for _determine_lap_state."""

    def setup_method(self):
        """Setup for lap state tests."""
        self.parser = LogParser()

    @staticmethod
    def _split_args(splits):
        """Adapt a ``{idx: ms}`` mapping into ``(keys, times)`` lists for
        ``_determine_lap_state``."""
        keys = sorted(splits.keys())
        return keys, [splits[k] for k in keys]

    def test_determine_lap_state_valid(self):
        """Non-outlap returns ``VALID``."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.physics_lap_num = 2

        state = self.parser._determine_lap_state(ip, "PRACTICE")
        assert state == LapState.VALID

    def test_determine_lap_state_outlap_flag(self):
        """Outlap flag returns ``OUTLAP``."""
        ip = self.parser._ip
        ip.is_outlap = True
        ip.physics_lap_num = 2

        state = self.parser._determine_lap_state(ip, "PRACTICE")
        assert state == LapState.OUTLAP

    def test_determine_lap_state_practice_outlap_fallback(self):
        """Practice lap 1 with no splits returns ``OUTLAP``."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.physics_lap_num = 1
        ip.splits = {}

        state = self.parser._determine_lap_state(ip, "PRACTICE")
        assert state == LapState.OUTLAP

    def test_determine_lap_state_practice_lap1_with_splits_is_valid(self):
        """Practice lap 1 with splits is a flying lap → ``VALID``."""
        ip = self.parser._ip
        ip.is_outlap = False
        ip.physics_lap_num = 1
        ip.splits = {0: 30000, 1: 30000, 2: 38456}

        state = self.parser._determine_lap_state(ip, "PRACTICE")
        assert state == LapState.VALID


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
        """Log buffer trims to ``max_log_lines`` keeping most-recent lines."""
        parser = LogParser()
        parser.max_log_lines = 5

        for i in range(10):
            parser._add_to_log_buffer(f"Line {i}")

        buffer = parser.get_log_buffer()
        assert len(buffer) == 5
        # Trim drops oldest, keeps most recent.
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

        with patch("src.core.log_parser.open", side_effect=OSError("permission denied")):
            result = parser.export_logs_to_file("some_path.txt")

        assert result is False
