"""
ACE Log Parser Constants

Tuning constants and configuration values.
"""

from typing import Optional


# `tyres out → 4` events with inside_distance above this are pit-teleport
# artefacts. All three analysed logs show exactly 12.52 m for teleports;
# real violations are below ±3.5 m.
PIT_TELEPORT_DISTANCE_M: float = 10.0

# Minimum inside_distance for a track limit violation to invalidate a lap.
# Values below this (brief momentary excursions) are tolerated by the game.
# Analysis shows violations < 2m don't invalidate, >= 2.62m do.
TRACK_LIMIT_INVALIDATION_THRESHOLD_M: float = 2.0

# Maximum acceptable difference between (S1+S2+S3) and lap_time_ms.
# Small deltas exist due to sub-millisecond boundary timing; anything larger
# indicates sector/lap desync corruption.
SECTOR_SUM_TOLERANCE_MS: int = 50

# Log and graphics-SHM lap timers are independently rounded to milliseconds.
# A tiny tolerance reconciles that representation drift without allowing a
# genuinely different lap to consume the completion.
LAP_TIME_RECONCILIATION_TOLERANCE_MS: int = 2

# Minimum reasonable lap distance in hundredmeters to flag a lap as aborted.
# Spa-GP is ~69. Setting low because track length varies; callers can filter.
MIN_FULL_LAP_HUNDREDM: int = 20


def is_hybrid_car(
    has_ers_from_shm: Optional[bool] = None,
    has_kers_from_shm: Optional[bool] = None,
) -> bool:
    """Determine whether the current car is a hybrid electric vehicle.

    Uses SHM flags (``has_ers`` / ``has_kers``) decoded from the game's
    graphics shared memory at runtime.  When SHM is not yet connected,
    returns ``False`` (fuel is assumed reliable until SHM says otherwise).
    """
    return bool(has_ers_from_shm or has_kers_from_shm)

# Raw GameModeType → normalised session label.
SESSION_TYPE_MAP: dict[str, str] = {
    "INSTANT_RACE": "RACE",
    "RACE":         "RACE",
    "PRACTICE":     "PRACTICE",
    "TIME_ATTACK":  "TIME_ATTACK",
    "QUALIFYING":   "QUALIFYING",
    "HOTLAP":       "HOTLAP",
    "DRIFT":        "DRIFT",
}

# Modes that use the player-only "On Split start" sector format.
PRACTICE_LIKE: frozenset[str] = frozenset({"PRACTICE", "TIME_ATTACK", "HOTLAP"})

# Modes that use the car-specific "Split completed for car <uuid>" format.
RACE_LIKE: frozenset[str] = frozenset({"RACE", "QUALIFYING"})
