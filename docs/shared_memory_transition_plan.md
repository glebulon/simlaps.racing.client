# sim-laps-client: Shared Memory Transition Plan

> **Status Note (April 2026):** This document describes a planned transition to full shared memory telemetry. The current implementation has telemetry capture and analysis working with **physics-only** data (graphics/static not supported due to format incompatibility). Lap detection uses a hybrid approach: game-reported log boundaries when available, falling back to telemetry-based physics analysis. This document remains as reference for future graphics SHM integration.

Replace regex-based log parsing as the primary lap detection mechanism with real-time shared memory (SHM) reading, while retaining minimal log parsing for session-level metadata that SHM cannot provide. The result is a hybrid system that is faster, more reliable, and far easier to maintain.

**Core principle:** SHM owns lap detection. Logs own identity and session context. The two sources are merged in a thin coordination layer.

---

## Current State

### What Works

- Full session metadata: track, car, weather, player identity
- Detailed lap invalidation reasons (track limits, penalties, sector splits)
- Outlap detection via `Outplap split` log markers
- Stint tracking across tyre changes
- Setup notes capture

### Pain Points

- Complex, brittle regex patterns that break on game updates
- Lap detection is delayed — must wait for log file flush (1–5s latency)
- File I/O overhead on every poll cycle
- Fragile multi-line event parsing
- Hybrid car fuel spike handling is a recurring source of edge cases

---

## Target State: Hybrid Architecture

SHM provides the real-time signal. Logs provide the session envelope. Neither source alone is complete.

### Shared Memory (Primary: Lap Detection + Telemetry)

| Field | Struct | Reliability | Notes |
|-------|--------|-------------|-------|
| `completedLaps` | Graphics | ✅ High | Primary lap completion signal |
| `iLastTime` | Graphics | ✅ High | Last lap time in ms |
| `lastSectorTime` | Graphics | ✅ High | Most recent sector time in ms |
| `currentSectorIndex` | Graphics | ✅ High | 0, 1, 2 → S1, S2, S3 |
| `isValidLap` | Graphics | ⚠️ Verify early | Binary only — see note below |
| `iBestTime` | Graphics | ✅ High | Session best in ms |
| `tyreCompound` | Graphics | ✅ High | String: "Dry", "Wet", etc. |
| `currentTyreSet` | Graphics | ✅ High | Tyre set number |
| `rainTyres` | Graphics | ✅ High | Boolean flag |
| `usedFuel` | Graphics | ✅ High | Fuel consumed this lap |
| `fuel` | Physics | ✅ High | Absolute fuel level (liters) |
| `isInPit` / `isInPitLane` | Graphics | ✅ High | Pit state flags |

> **`isValidLap` caveat:** Prior forensic work on the ACE SHM struct revealed garbage values in several fields. Treat this field as unverified until Phase 1 testing confirms it populates correctly for all invalidation scenarios (track limits, penalties, pit exit). Do not mark it ✅ without a data point.

### Variable Sector Counts

Not all tracks have 3 sectors. Fuji has 2 sectors; Spa has 3. The lap detection logic must be sector-agnostic.

| Track | Sectors |
|-------|---------|
| Fuji | 2 (S1, S2) |
| Most others | 3 (S1, S2, S3) |

**Implications:**
- `currentSectorIndex` range is track-dependent (0-1 or 0-2)
- Sector consistency check must sum however many sectors exist
- Outlap detection heuristics need per-track, per-sector thresholds
- Lap record structure must hold variable-length sector list

**Detection strategy:** Capture the max `currentSectorIndex` observed in the first lap of a session. This becomes the sector count for all subsequent laps in that session.

### Logs (Secondary: Session Metadata Only)

Log parsing is reduced to a single pass at session open. No lap detection, no fuel parsing, no sector tracking.

| Field | Pattern | Frequency |
|-------|---------|-----------|
| Steam ID | `(\d+) connected on car` | Once per session |
| Player name | `Driver (.+) on car` | Once per session |
| Track name | `TRACK NAME (.+)` | Once per session |
| Car model | `Game Started!.*\| ([\w_]+) \|` | Once per session |
| Session type | `GameModeType_([A-Z_]+)` | Once per session |
| Weather | `GameModeSelectionWeatherType_(\w+)` | Once per session |
| Setup notes | `KS-SETUP-GROUP (.+)` | Sporadic |

