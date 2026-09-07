"""Telemetry lifecycle service extracted from SimLapsApp.

Owns telemetry capture start/stop transitions and post-capture analysis flow.
"""

from typing import TYPE_CHECKING, Optional

from src.utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
    log_info,
)
from ..components.status_bar import ConnectionStatus
from ..components.telemetry_status import TelemetryStatus
from ..pages.home import HomePage
from src.core.telemetry_capture import TelemetryCapture

if TYPE_CHECKING:
    from src.core.analyzer import TelemetryAnalyzer


class TelemetryLifecycleService:
    """Encapsulates telemetry capture lifecycle orchestration."""

    async def start_capture(
        self,
        *,
        telemetry_capture: TelemetryCapture | None,
        home_page: HomePage | None,
        telemetry_enabled: bool,
    ) -> None:
        """Start telemetry capture when a session becomes active.

        The capture loop always runs so that real-time SHM validity data
        reaches the shared session.  When ``telemetry_enabled`` is False
        the loop runs in validity-only mode (reads SHM, pushes to shared
        session, but does not record frames).
        """
        output_prefix = telemetry_capture.get_output_prefix() if telemetry_capture else None
        log_debug(
            Component.APP,
            "Telemetry start requested",
            enabled=telemetry_enabled,
            capture_exists=telemetry_capture is not None,
            output_prefix=output_prefix,
        )

        if not telemetry_capture:
            log_info(Component.APP, "Telemetry start skipped: capture service unavailable")
            return
        if telemetry_capture.is_capturing():
            log_info(Component.APP, "Telemetry start skipped: already capturing")
            return

        # Ensure the capture's recording mode matches the current setting.
        # This handles the case where the user toggled telemetry in Settings
        # while no session was active.
        if telemetry_capture.record_frames != telemetry_enabled:
            telemetry_capture.set_record_frames(telemetry_enabled)

        try:
            mode_label = "full recording" if telemetry_enabled else "validity-only"
            log_info(Component.APP, f"Starting telemetry capture ({mode_label})")
            if home_page and telemetry_enabled:
                home_page.set_telemetry_status(TelemetryStatus.CAPTURING, 0)

            success = await telemetry_capture.start_capture()
            if not success:
                log_error(Component.APP, "Telemetry capture failed to start")
                if home_page and telemetry_enabled:
                    home_page.set_telemetry_status(TelemetryStatus.ERROR)
            else:
                log_info(
                    Component.APP,
                    "Telemetry capture started successfully",
                    prefix=telemetry_capture.get_output_prefix(),
                    mode=mode_label,
                )
        except Exception as exc:
            log_exception(Component.APP, "Telemetry start error", exc)
            if home_page and telemetry_enabled:
                home_page.set_telemetry_status(TelemetryStatus.ERROR)

    async def handle_auto_stop(
        self,
        *,
        reason: str,
        telemetry_capture: TelemetryCapture | None,
        telemetry_analyzer: "TelemetryAnalyzer | None",
        home_page: HomePage | None,
        current_track_name: Optional[str],
    ) -> None:
        """Handle automatic stop event (crash/quit) and run analysis if frames exist.

        Analysis only runs when an analyzer is present AND frames were
        recorded.  In validity-only mode there are no frames and no
        analyzer — the method still finalises the capture loop cleanly.
        """
        output_prefix = telemetry_capture.get_output_prefix() if telemetry_capture else None
        frame_count = len(telemetry_capture.get_frames()) if telemetry_capture else 0
        log_info(
            Component.APP,
            "Telemetry auto-stop",
            reason=reason,
            prefix=output_prefix,
            frames=frame_count,
        )

        if home_page:
            home_page.set_connection_status(
                ConnectionStatus.CONNECTED,
                f"Session ended ({reason})",
            )

        if telemetry_capture:
            frames = telemetry_capture.get_frames()
            frame_count = len(frames)

            if frame_count > 0 and telemetry_analyzer is not None:
                log_info(Component.APP, "Starting analysis", frames=frame_count)
                try:
                    if home_page:
                        home_page.set_telemetry_status(TelemetryStatus.ANALYZING, frame_count)
                    metadata = telemetry_capture.get_metadata()
                    lap_boundaries = telemetry_capture.get_lap_boundaries()
                    result = await telemetry_analyzer.analyze(
                        frames,
                        hz=10.0,
                        metadata=metadata,
                        track_name=current_track_name,
                        output_prefix=telemetry_capture.get_output_prefix(),
                        game_lap_boundaries=lap_boundaries,
                        capture_origin=telemetry_capture.get_capture_origin(),
                        capture_track_name=telemetry_capture.get_capture_track_name(),
                    )

                    log_info(
                        Component.APP,
                        "Analysis complete",
                        laps=result.laps_detected,
                        best_lap_time=(
                            f"{result.best_lap_time:.1f}s"
                            if result.best_lap_time is not None
                            else "none"
                        ),
                    )
                    if home_page:
                        home_page.set_telemetry_status(
                            TelemetryStatus.COMPLETE,
                            frame_count,
                            result.html_path,
                        )
                except Exception as exc:
                    log_exception(Component.APP, "Analysis error", exc)
                    if home_page:
                        home_page.set_telemetry_status(TelemetryStatus.ERROR)
            elif frame_count > 0:
                log_debug(Component.APP, "Frames captured but no analyzer — skipping (validity-only mode)")
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.IDLE)
            else:
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.IDLE)

    async def stop_capture(
        self,
        *,
        reason: str,
        discard: bool,
        telemetry_capture: TelemetryCapture | None,
        telemetry_analyzer: "TelemetryAnalyzer | None",
        home_page: HomePage | None,
        current_track_name: Optional[str],
    ) -> None:
        """Stop telemetry capture and run analysis unless discarded.

        The capture loop is always stopped regardless of whether an
        analyzer is present (validity-only mode has no analyzer).
        Analysis only runs when both an analyzer is available and
        frames were recorded.
        """
        output_prefix = telemetry_capture.get_output_prefix() if telemetry_capture else None
        is_capturing = telemetry_capture.is_capturing() if telemetry_capture else False
        log_info(
            Component.APP,
            "Telemetry stop requested",
            reason=reason,
            prefix=output_prefix,
            capturing=is_capturing,
        )

        if not telemetry_capture:
            log_debug(Component.APP, "Telemetry stop skipped: capture missing")
            return
        if not telemetry_capture.is_capturing():
            stop_reason = telemetry_capture.get_stop_reason()
            if stop_reason is not None:
                log_debug(Component.APP, "Telemetry stop skipped: already stopped", stop_reason=stop_reason)
                return
            log_debug(Component.APP, "Proceeding with telemetry stop: not capturing but no stop reason set")

        try:
            log_debug(Component.APP, "Stopping telemetry capture", prefix=telemetry_capture.get_output_prefix())
            frames = await telemetry_capture.stop_capture(reason)
            frame_count = len(frames)
            log_info(Component.APP, "Telemetry capture stopped", frames=frame_count, prefix=output_prefix)

            if discard:
                log_info(Component.APP, "Discarding captured frames (contaminated buffer)", frames=frame_count)
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.IDLE)
                return

            # Analysis requires both an analyzer and recorded frames.
            # In validity-only mode the analyzer is None — skip silently.
            if frame_count > 0 and telemetry_analyzer is not None:
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.ANALYZING, frame_count)
                log_info(Component.APP, "Starting telemetry analysis", frames=frame_count, prefix=output_prefix)
                metadata = telemetry_capture.get_metadata()
                lap_boundaries = telemetry_capture.get_lap_boundaries()
                result = await telemetry_analyzer.analyze(
                    frames,
                    hz=10.0,
                    metadata=metadata,
                    track_name=current_track_name,
                    output_prefix=output_prefix,
                    game_lap_boundaries=lap_boundaries,
                    capture_origin=telemetry_capture.get_capture_origin(),
                    capture_track_name=telemetry_capture.get_capture_track_name(),
                )

                log_info(
                    Component.APP,
                    "Telemetry analysis complete",
                    laps=result.laps_detected,
                    best_lap_time=(
                        f"{result.best_lap_time:.2f}s"
                        if result.best_lap_time is not None
                        else "none"
                    ),
                    html_path=result.html_path,
                    ai_prompt_path=result.ai_prompt_path,
                )
                if home_page:
                    home_page.set_telemetry_status(
                        TelemetryStatus.COMPLETE,
                        frame_count,
                        result.html_path,
                    )
            elif frame_count > 0:
                log_debug(Component.APP, "Frames captured but no analyzer — skipping analysis (validity-only mode)")
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.IDLE)
            else:
                log_debug(Component.APP, "No frames captured, skipping analysis")
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.IDLE)

        except Exception as exc:
            log_exception(Component.APP, "Telemetry stop/analysis error", exc)
            if home_page:
                home_page.set_telemetry_status(TelemetryStatus.ERROR)
