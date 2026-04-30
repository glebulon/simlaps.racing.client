"""
ACE Log Parser — v3 (Refactored)
Based on deep multi-log analysis + engineering review of v2.

This module now imports data models from src.models for better maintainability.
"""

import re
import os
import sys
import time
import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable
from pathlib import Path

# Import data models from the models module
from ..models import (
    LapState,
    InProgressLap,
    StintData,
    LapData,
    SessionData,
    SharedSessionManager,
    TyreState,
    LogContext,
    # Constants
    PIT_TELEPORT_DISTANCE_M,
    TRACK_LIMIT_INVALIDATION_THRESHOLD_M,
    SECTOR_SUM_TOLERANCE_MS,
    HYBRID_FUEL_THRESHOLD_L,
    HYBRID_SPIKE_SESSION_THRESHOLD,
    MIN_FULL_LAP_HUNDREDM,
    KNOWN_HYBRID_CARS,
    SESSION_TYPE_MAP,
    PRACTICE_LIKE,
    RACE_LIKE,
)
from ..utils.debug_logger import DebugLogger

# Global debug logger instance
_debug = DebugLogger()


# ─── Callback type aliases ────────────────────────────────────────────────────

LapCallback          = Callable[[SessionData, LapData], Awaitable[None]]
StatusCallback       = Callable[[str], Awaitable[None]]
GameStatusCallback   = Callable[[bool], Awaitable[None]]
UserDetectedCallback = Callable[[str, Optional[str]], Awaitable[None]]
GameVersionCallback  = Callable[[str], Awaitable[None]]
SessionEndCallback   = Callable[[], Awaitable[None]]
SessionRestartCallback = Callable[[], Awaitable[None]]


# ─── Main parser ──────────────────────────────────────────────────────────────

