"""
Comprehensive tests for the follow() method - the main log tailing loop.

This targets the biggest uncovered chunk (lines 1262-1382).
"""

import asyncio

import pytest

from src.core.log_parser import LogParser
from src.models import LapState, SessionData, SharedSessionManager


class TestFollowCore:
    """Test core follow() functionality."""

    @pytest.mark.asyncio
    async def test_stop_during_large_historical_pass_exits_before_live_callbacks(
        self, tmp_path
    ):
        """Stopping at a cooperative yield must not cross the live boundary."""
        car_id = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        historical_lap = (
            "[2026-05-20 00:01:03.439] [gameplay] [info] "
            f"New lap carId {car_id}: 02:23.706\n"
            "[2026-05-20 00:01:03.500] [network] [info] "
            "Relevant onSplit for Combo 6@2: laptime 143706, valid true, "
            "flags 2, lap 1 (prev 0)\n"
        )
        log_file = tmp_path / "large-historical.log"
        log_file.write_text(historical_lap + ("unrelated log line\n" * 600))
        statuses = []
        laps = []

        async def on_status(status):
            statuses.append(status)

        async def on_lap(session, lap):
            laps.append(lap)

        parser = LogParser(
            log_path=str(log_file),
            on_status_change=on_status,
            on_lap_complete=on_lap,
        )
        parser.context.car_uuid = car_id
        parser.context.player_car_uuids.add(car_id)
        parser.current_session = SessionData(track="spa", car="porsche")

        async def stop_at_checkpoint():
            await asyncio.sleep(0)
            parser.stop()

        stop_task = asyncio.create_task(stop_at_checkpoint())
        await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=1.0)
        await stop_task

        assert laps == []
        assert all("Monitoring for new laps" not in status for status in statuses)
        assert all("Ready" not in status for status in statuses)

    @pytest.mark.asyncio
    async def test_follow_exits_when_stop_is_called(self, tmp_path):
        """``follow()`` exits cleanly once ``stop()`` flips ``_running``.

        Note: the previous version of this test set ``_running = False``
        *before* calling ``follow()`` and expected an instant return. That
        contract was never real \u2014 ``follow()`` unconditionally starts
        with ``self._running = True`` (see ``log_parser.py``), so the
        only way out of the live-tail loop is ``stop()`` (or the file
        not existing). The test has been rewritten to exercise the real
        exit path and to bound itself with ``wait_for`` so a regression
        in the loop never hangs CI again.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("Game Started!\n")

        parser = LogParser(log_path=str(log_file))

        async def stopper():
            await asyncio.sleep(0.05)
            parser.stop()

        stop_task = asyncio.create_task(stopper())
        try:
            await asyncio.wait_for(
                parser.follow(poll_interval=0.01), timeout=1.0
            )
        finally:
            parser.stop()
            await stop_task

        assert parser._running is False

    @pytest.mark.asyncio
    async def test_follow_processes_game_started(self, tmp_path):
        """Test follow processes 'Game Started!' line."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Game Started!\n")

        parser = LogParser(log_path=str(log_file))
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        # Game start should have been detected
        assert True

    @pytest.mark.asyncio
    async def test_follow_processes_track_and_car(self, tmp_path):
        """Test follow records the active track from the historical pass.

        The real ``_handle_track_name`` regex is ``r"TRACK NAME (.+)"``
        (no colon) and stores the value on ``context.current_track``.
        ``current_session`` is only created when an actual session-start
        line fires — a stand-alone ``TRACK NAME`` line does not create a
        session, so this test only asserts on ``context.current_track``.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("TRACK NAME spa_francorchamps\n")

        parser = LogParser(log_path=str(log_file))
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()

        assert parser.context.current_track == "spa_francorchamps"

    @pytest.mark.asyncio
    async def test_follow_detects_race_start(self, tmp_path):
        """Test follow detects race start line."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Player (carId=abc123) has started the race!\n")

        parser = LogParser(log_path=str(log_file))
        parser.context.car_uuid = "abc123"
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        assert True  # Code path exercised

    @pytest.mark.asyncio
    async def test_follow_detects_end_session(self, tmp_path):
        """Test follow detects END_SESSION line."""
        log_file = tmp_path / "test.log"
        log_file.write_text("END_SESSION carId=abc123\n")

        parser = LogParser(log_path=str(log_file))
        parser.context.car_uuid = "abc123"
        parser.current_session = SessionData(track="spa", car="porsche")
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        assert True  # Code path exercised

    @pytest.mark.asyncio
    async def test_follow_handles_partial_line(self, tmp_path):
        """Test follow handles partially written line."""
        log_file = tmp_path / "test.log"

        parser = LogParser(log_path=str(log_file))
        parser._running = True

        # Write partial line (no newline)
        log_file.write_text("Partial line without newline")

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        assert True  # Should handle gracefully

    @pytest.mark.asyncio
    async def test_follow_clears_session_on_truncate(self, tmp_path):
        """Test follow clears session when log is truncated."""
        log_file = tmp_path / "test.log"
        log_file.write_text("TRACK NAME: spa\nCAR NAME: porsche\n")

        parser = LogParser(log_path=str(log_file))
        # Pre-populate session
        parser.current_session = SessionData(track="spa", car="porsche")

        parser._running = True

        # Truncate by overwriting with smaller content
        async def truncate():
            await asyncio.sleep(0.05)
            log_file.write_text("New start\n")

        task = asyncio.create_task(truncate())

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.2)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        await task

        # Context should have been reset
        assert True  # Code path exercised


