"""
ACE Log Parser — v3 (Refactored)
Based on deep multi-log analysis + engineering review of v2.

This module now imports data models from src.models for better maintainability.
"""

import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional, TextIO

# Import data models from the models module
from ..models import (
    LAP_TIME_RECONCILIATION_TOLERANCE_MS,
    PRACTICE_LIKE,
    RACE_LIKE,
    # Constants
    SECTOR_SUM_TOLERANCE_MS,
    SESSION_TYPE_MAP,
    InProgressLap,
    LapCompletionData,
    LapData,
    LapState,
    LogContext,
    SessionData,
    SharedSessionManager,
    StintData,
    is_hybrid_car,
)
from ..utils.structured_logger import Component, log_debug

# ─── Callback type aliases ────────────────────────────────────────────────────

LapCallback = Callable[[SessionData, LapData], Awaitable[None]]
LapUpdateCallback = Callable[[SessionData, LapData], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]
GameStatusCallback = Callable[[bool], Awaitable[None]]
UserDetectedCallback = Callable[[str, Optional[str]], Awaitable[None]]
GameVersionCallback = Callable[[str], Awaitable[None]]
SessionEndCallback = Callable[[], Awaitable[None]]
SessionRestartCallback = Callable[[], Awaitable[None]]


async def _iter_lines_cooperatively(
    file_handle: TextIO,
    *,
    yield_every: int = 256,
) -> AsyncIterator[str]:
    """Read lines in order without monopolizing the event-loop thread."""
    for line_number, line in enumerate(file_handle, start=1):
        if line_number % yield_every == 0:
            await asyncio.sleep(0)
        yield line


# ─── Main parser ──────────────────────────────────────────────────────────────