---

## Gap Analysis

| Requirement | SHM | Logs | Strategy |
|-------------|-----|------|----------|
| Lap time | ✅ | ✅ | SHM (real-time, no flush latency) |
| Sector times | ✅ | ✅ | SHM |
| Lap validity flag | ⚠️ Binary | ✅ Detailed | SHM for flag; optional log enrichment for reason |
| Outlap detection | ❌ | ✅ | Heuristics in logic layer — see Phase 3 |
| Player identity | ❌ | ✅ | Keep log parsing |
| Track / car / weather | ❌ | ✅ | Keep log parsing |
| Tyre compound + set | ✅ | Partial | SHM (more complete) |
| Rain tyres flag | ✅ | ❌ | SHM only |
| Fuel consumption | ✅ | ✅ | SHM (more accurate; eliminates fuel spike workaround) |
| Stint tracking | ❌ | ✅ | Logic layer: watch `tyreCompound` + `currentTyreSet` changes |

---

## Architecture

### `SharedMemoryReader`

Polls both SHM structs at a configurable rate. Exposes a simple read interface; does not interpret data.

```python
class SharedMemoryReader:
    """
    Reads ACE Graphics and Physics shared memory structs.
    Thread-safe; caller is responsible for poll timing.
    """

    def __init__(self, poll_hz: float = 10.0):
        self._graphics_shm = mmap.mmap(-1, ctypes.sizeof(ACEGraphics),
                                        "Local\\acpmf_graphics")
        self._physics_shm  = mmap.mmap(-1, ctypes.sizeof(ACEPhysics),
                                        "Local\\acpmf_physics")
        self.poll_interval = 1.0 / poll_hz

    def read(self) -> tuple[ACEGraphics, ACEPhysics]:
        """Snapshot both structs atomically as possible at 10 Hz."""
        ...

    def is_connected(self) -> bool:
        """True if both SHM handles are valid and status == LIVE."""
        ...
```

### `SessionMetadataParser` (replaces `LogParser`)

Tail-reads the log file for session-level fields only. Emits a single `SessionContext` object and then stays quiet until a new session starts.

```python
class SessionMetadataParser:
    """
    Extracts session metadata from log file.
    No lap detection. No fuel parsing. No sector tracking.
    """

    ACTIVE_PATTERNS = {
        "steam_id",      # Player Steam ID
        "driver_name",   # Player display name
        "track_name",    # Track identifier
        "game_started",  # Car, session type, weather
        "setup_group",   # Setup notes (optional)
    }

    # Removed from LogParser:
    # - "lap_finish"       → handled by SHM completedLaps
    # - "race_split"       → handled by SHM currentSectorIndex
    # - "practice_split"   → handled by SHM currentSectorIndex
    # - "track_limits"     → optional: keep only if detailed reason needed
    # - "fuel_consumed"    → handled by SHM fuel delta
```

### `HybridLapManager`

Coordinates both sources. SHM drives the lap detection loop. The metadata parser enriches lap records with session context.

```python
class HybridLapManager:
    """
    SHM owns lap detection timing.
    Log parser owns player identity and session context.
    This class merges the two into complete LapRecord objects.
    """

    def __init__(self):
        self.sm_reader       = SharedMemoryReader(poll_hz=10.0)
        self.metadata_parser = SessionMetadataParser()
        self.lap_tracker     = LapTracker()
        self.session_ctx: SessionContext | None = None

    async def run(self):
        # Boot: parse log for session metadata before accepting any laps
        # Loop: poll SHM, detect lap completions, merge context, emit records
        ...
```

**Lap submission gate:** No lap record is submitted until `session_ctx` is populated with at minimum: `player_id`, `track`, and `car`. If SHM detects a lap before log metadata is ready, the record is queued and flushed once metadata arrives.

---

## Implementation Phases

### Phase 1 — SHM Reader, Standalone (Week 1)

