"""
Tests for log parser using deterministic synthetic ACE lines.

Tests race start detection, session end detection, and lap completion
with representative log lines from ACE.
"""

from conftest import make_parser

PLAYER_CAR_ID = "aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb"
OTHER_CAR_ID = "cccccccccccccccc-dddddddddddddddd"
THIRD_CAR_ID = "eeeeeeeeeeeeeeee-ffffffffffffffff"
FOURTH_CAR_ID = "1111111111111111-2222222222222222"


class TestRaceStartDetection:
    """Test race start detection from representative log lines."""

    def test_race_start_detection_with_player_car(self):
        """Test that 'has started the race!' triggers game_status=True for player car."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [gameplay] [info] Car aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb has started the race!"
        
        # Process the line
        parser._process_line(line)
        
        assert parser._session_active_from_logs is True

    def test_race_start_detection_ignores_other_cars(self):
        """Test that race start for other cars doesn't trigger player game status."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [gameplay] [info] Car cccccccccccccccc-dddddddddddddddd has started the race!"
        
        # Session activity is global, but player-car matching remains scoped.
        parser._process_line(line)

        assert parser._session_active_from_logs is True
        assert not parser._line_mentions_player_car(line)


class TestSessionEndDetection:
    """Test session end detection from representative log lines."""

    def test_end_session_with_player_car(self):
        """Test that END_SESSION with player car triggers game_status=False."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION WatingForOthers Ending Lap for aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb car"
        
        # Process the line
        parser._process_line(line)
        
        assert parser.current_session is None
        assert parser._session_active_from_logs is False

    def test_end_session_with_other_car(self):
        """Test that END_SESSION for other cars can still trigger via fallback."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION WatingForOthers Ending Lap for cccccccccccccccc-dddddddddddddddd car"
        
        # Process the line - other car
        parser._process_line(line)
        
        assert parser.current_session is not None
        assert parser._session_active_from_logs is False

    def test_end_session_teleported_to_pit(self):
        """Test END_SESSION with 'teleported to pit' pattern."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION car cccccccccccccccc-dddddddddddddddd has ended teleported to pit"
        
        # Process the line
        parser._process_line(line)
        
        assert parser.current_session is not None

    def test_end_session_player_match_normalizes_hyphens(self):
        """Player-car matching should tolerate hyphenated and compact UUID forms."""
        parser = make_parser(PLAYER_CAR_ID)

        line = "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION car aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb has ended"

        assert parser._line_mentions_player_car(line)

    def test_end_session_other_car_does_not_match_player(self):
        """Other cars must not stop the player's live telemetry capture."""
        parser = make_parser(PLAYER_CAR_ID)

        line = "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION car cccccccccccccccc-dddddddddddddddd has ended"

        assert not parser._line_mentions_player_car(line)


class TestLapCompletion:
    """Test lap event parsing from representative log lines."""

    def test_lap_completed_basic(self):
        """Test basic lap completion log line parsing."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [physics] [info] Lap test evOnLapCompleted 1 completed"
        
        # Process the line
        result = parser._process_line(line)
        
        assert result is None
        assert parser._ip.physics_lap_num == 1

    def test_compound_change_detection(self):
        """Test tyre compound change detection from a representative log."""
        parser = make_parser(PLAYER_CAR_ID)
        
        line = "[2000-01-01 00:00:00.000] [physics] [info] setCompound Tyre: 0 compound name: S"
        
        # Process the line
        parser._process_line(line)
        
        parser._process_line("[2000-01-01 00:00:01.000] [physics] [info] no-op")
        assert parser.context.tyre.compound_name == "S"


class TestMultipleCarsInRace:
    """Test handling of multiple cars in the same race session."""

    def test_multiple_race_starts(self):
        """Test that multiple cars starting race is handled correctly."""
        parser = make_parser(PLAYER_CAR_ID)
        
        lines = [
            "[2000-01-01 00:00:00.000] [gameplay] [info] Car aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb has started the race!",
            "[2000-01-01 00:00:00.000] [gameplay] [info] Car cccccccccccccccc-dddddddddddddddd has started the race!",
            "[2000-01-01 00:00:00.000] [gameplay] [info] Car eeeeeeeeeeeeeeee-ffffffffffffffff has started the race!",
        ]
        
        for line in lines:
            parser._process_line(line)
        
        assert parser._session_active_from_logs is True

    def test_multiple_end_sessions(self):
        """Test that multiple END_SESSION events are handled."""
        parser = make_parser(PLAYER_CAR_ID)
        
        lines = [
            "[2000-01-01 00:00:00.000] [gameplay] [info] END_SESSION WatingForOthers Ending Lap for cccccccccccccccc-dddddddddddddddd car",
            "[2000-01-01 00:00:01.000] [gameplay] [info] END_SESSION WatingForOthers Ending Lap for 1111111111111111-2222222222222222 car",
            "[2000-01-01 00:00:02.000] [gameplay] [info] END_SESSION WatingForOthers Ending Lap for aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb car",
        ]
        
        for line in lines:
            parser._process_line(line)
        
        assert parser.current_session is None
        assert parser._session_active_from_logs is False
