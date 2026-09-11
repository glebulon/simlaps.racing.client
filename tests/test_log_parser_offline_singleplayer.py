"""
Regression tests for offline single-player sessions that emit neither a
``Game Started!`` line nor a network ``connect`` line.

Reproduces the Nordschleife / Alpine A110 S session from
logs/game_logs_20260911_133627.txt where the player car is only identifiable
via ``onSetPlayerCurrentCarCommand: Set new car`` + ``ServerVehicleSystem ...
Creating Car (<name> <steamid>)``, and the player's lap was dropped because no
session/car was ever bound.
"""

from src.core.log_parser import LogParser

STEAM_ID = "76561198321627695"
# "Set new car" uses fully-dashed UUID; runtime lap/split lines use the
# ServerVehicleSystem compact-dash form. Both normalise to the same value.
CAR_UUID_DASHED = "44609029-831d-84ea-76ee-f377d411e189"
CAR_UUID_RUNTIME = "44609029831d84ea-76eef377d411e189"


SET_NEW_CAR = (
    "[2026-09-11 13:15:02.430] [gameplay] [info] ACEVO-2629 "
    "onSetPlayerCurrentCarCommand: Set new car "
    f"{CAR_UUID_DASHED} content\\cars\\ks_alpine_a110_s\\presets\\"
    "preset_a110s_mech_1.mechanicalcarpreset"
)
CREATING_CAR = (
    "[2026-09-11 13:15:02.430] [server] [info] [C:3|G:0] "
    f"[ServerVehicleSystem][{CAR_UUID_RUNTIME}] Creating Car "
    f"(Glebulon  \t{STEAM_ID})"
)
SPLIT_START = (
    "[2026-09-11 13:29:27.842] [gameplay] [info] "
    "On Split start false end false id 0 splittime 0"
)
SPLIT_MID = (
    "[2026-09-11 13:32:14.768] [gameplay] [info] "
    "On Split start false end false id 1 splittime 166929"
)
PHYSICS_LAP = (
    "[2026-09-11 13:35:30.931] [physics] [info] "
    "Lap test evOnLapCompleted 2 completed"
)
NEW_LAP = (
    "[2026-09-11 13:35:30.966] [gameplay] [info] "
    f"New lap carId {CAR_UUID_RUNTIME}: 08:20.409"
)


class TestOfflineSinglePlayerBinding:
    def test_creating_car_binds_player_without_connect_line(self):
        parser = LogParser()
        parser._process_line(SET_NEW_CAR)
        parser._process_line(CREATING_CAR)

        assert parser.context.player_id == STEAM_ID
        assert parser._is_player_car(CAR_UUID_RUNTIME)
        assert parser.context.current_car == "ks_alpine_a110_s"

    def test_set_new_car_alone_does_not_bind(self):
        """The car model line without the authoritative Steam-ID binding must
        not, by itself, mark a player car."""
        parser = LogParser()
        parser._process_line(SET_NEW_CAR)

        assert parser.context.car_uuid is None
        assert not parser._is_player_car(CAR_UUID_RUNTIME)

    def test_creating_car_ignores_non_steam_ids(self):
        parser = LogParser()
        line = (
            "[2026-09-11 13:15:02.430] [server] [info] "
            "[ServerVehicleSystem][cccccccccccccccc-dddddddddddddddd] "
            "Creating Car (AI_Driver  \t12345)"
        )
        parser._process_line(line)

        assert parser.context.player_id is None
        assert parser.current_session is None


class TestOfflineSinglePlayerFallbackSession:
    def test_lap_emitted_without_game_started_or_connect(self):
        """The player's lap must be captured even though the log has no
        ``Game Started!`` and no ``connected ... with new carId`` line."""
        parser = LogParser()
        parser._process_line(SET_NEW_CAR)
        parser._process_line(CREATING_CAR)

        # Driving begins — first practice split should create a session.
        parser._process_line(SPLIT_START)
        assert parser.current_session is not None
        assert parser.current_session.session_type == "PRACTICE"

        parser._process_line(SPLIT_MID)
        parser._process_line(PHYSICS_LAP)
        completed = parser._process_line(NEW_LAP)

        # The lap is captured — either returned immediately or buffered awaiting
        # the game's authoritative validity flag (which is absent in this log).
        lap = completed or parser._pending_lap
        assert lap is not None
        assert lap.lap_time_ms == parser._parse_lap_time_ms("08:20.409")

        # It must land in the session once finalised.
        parser._finalise_current_session()
        assert any(
            lp.lap_time_ms == parser._parse_lap_time_ms("08:20.409")
            for lp in parser.sessions[-1].laps
        )

    def test_new_lap_alone_creates_session_and_emits(self):
        """Even if splits are missed, a player ``New lap`` should still create a
        fallback session and produce the lap."""
        parser = LogParser()
        parser._process_line(SET_NEW_CAR)
        parser._process_line(CREATING_CAR)

        completed = parser._process_line(NEW_LAP)

        assert parser.current_session is not None
        assert completed is not None or parser._pending_lap is not None

    def test_no_session_created_before_car_bound(self):
        """Driving signals must not create a session until a player car is
        bound (guards against menu/loading noise)."""
        parser = LogParser()
        parser._process_line(SPLIT_START)
        assert parser.current_session is None