class LogParser:
    """Parse ACE game logs and extract structured lap/session data.

    Supports one-shot (`parse_file`) and live-tail (`follow`) modes.
    """

    DEFAULT_LOG_DIR = Path.home() / "Saved Games" / "ACE" / "Logs"
    DEFAULT_LOG_PATH = DEFAULT_LOG_DIR
    PENDING_VALIDITY_GRACE_SECONDS = 0.5
    # ACE emits PENALTY_ADDED twice for a single cut: at detection (track
    # cut widget shown) and again when the recovery deadline expires
    # (~3-11s later). Events within this window are treated as duplicates.
    PENALTY_ADDED_DEDUP_SECONDS = 15.0

    @staticmethod
    def _find_latest_log(log_dir: Path) -> Optional[Path]:
        """Return the most recently modified .txt file in log_dir, or None."""
        try:
            files = list(log_dir.glob("*.txt"))
        except OSError:
            return None
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

    def __init__(
        self,
        log_path: Optional[str] = None,
        on_lap_complete: Optional[LapCallback] = None,
        on_lap_update: Optional[LapUpdateCallback] = None,
        on_status_change: Optional[StatusCallback] = None,
        on_game_status_change: Optional[GameStatusCallback] = None,
        on_user_detected: Optional[UserDetectedCallback] = None,
        on_game_version: Optional[GameVersionCallback] = None,
        on_session_end: Optional[SessionEndCallback] = None,
        on_session_restart: Optional[SessionRestartCallback] = None,
        session_manager: Optional[SharedSessionManager] = None,
    ) -> None:
        _path = Path(log_path) if log_path else self.DEFAULT_LOG_DIR
        if _path.is_dir() or (not _path.suffix and not _path.is_file()):
            self._log_dir: Optional[Path] = _path
            _latest = self._find_latest_log(_path)
            self.log_path = _latest if _latest is not None else _path / "log.txt"
        else:
            self._log_dir = None
            self.log_path = _path
        self.on_lap_complete = on_lap_complete
        self.on_lap_update = on_lap_update
        self.on_status_change = on_status_change
        self.on_game_status_change = on_game_status_change
        self.on_user_detected = on_user_detected
        self.on_game_version = on_game_version
        self.on_session_end = on_session_end
        self.on_session_restart = on_session_restart
        self._session_manager = session_manager or SharedSessionManager()

        # Track last emitted game status to prevent duplicate events
        self._last_emitted_game_status: Optional[bool] = None
        self._session_active_from_logs: bool = False

        self.sessions: list[SessionData] = []
        self.current_session: Optional[SessionData] = None
        self.context = LogContext()

        # In-progress lap accumulator
        self._ip = InProgressLap()
        # Practice pit exits can arm the structural outlap flag before ACE
        # rejects a short pit-to-line prefix. Keep the following full-lap
        # splits aside so an exact, explicitly-valid SHM completion can
        # recover that timed lap without exposing ordinary structural
        # outlaps in the UI.
        self._outlap_candidate_splits: dict[int, int] = {}

        # Most recently completed lap, buffered until either:
        #   a) the game's authoritative `Relevant onSplit ... valid` line
        #      arrives (typically ~ms later) and we apply it, or
        #   b) a short live grace period expires, in which case shared-memory
        #      validity is used when available, or
        #   c) a session boundary forces an immediate flush.
        # This lets older builds provide their authoritative game flag without
        # making ACE 0.8.1 laps wait for the next lap or session exit.
        self._pending_lap: Optional[LapData] = None
        # Monotonic timestamp is set only for laps completed during live tail.
        # Historical laps stay buffered until an explicit session boundary so
        # starting the client never re-emits an old lap after the grace period.
        self._pending_lap_since: Optional[float] = None
        latest_completion = self._session_manager.get_latest_lap_completion()
        self._last_shm_completion_observed_at = latest_completion.observed_at if latest_completion else 0.0
        self._shm_emitted_laps: list[LapData] = []
        # Preserve source completion identity across delayed log lines. Lap
        # times are only a metadata hint because consecutive laps may match.
        self._lap_completion_by_lap_id: dict[int, LapCompletionData] = {}
        self._reconciled_lap: Optional[LapData] = None
        # Fallback penalty hint for the currently in-progress lap. Used only
        # when no authoritative validity line is seen for that lap.
        self._pending_penalty_warning: bool = False
        # Log-line epoch seconds of the last PENALTY_ADDED event, used to
        # dedup ACE's double-fired penalty notifications.
        self._last_penalty_added_ts: Optional[float] = None

        # Stint tracking
        self._current_stint: Optional[StintData] = None

        # In-memory log buffer (for export / diagnostics)
        self.log_buffer: list[str] = []
        self.max_log_lines: int = 100_000

        self._last_activity_ts: Optional[float] = None
        self._running: bool = False
        self._emit_callbacks: bool = False

        # Track last seen car ID for compound detection
        self._last_car_uuid: Optional[str] = None
        self._last_setup_car_uuid: Optional[str] = None
        # normalized car_uuid -> car model, captured from "Set new car" lines so
        # the authoritative "Creating Car" binding can recover the car model in
        # offline single-player sessions that lack a network `connect` line.
        self._pending_set_car_model: dict[str, str] = {}
        # True once the log uses explicit session markers (`Game Started!` or a
        # network `connect` line).  When set, the offline single-player fallback
        # session creator is disabled: a failed/aborted marker there is
        # intentional and stray laps must NOT resurrect a session.
        self._seen_explicit_session_marker: bool = False
        self._pending_compound_ts: Optional[str] = None
        self._pending_compound_source_car_uuid: Optional[str] = None
        self._pending_compound_updates: dict[int, str] = {}

        self._compile_patterns()

    # ── Private helpers ─────────────────────────────────────────────────

    def _get_shm_hybrid_flags(self) -> tuple[Optional[bool], Optional[bool]]:
        """Read ``has_ers`` / ``has_kers`` from the shared session, if set.

        Returns ``(has_ers, has_kers)`` — both ``None`` when SHM static
        data has not yet been captured.
        """
        return self._session_manager.get_hybrid_flags()

    @staticmethod
    def _nearest_lap_match(laps: list[LapData], lap_time_ms: int) -> Optional[LapData]:
        """Return the nearest lap within the cross-source timing tolerance."""
        candidates = [
            (index, lap)
            for index, lap in enumerate(laps)
            if abs(lap.lap_time_ms - lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (abs(item[1].lap_time_ms - lap_time_ms), item[0]),
        )[1]

    # ── Pattern compilation ───────────────────────────────────────────────────

    def _compile_patterns(self) -> None:
        self._pats: dict[str, re.Pattern] = {
            "version": re.compile(r"Build release ([^,]+),"),
            "track_name_direct": re.compile(r"TRACK NAME (.+)"),
            "track_load": re.compile(r"Loading (?:scene|Scene) .+ content\\tracks\\([^\\]+)"),
            "driver_line": re.compile(r"\tDriver (.+) on car ([\w_]+)"),
            "connect": re.compile(r"(\d+) connected(?: \([^)]+\))? on car ([\w_]+), with new carId ([a-f0-9\-]+)"),
            "connecting_gamecar": re.compile(r"connecting gamecar ([a-f0-9\-]+) \((.+)\)"),
            # Offline single-player car selection. No network `connect` line is
            # emitted in these sessions, so this is how the player's car/model
            # is learned:
            #   onSetPlayerCurrentCarCommand: Set new car <uuid> content\cars\<model>\...
            "set_player_car": re.compile(
                r"onSetPlayerCurrentCarCommand: Set new car ([a-f0-9\-]+)"
                r" content\\cars\\([^\\]+)"
            ),
            # Authoritative player-car binding. Carries car uuid, driver name
            # and the player's Steam ID, e.g.:
            #   [ServerVehicleSystem][<uuid>] Creating Car (Glebulon  \t76561198…)
            "creating_car": re.compile(
                r"\[ServerVehicleSystem\]\[([a-f0-9\-]+)\] Creating Car "
                r"\((.*?)\s+(\d{17})\)"
            ),
            # Full pipe-delimited Game Started line
            "game_started": re.compile(
                r"\[gameplay\] \[info\] Game Started!\s*GameModeType_([A-Z_]+)"
                r" \| (.+?) \| ([\w_]+) \| GameModeSelectionWeatherType_(\w+)"
            ),
            "date": re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),
            "set_compound_old": re.compile(r"setCompound Tyre:\s*(\d+)\s+compound(?: name)?:\s*(\w+)"),
            "loading_tyre_compound": re.compile(r"LOADING TYRE COMPOUND (.+)"),
            "tyre_compound_summary": re.compile(r"TYRE COMPOUND: (.+)"),
            "fuel_filled": re.compile(r"FUEL car ([a-f0-9\-]+) (?:filled|setup) with ([\d.]+) L"),
            # Energy source: fires exactly once per completed lap
            "fuel_consumed": re.compile(
                r"\[gameplay\] \[info\] Energy source car ([a-f0-9\-]+)"
                r" for driver [a-f0-9\-]+ "
                r"hundredmeters done: (\d+) fuel consumed: ([\-\d.]+) L"
            ),
            # Track limit: car | new_tyre_out_count | inside_distance_m
            "track_limits": re.compile(
                r"\[physics\] \[info\] Limits: car ([a-f0-9\-]+)"
                r" tyres out changed: \d+ -> (\d+) with ([\-\d.]+)m inside"
            ),
            # Race-mode: car-specific sector event
            "race_split": re.compile(
                r"\[gameplay\] \[info\] Split completed for car ([a-f0-9\-]+)"
                r": \((\d+) ms, splitindex (\d+)\)"
            ),
            # Practice-mode: player-only sector event.
            # AC Evo logs boolean start/end flags ("start true end false"); the
            # \S+ tokens tolerate both that and legacy numeric values.
            "practice_split": re.compile(
                r"\[gameplay\] \[info\] On Split start \S+ end \S+"
                r" id (\d+) splittime (\d+)"
            ),
            "split_end": re.compile(r"\[gameplay\] \[info\] On Split end with all splits"),
            "physics_lap": re.compile(r"\[physics\] \[info\] Lap test evOnLapCompleted (\d+) completed"),
            # Car removed: fires when the player's car is removed at session end
            "remove_car": re.compile(r"onSetPlayerCurrentCarCommand: remove car ([a-f0-9\-]+)"),
            # New lap: timestamp | car_uuid | lap_time_str
            "lap_finish": re.compile(
                r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]"
                r" \[gameplay\] \[info\] New lap carId ([a-f0-9\-]+): ([\d:.]+)"
            ),
            # Game's authoritative per-lap validity flag.  Emitted on the
            # [network] channel ~ms after `New lap carId`, e.g.:
            #   Relevant onSplit for Combo 6@2: laptime 146939, valid true,
            #   flags 2, lap 1 (prev 0)
            # Captures: laptime_ms, valid_str ("true"|"false"), flags, lap_num.
            "lap_validity": re.compile(
                r"\[network\] \[info\] Relevant onSplit for Combo "
                r"\d+@\d+: laptime (\d+), valid (true|false), "
                r"flags (\d+), lap (\d+)"
            ),
            # Flexible field extraction for authoritative validity lines when
            # the channel/prefix/order changes between game versions.
            "lap_validity_laptime": re.compile(r"\blaptime\s+(\d+)\b", re.IGNORECASE),
            "lap_validity_valid": re.compile(r"\bvalid\s+(true|false)\b", re.IGNORECASE),
            "lap_validity_flags": re.compile(r"\bflags\s+(\d+)\b", re.IGNORECASE),
            "lap_validity_lap_number": re.compile(r"\blap\s+(\d+)\b", re.IGNORECASE),
            # AC Evo emits the same `UINotificationType_SessionPenalty`
            # notification line for BOTH penalty additions and clearances.
            # Each event is followed by a discriminating warning line:
            #   added:   `[warning] true {PENALTY_ADDED_KEY} #0`
            #   cleared: `[warning] false {PENALTY_CLEARED_KEY} #0`
            # We must only match the addition discriminator — matching the
            # generic notification line caused every penalty *clear* to flip
            # `has_penalty=True`, invalidating clean racing laps.
            "penalty": re.compile(r"\{PENALTY_ADDED_KEY\}"),
            "penalty_warning_type": re.compile(r"Penalty Type PenaltyType_Warning has no tranformation"),
            "setup_group": re.compile(r"KS-SETUP-GROUP\s+(.+)$"),
        }

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _parse_lap_time_ms(self, time_str: str) -> int:
        parts = time_str.replace(":", ".").split(".")
        if len(parts) == 3:
            return (int(parts[0]) * 60 + int(parts[1])) * 1000 + int(parts[2].ljust(3, "0")[:3])
        if len(parts) == 2:
            return int(parts[0]) * 1000 + int(parts[1].ljust(3, "0")[:3])
        return 0

    def _extract_line_timestamp(self, line: str) -> Optional[str]:
        if not line.startswith("["):
            return None
        end = line.find("]")
        if end <= 1:
            return None
        return line[1:end]

    def _line_ts_epoch_seconds(self, line: str) -> Optional[float]:
        ts = self._extract_line_timestamp(line)
        if not ts:
            return None
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        except ValueError:
            return None

    @staticmethod
    def _normalize_car_uuid(car_uuid: Optional[str]) -> str:
        return (car_uuid or "").replace("-", "").lower()

    def _is_player_car(self, car_uuid: str) -> bool:
        normalized = self._normalize_car_uuid(car_uuid)
        return normalized == self._normalize_car_uuid(self.context.car_uuid) or normalized in {
            self._normalize_car_uuid(uuid) for uuid in self.context.player_car_uuids
        }

    def _line_mentions_player_car(self, line: str) -> bool:
        if not self.context.car_uuid and not self.context.player_car_uuids:
            return False
        normalized_line = self._normalize_car_uuid(line)
        if self.context.car_uuid and self._normalize_car_uuid(self.context.car_uuid) in normalized_line:
            return True
        return any(self._normalize_car_uuid(uuid) in normalized_line for uuid in self.context.player_car_uuids)

    def _update_session_activity_from_line(self, line: str) -> None:
        """Track whether the latest parsed log state still looks drivable."""
        if "Game Started!" in line or "has started the race!" in line:
            self._session_active_from_logs = True
            return

        if "request made GameModeRequestExit" in line:
            self._session_active_from_logs = False
            return

        if "END_SESSION" in line and self._line_mentions_player_car(line):
            self._session_active_from_logs = False
            return

        if "onSetPlayerCurrentCarCommand: remove car" in line:
            m = self._pats["remove_car"].search(line)
            if m and self._is_player_car(m.group(1)):
                self._session_active_from_logs = False

    def _is_steam_id(self, pid: str) -> bool:
        return len(pid) == 17 and pid.startswith("7656")

    def _clean_track_name(self, raw: str) -> str:
        """Strip session-type words and date suffixes from track description."""
        if "@" in raw:
            raw = raw[: raw.index("@")]
        # Newer ACE builds append the session length before the @date,
        # e.g. "Laguna Seca GP Race Race  5 laps @2014/8/15 15:0:0".
        raw = re.sub(
            r"\s+\d+\s+(?:laps?|seconds?|minutes?|hours?)\s*$",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        for suffix in (
            " Race Race",
            " Race",
            " Time Attack Practice",
            " Time Attack",
            " Practice",
            " Qualifying",
            " Hotlap",
            " Drift",
        ):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        return raw.strip()

    def _reset_in_progress(self) -> None:
        self._ip = InProgressLap()
        self._outlap_candidate_splits = {}
        self._pending_penalty_warning = False

    def _reset_session_boundary(
        self,
        reason: str,
        *,
        finalize_current_session: bool,
    ) -> None:
        """Move the parser across a session/log boundary.

        A boundary has two deliberately different contracts.  A normal game
        start or an in-place restart finalises the old session so its final
        completed lap remains available.  A rotated/truncated log is only a
        new input stream: any pending lap belongs to the old stream and must
        be discarded rather than reconciled with the new stream.

        Keep all parser-side and shared-session state reset here.  In
        particular, clearing only ``SharedSessionManager`` is insufficient:
        a pending log lap, an already-emitted SHM lap, or the penalty dedup
        timestamp can otherwise be matched by the first lap of the new
        session.
        """
        old_session = self.current_session
        old_pending = self._pending_lap
        old_shm_laps = len(self._shm_emitted_laps)

        if finalize_current_session:
            self._finalise_current_session()
        else:
            self.current_session = None
            self._session_active_from_logs = False

        # ``_finalise_current_session`` clears the in-progress accumulator,
        # but the explicit reset below is intentional: this helper is also
        # used for boundaries where the old session is discarded.
        self._reset_in_progress()
        self._pending_lap = None
        self._pending_lap_since = None
        self._reconciled_lap = None
        self._shm_emitted_laps.clear()
        self._lap_completion_by_lap_id.clear()
        self._last_penalty_added_ts = None
        self._current_stint = None
        self._last_car_uuid = None
        self._last_setup_car_uuid = None
        self._pending_compound_ts = None
        self._pending_compound_source_car_uuid = None
        self._pending_compound_updates.clear()

        # Reset after finalisation so the old session can be synced first, but
        # before the caller creates/syncs the new session.  Seed the cursor
        # from the fresh manager so old SHM completions cannot be consumed.
        self._session_manager.reset()
        latest_completion = self._session_manager.get_latest_lap_completion()
        self._last_shm_completion_observed_at = latest_completion.observed_at if latest_completion else 0.0

        log_debug(
            Component.LOG_PARSER,
            f"[BOUNDARY] {reason}: finalize={finalize_current_session} "
            f"old_session={old_session is not None} "
            f"old_pending={old_pending is not None} old_shm={old_shm_laps}",
        )

    def _parse_authoritative_lap_validity(
        self,
        line: str,
    ) -> Optional[tuple[int, str, int, int]]:
        """Parse an authoritative per-lap validity line.

        Primary path: strict legacy ``Relevant onSplit for Combo ...`` regex.
        Fallback path: field-based extraction for format/channel variations.
        """
        strict = self._pats["lap_validity"].search(line)
        if strict:
            return (
                int(strict.group(1)),
                strict.group(2).lower(),
                int(strict.group(3)),
                int(strict.group(4)),
            )

        if "onsplit" not in line.lower():
            return None

        laptime_match = self._pats["lap_validity_laptime"].search(line)
        valid_match = self._pats["lap_validity_valid"].search(line)
        flags_match = self._pats["lap_validity_flags"].search(line)
        lap_match = self._pats["lap_validity_lap_number"].search(line)

        if not (laptime_match and valid_match and flags_match and lap_match):
            return None

        return (
            int(laptime_match.group(1)),
            valid_match.group(1).lower(),
            int(flags_match.group(1)),
            int(lap_match.group(1)),
        )

    # ── Log buffer ────────────────────────────────────────────────────────────

    def _add_to_log_buffer(self, line: str) -> None:
        self.log_buffer.append(line)
        if len(self.log_buffer) > self.max_log_lines:
            excess = len(self.log_buffer) - self.max_log_lines
            self.log_buffer = self.log_buffer[excess:]

    def get_log_buffer(self) -> list[str]:
        return self.log_buffer.copy()

    def clear_log_buffer(self) -> None:
        self.log_buffer.clear()

    def export_logs_to_file(self, file_path: str) -> bool:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.log_buffer))
            return True
        except (OSError, IOError) as exc:
            log_debug(Component.LOG_PARSER, f"[ERROR] export_logs_to_file: {exc}")
            return False

    # ── Async emitters ────────────────────────────────────────────────────────

    async def _emit_status(self, status: str) -> None:
        log_debug(Component.LOG_PARSER, f"[STATUS] {status}")
        if self.on_status_change:
            try:
                await self.on_status_change(status)
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_status_change: {exc}")

    async def _emit_lap(self, session: SessionData, lap: LapData) -> None:
        self._session_manager.update_lap_from_logs(lap, session_data=session)
        log_debug(
            Component.LOG_PARSER,
            f"[EMIT_LAP] #{lap.lap_number} {lap.lap_time_str} "
            f"state={lap.lap_state.value}  car={session.car}  track={session.track}  "
            f"session_id={session.session_id[:8]}...",
        )
        if self.on_lap_complete:
            try:
                await self.on_lap_complete(session, lap)
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_lap_complete: {exc}")

    async def _emit_lap_update(self, session: SessionData, lap: LapData) -> None:
        """Publish richer log fields for a lap already emitted from SHM."""
        self._session_manager.update_lap_from_logs(lap, session_data=session)
        if self.on_lap_update:
            try:
                await self.on_lap_update(session, lap)
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_lap_update: {exc}")

    def _sync_shared_session(self, session: Optional[SessionData]) -> None:
        if session is None:
            return
        self._session_manager.update_session_metadata_from_logs(session)

    async def _emit_game_status(self, is_running: bool, trigger: str = "unknown") -> None:
        """Emit game status change, logging if duplicate or state change."""
        if self._last_emitted_game_status == is_running:
            log_debug(
                Component.LOG_PARSER,
                f"[GAME_STATUS] DUPLICATE DROPPED: is_running={is_running}, "
                f"trigger={trigger}, last={self._last_emitted_game_status}  "
                f"⚠️ reset() will NOT be called for this event",
            )
            return

        log_debug(
            Component.LOG_PARSER,
            f"[GAME_STATUS] STATE CHANGE: is_running={is_running}, "
            f"trigger={trigger}, last={self._last_emitted_game_status}",
        )
        self._last_emitted_game_status = is_running

        if self.on_game_status_change:
            try:
                await self.on_game_status_change(is_running)
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_game_status_change: {exc}")

    async def _emit_session_end(self) -> None:
        log_debug(Component.LOG_PARSER, "[SESSION_END] car removed from session")
        if self.on_session_end:
            try:
                await self.on_session_end()
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_session_end: {exc}")

    async def _emit_session_restart(self) -> None:
        """Player clicked Restart Session in the pause menu.

        AC Evo restarts the same session in place — no new ``Game Started!``
        line follows — so downstream consumers (e.g. telemetry capture) must
        be told explicitly to clear any in-flight buffer and start fresh.
        """
        log_debug(Component.LOG_PARSER, "[SESSION_RESTART] user requested restart")
        prior_session_type = None
        if self.current_session:
            prior_session_type = self.current_session.session_type
        # Reset the status dedup guard so the restart can be observed by
        # downstream consumers (restart does not emit a fresh Game Started).
        self._last_emitted_game_status = None

        # AC Evo only logs the tyre compound (setCompound / LOADING TYRE
        # COMPOUND) at the original session start, NOT after an in-place
        # restart. A pause-menu restart reuses the same car and tyres, so
        # snapshot the compound before _start_new_session wipes it and restore
        # it afterwards; otherwise the restarted session's laps show "Unknown".
        self._flush_pending_compound_batch()
        preserved_tyre = self.context.tyre.snapshot()

        # AC Evo may restart in-place without a fresh session-start marker.
        # Create a new parser session immediately so subsequent player laps are
        # not dropped while waiting for optional race-start chatter.
        self._start_new_session(prior_session_type or "UNKNOWN", "")

        if preserved_tyre.compound_name != "Unknown":
            self.context.tyre = preserved_tyre
            if self.current_session:
                self.current_session.tyre_compound = preserved_tyre.compound_name
            log_debug(
                Component.LOG_PARSER,
                f"[SESSION_RESTART] Preserved tyre compound {preserved_tyre.compound_name} across restart",
            )
        if self.on_session_restart:
            try:
                await self.on_session_restart()
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_session_restart: {exc}")

    async def _emit_user_detected(self, steam_id: str, player_name: Optional[str]) -> None:
        log_debug(Component.LOG_PARSER, f"[USER] steam_id={steam_id} name={player_name}")
        if self.on_user_detected:
            try:
                await self.on_user_detected(steam_id, player_name)
            except (RuntimeError, asyncio.CancelledError) as exc:
                log_debug(Component.LOG_PARSER, f"[ERROR] on_user_detected: {exc}")

    # ── Stint management ──────────────────────────────────────────────────────

    def _ensure_stint(self, compound: str) -> StintData:
        """Return current stint, creating or rolling over as needed."""
        if not self.current_session:
            raise RuntimeError("_ensure_stint called without an active session")

        # First stint of the session
        if self._current_stint is None:
            self._current_stint = StintData(
                stint_number=1,
                tyre_compound=compound,
            )
            self.current_session.stints.append(self._current_stint)
            log_debug(Component.LOG_PARSER, f"[STINT] Stint 1 started on {compound}")
            return self._current_stint

        # Compound changed → new stint (tyre change at pit stop)
        if compound != self._current_stint.tyre_compound:
            log_debug(
                Component.LOG_PARSER,
                f"[STINT] Compound changed {self._current_stint.tyre_compound!r} "
                f"→ {compound!r}: starting stint "
                f"{self._current_stint.stint_number + 1}",
            )
            self._current_stint = StintData(
                stint_number=self._current_stint.stint_number + 1,
                tyre_compound=compound,
            )
            self.current_session.stints.append(self._current_stint)

        return self._current_stint

    def _finalise_stints(self) -> None:
        self._current_stint = None

    # ── Individual line handlers ──────────────────────────────────────────────

    def _handle_version(self, line: str) -> None:
        if "Build release" not in line:
            return
        m = self._pats["version"].search(line)
        if m:
            self.context.game_version = m.group(1)
            if self.on_game_version and self._emit_callbacks:
                asyncio.create_task(self.on_game_version(self.context.game_version))

    def _handle_track_name(self, line: str) -> None:
        if "TRACK NAME" in line:
            m = self._pats["track_name_direct"].search(line)
            if m:
                name = m.group(1).strip()
                self.context.current_track = name
                if self.current_session:
                    self.current_session.track = name
        elif ("Loading scene" in line or "Loading Scene" in line) and "content\\tracks" in line:
            m = self._pats["track_load"].search(line)
            if m and self.current_session and self.current_session.track == "Unknown":
                self.context.current_track = m.group(1)
                self.current_session.track = m.group(1)

    def _handle_connect(self, line: str) -> None:
        if "connected" not in line or "on car" not in line or "with new carId" not in line:
            return
        m = self._pats["connect"].search(line)
        if not m:
            return

        pid, car, car_uuid = m.group(1), m.group(2), m.group(3)
        # Explicit online session marker — disables the offline fallback.
        self._seen_explicit_session_marker = True
        already_has_steam = self._is_steam_id(self.context.player_id or "")

        if self._is_steam_id(pid) or not already_has_steam:
            self.context.player_id = pid
            self.context.current_car = car
            self.context.car_uuid = car_uuid
            self.context.player_car_uuids.add(car_uuid)
            # Keep the shared capability sample bound to the identified car.
            # This also clears a prior car's hybrid flags immediately when a
            # car switch is observed before the next static SHM snapshot.
            self._session_manager.update_player_identification_from_logs(
                {"steam_id": pid, "car_uuid": car_uuid, "car_model": car}
            )
            ers, kers = self._get_shm_hybrid_flags()
            self.context.car_is_hybrid = is_hybrid_car(has_ers_from_shm=ers, has_kers_from_shm=kers)

            if car_uuid in self.context.car_meta:
                meta = self.context.car_meta[car_uuid]
                if meta.get("player_name"):
                    self.context.player_name = meta["player_name"]

            if self.current_session:
                self.current_session.car_uuid = car_uuid
                self.current_session.car = car
                self.current_session.player_id = pid
                self.current_session.fuel_reliable = not self.context.car_is_hybrid
            else:
                self._start_new_session("UNKNOWN", line)

            log_debug(
                Component.LOG_PARSER,
                f"[CONNECT] pid={pid} car={car} uuid={car_uuid} hybrid={self.context.car_is_hybrid}",
            )

    def _handle_driver(self, line: str) -> None:
        if "\tDriver " not in line or " on car " not in line:
            return
        m = self._pats["driver_line"].search(line)
        if not m:
            return
        if not self._is_steam_id(self.context.player_id or ""):
            self.context.player_name = m.group(1).strip()
            self.context.current_car = m.group(2).strip()
            if self.current_session:
                self.current_session.player_name = self.context.player_name
                self.current_session.car = self.context.current_car

    def _handle_gamecar_meta(self, line: str) -> None:
        if "connecting gamecar" not in line:
            return
        m = self._pats["connecting_gamecar"].search(line)
        if not m:
            return
        car_uuid, raw = m.group(1), m.group(2)
        cleaned = raw.replace("â€¢", "").replace("•", "").strip()
        if "|" in cleaned:
            left, right = cleaned.split("|", 1)
            player_name, player_id = left.strip(), right.strip()
        else:
            player_name, player_id = cleaned, None
        self.context.car_meta[car_uuid] = {
            "player_name": player_name,
            "player_id": player_id,
        }

    def _handle_player_car_binding(self, line: str) -> None:
        """Identify the player's car in offline single-player sessions.

        These sessions emit no network ``connect`` line, so ``_handle_connect``
        never binds the player car. Instead the player car is learned from:

          onSetPlayerCurrentCarCommand: Set new car <uuid> content\\cars\\<model>
          [ServerVehicleSystem][<uuid>] Creating Car (<name>  \t<steamid>)

        The ``Set new car`` line carries the car model; the ``Creating Car``
        line is authoritative (it carries the player's Steam ID). We bind on
        the latter and recover the model from the former.
        """
        if "onSetPlayerCurrentCarCommand: Set new car " in line:
            m = self._pats["set_player_car"].search(line)
            if m:
                self._pending_set_car_model[self._normalize_car_uuid(m.group(1))] = m.group(2)
            return

        if "Creating Car (" not in line or "ServerVehicleSystem" not in line:
            return
        m = self._pats["creating_car"].search(line)
        if not m:
            return
        car_uuid, player_name, pid = m.group(1), m.group(2).strip(), m.group(3)
        if not self._is_steam_id(pid):
            return
        # Only bind the player's own car. If a Steam identity is already known,
        # ignore Creating Car lines for other Steam IDs (e.g. AI/opponents).
        if self._is_steam_id(self.context.player_id or "") and pid != self.context.player_id:
            return

        self.context.player_id = pid
        if player_name:
            self.context.player_name = player_name
        self.context.car_uuid = car_uuid
        self.context.player_car_uuids.add(car_uuid)
        model = self._pending_set_car_model.get(self._normalize_car_uuid(car_uuid))
        if model:
            self.context.current_car = model
        self._session_manager.update_player_identification_from_logs(
            {
                "steam_id": pid,
                "car_uuid": car_uuid,
                "car_model": model or self.context.current_car,
            }
        )
        ers, kers = self._get_shm_hybrid_flags()
        self.context.car_is_hybrid = is_hybrid_car(has_ers_from_shm=ers, has_kers_from_shm=kers)

        if self.current_session:
            self.current_session.car_uuid = car_uuid
            if model:
                self.current_session.car = model
            self.current_session.player_id = pid
            self.current_session.player_name = self.context.player_name
            self.current_session.fuel_reliable = not self.context.car_is_hybrid

        log_debug(
            Component.LOG_PARSER,
            f"[CAR_BIND] player car via Creating Car: uuid={car_uuid} model={model} steam={pid}",
        )

    def _maybe_start_fallback_session(self, line: str) -> None:
        """Create a session when driving begins without a session-start marker.

        Offline single-player sessions (e.g. Special Event / hotlap / practice)
        may emit neither ``Game Started!`` nor a network ``connect`` line, so no
        session is ever created and player laps are dropped. Once the player car
        is bound, the first in-session driving signal (a player-only practice
        split, a physics lap, or a player ``New lap``) creates a fallback
        PRACTICE session so subsequent sectors and laps are captured.
        """
        if self._seen_explicit_session_marker or not self.context.car_uuid:
            return
        is_practice_split = "On Split start" in line and self._pats["practice_split"].search(line)
        is_physics_lap = "Lap test evOnLapCompleted" in line
        is_player_lap = (
            "New lap carId" in line
            and (m := self._pats["lap_finish"].search(line)) is not None
            and self._is_player_car(m.group(2))
        )
        if is_practice_split or is_physics_lap or is_player_lap:
            self._start_new_session("PRACTICE", line)
            log_debug(
                Component.LOG_PARSER,
                "[SESSION] Fallback PRACTICE session created for offline "
                "single-player driving (no Game Started / connect line)",
            )

    def _handle_car_teleport(self, line: str) -> None:
        """Handle CarTeleportCompleted lines to track last seen car ID."""
        if "CarTeleportCompleted" not in line:
            return
        # Extract car ID from the line
        parts = line.split()
        for i, part in enumerate(parts):
            if part == "CarTeleportCompleted" and i + 1 < len(parts):
                car_uuid = parts[i + 1].strip()
                if self._is_player_car(car_uuid):
                    self._last_car_uuid = car_uuid
                    log_debug(Component.LOG_PARSER, f"[CAR_TELEPORT] Player car detected: {car_uuid}")
                break

    def _handle_compound(self, line: str) -> None:
        # Check for LOADING TYRE COMPOUND format (appears after CarTeleportCompleted)
        if "LOADING TYRE COMPOUND" in line:
            self._flush_pending_compound_batch()
            m = self._pats["loading_tyre_compound"].search(line)
            # In practice the player car may spawn without a CarTeleportCompleted
            # event, so fall back to the known player car UUID from connect lines.
            car_uuid = self._last_car_uuid or self.context.car_uuid
            if m and car_uuid and self._is_player_car(car_uuid):
                compound_name = m.group(1).strip()
                if self.context.tyre.compound_name == "Unknown":
                    self.context.tyre.set_all(compound_name)
                    log_debug(
                        Component.LOG_PARSER,
                        f"[COMPOUND] All tires -> {compound_name} (resolved: {self.context.tyre.compound_name})",
                    )
                else:
                    log_debug(
                        Component.LOG_PARSER,
                        f"[COMPOUND] Ignoring LOADING fallback -> {compound_name} "
                        f"because resolved compound is already {self.context.tyre.compound_name}",
                    )
            return

        # TYRE COMPOUND summary lines are ignored - they include all cars in
        # session. Physics setCompound lines are used directly (no platformCore
        # confirmation step).
        if "setCompound Tyre:" not in line:
            return

        line_ts = self._extract_line_timestamp(line)

        m = self._pats["set_compound_old"].search(line)
        if not m:
            return

        pos = int(m.group(1))
        code = m.group(2)
        compound_name = code.strip()

        if pos not in (0, 1, 2, 3):
            return

        if self._pending_compound_ts and line_ts != self._pending_compound_ts:
            self._flush_pending_compound_batch()
        elif (
            line_ts
            and self._pending_compound_ts == line_ts
            and pos in self._pending_compound_updates
            and set(self._pending_compound_updates) == {0, 1, 2, 3}
        ):
            self._flush_pending_compound_batch()

        if not line_ts:
            self.context.tyre.set(pos, compound_name)
            log_debug(
                Component.LOG_PARSER, f"[COMPOUND] Tyre {pos} -> {code} (resolved: {self.context.tyre.compound_name})"
            )
            return

        self._pending_compound_ts = line_ts
        if not self._pending_compound_updates:
            self._pending_compound_source_car_uuid = self._last_setup_car_uuid
        self._pending_compound_updates[pos] = compound_name
        log_debug(
            Component.LOG_PARSER,
            f"[COMPOUND] Pending tyre {pos} -> {code} at {line_ts} "
            f"(positions={sorted(self._pending_compound_updates)})",
        )

    def _flush_pending_compound_batch(self) -> None:
        if not self._pending_compound_updates:
            self._pending_compound_ts = None
            self._pending_compound_source_car_uuid = None
            return

        pending = dict(self._pending_compound_updates)
        source_car_uuid = self._pending_compound_source_car_uuid
        player_scoped = source_car_uuid is not None and self._is_player_car(source_car_uuid)
        legacy_unscoped = source_car_uuid is None
        prelap_window = self.current_session is None or not self.current_session.laps

        if player_scoped or (legacy_unscoped and prelap_window):
            for pos, compound in pending.items():
                self.context.tyre.set(pos, compound)
            log_debug(
                Component.LOG_PARSER,
                f"[COMPOUND] Applied batch at {self._pending_compound_ts} "
                f"(positions={sorted(pending)}) -> {self.context.tyre.compound_name}",
            )
        else:
            log_debug(
                Component.LOG_PARSER,
                f"[COMPOUND] Ignored unscoped batch at {self._pending_compound_ts} (positions={sorted(pending)})",
            )

        self._pending_compound_ts = None
        self._pending_compound_source_car_uuid = None
        self._pending_compound_updates.clear()

    def _handle_weather(self, line: str) -> None:
        if "GameModeSelectionWeatherType_" not in line:
            return
        idx = line.find("GameModeSelectionWeatherType_")
        if idx != -1:
            suffix = line[idx + len("GameModeSelectionWeatherType_") :].split()[0]
            self.context.weather = suffix
            if self.current_session:
                self.current_session.weather = suffix

    def _serialize_setup_notes(self) -> Optional[str]:
        if not self.context.setup_values:
            return None
        rows: list[str] = []
        for key, value in self.context.setup_values.items():
            rows.append(f"{key} {value}".strip())
        return "\n".join(rows)

    def _handle_setup_group(self, line: str) -> None:
        if "KS-SETUP-GROUP" not in line:
            return
        m = self._pats["setup_group"].search(line)
        if not m:
            return

        raw_setting = m.group(1).strip()
        if not raw_setting:
            return

        parts = raw_setting.split(maxsplit=1)
        key = parts[0]
        value = parts[1].strip() if len(parts) > 1 else ""

        # Keep only the latest value for each setup key.
        self.context.setup_values[key] = value

        if self.current_session:
            self.current_session.setup_notes = self._serialize_setup_notes()

        log_debug(Component.LOG_PARSER, f"[SETUP] {key}={value!r}")

    def _handle_session_start(self, line: str) -> bool:
        """Parse 'Game Started!' and initialise a fresh SessionData.
        Returns True if a new session was created (caller should short-circuit).
        """
        if "Game Started!" not in line:
            return False
        # This log uses explicit session markers, so disable the offline
        # single-player fallback session creator even if this particular marker
        # fails to parse (a stray lap after a failed marker must be dropped).
        self._seen_explicit_session_marker = True
        # The line contains "Game Started!" but may have an unrecognised format
        # (e.g. the game version changed the field names).  Still reset the
        # shared session so stale data doesn't leak in.
        if "GameModeType_" not in line:
            log_debug(
                Component.LOG_PARSER,
                "[SESSION_START] 'Game Started!' line missing 'GameModeType_' — "
                "unrecognised format, session NOT created.  "
                "Resetting shared session anyway.",
            )
            self._reset_session_boundary("unrecognised Game Started", finalize_current_session=True)
            self.context.reset_for_new_session()
            return False
        m = self._pats["game_started"].search(line)
        if not m:
            log_debug(
                Component.LOG_PARSER,
                "[SESSION_START] 'Game Started!' line did NOT match regex — "
                "session NOT created, old session persists!  "
                "Resetting shared session anyway to clear stale data.",
            )
            # Even though we can't parse the new session, we know a new game
            # session is starting.  Clear the shared session to prevent stale
            # lap timing/validity data from the previous session leaking in.
            self._reset_session_boundary("unrecognised Game Started", finalize_current_session=True)
            self.context.reset_for_new_session()
            return False

        self._reset_session_boundary("Game Started", finalize_current_session=True)
        self._session_active_from_logs = True

        raw_type, raw_track_desc, raw_car, raw_weather = (
            m.group(1),
            m.group(2),
            m.group(3).strip(),
            m.group(4).strip(),
        )
        session_type = SESSION_TYPE_MAP.get(raw_type, raw_type)
        track = self._clean_track_name(raw_track_desc)

        self.context.current_track = track
        self.context.current_car = raw_car
        self.context.weather = raw_weather
        ers, kers = self._get_shm_hybrid_flags()
        self.context.car_is_hybrid = is_hybrid_car(has_ers_from_shm=ers, has_kers_from_shm=kers)

        # Preserve setup values across session reset
        preserved_setup_values = self.context.setup_values.copy()
        self.context.reset_for_new_session()
        self.context.setup_values = preserved_setup_values
        self._last_setup_car_uuid = None
        self._pending_compound_source_car_uuid = None

        tm = self._pats["date"].match(line)
        start_time = tm.group(1) if tm else datetime.now().isoformat()

        self.current_session = SessionData(
            session_type=session_type,
            game_version=self.context.game_version,
            track=track,
            car=raw_car,
            player_name=self.context.player_name,
            player_id=self.context.player_id,
            car_uuid=self.context.car_uuid,
            weather=raw_weather,
            fuel_reliable=not self.context.car_is_hybrid,
            start_time=start_time,
        )

        # Apply any setup values that were captured before this session started
        if self.context.setup_values:
            self.current_session.setup_notes = self._serialize_setup_notes()
            log_debug(
                Component.LOG_PARSER, f"[SESSION] Applied {len(self.context.setup_values)} setup values to new session"
            )

        self._reset_in_progress()
        self._finalise_stints()

        # ── Reset shared session before syncing the new session ─────────
        # _finalise_current_session() (above) may have pushed the old session's
        # laps into the shared session via update_from_logs().  The dedup guard
        # in _emit_game_status() can also prevent the app-level reset() from
        # firing.  Explicitly clearing here guarantees the new session starts
        # with a clean shared state regardless of either code path.
        self._sync_shared_session(self.current_session)

        log_debug(
            Component.LOG_PARSER,
            f"[SESSION] New: type={session_type} track={track} car={raw_car} hybrid={self.context.car_is_hybrid}",
        )
        return True

    def _handle_fuel(self, line: str) -> None:
        # ── Fuel fill on pit exit / session start ─────────────────────────────
        if "FUEL car" in line and (("filled with" in line and "from setup" in line) or "setup with" in line):
            m = self._pats["fuel_filled"].search(line)
            if m:
                car_id = m.group(1)
                self._last_setup_car_uuid = car_id
                if self.current_session and self._is_player_car(car_id):
                    self.current_session.initial_fuel = float(m.group(2))
                    log_debug(Component.LOG_PARSER, f"[FUEL] Initial fill: {m.group(2)} L")
            return

        # ── Per-lap energy-source event ────────────────────────────────────────
        if "[gameplay] [info] Energy source car" not in line:
            return
        m = self._pats["fuel_consumed"].search(line)
        if not m:
            return

        car_id = m.group(1)
        hundredmeters = int(m.group(2))
        fuel_delta = float(m.group(3))

        if not self.current_session or not self._is_player_car(car_id):
            return

        # Negative delta = tank fill / init event (race start).
        if fuel_delta < 0:
            self.context.fuel_init_correction = abs(fuel_delta)
            log_debug(Component.LOG_PARSER, f"[FUEL] Init correction stored: {self.context.fuel_init_correction} L")
            return

        if fuel_delta == 0.0:
            return

        # Distance covered this lap
        lap_hundredm = hundredmeters - self.context.prev_hundredmeters
        self.context.prev_hundredmeters = hundredmeters
        self._ip.distance_hundredm = lap_hundredm

        # Apply one-time tank-fill correction (first real lap of a race).
        net_fuel = fuel_delta
        if self.context.fuel_init_correction > 0.0:
            net_fuel = max(0.0, fuel_delta - self.context.fuel_init_correction)
            log_debug(
                Component.LOG_PARSER, f"[FUEL] Init correction applied: raw={fuel_delta:.3f} → net={net_fuel:.3f} L"
            )
            self.context.fuel_init_correction = 0.0

        self._ip.fuel_used = net_fuel
        log_debug(
            Component.LOG_PARSER,
            f"[FUEL] Lap fuel: {net_fuel:.3f} L  dist: {lap_hundredm}×100 m  reliable={self._ip.fuel_reliable}",
        )

    def _handle_penalty_signals(self, line: str) -> None:
        """Capture log-only penalty hints as fallback invalidation triggers.

        These hints are only decisive when authoritative validity is absent.
        A signal in the brief window after completion (in-progress not started)
        belongs to the buffered lap. Once the next lap has started accumulating,
        the hint is recorded for that in-progress lap instead.
        """
        saw_penalty_warning = bool(self._pats["penalty_warning_type"].search(line))
        saw_penalty_added = bool(self._pats["penalty"].search(line))
        if not (saw_penalty_warning or saw_penalty_added):
            return

        trigger = "PenaltyType_Warning" if saw_penalty_warning else "PENALTY_ADDED_KEY"
        if saw_penalty_added:
            line_ts = self._line_ts_epoch_seconds(line)
            if (
                line_ts is not None
                and self._last_penalty_added_ts is not None
                and 0.0 <= line_ts - self._last_penalty_added_ts <= self.PENALTY_ADDED_DEDUP_SECONDS
            ):
                # Duplicate of a penalty already attributed to the correct
                # lap by the first (detection-time) event. Without this, the
                # deadline-expiry copy lands after the next lap has started
                # and wrongly invalidates it.
                log_debug(
                    Component.LOG_PARSER,
                    "[VALIDITY] Ignoring duplicate PENALTY_ADDED within "
                    f"{self.PENALTY_ADDED_DEDUP_SECONDS:.0f}s of previous event",
                )
                return
            if line_ts is not None:
                self._last_penalty_added_ts = line_ts
        ip = self._ip
        in_progress_started = bool(
            ip.splits
            or ip.is_outlap
            or ip.fuel_used is not None
            or ip.distance_hundredm is not None
            or ip.physics_lap_num is not None
        )

        pending = self._pending_lap
        if pending is not None and pending.validity_source != "authoritative" and not in_progress_started:
            if pending.lap_state != LapState.OUTLAP:
                pending.lap_state = LapState.INVALID_PENALTY
                pending.lap_type = LapState.INVALID_PENALTY.value
                pending.is_valid = False
                log_debug(
                    Component.LOG_PARSER,
                    f"[VALIDITY] Fallback penalty trigger ({trigger}) demoted "
                    f"pending lap #{pending.lap_number} -> INVALID_PENALTY",
                )
            return

        self._pending_penalty_warning = True
        log_debug(
            Component.LOG_PARSER,
            f"[VALIDITY] Fallback penalty trigger ({trigger}) recorded for current in-progress lap",
        )

    def _handle_splits_race(self, line: str) -> None:
        if "Split completed for car" not in line:
            return
        if not self.current_session:
            return
        if self.current_session.session_type not in RACE_LIKE:
            return
        m = self._pats["race_split"].search(line)
        if not m:
            return

        car_id, time_ms, split_idx = m.group(1), int(m.group(2)), int(m.group(3))
        if not self._is_player_car(car_id):
            return

        self._ip.splits[split_idx] = time_ms
        log_debug(Component.LOG_PARSER, f"[SPLIT_RACE] S{split_idx + 1}: {time_ms} ms")

    def _handle_splits_practice(self, line: str) -> None:
        if "On Split start" not in line:
            return
        if not self.current_session:
            return
        if self.current_session.session_type in RACE_LIKE:
            return

        m = self._pats["practice_split"].search(line)
        if not m:
            return

        split_idx, split_ms = int(m.group(1)), int(m.group(2))

        # Tourist-style layouts can publish a zero-time start marker instead
        # of a ``New lap`` line when the outlap ends. That marker starts the
        # first timed lap. A normal non-zero S1 does not: on tracks whose pit
        # exit lies beyond the timing line (Laguna Seca, for example), it is
        # still S1 of the outlap and clearing here would expose the outlap as
        # an ordinary invalid lap.
        if self._ip.is_outlap and split_idx == 0 and split_ms == 0:
            log_debug(
                Component.LOG_PARSER,
                "[OUTLAP] Clearing outlap flag — zero-time start marker for new flying lap detected",
            )
            self._ip.is_outlap = False

        # Do not put structural-outlap splits in the ordinary accumulator.
        # Retain them separately, though: some tracks reject the pit prefix
        # and then time the following full circuit as a valid lap. An exact
        # SHM completion verdict can safely promote that candidate later.
        if self._ip.is_outlap:
            self._outlap_candidate_splits[split_idx] = split_ms
            return

        # Record the split (including the id 0 start-line marker at splittime 0).
        # Keeping id 0 preserves contiguous split keys ([0,1,...]) for the
        # validity guard; for single-split tracks (e.g. Nurburgring Tourist) the
        # finish split carries the full lap time so 0 + lap_time still matches.
        self._ip.splits[split_idx] = split_ms
        log_debug(Component.LOG_PARSER, f"[SPLIT_PRACTICE] S{split_idx + 1}: {split_ms} ms")

    def _handle_outlap_signals(self, line: str) -> None:
        """'Outplap split' is the authoritative outlap marker for practice-like
        modes. It is NOT reliable in race-like modes: AC Evo emits one
        "Outplap split" per car on the grid at race countdown (seen 6×
        back-to-back in a 6-car race log), with no car identifier, so accepting
        it in a race would falsely flag the player's first competitive lap as
        an outlap and silently drop it from submission. The first lap of a
        race/qualifying session is always a real timed lap, so we only honor
        this signal in PRACTICE_LIKE sessions.

        'Couldn't create lap from opensplits' means the game rejected the
        partial pit-exit segment at the timing line. In a practice-like
        session the following full circuit is still the outlap, so reset its
        accumulated fields while carrying the structural marker forward.
        """
        if "Outplap split" in line:
            if self.current_session and self.current_session.session_type in PRACTICE_LIKE:
                self._ip.is_outlap = True
                log_debug(Component.LOG_PARSER, "[OUTLAP] Outplap split detected")
            else:
                log_debug(
                    Component.LOG_PARSER,
                    "[OUTLAP] Outplap split ignored in race-like session "
                    "(grid-countdown broadcast, not a player outlap marker)",
                )
        elif "Couldn't create lap from opensplits" in line:
            log_debug(Component.LOG_PARSER, "[OUTLAP] Couldn't create lap — resetting in-progress")
            preserve_outlap = (
                self._ip.is_outlap
                and self.current_session is not None
                and self.current_session.session_type in PRACTICE_LIKE
            )
            self._reset_in_progress()
            self._ip.is_outlap = preserve_outlap

    def _handle_physics_lap(self, line: str) -> None:
        if "Lap test evOnLapCompleted" not in line:
            return
        m = self._pats["physics_lap"].search(line)
        if m:
            self._ip.physics_lap_num = int(m.group(1))

    # ── Lap state determination ───────────────────────────────────────────────

    def _determine_lap_state(
        self,
        ip: InProgressLap,
        session_type: str,
    ) -> LapState:
        """Classify the lap structurally: OUTLAP or VALID.

        This method only decides whether the lap is an outlap (pits / warm-up)
        or provisionally valid. Final validity prefers the game's
        ``Relevant onSplit`` log flag and falls back to live graphics SHM when
        that broadcast is absent.

        Outlap detection is log-only (no SHM dependency):

        1. **Log signal** — ``ip.is_outlap`` is set by the explicit
           ``Outplap split`` marker in the log (see
           ``_handle_outlap_signals``).
        2. **Log fallback** — when no ``Outplap split`` was logged, infer
           outlap from ``physics_lap_num == 1`` in a practice-like session
           with no recorded splits.  If splits ARE recorded, it's a flying
           lap.
        """
        is_practice_outlap = session_type in PRACTICE_LIKE and ip.physics_lap_num == 1 and not ip.splits
        if ip.is_outlap or is_practice_outlap:
            if is_practice_outlap and not ip.is_outlap:
                log_debug(
                    Component.LOG_PARSER, "[VALIDITY] OUTLAP via physics_lap_num==1 fallback (no Outplap split logged)"
                )
            return LapState.OUTLAP

        return LapState.VALID

    # ── Lap completion ────────────────────────────────────────────────────────

    def _handle_lap_complete(self, line: str) -> Optional[LapData]:
        if "New lap carId" not in line:
            return None
        m = self._pats["lap_finish"].search(line)
        if not m:
            return None

        self._flush_pending_compound_batch()

        timestamp, car_id, time_str = m.group(1), m.group(2), m.group(3)

        if not self.current_session or not self._is_player_car(car_id):
            return None

        lap_time_ms = self._parse_lap_time_ms(time_str)
        shm_existing = self._nearest_lap_match(self._shm_emitted_laps, lap_time_ms)
        ip = self._ip
        completion_splits = (
            self._outlap_candidate_splits if ip.is_outlap and self._outlap_candidate_splits else ip.splits
        )
        split_keys: list[int] = sorted(completion_splits.keys())
        split_times: list[int] = [completion_splits[key] for key in split_keys]

        # ── Sector extraction ─────────────────────────────────────────────────
        s1: Optional[int] = completion_splits.get(0)
        s2: Optional[int] = completion_splits.get(1)
        s3: Optional[int] = completion_splits.get(2)

        # S1 corruption check — race grid start produces an inflated time in
        # slot 0 (cumulative time before the player crosses the start/finish
        # line for the first time) rather than the actual sector duration.
        # The corrupted value may exceed lap_time outright, or it may be
        # smaller than lap_time but still cause S1+S2+S3 to overshoot
        # lap_time by more than tolerance (e.g. Spa grid start:
        # raw S1=110411, S2=64650, S3=38082, lap=146939 → real S1=44207).
        if s1 is not None and s2 is not None and s3 is not None:
            sector_sum = s1 + s2 + s3
            overshoot = sector_sum - lap_time_ms
            if overshoot > SECTOR_SUM_TOLERANCE_MS:
                s1_calc = lap_time_ms - s2 - s3
                if s1_calc > 0:
                    log_debug(
                        Component.LOG_PARSER,
                        f"[SECTORS] S1 corrupted (raw={s1} ms, "
                        f"sum={sector_sum} > lap={lap_time_ms} by {overshoot} ms)"
                        f" → back-calculated: {s1_calc} ms",
                    )
                    s1 = s1_calc
                    if split_keys and split_keys[0] == 0:
                        split_times[0] = s1
                else:
                    log_debug(
                        Component.LOG_PARSER,
                        f"[SECTORS] S1 overshoot detected (raw={s1}, "
                        f"sum={sector_sum} > lap={lap_time_ms}) but "
                        f"back-calc non-positive ({s1_calc}); leaving as-is",
                    )

        session_type = self.current_session.session_type

        # ── Lap state ─────────────────────────────────────────────────────────
        lap_state = self._determine_lap_state(ip, session_type)
        is_valid = lap_state == LapState.VALID
        if self._pending_penalty_warning and lap_state != LapState.OUTLAP:
            lap_state = LapState.INVALID_PENALTY
            is_valid = False
            log_debug(
                Component.LOG_PARSER,
                "[VALIDITY] Fallback penalty trigger applied at lap completion "
                f"-> #{max([lap.lap_number for lap in self.current_session.laps], default=0) + 1} "
                "INVALID_PENALTY",
            )

        # ── Sector consistency flag ────────────────────────────────────────────
        sectors_consistent: Optional[bool] = None
        if len(split_times) >= 2:
            sectors_consistent = abs(sum(split_times) - lap_time_ms) <= SECTOR_SUM_TOLERANCE_MS

        # ── Fuel ──────────────────────────────────────────────────────────────
        fuel_used = ip.fuel_used
        fuel_reliable = ip.fuel_reliable and self.current_session.fuel_reliable
        if shm_existing is None and fuel_used and fuel_used > 0:
            self.current_session.fuel_used_session += fuel_used

        # ── Compound & stint ──────────────────────────────────────────────────
        compound = self.context.tyre.compound_name
        self.current_session.tyre_compound = compound

        # Lap numbering comes from the count of "New lap carId" lines for the
        # player, NOT from physics_lap_num (which includes formation laps and
        # gives inflated numbers).  physics_lap_number is still stored on
        # LapData for SHM validity lookup (SHM uses completed_laps+1 which
        # aligns with the physics counter).
        physics_lap_number = ip.physics_lap_num
        prior_lap_numbers = [lap.lap_number for lap in self.current_session.laps]
        if self._pending_lap is not None:
            prior_lap_numbers.append(self._pending_lap.lap_number)
        lap_number = shm_existing.lap_number if shm_existing is not None else max(prior_lap_numbers, default=0) + 1

        # Update stint (only for laps that actually ran, including invalid valid)
        if shm_existing is not None:
            stint_number = shm_existing.stint_number
        elif lap_state != LapState.OUTLAP:
            stint = self._ensure_stint(compound)
            stint.add_lap(lap_number, fuel_used if fuel_reliable else None)
            stint_number = stint.stint_number
        else:
            stint_number = self._current_stint.stint_number if self._current_stint else 1

        # ── Build LapData ─────────────────────────────────────────────────────
        completed_lap = LapData(
            lap_number=lap_number,
            physics_lap_number=physics_lap_number,
            lap_time_ms=lap_time_ms,
            lap_time_str=time_str,
            sector1_ms=s1,
            sector2_ms=s2,
            sector3_ms=s3,
            sectors_consistent=sectors_consistent,
            lap_state=lap_state,
            lap_type=lap_state.value,
            is_valid=is_valid,
            fuel_used=fuel_used,
            fuel_reliable=fuel_reliable,
            tyre_compound=compound,
            stint_number=stint_number,
            timestamp=timestamp,
            distance_hundredm=ip.distance_hundredm,
        )

        log_debug(
            Component.LOG_PARSER,
            f"[LAP] #{lap_number} phys={physics_lap_number} "
            f"{time_str}  state={lap_state.value}  "
            f"compound={compound}  fuel={fuel_used}  "
            f"consistent={sectors_consistent}  (buffered)",
        )

        self._reset_in_progress()

        if shm_existing is not None:
            # ACE flushed the richer log record after the SHM completion was
            # already shown. Mutate the shared model in place so UI/history
            # references keep their identity, and suppress a duplicate lap.
            for field_name in (
                "physics_lap_number",
                "sector1_ms",
                "sector2_ms",
                "sector3_ms",
                "sectors_consistent",
                "fuel_used",
                "fuel_reliable",
                "tyre_compound",
                "timestamp",
                "distance_hundredm",
            ):
                setattr(shm_existing, field_name, getattr(completed_lap, field_name))
            if completed_lap.lap_state != LapState.VALID:
                shm_existing.lap_state = completed_lap.lap_state
                shm_existing.lap_type = completed_lap.lap_type
                shm_existing.is_valid = completed_lap.is_valid
                shm_existing.validity_source = "logs"
            self._shm_emitted_laps.remove(shm_existing)
            self._reconciled_lap = shm_existing
            log_debug(
                Component.LOG_PARSER,
                f"[LAP] reconciled delayed log for SHM-emitted lap "
                f"#{shm_existing.lap_number} {shm_existing.lap_time_str}",
            )
            return None

        # Buffer this lap until the game's authoritative validity arrives.
        # If a previous lap is still pending, that means its validity line
        # never showed up — flush it now with its heuristic state.
        prior_pending = self._pending_lap
        if prior_pending is not None:
            prior_completion = self._lap_completion_by_lap_id.get(id(prior_pending))
            if prior_completion is None:
                prior_completion = self._session_manager.get_lap_completion_by_time(prior_pending.lap_time_ms)
                if prior_completion is not None:
                    self._lap_completion_by_lap_id[id(prior_pending)] = prior_completion
            if prior_completion is not None:
                # Reserve the prior lap's source before selecting one for the
                # new lap, even when their rounded times are equal.
                self._session_manager.consume_lap_completion(prior_completion)
        self._pending_lap = completed_lap
        associated_completion = self._session_manager.get_lap_completion_by_time(lap_time_ms)
        if associated_completion is not None:
            self._lap_completion_by_lap_id[id(completed_lap)] = associated_completion
        self._pending_lap_since = time.monotonic() if self._emit_callbacks else None
        if prior_pending is not None:
            self._apply_shm_fallback_validity(prior_pending)
            self.current_session.laps.append(prior_pending)
            log_debug(
                Component.LOG_PARSER,
                f"[LAP] ⚠️ FLUSHING PRIOR PENDING LAP: "
                f"#{prior_pending.lap_number} {prior_pending.lap_time_str} "
                f"state={prior_pending.lap_state.value}  "
                f"via heuristic (no authoritative validity seen)  "
                f"session_car={self.current_session.car}",
            )
            return prior_pending
        return None

    # ── Authoritative validity (from network broadcast) ──────────────────────

    def _handle_lap_validity(self, line: str) -> Optional[LapData]:
        """Apply the game's authoritative per-lap validity flag.

        AC Evo emits a `[network] [info] Relevant onSplit for Combo ...:
        laptime N, valid true|false, flags N, ...` line ~ms after each
        `New lap carId`. In 0.7.0 the textual boolean can be stale at
        session end; the flags value is the useful validity signal:
        2 = valid, 1 = invalid.

        When this matches the currently-pending (just-completed) lap by
        ``laptime``:

        * Game says valid → lap_state VALID, is_valid True.
        * Game says invalid → lap_state INVALID_GAME, is_valid False.
        * OUTLAP is retained — it is a structural classification (the lap
          leaving the pits), not a validity verdict.

        If no authoritative line arrives, finalisation consults the live
        graphics SHM validity and otherwise retains the heuristic state.

        Returns the now-finalised lap so the caller can emit it; otherwise
        returns None.
        """
        parsed = self._parse_authoritative_lap_validity(line)
        if parsed is None:
            return None
        if not self.current_session:
            return None
        laptime_ms, valid_text, validity_flags, game_lap_number = parsed
        pending = self._pending_lap
        already_emitted = False
        # Keep all retained lap objects eligible for a repeated or delayed
        # broadcast. Filtering to SHM-originated laps lets a duplicate
        # lap-one message fall through to an equal-time pending lap two after
        # lap one has already been enriched from the logs.
        emitted_candidates = list(self.current_session.laps)
        if pending is not None:
            # A delayed/reordered broadcast can belong to an already SHM-
            # emitted lap rather than the newest pending log lap. Compare
            # both candidates by time instead of letting the pending slot
            # swallow a nearby but different completion.
            candidates = [pending, *emitted_candidates]
            # When consecutive laps have equal rounded times, the game's lap
            # number disambiguates the originating object among completions
            # that are within the timing tolerance.
            timed_candidates = [
                lap for lap in candidates if abs(lap.lap_time_ms - laptime_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
            ]
            numbered_candidates = [lap for lap in timed_candidates if lap.lap_number == game_lap_number]
            pending = self._nearest_lap_match(
                numbered_candidates or timed_candidates,
                laptime_ms,
            )
            if pending is None:
                # A stale or foreign broadcast must not discard the pending
                # completion; a later matching broadcast can still finalize it.
                return None
            already_emitted = pending is not self._pending_lap
        else:
            timed_candidates = [
                lap
                for lap in emitted_candidates
                if abs(lap.lap_time_ms - laptime_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
            ]
            numbered_candidates = [lap for lap in timed_candidates if lap.lap_number == game_lap_number]
            pending = self._nearest_lap_match(
                numbered_candidates or timed_candidates,
                laptime_ms,
            )
            already_emitted = pending is not None
            if pending is None:
                return None

        if abs(laptime_ms - pending.lap_time_ms) > LAP_TIME_RECONCILIATION_TOLERANCE_MS:
            # Mismatch — likely a stale broadcast for a different car.
            return None

        # Since AC Evo 0.7.0 the text boolean can be false on a completed,
        # accepted lap at session end, while flags still carries the useful
        # validity class: 1 = invalid, 2 = valid.
        if validity_flags in (1, 2):
            game_valid = validity_flags == 2
        else:
            game_valid = valid_text == "true"
        # The Relevant onSplit message carries the authoritative game lap number.
        # Correct any physics-derived lap_number (which can be off-by-one) here.
        pending.lap_number = game_lap_number
        prev_state = pending.lap_state

        if prev_state == LapState.OUTLAP:
            # OUTLAP is a structural classification, not a validity verdict.
            # The game flag doesn't change it into a valid lap.
            pass
        elif game_valid:
            if pending.lap_state != LapState.VALID or not pending.is_valid:
                pending.lap_state = LapState.VALID
                pending.lap_type = LapState.VALID.value
                pending.is_valid = True
                log_debug(Component.LOG_PARSER, f"[VALIDITY] Game says valid — #{pending.lap_number} → VALID")
        else:
            if pending.lap_state != LapState.INVALID_GAME or pending.is_valid:
                pending.lap_state = LapState.INVALID_GAME
                pending.lap_type = LapState.INVALID_GAME.value
                pending.is_valid = False
                log_debug(Component.LOG_PARSER, f"[VALIDITY] Game says invalid — #{pending.lap_number} → INVALID_GAME")

        # Tag the lap as carrying an authoritative validity verdict from the
        # game's "Relevant onSplit" broadcast.  This provenance is consumed by
        # SharedSessionManager.update_lap_from_logs so it can protect
        # authoritative-valid results from being overridden by SHM.
        pending.validity_source = "authoritative"
        # Consume only the completion bound to this lap. A broad time lookup
        # could consume the next equal-time completion.
        bound_completion = self._lap_completion_by_lap_id.get(id(pending))
        if bound_completion is not None:
            self._session_manager.consume_lap_completion(bound_completion)

        if already_emitted:
            self._reconciled_lap = pending
        else:
            self.current_session.laps.append(pending)
            self._pending_lap = None
            self._pending_lap_since = None
        log_debug(
            Component.LOG_PARSER,
            f"[LAP] flushed pending #{pending.lap_number} via authoritative flag (game_valid={game_valid})",
        )
        return None if already_emitted else pending

    def _apply_shm_fallback_validity(self, pending: LapData) -> None:
        """Use live graphics validity when ACE omits its log verdict.

        ``Relevant onSplit`` remains authoritative whenever it arrives. This
        fallback is only consulted while finalising a still-pending lap.

        OUTLAP normally remains a stronger structural classification. An
        unambiguous SHM completion within the reconciliation tolerance proves that ACE timed the full
        circuit following a rejected pit prefix. Valid completions retain the
        existing recovery behaviour. Invalid completions are recovered only
        when the SHM, parser, and physics lap counters all agree; otherwise
        the structural OUTLAP is retained.
        """
        was_outlap = pending.lap_state == LapState.OUTLAP
        bound_completion = self._lap_completion_by_lap_id.get(id(pending))
        completion = None

        if was_outlap:
            if (
                bound_completion is not None
                and abs(bound_completion.lap_time_ms - pending.lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
            ):
                matching_completions = [bound_completion]
            else:
                matching_completions = []
            # A time-only binding does not prove that a completion belongs to
            # an outlap. Keep every nearby completion in the ambiguity check;
            # only a single candidate may promote the structural outlap.
            matching_completions.extend(
                candidate
                for candidate in self._session_manager.get_lap_completions_after(float("-inf"))
                if abs(candidate.lap_time_ms - pending.lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
                and (bound_completion is None or candidate.observed_at != bound_completion.observed_at)
            )
            exact_completions = sorted(
                matching_completions,
                key=lambda candidate: (
                    abs(candidate.lap_time_ms - pending.lap_time_ms),
                    candidate.observed_at,
                ),
            )
            shm_counters = [candidate.completed_laps for candidate in exact_completions]
            resolution = "retained_missing_completion"

            if len(exact_completions) == 1:
                candidate = exact_completions[0]
                counters_aligned = candidate.completed_laps == pending.lap_number == pending.physics_lap_number
                if candidate.is_valid is True:
                    completion = candidate
                    resolution = "promoted_valid_completion"
                elif candidate.is_valid is False and counters_aligned:
                    completion = candidate
                    resolution = "promoted_invalid_aligned_completion"
                elif candidate.is_valid is False:
                    resolution = "retained_invalid_counter_mismatch"
                else:
                    resolution = "retained_completion_without_validity"
            elif len(exact_completions) > 1:
                resolution = "retained_ambiguous_completion"

            log_debug(
                Component.LOG_PARSER,
                "[OUTLAP] Provisional resolution "
                f"time_ms={pending.lap_time_ms} "
                f"parser={pending.lap_number} "
                f"physics={pending.physics_lap_number} "
                f"shm={shm_counters or None} "
                f"result={resolution}",
            )
            if completion is None:
                # The log lap is still published as a structural outlap. A
                # bound SHM completion must not remain queued for
                # ``_take_ready_shm_lap`` to publish a duplicate later.
                if bound_completion is not None:
                    self._session_manager.consume_lap_completion(bound_completion)
                return

        if completion is None:
            completion = self._lap_completion_by_lap_id.get(id(pending))
        if completion is None:
            completion = self._session_manager.get_lap_completion_by_time(pending.lap_time_ms)
            if completion is not None:
                self._lap_completion_by_lap_id[id(pending)] = completion
        completion_matches = completion is not None and completion.is_valid is not None

        if completion_matches:
            is_valid = bool(completion.is_valid)
        else:
            lap_number = pending.physics_lap_number or pending.lap_number
            validity = self._session_manager.get_lap_validity_data(lap_number)
            if validity is None or validity.source != "shm_graphics":
                log_debug(
                    Component.LOG_PARSER,
                    "[VALIDITY] SHM fallback skipped — no shm_graphics validity "
                    f"for #{pending.lap_number} (phys={pending.physics_lap_number}, "
                    f"time_ms={pending.lap_time_ms}, validity={validity})",
                )
                if completion is not None:
                    self._session_manager.consume_lap_completion(completion)
                return
            is_valid = validity.is_valid

        pending.is_valid = is_valid
        pending.lap_state = LapState.VALID if is_valid else LapState.INVALID_GAME
        pending.lap_type = pending.lap_state.value
        pending.validity_source = "shm_graphics"

        # The log record is now being published. Consume its originating
        # completion even when the verdict came from the SHM validity map (or
        # remained heuristic), otherwise the ready-SHM path can emit it again.
        if completion is not None:
            self._session_manager.consume_lap_completion(completion)

        if was_outlap and self.current_session is not None:
            # The provisional OUTLAP did not enter a stint at construction
            # time. Enrol it now that a matching completion has been proven to
            # represent a timed lap (valid or counter-aligned invalid).
            stint = self._ensure_stint(pending.tyre_compound)
            if pending.lap_number not in stint.lap_numbers:
                stint.add_lap(
                    pending.lap_number,
                    pending.fuel_used if pending.fuel_reliable else None,
                )
                stint.lap_numbers.sort()
            pending.stint_number = stint.stint_number

        log_debug(
            Component.LOG_PARSER,
            "[VALIDITY] SHM fallback verdict "
            f"#{pending.lap_number} phys={pending.physics_lap_number} "
            f"time_ms={pending.lap_time_ms} "
            f"source={'completion' if completion_matches else 'validity_map'} "
            f"is_valid={is_valid} -> {pending.lap_state.value}",
        )

    def _flush_pending_lap(self) -> Optional[LapData]:
        """Append any buffered lap using the best remaining validity source.

        Used at session end / file EOF where no further authoritative
        validity line will arrive. Returns the flushed lap so callers can
        emit it.
        """
        pending = self._pending_lap
        if pending is None or not self.current_session:
            return None
        self._pending_lap = None
        self._pending_lap_since = None
        self._apply_shm_fallback_validity(pending)
        self.current_session.laps.append(pending)
        log_debug(
            Component.LOG_PARSER,
            f"[LAP] flushed pending #{pending.lap_number} on session/EOF (validity source={pending.validity_source})",
        )
        return pending

    def _flush_pending_lap_after_grace(self) -> Optional[LapData]:
        """Finalise a live lap when ACE does not emit a validity log line."""
        if self._pending_lap_since is None:
            return None
        if time.monotonic() - self._pending_lap_since < self.PENDING_VALIDITY_GRACE_SECONDS:
            return None
        return self._flush_pending_lap()

    def _take_ready_shm_lap(self) -> Optional[LapData]:
        """Build a live lap when ACE's file logger has not flushed yet."""
        completions = self._session_manager.get_lap_completions_after(self._last_shm_completion_observed_at)
        if not completions:
            return None
        # Consume oldest-first. ACE can delay file-log writes for more than a
        # full lap; keeping only the newest SHM transition swaps or drops laps.
        completion = completions[0]
        if time.monotonic() - completion.observed_at < self.PENDING_VALIDITY_GRACE_SECONDS:
            return None

        # A capture completion can arrive before the log parser has observed
        # the corresponding session boundary. Keep it at the head of the
        # queue until the log session exists; advancing the cursor here would
        # lose the completion permanently when the identity arrives later.
        if self.current_session is None:
            return None

        if self._pending_lap is not None:
            pending_completion = self._lap_completion_by_lap_id.get(id(self._pending_lap))
            if pending_completion is completion:
                self._last_shm_completion_observed_at = completion.observed_at
                self._session_manager.consume_lap_completion(completion)
                return None
            if (
                pending_completion is None
                and abs(self._pending_lap.lap_time_ms - completion.lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
            ):
                self._lap_completion_by_lap_id[id(self._pending_lap)] = completion
                self._last_shm_completion_observed_at = completion.observed_at
                self._session_manager.consume_lap_completion(completion)
                return None
        if self.current_session:
            unmatched_laps = [lap for lap in self.current_session.laps if id(lap) not in self._lap_completion_by_lap_id]
            matching_laps = [
                lap
                for lap in unmatched_laps
                if abs(lap.lap_time_ms - completion.lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
            ]
            if matching_laps:
                matched_lap = min(
                    matching_laps,
                    key=lambda lap: abs(lap.lap_time_ms - completion.lap_time_ms),
                )
                self._lap_completion_by_lap_id[id(matched_lap)] = completion
                self._last_shm_completion_observed_at = completion.observed_at
                self._session_manager.consume_lap_completion(completion)
                return None

        self._last_shm_completion_observed_at = completion.observed_at

        # SHM can report the finish before ACE flushes the corresponding log
        # lines. It has validity but no structural outlap classification. If
        # the log has already armed an outlap, consume this completion and wait
        # for ``New lap`` to supply the boundary without publishing a false
        # INVALID_GAME card first.
        if self._ip.is_outlap:
            log_debug(
                Component.LOG_PARSER,
                f"[OUTLAP] Deferred SHM completion {completion.lap_time_ms} ms until structural log reconciliation",
            )
            return None

        prior_numbers = [lap.lap_number for lap in self.current_session.laps]
        if self._pending_lap is not None:
            prior_numbers.append(self._pending_lap.lap_number)
        lap_number = max(prior_numbers, default=0) + 1
        is_valid = completion.is_valid is not False
        lap_state = LapState.VALID if is_valid else LapState.INVALID_GAME
        minutes, remainder = divmod(completion.lap_time_ms, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        compound = self.context.tyre.compound_name
        stint = self._ensure_stint(compound)
        stint.add_lap(lap_number, None)
        lap = LapData(
            lap_number=lap_number,
            physics_lap_number=completion.completed_laps,
            lap_time_ms=completion.lap_time_ms,
            lap_time_str=f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}",
            lap_state=lap_state,
            lap_type=lap_state.value,
            is_valid=is_valid,
            validity_source="shm_graphics",
            tyre_compound=compound,
            stint_number=stint.stint_number,
            timestamp=completion.timestamp,
        )
        self.current_session.laps.append(lap)
        self._shm_emitted_laps.append(lap)
        self._lap_completion_by_lap_id[id(lap)] = completion
        self._session_manager.consume_lap_completion(completion)
        # Do not reset the log accumulator here. ACE may already have flushed
        # early-sector lines while still buffering S3/New lap; those partial
        # fields must survive until the delayed completion line reconciles
        # this SHM-first lap.
        log_debug(
            Component.LOG_PARSER,
            f"[LAP] emitted from live SHM before log flush: "
            f"#{lap.lap_number} {lap.lap_time_str} state={lap.lap_state.value}",
        )
        return lap

    # ── Aborted lap emission ──────────────────────────────────────────────────

    def _maybe_emit_aborted_lap(self) -> Optional[LapData]:
        """Produce an ABORTED LapData if the in-progress lap has meaningful data.

        Called when a session ends unexpectedly (game quit, session change)
        mid-lap. Requires at least one sector to have been recorded — otherwise
        there's nothing worth emitting.
        """
        ip = self._ip
        has_data = ip.splits or ip.fuel_used is not None or ip.distance_hundredm is not None
        if not has_data or not self.current_session:
            return None

        self._flush_pending_compound_batch()

        compound = self.context.tyre.compound_name
        lap_number = ip.physics_lap_num or (len(self.current_session.laps) + 1)

        aborted = LapData(
            lap_number=lap_number,
            physics_lap_number=ip.physics_lap_num,
            lap_time_ms=0,
            lap_time_str="--:--.---",
            sector1_ms=ip.splits.get(0),
            sector2_ms=ip.splits.get(1),
            sector3_ms=ip.splits.get(2),
            sectors_consistent=None,
            lap_state=LapState.ABORTED,
            lap_type=LapState.ABORTED.value,
            is_valid=False,
            fuel_used=ip.fuel_used,
            fuel_reliable=ip.fuel_reliable,
            tyre_compound=compound,
            stint_number=self._current_stint.stint_number if self._current_stint else 1,
            distance_hundredm=ip.distance_hundredm,
        )

        self.current_session.laps.append(aborted)
        log_debug(
            Component.LOG_PARSER,
            f"[LAP] ABORTED #{lap_number}  sectors={sorted(ip.splits.keys())}  dist={ip.distance_hundredm}",
        )
        return aborted

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def _start_new_session(self, session_type: str, _line: str) -> None:
        """Fallback session creator for edge cases (no 'Game Started!' seen)."""
        self._reset_session_boundary("fallback session start", finalize_current_session=True)
        self.context.reset_for_new_session()
        self.current_session = SessionData(
            session_type=SESSION_TYPE_MAP.get(session_type, session_type),
            game_version=self.context.game_version,
            track=self.context.current_track,
            car=self.context.current_car,
            player_name=self.context.player_name,
            player_id=self.context.player_id,
            car_uuid=self.context.car_uuid,
            weather=self.context.weather,
            fuel_reliable=not self.context.car_is_hybrid,
        )
        self._reset_in_progress()
        self._finalise_stints()
        self._sync_shared_session(self.current_session)
        log_debug(Component.LOG_PARSER, f"[SESSION] Fallback session created: type={session_type}")

    def _finalise_current_session(self) -> None:
        if not self.current_session:
            return
        session_car = self.current_session.car
        session_lap_count = len(self.current_session.laps)
        has_pending = self._pending_lap is not None
        log_debug(
            Component.LOG_PARSER,
            f"[FINALISE] car={session_car}  laps={session_lap_count}  "
            f"pending_lap={has_pending}  session_id={self.current_session.session_id[:8]}...",
        )
        self._flush_pending_compound_batch()
        # Session-end metadata should reflect the latest known tyre state even
        # if no lap was completed after the final pit/setup change.
        self.current_session.tyre_compound = self.context.tyre.compound_name
        # Flush any buffered lap whose authoritative validity never arrived
        # (e.g. game quit immediately after lap completion). Emission is
        # handled by callers that have an event loop; here we only ensure
        # the lap is recorded in `session.laps`.
        flushed_pending = self._flush_pending_lap()
        if flushed_pending is not None:
            log_debug(
                Component.LOG_PARSER,
                f"[FINALISE] flushed pending lap #{flushed_pending.lap_number} "
                f"{flushed_pending.lap_time_str} into session {session_car}",
            )
        # Emit aborted lap if the session ends mid-lap
        aborted = self._maybe_emit_aborted_lap()
        if aborted is not None:
            log_debug(
                Component.LOG_PARSER, f"[FINALISE] emitted ABORTED lap #{aborted.lap_number} for session {session_car}"
            )
        self._finalise_stints()
        self._session_manager.update_from_logs(self.current_session)
        log_debug(
            Component.LOG_PARSER,
            f"[FINALISE] pushed {len(self.current_session.laps)} laps into shared session for car={session_car}",
        )
        if self.current_session.laps:
            self.sessions.append(self.current_session)
        self.current_session = None
        self._session_active_from_logs = False
        self._reset_in_progress()

    # ── Master line processor ─────────────────────────────────────────────────

    def _process_line(self, line: str) -> Optional[LapData]:
        """Process one raw log line. Returns LapData when a lap completes.

        Delegates to focused phase methods for clarity:
          1. Pre-processing (timestamp, buffer, activity)
          2. Metadata (always-evaluated handlers)
          3. Session lifecycle (start/end detection)
          4. Setup values
          5. In-session events (fuel, splits, physics)
          6. Lap completion / validity
        """
        line = line.strip()
        if not line:
            return None

        self._preprocess_line(line)
        self._process_metadata(line)
        if self._process_session_lifecycle(line):
            return None
        self._process_setup(line)
        if not self.current_session:
            self._maybe_start_fallback_session(line)
        if not self.current_session:
            return None
        self._process_session_events(line)
        return self._process_lap_completion(line)

    # ── Phase helpers ─────────────────────────────────────────────────────────

    def _preprocess_line(self, line: str) -> None:
        """Extract timestamp, flush pending compound batch, add to buffer."""
        line_ts = self._extract_line_timestamp(line)
        if self._pending_compound_ts and line_ts and line_ts != self._pending_compound_ts:
            self._flush_pending_compound_batch()
        self._add_to_log_buffer(line)
        self._last_activity_ts = time.time()
        self._update_session_activity_from_line(line)

    def _process_metadata(self, line: str) -> None:
        """Order-independent metadata handlers (always evaluated)."""
        self._handle_version(line)
        self._handle_track_name(line)
        self._handle_connect(line)
        self._handle_player_car_binding(line)
        self._handle_driver(line)
        self._handle_gamecar_meta(line)
        self._handle_car_teleport(line)
        self._handle_compound(line)
        self._handle_weather(line)

    def _process_session_lifecycle(self, line: str) -> bool:
        """Handle session start/end detection. Returns True if a new session started."""
        if self._handle_session_start(line):
            return True
        if "END_SESSION" in line and self.context.car_uuid:
            if self.context.car_uuid in line:
                log_debug(Component.LOG_PARSER, "[SESSION] END_SESSION for player car — finalising")
                self._finalise_current_session()
        return False

    def _process_setup(self, line: str) -> None:
        """Handle setup group values (captured regardless of session state)."""
        self._handle_setup_group(line)

    def _process_session_events(self, line: str) -> None:
        """Handle in-session events (fuel, sector splits, physics lap)."""
        self._handle_penalty_signals(line)
        self._handle_fuel(line)
        self._handle_splits_race(line)
        self._handle_splits_practice(line)
        self._handle_outlap_signals(line)
        self._handle_physics_lap(line)

    def _process_lap_completion(self, line: str) -> Optional[LapData]:
        """Handle lap completion and authoritative validity lines.

        Two paths can produce an emittable lap on a single line:
          * ``_handle_lap_complete`` builds a fresh lap and may flush a
            previously-buffered lap (when no authoritative validity arrived).
          * ``_handle_lap_validity`` finalises the buffered lap with the
            game's authoritative valid/invalid flag.
        At most one fires per line, so returning whichever is non-None is
        sufficient.
        """
        completed = self._handle_lap_complete(line)
        if completed is not None:
            return completed
        return self._handle_lap_validity(line)

    # ── Public API ────────────────────────────────────────────────────────────

    async def parse_file(self) -> list[SessionData]:
        """One-shot parse of the full log file."""
        if not self.log_path.exists():
            await self._emit_status(f"Log file not found: {self.log_path}")
            return []

        await self._emit_status(f"Parsing {self.log_path} …")
        with open(self.log_path, "r", encoding="utf-8", errors="ignore") as fh:
            async for line in _iter_lines_cooperatively(fh):
                completed = self._process_line(line)
                if completed and self.current_session:
                    await self._emit_lap(self.current_session, completed)

        self._flush_pending_compound_batch()
        self._finalise_current_session()
        await self._emit_status(f"Done — {len(self.sessions)} session(s)")
        return self.sessions

    async def follow(self, poll_interval: float = 0.25) -> None:
        """Live-tail the log file, emitting callbacks for new laps only.

        Reads existing content first to build context (historical laps are
        NOT emitted), then streams new lines as they arrive.
        """
        log_debug(Component.LOG_PARSER, "Debug logging initialized")
        log_debug(Component.LOG_PARSER, f"follow() starting — log path: {self.log_path}")
        self._running = True

        while self._running:
            if self._log_dir is not None:
                _latest = self._find_latest_log(self._log_dir)
                if _latest is not None and _latest != self.log_path:
                    self.log_path = _latest
                    await self._emit_status(f"Switched to latest log: {self.log_path}")

            while self._running and not self.log_path.exists():
                await self._emit_status(f"Waiting for log file: {self.log_path}")
                if self._log_dir is not None:
                    _latest = self._find_latest_log(self._log_dir)
                    if _latest is not None and _latest != self.log_path:
                        self.log_path = _latest
                        await self._emit_status(f"Switched to latest log: {self.log_path}")
                await asyncio.sleep(poll_interval)

            if not self._running:
                break

            await self._emit_status("Reading existing log …")
            _restart = False

            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as fh:
                # ── Historical pass ────────────────────────────────────────────────
                historical_laps = 0
                async for line in _iter_lines_cooperatively(fh):
                    if not self._running:
                        return
                    try:
                        lap = self._process_line(line)
                        if lap:
                            historical_laps += 1
                    except (RuntimeError, ValueError, TypeError) as exc:
                        log_debug(Component.LOG_PARSER, f"[ERROR] Historical parse: {exc}")

                log_debug(
                    Component.LOG_PARSER,
                    f"Historical pass: {historical_laps} lap(s). Session: {self.current_session is not None}",
                )

                # Discard historical laps — only lines observed after the
                # live-tail boundary may produce callbacks. In particular, a
                # historical final lap with no Relevant onSplit verdict must
                # not survive as ``_pending_lap`` and get emitted when the
                # user later exits the session. Genuine live pending laps are
                # still preserved and flushed by the exit/session-end paths.
                if self.current_session:
                    historical_pending = self._pending_lap
                    log_debug(
                        Component.LOG_PARSER,
                        f"[HISTORICAL] Clearing laps from session: "
                        f"car={self.current_session.car}  "
                        f"track={self.current_session.track}  "
                        f"pending_discarded={historical_pending is not None}",
                    )
                    self.current_session.laps.clear()
                    self.current_session.stints.clear()
                    self._pending_lap = None
                    self._pending_lap_since = None
                    self._reconciled_lap = None
                    self._shm_emitted_laps.clear()
                    latest_completion = self._session_manager.get_latest_lap_completion()
                    self._last_shm_completion_observed_at = latest_completion.observed_at if latest_completion else 0.0
                    self._finalise_stints()
                    self._reset_in_progress()

                self._emit_callbacks = True

                if self.current_session:
                    await self._emit_status("Monitoring for new laps …")
                    if self.current_session.player_id:
                        await self._emit_user_detected(
                            self.current_session.player_id,
                            self.current_session.player_name,
                        )
                    if self._session_active_from_logs:
                        await self._emit_game_status(True, trigger="historical active session")
                else:
                    await self._emit_status("Ready — waiting for session …")

                if self.context.game_version != "Unknown" and self.on_game_version:
                    try:
                        await self.on_game_version(self.context.game_version)
                    except (RuntimeError, asyncio.CancelledError) as exc:
                        log_debug(Component.LOG_PARSER, f"[ERROR] on_game_version: {exc}")

                # ── Live tail ──────────────────────────────────────────────────────
                log_debug(Component.LOG_PARSER, "Entering live tail loop …")
                while self._running:
                    line_start_pos = fh.tell()
                    line = fh.readline()

                    # Guard against processing partially written lines in live-tail.
                    # If a trailing newline is missing, rewind and retry on next poll.
                    if line and not line.endswith("\n"):
                        fh.seek(line_start_pos)
                        await asyncio.sleep(poll_interval)
                        continue

                    if line:
                        if "Game Started!" in line:
                            await self._emit_game_status(True, trigger="Game Started!")
                        if "has started the race!" in line:
                            if self._line_mentions_player_car(line):
                                await self._emit_game_status(True, trigger="has started the race!")
                                # After session restart, car detection may not re-fire.
                                # Ensure we have a session so lap completion callbacks work.
                                if not self.current_session:
                                    self._start_new_session("RACE", line)
                        # AC Evo: pause-menu "Restart Session" emits this line
                        # but does NOT emit a fresh "Game Started!", so we
                        # have to drive the buffer reset ourselves.
                        if "request made GameModeRequestRestartSession" in line:
                            await self._emit_session_restart()
                        # AC Evo: pause-menu "Exit to Menu" (GameModeRequestExit)
                        # or "Exit to Desktop" (GameModeRequestQuitGame) — the
                        # user is leaving the session entirely.
                        elif (
                            "request made GameModeRequestExit" in line or "request made GameModeRequestQuitGame" in line
                        ):
                            # Flush any pending lap whose authoritative
                            # validity never arrived (critical for
                            # single-split tracks like Nordschleife Tourist
                            # where the game may not emit a "Relevant
                            # onSplit for Combo" validity broadcast).
                            session_car = self.current_session.car if self.current_session else "None"
                            completed = self._flush_pending_lap()
                            if completed is not None and self.current_session is not None:
                                log_debug(
                                    Component.LOG_PARSER,
                                    f"[EXIT] Flushing pending lap on game exit: "
                                    f"#{completed.lap_number} {completed.lap_time_str}  "
                                    f"session_car={session_car}  "
                                    f"lap_state={completed.lap_state.value}",
                                )
                                await self._emit_lap(self.current_session, completed)
                            self._finalise_current_session()
                            await self._emit_game_status(False, trigger="game exit request")
                        if "END_SESSION" in line:
                            if self._line_mentions_player_car(line):
                                completed = self._flush_pending_lap()
                                if completed is not None and self.current_session is not None:
                                    await self._emit_lap(self.current_session, completed)
                                await self._emit_game_status(False, trigger="END_SESSION matched")
                            else:
                                log_debug(Component.LOG_PARSER, "[SESSION_END] ignoring END_SESSION for non-player car")
                        if "onSetPlayerCurrentCarCommand: remove car" in line:
                            m = self._pats["remove_car"].search(line)
                            if m and self._is_player_car(m.group(1)):
                                log_debug(Component.LOG_PARSER, f"[SESSION_END] remove car detected: {m.group(1)}")
                                await self._emit_session_end()

                        try:
                            completed = self._process_line(line)
                        except (RuntimeError, ValueError, TypeError) as exc:
                            log_debug(Component.LOG_PARSER, f"[ERROR] Live process_line: {exc}")
                            continue

                        if self._reconciled_lap is not None and self.current_session is not None:
                            reconciled = self._reconciled_lap
                            self._reconciled_lap = None
                            await self._emit_lap_update(self.current_session, reconciled)

                        if completed:
                            session = self.current_session or SessionData(track="Unknown", car="Unknown")
                            using_fallback = self.current_session is None
                            log_debug(
                                Component.LOG_PARSER,
                                f"[LIVE_EMIT] Emitting lap #{completed.lap_number} "
                                f"{completed.lap_time_str}  "
                                f"session_car={session.car}  "
                                f"fallback_session={using_fallback}  "
                                f"lap_state={completed.lap_state.value}",
                            )
                            try:
                                await self._emit_lap(session, completed)
                            except (RuntimeError, asyncio.CancelledError) as exc:
                                log_debug(Component.LOG_PARSER, f"[ERROR] emit_lap: {exc}")

                        # ACE 0.8.1 can omit the post-lap ``Relevant onSplit``
                        # validity broadcast.  Give legacy builds a brief
                        # chance to provide it, then publish from the best
                        # remaining live source instead of delaying one lap.
                        grace_completed = self._flush_pending_lap_after_grace()
                        if grace_completed is not None and self.current_session is not None:
                            await self._emit_lap(self.current_session, grace_completed)
                        shm_completed = self._take_ready_shm_lap()
                        if shm_completed is not None and self.current_session is not None:
                            await self._emit_lap(self.current_session, shm_completed)
                        continue

                    # No new data — check for a newer log file (new game session)
                    grace_completed = self._flush_pending_lap_after_grace()
                    if grace_completed is not None and self.current_session is not None:
                        await self._emit_lap(self.current_session, grace_completed)
                    shm_completed = self._take_ready_shm_lap()
                    if shm_completed is not None and self.current_session is not None:
                        await self._emit_lap(self.current_session, shm_completed)

                    if self._log_dir is not None:
                        _latest = self._find_latest_log(self._log_dir)
                        if _latest is not None and _latest != self.log_path:
                            self._flush_pending_compound_batch()
                            log_debug(Component.LOG_PARSER, f"[NEW_LOG] Switching to {_latest.name}")
                            if self._last_emitted_game_status is not False:
                                await self._emit_game_status(False, trigger="new log file detected")
                            self._reset_session_boundary("log rotation", finalize_current_session=False)
                            self.context = LogContext()
                            self._emit_callbacks = True
                            self._last_emitted_game_status = None
                            await self._emit_status("New game session log detected …")
                            self.log_path = _latest
                            _restart = True
                            break

                    # Check for log truncation (game restart on same file)
                    try:
                        current_size = os.path.getsize(self.log_path)
                    except OSError:
                        current_size = None

                    if current_size is not None and current_size < fh.tell():
                        self._flush_pending_compound_batch()
                        log_debug(Component.LOG_PARSER, "[TRUNCATE] Log file reset — restarting context")
                        if self._last_emitted_game_status is not False:
                            await self._emit_game_status(False, trigger="log file truncated")
                        self._reset_session_boundary("log truncation", finalize_current_session=False)
                        self.context = LogContext()
                        self._emit_callbacks = True
                        self._last_emitted_game_status = None
                        await self._emit_status("Log file reset — restarting …")
                        fh.seek(0)

                    await asyncio.sleep(poll_interval)

            if not _restart:
                break

        log_debug(Component.LOG_PARSER, "follow() exiting")

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def get_current_session(self) -> Optional[SessionData]:
        return self.current_session

    def get_player_id(self) -> Optional[str]:
        return self.context.player_id