class LogParser:
    """Parse ACE game logs and extract structured lap/session data.

    Supports one-shot (`parse_file`) and live-tail (`follow`) modes.
    """

    DEFAULT_LOG_DIR = Path.home() / "Saved Games" / "ACE" / "Logs"
    DEFAULT_LOG_PATH = DEFAULT_LOG_DIR

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

        # Most recently completed lap, buffered until either:
        #   a) the game's authoritative `Relevant onSplit ... valid` line
        #      arrives (typically ~ms later) and we apply it, or
        #   b) the next lap completes / the session ends, in which case the
        #      heuristic state stands.
        # Buffering by exactly one lap lets the authoritative game flag
        # override our local sector-sum / split heuristics before the lap is
        # emitted to listeners.
        self._pending_lap: Optional[LapData] = None

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
        self._pending_compound_ts: Optional[str] = None
        self._pending_compound_updates: dict[int, str] = {}
        self._pending_compound_confirmed: set[int] = set()

        self._compile_patterns()

    # ── Pattern compilation ───────────────────────────────────────────────────

    
    def _compile_patterns(self) -> None:
        self._pats: dict[str, re.Pattern] = {
            "version": re.compile(r"Build release ([^,]+),"),

            "track_name_direct": re.compile(r"TRACK NAME (.+)"),
            "track_load": re.compile(
                r"Loading (?:scene|Scene) .+ content\\tracks\\([^\\]+)"
            ),

            "driver_line": re.compile(r"\tDriver (.+) on car ([\w_]+)"),

            "connect": re.compile(
                r"(\d+) connected(?: \(\d+\))? on car ([\w_]+), with new carId ([a-f0-9\-]+)"
            ),
            "connecting_gamecar": re.compile(
                r"connecting gamecar ([a-f0-9\-]+) \((.+)\)"
            ),

            # Full pipe-delimited Game Started line
            "game_started": re.compile(
                r"\[gameplay\] \[info\] Game Started!\s*GameModeType_([A-Z_]+)"
                r" \| (.+?) \| ([\w_]+) \| GameModeSelectionWeatherType_(\w+)"
            ),

            "date": re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),

            "set_compound_old": re.compile(
                r"setCompound Tyre:\s*(\d+)\s+compound(?: name)?:\s*(\w+)"
            ),
            "platformcore_compound": re.compile(
                r"CarId:\s*([a-f0-9\-]+)\s+Tyre:\s*(\d+)\s+compound:\s*(\d+)"
            ),

            "loading_tyre_compound": re.compile(r"LOADING TYRE COMPOUND (.+)"),
            "tyre_compound_summary": re.compile(r"TYRE COMPOUND: (.+)"),

            "fuel_filled": re.compile(
                r"FUEL car ([a-f0-9\-]+) (?:filled|setup) with ([\d.]+) L"
            ),

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

            # Practice-mode: player-only sector event
            "practice_split": re.compile(
                r"\[gameplay\] \[info\] On Split start \d+ end \d+"
                r" id (\d+) splittime (\d+)"
            ),

            "split_end": re.compile(
                r"\[gameplay\] \[info\] On Split end with all splits"
            ),

            "physics_lap": re.compile(
                r"\[physics\] \[info\] Lap test evOnLapCompleted (\d+) completed"
            ),

            # Car removed: fires when the player's car is removed at session end
            "remove_car": re.compile(
                r"onSetPlayerCurrentCarCommand: remove car ([a-f0-9\-]+)"
            ),

            # New lap: timestamp | car_uuid | lap_time_str
            "lap_finish": re.compile(
                r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]"
                r" \[gameplay\] \[info\] New lap carId ([a-f0-9\-]+): ([\d:.]+)"
            ),

            # Game's authoritative per-lap validity flag.  Emitted on the
            # [network] channel ~ms after `New lap carId`, e.g.:
            #   Relevant onSplit for Combo 6@2: laptime 146939, valid true,
            #   flags 2, lap 1 (prev 0)
            # Captures: laptime_ms, valid_str ("true"|"false"), lap_num.
            "lap_validity": re.compile(
                r"\[network\] \[info\] Relevant onSplit for Combo "
                r"\d+@\d+: laptime (\d+), valid (true|false), "
                r"flags \d+, lap (\d+)"
            ),

            # AC Evo emits the same `UINotificationType_SessionPenalty`
            # notification line for BOTH penalty additions and clearances.
            # Each event is followed by a discriminating warning line:
            #   added:   `[warning] true {PENALTY_ADDED_KEY} #0`
            #   cleared: `[warning] false {PENALTY_CLEARED_KEY} #0`
            # We must only match the addition discriminator — matching the
            # generic notification line caused every penalty *clear* to flip
            # `has_penalty=True`, invalidating clean racing laps.
            "penalty": re.compile(r"\{PENALTY_ADDED_KEY\}"),
            "setup_group": re.compile(
                r"KS-SETUP-GROUP\s+(.+)$"
            ),
        }

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _parse_lap_time_ms(self, time_str: str) -> int:
        parts = time_str.replace(":", ".").split(".")
        if len(parts) == 3:
            return (int(parts[0]) * 60 + int(parts[1])) * 1000 + int(
                parts[2].ljust(3, "0")[:3]
            )
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

    @staticmethod
    def _normalize_car_uuid(car_uuid: Optional[str]) -> str:
        return (car_uuid or "").replace("-", "").lower()

    def _is_player_car(self, car_uuid: str) -> bool:
        normalized = self._normalize_car_uuid(car_uuid)
        return (
            normalized == self._normalize_car_uuid(self.context.car_uuid)
            or normalized in {
                self._normalize_car_uuid(uuid)
                for uuid in self.context.player_car_uuids
            }
        )

    def _line_mentions_player_car(self, line: str) -> bool:
        if not self.context.car_uuid and not self.context.player_car_uuids:
            return False
        normalized_line = self._normalize_car_uuid(line)
        if self.context.car_uuid and self._normalize_car_uuid(self.context.car_uuid) in normalized_line:
            return True
        return any(
            self._normalize_car_uuid(uuid) in normalized_line
            for uuid in self.context.player_car_uuids
        )

    def _update_session_activity_from_line(self, line: str) -> None:
        """Track whether the latest parsed log state still looks drivable."""
        if "Game Started!" in line or "has started the race!" in line:
            self._session_active_from_logs = True
            return

        if "request made GameModeRequestExit" in line:
            self._session_active_from_logs = False
            return

        if "END_SESSION car" in line and self._line_mentions_player_car(line):
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
        for suffix in (
            " Race Race", " Race", " Time Attack Practice",
            " Time Attack", " Practice", " Qualifying", " Hotlap", " Drift",
        ):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        return raw.strip()

    def _reset_in_progress(self) -> None:
        self._ip = InProgressLap()

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
            _debug.log(f"[ERROR] export_logs_to_file: {exc}")
            return False

    # ── Async emitters ────────────────────────────────────────────────────────

    async def _emit_status(self, status: str) -> None:
        _debug.log(f"[STATUS] {status}")
        if self.on_status_change:
            try:
                await self.on_status_change(status)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_status_change: {exc}")

    async def _emit_lap(self, session: SessionData, lap: LapData) -> None:
        self._session_manager.update_lap_from_logs(lap, session_data=session)
        _debug.log(
            f"[EMIT_LAP] #{lap.lap_number} {lap.lap_time_str} "
            f"state={lap.lap_state.value}"
        )
        if self.on_lap_complete:
            try:
                await self.on_lap_complete(session, lap)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_lap_complete: {exc}")

    def _sync_shared_session(self, session: Optional[SessionData]) -> None:
        if session is None:
            return
        self._session_manager.update_from_logs(
            SessionData(
                session_id=session.session_id,
                game_version=session.game_version,
                session_type=session.session_type,
                car=session.car,
                track=session.track,
                weather=session.weather,
                player_name=session.player_name,
                player_id=session.player_id,
                car_uuid=session.car_uuid,
                tyre_compound=session.tyre_compound,
                initial_fuel=session.initial_fuel,
                fuel_used_session=session.fuel_used_session,
                fuel_reliable=session.fuel_reliable,
                setup_notes=session.setup_notes,
                start_time=session.start_time,
                laps=[],
                stints=[],
            )
        )

    async def _emit_game_status(self, is_running: bool, trigger: str = "unknown") -> None:
        """Emit game status change, logging if duplicate or state change."""
        if self._last_emitted_game_status == is_running:
            _debug.log(f"[GAME_STATUS] DUPLICATE EVENT (ignored): is_running={is_running}, trigger={trigger}, last={self._last_emitted_game_status}")
            print(f"[LOG_PARSER] Duplicate game status {is_running} from {trigger}, ignoring")
            return
        
        _debug.log(f"[GAME_STATUS] STATE CHANGE: is_running={is_running}, trigger={trigger}, last={self._last_emitted_game_status}")
        print(f"[LOG_PARSER] Game status change: {is_running} (trigger: {trigger}, was: {self._last_emitted_game_status})")
        self._last_emitted_game_status = is_running
        
        if self.on_game_status_change:
            try:
                await self.on_game_status_change(is_running)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_game_status_change: {exc}")

    async def _emit_session_end(self) -> None:
        _debug.log("[SESSION_END] car removed from session")
        if self.on_session_end:
            try:
                await self.on_session_end()
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_session_end: {exc}")

    async def _emit_session_restart(self) -> None:
        """Player clicked Restart Session in the pause menu.

        AC Evo restarts the same session in place — no new ``Game Started!``
        line follows — so downstream consumers (e.g. telemetry capture) must
        be told explicitly to clear any in-flight buffer and start fresh.
        """
        _debug.log("[SESSION_RESTART] user requested restart")
        # Reset parser-side per-session state so stale flags from the old run
        # (penalty, track-limit, sector splits, physics_lap_num, etc.) don't
        # leak into the first lap of the restarted session.
        # Also reset the dedup guard so the upcoming game-status True event is
        # not silently dropped (restart does not emit a fresh "Game Started!").
        self._last_emitted_game_status = None
        self._reset_in_progress()
        if self.current_session:
            self._finalise_current_session()
        if self.on_session_restart:
            try:
                await self.on_session_restart()
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_session_restart: {exc}")

    async def _emit_user_detected(
        self, steam_id: str, player_name: Optional[str]
    ) -> None:
        _debug.log(f"[USER] steam_id={steam_id} name={player_name}")
        if self.on_user_detected:
            try:
                await self.on_user_detected(steam_id, player_name)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_user_detected: {exc}")

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
            _debug.log(f"[STINT] Stint 1 started on {compound}")
            return self._current_stint

        # Compound changed → new stint (tyre change at pit stop)
        if compound != self._current_stint.tyre_compound:
            _debug.log(
                f"[STINT] Compound changed {self._current_stint.tyre_compound!r} "
                f"→ {compound!r}: starting stint "
                f"{self._current_stint.stint_number + 1}"
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
        elif (
            ("Loading scene" in line or "Loading Scene" in line)
            and "content\\tracks" in line
        ):
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
        already_has_steam = self._is_steam_id(self.context.player_id or "")

        if self._is_steam_id(pid) or not already_has_steam:
            self.context.player_id = pid
            self.context.current_car = car
            self.context.car_uuid = car_uuid
            self.context.player_car_uuids.add(car_uuid)
            self.context.car_is_hybrid = car in KNOWN_HYBRID_CARS

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

            _debug.log(
                f"[CONNECT] pid={pid} car={car} uuid={car_uuid} "
                f"hybrid={self.context.car_is_hybrid}"
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
                    _debug.log(f"[CAR_TELEPORT] Player car detected: {car_uuid}")
                break

    def _handle_compound(self, line: str) -> None:
        self._handle_compound_v2(line)
        return
        # Check for LOADING TYRE COMPOUND format (appears after CarTeleportCompleted)
        if "LOADING TYRE COMPOUND" in line:
            m = self._pats["loading_tyre_compound"].search(line)
            if m and self._last_car_uuid and self._is_player_car(self._last_car_uuid):
                compound_name = m.group(1).strip()  # Use full name directly
                # Set all 4 tires to the same compound, replacing any existing values
                self.context.tyre.set_all(compound_name)
                _debug.log(
                    f"[COMPOUND] All tires → {compound_name} "
                    f"(resolved: {self.context.tyre.compound_name})"
                )
            return
        
        # TYRE COMPOUND summary lines are ignored - they include all cars in
        # session. Likewise, platformCore numeric "CarId ... compound: N"
        # events are not reliable compound identifiers in ACE and can disagree
        # with the authoritative physics "setCompound ... compound name: XX"
        # line for the same tyre update.
        if "setCompound Tyre:" not in line:
            return

        m = self._pats["set_compound_old"].search(line)
        if not m:
            return

        pos = int(m.group(1))
        code = m.group(2)

        compound_name = code.strip()

        # Only valid tyre positions
        if pos not in (0, 1, 2, 3):
            return

        self.context.tyre.set(pos, compound_name)

        _debug.log(
            f"[COMPOUND] Tyre {pos} → {code} "
            f"(resolved: {self.context.tyre.compound_name})"
        )

    def _handle_compound_v2(self, line: str) -> None:
        # Check for LOADING TYRE COMPOUND format (appears after CarTeleportCompleted)
        if "LOADING TYRE COMPOUND" in line:
            self._flush_pending_compound_batch()
            m = self._pats["loading_tyre_compound"].search(line)
            if m and self._last_car_uuid and self._is_player_car(self._last_car_uuid):
                compound_name = m.group(1).strip()
                self.context.tyre.set_all(compound_name)
                _debug.log(
                    f"[COMPOUND] All tires -> {compound_name} "
                    f"(resolved: {self.context.tyre.compound_name})"
                )
            return

        # TYRE COMPOUND summary lines are ignored - they include all cars in
        # session. platformCore numeric lines are only used as player-car
        # confirmation for an adjacent physics setCompound batch.
        if "setCompound Tyre:" not in line and "CarId:" not in line:
            return

        line_ts = self._extract_line_timestamp(line)

        if "setCompound Tyre:" not in line:
            m = self._pats["platformcore_compound"].search(line)
            if not m or not self._is_player_car(m.group(1)):
                return
            pos = int(m.group(2))
            if (
                line_ts
                and line_ts == self._pending_compound_ts
                and pos in self._pending_compound_updates
            ):
                self._pending_compound_confirmed.add(pos)
                _debug.log(f"[COMPOUND] Player-confirmed tyre {pos} at {line_ts}")
            return

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

        if not line_ts:
            self.context.tyre.set(pos, compound_name)
            _debug.log(
                f"[COMPOUND] Tyre {pos} -> {code} "
                f"(resolved: {self.context.tyre.compound_name})"
            )
            return

        self._pending_compound_ts = line_ts
        self._pending_compound_updates[pos] = compound_name
        _debug.log(
            f"[COMPOUND] Pending tyre {pos} -> {code} at {line_ts} "
            f"(positions={sorted(self._pending_compound_updates)})"
        )

    def _flush_pending_compound_batch(self) -> None:
        if not self._pending_compound_updates:
            self._pending_compound_ts = None
            self._pending_compound_confirmed.clear()
            return

        pending = dict(self._pending_compound_updates)
        confirmed = {
            pos: pending[pos]
            for pos in sorted(self._pending_compound_confirmed)
            if pos in pending
        }
        has_full_batch = set(pending) == {0, 1, 2, 3}
        prelap_window = self.current_session is None or not self.current_session.laps

        if confirmed:
            for pos, compound in confirmed.items():
                self.context.tyre.set(pos, compound)
            _debug.log(
                f"[COMPOUND] Applied player-confirmed positions "
                f"{sorted(confirmed)} -> {self.context.tyre.compound_name}"
            )
        elif has_full_batch and prelap_window:
            self.context.tyre.reset()
            for pos, compound in pending.items():
                self.context.tyre.set(pos, compound)
            _debug.log(
                f"[COMPOUND] Applied pre-lap full set at {self._pending_compound_ts} "
                f"-> {self.context.tyre.compound_name}"
            )
        else:
            _debug.log(
                f"[COMPOUND] Ignored unscoped batch at {self._pending_compound_ts} "
                f"(positions={sorted(pending)} confirmed={sorted(self._pending_compound_confirmed)})"
            )

        self._pending_compound_ts = None
        self._pending_compound_updates.clear()
        self._pending_compound_confirmed.clear()

    def _handle_weather(self, line: str) -> None:
        if "GameModeSelectionWeatherType_" not in line:
            return
        idx = line.find("GameModeSelectionWeatherType_")
        if idx != -1:
            suffix = line[idx + len("GameModeSelectionWeatherType_"):].split()[0]
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

        _debug.log(f"[SETUP] {key}={value!r}")

    def _handle_session_start(self, line: str) -> bool:
        """Parse 'Game Started!' and initialise a fresh SessionData.
        Returns True if a new session was created (caller should short-circuit).
        """
        if "Game Started!" not in line or "GameModeType_" not in line:
            return False
        m = self._pats["game_started"].search(line)
        if not m:
            return False

        self._flush_pending_compound_batch()

        if self.current_session:
            self._finalise_current_session()
        self._session_active_from_logs = True

        raw_type, raw_track_desc, raw_car, raw_weather = (
            m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()
        )
        session_type = SESSION_TYPE_MAP.get(raw_type, raw_type)
        track = self._clean_track_name(raw_track_desc)

        self.context.current_track = track
        self.context.current_car = raw_car
        self.context.weather = raw_weather
        self.context.car_is_hybrid = raw_car in KNOWN_HYBRID_CARS
        
        # Preserve setup values across session reset
        preserved_setup_values = self.context.setup_values.copy()
        self.context.reset_for_new_session()
        self.context.setup_values = preserved_setup_values

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
            _debug.log(f"[SESSION] Applied {len(self.context.setup_values)} setup values to new session")
        
        self._reset_in_progress()
        self._finalise_stints()

        self._sync_shared_session(self.current_session)

        _debug.log(
            f"[SESSION] New: type={session_type} track={track} "
            f"car={raw_car} hybrid={self.context.car_is_hybrid}"
        )
        return True

    def _handle_fuel(self, line: str) -> None:
        # ── Fuel fill on pit exit / session start ─────────────────────────────
        if "FUEL car" in line and (
            ("filled with" in line and "from setup" in line)
            or "setup with" in line
        ):
            m = self._pats["fuel_filled"].search(line)
            if m and self.current_session and self._is_player_car(m.group(1)):
                self.current_session.initial_fuel = float(m.group(2))
                _debug.log(f"[FUEL] Initial fill: {m.group(2)} L")
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
            _debug.log(
                f"[FUEL] Init correction stored: {self.context.fuel_init_correction} L"
            )
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
            _debug.log(
                f"[FUEL] Init correction applied: raw={fuel_delta:.3f} → "
                f"net={net_fuel:.3f} L"
            )
            self.context.fuel_init_correction = 0.0

        # ── Per-lap hybrid spike detection ─────────────────────────────────────
        # Always mark this individual lap as unreliable if net_fuel is spiked.
        if net_fuel > HYBRID_FUEL_THRESHOLD_L:
            self.context.fuel_spike_count += 1
            self._ip.fuel_reliable = False
            _debug.log(
                f"[FUEL] Spike #{self.context.fuel_spike_count}: {net_fuel:.2f} L "
                f"(> {HYBRID_FUEL_THRESHOLD_L} L)"
            )
            # Poison the whole session only after enough repeated spikes.
            if self.context.fuel_spike_count >= HYBRID_SPIKE_SESSION_THRESHOLD:
                self.current_session.fuel_reliable = False
                _debug.log(
                    "[FUEL] Session fuel marked unreliable "
                    f"({self.context.fuel_spike_count} spikes)"
                )

        self._ip.fuel_used = net_fuel
        _debug.log(
            f"[FUEL] Lap fuel: {net_fuel:.3f} L  "
            f"dist: {lap_hundredm}×100 m  "
            f"reliable={self._ip.fuel_reliable}"
        )

    def _handle_track_limits(self, line: str) -> None:
        """Detect real track-limit violations for the player's car.

        Pit-teleport artefacts are identified by a very large positive
        inside_distance value (always 12.52 m in the analysed logs; real
        violations are below ±3.5 m).

        Brief momentary excursions (inside_distance < 2m) are tolerated by
        the game and do not invalidate laps. Only sustained off-track
        cuts (inside_distance >= 2m) are considered lap-invalidating.
        """
        if "Limits: car" not in line or "tyres out changed:" not in line:
            return
        m = self._pats["track_limits"].search(line)
        if not m:
            return

        car_id = m.group(1)
        new_count = int(m.group(2))
        inside_dist = float(m.group(3))

        if not self._is_player_car(car_id):
            return
        if new_count != 4:
            return

        if inside_dist > PIT_TELEPORT_DISTANCE_M:
            _debug.log(
                f"[LIMITS] Pit-teleport artefact ignored "
                f"(inside_dist={inside_dist} m)"
            )
            return

        # Brief momentary excursions with small inside_distance are tolerated
        # by the game and do not invalidate the lap.
        if inside_dist < TRACK_LIMIT_INVALIDATION_THRESHOLD_M:
            _debug.log(
                f"[LIMITS] Brief excursion tolerated (inside_dist={inside_dist} m < "
                f"{TRACK_LIMIT_INVALIDATION_THRESHOLD_M} m threshold)"
            )
            return

        self._ip.has_track_limit_violation = True
        _debug.log(f"[LIMITS] Violation! inside_dist={inside_dist} m")

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
        _debug.log(f"[SPLIT_RACE] S{split_idx + 1}: {time_ms} ms")

    def _handle_splits_practice(self, line: str) -> None:
        if "On Split start" not in line:
            return
        if not self.current_session:
            return
        if self.current_session.session_type in RACE_LIKE:
            return
        if self._ip.is_outlap:
            return
        m = self._pats["practice_split"].search(line)
        if not m:
            return

        split_idx, split_ms = int(m.group(1)), int(m.group(2))
        self._ip.splits[split_idx] = split_ms
        _debug.log(f"[SPLIT_PRACTICE] S{split_idx + 1}: {split_ms} ms")

    def _handle_split_end(self, line: str) -> None:
        if "On Split end with all splits" in line:
            self._ip.split_end_confirmed = True

    def _handle_outlap_signals(self, line: str) -> None:
        """'Outplap split' is the authoritative outlap marker for practice-like
        modes. It is NOT reliable in race-like modes: AC Evo emits one
        "Outplap split" per car on the grid at race countdown (seen 6×
        back-to-back in a 6-car race log), with no car identifier, so accepting
        it in a race would falsely flag the player's first competitive lap as
        an outlap and silently drop it from submission. The first lap of a
        race/qualifying session is always a real timed lap, so we only honor
        this signal in PRACTICE_LIKE sessions.

        'Couldn't create lap from opensplits' means the game also rejected the
        lap; we reset in-progress state so stale flags don't infect the next lap.
        """
        if "Outplap split" in line:
            if (
                self.current_session
                and self.current_session.session_type in PRACTICE_LIKE
            ):
                self._ip.is_outlap = True
                _debug.log("[OUTLAP] Outplap split detected")
            else:
                _debug.log(
                    "[OUTLAP] Outplap split ignored in race-like session "
                    "(grid-countdown broadcast, not a player outlap marker)"
                )
        elif "Couldn't create lap from opensplits" in line:
            _debug.log("[OUTLAP] Couldn't create lap — resetting in-progress")
            self._reset_in_progress()

    def _handle_physics_lap(self, line: str) -> None:
        if "Lap test evOnLapCompleted" not in line:
            return
        m = self._pats["physics_lap"].search(line)
        if m:
            self._ip.physics_lap_num = int(m.group(1))

    def _handle_penalty(self, line: str) -> None:
        if self._pats["penalty"].search(line):
            self._ip.has_penalty = True
            _debug.log("[VALIDITY] Penalty detected")

    def _handle_unexpected_split(self, line: str) -> None:
        if "Unexpected On Split" in line:
            self._ip.has_unexpected_split = True
            _debug.log("[VALIDITY] Unexpected On Split")

    # ── Lap state determination ───────────────────────────────────────────────

    def _determine_lap_state(
        self,
        ip: InProgressLap,
        split_keys: list[int],
        split_times: list[int],
        lap_time_ms: int,
        session_type: str,
    ) -> LapState:
        """Evaluate all validity signals and return the most specific LapState.

        Order of precedence matters: outlap → track limit → penalty → split
        issues → sector consistency.  PUSH (valid) is returned only when all
        checks pass.
        """

        # ── 1. Outlap ──────────────────────────────────────────────────────────
        # Primary: explicit 'Outplap split' marker in log.
        # Fallback: physics lap counter == 1 in a practice session (covers the
        #           case where the game doesn't log 'Outplap split').
        is_practice_outlap = (
            session_type in PRACTICE_LIKE
            and ip.physics_lap_num == 1
        )
        if ip.is_outlap or is_practice_outlap:
            if is_practice_outlap and not ip.is_outlap:
                _debug.log(
                    "[VALIDITY] OUTLAP via physics_lap_num==1 fallback "
                    "(no Outplap split logged)"
                )
            # Clear any track limit violations that occurred during the outlap.
            # Outlaps are not competitive timed laps, so violations don't count.
            if ip.has_track_limit_violation:
                _debug.log(
                    "[VALIDITY] Clearing track limit violation from OUTLAP "
                    "(outlap violations don't invalidate subsequent laps)"
                )
                ip.has_track_limit_violation = False
            return LapState.OUTLAP

        # ── 2. Track limit violation ───────────────────────────────────────────
        if ip.has_track_limit_violation:
            return LapState.INVALID_TRACK_LIMIT

        # ── 3. Penalty ────────────────────────────────────────────────────────
        if ip.has_penalty:
            return LapState.INVALID_PENALTY

        # ── 4. Unexpected split ────────────────────────────────────────────────
        if ip.has_unexpected_split:
            return LapState.INVALID_SPLIT

        # ── 5. Split key guard ────────────────────────────────────────────────
        # Keys must be contiguous from 0 (e.g. [0,1] or [0,1,2]) and we
        # require at least two sectors to avoid validating partial laps.
        if len(split_keys) < 2:
            _debug.log(
                f"[VALIDITY] INVALID_SPLIT: keys={split_keys} "
                "expected at least [0,1]"
            )
            return LapState.INVALID_SPLIT

        expected_keys = list(range(split_keys[-1] + 1))
        if split_keys != expected_keys:
            _debug.log(
                f"[VALIDITY] INVALID_SPLIT: keys={split_keys} "
                f"expected {expected_keys}"
            )
            return LapState.INVALID_SPLIT

        # ── 6. Split-end confirmation ──────────────────────────────────────────
        if not ip.split_end_confirmed:
            # Live tailing can occasionally observe a complete lap payload with
            # split_end missing due to log write timing. In practice-like modes,
            # trust fully populated + consistent sectors as a fallback.
            if (
                session_type in PRACTICE_LIKE
                and abs(sum(split_times) - lap_time_ms) <= SECTOR_SUM_TOLERANCE_MS
            ):
                _debug.log(
                    "[VALIDITY] split-end missing but sectors are "
                    "complete/consistent in practice-like mode"
                )
            else:
                _debug.log("[VALIDITY] INVALID_SPLIT: no split-end confirmation")
                return LapState.INVALID_SPLIT

        # ── 7. Sector consistency guard ────────────────────────────────────────
        sector_sum = sum(split_times)
        if abs(sector_sum - lap_time_ms) > SECTOR_SUM_TOLERANCE_MS:
            _debug.log(
                f"[VALIDITY] INVALID_SECTORS: sum={sector_sum} "
                f"lap={lap_time_ms} "
                f"delta={abs(sector_sum - lap_time_ms)} ms"
            )
            return LapState.INVALID_SECTORS

        return LapState.PUSH

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
        ip = self._ip
        split_keys: list[int] = sorted(ip.splits.keys())
        split_times: list[int] = [ip.splits[key] for key in split_keys]

        # ── Sector extraction ─────────────────────────────────────────────────
        s1: Optional[int] = ip.splits.get(0)
        s2: Optional[int] = ip.splits.get(1)
        s3: Optional[int] = ip.splits.get(2)

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
                    _debug.log(
                        f"[SECTORS] S1 corrupted (raw={s1} ms, "
                        f"sum={sector_sum} > lap={lap_time_ms} by {overshoot} ms)"
                        f" → back-calculated: {s1_calc} ms"
                    )
                    s1 = s1_calc
                    if split_keys and split_keys[0] == 0:
                        split_times[0] = s1
                else:
                    _debug.log(
                        f"[SECTORS] S1 overshoot detected (raw={s1}, "
                        f"sum={sector_sum} > lap={lap_time_ms}) but "
                        f"back-calc non-positive ({s1_calc}); leaving as-is"
                    )

        session_type = self.current_session.session_type

        # ── Lap state ─────────────────────────────────────────────────────────
        lap_state = self._determine_lap_state(
            ip, split_keys, split_times, lap_time_ms, session_type
        )
        is_valid = lap_state == LapState.PUSH

        # ── Sector consistency flag ────────────────────────────────────────────
        sectors_consistent: Optional[bool] = None
        if len(split_times) >= 2:
            sectors_consistent = (
                abs(sum(split_times) - lap_time_ms) <= SECTOR_SUM_TOLERANCE_MS
            )

        # ── Fuel ──────────────────────────────────────────────────────────────
        fuel_used = ip.fuel_used
        fuel_reliable = ip.fuel_reliable and self.current_session.fuel_reliable
        if fuel_used and fuel_used > 0:
            self.current_session.fuel_used_session += fuel_used

        # ── Compound & stint ──────────────────────────────────────────────────
        compound = self.context.tyre.compound_name
        self.current_session.tyre_compound = compound

        # Physics lap number is the only source available in log parser (graphics data not available)
        physics_lap_number = ip.physics_lap_num
        lap_number = physics_lap_number or (len(self.current_session.laps) + 1)

        # Update stint (only for laps that actually ran, including invalid push)
        if lap_state != LapState.OUTLAP:
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

        _debug.log(
            f"[LAP] #{lap_number} phys={physics_lap_number} "
            f"{time_str}  state={lap_state.value}  "
            f"compound={compound}  fuel={fuel_used}  "
            f"consistent={sectors_consistent}  (buffered)"
        )

        self._reset_in_progress()

        # Buffer this lap until the game's authoritative validity arrives.
        # If a previous lap is still pending, that means its validity line
        # never showed up — flush it now with its heuristic state.
        prior_pending = self._pending_lap
        self._pending_lap = completed_lap
        if prior_pending is not None:
            self.current_session.laps.append(prior_pending)
            _debug.log(
                f"[LAP] flushed pending #{prior_pending.lap_number} via "
                f"heuristic (no authoritative validity seen)"
            )
            return prior_pending
        return None

    # ── Authoritative validity (from network broadcast) ──────────────────────

    def _handle_lap_validity(self, line: str) -> Optional[LapData]:
        """Apply the game's authoritative per-lap validity flag.

        AC Evo emits a `[network] [info] Relevant onSplit for Combo …:
        laptime N, valid true|false, …` line ~ms after each `New lap carId`.
        It is the ground truth for whether the lap counts.

        When this matches the currently-pending (just-completed) lap by
        ``laptime``, we override the heuristic state if it disagrees:

        * Heuristic INVALID_SECTORS / INVALID_SPLIT but game says valid →
          upgrade to PUSH (e.g. Spa grid-start S1 inflation).
        * Heuristic PUSH but game says invalid → demote to INVALID_GAME.
        * Other invalid heuristics (track limit, penalty, outlap) are
          retained — they encode the *reason* and remain truthful even when
          the game's binary flag would round to the same boolean.

        Returns the now-finalised lap so the caller can emit it; otherwise
        returns None.
        """
        if "Relevant onSplit for Combo" not in line:
            return None
        m = self._pats["lap_validity"].search(line)
        if not m:
            return None
        if not self.current_session:
            return None
        pending = self._pending_lap
        if pending is None:
            return None

        laptime_ms = int(m.group(1))
        if laptime_ms != pending.lap_time_ms:
            # Mismatch — likely a stale broadcast for a different car.
            return None

        game_valid = m.group(2) == "true"
        prev_state = pending.lap_state
        prev_valid = pending.is_valid

        if game_valid and not prev_valid and prev_state in (
            LapState.INVALID_SECTORS,
            LapState.INVALID_SPLIT,
        ):
            pending.lap_state = LapState.PUSH
            pending.lap_type = LapState.PUSH.value
            pending.is_valid = True
            _debug.log(
                f"[VALIDITY] Game says valid — upgrading "
                f"#{pending.lap_number} {prev_state.value} → PUSH"
            )
        elif (not game_valid) and prev_valid:
            pending.lap_state = LapState.INVALID_GAME
            pending.lap_type = LapState.INVALID_GAME.value
            pending.is_valid = False
            _debug.log(
                f"[VALIDITY] Game says invalid — demoting "
                f"#{pending.lap_number} PUSH → INVALID_GAME"
            )

        self.current_session.laps.append(pending)
        self._pending_lap = None
        _debug.log(
            f"[LAP] flushed pending #{pending.lap_number} via authoritative "
            f"flag (game_valid={game_valid})"
        )
        return pending

    def _flush_pending_lap(self) -> Optional[LapData]:
        """Append any buffered lap to the session with its heuristic state.

        Used at session end / file EOF where no further authoritative
        validity line will arrive. Returns the flushed lap so callers can
        emit it.
        """
        pending = self._pending_lap
        if pending is None or not self.current_session:
            return None
        self._pending_lap = None
        self.current_session.laps.append(pending)
        _debug.log(
            f"[LAP] flushed pending #{pending.lap_number} on session/EOF "
            f"(heuristic state)"
        )
        return pending

    # ── Aborted lap emission ──────────────────────────────────────────────────

    def _maybe_emit_aborted_lap(self) -> Optional[LapData]:
        """Produce an ABORTED LapData if the in-progress lap has meaningful data.

        Called when a session ends unexpectedly (game quit, session change)
        mid-lap. Requires at least one sector to have been recorded — otherwise
        there's nothing worth emitting.
        """
        ip = self._ip
        has_data = (
            ip.splits
            or ip.fuel_used is not None
            or ip.distance_hundredm is not None
        )
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
        _debug.log(
            f"[LAP] ABORTED #{lap_number}  sectors={sorted(ip.splits.keys())}  "
            f"dist={ip.distance_hundredm}"
        )
        return aborted

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def _start_new_session(self, session_type: str, _line: str) -> None:
        """Fallback session creator for edge cases (no 'Game Started!' seen)."""
        self._flush_pending_compound_batch()
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
        _debug.log(f"[SESSION] Fallback session created: type={session_type}")

    def _finalise_current_session(self) -> None:
        if not self.current_session:
            return
        self._flush_pending_compound_batch()
        # Session-end metadata should reflect the latest known tyre state even
        # if no lap was completed after the final pit/setup change.
        self.current_session.tyre_compound = self.context.tyre.compound_name
        # Flush any buffered lap whose authoritative validity never arrived
        # (e.g. game quit immediately after lap completion). Emission is
        # handled by callers that have an event loop; here we only ensure
        # the lap is recorded in `session.laps`.
        self._flush_pending_lap()
        # Emit aborted lap if the session ends mid-lap
        self._maybe_emit_aborted_lap()
        self._finalise_stints()
        self._session_manager.update_from_logs(self.current_session)
        if self.current_session.laps:
            self.sessions.append(self.current_session)
        self.current_session = None
        self._session_active_from_logs = False
        self._reset_in_progress()

    # ── Master line processor ─────────────────────────────────────────────────

    def _process_line(self, line: str) -> Optional[LapData]:
        """Process one raw log line. Returns LapData when a lap completes."""
        line = line.strip()
        if not line:
            return None

        line_ts = self._extract_line_timestamp(line)
        if (
            self._pending_compound_ts
            and line_ts
            and line_ts != self._pending_compound_ts
        ):
            self._flush_pending_compound_batch()

        self._add_to_log_buffer(line)
        self._last_activity_ts = time.time()
        self._update_session_activity_from_line(line)

        # ── Metadata (order-independent, always evaluated) ────────────────────
        self._handle_version(line)
        self._handle_track_name(line)
        self._handle_connect(line)
        self._handle_driver(line)
        self._handle_gamecar_meta(line)
        self._handle_car_teleport(line)
        self._handle_compound(line)
        self._handle_weather(line)

        # ── Session lifecycle ─────────────────────────────────────────────────
        if self._handle_session_start(line):
            return None

        if "END_SESSION car" in line and self.context.car_uuid:
            if self.context.car_uuid in line:
                _debug.log("[SESSION] END_SESSION for player car — finalising")

        # ── Setup values (captured regardless of session state) ─────────────────
        self._handle_setup_group(line)

        if not self.current_session:
            return None

        # ── In-session events ─────────────────────────────────────────────────
        self._handle_fuel(line)
        self._handle_track_limits(line)
        self._handle_splits_race(line)
        self._handle_splits_practice(line)
        self._handle_split_end(line)
        self._handle_outlap_signals(line)
        self._handle_physics_lap(line)
        self._handle_penalty(line)
        self._handle_unexpected_split(line)

        # ── Lap completion ────────────────────────────────────────────────────
        # Two paths can produce an emittable lap on a single line:
        #   * `_handle_lap_complete` builds a fresh lap and may flush a
        #     previously-buffered lap (when no authoritative validity arrived).
        #   * `_handle_lap_validity` finalises the buffered lap with the
        #     game's authoritative valid/invalid flag.
        # At most one fires per line, so returning whichever is non-None is
        # sufficient.
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
            for line in fh:
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
        _debug.start()
        _debug.log(f"follow() starting — log path: {self.log_path}")
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
                for line in fh:
                    try:
                        lap = self._process_line(line)
                        if lap:
                            historical_laps += 1
                    except (RuntimeError, ValueError, TypeError) as exc:
                        _debug.log(f"[ERROR] Historical parse: {exc}")

                _debug.log(
                    f"Historical pass: {historical_laps} lap(s). "
                    f"Session: {self.current_session is not None}"
                )

                # Discard historical laps — only new ones are emitted
                if self.current_session:
                    self.current_session.laps.clear()
                    self.current_session.stints.clear()
                    self._finalise_stints()
                    _debug.log(
                        f"Context: track={self.current_session.track} "
                        f"car={self.current_session.car}"
                    )

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
                        _debug.log(f"[ERROR] on_game_version: {exc}")

                # ── Live tail ──────────────────────────────────────────────────────
                _debug.log("Entering live tail loop …")
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
                        # AC Evo: pause-menu "Restart Session" emits this line
                        # but does NOT emit a fresh "Game Started!", so we
                        # have to drive the buffer reset ourselves.
                        if "request made GameModeRequestRestartSession" in line:
                            await self._emit_session_restart()
                        # AC Evo: pause-menu "Exit to Menu" — different from
                        # restart, the user is leaving the session entirely.
                        elif "request made GameModeRequestExit" in line:
                            await self._emit_game_status(False, trigger="GameModeRequestExit")
                        if "END_SESSION car" in line:
                            if self._line_mentions_player_car(line):
                                await self._emit_game_status(False, trigger="END_SESSION matched")
                            else:
                                _debug.log("[SESSION_END] ignoring END_SESSION for non-player car")
                        if "onSetPlayerCurrentCarCommand: remove car" in line:
                            m = self._pats["remove_car"].search(line)
                            if m and self._is_player_car(m.group(1)):
                                _debug.log(f"[SESSION_END] remove car detected: {m.group(1)}")
                                await self._emit_session_end()

                        try:
                            completed = self._process_line(line)
                        except (RuntimeError, ValueError, TypeError) as exc:
                            _debug.log(f"[ERROR] Live process_line: {exc}")
                            continue

                        if completed:
                            session = self.current_session or SessionData(
                                track="Unknown", car="Unknown"
                            )
                            try:
                                await self._emit_lap(session, completed)
                            except (RuntimeError, asyncio.CancelledError) as exc:
                                _debug.log(f"[ERROR] emit_lap: {exc}")
                        continue

                    # No new data — check for a newer log file (new game session)
                    if self._log_dir is not None:
                        _latest = self._find_latest_log(self._log_dir)
                        if _latest is not None and _latest != self.log_path:
                            self._flush_pending_compound_batch()
                            _debug.log(f"[NEW_LOG] Switching to {_latest.name}")
                            self.context = LogContext()
                            self.current_session = None
                            self._emit_callbacks = True
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
                        _debug.log("[TRUNCATE] Log file reset — restarting context")
                        self.context = LogContext()
                        self.current_session = None
                        self._emit_callbacks = True
                        await self._emit_status("Log file reset — restarting …")
                        fh.seek(0)

                    await asyncio.sleep(poll_interval)

            if not _restart:
                break

        _debug.log("follow() exiting")
        _debug.close()

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def get_current_session(self) -> Optional[SessionData]:
        return self.current_session

    def get_player_id(self) -> Optional[str]:
        return self.context.player_id