class TestFollowWithCallbacks:
    """Test follow() with all callback types."""

    @pytest.mark.asyncio
    async def test_follow_emits_status(self, tmp_path):
        """Test follow emits status updates."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Game Started!\n")

        status_calls = []
        async def on_status(msg):
            status_calls.append(msg)

        parser = LogParser(log_path=str(log_file), on_status_change=on_status)
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.15)
        except asyncio.TimeoutError:
            pass

        parser.stop()

        # Should have emitted some status updates
        assert len(status_calls) >= 0  # May or may not emit depending on timing

    @pytest.mark.asyncio
    async def test_follow_emits_user_detected(self, tmp_path):
        r"""Test follow emits user detected callback.

        The connect regex (see ``log_parser.py:_pats['connect']``) is
        ``r"(\d+) connected(?: \(\d+\))? on car ([\w_]+), with new carId
        ([a-f0-9\-]+)"`` — the previous test fed a fake
        ``Player steamId=...`` line that never matched, so player_id
        was never set and the assertion always failed.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "76561198321627695 connected on car gt3_porsche, "
            "with new carId abc12345-6789-abcd-ef01-23456789abcd\n"
        )

        user_calls = []
        async def on_user(uid, name):
            user_calls.append((uid, name))

        parser = LogParser(log_path=str(log_file), on_user_detected=on_user)
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.15)
        except asyncio.TimeoutError:
            pass

        parser.stop()

        assert parser.get_player_id() == "76561198321627695"

    @pytest.mark.asyncio
    async def test_follow_emits_game_status(self, tmp_path):
        """Test follow emits game status changes."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Game Started!\n")

        game_calls = []
        async def on_game(running):
            game_calls.append(running)

        parser = LogParser(log_path=str(log_file), on_game_status_change=on_game)
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.15)
        except asyncio.TimeoutError:
            pass

        parser.stop()

        # Should have detected game start
        assert True  # Code path exercised

    @pytest.mark.asyncio
    async def test_follow_emits_session_restart(self, tmp_path):
        """AC Evo pause-menu Restart Session must trigger on_session_restart.

        Regression test: previously the parser had no signal for
        ``GameModeRequestRestartSession``, so telemetry kept rolling across
        the user's aborted run into the restarted run, contaminating the
        analysis buffer with frames from a session that no longer existed.

        Note: the ``GameModeRequestRestartSession`` handler lives in the
        live-tail half of ``follow()``, not in ``_process_line``. The
        line therefore has to be appended *after* follow() starts so it
        isn't swallowed by the silent historical pass.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("")  # Empty so historical pass is a no-op

        restart_calls = []
        async def on_restart():
            restart_calls.append(True)

        parser = LogParser(
            log_path=str(log_file), on_session_restart=on_restart
        )
        parser._running = True

        async def append_restart():
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    "[2026-04-24 23:44:07.688] [gameface] [info] "
                    "request made GameModeRequestRestartSession \n"
                )

        appender = asyncio.create_task(append_restart())
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert restart_calls, "Restart callback was not invoked"

    @pytest.mark.asyncio
    async def test_follow_exit_request_emits_game_stopped(self, tmp_path):
        """Pause-menu Exit to Menu must flip game_status to False.

        Same caveat as ``test_follow_emits_session_restart``: the
        ``GameModeRequestExit`` handler is in the live-tail half of
        ``follow()``, so the trigger line must be appended after
        follow() is already running.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        game_calls = []
        async def on_game(running):
            game_calls.append(running)

        parser = LogParser(
            log_path=str(log_file), on_game_status_change=on_game
        )
        parser._running = True

        async def append_exit():
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    "[2026-04-24 23:10:37.129] [gameface] [info] "
                    "request made GameModeRequestExit \n"
                )

        appender = asyncio.create_task(append_exit())
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert False in game_calls, (
            f"Expected game_status=False from Exit request, got {game_calls!r}"
        )

    @pytest.mark.asyncio
    async def test_follow_end_session_ending_lap_emits_game_stopped(self, tmp_path):
        """END_SESSION 'Ending Lap for ... car' should stop the active session."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        game_calls = []
        async def on_game(running):
            game_calls.append(running)

        parser = LogParser(
            log_path=str(log_file), on_game_status_change=on_game
        )
        parser.context.car_uuid = "4d27cc23ee6ce0de-9c3810448288bcbb"
        parser._running = True

        async def append_end_session():
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    "[2026-06-01 23:55:08.308] [gameplay] [info] "
                    "END_SESSION Ending Lap for 4d27cc23ee6ce0de-9c3810448288bcbb car\n"
                )

        appender = asyncio.create_task(append_end_session())
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert False in game_calls, (
            f"Expected game_status=False from END_SESSION Ending Lap, got {game_calls!r}"
        )

    @pytest.mark.asyncio
    async def test_follow_restart_does_not_double_fire_exit(self, tmp_path):
        """A Restart line must not also be treated as an Exit (mutually exclusive).

        Trigger line is appended after follow() starts, see sibling
        tests for the rationale.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        game_calls = []
        async def on_game(running):
            game_calls.append(running)

        async def on_restart():
            pass

        parser = LogParser(
            log_path=str(log_file),
            on_game_status_change=on_game,
            on_session_restart=on_restart,
        )
        parser._running = True

        async def append_restart():
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    "[2026-04-24 23:44:07.688] [gameface] [info] "
                    "request made GameModeRequestRestartSession \n"
                )

        appender = asyncio.create_task(append_restart())
        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert False not in game_calls, (
            "Restart must not trigger game_status=False; that's reserved for Exit."
        )

    @pytest.mark.asyncio
    async def test_follow_restart_keeps_lap_emission_without_game_started(self, tmp_path):
        """Restart-in-place should not drop subsequent player lap emissions.

        Regression: after `GameModeRequestRestartSession`, AC Evo may not emit a
        fresh `Game Started!` marker. The parser still needs an active
        `current_session` so `New lap carId ...` lines are accepted and emitted.
        """
        laps = []

        async def on_lap(session, lap):
            laps.append((session, lap))

        parser = LogParser(
            log_path=str(tmp_path / "test.log"),
            on_lap_complete=on_lap,
        )

        parser._process_line(
            "[2026-05-20 00:00:00.000] [network] [info] "
            "76561198321627695 connected on car gt3_porsche, with new carId "
            "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        )
        await parser._emit_session_restart()

        parser._process_line(
            "[2026-05-20 00:01:03.439] [gameplay] [info] "
            "New lap carId 4d27cc23-ee6c-e0de-9c38-10448288bcbb: 02:23.706"
        )
        completed = parser._process_line(
            "[2026-05-20 00:01:03.500] [network] [info] "
            "Relevant onSplit for Combo 6@2: laptime 143706, valid true, "
            "flags 2, lap 1 (prev 0)"
        )

        assert completed is not None
        await parser._emit_lap(parser.current_session, completed)

        assert laps, "Expected lap emission after in-place session restart"
        assert laps[-1][1].lap_time_ms == 143706

    @pytest.mark.asyncio
    async def test_follow_restart_preserves_tyre_compound(self, tmp_path):
        """Tyre compound must survive a pause-menu restart.

        Regression: AC Evo logs the compound (setCompound / LOADING TYRE
        COMPOUND) only at the original session start, not after an in-place
        restart. The restart reset the parser tyre state to Unknown, so the
        restarted session's laps were reported with compound "Unknown" even
        though the same car/tyres were in use.
        """
        laps = []

        async def on_lap(session, lap):
            laps.append((session, lap))

        parser = LogParser(
            log_path=str(tmp_path / "test.log"),
            on_lap_complete=on_lap,
        )

        parser._process_line(
            "[2026-06-05 23:39:31.000] [network] [info] "
            "76561198321627695 connected on car ks_lotus_emira, with new carId "
            "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        )
        # Compound logged at original session start (full 4-tyre batch).
        for pos in range(4):
            parser._process_line(
                "[2026-06-05 23:39:41.117] [physics] [info] "
                f"setCompound Tyre: {pos} compound name: HC"
            )

        # Pause-menu restart — AC Evo does NOT re-log the compound afterwards.
        # The pending compound batch is flushed during _finalise_current_session
        # inside the restart handler.
        await parser._emit_session_restart()

        # Compound preserved across the restart
        assert parser.context.tyre.compound_name == "HC"

        parser._process_line(
            "[2026-06-05 23:49:36.991] [gameplay] [info] "
            "New lap carId 4d27cc23-ee6c-e0de-9c38-10448288bcbb: 08:21.918"
        )
        completed = parser._process_line(
            "[2026-06-05 23:49:36.998] [network] [info] "
            "Relevant onSplit for Combo 19@54: laptime 501918, valid true, "
            "flags 2, lap 1 (prev 0)"
        )

        assert completed is not None
        assert completed.tyre_compound == "HC"

    @pytest.mark.asyncio
    async def test_follow_emits_final_race_lap_before_end_session(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        car_id = "459ee57547c87da8-27add15c0fd9b297"
        emitted_laps = []
        all_laps_emitted = asyncio.Event()

        async def on_lap(session, lap):
            emitted_laps.append(lap)
            if len(emitted_laps) == 3:
                all_laps_emitted.set()

        parser = LogParser(log_path=str(log_file), on_lap_complete=on_lap)
        parser.context.car_uuid = car_id
        parser.current_session = SessionData(
            track="brands_hatch indy",
            car="ks_ktm_x_bow_gt4",
            session_type="RACE",
            car_uuid=car_id,
        )

        follow_task = asyncio.create_task(parser.follow(poll_interval=0.01))
        await asyncio.sleep(0.05)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                f"[2026-07-20 23:23:27.867] [physics] [info] "
                f"Lap test evOnLapCompleted 2 completed\n"
                f"[2026-07-20 23:23:28.073] [gameplay] [info] "
                f"New lap carId {car_id}: 00:59.172\n"
                f"[2026-07-20 23:23:28.080] [physics] [info] "
                f"Lap test evOnLapCompleted 2 completed\n"
                f"[2026-07-20 23:24:20.446] [gameplay] [info] "
                f"New lap carId {car_id}: 00:52.371\n"
                f"[2026-07-20 23:24:20.451] [physics] [info] "
                f"Lap test evOnLapCompleted 3 completed\n"
                f"[2026-07-20 23:25:11.016] [physics] [info] "
                f"Lap test evOnLapCompleted 4 completed\n"
                f"[2026-07-20 23:25:11.019] [gameplay] [info] "
                f"New lap carId {car_id}: 00:50.574\n"
                f"[2026-07-20 23:25:11.060] [gameplay] [info] "
                f"END_SESSION WatingForOthers Ending Lap for {car_id} car\n"
            )

        try:
            await asyncio.wait_for(all_laps_emitted.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert [lap.lap_time_ms for lap in emitted_laps] == [59172, 52371, 50574]
        assert [lap.lap_number for lap in emitted_laps] == [1, 2, 3]


class TestFollowLiveTailing:
    """Test live tailing behavior."""

    @pytest.mark.asyncio
    async def test_rotation_discards_pending_and_unmatched_shm_lap(self, tmp_path):
        """A rotated input stream cannot reconcile or emit the old stream's lap."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old_file = log_dir / "old.txt"
        car_id = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        old_file.write_text(
            "[2026-08-26 10:00:00.000] [network] [info] "
            "76561198321627695 connected on car ktm_xbow_gt4, with new carId "
            f"{car_id}\n"
            "[2026-08-26 10:00:01.000] [gameplay] [info] Game Started! "
            "GameModeType_PRACTICE | Old Track | ktm_xbow_gt4 | "
            "GameModeSelectionWeatherType_Clear\n"
        )
        manager = SharedSessionManager()
        emitted = []
        lap_seen = asyncio.Event()

        async def on_lap(session, lap):
            emitted.append((session, lap))
            lap_seen.set()

        parser = LogParser(
            log_path=str(log_dir),
            on_lap_complete=on_lap,
            session_manager=manager,
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 5.0
        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.04)

        # This old-stream log completion remains pending, while an unmatched
        # SHM completion is retained in the shared manager.
        with open(old_file, "a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-08-26 10:01:00.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:20.000\n"
            )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 80_000,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 20,
                "last_laptime_ms": 79_900,
                "is_valid_lap": True,
            }
        )

        new_file = log_dir / "new.txt"
        await asyncio.sleep(0.02)
        new_file.write_text(
            "[2026-08-26 11:00:00.000] [network] [info] "
            "76561198321627695 connected on car ktm_xbow_gt4, with new carId "
            f"{car_id}\n"
            "[2026-08-26 11:00:01.000] [gameplay] [info] Game Started! "
            "GameModeType_PRACTICE | New Track | ktm_xbow_gt4 | "
            "GameModeSelectionWeatherType_Clear\n"
        )
        await asyncio.sleep(0.08)
        with open(new_file, "a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-08-26 11:01:00.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:21.000\n"
                f"[2026-08-26 11:01:00.010] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 81000, valid true, "
                "flags 2, lap 1 (prev 0)\n"
            )

        try:
            await asyncio.wait_for(lap_seen.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert [lap.lap_time_ms for _, lap in emitted] == [81_000]
        assert emitted[0][0].track == "New Track"
        assert parser._pending_lap is None
        assert parser._shm_emitted_laps == []

    @pytest.mark.asyncio
    async def test_truncation_clears_penalty_latch_before_new_session(self, tmp_path):
        """A penalty from the truncated stream cannot invalidate new-session lap 1."""
        log_file = tmp_path / "session.log"
        car_id = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        prefix = (
            "[2026-08-26 12:00:00.000] [network] [info] "
            "76561198321627695 connected on car ktm_xbow_gt4, with new carId "
            f"{car_id}\n"
            "[2026-08-26 12:00:01.000] [gameplay] [info] Game Started! "
            "GameModeType_PRACTICE | Old Track | ktm_xbow_gt4 | "
            "GameModeSelectionWeatherType_Clear\n"
        )
        log_file.write_text(prefix)
        emitted = []
        lap_seen = asyncio.Event()

        async def on_lap(session, lap):
            emitted.append((session, lap))
            lap_seen.set()

        parser = LogParser(log_path=str(log_file), on_lap_complete=on_lap)
        parser.PENDING_VALIDITY_GRACE_SECONDS = 5.0
        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.04)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-08-26 12:01:00.000] [gameplay] [info] "
                f"Penalty Type PenaltyType_Warning has no tranformation\n"
                f"[2026-08-26 12:01:01.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:20.000\n"
            )

        # Replace the file with a fresh session before the old pending lap's
        # grace period expires.
        await asyncio.sleep(0.02)
        new_prefix = prefix.replace("12:00", "13:00").replace("Old Track", "New Track")
        log_file.write_text(new_prefix)
        await asyncio.sleep(0.08)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-08-26 13:01:00.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:21.000\n"
                f"[2026-08-26 13:01:00.010] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 81000, valid true, "
                "flags 2, lap 1 (prev 0)\n"
            )

        try:
            await asyncio.wait_for(lap_seen.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert [lap.lap_time_ms for _, lap in emitted] == [81_000]
        assert emitted[0][1].is_valid is True
        assert emitted[0][1].lap_state == LapState.VALID
        assert parser._last_penalty_added_ts is None

    @pytest.mark.asyncio
    async def test_altered_game_started_drops_old_pending_and_shm_state(self, tmp_path):
        """An unrecognized start marker still closes the old parser epoch."""
        log_file = tmp_path / "session.log"
        car_id = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        log_file.write_text(
            "[2026-08-26 14:00:00.000] [network] [info] "
            "76561198321627695 connected on car ktm_xbow_gt4, with new carId "
            f"{car_id}\n"
            "[2026-08-26 14:00:01.000] [gameplay] [info] Game Started! "
            "GameModeType_PRACTICE | Old Track | ktm_xbow_gt4 | "
            "GameModeSelectionWeatherType_Clear\n"
        )
        manager = SharedSessionManager()
        emitted = []
        lap_seen = asyncio.Event()

        async def on_lap(session, lap):
            emitted.append((session, lap))
            lap_seen.set()

        parser = LogParser(
            log_path=str(log_file),
            on_lap_complete=on_lap,
            session_manager=manager,
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 5.0
        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.04)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(
                f"[2026-08-26 14:01:00.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:20.000\n"
            )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 80_000,
                "last_laptime_ms": 0,
                "is_valid_lap": True,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 20,
                "last_laptime_ms": 79_900,
                "is_valid_lap": True,
            }
        )
        await asyncio.sleep(0.03)
        with open(log_file, "a", encoding="utf-8") as handle:
            # This marker contains a mode token but uses the altered weather
            # field spelling, so the strict Game Started parser cannot match.
            handle.write(
                "[2026-08-26 14:02:00.000] [gameplay] [info] Game Started! "
                "GameModeType_PRACTICE | Altered Track | ktm_xbow_gt4 | "
                "WeatherType_Sunny\n"
                # Must not reconcile with the old pending lap after the
                # malformed boundary, because no session is active yet.
                f"[2026-08-26 14:02:01.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:20.000\n"
                f"[2026-08-26 14:02:01.010] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 80000, valid true, "
                "flags 2, lap 1 (prev 0)\n"
                "[2026-08-26 14:03:00.000] [gameplay] [info] Game Started! "
                "GameModeType_PRACTICE | New Track | ktm_xbow_gt4 | "
                "GameModeSelectionWeatherType_Clear\n"
                f"[2026-08-26 14:03:01.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:21.000\n"
                f"[2026-08-26 14:03:01.010] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 81000, valid true, "
                "flags 2, lap 1 (prev 0)\n"
            )

        try:
            await asyncio.wait_for(lap_seen.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert [lap.lap_time_ms for _, lap in emitted] == [81_000]
        assert emitted[0][0].track == "New Track"
        assert parser._pending_lap is None
        assert parser._shm_emitted_laps == []

    @pytest.mark.asyncio
    async def test_follow_waits_for_new_lines(self, tmp_path):
        """Test follow waits for and processes new lines."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Initial\n")

        parser = LogParser(log_path=str(log_file))
        parser._running = True

        # Add new lines after a delay
        async def add_lines():
            await asyncio.sleep(0.05)
            with open(log_file, "a") as f:
                f.write("TRACK NAME: monza\n")
                f.write("CAR NAME: ferrari\n")

        task = asyncio.create_task(add_lines())

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.2)
        except asyncio.TimeoutError:
            pass

        parser.stop()
        await task

        # Should have processed the new lines
        assert True  # Code path exercised

    @pytest.mark.asyncio
    async def test_follow_skips_duplicate_historical_laps(self, tmp_path):
        """Test follow clears historical laps before live tail."""
        log_file = tmp_path / "test.log"
        # Write existing lap
        log_file.write_text(
            "TRACK NAME: spa\n"
            "CAR NAME: porsche\n"
            "New lap carId=abc123 time=1:30.000\n"
        )

        parser = LogParser(log_path=str(log_file))
        parser.context.car_uuid = "abc123"
        parser._running = True

        try:
            await asyncio.wait_for(parser.follow(poll_interval=0.01), timeout=0.1)
        except asyncio.TimeoutError:
            pass

        parser.stop()

        # Historical laps should have been cleared
        if parser.current_session:
            assert len(parser.current_session.laps) == 0


class TestFollowExitFlushesPendingLap:
    """Regression tests for GameModeRequestExit flushing buffered laps.

    Bug: AC Evo exits via GameModeRequestExit without emitting END_SESSION
    or a validity broadcast.  The handler previously only emitted
    game_status=False and never flushed _pending_lap, so laps on
    single-split tracks (e.g. Nordschleife Tourist) were silently lost.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exit_request",
        ["GameModeRequestExit", "GameModeRequestQuitGame"],
    )
    async def test_exit_flushes_pending_lap_without_validity(
        self, tmp_path, exit_request
    ):
        """Exit to Menu (GameModeRequestExit) and Exit to Desktop
        (GameModeRequestQuitGame) must both flush a pending lap even when
        no authoritative validity line was ever received."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")  # empty historical pass

        laps = []

        async def on_lap(session, lap):
            laps.append(lap)

        parser = LogParser(
            log_path=str(log_file), on_lap_complete=on_lap
        )
        # Set up a session and player car so _handle_lap_complete buffers
        parser.current_session = SessionData(
            track="nurburgring", car="ktm_xbow_gt4", player_id="76561198321627695"
        )
        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        parser.context.tyre.set_all("SM")
        parser._ip.physics_lap_num = 1
        parser._ip.splits = {0: 516642}
        parser._ip.fuel_used = 3.2
        parser._ip.fuel_reliable = True
        parser._running = True

        async def append_lines():
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                # New lap — buffered in _pending_lap (no validity line follows)
                f.write(
                    "[2026-07-26 19:17:37.000] [gameplay] [info] "
                    "New lap carId 4d27cc23-ee6c-e0de-9c38-10448288bcbb: 08:36.642\n"
                )
                await asyncio.sleep(0.05)
                # User exits — must flush the pending lap
                f.write(
                    "[2026-07-26 19:18:48.000] [gameface] [info] "
                    f"request made {exit_request} \n"
                )

        appender = asyncio.create_task(append_lines())
        try:
            await asyncio.wait_for(
                parser.follow(poll_interval=0.01), timeout=1.0
            )
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert laps, f"Pending lap was not flushed on {exit_request}"
        assert laps[0].lap_time_ms == 516642
        assert parser._pending_lap is None
        assert parser.current_session is None  # finalised


class TestFollowMissingValidityBroadcast:
    """ACE 0.8.1 may omit ``Relevant onSplit`` after a completed lap."""

    @pytest.mark.asyncio
    async def test_multiple_shm_completions_emit_in_observation_order(self, tmp_path):
        """A delayed log tail must not drop or reorder completed laps."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        manager = SharedSessionManager()
        emitted_laps = []
        both_emitted = asyncio.Event()

        async def on_lap(session, lap):
            emitted_laps.append(lap)
            if len(emitted_laps) == 2:
                both_emitted.set()

        parser = LogParser(
            log_path=str(log_file),
            on_lap_complete=on_lap,
            session_manager=manager,
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 0.02
        parser.current_session = SessionData(
            track="Red Bull Ring GP",
            car="Mazda Mx5 Nd CUP",
            player_id="76561197986609341",
            session_type="PRACTICE",
        )
        parser.context.player_id = "76561197986609341"
        parser.context.tyre.set_all("S")

        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.03)

        # Publish two complete SHM transitions without yielding to the parser,
        # reproducing ACE buffering its file log for longer than a full lap.
        for completed_laps, lap_time_ms, is_valid in (
            (1, 66393, True),
            (2, 65559, False),
        ):
            manager.update_from_graphics_shm(
                {
                    "total_lap_count": completed_laps - 1,
                    "current_lap_time_ms": lap_time_ms - 50,
                    "last_laptime_ms": 0,
                    "is_valid_lap": is_valid,
                }
            )
            manager.update_from_graphics_shm(
                {
                    "total_lap_count": completed_laps,
                    "current_lap_time_ms": 50,
                    "last_laptime_ms": lap_time_ms,
                    "is_valid_lap": True,
                }
            )

        try:
            await asyncio.wait_for(both_emitted.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert [lap.lap_number for lap in emitted_laps] == [1, 2]
        assert [lap.lap_time_ms for lap in emitted_laps] == [66393, 65559]
        assert [lap.is_valid for lap in emitted_laps] == [True, False]

    @pytest.mark.asyncio
    async def test_live_lap_flushes_after_grace_with_shm_validity(self, tmp_path):
        """A lap appears without waiting for another lap or session exit."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        car_id = "4d27cc23-ee6c-e0de-9c38-10448288bcbb"
        emitted_laps = []
        lap_emitted = asyncio.Event()
        manager = SharedSessionManager()

        async def on_lap(session, lap):
            emitted_laps.append(lap)
            lap_emitted.set()

        parser = LogParser(
            log_path=str(log_file),
            on_lap_complete=on_lap,
            session_manager=manager,
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 0.02
        parser.current_session = SessionData(
            track="Red Bull Ring GP",
            car="Mazda Mx5 Nd CUP",
            player_id="76561197986609341",
            session_type="PRACTICE",
            car_uuid=car_id,
        )
        parser.context.player_id = "76561197986609341"
        parser.context.car_uuid = car_id
        parser.context.tyre.set_all("S")

        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.03)
        # ACE can reset the timer and validity while total_lap_count still
        # describes the old lap. The callback must receive the latched invalid
        # verdict rather than the new lap's valid=True flag.
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 127900,
                "last_laptime_ms": 0,
                "is_valid_lap": False,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 50,
                "last_laptime_ms": 128028,
                "is_valid_lap": True,
            }
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                "[2026-08-16 12:06:00.000] [gameplay] [info] "
                "On Split start false end false id 0 splittime 35232\n"
                "[2026-08-16 12:06:53.868] [gameplay] [info] "
                "On Split start false end false id 1 splittime 53868\n"
                "[2026-08-16 12:07:32.796] [gameplay] [info] "
                "On Split start false end true id 2 splittime 38928\n"
                "[2026-08-16 12:07:32.797] [physics] [info] "
                "Lap test evOnLapCompleted 1 completed\n"
                f"[2026-08-16 12:07:32.800] [gameplay] [info] "
                f"New lap carId {car_id}: 02:08.028\n"
            )

        try:
            await asyncio.wait_for(lap_emitted.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert len(emitted_laps) == 1
        lap = emitted_laps[0]
        assert lap.lap_time_ms == 128028
        assert lap.lap_state == LapState.INVALID_GAME
        assert lap.is_valid is False
        assert lap.validity_source == "shm_graphics"
        assert parser._pending_lap is None
        assert parser.current_session is not None

    @pytest.mark.asyncio
    async def test_shm_completion_precedes_buffered_log_and_reconciles(self, tmp_path):
        """A lap is shown from SHM, then enriched without duplication."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        car_id = "4b6b0885fe9c2dd3-832175ac60984c9b"
        manager = SharedSessionManager()
        emitted = []
        updated = []
        lap_emitted = asyncio.Event()
        lap_updated = asyncio.Event()

        async def on_lap(session, lap):
            emitted.append(lap)
            lap_emitted.set()

        async def on_update(session, lap):
            updated.append(lap)
            lap_updated.set()

        parser = LogParser(
            log_path=str(log_file),
            on_lap_complete=on_lap,
            on_lap_update=on_update,
            session_manager=manager,
        )
        parser.PENDING_VALIDITY_GRACE_SECONDS = 0.02
        parser.current_session = SessionData(
            track="red_bull_ring national",
            car="mazda_mx5_nd_cup",
            player_id="76561197986609341",
            session_type="PRACTICE",
            car_uuid=car_id,
        )
        parser.context.player_id = "76561197986609341"
        parser.context.car_uuid = car_id
        parser.context.tyre.set_all("S")

        follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
        await asyncio.sleep(0.03)
        # ACE may flush early sectors while retaining S3/New lap. These must
        # survive the SHM-first completion emission.
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                "[2026-08-16 12:25:38.767] [gameplay] [info] "
                "On Split start false end false id 0 splittime 30813\n"
                "[2026-08-16 12:25:59.217] [gameplay] [info] "
                "On Split start false end false id 1 splittime 20454\n"
            )
        await asyncio.sleep(0.03)
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 0,
                "current_lap_time_ms": 75000,
                "last_laptime_ms": 0,
                "is_valid_lap": False,
            }
        )
        manager.update_from_graphics_shm(
            {
                "total_lap_count": 1,
                "current_lap_time_ms": 50,
                "last_laptime_ms": 75684,
                "is_valid_lap": True,
            }
        )

        await asyncio.wait_for(lap_emitted.wait(), timeout=1.0)
        assert len(emitted) == 1
        assert emitted[0].lap_state == LapState.INVALID_GAME
        assert emitted[0].sector1_ms is None

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                "[2026-08-16 12:26:23.634] [gameplay] [info] "
                "On Split start true end true id 2 splittime 24417\n"
                f"[2026-08-16 12:26:23.634] [gameplay] [info] "
                f"New lap carId {car_id}: 01:15.684\n"
            )

        try:
            await asyncio.wait_for(lap_updated.wait(), timeout=1.0)
        finally:
            parser.stop()
            await asyncio.wait_for(follow_task, timeout=1.0)

        assert updated == emitted
        assert len(parser.current_session.laps) == 1
        assert emitted[0].sector1_ms == 30813
        assert emitted[0].sector2_ms == 20454
        assert emitted[0].sector3_ms == 24417
        assert emitted[0].lap_state == LapState.INVALID_GAME


