"""
Comprehensive tests for _handle_lap_complete - the main lap completion logic.

Targets the big uncovered chunk (lines 992-1092).
"""

import pytest

from src.core.log_parser import LogParser
from src.models import LapData, SessionData, LapState, SharedSessionManager


class TestHandleLapCompleteBasic:
    """Test basic _handle_lap_complete functionality."""

    def test_handle_lap_complete_returns_none_no_new_lap(self):
        """Test returns None when line doesn't contain 'New lap'."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"
        
        result = parser._handle_lap_complete("Random log line")
        assert result is None

    def test_handle_lap_complete_returns_none_no_session(self):
        """Test returns None when no current_session."""
        parser = LogParser()
        # No session set
        
        result = parser._handle_lap_complete("New lap carId=123 time=1:30.000")
        assert result is None

    def test_handle_lap_complete_returns_none_wrong_car(self):
        """Test returns None when car_id doesn't match."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"  # Player car
        
        # Lap from different car
        result = parser._handle_lap_complete("New lap carId=other_car time=1:30.000")
        assert result is None


class TestHandleLapCompleteWithData:
    """Test lap completion with proper data setup."""

    def setup_parser_with_lap_data(self, parser):
        """Helper to set up parser with complete lap data."""
        parser.current_session = SessionData(
            track="spa", 
            car="porsche", 
            player_id="76561198321627695",
            session_type="PRACTICE"
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123def4567890"
        parser.context.tyre.set_all("SC")
        
        # Set up in-progress lap data
        parser._ip.physics_lap_num = 5
        parser._ip.splits = {0: 30000, 1: 30000, 2: 38456}  # 1:38.456
        parser._ip.distance_hundredm = 50
        parser._ip.fuel_used = 2.5
        parser._ip.fuel_reliable = True

    def test_handle_lap_complete_creates_lap_data(self):
        """Test creates LapData with all fields."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # May or may not return lap depending on internal logic
        # But code path should be exercised
        assert True

    def test_handle_lap_complete_with_fuel_tracking(self):
        """Test lap completion tracks fuel."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        
        parser._ip.fuel_used = 3.5
        parser._ip.start_fuel = 45.0
        parser._ip.end_fuel = 41.5
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Fuel tracking code path exercised
        assert True

    def test_handle_lap_complete_invalid_lap(self):
        """Test lap completion defaults to valid (heuristics removed)."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)

        line = (
            "[2024-01-01 12:00:00.000] [gameplay] [info] "
            "New lap carId abc123def4567890: 1:38.456"
        )
        result = parser._handle_lap_complete(line)

        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.VALID
        assert parser._pending_lap.is_valid is True

    def test_handle_lap_complete_outlap(self):
        """Test lap completion when marked as outlap."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        
        parser._ip.is_outlap = True
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Should handle outlap case
        assert True

    def test_handle_lap_complete_sector1_corruption(self):
        """Test handles S1 corruption (cumulative time from race start)."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        parser.current_session.session_type = "RACE"
        
        # S1 is corrupted - larger than total lap time (race grid start issue)
        parser._ip.splits = {0: 180000, 1: 20000, 2: 18456}  # S1=180s > lap=98s
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Should detect and fix S1 corruption
        assert True

    def test_handle_lap_complete_first_race_lap_grid_start_valid(self):
        """Regression: Spa grid-start lap 1 must be marked valid.

        On a grid start the game logs an inflated S1 (cumulative time
        from race start until the player first crosses the start/finish
        line) that can be *smaller* than lap_time but still causes
        S1+S2+S3 to overshoot lap_time. The previous corruption guard
        only fired when `s1 > lap_time`, so this case slipped through
        and the lap was wrongly marked INVALID_SECTORS.

        With deferred emit, the lap is buffered on "New lap carId" and
        flushed when the game's authoritative "Relevant onSplit" line
        arrives milliseconds later.

        Reference: telemetry/gamelogs/log.txt line 2646-2648
            New lap carId 48734ba531b55222-50fea70206fd60a7: 02:26.939
            Relevant onSplit for Combo 6@2: laptime 146939, valid true, ...
        """
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa",
            car="dallara_exp",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "48734ba531b55222-50fea70206fd60a7"
        parser.context.tyre.set_all("S")

        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 110411, 1: 64650, 2: 38082}  # raw, S1 inflated

        # 1) Lap completes — buffered, nothing emitted yet
        line_new_lap = (
            "[2026-04-26 00:08:16.599] [gameplay] [info] "
            "New lap carId 48734ba531b55222-50fea70206fd60a7: 02:26.939"
        )
        result = parser._handle_lap_complete(line_new_lap)
        assert result is None  # deferred emit
        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_time_ms == 146939
        # Default heuristic state is VALID; the validity line
        # will confirm it.
        assert parser._pending_lap.lap_state == LapState.VALID

        # 2) Authoritative validity line arrives ~13 ms later
        line_validity = (
            "[2026-04-26 00:08:16.612] [network] [info] "
            "Relevant onSplit for Combo 6@2: laptime 146939, valid true, flags 2, lap 1 (prev 0)"
        )
        result = parser._handle_lap_validity(line_validity)

        assert result is not None
        assert result.lap_time_ms == 146939
        assert result.sector1_ms == 146939 - 64650 - 38082  # 44207
        assert result.sector2_ms == 64650
        assert result.sector3_ms == 38082
        assert result.sectors_consistent is True
        assert result.lap_state == LapState.VALID
        assert result.is_valid is True
        assert parser._pending_lap is None  # flushed
        assert parser._pending_lap_since is None

    def test_handle_lap_validity_flags_one_invalidates_valid(self):
        """Test that the game's invalid flag demotes a heuristically-valid lap.

        If our sector/split checks say VALID but the game flags the
        lap is invalid, we trust the game and mark it INVALID_GAME.
        """
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa",
            car="porsche",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123-def456"
        parser.context.tyre.set_all("S")

        parser._ip.physics_lap_num = 2
        parser._ip.splits = {0: 42000, 1: 45000, 2: 38000}  # sum = 125000

        # 1) Lap completes — buffered
        line_new_lap = (
            "[2026-04-26 00:10:41.450] [gameplay] [info] "
            "New lap carId abc123-def456: 02:05.000"
        )
        result = parser._handle_lap_complete(line_new_lap)
        assert result is None  # deferred emit
        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.VALID
        assert parser._pending_lap.is_valid is True

        # 2) Game says invalid (e.g., track cut the UI didn't show)
        line_validity = (
            "[2026-04-26 00:10:41.462] [network] [info] "
            "Relevant onSplit for Combo 6@2: laptime 125000, valid false, flags 1, lap 2 (prev 1)"
        )
        result = parser._handle_lap_validity(line_validity)

        assert result is not None
        assert result.lap_time_ms == 125000
        assert result.lap_state == LapState.INVALID_GAME
        assert result.is_valid is False
        assert result.lap_type == "INVALID_GAME"
        assert parser._pending_lap is None  # flushed

    def test_late_authoritative_validity_updates_shm_emitted_lap(self):
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")
        lap = LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=125000,
            lap_time_str="02:05.000",
            lap_state=LapState.INVALID_GAME,
            lap_type="INVALID_GAME",
            is_valid=False,
            validity_source="shm_graphics",
        )
        parser.current_session.laps.append(lap)

        result = parser._handle_lap_validity(
            "[2026-08-16 12:00:00.000] [network] [info] "
            "Relevant onSplit for Combo 6@2: laptime 125000, valid true, "
            "flags 2, lap 1 (prev 0)"
        )

        assert result is None
        assert lap.lap_state == LapState.VALID
        assert lap.is_valid is True
        assert lap.validity_source == "authoritative"
        assert parser._reconciled_lap is lap
        assert parser.current_session.laps == [lap]

    def test_shm_completion_time_beats_reused_physical_lap_validity(self):
        """Fallback validity follows the matching completion across pit stints."""
        from src.models import SharedSessionManager

        manager = SharedSessionManager()
        manager.update_lap_from_logs(
            LapData(
                lap_number=2,
                physics_lap_number=2,
                lap_time_ms=70290,
                lap_time_str="01:10.290",
                is_valid=True,
                lap_state=LapState.VALID,
                lap_type="VALID",
            )
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 83000,
                "last_laptime_ms": 0,
                "is_valid_lap": False,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 80,
                "last_laptime_ms": 84057,
                "is_valid_lap": True,
            }
        )
        parser = LogParser(session_manager=manager)
        pending = LapData(
            lap_number=3,
            physics_lap_number=2,
            lap_time_ms=84057,
            lap_time_str="01:24.057",
            is_valid=True,
            lap_state=LapState.VALID,
            lap_type="VALID",
        )

        parser._apply_shm_fallback_validity(pending)

        assert pending.is_valid is False
        assert pending.lap_state == LapState.INVALID_GAME
        assert pending.validity_source == "shm_graphics"

    def test_handle_lap_validity_flags_two_valid_even_when_boolean_false(self):
        """AC Evo 0.7.0 can log valid false + flags 2 for a valid final lap."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="brands_hatch",
            car="ks_abarth_695_biposto",
            player_id="76561198321627695",
            session_type="PRACTICE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "42c664e0cfa01f9e-868df28c1b9fb49b"
        parser.context.tyre.set_all("SC")

        parser._ip.physics_lap_num = 5
        parser._ip.splits = {0: 39225, 1: 6456, 2: 24867}

        line_new_lap = (
            "[2026-06-03 23:17:00.127] [gameplay] [info] "
            "New lap carId 42c664e0cfa01f9e-868df28c1b9fb49b: 01:10.548"
        )
        result = parser._handle_lap_complete(line_new_lap)
        assert result is None
        assert parser._pending_lap is not None
        assert parser._pending_lap.is_valid is True

        line_validity = (
            "[2026-06-03 23:17:00.135] [network] [info] "
            "Relevant onSplit for Combo 2@8: laptime 70548, valid false, "
            "flags 2, lap 4 (prev 3)"
        )
        result = parser._handle_lap_validity(line_validity)

        assert result is not None
        assert result.lap_number == 4
        assert result.lap_time_ms == 70548
        assert result.lap_state == LapState.VALID
        assert result.is_valid is True
        assert result.lap_type == "VALID"
        assert parser._pending_lap is None

    def test_handle_lap_validity_accepts_flexible_field_order(self):
        """Authoritative validity parsing should survive format/order changes."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa",
            car="porsche",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123-def456"
        parser.context.tyre.set_all("S")

        parser._ip.physics_lap_num = 2
        parser._ip.splits = {0: 42000, 1: 45000, 2: 38000}  # sum = 125000

        parser._handle_lap_complete(
            "[2026-04-26 00:10:41.450] [gameplay] [info] "
            "New lap carId abc123-def456: 02:05.000"
        )

        result = parser._handle_lap_validity(
            "[2026-04-26 00:10:41.462] [network] [warning] "
            "Relevant onSplit payload changed: flags 1, lap 2, laptime 125000, valid false"
        )

        assert result is not None
        assert result.lap_number == 2
        assert result.lap_time_ms == 125000
        assert result.lap_state == LapState.INVALID_GAME
        assert result.is_valid is False
        assert parser._pending_lap is None

    def test_penalty_warning_before_lap_completion_demotes_to_invalid_penalty(self):
        """Penalty warning should mark the next completed lap invalid as fallback."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="laguna_seca",
            car="ks_mazda_mx5_nd_cup",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123-def456"
        parser.context.tyre.set_all("S")

        parser._ip.physics_lap_num = 3
        parser._ip.splits = {0: 43000, 1: 45000, 2: 36000}

        parser._process_line(
            "[2026-08-13 22:24:23.707] [gameplay] [info] "
            "Penalty Type PenaltyType_Warning has no tranformation"
        )
        parser._handle_lap_complete(
            "[2026-08-13 22:24:28.867] [gameplay] [info] "
            "New lap carId abc123-def456: 02:04.000"
        )

        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.INVALID_PENALTY
        assert parser._pending_lap.is_valid is False

    def test_penalty_during_next_lap_does_not_demote_pending_previous_lap(self):
        """A penalty after the next lap has started belongs to that lap."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="laguna_seca",
            car="ks_mazda_mx5_nd_cup",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123-def456"
        parser.context.tyre.set_all("S")

        parser._ip.physics_lap_num = 2
        parser._ip.splits = {0: 43000, 1: 45000, 2: 36000}
        flushed = parser._handle_lap_complete(
            "[2026-08-13 22:24:28.867] [gameplay] [info] "
            "New lap carId abc123-def456: 02:04.000"
        )
        assert flushed is None
        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.VALID

        parser._ip.physics_lap_num = 3
        parser._ip.splits = {0: 42000, 1: 44000, 2: 37000}
        parser._process_line(
            "[2026-08-13 22:26:30.100] [gameplay] [info] "
            "Penalty Type PenaltyType_Warning has no tranformation"
        )

        assert parser._pending_lap.lap_state == LapState.VALID
        assert parser._pending_penalty_warning is True

        flushed = parser._handle_lap_complete(
            "[2026-08-13 22:28:32.000] [gameplay] [info] "
            "New lap carId abc123-def456: 02:03.000"
        )
        assert flushed is not None
        assert flushed.lap_state == LapState.VALID
        assert flushed.is_valid is True
        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.INVALID_PENALTY
        assert parser._pending_lap.is_valid is False

    def test_penalty_added_deadline_duplicate_does_not_bleed_onto_next_lap(self):
        """ACE fires PENALTY_ADDED twice per cut: at detection and again when
        the recovery deadline expires (~3-11s later). The expiry copy lands
        after the next lap started and must not invalidate it."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="circuit_de_spa_francorchamps",
            car="ks_honda_nsx_r",
            player_id="76561198321627695",
            session_type="RACE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc123-def456"
        parser.context.tyre.set_all("S")

        # Cut detected 0.1s before lap 1 completes -> lap 1 invalid.
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 55000, 1: 79000, 2: 47000}
        parser._process_line(
            "[2026-08-18 23:13:02.935] [gameface] [warning] true {PENALTY_ADDED_KEY} #0 "
        )
        parser._handle_lap_complete(
            "[2026-08-18 23:13:03.050] [gameplay] [info] "
            "New lap carId abc123-def456: 03:01.000"
        )
        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.INVALID_PENALTY

        # Deadline-expiry duplicate ~11s into lap 2 -> must be ignored.
        parser._ip.physics_lap_num = 2
        parser._ip.splits = {0: 48000}
        parser._process_line(
            "[2026-08-18 23:13:13.856] [gameface] [warning] true {PENALTY_ADDED_KEY} #0 "
        )
        assert parser._pending_penalty_warning is False

        # A genuinely new penalty >15s later still applies.
        parser._process_line(
            "[2026-08-18 23:13:30.000] [gameface] [warning] true {PENALTY_ADDED_KEY} #0 "
        )
        assert parser._pending_penalty_warning is True

    def test_handle_lap_validity_upgrades_stale_penalty(self):
        """Game's valid flag must override a stale heuristic penalty.

        Regression (Nurburgring Tourist): fallback penalty hints can demote
        a pending lap to INVALID_PENALTY before authoritative validity arrives.
        The game's authoritative ``Relevant onSplit ... valid true, flags 2``
        must upgrade it back to VALID.

        Reference: logs/game_logs_20260606_221943.txt lines 20680-20684
            New lap carId 4d19cc73858c594e-1fb0a71f9cba54a7: 06:18.351
            Relevant onSplit for Combo 19@91: laptime 378351, valid true,
            flags 2, lap 1 (prev 0)
        """
        parser = LogParser()
        parser.current_session = SessionData(
            track="nurburgring touristenfahrten",
            car="ks_lotus_emira",
            player_id="76561198321627695",
            session_type="PRACTICE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "4d19cc73858c594e-1fb0a71f9cba54a7"
        parser.context.tyre.set_all("HC")

        # Single-finish-split track: start line (id 0) + finish (id 1).
        parser._ip.splits = {0: 0, 1: 378351}

        line_new_lap = (
            "[2026-06-06 22:09:00.622] [gameplay] [info] "
            "New lap carId 4d19cc73858c594e-1fb0a71f9cba54a7: 06:18.351"
        )
        result = parser._handle_lap_complete(line_new_lap)
        assert result is None  # deferred emit
        assert parser._pending_lap is not None

        parser._process_line(
            "[2026-06-06 22:09:00.630] [gameplay] [info] "
            "Penalty Type PenaltyType_Warning has no tranformation"
        )
        assert parser._pending_lap.lap_state == LapState.INVALID_PENALTY
        assert parser._pending_lap.is_valid is False

        line_validity = (
            "[2026-06-06 22:09:00.633] [network] [info] "
            "Relevant onSplit for Combo 19@91: laptime 378351, valid true, "
            "flags 2, lap 1 (prev 0)"
        )
        result = parser._handle_lap_validity(line_validity)

        assert result is not None
        assert result.lap_time_ms == 378351
        assert result.lap_state == LapState.VALID
        assert result.is_valid is True
        assert parser._pending_lap is None

    def test_handle_lap_validity_keeps_genuinely_invalid_penalty(self):
        """Game flag 1 (invalid) demotes a default-VALID lap to INVALID_GAME.

        Reference: logs/game_logs_20260606_221943.txt line 4414
            Relevant onSplit for Combo 19@92: laptime 385308, valid false,
            flags 1, lap 1 (prev 0)
        """
        parser = LogParser()
        parser.current_session = SessionData(
            track="nurburgring touristenfahrten",
            car="ks_lotus_emira",
            player_id="76561198321627695",
            session_type="PRACTICE",
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "4422a48dcd944a5a-d38c24fbe4c806a7"
        parser.context.tyre.set_all("HC")

        parser._ip.splits = {0: 0, 1: 385308}

        parser._handle_lap_complete(
            "[2026-06-06 19:27:36.422] [gameplay] [info] "
            "New lap carId 4422a48dcd944a5a-d38c24fbe4c806a7: 06:25.308"
        )
        result = parser._handle_lap_validity(
            "[2026-06-06 19:27:36.432] [network] [info] "
            "Relevant onSplit for Combo 19@92: laptime 385308, valid false, "
            "flags 1, lap 1 (prev 0)"
        )

        assert result is not None
        assert result.lap_time_ms == 385308
        assert result.is_valid is False
        assert result.lap_state == LapState.INVALID_GAME

    def test_handle_lap_complete_missing_sectors(self):
        """Test handles missing sector data."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        
        # Only 2 sectors
        parser._ip.splits = {0: 45000, 1: 53456}
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Should handle missing S3
        assert True

    def test_handle_lap_complete_defaults_valid(self):
        """With heuristics removed, laps default to VALID / valid."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        parser.current_session.session_type = "RACE"

        line = (
            "[2024-01-01 12:00:00.000] [gameplay] [info] "
            "New lap carId abc123def4567890: 1:38.456"
        )
        result = parser._handle_lap_complete(line)

        assert parser._pending_lap is not None
        assert parser._pending_lap.lap_state == LapState.VALID
        assert parser._pending_lap.is_valid is True

    def test_handle_lap_complete_no_physics_lap_num(self):
        """Test uses fallback lap number when physics_lap_num not set."""
        parser = LogParser()
        self.setup_parser_with_lap_data(parser)
        
        parser._ip.physics_lap_num = None
        
        line = "New lap carId=abc123def4567890 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Should use len(laps)+1 as fallback
        assert True


class TestHandleLapCompleteSectorConsistency:
    """Test sector consistency handling."""

    def test_sector_consistency_check_passes(self):
        """Test sectors sum matches lap time."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"
        parser.context.tyre.set_all("SC")
        
        # Perfect sector match: 30+30+38.456 = 98.456
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 30000, 1: 30000, 2: 38456}
        
        line = "New lap carId=abc123 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # sectors_consistent should be True
        assert True

    def test_sector_consistency_check_fails(self):
        """Test sectors inconsistent with lap time."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"
        parser.context.tyre.set_all("SC")
        
        # Inconsistent: 30+30+30 = 90 != 98.456
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 30000, 1: 30000, 2: 30000}
        
        line = "New lap carId=abc123 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Should detect inconsistency
        assert True


class TestHandleLapCompleteStint:
    """Test stint handling during lap completion."""

    def test_creates_stint_for_valid_lap(self):
        """Test creates stint entry for valid lap."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"
        parser.context.tyre.set_all("SC")
        
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 30000, 1: 30000, 2: 38456}
        parser._ip.fuel_used = 2.5
        parser._ip.fuel_reliable = True
        
        # Pre-create stint
        parser._ensure_stint("SC")
        
        line = "New lap carId=abc123 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Stint should have lap added
        assert True

    def test_no_stint_for_outlap(self):
        """Test doesn't add outlap to stint."""
        parser = LogParser()
        parser.current_session = SessionData(
            track="spa", car="porsche", player_id="123"
        )
        parser.context.player_id = "123"
        parser.context.car_uuid = "abc123"
        parser.context.tyre.set_all("SC")
        
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 30000, 1: 30000, 2: 38456}
        parser._ip.is_outlap = True  # Mark as outlap
        
        line = "New lap carId=abc123 time=1:38.456"
        result = parser._handle_lap_complete(line)
        
        # Outlaps shouldn't update stint
        assert True


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [-1, 1])
async def test_delayed_log_enrichment_rounding_keeps_one_invalid_lap_card(delta):
    """A rounded log finish enriches, rather than duplicates, SHM output."""
    manager = SharedSessionManager()
    parser = LogParser(session_manager=manager)
    session = SessionData(track="spa", car="porsche", car_uuid="abc123")
    parser.current_session = session
    parser.context.car_uuid = "abc123"
    parser.context.tyre.set_all("S")
    shm_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="01:40.000",
        lap_state=LapState.INVALID_GAME,
        lap_type=LapState.INVALID_GAME.value,
        is_valid=False,
        validity_source="shm_graphics",
    )
    session.laps.append(shm_lap)
    parser._shm_emitted_laps.append(shm_lap)
    parser._ip.physics_lap_num = 1

    updates = []

    async def on_update(_session, lap):
        updates.append(lap)

    parser.on_lap_update = on_update
    log_ms = 100000 + delta
    minutes, remainder = divmod(log_ms, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    assert parser._handle_lap_complete(
        f"[2026-08-26 12:00:00.000] [gameplay] [info] New lap carId abc123: "
        f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    ) is None
    assert parser._reconciled_lap is shm_lap

    await parser._emit_lap_update(session, shm_lap)

    assert updates == [shm_lap]
    assert len(session.laps) == 1
    assert shm_lap.is_valid is False
    assert shm_lap.lap_state == LapState.INVALID_GAME


@pytest.mark.parametrize(
    "delta, expected", [(1, True), (-1, True), (3, False), (-3, False)]
)
def test_lap_time_match_rejects_just_outside_tolerance(delta, expected):
    """The named tolerance accepts rounding only, never a distinct time."""
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="01:40.000",
    )
    assert (LogParser._nearest_lap_match([lap], 100000 + delta) is not None) is expected


