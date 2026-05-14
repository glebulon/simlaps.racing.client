"""
Telemetry Capture Module

Manages shared memory capture from AC Evo during game sessions.
Based on test_scripts/telemetry/1-capture.py
"""

import asyncio
import ctypes
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from src.core.security import is_game_running, GameProcessStatus
from src.models import SharedSessionManager
from src.utils.structured_logger import log_debug, log_info, log_warning, log_error, log_exception, Component
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Tuple, TextIO

# Windows-specific imports for safe shared memory access
if sys.platform == "win32":
    from ctypes import wintypes

    FILE_MAP_READ = 0x0004

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenFileMappingW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.OpenFileMappingW.restype = wintypes.HANDLE
    kernel32.MapViewOfFile.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.UnmapViewOfFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

REGIONS = {
    # AC Evo renamed the shared-memory objects from the ACC-era
    # "acpmf_*" names to "acevo_pmf_*" (see SharedFileOut.h /
    # ACE_SharedFileOut_Documentation_v1.md). Keep the new name primary.
    #
    # Sizes are generous bounds based on the documented AC Evo struct
    # layouts so we can capture the full mapping for reverse engineering
    # even before a typed decoder exists for graphics/static. If the
    # actual mapping is smaller, OpenFileMappingW + MapViewOfFile will
    # surface that and the region will simply fail to open — capture
    # continues with whichever regions did connect.
    "physics":  ("acevo_pmf_physics",  1024),
    "graphics": ("acevo_pmf_graphics", 4096),
    "static":   ("acevo_pmf_static",   2048),
}


@dataclass
class FrameData:
    """Single telemetry frame data.

    ``physics`` is the only region with a typed decoder today; ``graphics``
    and ``static`` are captured as raw bytes (hex strings) so a future
    decoder can pull them out of the JSONL without re-running the game.
    The ``*_raw`` fields hold the full hex blob; the decoded ``graphics``
    / ``static`` dicts stay empty until a typed decoder is wired in.
    """
    timestamp: str
    frame_number: int
    physics: Dict[str, Any]
    physics_raw: Optional[str] = None
    graphics: Dict[str, Any] = field(default_factory=dict)
    graphics_raw: Optional[str] = None
    static: Dict[str, Any] = field(default_factory=dict)
    static_raw: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaptureMetadata:
    """Metadata about the capture session."""
    captured_at: str
    hz: float
    regions_found: List[str]
    region_names: Dict[str, str]
    region_sizes: Dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


