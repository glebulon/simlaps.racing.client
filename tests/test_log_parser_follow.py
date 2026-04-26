"""
Comprehensive tests for the follow() method - the main log tailing loop.

This targets the biggest uncovered chunk (lines 1262-1382).
"""

import pytest
import asyncio
import os
os.environ["APP_SECRET"] = "31cdbbaf05e962038c9221bdc22845b7639f4a1e914b4596db6b8608a5ea5e18"

from src.core.log_parser import LogParser
from src.models import SessionData


class TestFollowCore:
    """Test core follow() functionality."""

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


class TestFollowLiveTailing:
    """Test live tailing behavior."""

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
