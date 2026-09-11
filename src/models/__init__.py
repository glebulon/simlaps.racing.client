"""
ACE Log Parser Models

Data models for lap, session, stint, and tyre tracking.
"""

from .constants import (
    LAP_TIME_RECONCILIATION_TOLERANCE_MS,
    MIN_FULL_LAP_HUNDREDM,
    PIT_TELEPORT_DISTANCE_M,
    PRACTICE_LIKE,
    RACE_LIKE,
    SECTOR_SUM_TOLERANCE_MS,
    SESSION_TYPE_MAP,
    TRACK_LIMIT_INVALIDATION_THRESHOLD_M,
    is_hybrid_car,
)
from .context import LogContext
from .lap import InProgressLap, LapData, LapState, SessionData, StintData
from .shared_session import (
    FuelData,
    LapCompletionData,
    LapTimingData,
    LapValidityData,
    PlayerIdentificationData,
    SectorSplitData,
    SessionMetadataData,
    SharedSessionData,
    SharedSessionManager,
)
from .tyre_state import TyreState

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
