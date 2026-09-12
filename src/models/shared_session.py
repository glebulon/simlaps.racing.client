"""Shared session data models and manager.

This module provides a single thread-safe session store used by log parsing,
shared-memory decoding, telemetry analysis, and API submission code.
"""

from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Set

from .constants import LAP_TIME_RECONCILIATION_TOLERANCE_MS
from .lap import LapData, SessionData


class OriginRelation(str, Enum):
    """Relationship between a captured origin and the manager's current state."""

    ACTIVE = "active"
    LATE_BOUND = "late_bound"
    CLOSED = "closed"
    MISMATCH = "mismatch"

_TERMINAL_SESSION_PHASES = frozenset(
    {
    "ended",
    "disqualified",
    "teardown",
})
_BASELINE_SESSION_PHASES = frozenset({
    "start_spawn_on_position",
    "start_countdown_lights_on",
    "start_countdown_lights_off",
    "waiting_for_start",
    "waiting_for_pitbox",
})


def _car_identity(value: Optional[str]) -> str:
    """Compare log ids and graphics display labels without cross-source drift."""
    if not value:
        return ""
    normalized = "".join(ch for ch in str(value).casefold() if ch.isalnum())
    return normalized[2:] if normalized.startswith("ks") else normalized


# ACE's log and graphics sources use different names for a small number of
# cars.  Keep this bridge explicit: accepting a fuzzy token overlap here would
# make a real cross-car transition look like the current origin.
# The M3 pair is an ACE 0.9.1 observed log-id/display-label pair. The Cup pair
# is controlled-live-observed in ACE 0.9.1 (graphics label captured at runtime);
# the RS and Cayman labels are grounded in local ACE log UI selection labels.
# Raw live SHM bytes for the latter pairs were not preserved. This table is
# deliberately not a claim that every catalog alias is covered.
_CAR_IDENTITY_ALIASES = frozenset(
    {
        frozenset(
            {
                "bmwm3e30evoiii",
                "bmwm3e30sportevoevolutioniii",
            }
        ),
        frozenset({
            # ACE log id and graphics display label observed for the M4 GT3
            # Evo in the same live session.
            "bmwm4gt3",
            "bmwm4gt3evo",
        }),
        frozenset({
            "porsche992gt3cup",
            "porsche911gt3cup992",
        }),
        frozenset({
            "porsche992gt3rs",
            "porsche911gt3rs992",
        }),
        frozenset({
            "porsche718caymangt4csmr",
            "porsche718caymangt4clubsport",
        }),
    }
)


def car_models_match(expected: Optional[str], current: Optional[str]) -> bool:
    """Return whether two source-specific car names identify one car.

    Empty values remain compatible with the existing origin contract: a
    missing source cannot disprove ownership.  Non-empty values require exact
    normalized equality or an explicitly verified source alias.
    """
    if not expected or not current:
        return True
    expected_key = _car_identity(expected)
    current_key = _car_identity(current)
    return (
        expected_key == current_key
        or frozenset({expected_key, current_key}) in _CAR_IDENTITY_ALIASES
    )


def car_uuids_match(expected: Optional[str], current: Optional[str]) -> bool:
    """Compare car UUIDs across log formats that may add punctuation."""
    if not expected or not current:
        return True
    expected_key = "".join(ch for ch in str(expected).casefold() if ch.isalnum())
    current_key = "".join(ch for ch in str(current).casefold() if ch.isalnum())
    return expected_key == current_key


@dataclass(frozen=True, slots=True)
class SessionOriginSnapshot:
    epoch: int
    session_id: Optional[str]
    graphics_car_model: Optional[str]
    car_uuid: Optional[str]
    graphics_ready: bool


@dataclass(frozen=True, slots=True)
class SubmissionSnapshot:
    """Small immutable overlay used while submitting one requested lap."""

    origin: SessionOriginSnapshot
    retained_session_id: Optional[str]
    steam_id: Optional[str]
    car_model: Optional[str]
    track: Optional[str]
    session_type: Optional[str]
    game_version: Optional[str]
    lap_number: Optional[int]
    lap_time_ms: Optional[int]
    sectors: tuple[Optional[int], Optional[int], Optional[int]]
    fuel_per_lap: Optional[float]


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Small immutable overlay used by analysis for one capture origin."""

    origin: SessionOriginSnapshot
    retained_session_id: Optional[str]
    car_model: Optional[str]
    timing_records: tuple[tuple[int, Optional[float]], ...]
    validity_records: tuple[tuple[int, bool], ...]


@dataclass(frozen=True, slots=True)
class OriginSnapshotResult:
    """Atomic origin classification and its current origin at one lock point."""

    relation: OriginRelation
    current_origin: SessionOriginSnapshot
    snapshot: Optional[SubmissionSnapshot | AnalysisSnapshot] = None


def _is_terminal_graphics_state(
    graphics_data: Dict[str, Any],
    current_phase: Optional[str],
) -> bool:
    """Return whether a graphics snapshot is a terminal/teardown state."""
    phase = graphics_data.get("session_phase", current_phase)
    normalized_phase = str(phase or "").strip().casefold()
    if normalized_phase in _TERMINAL_SESSION_PHASES:
        return True

    # AC_OFF is the mapping teardown state. It is terminal even when a stale
    # phase string remains from the last active snapshot.
    status_name = str(graphics_data.get("status_name") or "").strip().upper()
    return status_name == "AC_OFF"


@dataclass
class LapValidityData:
    """Lap validity information for lap posting."""

    lap_number: int
    is_valid: bool
    lap_state: Optional[str] = None
    invalidation_reason: Optional[str] = None
    invalidation_timestamp: Optional[str] = None
    source: str = "shm_graphics"
    penalty_count: Optional[int] = None
    track_limit_violations: Optional[int] = None


@dataclass
class LapTimingData:
    """Lap timing information with source tracking."""

    lap_number: int
    current_lap_time_ms: Optional[int] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None
    source: str = "shm_graphics"
    lap_time_str: Optional[str] = None
    lap_completion_timestamp: Optional[str] = None
    completed_lap_time: Optional[float] = None
    completed_lap_time_source: Optional[str] = None  # "logs", "shm_graphics", "calculated"


@dataclass(frozen=True)
class LapCompletionData:
    """A live SHM lap-counter transition observed before logs may flush."""

    completed_laps: int
    lap_time_ms: int
    is_valid: Optional[bool]
    timestamp: str
    observed_at: float
    source: str = "shm_graphics"
    # Completion ownership is captured at the instant the SHM transition is
    # observed.  Defaults keep older callers and fixtures source compatible.
    origin_epoch: Optional[int] = None
    session_id: Optional[str] = None
    car_model: Optional[str] = None
    car_uuid: Optional[str] = None


@dataclass
class FuelData:
    """Fuel information with source tracking."""

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None
    fuel_consumed_lap: Optional[float] = None
    source: str = "shm_graphics"


@dataclass
class PlayerIdentificationData:
    """Player identification from logs (SHM does not provide this)."""

    steam_id: Optional[str] = None
    player_name: Optional[str] = None
    car_uuid: Optional[str] = None
    car_model: Optional[str] = None
    source: str = "logs"


@dataclass
class SectorSplitData:
    """Sector split times from logs (SHM does not provide this)."""

    lap_number: int
    sector1_ms: Optional[int] = None
    sector2_ms: Optional[int] = None
    sector3_ms: Optional[int] = None
    source: str = "logs"


@dataclass
class SessionMetadataData:
    """Session metadata from Static SHM (or logs as fallback)."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    session_name: str = "Unknown"
    track: str = "Unknown"
    track_configuration: str = "Unknown"
    track_length_m: Optional[float] = None
    weather: str = "Unknown"
    is_online: bool = False
    is_timed_race: bool = False
    event_id: Optional[int] = None
    source: str = "shm_static"