Goal: get SHM lap detection working in isolation and validate it against the existing log-based system.

- [ ] Implement `ACEGraphics` and `ACEPhysics` ctypes structs in `src/core/shared_memory.py`
  - Validate struct size and field offsets against known-good forensic mapping
  - Add struct size assertion at startup to catch ACE update breakage early
- [ ] Implement `SharedMemoryReader` with connection state detection
- [ ] Port lap detection logic from `ac_evo_lap_tracker.py`:
  - Lap completion: `completedLaps` increment
  - Sector transitions: `currentSectorIndex` changes
  - Lap time: `iLastTime` on completion
  - Fuel delta: `fuel` at lap start vs end
- [ ] **Verify `isValidLap`** against real sessions — test track limits, pit exit laps, and penalty scenarios before treating it as reliable
- [ ] Run SHM reader in parallel with existing log parser
- [ ] Log disagreements between the two sources; target ±10 ms lap time parity
- [ ] Measure `iLastTime` population latency — confirm it is available immediately on lap completion, not subject to a write delay

**Exit criteria:** All lap times agree within ±10 ms across a full session at Laguna Seca or Spa.

---

### Phase 2 — Hybrid Integration (Week 2)

Goal: session metadata flows from logs into SHM-detected lap records.

- [ ] Refactor `LogParser` → `SessionMetadataParser` (metadata patterns only)
- [ ] Implement `SessionContext` dataclass: player identity, track, car, weather, setup notes
- [ ] Implement `HybridLapManager` with lap submission queue (holds laps until metadata is ready)
- [ ] Wire identity resolution: log-provided `player_id` attached to every SHM lap record
- [ ] Add session boundary detection: handle game restart and SHM reconnection

**Exit criteria:** 100% of submitted laps carry complete metadata; no regression in lap detection accuracy.

---

### Phase 3 — Outlap Detection + Stint Tracking (Week 3)

Goal: replicate the two remaining log-derived signals using SHM state and logic layer heuristics.

#### Outlap Detection

The current log-based `Outplap split` marker is reliable but requires regex. SHM has no direct equivalent. The replacement is a set of heuristics that must be calibrated per track.

**Proposed signals (combine for confidence):**

| Signal | Method | Caveat |
|--------|--------|--------|
| Slow S1 time | S1 > (personal best S1 × threshold) | Threshold is track-specific |
| Race start | `completedLaps == 0` at first lap completion | Works cleanly |
| Pit exit transition | `isInPitLane` → false in S1 | Requires `isInPitLane` to be reliable |
| Speed profile | Very low speed sustained into first sector | Needs tuning |

**Calibration step required:** Seed threshold values from existing JSONL lap data. Laguna (3 sectors) and Fuji (2 sectors) have different outlap profiles — a 2-sector track like Fuji has longer sectors, so "slow S1" thresholds must be normalized by sector count. Build a calibration utility that reads historical laps and outputs `(track, sector_count, threshold)` tuples.

**Fallback:** Retain optional log-based outlap detection behind a config flag. Use as ground truth for A/B accuracy comparison during this phase. Do not remove until accuracy is verified.

#### Stint Tracking

Detect stints via `tyreCompound` + `currentTyreSet` change across lap boundaries. A change in either field signals a new stint.

```python
def is_new_stint(prev: ACEGraphics, curr: ACEGraphics) -> bool:
    return (prev.tyreCompound != curr.tyreCompound or
            prev.currentTyreSet != curr.currentTyreSet)
```

- [ ] Implement outlap heuristics with per-track threshold configuration
- [ ] Build calibration utility from existing JSONL data
- [ ] Implement stint tracking via tyre compound/set change detection
- [ ] A/B test outlap accuracy vs log-based method on recorded sessions

**Exit criteria:** Outlap detection matches log-based method on ≥95% of laps across test sessions.

---

### Phase 4 — Cleanup (Week 4+)

Goal: remove legacy lap detection code once Phase 3 accuracy is confirmed. Do not start this phase until outlap detection has been stable for at least one full week of real sessions.

