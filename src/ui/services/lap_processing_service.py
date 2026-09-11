"""Lap processing/presentation service extracted from SimLapsApp.

Owns lap-complete orchestration for telemetry lap boundary recording,
submission eligibility, history/card synchronization, and auto-submit trigger.
"""

from typing import Callable, Optional

from src.core.pb_cache import PBCache
from src.core.telemetry_capture import TelemetryCapture
from src.models import LapData, SessionData, SharedSessionManager
from src.utils.config import AppConfig
from src.utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
)

from ..components.lap_card import LapCardStatus
from ..pages.history import HistoryEntry
from ..pages.home import HomePage


class LapProcessingService:
    """Encapsulates app-side lap completion processing flow."""

    async def handle_lap_complete(
        self,
        *,
        session: SessionData,
        lap: LapData,
        home_page: HomePage,
        telemetry_capture: Optional[TelemetryCapture],
        config: AppConfig,
        session_manager: SharedSessionManager,
        pb_cache: PBCache,
        history_entries: list[HistoryEntry],
        schedule_submission: Callable[..., None],
        create_history_entry: Callable[..., HistoryEntry],
    ) -> Optional[str]:
        """Process lap completion, update UI/history, and optionally auto-submit.

        Returns the track name if it was updated (caller should sync
        ``current_track_name``), otherwise ``None``.
        """
        updated_track: Optional[str] = None

        # Update detected user in UI
        if session.player_id:
            log_debug(Component.APP, "Updating detected user", steam_id=session.player_id)
            home_page.set_detected_user(session.player_id, session.player_name)

        # Return updated track name for telemetry (caller sets it on the app)
        if session.track and session.track != "Unknown":
            updated_track = session.track

        # Record lap boundary so the analyzer can use authoritative lap splits.
        # Fuel per lap is owned entirely by the log parser (Physics SHM + spike
        # detection) and is already set on lap.fuel_used before this point.
        if telemetry_capture and telemetry_capture.is_capturing():
            lap_type = getattr(lap, "lap_type", None) or getattr(getattr(lap, "lap_state", None), "value", None)
            telemetry_capture.record_lap_boundary(
                lap.lap_time_ms,
                lap.lap_number,
                lap_type or "VALID",
            )

        elif config.telemetry_enabled and telemetry_capture:
            # A lap-complete event is too late to begin a useful capture
            # for that lap and can fire during post-session shutdown.
            log_debug(
                Component.APP,
                "Telemetry missed lap boundary; not starting capture from lap-complete",
                lap_number=lap.lap_number,
            )

        # OUTLAP is a structural timing boundary, not a result. Telemetry
        # needs the boundary above to exclude the pit-exit circuit, but it must
        # not become a card, history entry, PB, or submission.
        if (getattr(lap, "lap_type", None) or "").upper() == "OUTLAP":
            log_debug(
                Component.APP,
                "Outlap boundary recorded; suppressing result presentation",
                lap_number=lap.lap_number,
            )
            return updated_track

        # Determine if we should submit this lap. The log parser's verdict
        # is authoritative for completed laps: it uses the game's own
        # ``Relevant onSplit`` broadcast when available and structural
        # classification otherwise. The SHM is_valid_lap flag cannot
        # distinguish contact from track cuts, and contact must never
        # invalidate a lap, so SHM validity is only used for the real-time
        # in-progress display — never to override a completed-lap verdict.
        effective_is_valid = lap.is_valid
        should_submit = config.auto_submit and (
            effective_is_valid or config.submit_invalid_laps
        )
        log_debug(
            Component.APP,
            "Lap submission decision",
            should_submit=should_submit,
            parser_is_valid=lap.is_valid,
            effective_is_valid=effective_is_valid,
            lap_number=lap.lap_number,
        )
        log_debug(
            Component.APP,
            "Lap diagnostics",
            lap_state=getattr(lap, "lap_state", "UNKNOWN"),
            lap_type=getattr(lap, "lap_type", "UNKNOWN"),
            physics_lap_number=getattr(lap, "physics_lap_number", None),
            sector1_ms=lap.sector1_ms,
            sector2_ms=lap.sector2_ms,
            sector3_ms=lap.sector3_ms,
            sectors_consistent=getattr(lap, "sectors_consistent", None),
        )
        if not effective_is_valid:
            log_debug(
                Component.APP,
                "Invalid lap diagnostics",
                lap_state=getattr(lap, "lap_state", "UNKNOWN"),
                lap_number=lap.lap_number,
            )

        # Update local PB cache for every valid lap (independent of Discord posting)
        pb_was_new: Optional[bool] = None
        if effective_is_valid and lap.lap_time_ms > 0:
            if session.track and session.track != "Unknown" and session.car and session.car != "Unknown":
                pb_was_new = pb_cache.check_and_update_pb(
                    session.track,
                    session.car,
                    lap.lap_time_ms,
                )
                log_debug(
                    Component.APP,
                    "PB cache update",
                    pb_was_new=pb_was_new,
                    track=session.track,
                    car=session.car,
                    lap_time_ms=lap.lap_time_ms,
                )
            else:
                log_debug(Component.APP, "Skipping PB cache update: missing track/car")

        # Determine initial status
        if not effective_is_valid and not config.submit_invalid_laps:
            status = LapCardStatus.INVALID
        else:
            status = LapCardStatus.SUBMITTING if should_submit else LapCardStatus.PENDING

        # Add to history FIRST (before home page to ensure synchronization)
        history_entry = create_history_entry(
            track=session.track,
            car=session.car,
            lap_time_ms=lap.lap_time_ms,
            timestamp=lap.timestamp,
            was_submitted=False,
            was_valid=lap.is_valid,
        )
        history_entries.append(history_entry)

        # Add to home page (this increments the counter)
        try:
            card = home_page.add_lap(session, lap, status)
            log_debug(Component.APP, "Lap card added", lap_number=lap.lap_number)
        except Exception as exc:
            # If home page add fails, remove the history entry to maintain sync
            log_exception(Component.APP, "Failed to add lap card to home page", exc)
            history_entries.pop()  # Remove the entry we just added
            raise

        # Debug: Check synchronization
        log_debug(
            Component.APP,
            "Lap/history synchronization state",
            home_lap_count=home_page._lap_count,
            history_entries=len(history_entries),
            was_submitted=history_entry.was_submitted,
            was_valid=history_entry.was_valid,
        )

        # Verify synchronization
        if home_page._lap_count != len(history_entries):
            log_error(
                Component.APP,
                "Synchronization mismatch",
                home_lap_count=home_page._lap_count,
                history_entries=len(history_entries),
            )
            # This should never happen now, but if it does, we have a serious issue

        # Auto-submit if enabled
        if should_submit:
            log_debug(Component.APP, "Queueing auto-submit", lap_number=lap.lap_number)
            schedule_submission(
                card,
                session,
                lap,
                history_entry,
                pb_was_new,
            )
            log_debug(Component.APP, "Auto-submit queued", lap_number=lap.lap_number)

        return updated_track