class RegionReader:
    """Reads a single shared memory region."""

    def __init__(self, name: str, size: int, diag_file: Optional[TextIO] = None):
        self.name = name
        self.size = size
        self._handle = None
        self._view = None
        self._path_used = None
        self._diag_file = diag_file

    def _log(self, msg: str):
        """Log to both console and diagnostic file via structured logger."""
        log_debug(Component.TELEMETRY, msg)
        if self._diag_file:
            try:
                self._diag_file.write(f"{datetime.now().isoformat()} {msg}\n")
                self._diag_file.flush()
            except (OSError, IOError):
                # Expected: file closed during shutdown
                pass

    def open(self) -> bool:
        """Open shared memory region for reading only."""
        if sys.platform != "win32":
            return False

        # Get current Windows session ID for diagnostics
        try:
            import subprocess
            session_result = subprocess.run(
                ['query', 'session'],
                capture_output=True,
                text=True,
                timeout=1,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            current_session = session_result.stdout
            self._log(f"[TELEMETRY] Current Windows sessions:\n{current_session}")
        except Exception as e:
            self._log(f"[TELEMETRY] Could not query session info: {e}")

        # Only Win32-style names are valid for OpenFileMappingW.
        # Prior versions also tried NT Object Manager paths like
        # "\Sessions\N\BaseNamedObjects\<name>" — those always fail with
        # ERROR_BAD_PATHNAME (161) and are pure log noise, so they are gone.
        # AC Evo creates the mapping in the caller's session (Local\), we
        # also try the bare name (same namespace via default resolution) and
        # fall back to Global\ in case the game ever publishes it there.
        candidates = [
            f"Local\\{self.name}",
            self.name,
            f"Global\\{self.name}",
        ]

        self._log(f"[TELEMETRY] Trying to open {self.name}, candidates: {candidates}")
        seen = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            try:
                # Open existing file mapping for read-only access
                handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, path)
                if handle and handle != 0:
                    # Map view of file
                    view = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self.size)
                    if view and view != 0:
                        self._handle = handle
                        self._view = view
                        self._path_used = path
                        self._log(f"[TELEMETRY] SUCCESS: Opened {self.name} at path: {path}")
                        return True
                    else:
                        # Get detailed error for MapViewOfFile
                        error_code = ctypes.get_last_error()
                        kernel32.CloseHandle(handle)
                        self._log(f"[TELEMETRY] FAILED: MapViewOfFile failed for {self.name} at {path} (error code: {error_code})")
                else:
                    # Get detailed Windows error code
                    error_code = ctypes.get_last_error()
                    self._log(f"[TELEMETRY] FAILED: OpenFileMappingW failed for {self.name} at {path} (error code: {error_code}: {self._get_error_message(error_code)})")
            except Exception as e:
                self._log(f"[TELEMETRY] EXCEPTION: Error opening {self.name} at {path}: {e}")
                # Silently continue - game might still be initializing
                continue
        self._log(f"[TELEMETRY] FAILED: Could not open {self.name} after trying {len(candidates)} paths")
        return False

    def _get_error_message(self, error_code: int) -> str:
        """Get Windows error message from error code."""
        try:
            # Common error codes
            error_messages = {
                2: "ERROR_FILE_NOT_FOUND - The system cannot find the file specified",
                5: "ERROR_ACCESS_DENIED - Access is denied",
                6: "ERROR_INVALID_HANDLE - The handle is invalid",
                87: "ERROR_INVALID_PARAMETER - The parameter is incorrect",
                1314: "ERROR_PRIVILEGE_NOT_HELD - A required privilege is not held by the client",
            }
            return error_messages.get(error_code, f"Unknown error {error_code}")
        except Exception:
            return "Unknown"

    def read_raw(self) -> bytes:
        if not self._view:
            raise RuntimeError(f"Region not open: {self.name}")

        return ctypes.string_at(self._view, self.size)

    def close(self):
        if self._view:
            try:
                kernel32.UnmapViewOfFile(self._view)
            except Exception:
                pass
            self._view = None
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