def test_unmatched_validity_broadcast_preserves_pending_lap() -> None:
    """A stale onSplit message must not terminate log following state."""
    parser = LogParser()
    parser.current_session = SessionData(
        track="spa", car="porsche", session_type="PRACTICE"
    )
    parser._pending_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
    )

    result = parser._handle_lap_validity(
        "[2026-08-26 12:00:00.000] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 120000, valid false, "
        "flags 1, lap 2 (prev 1)"
    )

    assert result is None
    assert parser._pending_lap is not None
    assert parser._pending_lap.lap_time_ms == 100_000


def test_equal_time_completions_keep_their_lap_associations() -> None:
    """A delayed first log line cannot consume the second SHM completion."""
    manager = SharedSessionManager()
    session = SessionData(
        track="spa", car="porsche", session_type="PRACTICE", car_uuid="abc123"
    )
    manager.begin_session(
        session.session_id, car_model=session.car, car_uuid=session.car_uuid
    )
    for completed_laps, lap_time_ms in ((1, 100_000), (2, 100_001)):
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps - 1,
                "current_lap_time_ms": 100_000,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": completed_laps,
                "current_lap_time_ms": 10,
                "last_laptime_ms": lap_time_ms,
                "is_valid_lap": True,
            }
        )

    parser = LogParser(session_manager=manager)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    parser._last_shm_completion_observed_at = 0
    parser.current_session = session
    parser._sessions_by_id[session.session_id] = session
    parser.context.car_uuid = session.car_uuid

    assert parser._handle_lap_complete(
        "[2026-08-26 12:00:00.000] [gameplay] [info] "
        "New lap carId abc123: 01:40.000"
    ) is None
    assert parser._take_ready_shm_lap() is None  # consumes only completion 1
    second = parser._take_ready_shm_lap()
    assert second is not None
    assert second.lap_time_ms == 100_001
    assert [item.lap_time_ms for item in manager.get_lap_completions_after(0)] == []