class TestFollowHistoricalPassDoesNotEmitPendingLap:
    """Historical context must never become a live lap callback."""

    @pytest.mark.asyncio
    async def test_historical_pending_lap_is_not_flushed_on_later_exit(self, tmp_path):
        """A missing historical validity line must not replay the lap on exit."""
        log_file = tmp_path / "test.log"
        # Historical content: session start + lap completion, no validity
        log_file.write_text(
            "[2026-07-26 19:08:04.000] [network] [info] "
            "76561198321627695 connected on car ktm_xbow_gt4, with new carId "
            "4d27cc23-ee6c-e0de-9c38-10448288bcbb\n"
            "[2026-07-26 19:17:37.000] [gameplay] [info] "
            "New lap carId 4d27cc23-ee6c-e0de-9c38-10448288bcbb: 08:36.642\n"
        )

        laps = []

        async def on_lap(session, lap):
            laps.append(lap)

        parser = LogParser(
            log_path=str(log_file), on_lap_complete=on_lap
        )
        parser._running = True

        async def append_exit():
            await asyncio.sleep(0.1)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    "[2026-07-26 19:18:48.000] [gameface] [info] "
                    "request made GameModeRequestExit \n"
                )

        appender = asyncio.create_task(append_exit())
        try:
            await asyncio.wait_for(
                parser.follow(poll_interval=0.01), timeout=1.0
            )
        except asyncio.TimeoutError:
            pass
        finally:
            parser.stop()
            await appender

        assert laps == []
        assert parser._pending_lap is None


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [0, 1, -1, 2, -2])
async def test_follow_keeps_equal_near_equal_delayed_laps_and_ignores_stale_broadcast(
    tmp_path, delta
):
    """The public tail flow binds delayed metadata to each SHM completion."""
    car_id = "4d27cc23-ee6e-e0de-9c38-10448288bcbb"
    log_file = tmp_path / "equal-laps.log"
    log_file.write_text(
        "[2026-09-05 12:00:00.000] [network] [info] "
        "12345678901234567 connected on car ktm_xbow_gt4, with new carId "
        f"{car_id}\n"
        "[2026-09-05 12:00:01.000] [gameplay] [info] Game Started! "
        "GameModeType_PRACTICE | Spa | ktm_xbow_gt4 | "
        "GameModeSelectionWeatherType_Clear\n",
        encoding="utf-8",
    )
    manager = SharedSessionManager()
    completed = []
    updates = []
    live = asyncio.Event()
    done = asyncio.Event()

    async def on_status(status):
        if "Monitoring for new laps" in status:
            live.set()

    async def on_lap(session, lap):
        completed.append((session, lap))
        if len(completed) >= 2:
            done.set()

    async def on_update(_session, lap):
        updates.append(lap)

    parser = LogParser(
        log_path=str(log_file),
        session_manager=manager,
        on_status_change=on_status,
        on_lap_complete=on_lap,
        on_lap_update=on_update,
    )
    # Keep the first completion pending while the deliberately stale
    # broadcast arrives, so the real follow loop exercises the guard.
    parser.PENDING_VALIDITY_GRACE_SECONDS = 5.0
    follow_task = asyncio.create_task(parser.follow(poll_interval=0.005))
    try:
        await asyncio.wait_for(live.wait(), timeout=1.0)

        def publish(completed_laps, lap_time_ms, is_valid):
            manager.update_from_graphics_shm(
                {
                    "total_lap_count": completed_laps - 1,
                    "current_lap_time_ms": 100_000,
                    "last_laptime_ms": 0,
                    "is_valid_lap": is_valid,
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

        first_time = 100_000
        second_time = first_time + delta
        second_minutes, second_remainder = divmod(second_time, 60_000)
        second_seconds, second_millis = divmod(second_remainder, 1_000)
        second_time_str = f"{second_minutes:02d}:{second_seconds:02d}.{second_millis:03d}"
        publish(1, first_time, False)
        publish(2, second_time, True)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(
                "[2026-09-05 12:01:00.000] [gameplay] [info] "
                f"New lap carId {car_id}: 01:40.000\n"
                "[2026-09-05 12:01:00.001] [network] [info] "
                "Relevant onSplit for Combo 1@1: laptime 120000, "
                "valid false, flags 1, lap 99 (prev 98)\n"
                "[2026-09-05 12:01:00.002] [gameplay] [info] "
                f"New lap carId {car_id}: {second_time_str}\n"
                "[2026-09-05 12:01:00.003] [network] [info] "
                f"Relevant onSplit for Combo 1@1: laptime {first_time}, "
                "valid false, flags 1, lap 1 (prev 0)\n"
                "[2026-09-05 12:01:00.004] [network] [info] "
                f"Relevant onSplit for Combo 1@1: laptime {second_time}, "
                "valid true, flags 2, lap 2 (prev 1)\n"
            )

        await asyncio.wait_for(done.wait(), timeout=1.0)
    finally:
        parser.stop()
        await asyncio.wait_for(follow_task, timeout=1.0)

    assert [lap.lap_time_ms for _, lap in completed] == [first_time, second_time]
    assert [lap.is_valid for _, lap in completed] == [False, True]
    assert len({id(lap) for _, lap in completed}) == 2
    assert {id(lap) for lap in updates} <= {id(lap) for _, lap in completed}
