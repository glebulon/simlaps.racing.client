"""
ACE Log Parser Models

Data models for lap, session, stint, and tyre tracking.
"""

from .constants import (
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
from .lap import LapState, InProgressLap, StintData, LapData, SessionData
from .tyre_state import TyreState
from .context import LogContext
from .shared_session import (
    LapValidityData,
    LapTimingData,
    FuelData,
    PlayerIdentificationData,
    SectorSplitData,
    SessionMetadataData,
    SharedSessionData,
    SharedSessionManager,
    LegacySessionDataWrapper,
)

__all__ = [
    # Constants
    "PIT_TELEPORT_DISTANCE_M",
    "TRACK_LIMIT_INVALIDATION_THRESHOLD_M",
    "SECTOR_SUM_TOLERANCE_MS",
    "HYBRID_FUEL_THRESHOLD_L",
    "HYBRID_SPIKE_SESSION_THRESHOLD",
    "MIN_FULL_LAP_HUNDREDM",
    "KNOWN_HYBRID_CARS",
    "SESSION_TYPE_MAP",
    "PRACTICE_LIKE",
    "RACE_LIKE",
    # Models
    "LapState",
    "InProgressLap",
    "StintData",
    "LapData",
    "SessionData",
    "TyreState",
    "LogContext",
    "LapValidityData",
    "LapTimingData",
    "FuelData",
    "PlayerIdentificationData",
    "SectorSplitData",
    "SessionMetadataData",
    "SharedSessionData",
    "SharedSessionManager",
    "LegacySessionDataWrapper",
]
