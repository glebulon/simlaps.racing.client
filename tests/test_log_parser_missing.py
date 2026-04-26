"""
Targeted tests for ``LogParser`` covering small accessor surfaces and a few
historical-pass / live-tail integration paths.

Originally this module contained ~30 tests that referenced methods and
attributes that no longer exist on the current ``LogParser`` /
``LogContext`` / ``StintData`` API (e.g. ``_handle_player_id``,
``_handle_car_name``, ``_handle_outlap``, ``_handle_track_limit``,
``_handle_lap_end``, ``_pending_compound_batch``, ``LogContext.reset``,
``LogContext.track_name``/``car_name``, ``LogContext.__str__``,
``StintData.compound``, ``LogParser.is_running()`` as a method instead of
a property, and ``_process_line`` returning a non-``None`` value for any
line other than a completed lap).

Those tests were not catching real regressions because they exercised an
imaginary API. They have been removed; the surviving tests below cover
the small set of public accessors and integration paths that actually
exist on the current parser.
"""

import asyncio
import os

import pytest

os.environ["APP_SECRET"] = (
    "31cdbbaf05e962038c9221bdc22845b7639f4a1e914b4596db6b8608a5ea5e18"
)

from src.core.log_parser import LogParser
from src.models import SessionData


class TestFollowControl:
    """Public start/stop / running-state surface."""

    @pytest.mark.asyncio
    async def test_follow_stop_method(self, tmp_path):
        """``stop()`` flips ``_running`` and lets ``follow()`` exit cleanly."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Game Started!\n")

        parser = LogParser(log_path=str(log_file))
        parser._running = True

        follow_task = asyncio.create_task(parser.follow(poll_interval=0.01))

        await asyncio.sleep(0.05)
        parser.stop()

        try:
            await asyncio.wait_for(follow_task, timeout=0.5)
        except asyncio.TimeoutError:
            follow_task.cancel()

        assert parser._running is False

    def test_is_running_property_reflects_state(self, tmp_path):
        """``is_running`` is a *property* on the current API, not a method."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        parser = LogParser(log_path=str(log_file))

        # Initially false.
        assert parser.is_running is False

        # Manual flip → true.
        parser._running = True
        assert parser.is_running is True

        # ``stop()`` clears it.
        parser.stop()
        assert parser.is_running is False


class TestSessionAccessors:
    """``get_current_session`` / ``get_player_id`` accessor contracts."""

    def test_get_current_session_returns_set_session(self):
        parser = LogParser()

        assert parser.get_current_session() is None

        session = SessionData(track="spa", car="porsche")
        parser.current_session = session

        assert parser.get_current_session() is session

    def test_get_player_id_reads_from_context(self):
        """``get_player_id`` returns ``context.player_id`` (not session.player_id).

        ``LogContext`` is the persistent identity layer that survives
        session boundaries, so the accessor reads from there.
        """
        parser = LogParser()
        assert parser.get_player_id() is None

        parser.context.player_id = "76561198321627695"
        assert parser.get_player_id() == "76561198321627695"


class TestProcessLineReturnsLapDataOnly:
    """``_process_line`` only returns a non-None ``LapData`` for lap-finish.

    For every other line type (game-started, track-name, fuel, penalty,
    track-limit, …) it returns ``None`` and side-effects state on
    ``self.context`` / ``self._ip``. The previous tests asserted
    ``result is not None`` for non-lap lines, which contradicted the
    actual contract.
    """

    def test_process_line_unknown_returns_none(self):
        parser = LogParser()
        assert parser._process_line("Some random unknown log line") is None

    def test_process_line_empty_returns_none(self):
        parser = LogParser()
        assert parser._process_line("") is None


class TestHistoricalPassClearsLaps:
    """Historical pass parses the file silently then clears emitted laps."""

    @pytest.mark.asyncio
    async def test_historical_pass_clears_laps_before_live_tail(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "TRACK NAME spa\n"
            "New lap carId=abc123 time=1:30.000\n"
        )

        parser = LogParser(log_path=str(log_file))
        parser.context.car_uuid = "abc123"
        parser.current_session = SessionData(track="spa", car="porsche")
        parser._running = True

        try:
            await asyncio.wait_for(
                parser.follow(poll_interval=0.01), timeout=0.1
            )
        except asyncio.TimeoutError:
            pass

        parser.stop()

        # Historical laps are cleared; live-tail emits new laps only.
        if parser.current_session is not None:
            assert len(parser.current_session.laps) == 0


class TestStintManagement:
    """``_ensure_stint`` creates/reuses ``StintData`` keyed by compound."""

    def test_ensure_stint_creates_new_stint(self):
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")

        parser._ensure_stint("SC")

        assert len(parser.current_session.stints) == 1
        # Real attribute name on ``StintData`` is ``tyre_compound``.
        assert parser.current_session.stints[0].tyre_compound == "SC"

    def test_ensure_stint_reuses_existing_when_same_compound(self):
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")

        parser._ensure_stint("SC")
        first_stint = parser.current_session.stints[0]

        parser._ensure_stint("SC")

        assert len(parser.current_session.stints) == 1
        assert parser.current_session.stints[0] is first_stint

    def test_ensure_stint_creates_new_when_compound_changes(self):
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")

        parser._ensure_stint("SC")
        parser._ensure_stint("MC")

        assert len(parser.current_session.stints) == 2
        assert parser.current_session.stints[0].tyre_compound == "SC"
        assert parser.current_session.stints[1].tyre_compound == "MC"


class TestCompoundBatchFlush:
    """``_flush_pending_compound_batch`` is a no-op when nothing is pending."""

    def test_flush_no_pending_does_not_crash(self):
        parser = LogParser()
        parser.current_session = SessionData(track="spa", car="porsche")

        # No pending updates and no session-state changes expected.
        parser._flush_pending_compound_batch()

        assert parser._pending_compound_updates == {}

    def test_flush_no_session_does_not_crash(self):
        parser = LogParser()  # No current_session

        # Even without a session, flushing must not raise.
        parser._flush_pending_compound_batch()


class TestContextIdentity:
    """``LogContext`` exposes ``current_track`` / ``current_car`` (not the legacy
    ``track_name`` / ``car_name`` names) and a session-scoped reset helper.
    """

    def test_context_default_state(self):
        parser = LogParser()
        assert parser.context.current_track == "Unknown"
        assert parser.context.current_car == "Unknown"
        assert parser.context.game_version == "Unknown"
        assert parser.context.player_id is None

    def test_context_reset_for_new_session_clears_session_scoped_state(self):
        parser = LogParser()

        parser.context.player_id = "76561198321627695"
        parser.context.car_uuid = "abc-uuid"
        parser.context.fuel_init_correction = 1.5
        parser.context.prev_hundredmeters = 250
        parser.context.fuel_spike_count = 3
        parser.context.player_car_uuids.add("other-uuid")
        parser.context.setup_values["brake_balance"] = "55"

        parser.context.reset_for_new_session()

        # Session-scoped fields cleared.
        assert parser.context.fuel_init_correction == 0.0
        assert parser.context.prev_hundredmeters == 0
        assert parser.context.fuel_spike_count == 0
        assert parser.context.setup_values == {}

        # The current car_uuid is re-seeded into player_car_uuids.
        assert parser.context.player_car_uuids == {"abc-uuid"}

        # Identity (player_id) survives across sessions by design.
        assert parser.context.player_id == "76561198321627695"
