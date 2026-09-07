"""
ACE Log Parser Models

Data models for lap, session, stint, and tyre tracking.
"""

from .constants import (
    PIT_TELEPORT_DISTANCE_M,
    TRACK_LIMIT_INVALIDATION_THRESHOLD_M,
    SECTOR_SUM_TOLERANCE_MS,
    LAP_TIME_RECONCILIATION_TOLERANCE_MS,
    MIN_FULL_LAP_HUNDREDM,
    is_hybrid_car,
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
    LapCompletionData,
    FuelData,
    PlayerIdentificationData,
    SectorSplitData,
    SessionMetadataData,
    SharedSessionData,
    SharedSessionManager,
)

__all__ = [
    # Constants
    "PIT_TELEPORT_DISTANCE_M",
    "TRACK_LIMIT_INVALIDATION_THRESHOLD_M",
    "SECTOR_SUM_TOLERANCE_MS",
    "LAP_TIME_RECONCILIATION_TOLERANCE_MS",
    "MIN_FULL_LAP_HUNDREDM",
    "is_hybrid_car",
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
    "LapCompletionData",
    "FuelData",
    "PlayerIdentificationData",
    "SectorSplitData",
    "SessionMetadataData",
    "SharedSessionData",
    "SharedSessionManager",
]