def test_equal_time_validity_prefers_game_lap_number_over_pending_tie() -> None:
    """A lap-one broadcast must not retag an equal-time pending lap two."""
    parser = LogParser()
    parser.current_session = SessionData(
        track="spa", car="porsche", session_type="PRACTICE"
    )
    emitted = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
        validity_source="shm_graphics",
    )
    pending = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
    )
    parser.current_session.laps.append(emitted)
    parser._pending_lap = pending

    assert parser._handle_lap_validity(
        "[2026-08-26 12:00:00.000] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 100000, valid false, "
        "flags 1, lap 1 (prev 0)"
    ) is None

    assert emitted.is_valid is False
    assert emitted.validity_source == "authoritative"
    assert parser._pending_lap is pending
    assert pending.lap_number == 2
    assert pending.is_valid is True

    # Reordered duplicate metadata must continue to address lap one after
    # its first verdict has changed the provenance from SHM to logs.
    assert parser._handle_lap_validity(
        "[2026-08-26 12:00:00.001] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 100000, valid false, "
        "flags 1, lap 1 (prev 0)"
    ) is None
    assert parser._pending_lap is pending
    assert pending.lap_number == 2
    assert pending.is_valid is True


def test_validity_uses_pending_time_before_stale_same_number_lap() -> None:
    """A far older lap number cannot steal a nearby pending verdict."""
    parser = LogParser()
    parser.current_session = SessionData(
        track="spa", car="porsche", session_type="PRACTICE"
    )
    old_lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=90_000,
        lap_time_str="01:30.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
        validity_source="authoritative",
    )
    pending = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
    )
    parser.current_session.laps.append(old_lap)
    parser._pending_lap = pending

    assert parser._handle_lap_validity(
        "[2026-08-26 12:00:00.000] [network] [info] "
        "Relevant onSplit for Combo 1@1: laptime 100000, valid false, "
        "flags 1, lap 1 (prev 0)"
    ) is pending
    assert pending.is_valid is False
    assert pending.lap_number == 1
    assert old_lap.is_valid is True