@dataclass
class SharedSessionData:
    """Unified session data accessible by telemetry and log parser."""

    # Shared objects
    lap_validity: Dict[int, LapValidityData] = field(default_factory=dict)
    lap_timing: Dict[int, LapTimingData] = field(default_factory=dict)
    latest_lap_completion: Optional[LapCompletionData] = None
    lap_completions: list[LapCompletionData] = field(default_factory=list)
    consumed_lap_completion_times: Set[float] = field(default_factory=set)
    # A timer-reset completion can be followed by a delayed completed-lap
    # counter update for that same boundary. Keep that relationship explicit
    # so equal-time later laps are still emitted.
    pending_counter_echo: bool = False
    pending_counter_echo_lap: Optional[int] = None
    pending_counter_echo_time_ms: Optional[int] = None
    fuel_data: FuelData = field(default_factory=FuelData)
    player_identification: PlayerIdentificationData = field(default_factory=PlayerIdentificationData)
    sector_splits: Dict[int, SectorSplitData] = field(default_factory=dict)
    session_metadata: SessionMetadataData = field(default_factory=SessionMetadataData)

    current_lap_time_ms: Optional[int] = None
    # Live graphics validity is latched independently of the physical lap
    # number. ACE can reuse its completed-lap counter after returning to the
    # pits, while completed log records remain keyed by absolute session lap.
    active_lap_is_valid: Optional[bool] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None

    sector_times: Dict[int, Dict[int, int]] = field(default_factory=dict)

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None

    total_laps: Optional[int] = None
    current_lap: Optional[int] = None
    session_phase: Optional[str] = None
    session_time_left_ms: Optional[int] = None
    current_pos: Optional[int] = None
    total_drivers: Optional[int] = None

    car_setup: Dict[str, Any] = field(default_factory=dict)
    assists_state: Dict[str, Any] = field(default_factory=dict)

    max_speed: Optional[float] = None
    tyre_compound: str = "Unknown"
    stint_number: int = 1

    # Powertrain flags decoded from the static SHM region.
    # Used to detect hybrid/electric cars dynamically instead of
    # relying solely on a hardcoded model-name list.
    has_ers: Optional[bool] = None
    has_kers: Optional[bool] = None
    # Identity to which the capability sample belongs, so it can be cleared
    # when the player changes cars.
    hybrid_flags_car_uuid: Optional[str] = None

    starting_ambient_temp_c: Optional[float] = None
    starting_ground_temp_c: Optional[float] = None
    starting_grip: Optional[str] = None
    air_density: Optional[float] = None

    data_sources: Dict[str, Set[str]] = field(default_factory=dict)


