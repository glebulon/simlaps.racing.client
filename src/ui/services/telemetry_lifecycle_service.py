"""Telemetry lifecycle service extracted from SimLapsApp.

Owns telemetry capture start/stop transitions and post-capture analysis flow.
"""

from typing import Any, Optional

from src.utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
    log_info,
)
from ..components.status_bar import ConnectionStatus
from ..components.telemetry_status import TelemetryStatus


class TelemetryLifecycleService:
    """Encapsulates telemetry capture lifecycle orchestration."""

    async def start_capture(
        self,
        *,
        telemetry_capture: Any,
        home_page: Any,
        telemetry_enabled: bool,
    ) -> None:
        """Start telemetry capture when a session becomes active."""
        output_prefix = telemetry_capture.get_output_prefix() if telemetry_capture else None
        log_debug(
            Component.APP,
            "Telemetry start requested",
            enabled=telemetry_enabled,
            capture_exists=telemetry_capture is not None,
            output_prefix=output_prefix,
        )

        if not telemetry_capture or not telemetry_enabled:
            log_info(Component.APP, "Telemetry start skipped: disabled or unavailable")
            return
        if telemetry_capture.is_capturing():
            log_info(Component.APP, "Telemetry start skipped: already capturing")
            return

        try:
            log_info(Component.APP, "Starting telemetry capture from UI")
            if home_page:
                home_page.set_telemetry_status(TelemetryStatus.CAPTURING, 0)

            success = await telemetry_capture.start_capture()
            if not success:
                log_error(Component.APP, "Telemetry capture failed to start")
                if home_page:
                    home_page.set_telemetry_status(TelemetryStatus.ERROR)
            else:
                log_info(
                    Component.APP,
                    "Telemetry capture started successfully",
                    prefix=telemetry_capture.get_output_prefix(),
                )
        except Exception as exc:
            log_exception(Component.APP, "Telemetry start error", exc)
            if home_page:
                home_page.set_telemetry_status(TelemetryStatus.ERROR)

    async def handle_auto_stop(
        self,
        *,
        reason: str,
        telemetry_capture: Any,
        telemetry_analyzer: Any,
        home_page: Any,
        current_track_name: Optional[str],
    ) -> None:
        """Handle automatic stop event (crash/quit) and run analysis if frames exist."""
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

        if telemetry_capture and telemetry_analyzer:
            frames = telemetry_capture.get_frames()
            frame_count = len(frames)

            if frame_count > 0:
                log_info(Component.APP, "Starting analysis", frames=frame_count)
                try:
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
                    )

                    log_info(
                        Component.APP,
                        "Analysis complete",
                        laps=result.laps_detected,
                        best_lap_time=f"{result.best_lap_time:.1f}s",
                    )
                    home_page.set_telemetry_status(
                        TelemetryStatus.COMPLETE,
                        frame_count,
                        result.html_path,
                    )
                except Exception as exc:
                    log_exception(Component.APP, "Analysis error", exc)
                    home_page.set_telemetry_status(TelemetryStatus.ERROR)
            else:
                home_page.set_telemetry_status(TelemetryStatus.IDLE)

    async def stop_capture(
        self,
        *,
        reason: str,
        discard: bool,
        telemetry_capture: Any,
        telemetry_analyzer: Any,
        home_page: Any,
        current_track_name: Optional[str],
    ) -> None:
        """Stop telemetry capture and run analysis unless discarded."""
        output_prefix = telemetry_capture.get_output_prefix() if telemetry_capture else None
        is_capturing = telemetry_capture.is_capturing() if telemetry_capture else False
        log_info(
            Component.APP,
            "Telemetry stop requested",
            reason=reason,
            prefix=output_prefix,
            capturing=is_capturing,
        )

        if not telemetry_capture or not telemetry_analyzer:
            log_debug(Component.APP, "Telemetry stop skipped: capture or analyzer missing")
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
                home_page.set_telemetry_status(TelemetryStatus.IDLE)
                return

            if frame_count > 0:
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
                )

                log_info(
                    Component.APP,
                    "Telemetry analysis complete",
                    laps=result.laps_detected,
                    best_lap_time=f"{result.best_lap_time:.2f}s",
                    html_path=result.html_path,
                    ai_prompt_path=result.ai_prompt_path,
                )
                home_page.set_telemetry_status(
                    TelemetryStatus.COMPLETE,
                    frame_count,
                    result.html_path,
                )
            else:
                log_debug(Component.APP, "No frames captured, skipping analysis")
                home_page.set_telemetry_status(TelemetryStatus.IDLE)

        except Exception as exc:
            log_exception(Component.APP, "Telemetry stop/analysis error", exc)
            home_page.set_telemetry_status(TelemetryStatus.ERROR)