- [ ] Delete lap detection patterns from `LogParser` / `SessionMetadataParser`
- [ ] Remove `InProgressLap` accumulation logic from log parser
- [ ] Delete hybrid fuel spike handling (SHM `fuel` is clean)
- [ ] Remove sector consistency cross-validation (SHM sectors are structurally consistent)
- [ ] Harden SHM connection: retry logic, reconnect on handle invalidation
- [ ] Final regex audit: target fewer than 100 lines of active patterns

**Exit criteria:** Clean test suite pass with no regex-based lap parsing active. Regex line count < 100.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| ACE update changes SHM struct layout | Medium | High | Struct size assertion at startup; version detection; fallback to log-only mode |
| `isValidLap` unreliable for some invalidation types | Medium | Medium | Verify in Phase 1 before relying on it; optional log enrichment for edge cases |
| Outlap heuristics fail on new tracks | Medium | Medium | Per-track calibration utility; log-based fallback retained through Phase 3 |
| `iLastTime` has write delay (same as log flush) | Low | High | Stress-test in Phase 1 — this is the core latency claim |
| Player identity not captured before first lap | Low | High | Lap submission queue; require session context before any lap is emitted |
| SHM unavailable (permissions, game not running) | Low | High | Graceful fallback to log-only mode with user-visible warning |

---

## Success Criteria

| Criterion | Target |
|-----------|--------|
| Lap time parity (SHM vs log) | ±10 ms |
| Metadata completeness | 100% of submitted laps have player ID, track, car |
| Lap detection latency | < 100 ms from finish line crossing |
| Missed laps due to parse failure | Zero |
| Remaining regex patterns | < 100 lines |

---

## Migration Timeline

| Week | Milestone | Validation |
|------|-----------|------------|
| 1 | SHM reader standalone, `iLastTime` latency confirmed | Parallel lap recording vs log parser |
| 2 | Hybrid mode: all laps carry full metadata | No regression, lap queue logic proven |
| 3 | Outlap detection and stint tracking operational | ≥95% outlap accuracy vs log baseline |
| 4 | Cleanup complete, regex < 100 lines | Clean build, full test suite |
| 5 | Production — monitor for one week | Rollback plan on standby |

---

## Rollback Plan

1. **Immediate (< 1 min):** Toggle `lap_detection_source = "log"` config flag — reverts to log-only mode without code change
2. **Within 24h:** Roll back binary to last stable release
3. **Data integrity:** All laps recorded during hybrid period carry full metadata; no data loss risk from rollback

---

## Appendix: SHM Struct Reference

Abbreviated. Full struct must be validated against forensic mapping before use — do not trust field order or offsets from community documentation without cross-checking against the known-good decode from prior work.

### ACEGraphics (relevant fields)

```python
_fields_ = [
    ("status",             ctypes.c_int),          # 2 = LIVE
    ("currentTime",        ctypes.c_wchar * 15),   # Current lap time string
    ("lastTime",           ctypes.c_wchar * 15),   # Last lap time string
    ("bestTime",           ctypes.c_wchar * 15),   # Best lap time string
    ("completedLaps",      ctypes.c_int),           # Lap counter (primary trigger)
    ("currentSectorIndex", ctypes.c_int),           # 0, 1, 2
    ("lastSectorTime",     ctypes.c_int),           # ms
    ("iLastTime",          ctypes.c_int),           # Last lap ms (verify latency)
    ("iBestTime",          ctypes.c_int),           # Session best ms
    ("isInPit",            ctypes.c_int),
    ("isInPitLane",        ctypes.c_int),           # For outlap heuristic
    ("tyreCompound",       ctypes.c_wchar * 33),
    ("currentTyreSet",     ctypes.c_int),
    ("rainTyres",          ctypes.c_int),
    ("usedFuel",           ctypes.c_float),
    ("isValidLap",         ctypes.c_int),           # ⚠️ Verify before trusting
]
```

### ACEPhysics (relevant fields)

```python
_fields_ = [
    ("fuel",      ctypes.c_float),  # Absolute fuel level (liters)
    ("speedKmh",  ctypes.c_float),
    ("airTemp",   ctypes.c_float),
    ("roadTemp",  ctypes.c_float),
]
```