class SharedSessionManager:
    """Thread-safe manager for shared session data."""

    def __init__(self) -> None:
        self._session_data = SharedSessionData()
        self._lock = threading.RLock()
        # Session ownership is deliberately separate from the merged log
        # identity.  SHM can lag a log/session transition by several seconds.
        self._session_epoch = 0
        self._active_session_id: Optional[str] = None
        self._active_car_model: Optional[str] = None
        self._active_car_uuid: Optional[str] = None
        self._graphics_car_model: Optional[str] = None
        self._graphics_status_name: Optional[str] = None
        self._graphics_timing_active = False
        self._graphics_origin_pending = False
        self._anonymous_origin_pending = False
        self._graphics_transaction_epoch: Optional[int] = None
        self._completion_bindings: dict[float, tuple[Optional[int], Optional[str], Optional[str], Optional[str]]] = {}

    def get_active_session_id(self) -> Optional[str]:
        with self._lock:
            return self._active_session_id

    def get_session_origin(self) -> SessionOriginSnapshot:
        """Return the immutable origin used by capture and completion code."""
        with self._lock:
            return SessionOriginSnapshot(
                epoch=self._session_epoch,
                session_id=self._active_session_id,
                graphics_car_model=self._graphics_car_model,
                car_uuid=self._active_car_uuid,
                graphics_ready=not self._graphics_origin_pending,
            )

    def _current_origin_locked(self) -> SessionOriginSnapshot:
        return SessionOriginSnapshot(
            epoch=self._session_epoch,
            session_id=self._active_session_id,
            graphics_car_model=self._graphics_car_model,
            car_uuid=self._active_car_uuid,
            graphics_ready=not self._graphics_origin_pending,
        )

    def get_origin_relation(
        self, expected_origin: Optional[SessionOriginSnapshot]
    ) -> tuple[OriginRelation, SessionOriginSnapshot]:
        """Classify an expected origin and return the current origin atomically."""
        with self._lock:
            return self._origin_relation_locked(expected_origin)

    def _origin_relation_locked(
        self, expected_origin: Optional[SessionOriginSnapshot]
    ) -> tuple[OriginRelation, SessionOriginSnapshot]:
        current = self._current_origin_locked()
        if expected_origin is None:
            return OriginRelation.ACTIVE, current
        if expected_origin.epoch != current.epoch:
            return OriginRelation.MISMATCH, current
        if not car_models_match(
            expected_origin.graphics_car_model, current.graphics_car_model
        ):
            return OriginRelation.MISMATCH, current
        if (
            expected_origin.car_uuid
            and current.car_uuid
            and not car_uuids_match(expected_origin.car_uuid, current.car_uuid)
        ):
            return OriginRelation.MISMATCH, current
        if current == expected_origin:
            return OriginRelation.ACTIVE, current
        if (
            current.session_id is None
            and expected_origin.session_id is not None
            and self._session_data.session_metadata.session_id == expected_origin.session_id
        ):
            return OriginRelation.CLOSED, current
        if (
            expected_origin.session_id is None
            and current.session_id is not None
            and (current.car_uuid or current.graphics_car_model)
        ):
            return OriginRelation.LATE_BOUND, current
        return OriginRelation.MISMATCH, current

    def begin_session(
        self,
        session_id: str,
        *,
        car_model: Optional[str] = None,
        car_uuid: Optional[str] = None,
    ) -> int:
        """Atomically establish the owner used by subsequent SHM samples."""
        with self._lock:
            prior_graphics_car = self._graphics_car_model
            anonymous_mismatch = bool(
                self._anonymous_origin_pending
                and prior_graphics_car
                and car_model
                and not car_models_match(prior_graphics_car, car_model)
            )
            if not self._anonymous_origin_pending or anonymous_mismatch:
                self._session_epoch += 1
            if anonymous_mismatch:
                # A parser identity that contradicts an anonymous graphics
                # epoch starts a new epoch. The old capture/completions must
                # never resume when that graphics model later appears.
                self._anonymous_origin_pending = False
            self._active_session_id = session_id
            self._active_car_model = car_model or None
            self._active_car_uuid = car_uuid or None
            prior_hybrid_uuid = self._session_data.hybrid_flags_car_uuid
            self._graphics_origin_pending = bool(
                prior_graphics_car
                and car_model
                and not car_models_match(prior_graphics_car, car_model)
            )
            self._replace_session_data_locked(preserve_identity=True)
            ident = self._session_data.player_identification
            ident.car_model = car_model or None
            ident.car_uuid = car_uuid or None
            if (
                not car_uuid
                or not prior_hybrid_uuid
                or str(prior_hybrid_uuid).casefold() != str(car_uuid).casefold()
            ):
                self._session_data.has_ers = None
                self._session_data.has_kers = None
                self._session_data.hybrid_flags_car_uuid = None
            self._session_data.session_metadata.session_id = session_id
            anonymous_matches_graphics = bool(
                not self._graphics_origin_pending
                and (
                    not prior_graphics_car
                    or not car_model
                    or car_models_match(prior_graphics_car, car_model)
                )
            )
            if self._anonymous_origin_pending and anonymous_matches_graphics:
                self.bind_session_identity(
                    session_id=session_id,
                    car_model=car_model,
                    car_uuid=car_uuid,
                )
                self._anonymous_origin_pending = False
            return self._session_epoch

    def bind_session_identity(
        self,
        *,
        car_model: Optional[str] = None,
        car_uuid: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Bind delayed log identity within the already-open epoch.

        Only completions created in this epoch while identity was unknown are
        adopted.  This prevents an unowned completion from a prior run being
        assigned to an arbitrary later session.
        """
        with self._lock:
            if (
                session_id is not None
                and self._active_session_id is not None
                and session_id != self._active_session_id
            ):
                return
            if (
                session_id is not None
                and self._active_session_id is None
                and self._session_epoch > 0
                and self._session_data.session_metadata.session_id
                and session_id != self._session_data.session_metadata.session_id
            ):
                return
            if (
                car_model
                and self._active_car_model
                and not car_models_match(self._active_car_model, car_model)
            ):
                return
            if (
                car_uuid
                and self._active_car_uuid
                and not car_uuids_match(car_uuid, self._active_car_uuid)
            ):
                return
            if (
                self._graphics_car_model
                and car_model
                and not car_models_match(self._graphics_car_model, car_model)
            ):
                # A parser identity that disagrees with the still-visible
                # graphics car cannot bind an anonymous completion. Wait for
                # the matching graphics episode or a later explicit boundary.
                return
            retained_identity = self._session_data.player_identification
            closed_owner = (
                self._active_session_id is None
                and self._session_epoch > 0
                and self._session_data.session_metadata.session_id is not None
            )
            if closed_owner and (
                (
                    car_model
                    and retained_identity.car_model
                    and not car_models_match(car_model, retained_identity.car_model)
                )
                or (
                    car_uuid
                    and retained_identity.car_uuid
                    and not car_uuids_match(car_uuid, retained_identity.car_uuid)
                )
            ):
                return
            bound_car_uuid = car_uuid or self._active_car_uuid
            bound_car_model = car_model or self._active_car_model
            bound_session_id = session_id or self._active_session_id
            adoptable_completions = []
            for completion in self._session_data.lap_completions:
                if completion.origin_epoch != self._session_epoch:
                    continue
                if (
                    completion.session_id
                    and bound_session_id
                    and completion.session_id != bound_session_id
                ):
                    continue
                if (
                    completion.car_uuid
                    and bound_car_uuid
                    and not car_uuids_match(completion.car_uuid, bound_car_uuid)
                ) or (
                    completion.car_model
                    and bound_car_model
                    and not car_models_match(completion.car_model, bound_car_model)
                ):
                    if completion.session_id is None:
                        return
                    continue
                has_positive_evidence = bool(
                    (
                        completion.session_id
                        and bound_session_id
                        and completion.session_id == bound_session_id
                    )
                    or (
                        completion.car_uuid
                        and bound_car_uuid
                        and car_uuids_match(completion.car_uuid, bound_car_uuid)
                    )
                    or (
                        completion.car_model
                        and bound_car_model
                        and car_models_match(completion.car_model, bound_car_model)
                    )
                )
                if has_positive_evidence:
                    adoptable_completions.append(completion)
            if session_id is not None and not closed_owner:
                self._active_session_id = session_id
                self._session_data.session_metadata.session_id = session_id
            if car_model:
                self._session_data.player_identification.car_model = car_model
                if not closed_owner:
                    self._active_car_model = car_model
            if car_uuid:
                self._session_data.player_identification.car_uuid = car_uuid
                if not closed_owner:
                    self._active_car_uuid = car_uuid
            for completion in adoptable_completions:
                if self._active_session_id:
                    # Keep the frozen completion object stable for consumers
                    # that already hold a reference. Ownership is an atomic
                    # manager-side binding keyed by its observation identity.
                    self._completion_bindings[completion.observed_at] = (
                        self._session_epoch,
                        self._active_session_id,
                        self._active_car_model,
                        self._active_car_uuid,
                    )

    def get_lap_completion_owner(
        self, completion: LapCompletionData
    ) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
        with self._lock:
            return self._completion_bindings.get(
                completion.observed_at,
                (
                    completion.origin_epoch,
                    completion.session_id,
                    completion.car_model,
                    completion.car_uuid,
                ),
            )

    def end_session(self, session_id: Optional[str] = None) -> None:
        """Close ownership for a session without discarding queued completions."""
        with self._lock:
            if session_id is None or session_id == self._active_session_id:
                self._active_session_id = None
                self._active_car_model = None
                self._active_car_uuid = None
            self._graphics_timing_active = False
            self._graphics_status_name = None

    def _replace_session_data_locked(self, *, preserve_identity: bool) -> None:
        old = self._session_data
        old_ident = old.player_identification if preserve_identity else PlayerIdentificationData()
        old_has_ers = old.has_ers
        old_has_kers = old.has_kers
        old_hybrid_uuid = old.hybrid_flags_car_uuid
        self._session_data = SharedSessionData()
        self._session_data.player_identification = old_ident
        # Delayed SHM completions are an ordered cross-boundary queue. They
        # retain their immutable origin and are pruned after consumption.
        self._session_data.lap_completions = list(old.lap_completions)
        self._session_data.latest_lap_completion = old.latest_lap_completion
        self._session_data.consumed_lap_completion_times = set(
            old.consumed_lap_completion_times
        )
        if (
            old_ident.car_uuid
            and old_hybrid_uuid
            and str(old_ident.car_uuid).casefold() == str(old_hybrid_uuid).casefold()
        ):
            self._session_data.has_ers = old_has_ers
            self._session_data.has_kers = old_has_kers
            self._session_data.hybrid_flags_car_uuid = old_hybrid_uuid

    def _mark_source(self, field_name: str, source: str) -> None:
        if field_name not in self._session_data.data_sources:
            self._session_data.data_sources[field_name] = set()
        self._session_data.data_sources[field_name].add(source)

    def _session_update_is_owned_locked(self, session_id: str) -> bool:
        if self._active_session_id == session_id:
            return True
        if self._active_session_id is not None:
            return False
        # A provenance-capable manager with an opened epoch or graphics car
        # is in an anonymous/end window. Do not treat that as a legacy
        # invitation for an arbitrary delayed session to write shared state.
        return self._session_epoch == 0 and self._graphics_car_model is None

    # New shared object access
    def get_lap_validity_data(self, lap_num: int) -> Optional[LapValidityData]:
        with self._lock:
            return self._session_data.lap_validity.get(lap_num)

    def get_lap_timing_data(self, lap_num: int) -> Optional[LapTimingData]:
        with self._lock:
            return self._session_data.lap_timing.get(lap_num)

    def get_latest_lap_completion(self) -> Optional[LapCompletionData]:
        with self._lock:
            return self._session_data.latest_lap_completion

    def get_lap_completions_after(self, observed_at: float) -> list[LapCompletionData]:
        """Return unconsumed SHM completions in observation order."""
        with self._lock:
            return [
                completion
                for completion in self._session_data.lap_completions
                if (
                    completion.observed_at > observed_at
                    and completion.observed_at not in self._session_data.consumed_lap_completion_times
                )
            ]

    def get_lap_completions_for_session_after(
        self,
        observed_at: float,
        *,
        session_id: Optional[str],
        origin_epoch: Optional[int] = None,
    ) -> list[LapCompletionData]:
        """Return only completions owned by the requested session epoch."""
        with self._lock:
            return [
                completion
                for completion in self._session_data.lap_completions
                if (
                    completion.observed_at > observed_at
                    and completion.observed_at
                    not in self._session_data.consumed_lap_completion_times
                    and self._completion_owner_locked(completion)[1] == session_id
                    and (
                        origin_epoch is None
                        or self._completion_owner_locked(completion)[0] == origin_epoch
                    )
                )
            ]

    def _completion_owner_locked(
        self, completion: LapCompletionData
    ) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
        return self._completion_bindings.get(
            completion.observed_at,
            (
                completion.origin_epoch,
                completion.session_id,
                completion.car_model,
                completion.car_uuid,
            ),
        )

    def get_lap_completion_by_time(
        self,
        lap_time_ms: int,
        *,
        consume: bool = False,
    ) -> Optional[LapCompletionData]:
        """Return the nearest unconsumed completion within the small tolerance.

        Matching is nearest-time first and observation-order stable for ties.
        A caller that has used the completion to reconcile a log lap can pass
        ``consume=True`` so another delayed log record cannot use it again.
        """
        with self._lock:
            candidates = [
                completion
                for completion in self._session_data.lap_completions
                if (
                    completion.observed_at not in self._session_data.consumed_lap_completion_times
                    and abs(completion.lap_time_ms - lap_time_ms) <= LAP_TIME_RECONCILIATION_TOLERANCE_MS
                )
            ]
            if not candidates:
                return None
            completion = min(
                candidates,
                key=lambda item: (abs(item.lap_time_ms - lap_time_ms), item.observed_at),
            )
            if consume:
                self._session_data.consumed_lap_completion_times.add(completion.observed_at)
                self._compact_completion_history_locked()
            return completion

    def consume_lap_completion(self, completion: LapCompletionData) -> None:
        """Mark one SHM completion as used by a parser reconciliation."""
        with self._lock:
            self._session_data.consumed_lap_completion_times.add(completion.observed_at)
            self._compact_completion_history_locked()

    def _compact_completion_history_locked(self) -> None:
        """Drop only old consumed completions; unresolved records never age out."""
        consumed = self._session_data.consumed_lap_completion_times
        consumed_items = sorted(
            (item for item in self._session_data.lap_completions if item.observed_at in consumed),
            key=lambda item: item.observed_at,
        )
        keep_consumed = {item.observed_at for item in consumed_items[-512:]}
        self._session_data.lap_completions = [
            item
            for item in self._session_data.lap_completions
            if item.observed_at not in consumed or item.observed_at in keep_consumed
        ]
        consumed.intersection_update(keep_consumed)
        retained_observations = {
            item.observed_at for item in self._session_data.lap_completions
        }
        for observed_at in tuple(self._completion_bindings):
            if observed_at not in retained_observations:
                del self._completion_bindings[observed_at]

    def get_fuel_data(self) -> FuelData:
        with self._lock:
            return replace(self._session_data.fuel_data)

    def get_player_identification(self) -> PlayerIdentificationData:
        with self._lock:
            return replace(self._session_data.player_identification)

    def get_sector_split_data(self, lap_num: int) -> Optional[SectorSplitData]:
        with self._lock:
            return self._session_data.sector_splits.get(lap_num)

    def get_session_metadata_data(self) -> SessionMetadataData:
        with self._lock:
            return replace(self._session_data.session_metadata)

    # Legacy accessors
    def get_lap_time(self, lap_num: int) -> Optional[float]:
        with self._lock:
            timing = self._session_data.lap_timing.get(lap_num)
            if timing is None or timing.completed_lap_time is None:
                return None
            return timing.completed_lap_time

    def get_current_lap_time(self) -> Optional[int]:
        with self._lock:
            return self._session_data.current_lap_time_ms

    def get_sector_times(self, lap_num: int) -> Optional[Dict[int, int]]:
        with self._lock:
            return self._session_data.sector_times.get(lap_num)

    def get_lap_validity(self, lap_num: int) -> bool:
        with self._lock:
            validity = self._session_data.lap_validity.get(lap_num)
            return validity.is_valid if validity is not None else True

    def get_lap_state(self, lap_num: int) -> Optional[str]:
        with self._lock:
            lap_validity = self._session_data.lap_validity.get(lap_num)
            if lap_validity is None:
                return None
            return lap_validity.lap_state or ("VALID" if lap_validity.is_valid else "INVALID_GAME")

    def get_car_setup(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._session_data.car_setup)

    def get_car(self) -> str:
        with self._lock:
            return self._session_data.player_identification.car_model or "Unknown"

    def get_hybrid_flags(self) -> tuple[Optional[bool], Optional[bool]]:
        """Return ``(has_ers, has_kers)`` from the static SHM region.

        Both values are ``None`` until the telemetry capture decodes the
        first static frame.
        """
        with self._lock:
            return self._session_data.has_ers, self._session_data.has_kers

    def get_session_metadata(self) -> Dict[str, Any]:
        with self._lock:
            md = self._session_data.session_metadata
            ident = self._session_data.player_identification
            return {
                "session_id": md.session_id,
                "game_version": md.game_version,
                "session_type": md.session_type,
                "track": md.track,
                "track_configuration": md.track_configuration,
                "track_length_m": md.track_length_m,
                "is_online": md.is_online,
                "is_timed_race": md.is_timed_race,
                "event_id": md.event_id,
                "player_id": ident.steam_id,
                "car_uuid": ident.car_uuid,
            }

    def get_best_lap_time(self) -> Optional[float]:
        with self._lock:
            times = [
                t.completed_lap_time for t in self._session_data.lap_timing.values() if t.completed_lap_time is not None
            ]
            return min(times) if times else None

    def get_all_lap_times(self) -> Dict[int, float]:
        with self._lock:
            return {
                lap_num: t.completed_lap_time
                for lap_num, t in self._session_data.lap_timing.items()
                if t.completed_lap_time is not None
            }

    def validate_data_consistency(self) -> Dict[str, list[str]]:
        issues: list[str] = []
        with self._lock:
            for _lap_num, timing in sorted(self._session_data.lap_timing.items()):
                if timing.completed_lap_time is None:
                    continue
                # Check for source drift: if both logs and graphics provided
                # times, compare them.  We detect this by checking if the
                # LapTimingData was updated from both sources.
                # Since we now store a single completed_lap_time with priority,
                # we compare against the LapData from logs if available.
                pass

        return {"inconsistencies": issues}

    def get_all_lap_validity(self) -> Dict[int, bool]:
        with self._lock:
            return {lap_num: v.is_valid for lap_num, v in self._session_data.lap_validity.items()}

    # New shared object updates
    def update_lap_validity_from_graphics_shm(self, lap_num: int, is_invalid: bool) -> None:
        with self._lock:
            if (
                self._graphics_transaction_epoch is not None
                and self._graphics_transaction_epoch != self._session_epoch
            ):
                return
            current = self._session_data.lap_validity.get(lap_num)
            lap_state = "INVALID_GAME" if is_invalid else "VALID"

            # Completed laps (source == "logs") are frozen — SHM validity
            # is read-only for them.  Only the in-progress lap is updated.
            if current is not None and current.source == "logs":
                return

            if current is None:
                current = LapValidityData(lap_number=lap_num, is_valid=not is_invalid, lap_state=lap_state)
                self._session_data.lap_validity[lap_num] = current
            else:
                current.is_valid = not is_invalid
                current.lap_state = lap_state
                current.source = "shm_graphics"

            self._mark_source("lap_validity", "shm_graphics")

    def update_lap_timing_from_graphics_shm(
        self,
        lap_num: int,
        timing_data: Dict[str, Any],
        *,
        completed_lap_num: Optional[int] = None,
    ) -> None:
        with self._lock:
            if (
                self._graphics_transaction_epoch is not None
                and self._graphics_transaction_epoch != self._session_epoch
            ):
                return
            current = self._session_data.lap_timing.get(lap_num)
            if current is None:
                current = LapTimingData(lap_number=lap_num)
                self._session_data.lap_timing[lap_num] = current

            current.current_lap_time_ms = timing_data.get(
                "current_lap_time_ms",
                timing_data.get("current_laptime_ms"),
            )
            current.last_lap_time_ms = timing_data.get("last_laptime_ms")
            current.best_lap_time_ms = timing_data.get("best_laptime_ms")
            current.ideal_lap_time_ms = timing_data.get("ideal_laptime_ms")
            current.delta_time_ms = timing_data.get("delta_time_ms")
            current.source = "shm_graphics"

            self._session_data.current_lap_time_ms = current.current_lap_time_ms
            self._session_data.last_lap_time_ms = current.last_lap_time_ms
            self._session_data.best_lap_time_ms = current.best_lap_time_ms
            self._session_data.ideal_lap_time_ms = current.ideal_lap_time_ms
            self._session_data.delta_time_ms = current.delta_time_ms

            if current.last_lap_time_ms and current.last_lap_time_ms > 0:
                # Graphics exposes the previous lap's completed time alongside
                # the new current lap. Callers that know the completed-lap
                # counter must map that value back to the previous lap rather
                # than creating a duplicate completed time on the current lap.
                completed_num = completed_lap_num or lap_num
                completed = self._session_data.lap_timing.get(completed_num)
                if completed is None:
                    completed = LapTimingData(lap_number=completed_num)
                    self._session_data.lap_timing[completed_num] = completed
                # Only store graphics-sourced completed time if logs haven't
                # already set one (logs are authoritative).
                if completed.completed_lap_time_source != "logs":
                    completed.completed_lap_time = float(current.last_lap_time_ms)
                    completed.completed_lap_time_source = "shm_graphics"
                self._mark_source("lap_times", "shm_graphics")

            for field_name in (
                "current_lap_time_ms",
                "last_lap_time_ms",
                "best_lap_time_ms",
                "ideal_lap_time_ms",
                "delta_time_ms",
            ):
                self._mark_source(field_name, "shm_graphics")

    def update_fuel_from_graphics_shm(self, fuel_data: Dict[str, Any]) -> None:
        with self._lock:
            if (
                self._graphics_transaction_epoch is not None
                and self._graphics_transaction_epoch != self._session_epoch
            ):
                return
            current_fuel = fuel_data.get("fuel_liter_current_quantity")
            fuel_rate = fuel_data.get("fuel_liter_per_km")
            fuel_economy = fuel_data.get("km_per_fuel_liter")
            fuel_per_lap = fuel_data.get("fuel_liter_per_lap")

            self._session_data.current_fuel = current_fuel
            self._session_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_economy = fuel_economy

            self._session_data.fuel_data.current_fuel = current_fuel
            self._session_data.fuel_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_data.fuel_economy = fuel_economy
            self._session_data.fuel_data.fuel_consumed_lap = fuel_per_lap
            self._session_data.fuel_data.source = "shm_graphics"

            self._mark_source("current_fuel", "shm_graphics")
            self._mark_source("fuel_consumption_rate", "shm_graphics")
            self._mark_source("fuel_economy", "shm_graphics")

    def update_player_identification_from_logs(self, player_data: Dict[str, Any]) -> None:
        with self._lock:
            ident = self._session_data.player_identification
            incoming_car_uuid = player_data.get("car_uuid")
            incoming_car_model = player_data.get("car_model")
            if (
                incoming_car_uuid
                and self._active_car_uuid
                and not car_uuids_match(incoming_car_uuid, self._active_car_uuid)
            ):
                return
            if (
                incoming_car_model
                and self._active_car_model
                and not car_models_match(incoming_car_model, self._active_car_model)
            ):
                return
            if (
                incoming_car_model
                and self._graphics_car_model
                and not car_models_match(incoming_car_model, self._graphics_car_model)
            ):
                return
            closed_owner = (
                self._active_session_id is None
                and self._session_epoch > 0
                and self._session_data.session_metadata.session_id is not None
            )
            if closed_owner and (
                (
                    incoming_car_model
                    and ident.car_model
                    and not car_models_match(incoming_car_model, ident.car_model)
                )
                or (
                    incoming_car_uuid
                    and ident.car_uuid
                    and not car_uuids_match(incoming_car_uuid, ident.car_uuid)
                )
            ):
                return
            if (
                incoming_car_uuid
                and ident.car_uuid
                and not car_uuids_match(incoming_car_uuid, ident.car_uuid)
            ):
                self._session_data.has_ers = None
                self._session_data.has_kers = None
                self._session_data.hybrid_flags_car_uuid = None
            ident.steam_id = player_data.get("steam_id") or ident.steam_id
            ident.player_name = player_data.get("player_name") or ident.player_name
            ident.car_uuid = incoming_car_uuid or ident.car_uuid
            ident.car_model = incoming_car_model or ident.car_model
            if ident.car_model and self._active_session_id:
                self._active_car_model = ident.car_model
            if incoming_car_uuid and self._active_session_id:
                self._active_car_uuid = str(incoming_car_uuid)
            ident.source = "logs"
            if ident.car_uuid and self._session_data.hybrid_flags_car_uuid is None:
                self._session_data.hybrid_flags_car_uuid = ident.car_uuid

            self._mark_source("player_id", "logs")
            self._mark_source("car_uuid", "logs")

    def update_sector_splits_from_logs(self, lap_num: int, sector_data: Dict[str, Any]) -> None:
        with self._lock:
            splits = SectorSplitData(
                lap_number=lap_num,
                sector1_ms=sector_data.get("sector1_ms"),
                sector2_ms=sector_data.get("sector2_ms"),
                sector3_ms=sector_data.get("sector3_ms"),
                source="logs",
            )
            self._session_data.sector_splits[lap_num] = splits

            legacy: Dict[int, int] = {}
            if splits.sector1_ms is not None:
                legacy[1] = splits.sector1_ms
            if splits.sector2_ms is not None:
                legacy[2] = splits.sector2_ms
            if splits.sector3_ms is not None:
                legacy[3] = splits.sector3_ms
            if legacy:
                self._session_data.sector_times[lap_num] = legacy

            self._mark_source("sector_times", "logs")

    def update_session_metadata_from_static_shm(self, metadata: Dict[str, Any]) -> None:
        with self._lock:
            if not self._secondary_graphics_origin_is_current_locked():
                return
            md = self._session_data.session_metadata
            md.game_version = metadata.get("ac_evo_version", md.game_version)
            md.session_type = str(metadata.get("session", md.session_type))
            md.session_name = metadata.get("session_name", md.session_name)
            md.track = metadata.get("track", md.track)
            md.track_configuration = metadata.get("track_configuration", md.track_configuration)
            md.track_length_m = metadata.get("track_length_m", md.track_length_m)
            md.is_online = bool(metadata.get("is_online", md.is_online))
            md.is_timed_race = bool(metadata.get("is_timed_race", md.is_timed_race))
            md.event_id = metadata.get("event_id", md.event_id)
            md.source = "shm_static"

            self._session_data.starting_ambient_temp_c = metadata.get(
                "starting_ambient_temperature_c", self._session_data.starting_ambient_temp_c
            )
            self._session_data.starting_ground_temp_c = metadata.get(
                "starting_ground_temperature_c", self._session_data.starting_ground_temp_c
            )

            # Powertrain flags from SHM static region — primary hybrid
            # detection source (covers any car without manual list updates).
            static_car_uuid = metadata.get("car_uuid")
            if (
                static_car_uuid
                and self._session_data.player_identification.car_uuid
                and str(static_car_uuid).casefold() != str(self._session_data.player_identification.car_uuid).casefold()
            ):
                self._session_data.has_ers = None
                self._session_data.has_kers = None
            if static_car_uuid:
                self._session_data.hybrid_flags_car_uuid = str(static_car_uuid)
            if "has_ers" in metadata:
                self._session_data.has_ers = bool(metadata["has_ers"])
            if "has_kers" in metadata:
                self._session_data.has_kers = bool(metadata["has_kers"])
            starting_grip = metadata.get("starting_grip_name") or metadata.get("starting_grip")
            self._session_data.starting_grip = starting_grip

            for field_name in ("game_version", "session_type", "track", "is_online", "is_timed_race"):
                self._mark_source(field_name, "shm_static")

    # Legacy update entry points
    def update_lap_from_logs(self, lap_data: LapData, session_data: Optional[SessionData] = None) -> None:
        """Apply a log lap and optional session metadata atomically."""
        with self._lock:
            self._update_lap_from_logs_locked(lap_data, session_data)

    def _update_lap_from_logs_locked(
        self, lap_data: LapData, session_data: Optional[SessionData] = None
    ) -> None:
        if session_data is not None:
            with self._lock:
                if not self._session_update_is_owned_locked(session_data.session_id):
                    return
            self.update_session_metadata_from_logs(session_data)

        self.update_sector_splits_from_logs(
            lap_data.lap_number,
            {
                "sector1_ms": lap_data.sector1_ms,
                "sector2_ms": lap_data.sector2_ms,
                "sector3_ms": lap_data.sector3_ms,
            },
        )

        with self._lock:
            timing = self._session_data.lap_timing.get(lap_data.lap_number)
            if timing is None:
                timing = LapTimingData(lap_number=lap_data.lap_number)
                self._session_data.lap_timing[lap_data.lap_number] = timing
            timing.lap_completion_timestamp = lap_data.timestamp

            if lap_data.lap_time_ms > 0:
                lap_time = float(lap_data.lap_time_ms)
                timing.completed_lap_time = lap_time
                timing.completed_lap_time_source = "logs"
                self._mark_source("lap_times", "logs")

            # ── Validity ───────────────────────────────────────────────
            # The log parser's verdict is authoritative for completed laps
            # regardless of whether it came from the game's ``Relevant
            # onSplit`` broadcast or structural classification.  SHM
            # is_valid_lap cannot distinguish contact from track cuts, and
            # contact must never invalidate a lap, so log always wins.
            # Freeze with source="logs" so future SHM peeks cannot flip
            # the entry back.
            log_state = lap_data.lap_type or lap_data.lap_state.value
            self._session_data.lap_validity[lap_data.lap_number] = LapValidityData(
                lap_number=lap_data.lap_number,
                is_valid=lap_data.is_valid,
                lap_state=log_state,
                source="logs",
            )
            self._mark_source("lap_times", "logs")

    def update_session_metadata_from_logs(self, session_data: SessionData) -> None:
        """Update session metadata and player identification from log SessionData.

        Like update_from_logs but skips lap iteration — used at session start
        before any laps have been parsed.
        """
        with self._lock:
            if not self._session_update_is_owned_locked(session_data.session_id):
                # A delayed outgoing-session completion may still be emitted
                # to its frozen SessionData. Do not let it overwrite the
                # active session's coordination state.
                return
            md = self._session_data.session_metadata
            md.session_id = session_data.session_id
            md.game_version = session_data.game_version
            md.session_type = session_data.session_type
            md.track = session_data.track
            md.weather = session_data.weather

            self._mark_source("game_version", "logs")
            self._mark_source("session_type", "logs")
            self._mark_source("track", "logs")
            self.update_player_identification_from_logs(
                {
                    "steam_id": session_data.player_id,
                    "player_name": session_data.player_name,
                    "car_uuid": session_data.car_uuid,
                    "car_model": session_data.car,
                }
            )

    def update_from_logs(self, log_session_data: SessionData) -> None:
        with self._lock:
            if not self._session_update_is_owned_locked(log_session_data.session_id):
                return
            self.update_session_metadata_from_logs(log_session_data)
            for lap in log_session_data.laps:
                self.update_lap_from_logs(lap)

    def _origin_snapshot_matches_locked(
        self, expected_origin: Optional[SessionOriginSnapshot]
    ) -> bool:
        """Check a secondary SHM write against its graphics transaction."""
        if expected_origin is None:
            return True
        current = SessionOriginSnapshot(
            epoch=self._session_epoch,
            session_id=self._active_session_id,
            graphics_car_model=self._graphics_car_model,
            car_uuid=self._active_car_uuid,
            graphics_ready=not self._graphics_origin_pending,
        )
        return current == expected_origin

    def update_from_static_shm(
        self,
        static_data: Dict[str, Any],
        *,
        expected_origin: Optional[SessionOriginSnapshot] = None,
    ) -> bool:
        with self._lock:
            if not self._origin_snapshot_matches_locked(expected_origin):
                return False
            if not self._secondary_graphics_origin_is_current_locked():
                return False
            self.update_session_metadata_from_static_shm(static_data)
            return True

    def _secondary_graphics_origin_is_current_locked(self) -> bool:
        """Reject static/physics from a graphics sample's old epoch."""
        if self._graphics_origin_pending:
            return False
        if (
            self._active_car_model
            and self._graphics_car_model
            and not car_models_match(
                self._active_car_model,
                self._graphics_car_model,
            )
        ):
            return False
        return True

    def update_from_graphics_shm(self, graphics_data: Dict[str, Any]) -> None:
        """Apply one graphics sample as an atomic origin/data transaction."""
        with self._lock:
            prior_transaction_epoch = self._graphics_transaction_epoch
            self._graphics_transaction_epoch = self._session_epoch
            try:
                self._update_from_graphics_shm_locked(graphics_data)
            finally:
                self._graphics_transaction_epoch = prior_transaction_epoch

    def _update_from_graphics_shm_locked(
        self, graphics_data: Dict[str, Any]
    ) -> None:
        # ── Determine current lap number ────────────────────────────────
        # session_current_lap (from SMEvoSessionState.current_lap) has a
        # fragile offset that reads 0 on AC Evo 0.8.0.1.  total_lap_count
        # (SPageFileGraphicEvo at stable offset 2384) is the completed-lap
        # counter; +1 gives the in-progress lap.  The lightweight
        # ``peek_graphics_validity()`` always provides total_lap_count and
        # sets session_current_lap=0, so the fallback is the normal path.
        shm_current_lap = int(graphics_data.get("session_current_lap") or 0)
        completed_laps = int(graphics_data.get("total_lap_count") or 0)
        current_lap_time_ms = int(graphics_data.get("current_lap_time_ms") or 0)
        raw_current_lap_time_ms = current_lap_time_ms
        last_laptime_ms = int(graphics_data.get("last_laptime_ms") or 0)
        is_valid_lap = graphics_data.get("is_valid_lap")
        incoming_car_model = graphics_data.get("car_model")
        if isinstance(incoming_car_model, str):
            incoming_car_model = incoming_car_model.strip() or None
        incoming_status = graphics_data.get("status_name")
        incoming_status_name = (
            str(incoming_status).strip().upper() if incoming_status is not None else None
        )

        # Capture the boundary before current-lap validity is updated. ACE can
        # reset the live timer and validity several seconds before advancing
        # total_lap_count, so the timer reset is the primary boundary signal.
        # The counter transition remains a fallback for builds without a
        # usable timer. ``active_lap_is_valid`` is deliberately not keyed by
        # the physical lap counter because that counter can be reused after a
        # pit stop.
        with self._lock:
            # Publish phase before evaluating timing transitions. The same
            # snapshot must decide both whether a completion is physical and
            # whether the active validity latch remains meaningful.
            incoming_phase = graphics_data.get("session_phase")
            if incoming_phase is not None:
                self._session_data.session_phase = incoming_phase
            terminal_state = _is_terminal_graphics_state(
                graphics_data,
                self._session_data.session_phase,
            )
            previous_car_model = self._graphics_car_model
            car_changed = bool(
                incoming_car_model
                and previous_car_model
                and not car_models_match(incoming_car_model, previous_car_model)
            )
            confirms_pending_origin = bool(
                car_changed
                and self._graphics_origin_pending
                and incoming_car_model
                and self._active_car_model
                and car_models_match(incoming_car_model, self._active_car_model)
            )
            if car_changed:
                # Graphics can expose the replacement car before logs provide
                # a matching session identity. Start a clean merged-data
                # episode immediately, while _replace_session_data_locked
                # retains the immutable cross-boundary completion queue and
                # the driver's Steam identity.
                self._replace_session_data_locked(preserve_identity=True)
                ident = self._session_data.player_identification
                ident.car_uuid = None
                ident.car_model = None
                self._session_data.has_ers = None
                self._session_data.has_kers = None
                self._session_data.hybrid_flags_car_uuid = None
                self._session_data.session_metadata.session_id = (
                    self._active_session_id
                    or self._session_data.session_metadata.session_id
                )
            if confirms_pending_origin:
                self._graphics_origin_pending = False
            phase_name = str(incoming_phase or "").strip().casefold()
            baseline_phase = phase_name in _BASELINE_SESSION_PHASES
            status_supplied = incoming_status_name is not None
            active_status = (
                not status_supplied
                or (
                    self._graphics_status_name == "AC_LIVE"
                    and incoming_status_name == "AC_LIVE"
                    and self._graphics_timing_active
                )
            )
            baseline_reset = (
                terminal_state
                or car_changed
                or baseline_phase
                or self._graphics_origin_pending
            )
            previous_completed = (
                None if baseline_reset else self._session_data.total_laps
            )
            previous_lap_time_ms = (
                0
                if baseline_reset
                else int(self._session_data.current_lap_time_ms or 0)
            )
            if baseline_reset:
                # A model/phase transition is a new timing episode. Clear
                # the old baseline before looking at the incoming ``last``
                # value; ACE may leave the previous car's time in the mapping.
                self._session_data.current_lap_time_ms = 0
                self._session_data.last_lap_time_ms = 0
                self._session_data.active_lap_is_valid = None
                self._session_data.pending_counter_echo = False
                self._session_data.pending_counter_echo_lap = None
                self._session_data.pending_counter_echo_time_ms = None
                last_laptime_ms = 0
                if not terminal_state:
                    completed_laps = 0
                    shm_current_lap = 0
                current_lap_time_ms = 0
                graphics_data = dict(graphics_data)
                graphics_data.update(
                    {
                        "total_lap_count": completed_laps,
                        "session_current_lap": shm_current_lap,
                        "current_lap_time_ms": 0,
                        "last_laptime_ms": 0,
                    }
                )
            if car_changed and not confirms_pending_origin:
                    # The new graphics model has not yet been paired with a
                    # log connect UUID. Keep the outgoing UUID only on its
                    # already-frozen completions.
                    self._active_car_uuid = None
                    self._session_data.player_identification.car_uuid = None
                    # SHM can expose the next car before the log parser has
                    # opened its session. Completions from this new episode
                    # must remain anonymous until that same epoch is bound.
                    self._session_epoch += 1
                    self._active_session_id = None
                    self._active_car_model = None
                    self._anonymous_origin_pending = True
                    # Metadata belongs to the old session too. Clearing it
                    # prevents a closed-session finalization check from
                    # mistaking an anonymous replacement car for that owner.
                    self._session_data.session_metadata.session_id = None
            lap_timer_reset = (
                previous_lap_time_ms >= 5_000
                and 0 <= current_lap_time_ms <= 1_000
            )
            completed_timer_reset = (
                lap_timer_reset and last_laptime_ms > 0 and abs(previous_lap_time_ms - last_laptime_ms) <= 2_000
            )
            counter_advanced = previous_completed is not None and completed_laps > int(previous_completed)
            duplicate_counter_echo = (
                counter_advanced
                and not completed_timer_reset
                and self._session_data.pending_counter_echo
                and completed_laps == self._session_data.pending_counter_echo_lap
                and last_laptime_ms == self._session_data.pending_counter_echo_time_ms
            )
            new_physical_boundary = lap_timer_reset or (
                counter_advanced and not duplicate_counter_echo
            )
            if (
                not terminal_state
                and not baseline_reset
                and active_status
                and (completed_timer_reset or counter_advanced)
                and last_laptime_ms > 0
            ):
                now_mono = time.monotonic()
                if not duplicate_counter_echo:
                    completion = LapCompletionData(
                        completed_laps=max(
                            completed_laps,
                            int(previous_completed or 0),
                            1,
                        ),
                        lap_time_ms=last_laptime_ms,
                        is_valid=self._session_data.active_lap_is_valid,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        observed_at=now_mono,
                        origin_epoch=self._session_epoch,
                        session_id=self._active_session_id,
                        car_model=(
                            incoming_car_model
                            or previous_car_model
                            or self._active_car_model
                        ),
                        car_uuid=self._active_car_uuid,
                    )
                    self._session_data.latest_lap_completion = completion
                    self._session_data.lap_completions.append(completion)
                    # Preserve every unresolved completion.  Only consumed
                    # records are eligible for the bounded history compact.
                    self._compact_completion_history_locked()
                if completed_timer_reset:
                    self._session_data.pending_counter_echo = not counter_advanced
                    self._session_data.pending_counter_echo_lap = (
                        max(completed_laps, int(previous_completed or 0)) + 1 if not counter_advanced else None
                    )
                    self._session_data.pending_counter_echo_time_ms = last_laptime_ms if not counter_advanced else None
                elif duplicate_counter_echo:
                    self._session_data.pending_counter_echo = False
                    self._session_data.pending_counter_echo_lap = None
                    self._session_data.pending_counter_echo_time_ms = None
                elif counter_advanced:
                    # A jump beyond the expected delayed counter acknowledges
                    # a later physical boundary; the old echo is no longer
                    # eligible to suppress a future transition.
                    self._session_data.pending_counter_echo = False
                    self._session_data.pending_counter_echo_lap = None
                    self._session_data.pending_counter_echo_time_ms = None
            elif duplicate_counter_echo:
                self._session_data.pending_counter_echo = False
                self._session_data.pending_counter_echo_lap = None
                self._session_data.pending_counter_echo_time_ms = None
            # Every timer reset starts a new physical lap, including the pit
            # outlap boundary where ACE deliberately leaves last_laptime_ms at
            # zero.  Do not let an invalid outlap latch contaminate the first
            # timed lap merely because there is no completion to publish.
            if terminal_state:
                self._session_data.active_lap_is_valid = None
                self._session_data.pending_counter_echo = False
                self._session_data.pending_counter_echo_lap = None
                self._session_data.pending_counter_echo_time_ms = None
            elif new_physical_boundary:
                self._session_data.active_lap_is_valid = None

            if not terminal_state:
                if current_lap_time_ms <= 0:
                    self._session_data.active_lap_is_valid = None
                elif is_valid_lap is not None:
                    sampled_validity = bool(is_valid_lap)
                    if self._session_data.active_lap_is_valid is None:
                        self._session_data.active_lap_is_valid = sampled_validity
                    elif not sampled_validity:
                        # Once ACE invalidates an active lap, keep that verdict
                        # until its timer resets. The finish-line frame may
                        # already carry the next lap's valid=True value.
                        self._session_data.active_lap_is_valid = False
            if incoming_car_model:
                self._graphics_car_model = incoming_car_model
            if incoming_status_name is not None:
                self._graphics_status_name = incoming_status_name
            self._graphics_timing_active = (
                raw_current_lap_time_ms > 1_000
                and not terminal_state
                and (not status_supplied or incoming_status_name == "AC_LIVE")
            )
        if shm_current_lap > 0:
            current_lap = shm_current_lap
        else:
            # completed_laps is the number of laps already finished; the
            # in-progress lap is always the next one.  completed_laps=0 means
            # lap 1 is running (formation/outlap or the first timed lap),
            # completed_laps=1 means lap 2 is running, and so on.
            current_lap = completed_laps + 1

        if current_lap > 0:
            # ── Guard against stale SHM last_laptime_ms at session start ─
            # When completed_laps == 0 AND we are on lap 1 (current_lap <= 1),
            # no laps have been finished in the current session.  Any non-zero
            # last_laptime_ms is stale data carried over from a previous game
            # session via the Windows file mapping.  Scrub it so
            # update_lap_timing_from_graphics_shm does not store it as a
            # completed lap time for lap 1.
            #
            # We also check current_lap <= 1 because some decoders provide
            # session_current_lap but not total_lap_count; a current_lap > 1
            # implies laps have been completed and last_laptime_ms is legit.
            shm_last = graphics_data.get("last_laptime_ms")
            if shm_last and int(shm_last) > 0 and completed_laps == 0 and current_lap <= 1:
                existing = self._session_data.lap_timing.get(current_lap)
                already_stored = (
                    existing is not None and existing.completed_lap_time is not None and existing.completed_lap_time > 0
                )
                if not already_stored:
                    from ..utils.structured_logger import Component, log_debug

                    log_debug(
                        Component.SHARED_SESSION,
                        f"[SHM_STALE] Discarding stale last_laptime_ms={shm_last} ms "
                        f"for lap {current_lap} with completed_laps=0 — "
                        f"likely carryover from previous game session",
                    )
                # Scrub the stale value before it reaches the timing update
                graphics_data = dict(graphics_data)
                graphics_data["last_laptime_ms"] = 0
            self.update_lap_timing_from_graphics_shm(
                current_lap,
                ({**graphics_data, "last_laptime_ms": 0} if terminal_state else graphics_data),
                completed_lap_num=completed_laps if completed_laps > 0 else None,
            )
            if (
                self._graphics_transaction_epoch is not None
                and self._graphics_transaction_epoch != self._session_epoch
            ):
                return

            # ── Wire SHM validity flags into shared session ──────────────
            # Priority 1: is_valid_lap (SPageFileGraphicEvo at offset 3121)
            # DOES work on AC Evo 0.8.0.1 and indicates whether the current
            # lap is being timed as valid.  is_valid_lap=False with
            # current_lap_time_ms > 0 means the in-progress lap has been
            # invalidated (cut track, penalty, etc.).  is_valid_lap=False
            # with current_lap_time_ms == 0 means timing is inactive
            # (between sessions, in pits, etc.) and should NOT be treated
            # as an invalidity verdict.
            # Priority 2: is_invalid / timing_is_invalid (SMEvoTimingState)
            # is NOT populated by AC Evo 0.8.0.1 — always False, which is
            # why it must NOT be checked first (False ≠ None).
            lap_time_ms = current_lap_time_ms

            if terminal_state:
                is_invalid = None
            elif is_valid_lap is not None:
                # is_valid_lap is the authoritative flag on AC Evo 0.8.0.1.
                # Only apply it when timing is active (lap_time_ms > 0).
                # When lap_time_ms == 0, timing is inactive (session end,
                # in pits) and is_valid_lap=False should NOT be treated as
                # an invalidity verdict — skip the update entirely.
                if lap_time_ms > 0:
                    # Use the latched verdict so a delayed counter echo that
                    # carries the next lap's valid=True flag cannot erase an
                    # invalidation already observed on that lap.
                    is_invalid = self._session_data.active_lap_is_valid is False
                else:
                    is_invalid = None
            else:
                is_invalid = graphics_data.get("is_invalid")
                if is_invalid is None:
                    is_invalid = graphics_data.get("timing_is_invalid")

            if is_invalid is not None:
                is_invalid_bool = bool(is_invalid)
                self.update_lap_validity_from_graphics_shm(current_lap, is_invalid_bool)
                if (
                    self._graphics_transaction_epoch is not None
                    and self._graphics_transaction_epoch != self._session_epoch
                ):
                    return

        self.update_fuel_from_graphics_shm(graphics_data)

        if (
            self._graphics_transaction_epoch is not None
            and self._graphics_transaction_epoch != self._session_epoch
        ):
            return

        with self._lock:
            self._session_data.total_laps = graphics_data.get("total_lap_count")
            self._session_data.current_lap = graphics_data.get("session_current_lap")
            if "session_phase" in graphics_data:
                self._session_data.session_phase = graphics_data.get("session_phase")
            self._session_data.session_time_left_ms = graphics_data.get("session_time_left_ms")
            self._session_data.current_pos = graphics_data.get("current_pos")
            self._session_data.total_drivers = graphics_data.get("total_drivers")

            # Car model from graphics SHM (authoritative, overrides logs fallback)
            car_model = graphics_data.get("car_model")
            if isinstance(car_model, str) and car_model.strip():
                self._session_data.player_identification.car_model = car_model.strip()
                self._mark_source("car", "shm_graphics")

            # has_kers from graphics SHM (AC Evo SPageFileGraphicEvo offset 2420)
            has_kers = graphics_data.get("has_kers")
            if has_kers is not None:
                self._session_data.has_kers = bool(has_kers)

            self._mark_source("session_summary", "shm_graphics")

    def update_from_physics_shm(
        self,
        physics_data: Dict[str, Any],
        *,
        expected_origin: Optional[SessionOriginSnapshot] = None,
    ) -> bool:
        with self._lock:
            if not self._origin_snapshot_matches_locked(expected_origin):
                return False
            if not self._secondary_graphics_origin_is_current_locked():
                return False
            speed_kmh = physics_data.get("speed_kmh")
            if isinstance(speed_kmh, (int, float)):
                if self._session_data.max_speed is None:
                    self._session_data.max_speed = float(speed_kmh)
                else:
                    self._session_data.max_speed = max(self._session_data.max_speed, float(speed_kmh))

            car_setup = physics_data.get("car_setup")
            if isinstance(car_setup, dict):
                self._session_data.car_setup.update(car_setup)
            assists_state = physics_data.get("assists_state")
            if isinstance(assists_state, dict):
                self._session_data.assists_state.update(assists_state)

            self._session_data.air_density = physics_data.get("air_density", self._session_data.air_density)

            self._mark_source("max_speed", "shm_physics")
            self._mark_source("car_setup", "shm_physics")
            return True

    def update_from_telemetry(
        self,
        telemetry_data: Dict[str, Any],
        *,
        expected_origin: Optional[SessionOriginSnapshot] = None,
    ) -> bool:
        with self._lock:
            if not self._origin_snapshot_matches_locked(expected_origin):
                return False
            max_speed = telemetry_data.get("max_speed")
            if isinstance(max_speed, (int, float)):
                self._session_data.max_speed = float(max_speed)

            stint_number = telemetry_data.get("stint_number")
            if isinstance(stint_number, int) and stint_number > 0:
                self._session_data.stint_number = stint_number

            tyre_compound = telemetry_data.get("tyre_compound")
            if isinstance(tyre_compound, str) and tyre_compound.strip():
                self._session_data.tyre_compound = tyre_compound

            self._mark_source("telemetry_summary", "calculated")
            return True

    def get_data_sources(self) -> Dict[str, Set[str]]:
        """Return a snapshot of data source tracking (thread-safe)."""
        with self._lock:
            return {k: set(v) for k, v in self._session_data.data_sources.items()}

    def get_data_for_origin(
        self,
        expected_origin: SessionOriginSnapshot,
        *,
        session_id: Optional[str] = None,
    ) -> Optional[SharedSessionData]:
        """Copy merged data only when the requested origin is still current."""
        with self._lock:
            if not self._origin_snapshot_matches_locked(expected_origin):
                return None
            if (
                session_id is not None
                and expected_origin.session_id != session_id
            ):
                return None
            return deepcopy(self._session_data)

    def get_submission_snapshot_for_origin(
        self,
        expected_origin: Optional[SessionOriginSnapshot],
        *,
        lap_number: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> OriginSnapshotResult:
        """Atomically classify an origin and freeze only submission fields."""
        with self._lock:
            relation, current = self._origin_relation_locked(expected_origin)
            if relation is OriginRelation.MISMATCH:
                return OriginSnapshotResult(relation, current, None)
            requested_id = session_id or (
                expected_origin.session_id if expected_origin is not None else None
            )
            if (
                requested_id is not None
                and requested_id not in {
                    self._active_session_id,
                    self._session_data.session_metadata.session_id,
                }
                and not (
                    self._session_epoch == 0 and self._active_session_id is None
                )
            ):
                return OriginSnapshotResult(OriginRelation.MISMATCH, current, None)
            metadata = self._session_data.session_metadata
            ident = self._session_data.player_identification
            timing = (
                self._session_data.lap_timing.get(lap_number)
                if lap_number is not None
                else None
            )
            split = (
                self._session_data.sector_splits.get(lap_number)
                if lap_number is not None
                else None
            )
            sectors = (
                getattr(split, "sector1_ms", None),
                getattr(split, "sector2_ms", None),
                getattr(split, "sector3_ms", None),
            )
            requested_lap_time = (
                timing.last_lap_time_ms if timing is not None else None
            )
            if (
                not isinstance(requested_lap_time, (int, float))
                or requested_lap_time <= 0
            ):
                requested_lap_time = (
                    timing.completed_lap_time if timing is not None else None
                )
            snapshot = SubmissionSnapshot(
                origin=current,
                retained_session_id=self._session_data.session_metadata.session_id,
                steam_id=ident.steam_id,
                car_model=ident.car_model,
                track=metadata.track,
                session_type=metadata.session_type,
                game_version=metadata.game_version,
                lap_number=lap_number,
                lap_time_ms=(
                    int(requested_lap_time)
                    if isinstance(requested_lap_time, (int, float))
                    else None
                ),
                sectors=sectors,
                fuel_per_lap=self._session_data.fuel_data.fuel_consumed_lap,
            )
            return OriginSnapshotResult(relation, current, snapshot)

    def get_analysis_snapshot_for_origin(
        self, expected_origin: Optional[SessionOriginSnapshot]
    ) -> OriginSnapshotResult:
        """Atomically classify an origin and freeze timing/validity records."""
        with self._lock:
            relation, current = self._origin_relation_locked(expected_origin)
            if relation is OriginRelation.MISMATCH:
                return OriginSnapshotResult(relation, current, None)
            timing_records = tuple(
                (lap_num, timing.completed_lap_time)
                for lap_num, timing in sorted(self._session_data.lap_timing.items())
            )
            validity_records = tuple(
                (lap_num, bool(validity.is_valid))
                for lap_num, validity in sorted(self._session_data.lap_validity.items())
            )
            snapshot = AnalysisSnapshot(
                origin=current,
                retained_session_id=self._session_data.session_metadata.session_id,
                car_model=self._session_data.player_identification.car_model,
                timing_records=timing_records,
                validity_records=validity_records,
            )
            return OriginSnapshotResult(relation, current, snapshot)

    def reset(self, *, preserve_completions: bool = False) -> None:
        """Replace session data with a fresh instance, preserving observers and lock.

        Call this when a new game session starts so stale lap validity, timing,
        and fuel data from the previous session cannot bleed into the new one.
        Player identification is intentionally preserved across resets because the
        same driver is still logged in.
        """
        with self._lock:
            old_timing_count = len(self._session_data.lap_timing)
            old_validity_count = len(self._session_data.lap_validity)
            old_ident = self._session_data.player_identification
            old_completions = list(self._session_data.lap_completions)
            old_latest_completion = self._session_data.latest_lap_completion
            old_consumed = set(self._session_data.consumed_lap_completion_times)
            old_bindings = dict(self._completion_bindings)
            old_has_ers = self._session_data.has_ers
            old_has_kers = self._session_data.has_kers
            old_hybrid_car_uuid = self._session_data.hybrid_flags_car_uuid
            old_anonymous_origin_pending = self._anonymous_origin_pending
            old_graphics_car_model = self._graphics_car_model
            old_graphics_status_name = self._graphics_status_name
            old_graphics_timing_active = self._graphics_timing_active
            old_graphics_origin_pending = self._graphics_origin_pending
            self._session_data = SharedSessionData()
            # Re-attach player identification — Steam ID / car UUID don't change
            # between sessions and must not be wiped.
            self._session_data.player_identification = old_ident
            if preserve_completions:
                self._session_data.lap_completions = old_completions
                self._session_data.latest_lap_completion = old_latest_completion
                self._session_data.consumed_lap_completion_times = old_consumed
                self._completion_bindings = old_bindings
            else:
                self._completion_bindings.clear()
            self._graphics_car_model = old_graphics_car_model if preserve_completions else None
            self._graphics_status_name = old_graphics_status_name if preserve_completions else None
            self._graphics_timing_active = old_graphics_timing_active if preserve_completions else False
            self._graphics_origin_pending = old_graphics_origin_pending if preserve_completions else False
            self._anonymous_origin_pending = (
                old_anonymous_origin_pending if preserve_completions else False
            )
            same_identified_car = bool(
                old_ident.car_uuid
                and old_hybrid_car_uuid
                and str(old_ident.car_uuid).casefold() == str(old_hybrid_car_uuid).casefold()
            )
            if same_identified_car:
                self._session_data.has_ers = old_has_ers
                self._session_data.has_kers = old_has_kers
                self._session_data.hybrid_flags_car_uuid = old_hybrid_car_uuid
            from ..utils.structured_logger import Component, log_debug

            log_debug(
                Component.SHARED_SESSION,
                f"[RESET] Cleared shared session: dropped {old_timing_count} timing entries, "
                f"{old_validity_count} validity entries. "
                f"Car model after reset: {old_ident.car_model}",
            )
