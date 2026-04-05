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


# ─── Main parser ──────────────────────────────────────────────────────────────

class LogParser:
    """Parse ACE game logs and extract structured lap/session data.

    Supports one-shot (`parse_file`) and live-tail (`follow`) modes.
    """

    DEFAULT_LOG_PATH = Path.home() / "Saved Games" / "ACE" / "log.txt"

    def __init__(
        self,
        log_path: Optional[str] = None,
        on_lap_complete: Optional[LapCallback] = None,
        on_status_change: Optional[StatusCallback] = None,
        on_game_status_change: Optional[GameStatusCallback] = None,
        on_user_detected: Optional[UserDetectedCallback] = None,
        on_game_version: Optional[GameVersionCallback] = None,
    ) -> None:
        self.log_path = Path(log_path) if log_path else self.DEFAULT_LOG_PATH
        self.on_lap_complete = on_lap_complete
        self.on_status_change = on_status_change
        self.on_game_status_change = on_game_status_change
        self.on_user_detected = on_user_detected
        self.on_game_version = on_game_version

        self.sessions: list[SessionData] = []
        self.current_session: Optional[SessionData] = None
        self.context = LogContext()

        # In-progress lap accumulator
        self._ip = InProgressLap()

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
                r"(\d+) connected on car ([\w_]+), with new carId ([a-f0-9\-]+)"
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
                r"FUEL car ([a-f0-9\-]+) filled with ([\d.]+) L"
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

            # New lap: timestamp | car_uuid | lap_time_str
            "lap_finish": re.compile(
                r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]"
                r" \[gameplay\] \[info\] New lap carId ([a-f0-9\-]+): ([\d:.]+)"
            ),

            "penalty": re.compile(
                r"UINotificationType_SessionPenalty|\{PENALTY_ADDED_KEY\}"
            ),
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

    def _is_player_car(self, car_uuid: str) -> bool:
        return (
            car_uuid == self.context.car_uuid
            or car_uuid in self.context.player_car_uuids
        )

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
        _debug.log(
            f"[EMIT_LAP] #{lap.lap_number} {lap.lap_time_str} "
            f"state={lap.lap_state.value}"
        )
        if self.on_lap_complete:
            try:
                await self.on_lap_complete(session, lap)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_lap_complete: {exc}")

    async def _emit_game_status(self, is_running: bool) -> None:
        _debug.log(f"[GAME_STATUS] is_running={is_running}")
        if self.on_game_status_change:
            try:
                await self.on_game_status_change(is_running)
            except (RuntimeError, asyncio.CancelledError) as exc:
                _debug.log(f"[ERROR] on_game_status_change: {exc}")

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
        if "connected on car" not in line:
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

        _debug.log(
            f"[SESSION] New: type={session_type} track={track} "
            f"car={raw_car} hybrid={self.context.car_is_hybrid}"
        )
        return True

    def _handle_fuel(self, line: str) -> None:
        # ── Fuel fill on pit exit / session start ─────────────────────────────
        if "FUEL car" in line and "filled with" in line and "from setup" in line:
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
        """'Outplap split' is the authoritative outlap marker.

        'Couldn't create lap from opensplits' means the game also rejected the
        lap; we reset in-progress state so stale flags don't infect the next lap.
        """
        if "Outplap split" in line:
            self._ip.is_outlap = True
            _debug.log("[OUTLAP] Outplap split detected")
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

        # S1 corruption check — race grid start produces a cumulative time in
        # slot 0 rather than the actual sector duration.  Detect by checking
        # if the raw value exceeds the total lap time (which it must, as a
        # cumulative reading from race start will be far larger).
        if s1 is not None and s2 is not None and s3 is not None:
            if s1 > lap_time_ms:
                s1_calc = lap_time_ms - s2 - s3
                _debug.log(
                    f"[SECTORS] S1 corrupted ({s1} ms > lap {lap_time_ms} ms)"
                    f" → back-calculated: {s1_calc} ms"
                )
                s1 = s1_calc if s1_calc > 0 else None
                if split_keys and split_keys[0] == 0 and s1 is not None:
                    split_times[0] = s1

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

        # physics_lap_num is the most authoritative lap numbering source.
        # Fall back to len(laps)+1 if not available (e.g., late-start parsing).
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

        self.current_session.laps.append(completed_lap)
        _debug.log(
            f"[LAP] #{lap_number} phys={physics_lap_number} "
            f"{time_str}  state={lap_state.value}  "
            f"compound={compound}  fuel={fuel_used}  "
            f"consistent={sectors_consistent}"
        )

        self._reset_in_progress()
        return completed_lap

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
        lap_number = (ip.physics_lap_num or len(self.current_session.laps) + 1)

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
        _debug.log(f"[SESSION] Fallback session created: type={session_type}")

    def _finalise_current_session(self) -> None:
        if not self.current_session:
            return
        self._flush_pending_compound_batch()
        # Session-end metadata should reflect the latest known tyre state even
        # if no lap was completed after the final pit/setup change.
        self.current_session.tyre_compound = self.context.tyre.compound_name
        # Emit aborted lap if the session ends mid-lap
        self._maybe_emit_aborted_lap()
        self._finalise_stints()
        if self.current_session.laps:
            self.sessions.append(self.current_session)
        self.current_session = None
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
        return self._handle_lap_complete(line)

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

        while self._running and not self.log_path.exists():
            await self._emit_status(f"Waiting for log file: {self.log_path}")
            await asyncio.sleep(poll_interval)

        if not self._running:
            return

        await self._emit_status("Reading existing log …")

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
                        await self._emit_game_status(True)
                    if "END_SESSION car" in line:
                        if self.context.car_uuid and self.context.car_uuid in line:
                            await self._emit_game_status(False)

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

                # No new data — check for log truncation (game restart)
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