class TelemetryCapture:
    """Manages telemetry capture during game sessions."""

    # Timeout after 5 seconds without valid frame data (game crash/quit detection)
    HEARTBEAT_TIMEOUT_SECONDS = 5.0
    # Timeout after 3 seconds with all regions disconnected
    DISCONNECT_TIMEOUT_SECONDS = 3.0
    # Timeout after 120 seconds of zero speed (race exit to menu detection).
    # 120s covers pit stops and formation laps without triggering prematurely.
    # Once lap boundaries are recorded we skip idle timeout entirely and rely
    # on the "remove car" log signal as the authoritative session-end trigger.
    IDLE_TIMEOUT_SECONDS = 120.0

    def __init__(
        self,
        hz: float = 20.0,
        output_dir: Optional[str] = None,
        debug_logs: bool = False,
        session_manager: Optional[SharedSessionManager] = None,
    ):
        # ``debug_logs`` gates three on-disk artefacts that are only useful
        # for reverse-engineering / capture-loop debugging:
        #   * ``telemetry_diagnostics_*.log`` (per-frame SHM open/read trace)
        #   * ``capture_*.jsonl``             (compat-format frame dump)
        #   * ``raw_dump_*.jsonl``            (raw SHM bytes per frame)
        # The HTML summary, AI prompt, and analyzer outputs are produced
        # regardless. Default is False so end-users don't accumulate
        # multi-MB files every session.
        self._debug_logs = debug_logs
        self._hz = hz
        self._frames: List[FrameData] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._readers: Dict[str, RegionReader] = {}
        self._interval = 1.0 / max(hz, 1.0)
        self._metadata: Optional[CaptureMetadata] = None
        self._session_start_time: Optional[datetime] = None
        self._region_paths: Dict[str, str] = {}
        self._last_valid_frame_time: Optional[float] = None
        self._stop_reason: Optional[str] = None
        self._on_stop_callback: Optional[Callable[[str], None]] = None
        self._output_dir = output_dir or get_default_output_dir()
        self._all_disconnected_since: Optional[float] = None
        self._output_prefix: Optional[str] = None
        self._idle_since: Optional[float] = None
        self._lap_boundaries: List[Tuple[int, Optional[float], Optional[int]]] = []  # (frame_idx, lap_time_ms, lap_number)
        self._diag_file: Optional[TextIO] = None
        self._session_manager = session_manager or SharedSessionManager()

    def is_capturing(self) -> bool:
        """Check if currently capturing."""
        return self._running

    def get_stop_reason(self) -> Optional[str]:
        """Get the reason why capture stopped (None if still running)."""
        return self._stop_reason

    def get_output_prefix(self) -> Optional[str]:
        """Get the current session output prefix."""
        return self._output_prefix

    def set_on_stop_callback(self, callback: Optional[Callable[[str], None]]):
        """Set callback to be called when capture stops.

        Args:
            callback: Function receiving stop reason string
        """
        self._on_stop_callback = callback

    def get_frame_count(self) -> int:
        """Get number of captured frames."""
        return len(self._frames)

    def record_lap_boundary(
        self,
        lap_time_ms: Optional[float] = None,
        lap_number: Optional[int] = None,
    ) -> None:
        """Record the current frame index as a lap boundary.

        Called by the app when the game reports a lap completion so the
        analyzer can use authoritative lap boundaries instead of guessing
        from normalizedCarPosition.  Fuel is owned exclusively by the log
        parser (which has spike detection); telemetry capture does not
        duplicate that calculation.

        Args:
            lap_time_ms: The lap time in milliseconds from the game log
            lap_number: The game-reported completed lap number
        """
        frame_idx = max(0, len(self._frames) - 1)
        self._lap_boundaries.append((frame_idx, lap_time_ms, lap_number))
        log_info(Component.TELEMETRY, "Lap boundary recorded",
                 frame=frame_idx, lap_time_ms=lap_time_ms, lap_number=lap_number)

    def get_lap_boundaries(self) -> List[Tuple[int, Optional[float], Optional[int]]]:
        """Return game-reported lap boundaries (frame_idx, lap_time_ms, lap_number)."""
        return self._lap_boundaries.copy()

    def save_raw_dump(self, output_path: str) -> bool:
        """Save raw hex dump of all captured frames to disk for reverse-engineering.

        Args:
            output_path: Path to save the JSONL file

        Returns:
            True if successful, False otherwise
        """
        try:
            import json
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                for frame in self._frames:
                    dump_entry = {
                        "timestamp": frame.timestamp,
                        "frame_number": frame.frame_number,
                        "physics_raw": frame.physics_raw,
                        "graphics_raw": frame.graphics_raw,
                        "static_raw": frame.static_raw,
                    }
                    f.write(json.dumps(dump_entry) + "\n")

            log_debug(Component.TELEMETRY, "Saved raw dump", path=output_path, frames=len(self._frames))
            return True
        except Exception as e:
            log_exception(Component.TELEMETRY, "Error saving raw dump", e)
            return False

    def _build_compat_meta_record(self) -> Dict[str, Any]:
        """Build JSONL metadata matching the standalone capture format."""
        return {
            "_record_type": "meta",
            "_captured_at": (self._session_start_time or datetime.now(timezone.utc)).isoformat(),
            "_output_prefix": self._output_prefix,
            "_hz": self._hz,
            "_regions_known": list(REGIONS.keys()),
            "_regions_found": list(self._readers.keys()) if self._readers else (list(self._metadata.regions_found) if self._metadata else []),
            "_region_names": {key: REGIONS[key][0] for key in REGIONS},
            "_region_paths": self._region_paths.copy(),
            "_region_sizes": {key: size for key, (_, size) in REGIONS.items()},
            "_payload_encoding": "json",
            "_payload_type": "decoded_region_data",
        }

    def _build_compat_frame_record(self, frame: FrameData) -> Dict[str, Any]:
        """Build a standalone-script-compatible frame record.

        Physics is decoded into a typed dict; graphics and static are
        captured as raw hex blobs (no typed decoder yet) so they can be
        reverse-engineered offline against the AC Evo struct documentation
        without re-running the game.
        """
        regions: Dict[str, Any] = {}

        # ── Physics: decoded payload + truncated raw hex preview
        payload = frame.physics or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        region_payload: Dict[str, Any] = {"size": REGIONS["physics"][1], **payload}
        if frame.physics_raw:
            region_payload.setdefault("raw_hex_start", frame.physics_raw[:200])
        regions["physics"] = region_payload

        # ── Graphics: decoded payload + full raw hex (kept for offline
        # re-analysis even after the decoder lands, since it lets us add
        # more fields later without re-running the game).
        # Static: raw bytes only until a typed decoder lands.
        for name in ("graphics", "static"):
            raw_hex = getattr(frame, f"{name}_raw", None)
            if not raw_hex:
                continue
            decoded = getattr(frame, name) or {}
            if not isinstance(decoded, dict):
                decoded = {"value": decoded}
            # ``_decoder`` is set by the decoder itself (e.g.
            # ``ac_evo_graphics``); only fall back to ``raw_only`` if
            # nothing decoded the region.
            region_record: Dict[str, Any] = {
                "size": REGIONS[name][1],
                "raw_hex": raw_hex,
                **decoded,
            }
            region_record.setdefault("_decoder", "raw_only")
            regions[name] = region_record

        return {
            "_record_type": "frame",
            "_ts": frame.timestamp,
            "_frame": frame.frame_number + 1,
            "_output_prefix": self._output_prefix,
            "regions": regions,
        }

    def _make_output_prefix(self) -> str:
        """Create a stable per-session file prefix."""
        return datetime.now().strftime("%m-%d-%H-%M-%S")

    def get_frames(self) -> List[FrameData]:
        """Get captured frames."""
        return self._frames.copy()

    def get_metadata(self) -> Optional[CaptureMetadata]:
        """Get capture metadata."""
        return self._metadata

    def _close_readers(self):
        """Close all active shared-memory readers."""
        for reader in self._readers.values():
            reader.close()
        self._readers = {}

    def _should_notify_stop_callback(self) -> bool:
        """Only auto-notify for unexpected/internal stops."""
        return self._stop_reason not in {
            None,
            "manual",
            "session_end",
            "disabled",
            "app_close",
        }

    def _connect_regions(self) -> Dict[str, RegionReader]:
        """Connect to all shared memory regions with single attempt."""
        readers = {}
        for key, (region_name, size) in REGIONS.items():
            if key in readers:
                continue
            reader = RegionReader(region_name, size, self._diag_file)
            if reader.open():
                readers[key] = reader
                self._region_paths[key] = reader._path_used or ""
                log_debug(Component.TELEMETRY, "Connected to region", key=key, region=region_name)
            else:
                log_debug(Component.TELEMETRY, "Region not found", key=key, region=region_name)
        return readers

    def _reconnect_missing(self, readers: Dict[str, RegionReader]):
        """Try to reconnect to missing regions."""
        for key, (region_name, size) in REGIONS.items():
            if key in readers:
                continue
            reader = RegionReader(region_name, size, self._diag_file)
            if reader.open():
                readers[key] = reader
                self._region_paths[key] = reader._path_used or ""
                log_debug(Component.TELEMETRY, "Reconnected to region", key=key, region=region_name)

    def _capture_frame(self, frame_num: int) -> Optional[FrameData]:
        """Capture a single frame from shared memory."""
        from .telemetry_decoder import decode_physics, decode_graphics, decode_static

        frame = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "frame_number": frame_num,
            "physics": {},
            "physics_raw": None,
            "graphics": {},
            "graphics_raw": None,
            "static": {},
            "static_raw": None,
        }

        disconnected = []
        for key, reader in list(self._readers.items()):
            try:
                raw = reader.read_raw()
                if len(raw) != reader.size:
                    # Incomplete read - might be corrupted
                    log_debug(Component.TELEMETRY, "Incomplete read", key=key, got=len(raw), expected=reader.size)
                    disconnected.append(key)
                    continue
            except Exception as e:
                log_debug(Component.TELEMETRY, "Error reading region", key=key, error=str(e))
                disconnected.append(key)
                continue

            # Save raw bytes for later reverse-engineering
            frame[f"{key}_raw"] = raw.hex()

            try:
                if key == "physics":
                    frame["physics"] = decode_physics(raw)
                elif key == "graphics":
                    frame["graphics"] = decode_graphics(raw)
                elif key == "static":
                    frame["static"] = decode_static(raw)
            except Exception as e:
                log_debug(Component.TELEMETRY, "Error decoding region", key=key, error=str(e))
                # Don't disconnect for decode errors - might be temporary corruption
                frame[key] = {"error": str(e)}

        for key in disconnected:
            try:
                self._readers[key].close()
            except Exception:
                pass
            self._readers.pop(key, None)

        # Debug: Log which regions have valid data
        if frame_num == 0:
            data = frame.get("physics", {})
            if data and not data.get("error"):
                log_debug(Component.TELEMETRY, "First frame data: physics region valid")

        graphics_data = frame.get("graphics") or {}
        if isinstance(graphics_data, dict) and not graphics_data.get("error"):
            self._session_manager.update_from_graphics_shm(graphics_data)

        static_data = frame.get("static") or {}
        if isinstance(static_data, dict) and not static_data.get("error"):
            self._session_manager.update_from_static_shm(static_data)

        physics_data = frame.get("physics") or {}
        if isinstance(physics_data, dict) and not physics_data.get("error"):
            self._session_manager.update_from_physics_shm(physics_data)
        
        return FrameData(**frame)

    async def start_capture(self) -> bool:
        """Start capturing telemetry frames.

        Returns:
            True if capture started successfully, False otherwise
        """
        if self._running:
            return True

        log_info(Component.TELEMETRY, "Starting telemetry capture")
        log_info(Component.TELEMETRY, "Waiting for game to start and create shared memory regions")
        
        # Open diagnostic log file (only when debug_logs setting is enabled)
        if self._debug_logs:
            try:
                os.makedirs(self._output_dir, exist_ok=True)
                diag_path = os.path.join(self._output_dir, f"telemetry_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
                self._diag_file = open(diag_path, "w", encoding="utf-8")
                log_info(Component.TELEMETRY, "Diagnostic log opened", path=diag_path)
            except Exception as e:
                log_error(Component.TELEMETRY, "Could not open diagnostic log", error=str(e))
        else:
            self._diag_file = None
        
        # Try to connect to regions, but don't fail if game hasn't started yet
        # The capture loop will continuously retry
        self._readers = self._connect_regions()
        
        if self._readers:
            log_info(Component.TELEMETRY, "Found regions", count=len(self._readers), regions=list(self._readers.keys()))
        else:
            log_info(Component.TELEMETRY, "Game not running yet - will retry in capture loop")

        self._running = True
        self._frames = []
        self._lap_boundaries = []
        self._metadata = None
        self._session_start_time = datetime.now(timezone.utc)
        self._output_prefix = self._make_output_prefix()
        self._last_valid_frame_time = None
        self._stop_reason = None
        self._all_disconnected_since = None
        self._idle_since = None

        # Start the capture loop task
        self._task = asyncio.create_task(self._capture_loop_wrapper())
        
        # Add exception handler to catch task cancellation/crashes
        def task_done_callback(task):
            if task.cancelled():
                return

            e = task.exception()
            if e is not None:
                import traceback
                log_error(Component.TELEMETRY, "Capture task exception", error=str(e))
                traceback.print_exception(type(e), e, e.__traceback__)
                self._stop_reason = f"task_exception: {e}"
                self._running = False
        
        self._task.add_done_callback(task_done_callback)

        return True

    async def _capture_loop_wrapper(self):
        """Wrapper around capture loop to catch and log any unhandled exceptions."""
        try:
            await self._capture_loop()
        except Exception as e:
            log_error(Component.TELEMETRY, "Unhandled capture exception", error=str(e))
            log_debug(Component.TELEMETRY, "Traceback", traceback=traceback.format_exc())
            self._stop_reason = f"unhandled_exception: {e}"
            self._running = False
            self._close_readers()
            
            # Try to save any frames we captured before the crash. These
            # are debug artefacts only - gated on the same setting as the
            # normal-path dumps so a disabled telemetry-debug-logs toggle
            # is honored even on the crash branch.
            if self._frames and self._debug_logs:
                prefix = self._output_prefix or self._make_output_prefix()
                compat_dump_path = os.path.join(self._output_dir, f"crash_capture_{prefix}.jsonl")
                raw_dump_path = os.path.join(self._output_dir, f"crash_dump_{prefix}.jsonl")
                self.export_to_jsonl(compat_dump_path)
                self.save_raw_dump(raw_dump_path)
                log_info(Component.TELEMETRY, "Emergency dump saved", path=raw_dump_path)

    async def _capture_loop(self):
        """Main capture loop with continuous retry for game startup."""
        frame_num = 0
        next_deadline = time.perf_counter()
        next_reconnect = time.perf_counter()
        last_disconnect_check = time.perf_counter()
        first_connection_logged = False

        # Metadata will be created once we have our first connection
        metadata_created = False

        try:
            while self._running:
                now_mono = time.perf_counter()

                # Continuously try to connect to missing regions
                if now_mono >= next_reconnect:
                    had_readers = bool(self._readers)
                    self._reconnect_missing(self._readers)

                    # Log when we first connect to the game
                    if not had_readers and self._readers and not first_connection_logged:
                        log_info(Component.TELEMETRY, "Connected to game", regions=len(self._readers))
                        first_connection_logged = True

                        # Create metadata now that we have a connection
                        if not metadata_created:
                            self._metadata = CaptureMetadata(
                                captured_at=datetime.now(timezone.utc).isoformat(),
                                hz=self._hz,
                                regions_found=list(self._readers.keys()),
                                region_names={k: REGIONS[k][0] for k in self._readers},
                                region_sizes={k: v.size for k, v in self._readers.items()},
                            )
                            metadata_created = True

                    next_reconnect = now_mono + 0.5  # Retry every 500ms

                # If no readers yet, just wait for game to start
                if not self._readers:
                    await asyncio.sleep(0.1)
                    continue

                # Check for heartbeat timeout (no valid data for too long)
                if self._last_valid_frame_time is not None:
                    time_since_last_frame = now_mono - self._last_valid_frame_time
                    if time_since_last_frame > self.HEARTBEAT_TIMEOUT_SECONDS:
                        log_warning(Component.TELEMETRY, "Heartbeat timeout", timeout=f"{time_since_last_frame:.1f}s")
                        self._stop_reason = f"heartbeat_timeout ({time_since_last_frame:.1f}s)"
                        self._running = False
                        break

                # Check for complete disconnection timeout
                if now_mono - last_disconnect_check > 1.0:  # Check every second
                    last_disconnect_check = now_mono
                    if not self._readers:
                        # All regions disconnected - check how long
                        if hasattr(self, '_all_disconnected_since'):
                            if now_mono - self._all_disconnected_since > self.DISCONNECT_TIMEOUT_SECONDS:
                                log_warning(Component.TELEMETRY, "Disconnect timeout", timeout=f"{self.DISCONNECT_TIMEOUT_SECONDS:.1f}s")
                                self._stop_reason = f"disconnect_timeout ({self.DISCONNECT_TIMEOUT_SECONDS:.1f}s)"
                                self._running = False
                                break
                        else:
                            self._all_disconnected_since = now_mono
                    else:
                        self._all_disconnected_since = None

                # Check if game process is still running (treat UNKNOWN as NOT_RUNNING for safety)
                if is_game_running() != GameProcessStatus.RUNNING:
                    log_warning(Component.TELEMETRY, "Game process no longer running or detection uncertain - stopping capture")
                    self._stop_reason = "game_not_running"
                    self._running = False
                    break

                frame = self._capture_frame(frame_num)
                if frame:
                    # Accept all frames regardless of content - capture everything
                    # The decoder may return fallback data or empty dicts if structure doesn't match
                    self._last_valid_frame_time = now_mono
                    self._frames.append(frame)
                    frame_num += 1
                    # Reset disconnect timer on valid frame
                    self._all_disconnected_since = None
                    
                    # Check for idle state (speed = 0 for extended period = race exit to menu).
                    # Skip this check once laps are being recorded — the "remove car" log
                    # signal is the authoritative session-end trigger for active races.
                    physics = frame.physics if frame.physics else {}
                    speed_kmh = physics.get("speed_kmh", 0)
                    if speed_kmh < 1.0:  # Consider speed < 1 km/h as idle
                        if not self._lap_boundaries:  # Only idle-timeout before first lap
                            if self._idle_since is None:
                                self._idle_since = now_mono
                            elif now_mono - self._idle_since > self.IDLE_TIMEOUT_SECONDS:
                                log_warning(Component.TELEMETRY, "Idle timeout", timeout=f"{self.IDLE_TIMEOUT_SECONDS:.1f}s")
                                self._stop_reason = f"idle_timeout ({self.IDLE_TIMEOUT_SECONDS:.1f}s)"
                                self._running = False
                                break
                        else:
                            self._idle_since = None  # Laps recorded — ignore idle
                    else:
                        self._idle_since = None  # Reset idle timer when moving
                    
                    # Debug: log first frame
                    if frame_num == 1:
                        log_info(Component.TELEMETRY, "First frame captured - telemetry active")

                if frame_num % int(self._hz * 5) == 0 and frame_num > 0:
                    if frame_num % int(self._hz * 30) == 0:  # Log every 30 seconds at 10Hz
                        log_info(Component.TELEMETRY, "Capture progress", frames=frame_num)

                next_deadline += self._interval
                sleep_for = next_deadline - time.perf_counter()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    next_deadline = time.perf_counter()
        except Exception as e:
            import traceback
            log_error(Component.TELEMETRY, "Capture loop error", error=str(e))
            traceback.print_exc()
            self._stop_reason = f"capture_error: {e}"
            self._running = False

        # Loop exited - perform cleanup
        log_info(Component.TELEMETRY, "Capture loop ended", reason=self._stop_reason or 'manual_stop')
        self._running = False

        self._close_readers()
        
        # Close diagnostic log
        if self._diag_file:
            try:
                self._diag_file.close()
            except Exception:
                pass
            self._diag_file = None

        # Save raw dump for reverse-engineering if we captured frames.
        # Gated behind the telemetry-debug-logs setting because in normal
        # use these files are large and not surfaced in the UI.
        if self._frames and self._debug_logs:
            prefix = self._output_prefix or self._make_output_prefix()
            compat_dump_path = os.path.join(self._output_dir, f"capture_{prefix}.jsonl")
            raw_dump_path = os.path.join(self._output_dir, f"raw_dump_{prefix}.jsonl")
            self.export_to_jsonl(compat_dump_path)
            self.save_raw_dump(raw_dump_path)

        # Notify callback if set
        if self._on_stop_callback and self._should_notify_stop_callback():
            try:
                result = self._on_stop_callback(self._stop_reason or "manual_stop")
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                log_error(Component.TELEMETRY, "Error in stop callback", error=str(e))

    async def stop_capture(self, reason: str = "manual") -> List[FrameData]:
        """Stop capturing and return captured frames.

        Args:
            reason: Reason for stopping (for logging/debugging)

        Returns:
            List of captured frames
        """
        if not self._running:
            return self._frames.copy()

        log_info(Component.TELEMETRY, "Capture stopped", reason=reason, frames=len(self._frames))
        self._stop_reason = reason
        self._running = False

        # Wait for the capture loop task to finish if it exists
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except asyncio.TimeoutError:
                log_debug(Component.TELEMETRY, "Capture loop task did not finish in time, cancelling")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            finally:
                self._task = None

        self._close_readers()
        
        # Close diagnostic log
        if self._diag_file:
            try:
                self._diag_file.close()
            except Exception:
                pass
            self._diag_file = None

        return self._frames.copy()

    def export_to_jsonl(self, path: str) -> bool:
        """Export captured frames to JSONL file for debugging.

        Args:
            path: Output file path

        Returns:
            True if export successful
        """
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                meta_dict = self._build_compat_meta_record()
                if self._metadata:
                    meta_dict["capture_metadata"] = self._metadata.to_dict()
                f.write(json.dumps(meta_dict) + "\n")

                for frame in self._frames:
                    frame_dict = self._build_compat_frame_record(frame)
                    f.write(json.dumps(frame_dict) + "\n")

            log_debug(Component.TELEMETRY, "Exported frames", path=path, frames=len(self._frames))
            return True
        except Exception as e:
            log_error(Component.TELEMETRY, "Export failed", error=str(e))
            return False

    def clear(self):
        """Clear captured frames to free memory."""
        self._frames = []
        self._metadata = None


def get_default_output_dir() -> str:
    """Get the default telemetry output directory."""
    if sys.platform == "win32":
        base = os.environ.get("USERPROFILE", str(Path.home()))
        return os.path.join(base, "Documents", "SimLaps", "Telemetry")
    else:
        return str(Path.home() / "SimLaps" / "Telemetry")