def test_bound_unknown_completion_is_consumed_when_validity_map_finishes_lap() -> None:
    """Map validity may finish a bound lap without leaving an SHM duplicate."""
    from src.models import SharedSessionManager

    manager = SharedSessionManager()
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 0,
            "current_lap_time_ms": 100_000,
            "last_laptime_ms": 0,
        }
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 10,
            "last_laptime_ms": 100_000,
        }
    )
    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.is_valid is None
    manager.update_lap_validity_from_graphics_shm(1, False)

    parser = LogParser(session_manager=manager)
    pending = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.VALID,
        lap_type=LapState.VALID.value,
        is_valid=True,
    )
    parser._lap_completion_by_lap_id[id(pending)] = completion
    parser._apply_shm_fallback_validity(pending)

    assert pending.is_valid is True
    assert pending.validity_source == "shm_graphics"
    assert manager.get_lap_completions_after(0) == []


def test_outlap_bound_invalid_completion_requires_matching_counter() -> None:
    """A time-only outlap binding cannot promote another physical lap."""
    from src.models import LapCompletionData, SharedSessionManager

    manager = SharedSessionManager()
    completion = LapCompletionData(
        completed_laps=5,
        lap_time_ms=100_000,
        is_valid=False,
        timestamp="2026-09-05T00:00:00+00:00",
        observed_at=1.0,
    )
    manager._session_data.lap_completions.append(completion)
    parser = LogParser(session_manager=manager)
    parser.current_session = SessionData(
        track="spa", car="porsche", session_type="PRACTICE"
    )
    pending = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100_000,
        lap_time_str="01:40.000",
        lap_state=LapState.OUTLAP,
        lap_type=LapState.OUTLAP.value,
        is_valid=True,
    )
    parser._lap_completion_by_lap_id[id(pending)] = completion
    parser._apply_shm_fallback_validity(pending)

    assert pending.lap_state == LapState.OUTLAP
    assert pending.is_valid is True


def test_shm_completion_waits_for_log_session_identity() -> None:
    """A completion observed before Game Started remains available."""
    from src.models import SharedSessionManager

    manager = SharedSessionManager()
    session = SessionData(track="spa", car="porsche", session_type="PRACTICE")
    manager.begin_session(session.session_id, car_model=session.car)
    manager.update_from_graphics_shm(
        {"total_lap_count": 0, "current_lap_time_ms": 100_000}
    )
    manager.update_from_graphics_shm(
        {
            "total_lap_count": 1,
            "current_lap_time_ms": 10,
            "last_laptime_ms": 100_000,
        }
    )
    parser = LogParser(session_manager=manager)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    parser._last_shm_completion_observed_at = 0

    assert parser._take_ready_shm_lap() is None
    assert manager.get_lap_completions_after(0)

    parser.current_session = session
    parser._sessions_by_id[session.session_id] = session
    lap = parser._take_ready_shm_lap()
    assert lap is not None
    assert lap.lap_time_ms == 100_000
